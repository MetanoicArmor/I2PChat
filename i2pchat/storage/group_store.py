from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from i2pchat.groups.models import (
    GroupContentType,
    GroupEnvelope,
    GroupRecipientDeliveryMetadata,
    GroupState,
    normalize_member_id,
    utc_now,
)
from i2pchat.storage.blindbox_state import BlindBoxState, atomic_write_json

GROUP_RECORD_VERSION = 1
MAX_SEEN_GROUP_MSG_IDS = 4096


def _to_iso8601(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _from_iso8601(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_group_token(group_id: str) -> str:
    return hashlib.sha256((group_id or "").encode("utf-8")).hexdigest()


def _group_record_path(profile_data_dir: str, profile: str, group_id: str) -> str:
    return os.path.join(
        profile_data_dir,
        f"{profile}.group.{_safe_group_token(group_id)}.json",
    )


def _group_record_paths(profile_data_dir: str, profile: str) -> list[str]:
    prefix = f"{profile}.group."
    suffix = ".json"
    if not os.path.isdir(profile_data_dir):
        return []
    paths: list[str] = []
    for name in sorted(os.listdir(profile_data_dir)):
        if name.startswith(prefix) and name.endswith(suffix):
            paths.append(os.path.join(profile_data_dir, name))
    return paths


@dataclass(slots=True, frozen=True)
class GroupHistoryEntry:
    kind: str
    sender_id: str
    content_type: GroupContentType
    text: str = ""
    payload: Any | None = None
    msg_id: str | None = None
    group_seq: int = 0
    epoch: int = 0
    created_at: datetime = field(default_factory=utc_now)
    source_peer: str | None = None
    delivery_results: dict[str, str] = field(default_factory=dict)
    # Per-recipient failure/detail reasons (e.g. blindbox-await-root); optional, backward compatible.
    delivery_reasons: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        content_type = (
            self.content_type
            if isinstance(self.content_type, GroupContentType)
            else GroupContentType(str(self.content_type))
        )
        payload = self.payload
        text = str(self.text or "")
        if content_type == GroupContentType.GROUP_TEXT:
            text = text or (str(payload) if payload is not None else "")
            payload = text
        elif content_type == GroupContentType.GROUP_CONTROL and isinstance(payload, dict):
            payload = dict(payload)
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        object.__setattr__(
            self,
            "kind",
            "me" if str(self.kind or "").strip().lower() == "me" else "peer",
        )
        object.__setattr__(self, "sender_id", normalize_member_id(self.sender_id))
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "msg_id", str(self.msg_id or "").strip() or None)
        object.__setattr__(self, "group_seq", max(0, int(self.group_seq)))
        object.__setattr__(self, "epoch", max(0, int(self.epoch)))
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "source_peer", str(self.source_peer or "").strip() or None)
        object.__setattr__(
            self,
            "delivery_results",
            {
                recipient_id: str(status or "").strip()
                for raw_recipient_id, status in dict(self.delivery_results or {}).items()
                if (recipient_id := normalize_member_id(str(raw_recipient_id)))
            },
        )
        object.__setattr__(
            self,
            "delivery_reasons",
            {
                recipient_id: str(reason or "").strip()
                for raw_recipient_id, reason in dict(self.delivery_reasons or {}).items()
                if (recipient_id := normalize_member_id(str(raw_recipient_id)))
                and str(reason or "").strip()
            },
        )


@dataclass(slots=True, frozen=True)
class GroupPendingDelivery:
    group_id: str
    group_title: str | None = None
    group_members: tuple[str, ...] = ()
    sender_id: str = ""
    recipient_id: str = ""
    delivery_id: str = ""
    msg_id: str = ""
    group_seq: int = 0
    epoch: int = 0
    content_type: GroupContentType = GroupContentType.GROUP_TEXT
    payload: Any | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        content_type = (
            self.content_type
            if isinstance(self.content_type, GroupContentType)
            else GroupContentType(str(self.content_type))
        )
        payload = self.payload
        if content_type == GroupContentType.GROUP_TEXT:
            payload = str(payload or "")
        elif content_type == GroupContentType.GROUP_CONTROL and isinstance(payload, dict):
            payload = dict(payload)
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        object.__setattr__(self, "group_id", str(self.group_id or "").strip())
        object.__setattr__(self, "group_title", str(self.group_title or "").strip() or None)
        object.__setattr__(
            self,
            "group_members",
            GroupState(
                group_id=str(self.group_id or "").strip() or "__pending__",
                epoch=max(0, int(self.epoch)),
                members=tuple(str(member) for member in tuple(self.group_members or ())),
                title=str(self.group_title or "").strip() or None,
                created_at=created_at,
                updated_at=created_at,
            ).members,
        )
        object.__setattr__(self, "sender_id", normalize_member_id(self.sender_id))
        object.__setattr__(self, "recipient_id", normalize_member_id(self.recipient_id))
        object.__setattr__(self, "delivery_id", str(self.delivery_id or "").strip())
        object.__setattr__(self, "msg_id", str(self.msg_id or "").strip())
        object.__setattr__(self, "group_seq", max(0, int(self.group_seq)))
        object.__setattr__(self, "epoch", max(0, int(self.epoch)))
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "created_at", created_at)

    def as_group_state(self) -> GroupState:
        return GroupState(
            group_id=self.group_id,
            epoch=self.epoch,
            members=self.group_members,
            title=self.group_title,
            created_at=self.created_at,
            updated_at=self.created_at,
        )

    def as_envelope(self) -> GroupEnvelope:
        return GroupEnvelope(
            group_id=self.group_id,
            epoch=self.epoch,
            msg_id=self.msg_id,
            sender_id=self.sender_id,
            group_seq=self.group_seq,
            content_type=self.content_type,
            payload=self.payload,
            created_at=self.created_at,
        )

    def as_metadata(self) -> GroupRecipientDeliveryMetadata:
        return GroupRecipientDeliveryMetadata(
            recipient_id=self.recipient_id,
            delivery_id=self.delivery_id,
        )


@dataclass(slots=True, frozen=True)
class GroupBlindBoxChannel:
    channel_id: str
    group_epoch: int
    state: BlindBoxState
    root_secret_enc: str | None = None
    root_epoch: int = 0
    root_created_at: int = 0
    root_send_index_base: int = 0
    pending_root_secret_enc: str | None = None
    pending_root_epoch: int = 0
    pending_root_created_at: int = 0
    pending_root_send_index_base: int = 0
    pending_root_target_members: tuple[str, ...] = ()
    pending_root_acked_members: tuple[str, ...] = ()
    prev_roots: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        raw_state = self.state
        if isinstance(raw_state, BlindBoxState):
            state = BlindBoxState.from_dict(raw_state.to_dict())
        else:
            state = BlindBoxState.from_dict(dict(raw_state))

        def _normalize_members(raw_members: tuple[str, ...]) -> tuple[str, ...]:
            normalized: list[str] = []
            seen: set[str] = set()
            for raw_member in tuple(raw_members or ()):
                member_id = normalize_member_id(str(raw_member))
                if not member_id or member_id in seen:
                    continue
                seen.add(member_id)
                normalized.append(member_id)
            return tuple(normalized)

        normalized_prev_roots: list[dict[str, Any]] = []
        for raw_item in tuple(self.prev_roots or ()):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            normalized_prev_roots.append(
                {
                    "group_epoch": int(item.get("group_epoch", 0)),
                    "root_epoch": int(item.get("root_epoch", 0)),
                    "expires_at": int(item.get("expires_at", 0)),
                    "secret_enc": str(item.get("secret_enc") or "").strip(),
                }
            )

        object.__setattr__(self, "channel_id", str(self.channel_id or "").strip())
        object.__setattr__(self, "group_epoch", max(0, int(self.group_epoch)))
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "root_secret_enc",
            str(self.root_secret_enc or "").strip() or None,
        )
        object.__setattr__(self, "root_epoch", max(0, int(self.root_epoch)))
        object.__setattr__(self, "root_created_at", max(0, int(self.root_created_at)))
        object.__setattr__(
            self,
            "root_send_index_base",
            max(0, int(self.root_send_index_base)),
        )
        object.__setattr__(
            self,
            "pending_root_secret_enc",
            str(self.pending_root_secret_enc or "").strip() or None,
        )
        object.__setattr__(
            self,
            "pending_root_epoch",
            max(0, int(self.pending_root_epoch)),
        )
        object.__setattr__(
            self,
            "pending_root_created_at",
            max(0, int(self.pending_root_created_at)),
        )
        object.__setattr__(
            self,
            "pending_root_send_index_base",
            max(0, int(self.pending_root_send_index_base)),
        )
        object.__setattr__(
            self,
            "pending_root_target_members",
            _normalize_members(self.pending_root_target_members),
        )
        object.__setattr__(
            self,
            "pending_root_acked_members",
            _normalize_members(self.pending_root_acked_members),
        )
        object.__setattr__(self, "prev_roots", tuple(normalized_prev_roots))


@dataclass(slots=True, frozen=True)
class GroupPendingBlindBoxMessage:
    group_id: str
    group_title: str | None = None
    group_members: tuple[str, ...] = ()
    sender_id: str = ""
    msg_id: str = ""
    group_seq: int = 0
    epoch: int = 0
    content_type: GroupContentType = GroupContentType.GROUP_TEXT
    payload: Any | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        content_type = (
            self.content_type
            if isinstance(self.content_type, GroupContentType)
            else GroupContentType(str(self.content_type))
        )
        payload = self.payload
        if content_type == GroupContentType.GROUP_TEXT:
            payload = str(payload or "")
        elif content_type == GroupContentType.GROUP_CONTROL and isinstance(payload, dict):
            payload = dict(payload)
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        object.__setattr__(self, "group_id", str(self.group_id or "").strip())
        object.__setattr__(self, "group_title", str(self.group_title or "").strip() or None)
        object.__setattr__(
            self,
            "group_members",
            GroupState(
                group_id=str(self.group_id or "").strip() or "__pending_group_blindbox__",
                epoch=max(0, int(self.epoch)),
                members=tuple(str(member) for member in tuple(self.group_members or ())),
                title=str(self.group_title or "").strip() or None,
                created_at=created_at,
                updated_at=created_at,
            ).members,
        )
        object.__setattr__(self, "sender_id", normalize_member_id(self.sender_id))
        object.__setattr__(self, "msg_id", str(self.msg_id or "").strip())
        object.__setattr__(self, "group_seq", max(0, int(self.group_seq)))
        object.__setattr__(self, "epoch", max(0, int(self.epoch)))
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "created_at", created_at)

    def as_group_state(self) -> GroupState:
        return GroupState(
            group_id=self.group_id,
            epoch=self.epoch,
            members=self.group_members,
            title=self.group_title,
            created_at=self.created_at,
            updated_at=self.created_at,
        )

    def as_envelope(self) -> GroupEnvelope:
        return GroupEnvelope(
            group_id=self.group_id,
            epoch=self.epoch,
            msg_id=self.msg_id,
            sender_id=self.sender_id,
            group_seq=self.group_seq,
            content_type=self.content_type,
            payload=self.payload,
            created_at=self.created_at,
        )


@dataclass(slots=True, frozen=True)
class StoredGroupConversation:
    state: GroupState
    next_group_seq: int = 1
    history: tuple[GroupHistoryEntry, ...] = ()
    seen_msg_ids: tuple[str, ...] = ()
    pending_deliveries: tuple[GroupPendingDelivery, ...] = ()
    blindbox_channel: GroupBlindBoxChannel | None = None
    pending_group_blindbox_messages: tuple[GroupPendingBlindBoxMessage, ...] = ()

    def __post_init__(self) -> None:
        history = tuple(self.history or ())
        highest_group_seq = max((entry.group_seq for entry in history), default=0)
        normalized_seen_msg_ids = _resolve_seen_msg_ids(
            history,
            tuple(
                str(raw_msg_id).strip()
                for raw_msg_id in tuple(self.seen_msg_ids or ())
                if str(raw_msg_id).strip()
            ),
        )
        normalized_pending: list[GroupPendingDelivery] = []
        seen_pending_delivery_ids: set[str] = set()
        for raw_item in tuple(self.pending_deliveries or ()):
            item = (
                raw_item
                if isinstance(raw_item, GroupPendingDelivery)
                else GroupPendingDelivery(**dict(raw_item))
            )
            if not item.delivery_id or item.delivery_id in seen_pending_delivery_ids:
                continue
            seen_pending_delivery_ids.add(item.delivery_id)
            normalized_pending.append(item)
        normalized_blindbox_channel: GroupBlindBoxChannel | None
        if self.blindbox_channel is None:
            normalized_blindbox_channel = None
        elif isinstance(self.blindbox_channel, GroupBlindBoxChannel):
            normalized_blindbox_channel = self.blindbox_channel
        else:
            normalized_blindbox_channel = GroupBlindBoxChannel(
                **dict(self.blindbox_channel)
            )
        normalized_pending_group_blindbox: list[GroupPendingBlindBoxMessage] = []
        seen_pending_group_msg_ids: set[str] = set()
        for raw_item in tuple(self.pending_group_blindbox_messages or ()):
            item = (
                raw_item
                if isinstance(raw_item, GroupPendingBlindBoxMessage)
                else GroupPendingBlindBoxMessage(**dict(raw_item))
            )
            if not item.msg_id or item.msg_id in seen_pending_group_msg_ids:
                continue
            seen_pending_group_msg_ids.add(item.msg_id)
            normalized_pending_group_blindbox.append(item)
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "seen_msg_ids", normalized_seen_msg_ids)
        object.__setattr__(self, "pending_deliveries", tuple(normalized_pending))
        object.__setattr__(self, "blindbox_channel", normalized_blindbox_channel)
        object.__setattr__(
            self,
            "pending_group_blindbox_messages",
            tuple(normalized_pending_group_blindbox),
        )
        object.__setattr__(
            self,
            "next_group_seq",
            max(1, int(self.next_group_seq), highest_group_seq + 1),
        )


def _serialize_state(state: GroupState) -> dict[str, Any]:
    return {
        "group_id": state.group_id,
        "title": state.title,
        "epoch": int(state.epoch),
        "members": list(state.members),
        "created_at": _to_iso8601(state.created_at),
        "updated_at": _to_iso8601(state.updated_at),
    }


def _deserialize_state(data: dict[str, Any]) -> GroupState:
    return GroupState(
        group_id=str(data["group_id"]),
        title=str(data.get("title") or "").strip() or None,
        epoch=int(data.get("epoch", 0)),
        members=tuple(str(member) for member in data.get("members", [])),
        created_at=_from_iso8601(str(data["created_at"])),
        updated_at=_from_iso8601(str(data["updated_at"])),
    )


def _serialize_entry(entry: GroupHistoryEntry) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "sender_id": entry.sender_id,
        "content_type": str(entry.content_type),
        "text": entry.text,
        "payload": entry.payload,
        "msg_id": entry.msg_id,
        "group_seq": int(entry.group_seq),
        "epoch": int(entry.epoch),
        "created_at": _to_iso8601(entry.created_at),
        "source_peer": entry.source_peer,
        "delivery_results": dict(entry.delivery_results),
        "delivery_reasons": dict(entry.delivery_reasons),
    }


def _serialize_pending_delivery(entry: GroupPendingDelivery) -> dict[str, Any]:
    return {
        "group_id": entry.group_id,
        "group_title": entry.group_title,
        "group_members": list(entry.group_members),
        "sender_id": entry.sender_id,
        "recipient_id": entry.recipient_id,
        "delivery_id": entry.delivery_id,
        "msg_id": entry.msg_id,
        "group_seq": int(entry.group_seq),
        "epoch": int(entry.epoch),
        "content_type": str(entry.content_type),
        "payload": entry.payload,
        "created_at": _to_iso8601(entry.created_at),
    }


def _serialize_blindbox_channel(channel: GroupBlindBoxChannel) -> dict[str, Any]:
    return {
        "channel_id": channel.channel_id,
        "group_epoch": int(channel.group_epoch),
        "state": channel.state.to_dict(),
        "root_secret_enc": channel.root_secret_enc,
        "root_epoch": int(channel.root_epoch),
        "root_created_at": int(channel.root_created_at),
        "root_send_index_base": int(channel.root_send_index_base),
        "pending_root_secret_enc": channel.pending_root_secret_enc,
        "pending_root_epoch": int(channel.pending_root_epoch),
        "pending_root_created_at": int(channel.pending_root_created_at),
        "pending_root_send_index_base": int(channel.pending_root_send_index_base),
        "pending_root_target_members": list(channel.pending_root_target_members),
        "pending_root_acked_members": list(channel.pending_root_acked_members),
        "prev_roots": [dict(item) for item in channel.prev_roots],
    }


def _serialize_pending_group_blindbox_message(
    entry: GroupPendingBlindBoxMessage,
) -> dict[str, Any]:
    return {
        "group_id": entry.group_id,
        "group_title": entry.group_title,
        "group_members": list(entry.group_members),
        "sender_id": entry.sender_id,
        "msg_id": entry.msg_id,
        "group_seq": int(entry.group_seq),
        "epoch": int(entry.epoch),
        "content_type": str(entry.content_type),
        "payload": entry.payload,
        "created_at": _to_iso8601(entry.created_at),
    }


def _deserialize_entry(data: dict[str, Any]) -> GroupHistoryEntry:
    return GroupHistoryEntry(
        kind=str(data.get("kind", "peer")),
        sender_id=str(data.get("sender_id", "")),
        content_type=GroupContentType(str(data["content_type"])),
        text=str(data.get("text", "")),
        payload=data.get("payload"),
        msg_id=str(data.get("msg_id") or "").strip() or None,
        group_seq=int(data.get("group_seq", 0)),
        epoch=int(data.get("epoch", 0)),
        created_at=_from_iso8601(str(data["created_at"])),
        source_peer=str(data.get("source_peer") or "").strip() or None,
        delivery_results={
            str(key): str(value)
            for key, value in dict(data.get("delivery_results", {})).items()
        },
        delivery_reasons={
            str(key): str(value)
            for key, value in dict(data.get("delivery_reasons", {})).items()
        },
    )


def _deserialize_pending_delivery(data: dict[str, Any]) -> GroupPendingDelivery:
    return GroupPendingDelivery(
        group_id=str(data.get("group_id", "")),
        group_title=str(data.get("group_title") or "").strip() or None,
        group_members=tuple(str(member) for member in data.get("group_members", [])),
        sender_id=str(data.get("sender_id", "")),
        recipient_id=str(data.get("recipient_id", "")),
        delivery_id=str(data.get("delivery_id", "")),
        msg_id=str(data.get("msg_id", "")),
        group_seq=int(data.get("group_seq", 0)),
        epoch=int(data.get("epoch", 0)),
        content_type=GroupContentType(str(data["content_type"])),
        payload=data.get("payload"),
        created_at=_from_iso8601(str(data["created_at"])),
    )


def _deserialize_blindbox_channel(data: dict[str, Any]) -> GroupBlindBoxChannel:
    return GroupBlindBoxChannel(
        channel_id=str(data.get("channel_id", "")),
        group_epoch=int(data.get("group_epoch", 0)),
        state=BlindBoxState.from_dict(dict(data.get("state", {}))),
        root_secret_enc=str(data.get("root_secret_enc") or "").strip() or None,
        root_epoch=int(data.get("root_epoch", 0)),
        root_created_at=int(data.get("root_created_at", 0)),
        root_send_index_base=int(data.get("root_send_index_base", 0)),
        pending_root_secret_enc=(
            str(data.get("pending_root_secret_enc") or "").strip() or None
        ),
        pending_root_epoch=int(data.get("pending_root_epoch", 0)),
        pending_root_created_at=int(data.get("pending_root_created_at", 0)),
        pending_root_send_index_base=int(data.get("pending_root_send_index_base", 0)),
        pending_root_target_members=tuple(
            str(member) for member in data.get("pending_root_target_members", [])
        ),
        pending_root_acked_members=tuple(
            str(member) for member in data.get("pending_root_acked_members", [])
        ),
        prev_roots=tuple(
            dict(item)
            for item in data.get("prev_roots", [])
            if isinstance(item, dict)
        ),
    )


def _deserialize_pending_group_blindbox_message(
    data: dict[str, Any],
) -> GroupPendingBlindBoxMessage:
    return GroupPendingBlindBoxMessage(
        group_id=str(data.get("group_id", "")),
        group_title=str(data.get("group_title") or "").strip() or None,
        group_members=tuple(str(member) for member in data.get("group_members", [])),
        sender_id=str(data.get("sender_id", "")),
        msg_id=str(data.get("msg_id", "")),
        group_seq=int(data.get("group_seq", 0)),
        epoch=int(data.get("epoch", 0)),
        content_type=GroupContentType(str(data["content_type"])),
        payload=data.get("payload"),
        created_at=_from_iso8601(str(data["created_at"])),
    )


def _resolve_seen_msg_ids(
    history: tuple[GroupHistoryEntry, ...],
    stored_seen_msg_ids: tuple[str, ...],
) -> tuple[str, ...]:
    resolved: list[str] = []
    seen: set[str] = set()

    def _remember(raw_msg_id: str | None) -> None:
        msg_id = str(raw_msg_id or "").strip()
        if not msg_id or msg_id in seen:
            return
        seen.add(msg_id)
        resolved.append(msg_id)

    for msg_id in stored_seen_msg_ids:
        _remember(msg_id)
    for entry in history:
        _remember(entry.msg_id)
    if len(resolved) > MAX_SEEN_GROUP_MSG_IDS:
        resolved = resolved[-MAX_SEEN_GROUP_MSG_IDS:]
    return tuple(resolved)


def _resolve_next_group_seq(
    existing: StoredGroupConversation | None,
    next_group_seq: int | None,
) -> int:
    resolved = existing.next_group_seq if existing is not None else 1
    if next_group_seq is not None:
        resolved = max(resolved, int(max(1, next_group_seq)))
    return resolved


def save_group_conversation(
    profile_data_dir: str,
    profile: str,
    conversation: StoredGroupConversation,
) -> None:
    payload = {
        "version": GROUP_RECORD_VERSION,
        "state": _serialize_state(conversation.state),
        "next_group_seq": int(max(1, conversation.next_group_seq)),
        "history": [_serialize_entry(entry) for entry in conversation.history],
        "seen_msg_ids": list(conversation.seen_msg_ids),
        "pending_deliveries": [
            _serialize_pending_delivery(entry)
            for entry in conversation.pending_deliveries
        ],
        "blindbox_channel": (
            _serialize_blindbox_channel(conversation.blindbox_channel)
            if conversation.blindbox_channel is not None
            else None
        ),
        "pending_group_blindbox_messages": [
            _serialize_pending_group_blindbox_message(entry)
            for entry in conversation.pending_group_blindbox_messages
        ],
    }
    atomic_write_json(
        _group_record_path(profile_data_dir, profile, conversation.state.group_id),
        payload,
    )


def load_group_conversation(
    profile_data_dir: str,
    profile: str,
    group_id: str,
) -> StoredGroupConversation | None:
    path = _group_record_path(profile_data_dir, profile, group_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if int(payload.get("version", 0)) != GROUP_RECORD_VERSION:
        raise ValueError("Unsupported group store version")
    state = _deserialize_state(dict(payload["state"]))
    history = tuple(
        _deserialize_entry(item)
        for item in list(payload.get("history", []))
        if isinstance(item, dict)
    )
    stored_seen_msg_ids = tuple(
        str(item).strip()
        for item in list(payload.get("seen_msg_ids", []))
        if str(item).strip()
    )
    seen_msg_ids = _resolve_seen_msg_ids(history, stored_seen_msg_ids)
    pending_deliveries = tuple(
        _deserialize_pending_delivery(item)
        for item in list(payload.get("pending_deliveries", []))
        if isinstance(item, dict)
    )
    blindbox_channel_raw = payload.get("blindbox_channel", {})
    blindbox_channel = None
    if isinstance(blindbox_channel_raw, dict) and blindbox_channel_raw:
        blindbox_channel = _deserialize_blindbox_channel(blindbox_channel_raw)
    pending_group_blindbox_messages = tuple(
        _deserialize_pending_group_blindbox_message(item)
        for item in list(payload.get("pending_group_blindbox_messages", []))
        if isinstance(item, dict)
    )
    return StoredGroupConversation(
        state=state,
        next_group_seq=int(max(1, payload.get("next_group_seq", 1))),
        history=history,
        seen_msg_ids=seen_msg_ids,
        pending_deliveries=pending_deliveries,
        blindbox_channel=blindbox_channel,
        pending_group_blindbox_messages=pending_group_blindbox_messages,
    )


def upsert_group_state(
    profile_data_dir: str,
    profile: str,
    state: GroupState,
    *,
    next_group_seq: int | None = None,
) -> StoredGroupConversation:
    existing = load_group_conversation(profile_data_dir, profile, state.group_id)
    conversation = StoredGroupConversation(
        state=state,
        next_group_seq=_resolve_next_group_seq(existing, next_group_seq),
        history=existing.history if existing is not None else (),
        seen_msg_ids=existing.seen_msg_ids if existing is not None else (),
        pending_deliveries=(
            existing.pending_deliveries if existing is not None else ()
        ),
        blindbox_channel=existing.blindbox_channel if existing is not None else None,
        pending_group_blindbox_messages=(
            existing.pending_group_blindbox_messages if existing is not None else ()
        ),
    )
    save_group_conversation(profile_data_dir, profile, conversation)
    return conversation


def append_group_history_entry(
    profile_data_dir: str,
    profile: str,
    state: GroupState,
    entry: GroupHistoryEntry,
    *,
    next_group_seq: int | None = None,
) -> tuple[StoredGroupConversation, bool]:
    existing = load_group_conversation(profile_data_dir, profile, state.group_id)
    history = list(existing.history) if existing is not None else []
    seen_msg_ids = list(existing.seen_msg_ids) if existing is not None else []
    seen_msg_id_set = set(seen_msg_ids)
    resolved_next_group_seq = _resolve_next_group_seq(existing, next_group_seq)
    normalized_msg_id = str(entry.msg_id or "").strip()
    if normalized_msg_id and normalized_msg_id in seen_msg_id_set:
        conversation = StoredGroupConversation(
            state=state,
            next_group_seq=resolved_next_group_seq,
            history=tuple(history),
            seen_msg_ids=tuple(seen_msg_ids),
            pending_deliveries=(
                existing.pending_deliveries if existing is not None else ()
            ),
            blindbox_channel=(
                existing.blindbox_channel if existing is not None else None
            ),
            pending_group_blindbox_messages=(
                existing.pending_group_blindbox_messages
                if existing is not None
                else ()
            ),
        )
        save_group_conversation(profile_data_dir, profile, conversation)
        return conversation, False
    history.append(entry)
    if normalized_msg_id:
        seen_msg_ids.append(normalized_msg_id)
        seen_msg_id_set.add(normalized_msg_id)
        if len(seen_msg_ids) > MAX_SEEN_GROUP_MSG_IDS:
            seen_msg_ids = seen_msg_ids[-MAX_SEEN_GROUP_MSG_IDS:]
    conversation = StoredGroupConversation(
        state=state,
        next_group_seq=resolved_next_group_seq,
        history=tuple(history),
        seen_msg_ids=tuple(seen_msg_ids),
        pending_deliveries=(
            existing.pending_deliveries if existing is not None else ()
        ),
        blindbox_channel=existing.blindbox_channel if existing is not None else None,
        pending_group_blindbox_messages=(
            existing.pending_group_blindbox_messages if existing is not None else ()
        ),
    )
    save_group_conversation(profile_data_dir, profile, conversation)
    return conversation, True


def load_group_state(
    profile_data_dir: str,
    profile: str,
    group_id: str,
) -> GroupState | None:
    conversation = load_group_conversation(profile_data_dir, profile, group_id)
    return None if conversation is None else conversation.state


def list_group_states(profile_data_dir: str, profile: str) -> list[GroupState]:
    states: list[GroupState] = []
    for path in _group_record_paths(profile_data_dir, profile):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if int(payload.get("version", 0)) != GROUP_RECORD_VERSION:
            continue
        states.append(_deserialize_state(dict(payload["state"])))
    states.sort(key=lambda item: item.updated_at, reverse=True)
    return states


def delete_group_record(profile_data_dir: str, profile: str, group_id: str) -> bool:
    """
    Remove the persisted group conversation file for this profile.
    Returns True if a file was removed, False if group_id is empty or no file existed.
    """
    gid = (group_id or "").strip()
    if not gid:
        return False
    path = _group_record_path(profile_data_dir, profile, gid)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True
