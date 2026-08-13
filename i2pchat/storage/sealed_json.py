"""At-rest JSON blobs wrapped in NaCl SecretBox (identity-keyed).

On-disk format:
  4 bytes  — magic
  2 bytes  — wrap version (big-endian uint16, currently 1)
  32 bytes — salt
  rest     — SecretBox(JSON)

Legacy plaintext UTF-8 JSON is still read and re-encrypted on the next save
when an identity key is available.
"""

from __future__ import annotations

import json
import logging
import secrets
import struct
from typing import Any

from i2pchat import crypto
from i2pchat.storage.blindbox_state import atomic_write_bytes, atomic_write_json

logger = logging.getLogger("i2pchat.storage.sealed_json")

SEALED_JSON_ENC_VERSION = 1
SEALED_JSON_SALT_SIZE = 32
SEALED_JSON_HEADER_SIZE = 4 + 2 + SEALED_JSON_SALT_SIZE


def _derive_file_key(identity_key: bytes, salt: bytes, domain: bytes) -> bytes:
    prk = crypto.hkdf_extract(domain, identity_key)
    profile_key = crypto.hkdf_expand(prk, domain + b"|profile-key", 32)
    prk2 = crypto.hkdf_extract(salt, profile_key)
    return crypto.hkdf_expand(prk2, domain + b"|file-key", 32)


def _read_existing_salt(path: str, magic: bytes) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(SEALED_JSON_HEADER_SIZE)
    except OSError:
        return None
    if len(header) < SEALED_JSON_HEADER_SIZE or header[:4] != magic:
        return None
    return header[6:SEALED_JSON_HEADER_SIZE]


def is_sealed_json_file(path: str, magic: bytes) -> bool:
    return _read_existing_salt(path, magic) is not None


def write_sealed_json(
    path: str,
    payload: dict[str, Any],
    *,
    identity_key: bytes | None,
    magic: bytes,
    domain: bytes,
) -> None:
    existing_encrypted = is_sealed_json_file(path, magic)
    if not identity_key or not crypto.NACL_AVAILABLE:
        if existing_encrypted:
            logger.warning(
                "refusing to overwrite encrypted %s without an identity key",
                path,
            )
            return
        if identity_key and not crypto.NACL_AVAILABLE:
            logger.warning(
                "PyNaCl unavailable — writing %s without at-rest encryption",
                path,
            )
        atomic_write_json(path, payload)
        return
    salt = _read_existing_salt(path, magic) or secrets.token_bytes(SEALED_JSON_SALT_SIZE)
    file_key = _derive_file_key(identity_key, salt, domain)
    plaintext = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    ciphertext = crypto.encrypt_message(file_key, plaintext)
    header = magic + struct.pack(">H", SEALED_JSON_ENC_VERSION) + salt
    atomic_write_bytes(path, header + ciphertext)


def read_sealed_json(
    path: str,
    *,
    identity_key: bytes | None,
    magic: bytes,
    domain: bytes,
) -> dict[str, Any]:
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:4] != magic:
        return json.loads(raw.decode("utf-8"))
    if len(raw) < SEALED_JSON_HEADER_SIZE:
        raise ValueError("Sealed JSON record truncated")
    if not identity_key or not crypto.NACL_AVAILABLE:
        raise ValueError("Record is encrypted but no identity key is available")
    version = struct.unpack(">H", raw[4:6])[0]
    if version != SEALED_JSON_ENC_VERSION:
        raise ValueError(f"Unsupported sealed JSON version {version}")
    salt = raw[6:SEALED_JSON_HEADER_SIZE]
    ciphertext = raw[SEALED_JSON_HEADER_SIZE:]
    file_key = _derive_file_key(identity_key, salt, domain)
    plaintext = crypto.decrypt_message(file_key, ciphertext)
    if plaintext is None:
        raise ValueError("Sealed JSON decryption failed (wrong key or tampered)")
    payload = json.loads(plaintext.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Sealed JSON payload must be an object")
    return payload
