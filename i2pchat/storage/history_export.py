"""
Encrypted chat history export/import for I2PChat.

File format (binary):
  4 bytes  — magic b"I2HX"
  2 bytes  — format version (big-endian uint16, currently 1)
  32 bytes — random salt for pwhash key derivation
  rest     — NaCl SecretBox ciphertext (nonce prepended by SecretBox)

The ciphertext wraps a UTF-8 JSON payload:
  {
    "version": 1,
    "profile": str,
    "export_ts": str (ISO-8601 UTC),
    "peers": [
      {
        "addr": str,
        "entries": [HistoryEntry dict, ...]
      },
      ...
    ]
  }

Key derivation uses nacl.pwhash (Argon2id) with the embedded salt.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import struct
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional

from i2pchat import crypto
from i2pchat.storage.chat_history import (
    HistoryEntry,
    _normalize_peer_addr,
    load_history,
    save_history,
)

logger = logging.getLogger("i2pchat.history_export")

EXPORT_MAGIC = b"I2HX"
EXPORT_VERSION = 1
EXPORT_SALT_SIZE = 16  # argon2id requires exactly 16 bytes
EXPORT_HEADER_SIZE = 4 + 2 + EXPORT_SALT_SIZE  # magic + version + salt

# Conflict strategies for import
CONFLICT_MERGE = "merge"
CONFLICT_REPLACE = "replace"
CONFLICT_SKIP = "skip"
VALID_CONFLICT_STRATEGIES = {CONFLICT_MERGE, CONFLICT_REPLACE, CONFLICT_SKIP}

# Required fields for a HistoryEntry dict
_REQUIRED_ENTRY_FIELDS = {"kind", "text", "ts"}


def _derive_export_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from password + salt using nacl.pwhash (Argon2id)."""
    try:
        from nacl.pwhash import argon2id
    except ImportError:
        raise RuntimeError("PyNaCl with pwhash support is required for history export")

    password_bytes = password.encode("utf-8") if isinstance(password, str) else password
    return argon2id.kdf(
        32,
        password_bytes,
        salt,
        opslimit=argon2id.OPSLIMIT_MODERATE,
        memlimit=argon2id.MEMLIMIT_MODERATE,
    )


def _entry_to_dict(entry: HistoryEntry) -> dict:
    return {
        "kind": entry.kind,
        "text": entry.text,
        "ts": entry.ts,
        "message_id": entry.message_id,
        "delivery_state": entry.delivery_state,
        "delivery_route": entry.delivery_route,
        "delivery_hint": entry.delivery_hint,
        "delivery_reason": entry.delivery_reason,
        "retryable": bool(entry.retryable),
    }


def _dict_to_entry(d: dict) -> HistoryEntry:
    """Validate and convert a dict to HistoryEntry. Raises ValueError on bad data."""
    missing = _REQUIRED_ENTRY_FIELDS - set(d.keys())
    if missing:
        raise ValueError(f"HistoryEntry missing fields: {missing}")
    return HistoryEntry(
        kind=str(d["kind"]),
        text=str(d["text"]),
        ts=str(d["ts"]),
        message_id=str(d.get("message_id", "") or "") or None,
        delivery_state=str(d.get("delivery_state", "") or "") or None,
        delivery_route=str(d.get("delivery_route", "") or "") or None,
        delivery_hint=str(d.get("delivery_hint", "")),
        delivery_reason=str(d.get("delivery_reason", "")),
        retryable=bool(d.get("retryable", False)),
    )


def _build_payload(
    profile_name: str,
    peer_entries: dict[str, List[HistoryEntry]],
) -> bytes:
    """Serialize export payload to UTF-8 JSON bytes."""
    peers = []
    for addr, entries in peer_entries.items():
        peers.append({
            "addr": addr,
            "entries": [_entry_to_dict(e) for e in entries],
        })
    obj = {
        "version": EXPORT_VERSION,
        "profile": profile_name,
        "export_ts": datetime.now(timezone.utc).isoformat(),
        "peers": peers,
    }
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _parse_payload(data: bytes) -> dict:
    """Parse and validate the export payload JSON. Raises ValueError on bad structure."""
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Export payload must be a JSON object")
    if obj.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {obj.get('version')}")
    if "profile" not in obj or not isinstance(obj["profile"], str):
        raise ValueError("Export payload missing 'profile' field")
    if "peers" not in obj or not isinstance(obj["peers"], list):
        raise ValueError("Export payload missing 'peers' list")
    for i, peer_obj in enumerate(obj["peers"]):
        if not isinstance(peer_obj, dict):
            raise ValueError(f"peers[{i}] is not an object")
        if "addr" not in peer_obj or not isinstance(peer_obj["addr"], str):
            raise ValueError(f"peers[{i}] missing 'addr'")
        if "entries" not in peer_obj or not isinstance(peer_obj["entries"], list):
            raise ValueError(f"peers[{i}] missing 'entries'")
        for j, entry_d in enumerate(peer_obj["entries"]):
            if not isinstance(entry_d, dict):
                raise ValueError(f"peers[{i}].entries[{j}] is not an object")
            missing = _REQUIRED_ENTRY_FIELDS - set(entry_d.keys())
            if missing:
                raise ValueError(f"peers[{i}].entries[{j}] missing fields: {missing}")
    return obj


def export_history(
    profile_name: str,
    identity_key: bytes,
    peers: Optional[List[str]],
    password: str,
    output_path: str,
    profiles_dir: str,
) -> None:
    """
    Export encrypted chat history to an archive file.

    Args:
        profile_name: Name of the profile.
        identity_key: 32-byte identity key for decrypting history files.
        peers: List of peer addresses to export, or None/[] for all peers.
        password: Password for encrypting the archive.
        output_path: Destination file path.
        profiles_dir: Directory containing encrypted history files.
    """
    if not crypto.NACL_AVAILABLE:
        raise RuntimeError("PyNaCl not available — cannot export history")

    # Discover peers if not specified
    if not peers:
        peers = _discover_peers(profiles_dir, profile_name, identity_key)

    peer_entries: dict[str, List[HistoryEntry]] = {}
    for peer_addr in peers:
        entries = load_history(profiles_dir, profile_name, peer_addr, identity_key)
        if entries:
            peer_entries[_normalize_peer_addr(peer_addr)] = entries

    if not peer_entries:
        logger.warning("No history found to export for profile %r", profile_name)

    salt = secrets.token_bytes(EXPORT_SALT_SIZE)
    export_key = _derive_export_key(password, salt)

    plaintext = _build_payload(profile_name, peer_entries)
    ciphertext = crypto.encrypt_message(export_key, plaintext)

    header = EXPORT_MAGIC + struct.pack(">H", EXPORT_VERSION) + salt

    # Write atomically via temp file
    tmp_path = output_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(header + ciphertext)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def import_history(
    archive_path: str,
    password: str,
    identity_key: bytes,
    profiles_dir: str,
    conflict_strategy: str,
    profile_name: Optional[str] = None,
) -> dict[str, int]:
    """
    Import encrypted chat history from an archive.

    Args:
        archive_path: Path to the .i2hx archive.
        password: Password used when exporting.
        identity_key: 32-byte identity key for re-encrypting history.
        profiles_dir: Directory to write history files into.
        conflict_strategy: One of 'merge', 'replace', 'skip'.
        profile_name: Override the profile name from the archive. Uses archive value if None.

    Returns:
        Dict mapping peer_addr -> number of entries written (0 = skipped).
    """
    if conflict_strategy not in VALID_CONFLICT_STRATEGIES:
        raise ValueError(
            f"conflict_strategy must be one of {VALID_CONFLICT_STRATEGIES}, got {conflict_strategy!r}"
        )
    if not crypto.NACL_AVAILABLE:
        raise RuntimeError("PyNaCl not available — cannot import history")

    try:
        with open(archive_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise OSError(f"Cannot read archive {archive_path}: {e}") from e

    if len(raw) < EXPORT_HEADER_SIZE:
        raise ValueError("Archive file too short")

    magic = raw[:4]
    if magic != EXPORT_MAGIC:
        raise ValueError(f"Invalid archive magic: {magic!r}")

    version = struct.unpack(">H", raw[4:6])[0]
    if version != EXPORT_VERSION:
        raise ValueError(f"Unsupported archive version: {version}")

    salt = raw[6:EXPORT_HEADER_SIZE]
    ciphertext = raw[EXPORT_HEADER_SIZE:]

    export_key = _derive_export_key(password, salt)
    plaintext = crypto.decrypt_message(export_key, ciphertext)
    if plaintext is None:
        raise ValueError("Decryption failed — wrong password or corrupted archive")

    payload = _parse_payload(plaintext)

    target_profile = profile_name or payload["profile"]
    results: dict[str, int] = {}

    for peer_obj in payload["peers"]:
        peer_addr = _normalize_peer_addr(peer_obj["addr"])
        imported_entries = [_dict_to_entry(e) for e in peer_obj["entries"]]

        existing = load_history(profiles_dir, target_profile, peer_addr, identity_key)

        if conflict_strategy == CONFLICT_SKIP:
            if existing:
                results[peer_addr] = 0
                continue
            merged = imported_entries

        elif conflict_strategy == CONFLICT_REPLACE:
            merged = imported_entries

        else:  # CONFLICT_MERGE
            merged = _merge_entries(existing, imported_entries)

        # Re-encrypt and save with the current profile's key derivation
        save_history(
            profiles_dir,
            target_profile,
            peer_addr,
            merged,
            identity_key,
        )
        results[peer_addr] = len(merged)

    return results


def _merge_entries(
    existing: List[HistoryEntry],
    imported: List[HistoryEntry],
) -> List[HistoryEntry]:
    """Merge imported entries into existing, deduplicating by (message_id, ts)."""
    seen: set[tuple] = set()
    merged: List[HistoryEntry] = []

    for entry in existing:
        key = (entry.message_id or "", entry.ts)
        seen.add(key)
        merged.append(entry)

    for entry in imported:
        key = (entry.message_id or "", entry.ts)
        if key not in seen:
            seen.add(key)
            merged.append(entry)

    # Sort by timestamp so history stays chronological
    merged.sort(key=lambda e: e.ts)
    return merged


def _discover_peers(
    profiles_dir: str,
    profile_name: str,
    identity_key: bytes,
) -> List[str]:
    """
    Discover peer addresses that have history files for the given profile.

    Note: History filenames use a SHA-256 hash of the peer address, so they
    cannot be reversed without a known peer list. This function returns an
    empty list — callers should supply a peers list explicitly to export_history.
    """
    return []
