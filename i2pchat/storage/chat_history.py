"""
Encrypted per-peer chat history persistence.

File format (binary):
  4 bytes  — magic b"I2CH"
  2 bytes  — format version (big-endian uint16, currently 1)
  32 bytes — random salt (generated once per file, reused on updates)
  rest     — NaCl SecretBox ciphertext wrapping a UTF-8 JSON payload

Key derivation (two-stage HKDF so that compromising one file does not
reveal others):

  profile_key = HKDF-Expand(
      HKDF-Extract(b"I2PCHAT-HISTORY", identity_key_bytes),
      info=b"I2PCHAT-HISTORY|profile-key", 32)

  file_key = HKDF-Expand(
      HKDF-Extract(salt, profile_key),
      info=b"I2PCHAT-HISTORY|file-key|" + peer_id_bytes, 32)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import struct
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Optional

from i2pchat import crypto
from i2pchat.storage.blindbox_state import atomic_write_bytes

logger = logging.getLogger("i2pchat.history")

HISTORY_MAGIC = b"I2CH"
HISTORY_VERSION = 2
HEADER_SIZE = 4 + 2 + 32  # magic + version + salt
SALT_SIZE = 32
DEFAULT_MAX_MESSAGES = 1000
DEFAULT_HISTORY_RETENTION_DAYS = 30
LEGACY_PEER_ID_HEX_LEN = 16


def normalize_peer_addr(peer_addr: str) -> str:
    """Нормализация адреса пира для ключей (история, черновики в UI)."""
    return peer_addr.strip().lower()


def _normalize_peer_addr(peer_addr: str) -> str:
    return normalize_peer_addr(peer_addr)


@dataclass
class HistoryEntry:
    kind: str
    text: str
    ts: str  # ISO-8601 UTC
    message_id: Optional[str] = None
    delivery_state: Optional[str] = None
    delivery_route: Optional[str] = None
    delivery_hint: str = ""
    delivery_reason: str = ""
    retryable: bool = False


def _safe_peer_id(peer_addr: str) -> str:
    """SHA-256 of the normalised peer address (full hex digest)."""
    normalized = _normalize_peer_addr(peer_addr)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _legacy_safe_peer_id(peer_addr: str) -> str:
    """Legacy peer id format (first 16 hex chars of SHA-256)."""
    normalized = _normalize_peer_addr(peer_addr)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:LEGACY_PEER_ID_HEX_LEN]


def _history_path(profile_data_dir: str, profile: str, peer_addr: str) -> str:
    pid = _safe_peer_id(peer_addr)
    return os.path.join(profile_data_dir, f"{profile}.history.{pid}.enc")


def _legacy_history_path(profile_data_dir: str, profile: str, peer_addr: str) -> str:
    pid = _legacy_safe_peer_id(peer_addr)
    return os.path.join(profile_data_dir, f"{profile}.history.{pid}.enc")


def _history_search_roots(
    profile_data_dir: str, app_data_root: Optional[str] = None
) -> list[str]:
    roots = [os.path.abspath(profile_data_dir)]
    if app_data_root:
        ar = os.path.abspath(app_data_root)
        if ar not in roots:
            roots.append(ar)
    return roots


def _history_path_candidates(
    profile_data_dir: str,
    profile: str,
    peer_addr: str,
    *,
    app_data_root: Optional[str] = None,
) -> list[str]:
    out: list[str] = []
    for root in _history_search_roots(profile_data_dir, app_data_root):
        cur = _history_path(root, profile, peer_addr)
        if cur not in out:
            out.append(cur)
        leg = _legacy_history_path(root, profile, peer_addr)
        if leg not in out:
            out.append(leg)
    return out


def _resolve_existing_history_path(
    profile_data_dir: str,
    profile: str,
    peer_addr: str,
    *,
    app_data_root: Optional[str] = None,
) -> Optional[str]:
    for candidate in _history_path_candidates(
        profile_data_dir, profile, peer_addr, app_data_root=app_data_root
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def derive_history_key(identity_key_bytes: bytes) -> bytes:
    """Derive a 32-byte profile-level master key for history encryption."""
    prk = crypto.hkdf_extract(b"I2PCHAT-HISTORY", identity_key_bytes)
    return crypto.hkdf_expand(prk, b"I2PCHAT-HISTORY|profile-key", 32)


def _derive_file_key(profile_key: bytes, salt: bytes, peer_addr: str) -> bytes:
    peer_id = _normalize_peer_addr(peer_addr).encode("utf-8")
    prk = crypto.hkdf_extract(salt, profile_key)
    return crypto.hkdf_expand(prk, b"I2PCHAT-HISTORY|file-key|" + peer_id, 32)


def list_history_file_names(
    profile_data_dir: str,
    profile: str,
    *,
    app_data_root: Optional[str] = None,
) -> list[str]:
    prefix = f"{profile}.history."
    names: set[str] = set()
    for root in _history_search_roots(profile_data_dir, app_data_root):
        try:
            for name in os.listdir(root):
                if name.startswith(prefix) and name.endswith(".enc"):
                    names.add(name)
        except FileNotFoundError:
            continue
    return sorted(names)


def list_history_file_paths(
    profile_data_dir: str,
    profile: str,
    *,
    app_data_root: Optional[str] = None,
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    prefix = f"{profile}.history."
    for root in _history_search_roots(profile_data_dir, app_data_root):
        try:
            for name in os.listdir(root):
                if name.startswith(prefix) and name.endswith(".enc"):
                    full = os.path.join(root, name)
                    if full not in seen:
                        seen.add(full)
                        paths.append(full)
        except FileNotFoundError:
            continue
    paths.sort()
    return paths


def list_history_files(
    profile_data_dir: str,
    profile: str,
    *,
    app_data_root: Optional[str] = None,
) -> list[str]:
    """Absolute paths to encrypted history files for a profile."""
    return list_history_file_paths(
        profile_data_dir, profile, app_data_root=app_data_root
    )


def _parse_history_entry_ts(raw_ts: str) -> Optional[datetime]:
    value = (raw_ts or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def apply_history_retention(
    entries: List[HistoryEntry],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_age_days: int = 0,
    now_utc: Optional[datetime] = None,
) -> tuple[List[HistoryEntry], Optional[str]]:
    retained = list(entries)
    truncated_at: Optional[str] = None
    if max_age_days > 0:
        ref_now = now_utc or datetime.now(timezone.utc)
        cutoff = ref_now - timedelta(days=max_age_days)
        kept: list[HistoryEntry] = []
        dropped_first_ts: Optional[str] = None
        for entry in retained:
            dt = _parse_history_entry_ts(entry.ts)
            if dt is not None and dt < cutoff:
                if dropped_first_ts is None:
                    dropped_first_ts = entry.ts
                continue
            kept.append(entry)
        if dropped_first_ts:
            truncated_at = dropped_first_ts
        retained = kept
    if max_messages > 0 and len(retained) > max_messages:
        cutoff_idx = len(retained) - max_messages
        dropped = retained[:cutoff_idx]
        if dropped:
            truncated_at = dropped[0].ts
        retained = retained[-max_messages:]
    return retained, truncated_at


def apply_history_retention_policy(
    entries: List[HistoryEntry],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_age_days: int = 0,
    now_utc: Optional[datetime] = None,
) -> tuple[List[HistoryEntry], Optional[str]]:
    return apply_history_retention(
        entries,
        max_messages=max_messages,
        max_age_days=max_age_days,
        now_utc=now_utc,
    )


def _entries_to_json(
    peer_addr: str,
    entries: List[HistoryEntry],
    truncated_at: Optional[str],
) -> bytes:
    obj = {
        "version": HISTORY_VERSION,
        "peer": peer_addr.strip().lower(),
        "messages": [
            {
                "kind": e.kind,
                "text": e.text,
                "ts": e.ts,
                "message_id": e.message_id,
                "delivery_state": e.delivery_state,
                "delivery_route": e.delivery_route,
                "delivery_hint": e.delivery_hint,
                "delivery_reason": e.delivery_reason,
                "retryable": bool(e.retryable),
            }
            for e in entries
        ],
        "truncated_at": truncated_at,
    }
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _json_to_entries(data: bytes) -> tuple[str, List[HistoryEntry], Optional[str]]:
    obj = json.loads(data.decode("utf-8"))
    version = int(obj.get("version", 1))
    if version not in {1, HISTORY_VERSION}:
        raise ValueError("Unsupported history format version")
    peer = str(obj.get("peer", ""))
    messages = obj.get("messages", [])
    entries = []
    for m in messages:
        entries.append(
            HistoryEntry(
                kind=str(m.get("kind", "peer")),
                text=str(m.get("text", "")),
                ts=str(m.get("ts", "")),
                message_id=str(m.get("message_id", "")) or None,
                delivery_state=str(m.get("delivery_state", "")) or None,
                delivery_route=str(m.get("delivery_route", "")) or None,
                delivery_hint=str(m.get("delivery_hint", "")),
                delivery_reason=str(m.get("delivery_reason", "")),
                retryable=bool(m.get("retryable", False)),
            )
        )
    truncated_at = obj.get("truncated_at")
    if truncated_at is not None:
        truncated_at = str(truncated_at)
    return peer, entries, truncated_at


def save_history(
    profile_data_dir: str,
    profile: str,
    peer_addr: str,
    entries: List[HistoryEntry],
    identity_key: bytes,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_age_days: int = 0,
    *,
    app_data_root: Optional[str] = None,
) -> None:
    """Encrypt and atomically write chat history for a peer into ``profile_data_dir``."""
    if not entries:
        return
    if not crypto.NACL_AVAILABLE:
        logger.warning("PyNaCl not available — cannot save encrypted history")
        return

    path = _history_path(profile_data_dir, profile, peer_addr)

    # Try to reuse the existing salt so the file key stays stable.
    salt = _read_existing_salt(path)
    if salt is None:
        legacy_path = _legacy_history_path(profile_data_dir, profile, peer_addr)
        if legacy_path != path:
            salt = _read_existing_salt(legacy_path)
    if salt is None and app_data_root:
        for cand in _history_path_candidates(
            profile_data_dir, profile, peer_addr, app_data_root=app_data_root
        ):
            salt = _read_existing_salt(cand)
            if salt is not None:
                break
    if salt is None:
        salt = secrets.token_bytes(SALT_SIZE)

    entries, truncated_at = apply_history_retention(
        entries,
        max_messages=max_messages,
        max_age_days=max_age_days,
    )

    profile_key = derive_history_key(identity_key)
    file_key = _derive_file_key(profile_key, salt, peer_addr)

    plaintext = _entries_to_json(peer_addr, entries, truncated_at)
    ciphertext = crypto.encrypt_message(file_key, plaintext)

    header = HISTORY_MAGIC + struct.pack(">H", HISTORY_VERSION) + salt
    atomic_write_bytes(path, header + ciphertext)


def load_history(
    profile_data_dir: str,
    profile: str,
    peer_addr: str,
    identity_key: bytes,
    *,
    app_data_root: Optional[str] = None,
) -> List[HistoryEntry]:
    """Decrypt and return chat history for a peer.  Returns [] on any error."""
    if not crypto.NACL_AVAILABLE:
        logger.warning("PyNaCl not available — cannot load encrypted history")
        return []

    path = _resolve_existing_history_path(
        profile_data_dir, profile, peer_addr, app_data_root=app_data_root
    )
    if path is None:
        return []

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        logger.warning("Failed to read history file %s: %s", path, e)
        return []

    if len(raw) < HEADER_SIZE:
        logger.warning("History file too short: %s", path)
        return []

    magic = raw[:4]
    if magic != HISTORY_MAGIC:
        logger.warning("Bad magic in history file: %s", path)
        return []

    version = struct.unpack(">H", raw[4:6])[0]
    if version != HISTORY_VERSION:
        logger.warning("Unsupported history version %d in %s", version, path)
        return []

    salt = raw[6:38]
    ciphertext = raw[38:]

    profile_key = derive_history_key(identity_key)
    file_key = _derive_file_key(profile_key, salt, peer_addr)

    plaintext = crypto.decrypt_message(file_key, ciphertext)
    if plaintext is None:
        logger.warning("Decryption failed for history file %s (wrong key?)", path)
        return []

    try:
        _peer, entries, _truncated = _json_to_entries(plaintext)
        expected_peer = _normalize_peer_addr(peer_addr)
        if _normalize_peer_addr(_peer) != expected_peer:
            logger.warning(
                "Peer mismatch in history file %s: expected %s, got %s",
                path,
                expected_peer,
                _peer,
            )
            return []
        return entries
    except Exception as e:
        logger.warning("Failed to parse history JSON from %s: %s", path, e)
        return []


def delete_history(
    profile_data_dir: str,
    profile: str,
    peer_addr: str,
    *,
    app_data_root: Optional[str] = None,
) -> bool:
    """Remove the encrypted history file for a peer.  Returns True if deleted."""
    deleted_any = False
    for path in _history_path_candidates(
        profile_data_dir, profile, peer_addr, app_data_root=app_data_root
    ):
        try:
            os.remove(path)
            deleted_any = True
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("Failed to delete history file %s: %s", path, e)
    return deleted_any


def _read_existing_salt(path: str) -> Optional[bytes]:
    """Read the salt from an existing history file (if valid)."""
    try:
        with open(path, "rb") as f:
            header = f.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            return None
        if header[:4] != HISTORY_MAGIC:
            return None
        return header[6:38]
    except OSError:
        return None
