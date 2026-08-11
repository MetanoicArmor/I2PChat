from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from i2pchat import crypto

from .models import normalize_member_id, utc_now

GROUP_INVITE_PREFIX = "__I2PCHAT_GROUP_INVITE__:"
# Protocol v4 (I2PChat 1.4.0): invites are Ed25519-signed by the inviter's
# handshake signing key. v1 (unsigned) invites are no longer accepted.
GROUP_INVITE_VERSION = 2
MAX_GROUP_INVITE_BYTES = 256 * 1024
_INVITE_SIG_DOMAIN = b"I2PCHAT-GROUP-INVITE-v2"


def _to_iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_members(raw_members: Any) -> tuple[str, ...]:
    if not isinstance(raw_members, (list, tuple)):
        raise ValueError("Group invite members must be a list")
    members: list[str] = []
    seen: set[str] = set()
    for raw in raw_members:
        member_id = normalize_member_id(str(raw or ""))
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        members.append(member_id)
    if not members:
        raise ValueError("Group invite must include at least one member")
    return tuple(members)


@dataclass(slots=True, frozen=True)
class GroupInvite:
    invite_id: str
    group_id: str
    members: tuple[str, ...]
    epoch: int
    inviter_id: str
    title: str | None = None
    created_at: datetime | None = None
    version: int = GROUP_INVITE_VERSION
    # v2: Ed25519 identity (handshake signing key) of the inviter and, once
    # encoded, the detached signature over the canonical invite payload.
    inviter_signing_pub: str = ""
    expires_at: datetime | None = None
    signature: str = ""

    def __post_init__(self) -> None:
        invite_id = (self.invite_id or "").strip()
        group_id = (self.group_id or "").strip()
        inviter_id = normalize_member_id(self.inviter_id)
        title = (self.title or "").strip() or None
        if not invite_id:
            raise ValueError("Group invite invite_id is required")
        if not group_id:
            raise ValueError("Group invite group_id is required")
        if not inviter_id:
            raise ValueError("Group invite inviter_id is required")
        members = _normalize_members(self.members)
        if inviter_id not in members:
            members = members + (inviter_id,)
        object.__setattr__(self, "invite_id", invite_id)
        object.__setattr__(self, "group_id", group_id)
        object.__setattr__(self, "inviter_id", inviter_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "epoch", int(self.epoch))
        object.__setattr__(self, "version", int(self.version))
        object.__setattr__(
            self, "inviter_signing_pub", (self.inviter_signing_pub or "").strip().lower()
        )
        object.__setattr__(self, "signature", (self.signature or "").strip().lower())


def build_group_invite(
    *,
    group_id: str,
    members: list[str] | tuple[str, ...],
    epoch: int,
    inviter_id: str,
    title: str | None = None,
    invite_id: str | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> GroupInvite:
    return GroupInvite(
        invite_id=(invite_id or secrets.token_hex(8)).strip(),
        group_id=group_id,
        members=tuple(members),
        epoch=int(epoch),
        inviter_id=inviter_id,
        title=title,
        created_at=created_at or utc_now(),
        expires_at=expires_at,
        version=GROUP_INVITE_VERSION,
    )


def _canonical_invite_bytes(
    *,
    version: int,
    invite_id: str,
    group_id: str,
    title: str | None,
    members: tuple[str, ...],
    epoch: int,
    inviter_id: str,
    inviter_signing_pub: str,
    created_at: str,
    expires_at: str | None,
) -> bytes:
    """Deterministic serialization signed by the inviter (excludes signature)."""
    payload = {
        "created_at": created_at,
        "epoch": int(epoch),
        "expires_at": expires_at,
        "group_id": group_id,
        "invite_id": invite_id,
        "inviter_id": inviter_id,
        "inviter_signing_pub": inviter_signing_pub,
        "members": list(members),
        "title": title,
        "v": int(version),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _INVITE_SIG_DOMAIN + b"|" + body.encode("utf-8")


def encode_group_invite(invite: GroupInvite, signing_seed: bytes) -> str:
    """Serialize and Ed25519-sign an invite with the inviter's signing seed.

    ``signing_seed`` is the inviter's handshake signing seed (the same key that
    is TOFU-pinned during the secure handshake), which binds the invite — and
    therefore the group roster snapshot — to the inviter's verified identity.
    """
    if not signing_seed:
        raise ValueError("A signing seed is required to sign a group invite")
    inviter_signing_pub = crypto.get_verify_key_from_seed(signing_seed).hex()
    created_at = _to_iso8601(invite.created_at or utc_now())
    expires_at = _to_iso8601(invite.expires_at) if invite.expires_at else None
    signed_bytes = _canonical_invite_bytes(
        version=GROUP_INVITE_VERSION,
        invite_id=invite.invite_id,
        group_id=invite.group_id,
        title=invite.title,
        members=invite.members,
        epoch=int(invite.epoch),
        inviter_id=invite.inviter_id,
        inviter_signing_pub=inviter_signing_pub,
        created_at=created_at,
        expires_at=expires_at,
    )
    signature = crypto.sign_data(signing_seed, signed_bytes).hex()
    payload = {
        "v": GROUP_INVITE_VERSION,
        "invite_id": invite.invite_id,
        "group_id": invite.group_id,
        "title": invite.title,
        "members": list(invite.members),
        "epoch": int(invite.epoch),
        "inviter_id": invite.inviter_id,
        "inviter_signing_pub": inviter_signing_pub,
        "created_at": created_at,
        "expires_at": expires_at,
        "signature": signature,
    }
    return GROUP_INVITE_PREFIX + json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False
    )


def decode_group_invite(text: str) -> GroupInvite:
    raw = (text or "").strip()
    if not raw.startswith(GROUP_INVITE_PREFIX):
        raise ValueError("Not a group invite string")
    # Bound untrusted input before json.loads to avoid CPU/memory DoS.
    if len(raw) > MAX_GROUP_INVITE_BYTES:
        raise ValueError("Group invite payload too large")
    body = raw[len(GROUP_INVITE_PREFIX) :].strip()
    if not body:
        raise ValueError("Empty group invite payload")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid group invite JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Group invite payload must be an object")

    version = int(payload.get("v", 0))
    if version != GROUP_INVITE_VERSION:
        raise ValueError(
            f"Unsupported group invite version: {version} "
            f"(v1 unsigned invites are rejected)"
        )

    invite_id = str(payload.get("invite_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    inviter_id = str(payload.get("inviter_id") or "").strip()
    title = str(payload.get("title") or "").strip() or None
    inviter_signing_pub = str(payload.get("inviter_signing_pub") or "").strip().lower()
    signature = str(payload.get("signature") or "").strip().lower()
    if not invite_id:
        raise ValueError("Missing required group invite field: invite_id")
    if not group_id:
        raise ValueError("Missing required group invite field: group_id")
    if not inviter_id:
        raise ValueError("Missing required group invite field: inviter_id")
    if len(inviter_signing_pub) != 64:
        raise ValueError("Missing or invalid inviter signing public key")
    if len(signature) != 128:
        raise ValueError("Missing or invalid group invite signature")

    try:
        epoch = int(payload.get("epoch", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid group invite epoch") from exc
    if epoch < 0:
        raise ValueError("Invalid group invite epoch")

    created_raw = payload.get("created_at")
    created_at: datetime | None = None
    if created_raw:
        try:
            created_at = _parse_datetime(str(created_raw))
        except Exception as exc:
            raise ValueError("Invalid group invite created_at") from exc

    expires_raw = payload.get("expires_at")
    expires_at: datetime | None = None
    if expires_raw:
        try:
            expires_at = _parse_datetime(str(expires_raw))
        except Exception as exc:
            raise ValueError("Invalid group invite expires_at") from exc

    members = _normalize_members(payload.get("members"))

    # Verify the inviter's signature over the canonical payload. This proves
    # the invite (and its member roster) was produced by the holder of
    # ``inviter_signing_pub`` and has not been tampered with in transit.
    try:
        signing_pub = bytes.fromhex(inviter_signing_pub)
        signature_bytes = bytes.fromhex(signature)
    except ValueError as exc:
        raise ValueError("Malformed group invite signature encoding") from exc
    normalized_inviter = normalize_member_id(inviter_id)
    signed_bytes = _canonical_invite_bytes(
        version=version,
        invite_id=invite_id,
        group_id=group_id,
        title=title,
        members=(
            members if normalized_inviter in members else members + (normalized_inviter,)
        ),
        epoch=epoch,
        inviter_id=normalized_inviter,
        inviter_signing_pub=inviter_signing_pub,
        # Use the raw payload strings so canonicalization matches the encoder
        # byte-for-byte (no datetime round-trip involved).
        created_at=str(created_raw or ""),
        expires_at=str(expires_raw) if expires_raw else None,
    )
    if not crypto.verify_signature(signing_pub, signed_bytes, signature_bytes):
        raise ValueError("Group invite signature verification failed")

    if expires_at is not None and expires_at < utc_now():
        raise ValueError("Group invite has expired")

    return GroupInvite(
        invite_id=invite_id,
        group_id=group_id,
        members=members,
        epoch=epoch,
        inviter_id=inviter_id,
        title=title,
        created_at=created_at,
        expires_at=expires_at,
        version=version,
        inviter_signing_pub=inviter_signing_pub,
        signature=signature,
    )


def looks_like_group_invite(text: str) -> bool:
    return (text or "").strip().startswith(GROUP_INVITE_PREFIX)
