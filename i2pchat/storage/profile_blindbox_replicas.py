"""
Per-profile Blind Box replica endpoints (GUI-editable when not overridden by env).

File: {profile_data_dir}/{profile}.blindbox_replicas.json (``profile_data_dir`` = ``profiles/<profile>/``).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import struct
from typing import Any

from i2pchat import crypto
from i2pchat.core.transient_profile import is_transient_profile_name
from i2pchat.storage.blindbox_state import atomic_write_json

logger = logging.getLogger("i2pchat.storage.profile_blindbox_replicas")

# Version 3 stores the per-endpoint auth tokens (bearer secrets) encrypted at
# rest under the profile identity key instead of as plaintext ``replica_auth``.
PROFILE_BLINDBOX_REPLICAS_VERSION = 3
_SUPPORTED_LOAD_VERSIONS = frozenset({1, 2, 3})

_REPLICA_AUTH_MAGIC = b"I2RA"
_REPLICA_AUTH_ENC_VERSION = 1
_REPLICA_AUTH_SALT_SIZE = 32
_REPLICA_AUTH_HEADER_SIZE = 4 + 2 + _REPLICA_AUTH_SALT_SIZE


def _derive_replica_auth_key(identity_key: bytes, salt: bytes) -> bytes:
    prk = crypto.hkdf_extract(b"I2PCHAT-REPLICA-AUTH", identity_key)
    profile_key = crypto.hkdf_expand(prk, b"I2PCHAT-REPLICA-AUTH|profile-key", 32)
    prk2 = crypto.hkdf_extract(salt, profile_key)
    return crypto.hkdf_expand(prk2, b"I2PCHAT-REPLICA-AUTH|file-key", 32)


def _encrypt_replica_auth(auth: dict[str, str], identity_key: bytes) -> str:
    salt = secrets.token_bytes(_REPLICA_AUTH_SALT_SIZE)
    key = _derive_replica_auth_key(identity_key, salt)
    plaintext = json.dumps(auth, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    ciphertext = crypto.encrypt_message(key, plaintext)
    header = _REPLICA_AUTH_MAGIC + struct.pack(">H", _REPLICA_AUTH_ENC_VERSION) + salt
    return base64.b64encode(header + ciphertext).decode("ascii")


def _decrypt_replica_auth(blob: str, identity_key: bytes) -> dict[str, str]:
    raw = base64.b64decode(blob.encode("ascii"), validate=True)
    if len(raw) < _REPLICA_AUTH_HEADER_SIZE or raw[:4] != _REPLICA_AUTH_MAGIC:
        raise ValueError("Bad replica_auth blob header")
    version = struct.unpack(">H", raw[4:6])[0]
    if version != _REPLICA_AUTH_ENC_VERSION:
        raise ValueError(f"Unsupported replica_auth blob version {version}")
    salt = raw[6:_REPLICA_AUTH_HEADER_SIZE]
    ciphertext = raw[_REPLICA_AUTH_HEADER_SIZE:]
    key = _derive_replica_auth_key(identity_key, salt)
    plaintext = crypto.decrypt_message(key, ciphertext)
    if plaintext is None:
        raise ValueError("replica_auth decryption failed (wrong key or tampered)")
    obj = json.loads(plaintext.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("replica_auth blob is not an object")
    return {str(k): str(v) for k, v in obj.items()}


def profile_blindbox_replicas_path(profiles_dir: str, profile: str) -> str:
    safe = (profile or "").strip()
    if not safe or is_transient_profile_name(safe):
        raise ValueError("profile must be a named persistent profile")
    return os.path.join(profiles_dir, f"{safe}.blindbox_replicas.json")


def normalize_replica_endpoints(raw: list[str]) -> list[str]:
    """Strip, drop empties, preserve first-seen order (like _parse_replicas_list)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        candidate = (item or "").strip()
        if not candidate or candidate.startswith("#") or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _replica_auth_subset(replicas: list[str], raw: Any) -> dict[str, str]:
    """Keep only non-empty tokens for keys that appear exactly in ``replicas``."""
    rep_set = set(replicas)
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        val = str(v).strip() if v is not None else ""
        if not key or not val:
            continue
        if key not in rep_set:
            logger.warning(
                "BlindBox replica_auth key not in replicas list, ignored: %s", key
            )
            continue
        out[key] = val
    return out


def load_profile_blindbox_replicas_bundle(
    profiles_dir: str,
    profile: str,
    *,
    identity_key: bytes | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Load normalized replicas and per-endpoint auth map. Returns ([], {}) if missing/invalid.

    Auth tokens (bearer secrets) are stored encrypted at rest (version 3); pass
    ``identity_key`` to decrypt them. Without a key, encrypted auth is skipped
    and the returned auth map is empty (replicas list is still returned).
    """
    if is_transient_profile_name(profile):
        return [], {}
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    if not os.path.isfile(path):
        return [], {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("BlindBox profile replicas load failed (%s): %s", path, e)
        return [], {}
    if not isinstance(data, dict):
        return [], {}
    ver = int(data.get("version", 0))
    if ver not in _SUPPORTED_LOAD_VERSIONS:
        return [], {}
    reps = data.get("replicas")
    if not isinstance(reps, list):
        return [], {}
    strings = [str(x).strip() for x in reps if str(x).strip()]
    replicas = normalize_replica_endpoints(strings)
    if ver == 1:
        return replicas, {}
    if ver >= 3:
        blob = data.get("replica_auth_enc")
        if isinstance(blob, str) and blob:
            if not identity_key or not crypto.NACL_AVAILABLE:
                logger.debug(
                    "Encrypted replica_auth present but no identity key — skipping"
                )
                return replicas, {}
            try:
                decrypted = _decrypt_replica_auth(blob, identity_key)
            except Exception as e:
                logger.warning("BlindBox replica_auth decrypt failed: %s", e)
                return replicas, {}
            return replicas, _replica_auth_subset(replicas, decrypted)
        # v3 file may still carry legacy plaintext (e.g. written without a key).
    return replicas, _replica_auth_subset(replicas, data.get("replica_auth"))


def load_profile_blindbox_replicas_list(profiles_dir: str, profile: str) -> list[str]:
    """Returns non-empty list if file exists and valid; otherwise []."""
    reps, _ = load_profile_blindbox_replicas_bundle(profiles_dir, profile)
    return reps


def save_profile_blindbox_replicas_bundle(
    profiles_dir: str,
    profile: str,
    replicas: list[str],
    replica_auth: dict[str, str],
    *,
    identity_key: bytes | None = None,
) -> None:
    normalized = normalize_replica_endpoints(replicas)
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    auth_clean = _replica_auth_subset(normalized, replica_auth)
    payload: dict[str, Any] = {
        "version": PROFILE_BLINDBOX_REPLICAS_VERSION,
        "replicas": normalized,
    }
    if auth_clean and identity_key and crypto.NACL_AVAILABLE:
        payload["replica_auth_enc"] = _encrypt_replica_auth(auth_clean, identity_key)
    elif auth_clean:
        # No identity key / NaCl: fall back to plaintext so functionality is
        # preserved, but warn since tokens then sit unencrypted on disk.
        logger.warning(
            "Storing BlindBox replica_auth without at-rest encryption "
            "(no identity key or PyNaCl unavailable)"
        )
        payload["replica_auth"] = auth_clean
    atomic_write_json(path, payload)


def save_profile_blindbox_replicas_list(
    profiles_dir: str, profile: str, replicas: list[str]
) -> None:
    save_profile_blindbox_replicas_bundle(profiles_dir, profile, replicas, {})


def delete_profile_blindbox_replicas_file(profiles_dir: str, profile: str) -> None:
    if is_transient_profile_name(profile):
        return
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    try:
        os.unlink(path)
    except OSError:
        pass
