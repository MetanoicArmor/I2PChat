import unittest
from unittest.mock import AsyncMock

from i2pchat.core.session_manager import SessionManager
from i2pchat.groups import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupManager,
    GroupState,
    GroupTransportOutcome,
)


class GroupManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_text_builds_per_member_metadata_for_all_recipients(self) -> None:
        session_manager = SessionManager()
        live_sender = AsyncMock(
            return_value=GroupTransportOutcome(accepted=True, reason="live-session")
        )
        offline_sender = AsyncMock(
            return_value=GroupTransportOutcome(
                accepted=True,
                reason="blindbox-ready",
                transport_message_id="offline-1",
            )
        )
        manager = GroupManager(
            session_manager=session_manager,
            send_live=live_sender,
            send_offline=offline_sender,
        )
        state = GroupState(
            group_id="group-1",
            epoch=3,
            members=("alice", "bob", "carol", "alice"),
            title="Test group",
        )

        result = await manager.send_text(state, sender_id="alice", text="hello group")

        self.assertEqual(set(result.delivery_results), {"bob", "carol"})
        self.assertEqual(set(result.envelope.member_metadata), {"bob", "carol"})
        self.assertEqual(result.envelope.member_metadata["bob"].delivery_id, f"{result.envelope.msg_id}:bob")
        self.assertEqual(result.envelope.member_metadata["carol"].delivery_id, f"{result.envelope.msg_id}:carol")
        live_sender.assert_not_awaited()
        # GroupManager remains peer-scoped; single-shot group BlindBox batching is owned by core.
        self.assertEqual(offline_sender.await_count, 2)

    async def test_online_members_get_live_delivery(self) -> None:
        session_manager = SessionManager()
        session_manager.set_peer_handshake_complete("bob")
        live_sender = AsyncMock(
            return_value=GroupTransportOutcome(
                accepted=True,
                reason="live-session",
                transport_message_id="live-bob",
            )
        )
        offline_sender = AsyncMock(
            return_value=GroupTransportOutcome(accepted=True, reason="blindbox-ready")
        )
        manager = GroupManager(
            session_manager=session_manager,
            send_live=live_sender,
            send_offline=offline_sender,
        )
        state = GroupState(group_id="group-2", epoch=1, members=("alice", "bob"))

        result = await manager.send_text(state, sender_id="alice", text="hello bob")
        bob_result = result.delivery_results["bob"]

        self.assertEqual(bob_result.status, GroupDeliveryStatus.DELIVERED_LIVE)
        self.assertEqual(bob_result.transport_message_id, "live-bob")
        live_sender.assert_awaited_once()
        offline_sender.assert_not_awaited()

    async def test_offline_members_get_offline_queued_delivery_status(self) -> None:
        session_manager = SessionManager()
        live_sender = AsyncMock(
            return_value=GroupTransportOutcome(accepted=True, reason="live-session")
        )
        offline_sender = AsyncMock(
            return_value=GroupTransportOutcome(
                accepted=True,
                reason="blindbox-ready",
                transport_message_id="queue-bob",
            )
        )
        manager = GroupManager(
            session_manager=session_manager,
            send_live=live_sender,
            send_offline=offline_sender,
        )
        state = GroupState(group_id="group-3", epoch=4, members=("alice", "bob"))

        result = await manager.send_text(state, sender_id="alice", text="hello bob")
        bob_result = result.delivery_results["bob"]

        self.assertEqual(bob_result.status, GroupDeliveryStatus.QUEUED_OFFLINE)
        self.assertEqual(bob_result.transport_message_id, "queue-bob")
        live_sender.assert_not_awaited()
        offline_sender.assert_awaited_once()

    async def test_mixed_online_offline_group_send_returns_per_member_map(self) -> None:
        session_manager = SessionManager()
        session_manager.set_peer_handshake_complete("bob")

        async def live_send(
            recipient_id: str,
            _envelope,
            _metadata,
        ) -> GroupTransportOutcome:
            return GroupTransportOutcome(
                accepted=True,
                reason=f"live-{recipient_id}",
                transport_message_id=f"live-{recipient_id}",
            )

        async def offline_send(
            recipient_id: str,
            _envelope,
            _metadata,
        ) -> GroupTransportOutcome:
            return GroupTransportOutcome(
                accepted=True,
                reason=f"queued-{recipient_id}",
                transport_message_id=f"queue-{recipient_id}",
            )

        manager = GroupManager(
            session_manager=session_manager,
            send_live=AsyncMock(side_effect=live_send),
            send_offline=AsyncMock(side_effect=offline_send),
        )
        state = GroupState(
            group_id="group-4",
            epoch=2,
            members=("alice", "bob", "carol"),
        )

        result = await manager.send_text(state, sender_id="alice", text="hello mixed")

        self.assertEqual(
            {
                member: delivery.status
                for member, delivery in result.delivery_results.items()
            },
            {
                "bob": GroupDeliveryStatus.DELIVERED_LIVE,
                "carol": GroupDeliveryStatus.QUEUED_OFFLINE,
            },
        )
        # GroupManager only decides per-member outcomes; core may collapse offline subset to one upload.
        self.assertEqual(set(result.envelope.member_metadata), {"bob", "carol"})

    async def test_group_epoch_is_preserved_in_envelope(self) -> None:
        session_manager = SessionManager()
        manager = GroupManager(
            session_manager=session_manager,
            send_live=AsyncMock(
                return_value=GroupTransportOutcome(accepted=True, reason="live-session")
            ),
            send_offline=AsyncMock(
                return_value=GroupTransportOutcome(accepted=True, reason="blindbox-ready")
            ),
        )
        state = GroupState(group_id="group-5", epoch=9, members=("alice", "bob"))

        result = await manager.send_control(
            state,
            sender_id="alice",
            payload={"op": "rename", "title": "New title"},
        )

        self.assertEqual(result.envelope.epoch, 9)
        self.assertEqual(result.envelope.content_type, GroupContentType.GROUP_CONTROL)

    async def test_group_routing_uses_peer_scoped_session_truth_not_active_peer(self) -> None:
        session_manager = SessionManager()
        session_manager.set_peer_handshake_complete("bob")
        session_manager.set_active_peer("carol")

        live_sender = AsyncMock(
            return_value=GroupTransportOutcome(
                accepted=True,
                reason="live-session",
                transport_message_id="live-bob",
            )
        )
        offline_sender = AsyncMock(
            return_value=GroupTransportOutcome(
                accepted=True,
                reason="blindbox-ready",
                transport_message_id="queue-carol",
            )
        )
        manager = GroupManager(
            session_manager=session_manager,
            send_live=live_sender,
            send_offline=offline_sender,
        )
        state = GroupState(
            group_id="group-6",
            epoch=5,
            members=("alice", "bob", "carol"),
        )

        result = await manager.send_text(
            state,
            sender_id="alice",
            text="hello scoped routing",
        )

        self.assertEqual(
            result.delivery_results["bob"].status,
            GroupDeliveryStatus.DELIVERED_LIVE,
        )
        self.assertEqual(
            result.delivery_results["carol"].status,
            GroupDeliveryStatus.QUEUED_OFFLINE,
        )
        live_sender.assert_awaited_once()
        self.assertEqual(offline_sender.await_count, 1)


if __name__ == "__main__":
    unittest.main()
