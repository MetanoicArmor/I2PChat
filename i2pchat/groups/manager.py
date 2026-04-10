from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, TypeAlias

from i2pchat.core.session_manager import OutboundPolicy, SessionManager
from i2pchat.storage.contact_book import same_i2p_destination

from .models import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupEnvelope,
    GroupMemberDeliveryResult,
    GroupRecipientDeliveryMetadata,
    GroupSendResult,
    GroupState,
    GroupTransportOutcome,
    normalize_member_id,
    utc_now,
)

GroupSender: TypeAlias = Callable[
    [str, GroupEnvelope, GroupRecipientDeliveryMetadata],
    Awaitable[GroupTransportOutcome],
]


class GroupManager:
    """Group transport fan-out orchestration over peer-scoped transport truth."""

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        send_live: GroupSender,
        send_offline: GroupSender,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._send_live = send_live
        self._send_offline = send_offline
        self._id_factory = id_factory or self._default_id_factory
        self._clock = clock or utc_now
        self._group_seq_by_id: dict[str, int] = {}

    @staticmethod
    def _default_id_factory() -> str:
        return secrets.token_hex(16)

    def prime_group_sequence(self, group_id: str, *, next_group_seq: int) -> None:
        group_key = (group_id or "").strip()
        if not group_key:
            return
        seeded_last_seq = max(0, int(next_group_seq) - 1)
        current_last_seq = self._group_seq_by_id.get(group_key, 0)
        self._group_seq_by_id[group_key] = max(current_last_seq, seeded_last_seq)

    def _next_group_seq(self, group_id: str) -> int:
        next_seq = self._group_seq_by_id.get(group_id, 0) + 1
        self._group_seq_by_id[group_id] = next_seq
        return next_seq

    def _recipient_ids(self, state: GroupState, sender_id: str) -> tuple[str, ...]:
        return tuple(
            member
            for member in state.members
            if member and not same_i2p_destination(member, sender_id)
        )

    def _build_envelope(
        self,
        *,
        state: GroupState,
        sender_id: str,
        payload: Any,
        content_type: GroupContentType,
        recipients: tuple[str, ...],
    ) -> GroupEnvelope:
        msg_id = self._id_factory()
        created_at = self._clock()
        sender = normalize_member_id(sender_id)
        envelope = GroupEnvelope(
            group_id=state.group_id,
            epoch=int(state.epoch),
            msg_id=msg_id,
            sender_id=sender,
            group_seq=self._next_group_seq(state.group_id),
            content_type=content_type,
            payload=payload,
            created_at=created_at,
        )
        envelope.member_metadata = {
            recipient: GroupRecipientDeliveryMetadata(
                recipient_id=recipient,
                delivery_id=f"{msg_id}:{recipient}",
            )
            for recipient in recipients
        }
        return envelope

    async def send_text(
        self,
        state: GroupState,
        *,
        sender_id: str,
        text: str,
        requested_route: str = "auto",
    ) -> GroupSendResult:
        return await self.send_payload(
            state,
            sender_id=sender_id,
            payload=text,
            content_type=GroupContentType.GROUP_TEXT,
            requested_route=requested_route,
        )

    async def send_control(
        self,
        state: GroupState,
        *,
        sender_id: str,
        payload: Mapping[str, Any],
        requested_route: str = "auto",
    ) -> GroupSendResult:
        return await self.send_payload(
            state,
            sender_id=sender_id,
            payload=dict(payload),
            content_type=GroupContentType.GROUP_CONTROL,
            requested_route=requested_route,
        )

    async def send_payload(
        self,
        state: GroupState,
        *,
        sender_id: str,
        payload: Any,
        content_type: GroupContentType,
        requested_route: str = "auto",
    ) -> GroupSendResult:
        if content_type not in (
            GroupContentType.GROUP_TEXT,
            GroupContentType.GROUP_CONTROL,
        ):
            raise ValueError(f"Unsupported group content type: {content_type!r}")
        recipients = self._recipient_ids(state, sender_id)
        envelope = self._build_envelope(
            state=state,
            sender_id=sender_id,
            payload=payload,
            content_type=content_type,
            recipients=recipients,
        )
        delivery_results: dict[str, GroupMemberDeliveryResult] = {}
        for recipient_id in recipients:
            metadata = envelope.member_metadata[recipient_id]
            delivery_results[recipient_id] = await self._deliver_to_member(
                recipient_id=recipient_id,
                envelope=envelope,
                metadata=metadata,
                requested_route=requested_route,
            )
        return GroupSendResult(
            envelope=envelope,
            delivery_results=delivery_results,
        )

    async def _deliver_to_member(
        self,
        *,
        recipient_id: str,
        envelope: GroupEnvelope,
        metadata: GroupRecipientDeliveryMetadata,
        requested_route: str,
    ) -> GroupMemberDeliveryResult:
        policy = self._session_manager.select_outbound_policy(
            requested_route=requested_route,
            peer_id=recipient_id,
        )
        if policy in (OutboundPolicy.LIVE_ONLY, OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX):
            live_ready = self._session_manager.is_live_path_alive(peer_id=recipient_id)
            if live_ready:
                live_result = await self._send_live(recipient_id, envelope, metadata)
                if live_result.accepted:
                    return GroupMemberDeliveryResult(
                        recipient_id=recipient_id,
                        status=GroupDeliveryStatus.DELIVERED_LIVE,
                        reason=live_result.reason or "live-session",
                        transport_message_id=live_result.transport_message_id,
                        delivery_id=metadata.delivery_id,
                    )
                if policy == OutboundPolicy.LIVE_ONLY:
                    return GroupMemberDeliveryResult(
                        recipient_id=recipient_id,
                        status=GroupDeliveryStatus.FAILED,
                        reason=live_result.reason or "needs-live-session",
                        transport_message_id=live_result.transport_message_id,
                        delivery_id=metadata.delivery_id,
                    )
            elif policy == OutboundPolicy.LIVE_ONLY:
                return GroupMemberDeliveryResult(
                    recipient_id=recipient_id,
                    status=GroupDeliveryStatus.FAILED,
                    reason="needs-live-session",
                    delivery_id=metadata.delivery_id,
                )

        offline_result = await self._send_offline(recipient_id, envelope, metadata)
        if offline_result.accepted:
            return GroupMemberDeliveryResult(
                recipient_id=recipient_id,
                status=GroupDeliveryStatus.QUEUED_OFFLINE,
                reason=offline_result.reason or "blindbox-ready",
                transport_message_id=offline_result.transport_message_id,
                delivery_id=metadata.delivery_id,
            )
        return GroupMemberDeliveryResult(
            recipient_id=recipient_id,
            status=GroupDeliveryStatus.FAILED,
            reason=offline_result.reason or "blindbox-unavailable",
            transport_message_id=offline_result.transport_message_id,
            delivery_id=metadata.delivery_id,
        )
