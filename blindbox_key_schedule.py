"""
BlindBox key schedule primitives.

This module provides deterministic per-message key derivation for offline
delivery with strict domain separation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import crypto

BLINDBOX_LOOKUP_V1 = b"BLINDBOX_LOOKUP_V1"
BLINDBOX_BLOB_V1 = b"BLINDBOX_BLOB_V1"
BLINDBOX_STATE_V1 = b"BLINDBOX_STATE_V1"


@dataclass(frozen=True)
class BlindBoxMessageKeys:
    lookup_token: str
    lookup_key: bytes
    blob_key: bytes
    state_tag: bytes
    direction_label: str
    index: int
    epoch: int


def _normalize_peer_id(peer_id: str) -> str:
    normalized = (peer_id or "").strip().lower()
    if not normalized:
        raise ValueError("Peer id cannot be empty")
    if normalized.endswith(".b32.i2p"):
        normalized = normalized[: -len(".b32.i2p")]
    return normalized


def _canonical_pair(local_peer_id: str, remote_peer_id: str) -> tuple[str, str]:
    local = _normalize_peer_id(local_peer_id)
    remote = _normalize_peer_id(remote_peer_id)
    if local == remote:
        raise ValueError("Local and remote peer ids must differ")
    low_id, high_id = sorted([local, remote])
    return low_id, high_id


def _direction_label(local_peer_id: str, remote_peer_id: str, direction: str) -> str:
    normalized_direction = (direction or "").strip().lower()
    if normalized_direction not in {"send", "recv"}:
        raise ValueError("direction must be 'send' or 'recv'")
    low_id, high_id = _canonical_pair(local_peer_id, remote_peer_id)
    local = _normalize_peer_id(local_peer_id)
    if local == low_id:
        send_label = "LOW_TO_HIGH"
        recv_label = "HIGH_TO_LOW"
    else:
        send_label = "HIGH_TO_LOW"
        recv_label = "LOW_TO_HIGH"
    return send_label if normalized_direction == "send" else recv_label


def derive_blindbox_message_keys(
    root_secret: bytes,
    local_peer_id: str,
    remote_peer_id: str,
    direction: str,
    index: int,
    *,
    epoch: int = 0,
) -> BlindBoxMessageKeys:
    if not isinstance(root_secret, (bytes, bytearray)) or len(root_secret) < 16:
        raise ValueError("root_secret must be bytes and at least 16 bytes long")
    if index < 0:
        raise ValueError("index must be non-negative")
    if epoch < 0:
        raise ValueError("epoch must be non-negative")

    low_id, high_id = _canonical_pair(local_peer_id, remote_peer_id)
    direction_label = _direction_label(local_peer_id, remote_peer_id, direction)
    index_bytes = int(index).to_bytes(8, "big", signed=False)

    salt = hashlib.sha256(
        b"BLINDBOX-SALT-V1|" + low_id.encode("utf-8") + b"|" + high_id.encode("utf-8")
    ).digest()
    prk = crypto.hkdf_extract(salt, bytes(root_secret))

    context = b"|".join(
        [
            low_id.encode("utf-8"),
            high_id.encode("utf-8"),
            direction_label.encode("ascii"),
            f"epoch={int(epoch)}".encode("ascii"),
            index_bytes.hex().encode("ascii"),
        ]
    )
    lookup_key = crypto.hkdf_expand(prk, BLINDBOX_LOOKUP_V1 + b"|" + context, 32)
    blob_key = crypto.hkdf_expand(prk, BLINDBOX_BLOB_V1 + b"|" + context, 32)
    state_tag = crypto.hkdf_expand(prk, BLINDBOX_STATE_V1 + b"|" + context, 16)

    # Server-facing lookup token is hash-derived from internal lookup key.
    lookup_token = hashlib.sha256(lookup_key).hexdigest()
    return BlindBoxMessageKeys(
        lookup_token=lookup_token,
        lookup_key=lookup_key,
        blob_key=blob_key,
        state_tag=state_tag,
        direction_label=direction_label,
        index=index,
        epoch=int(epoch),
    )
