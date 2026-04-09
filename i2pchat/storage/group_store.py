from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from i2pchat.groups.models import (
    GroupContentType,
    GroupState,
    normalize_member_id,
    utc_now,
)
from i2pchat.storage.blindbox_state import atomic_write_json

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


@dataclass(slots=True, frozen=True)
class StoredGroupConversation:
    state: GroupState
    next_group_seq: int = 1
    history: tuple[GroupHistoryEntry, ...] = ()
    seen_msg_ids: tuple[str, ...] = ()

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
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "seen_msg_ids", normalized_seen_msg_ids)
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
    return StoredGroupConversation(
        state=state,
        next_group_seq=int(max(1, payload.get("next_group_seq", 1))),
        history=history,
        seen_msg_ids=seen_msg_ids,
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
