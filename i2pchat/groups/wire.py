from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from i2pchat.storage.contact_book import same_i2p_destination

from .models import (
    GroupContentType,
    GroupEnvelope,
    GroupRecipientDeliveryMetadata,
    GroupState,
    normalize_member_id,
)

GROUP_TRANSPORT_PREFIX = "__I2PCHAT_GROUP__:"
GROUP_TRANSPORT_VERSION = 1


def _required_text_field(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required group transport field: {key}")
    return value


def _required_int_field(payload: dict[str, Any], key: str, *, minimum: int | None = None) -> int:
    try:
        value = int(payload[key])
    except KeyError as exc:
        raise ValueError(f"Missing required group transport field: {key}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid group transport integer field: {key}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"Invalid group transport integer field: {key}")
    return value


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
    if not isinstance(payload, dict):
        raise ValueError("Group transport payload must be a JSON object")
    if payload.get("transport") != "group":
        raise ValueError("Unsupported group transport payload")
    if int(payload.get("version", 0)) != GROUP_TRANSPORT_VERSION:
        raise ValueError("Unsupported group transport version")

    content_type = GroupContentType(_required_text_field(payload, "content_type"))
    group_id = _required_text_field(payload, "group_id")
    msg_id = _required_text_field(payload, "msg_id")
    sender_id = normalize_member_id(_required_text_field(payload, "sender_id"))
    if not sender_id:
        raise ValueError("Missing required group transport field: sender_id")
    recipient_id = normalize_member_id(_required_text_field(payload, "recipient_id"))
    if not recipient_id:
        raise ValueError("Missing required group transport field: recipient_id")
    delivery_id = _required_text_field(payload, "delivery_id")
    created_at = _parse_datetime(_required_text_field(payload, "created_at"))
    members_raw = payload.get("members", [])
    if not isinstance(members_raw, list):
        raise ValueError("Invalid group transport field: members")
    if content_type == GroupContentType.GROUP_TEXT and not isinstance(payload.get("payload"), str):
        raise ValueError("GROUP_TEXT payload must be a string")
    if content_type == GroupContentType.GROUP_CONTROL and not isinstance(payload.get("payload"), dict):
        raise ValueError("GROUP_CONTROL payload must be an object")
    state = GroupState(
        group_id=group_id,
        epoch=_required_int_field(payload, "epoch", minimum=0),
        members=tuple(str(member) for member in members_raw),
        title=str(payload.get("group_title") or "").strip() or None,
        created_at=created_at,
        updated_at=created_at,
    )
    if not state.members:
        raise ValueError("Group transport must include at least one member")
    if not any(same_i2p_destination(sender_id, m) for m in state.members if m):
        raise ValueError("Group transport sender is not a group member")
    if not any(same_i2p_destination(recipient_id, m) for m in state.members if m):
        raise ValueError("Group transport recipient is not a group member")
    envelope = GroupEnvelope(
        group_id=state.group_id,
        epoch=state.epoch,
        msg_id=msg_id,
        sender_id=sender_id,
        group_seq=_required_int_field(payload, "group_seq", minimum=1),
        content_type=content_type,
        payload=payload.get("payload"),
        created_at=created_at,
    )
    envelope.member_metadata = {
        recipient_id: GroupRecipientDeliveryMetadata(
            recipient_id=recipient_id,
            delivery_id=delivery_id,
        )
    }
    return DecodedGroupTransportMessage(
        state=state,
        envelope=envelope,
        recipient_id=recipient_id or None,
        delivery_id=delivery_id or None,
    )
