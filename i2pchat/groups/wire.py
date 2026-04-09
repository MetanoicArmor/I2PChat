from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import (
    GroupContentType,
    GroupEnvelope,
    GroupRecipientDeliveryMetadata,
    GroupState,
    normalize_member_id,
)

GROUP_TRANSPORT_PREFIX = "__I2PCHAT_GROUP__:"
GROUP_TRANSPORT_VERSION = 1


def _to_iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True, frozen=True)
class DecodedGroupTransportMessage:
    state: GroupState
    envelope: GroupEnvelope
    recipient_id: str | None = None
    delivery_id: str | None = None


def encode_group_transport_text(
    state: GroupState,
    envelope: GroupEnvelope,
    metadata: GroupRecipientDeliveryMetadata,
) -> str:
    payload = {
        "transport": "group",
        "version": GROUP_TRANSPORT_VERSION,
        "group_id": state.group_id,
        "group_title": state.title,
        "members": list(state.members),
        "epoch": int(envelope.epoch),
        "msg_id": envelope.msg_id,
        "sender_id": envelope.sender_id,
        "group_seq": int(envelope.group_seq),
        "content_type": str(envelope.content_type),
        "payload": envelope.payload,
        "created_at": _to_iso8601(envelope.created_at),
        "recipient_id": metadata.recipient_id,
        "delivery_id": metadata.delivery_id,
    }
    return GROUP_TRANSPORT_PREFIX + json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_group_transport_text(text: str) -> DecodedGroupTransportMessage | None:
    raw = str(text or "")
    if not raw.startswith(GROUP_TRANSPORT_PREFIX):
        return None
    payload = json.loads(raw[len(GROUP_TRANSPORT_PREFIX) :])
    if payload.get("transport") != "group":
        raise ValueError("Unsupported group transport payload")
    if int(payload.get("version", 0)) != GROUP_TRANSPORT_VERSION:
        raise ValueError("Unsupported group transport version")

    content_type = GroupContentType(str(payload["content_type"]))
    state = GroupState(
        group_id=str(payload["group_id"]),
        epoch=int(payload["epoch"]),
        members=tuple(str(member) for member in payload.get("members", [])),
        title=str(payload.get("group_title") or "").strip() or None,
        created_at=_parse_datetime(str(payload["created_at"])),
        updated_at=_parse_datetime(str(payload["created_at"])),
    )
    envelope = GroupEnvelope(
        group_id=state.group_id,
        epoch=int(payload["epoch"]),
        msg_id=str(payload["msg_id"]),
        sender_id=normalize_member_id(str(payload["sender_id"])),
        group_seq=int(payload["group_seq"]),
        content_type=content_type,
        payload=payload.get("payload"),
        created_at=_parse_datetime(str(payload["created_at"])),
    )
    recipient_id = (
        normalize_member_id(str(payload.get("recipient_id") or "")) or None
    )
    delivery_id = str(payload.get("delivery_id") or "").strip() or None
    if recipient_id and delivery_id:
        envelope.member_metadata = {
            recipient_id: GroupRecipientDeliveryMetadata(
                recipient_id=recipient_id,
                delivery_id=delivery_id,
            )
        }
    return DecodedGroupTransportMessage(
        state=state,
        envelope=envelope,
        recipient_id=recipient_id,
        delivery_id=delivery_id,
    )
