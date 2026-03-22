"""
BlindBox encrypted blob format.

Envelope is encrypted with SecretBox and includes direction/index/state tag
to enable strict validation and replay-safe state transitions.
"""

from __future__ import annotations

import os
import struct
from typing import Optional

import crypto

BLINDBOX_BLOB_MAGIC = b"BLNDBX01"
BLINDBOX_BLOB_VERSION = 1
BLINDBOX_HEADER = struct.Struct(">8sBBQ16sI")
BLINDBOX_MAX_FRAME_SIZE = 2 * 1024 * 1024

_DIRECTION_TO_CODE = {"send": 1, "recv": 2}
_CODE_TO_DIRECTION = {1: "send", 2: "recv"}


def _apply_padding(plaintext: bytes, padding_bucket: int) -> bytes:
    if padding_bucket <= 0:
        raise ValueError("padding_bucket must be positive")
    target_len = ((len(plaintext) + padding_bucket - 1) // padding_bucket) * padding_bucket
    pad_len = target_len - len(plaintext)
    if pad_len == 0:
        return plaintext
    return plaintext + os.urandom(pad_len)


def encrypt_blindbox_blob(
    frame: bytes,
    blob_key: bytes,
    direction: str,
    index: int,
    state_tag: bytes,
    *,
    padding_bucket: int = 256,
) -> bytes:
    if not crypto.NACL_AVAILABLE:
        raise RuntimeError("PyNaCl is required for BlindBox blob encryption")
    if not isinstance(frame, (bytes, bytearray)) or len(frame) == 0:
        raise ValueError("frame must be non-empty bytes")
    if len(frame) > BLINDBOX_MAX_FRAME_SIZE:
        raise ValueError("frame is too large")
    if len(blob_key) != 32:
        raise ValueError("blob_key must be 32 bytes")
    if len(state_tag) != 16:
        raise ValueError("state_tag must be 16 bytes")
    if index < 0:
        raise ValueError("index must be non-negative")
    direction_code = _DIRECTION_TO_CODE.get((direction or "").strip().lower())
    if direction_code is None:
        raise ValueError("direction must be 'send' or 'recv'")

    header = BLINDBOX_HEADER.pack(
        BLINDBOX_BLOB_MAGIC,
        BLINDBOX_BLOB_VERSION,
        direction_code,
        int(index),
        bytes(state_tag),
        len(frame),
    )
    plaintext = _apply_padding(header + bytes(frame), padding_bucket)
    return crypto.encrypt_message(bytes(blob_key), plaintext)


def decrypt_blindbox_blob(
    blob: bytes,
    blob_key: bytes,
    *,
    expected_direction: Optional[str] = None,
    expected_index: Optional[int] = None,
    expected_state_tag: Optional[bytes] = None,
) -> bytes:
    if not crypto.NACL_AVAILABLE:
        raise RuntimeError("PyNaCl is required for BlindBox blob decryption")
    if len(blob_key) != 32:
        raise ValueError("blob_key must be 32 bytes")
    if expected_state_tag is not None and len(expected_state_tag) != 16:
        raise ValueError("expected_state_tag must be 16 bytes")

    decrypted = crypto.decrypt_message(bytes(blob_key), bytes(blob))
    if decrypted is None:
        raise ValueError("BlindBox blob decryption failed")
    if len(decrypted) < BLINDBOX_HEADER.size:
        raise ValueError("BlindBox blob too short")

    magic, version, direction_code, index, state_tag, frame_len = BLINDBOX_HEADER.unpack(
        decrypted[: BLINDBOX_HEADER.size]
    )
    if magic != BLINDBOX_BLOB_MAGIC:
        raise ValueError("BlindBox blob magic mismatch")
    if version != BLINDBOX_BLOB_VERSION:
        raise ValueError("Unsupported BlindBox blob version")
    if direction_code not in _CODE_TO_DIRECTION:
        raise ValueError("Invalid BlindBox direction code")
    if frame_len <= 0 or frame_len > BLINDBOX_MAX_FRAME_SIZE:
        raise ValueError("Invalid BlindBox frame length")

    payload = decrypted[BLINDBOX_HEADER.size :]
    if frame_len > len(payload):
        raise ValueError("Malformed BlindBox blob payload")

    direction_name = _CODE_TO_DIRECTION[direction_code]
    if expected_direction is not None:
        normalized_expected_direction = (expected_direction or "").strip().lower()
        if normalized_expected_direction != direction_name:
            raise ValueError("BlindBox direction mismatch")
    if expected_index is not None and int(expected_index) != int(index):
        raise ValueError("BlindBox index mismatch")
    if expected_state_tag is not None and state_tag != bytes(expected_state_tag):
        raise ValueError("BlindBox state tag mismatch")

    return payload[:frame_len]
