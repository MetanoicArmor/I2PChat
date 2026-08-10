from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import normalize_member_id, utc_now

GROUP_INVITE_PREFIX = "__I2PCHAT_GROUP_INVITE__:"
GROUP_INVITE_VERSION = 1


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


def build_group_invite(
    *,
    group_id: str,
    members: list[str] | tuple[str, ...],
    epoch: int,
    inviter_id: str,
    title: str | None = None,
    invite_id: str | None = None,
    created_at: datetime | None = None,
) -> GroupInvite:
    return GroupInvite(
        invite_id=(invite_id or secrets.token_hex(8)).strip(),
        group_id=group_id,
        members=tuple(members),
        epoch=int(epoch),
        inviter_id=inviter_id,
        title=title,
        created_at=created_at or utc_now(),
        version=GROUP_INVITE_VERSION,
    )


def encode_group_invite(invite: GroupInvite) -> str:
    payload = {
        "v": int(invite.version),
        "invite_id": invite.invite_id,
        "group_id": invite.group_id,
        "title": invite.title,
        "members": list(invite.members),
        "epoch": int(invite.epoch),
        "inviter_id": invite.inviter_id,
        "created_at": _to_iso8601(invite.created_at or utc_now()),
    }
    return GROUP_INVITE_PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decode_group_invite(text: str) -> GroupInvite:
    raw = (text or "").strip()
    if not raw.startswith(GROUP_INVITE_PREFIX):
        raise ValueError("Not a group invite string")
    body = raw[len(GROUP_INVITE_PREFIX) :].strip()
    if not body:
        raise ValueError("Empty group invite payload")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid group invite JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Group invite payload must be an object")

    version = int(payload.get("v", GROUP_INVITE_VERSION))
    if version != GROUP_INVITE_VERSION:
        raise ValueError(f"Unsupported group invite version: {version}")

    invite_id = str(payload.get("invite_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    inviter_id = str(payload.get("inviter_id") or "").strip()
    title = str(payload.get("title") or "").strip() or None
    if not invite_id:
        raise ValueError("Missing required group invite field: invite_id")
    if not group_id:
        raise ValueError("Missing required group invite field: group_id")
    if not inviter_id:
        raise ValueError("Missing required group invite field: inviter_id")

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

    return GroupInvite(
        invite_id=invite_id,
        group_id=group_id,
        members=_normalize_members(payload.get("members")),
        epoch=epoch,
        inviter_id=inviter_id,
        title=title,
        created_at=created_at,
        version=version,
    )


def looks_like_group_invite(text: str) -> bool:
    return (text or "").strip().startswith(GROUP_INVITE_PREFIX)
