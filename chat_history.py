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
from dataclasses import dataclass
from typing import List, Optional

import crypto
from blindbox_state import atomic_write_bytes

logger = logging.getLogger("i2pchat.history")

HISTORY_MAGIC = b"I2CH"
HISTORY_VERSION = 1
HEADER_SIZE = 4 + 2 + 32  # magic + version + salt
SALT_SIZE = 32
DEFAULT_MAX_MESSAGES = 1000


def _normalize_peer_addr(peer_addr: str) -> str:
    return peer_addr.strip().lower()


@dataclass
class HistoryEntry:
    kind: str
    text: str
    ts: str  # ISO-8601 UTC


def _safe_peer_id(peer_addr: str) -> str:
    """SHA-256 of the normalised peer address (full hex digest)."""
    normalized = _normalize_peer_addr(peer_addr)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _history_path(profiles_dir: str, profile: str, peer_addr: str) -> str:
    pid = _safe_peer_id(peer_addr)
    return os.path.join(profiles_dir, f"{profile}.history.{pid}.enc")


def derive_history_key(identity_key_bytes: bytes) -> bytes:
    """Derive a 32-byte profile-level master key for history encryption."""
    prk = crypto.hkdf_extract(b"I2PCHAT-HISTORY", identity_key_bytes)
    return crypto.hkdf_expand(prk, b"I2PCHAT-HISTORY|profile-key", 32)


def _derive_file_key(profile_key: bytes, salt: bytes, peer_addr: str) -> bytes:
    peer_id = _normalize_peer_addr(peer_addr).encode("utf-8")
    prk = crypto.hkdf_extract(salt, profile_key)
    return crypto.hkdf_expand(prk, b"I2PCHAT-HISTORY|file-key|" + peer_id, 32)


def _entries_to_json(
    peer_addr: str,
    entries: List[HistoryEntry],
    truncated_at: Optional[str],
) -> bytes:
    obj = {
        "version": 1,
        "peer": peer_addr.strip().lower(),
        "messages": [
            {"kind": e.kind, "text": e.text, "ts": e.ts} for e in entries
        ],
        "truncated_at": truncated_at,
    }
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _json_to_entries(data: bytes) -> tuple[str, List[HistoryEntry], Optional[str]]:
    obj = json.loads(data.decode("utf-8"))
    if obj.get("version") != 1:
        raise ValueError("Unsupported history format version")
    peer = str(obj.get("peer", ""))
    messages = obj.get("messages", [])
    entries = []
    for m in messages:
        entries.append(HistoryEntry(
            kind=str(m.get("kind", "peer")),
            text=str(m.get("text", "")),
            ts=str(m.get("ts", "")),
        ))
    truncated_at = obj.get("truncated_at")
    if truncated_at is not None:
        truncated_at = str(truncated_at)
    return peer, entries, truncated_at


def save_history(
    profiles_dir: str,
    profile: str,
    peer_addr: str,
    entries: List[HistoryEntry],
    identity_key: bytes,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> None:
    """Encrypt and atomically write chat history for a peer."""
    if not entries:
        return
    if not crypto.NACL_AVAILABLE:
        logger.warning("PyNaCl not available — cannot save encrypted history")
        return

    path = _history_path(profiles_dir, profile, peer_addr)

    # Try to reuse the existing salt so the file key stays stable.
    salt = _read_existing_salt(path)
    if salt is None:
        salt = secrets.token_bytes(SALT_SIZE)

    truncated_at: Optional[str] = None
    if len(entries) > max_messages:
        truncated_at = entries[-max_messages - 1].ts if len(entries) > max_messages else None
        entries = entries[-max_messages:]

    profile_key = derive_history_key(identity_key)
    file_key = _derive_file_key(profile_key, salt, peer_addr)

    plaintext = _entries_to_json(peer_addr, entries, truncated_at)
    ciphertext = crypto.encrypt_message(file_key, plaintext)

    header = HISTORY_MAGIC + struct.pack(">H", HISTORY_VERSION) + salt
    atomic_write_bytes(path, header + ciphertext)


def load_history(
    profiles_dir: str,
    profile: str,
    peer_addr: str,
    identity_key: bytes,
) -> List[HistoryEntry]:
    """Decrypt and return chat history for a peer.  Returns [] on any error."""
    if not crypto.NACL_AVAILABLE:
        logger.warning("PyNaCl not available — cannot load encrypted history")
        return []

    path = _history_path(profiles_dir, profile, peer_addr)
    if not os.path.exists(path):
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
    profiles_dir: str,
    profile: str,
    peer_addr: str,
) -> bool:
    """Remove the encrypted history file for a peer.  Returns True if deleted."""
    path = _history_path(profiles_dir, profile, peer_addr)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("Failed to delete history file %s: %s", path, e)
        return False


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
