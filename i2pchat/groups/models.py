from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from i2pchat.storage.contact_book import normalize_peer_address


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_member_id(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    canonical = normalize_peer_address(raw)
    if canonical is not None:
        return canonical
    return raw


class GroupContentType(StrEnum):
    GROUP_TEXT = "GROUP_TEXT"
    GROUP_CONTROL = "GROUP_CONTROL"


class GroupDeliveryStatus(StrEnum):
    DELIVERED_LIVE = "delivered_live"
    QUEUED_OFFLINE = "queued_offline"
    FAILED = "failed"


class GroupImportStatus(StrEnum):
    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


@dataclass(slots=True, frozen=True)
class GroupState:
    group_id: str
    epoch: int
    members: tuple[str, ...]
    title: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        members: list[str] = []
        seen: set[str] = set()
        for raw_member in self.members:
            member_id = normalize_member_id(raw_member)
            if not member_id or member_id in seen:
                continue
            seen.add(member_id)
            members.append(member_id)
        object.__setattr__(self, "group_id", (self.group_id or "").strip())
        object.__setattr__(self, "title", (self.title or "").strip() or None)
        object.__setattr__(self, "members", tuple(members))


@dataclass(slots=True, frozen=True)
class GroupRecipientDeliveryMetadata:
    recipient_id: str
    delivery_id: str
    ciphertext: bytes | None = None


@dataclass(slots=True)
class GroupEnvelope:
    group_id: str
    epoch: int
    msg_id: str
    sender_id: str
    group_seq: int
    content_type: GroupContentType
    payload: Any | None = None
    ciphertext: bytes | None = None
    created_at: datetime = field(default_factory=utc_now)
    member_metadata: dict[str, GroupRecipientDeliveryMetadata] = field(
        default_factory=dict
    )


@dataclass(slots=True, frozen=True)
class GroupTransportOutcome:
    accepted: bool
    reason: str = ""
    transport_message_id: str | None = None


@dataclass(slots=True, frozen=True)
class GroupMemberDeliveryResult:
    recipient_id: str
    status: GroupDeliveryStatus
    reason: str = ""
    transport_message_id: str | None = None
    delivery_id: str | None = None


@dataclass(slots=True, frozen=True)
class GroupSendResult:
    envelope: GroupEnvelope
    delivery_results: dict[str, GroupMemberDeliveryResult]


@dataclass(slots=True, frozen=True)
class GroupImportResult:
    status: GroupImportStatus
    envelope: GroupEnvelope | None = None
    state: GroupState | None = None
    source_peer: str | None = None
    error: str | None = None

    @property
    def imported(self) -> bool:
        return self.status == GroupImportStatus.IMPORTED

    @property
    def duplicate(self) -> bool:
        return self.status == GroupImportStatus.DUPLICATE

    @property
    def invalid(self) -> bool:
        return self.status == GroupImportStatus.INVALID
