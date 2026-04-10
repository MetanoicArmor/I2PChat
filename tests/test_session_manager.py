import asyncio
import unittest
from unittest.mock import patch

from i2pchat.core.session_manager import (
    OutboundPolicy,
    PeerState,
    SessionManager,
    TransportState,
)


class _DummyWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_select_outbound_policy(self) -> None:
        sm = SessionManager()
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="live",
                connected=False,
                handshake_complete=False,
            ),
            OutboundPolicy.LIVE_ONLY,
        )
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="offline",
                connected=True,
                handshake_complete=True,
            ),
            OutboundPolicy.BLINDBOX_ONLY,
        )
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="auto",
                connected=True,
                handshake_complete=True,
            ),
            OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX,
        )
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="auto",
                connected=True,
                handshake_complete=False,
            ),
            OutboundPolicy.QUEUE_THEN_RETRY_LIVE,
        )

    def test_reconnect_backoff_metadata(self) -> None:
        sm = SessionManager()
        delay_1 = sm.schedule_reconnect_backoff(reason="first-fail")
        self.assertGreater(delay_1, 0.0)
        self.assertEqual(sm.reconnect.attempt, 1)
        self.assertEqual(sm.transport_state, TransportState.RECONNECTING)
        self.assertEqual(sm.reconnect.last_failure_reason, "first-fail")

        delay_2 = sm.schedule_reconnect_backoff(reason="second-fail")
        self.assertGreater(delay_2, 0.0)
        self.assertEqual(sm.reconnect.attempt, 2)
        self.assertGreaterEqual(delay_2, delay_1 / 2.0)

    def test_reconnect_backoff_is_scoped_per_peer(self) -> None:
        sm = SessionManager()
        with patch("i2pchat.core.session_manager.random.uniform", return_value=0.0):
            sm.schedule_reconnect_backoff(reason="peer-a-1", peer_id="peer-a.b32.i2p")
            sm.schedule_reconnect_backoff(reason="peer-a-2", peer_id="peer-a.b32.i2p")
            sm.schedule_reconnect_backoff(reason="peer-b-1", peer_id="peer-b.b32.i2p")

        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.reconnect.attempt, 2)
        self.assertEqual(peer_b.reconnect.attempt, 1)
        self.assertEqual(peer_a.reconnect.last_failure_reason, "peer-a-2")
        self.assertEqual(peer_b.reconnect.last_failure_reason, "peer-b-1")

    def test_mark_live_healthy_resets_only_target_peer_backoff(self) -> None:
        sm = SessionManager()
        with patch("i2pchat.core.session_manager.random.uniform", return_value=0.0):
            sm.schedule_reconnect_backoff(reason="peer-a-1", peer_id="peer-a.b32.i2p")
            sm.schedule_reconnect_backoff(reason="peer-b-1", peer_id="peer-b.b32.i2p")
            sm.schedule_reconnect_backoff(reason="peer-b-2", peer_id="peer-b.b32.i2p")
        sm.mark_live_healthy(peer_id="peer-a.b32.i2p")

        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.reconnect.attempt, 0)
        self.assertEqual(peer_b.reconnect.attempt, 2)

    def test_stream_registry_tracks_peer_state(self) -> None:
        sm = SessionManager()
        sm.register_stream("peer.b32.i2p", state=PeerState.CONNECTING)
        self.assertEqual(sm.peer_state, PeerState.CONNECTING)
        sm.update_stream_state("peer.b32.i2p", PeerState.HANDSHAKING)
        self.assertEqual(sm.peer_state, PeerState.HANDSHAKING)
        sm.unregister_stream("peer.b32.i2p")
        self.assertEqual(sm.peer_state, PeerState.DISCONNECTED)

    def test_stream_states_are_independent_per_peer(self) -> None:
        sm = SessionManager()
        sm.register_stream("peer-a.b32.i2p", state=PeerState.HANDSHAKING)
        sm.register_stream("peer-b.b32.i2p", state=PeerState.CONNECTING)
        sm.update_stream_state("peer-a.b32.i2p", PeerState.SECURE)

        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.outbound_streams["peer-a.b32.i2p"].state, PeerState.SECURE)
        self.assertEqual(
            peer_b.outbound_streams["peer-b.b32.i2p"].state,
            PeerState.CONNECTING,
        )

    def test_unreg_recomputes_aggregate_peer_state_from_remaining_streams(self) -> None:
        sm = SessionManager()
        sm.register_stream("peer-a.b32.i2p", state=PeerState.HANDSHAKING)
        sm.register_stream("peer-b.b32.i2p", state=PeerState.SECURE)
        sm.set_active_peer("peer-a.b32.i2p")

        sm.unregister_stream("peer-a.b32.i2p")

        self.assertEqual(sm.get_active_peer(), "peer-a.b32.i2p")
        self.assertEqual(sm.peer_state, PeerState.SECURE)
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_b is not None
        self.assertEqual(peer_b.peer_state, PeerState.SECURE)

    def test_secure_ttl_marks_peer_stale(self) -> None:
        sm = SessionManager(secure_session_ttl_sec=0.001, treat_stale_as_offline=True)
        sm.set_peer_connected("peer-a.b32.i2p", state=PeerState.HANDSHAKING)
        sm.set_peer_handshake_complete("peer-a.b32.i2p")
        self.assertTrue(sm.is_live_path_alive(peer_id="peer-a.b32.i2p"))
        time_before = sm.get_peer_transport("peer-a.b32.i2p").last_activity_mono  # type: ignore[union-attr]
        with patch(
            "i2pchat.core.session_manager.time.monotonic",
            return_value=time_before + 1.0,
        ):
            self.assertFalse(sm.is_live_path_alive(peer_id="peer-a.b32.i2p"))
        self.assertEqual(
            sm.get_peer_transport("peer-a.b32.i2p").peer_state,  # type: ignore[union-attr]
            PeerState.STALE,
        )

    def test_inflight_registry_tracks_messages_per_peer(self) -> None:
        sm = SessionManager()
        sm.set_active_peer("peer-a.b32.i2p")
        sm.register_inflight_message(101)
        self.assertIsNone(sm.get_peer_transport("peer-a.b32.i2p"))
        sm.register_inflight_message(101, peer_id="peer-a.b32.i2p")
        sm.register_inflight_message(202, peer_id="peer-b.b32.i2p")

        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.inflight_msg_ids, {101})
        self.assertEqual(peer_b.inflight_msg_ids, {202})
        self.assertFalse(sm.acknowledge_inflight_message(101))
        self.assertTrue(sm.acknowledge_inflight_message(101, peer_id="peer-a.b32.i2p"))
        self.assertFalse(sm.acknowledge_inflight_message(999, peer_id="peer-a.b32.i2p"))
        self.assertEqual(peer_a.inflight_msg_ids, set())

    def test_is_live_path_alive_legacy_fallback_is_compatibility_only(self) -> None:
        sm = SessionManager()
        sm.transition_peer(PeerState.FAILED, reason="test")
        self.assertTrue(
            sm.is_live_path_alive(connected=True, handshake_complete=True)
        )
        self.assertFalse(
            sm.is_live_path_alive(connected=True, handshake_complete=False)
        )
        self.assertFalse(
            sm.is_live_path_alive(
                connected=True,
                handshake_complete=True,
                peer_id="missing-peer.b32.i2p",
            )
        )
        self.assertFalse(sm.is_live_path_alive())

    def test_active_peer_is_view_only_for_routing_truth(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-b.b32.i2p")
        sm.set_active_peer("peer-a.b32.i2p")

        self.assertEqual(sm.get_active_peer(), "peer-a.b32.i2p")
        self.assertIsNone(sm.get_peer_transport())
        self.assertIsNone(sm.get_active_peer_transport())
        self.assertFalse(sm.is_live_path_alive())
        self.assertFalse(sm.is_live_path_alive(peer_id="peer-a.b32.i2p"))
        self.assertTrue(sm.is_live_path_alive(peer_id="peer-b.b32.i2p"))
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="auto",
                connected=True,
                handshake_complete=True,
                peer_id="peer-a.b32.i2p",
            ),
            OutboundPolicy.QUEUE_THEN_RETRY_LIVE,
        )
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="auto",
                connected=True,
                handshake_complete=True,
            ),
            OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX,
        )

    def test_route_selection_does_not_reuse_live_flags_for_other_peer(self) -> None:
        sm = SessionManager()
        sm.set_peer_connected("peer-a.b32.i2p", state=PeerState.HANDSHAKING)
        sm.set_peer_handshake_complete("peer-a.b32.i2p")

        self.assertFalse(
            sm.is_live_path_alive(
                connected=True,
                handshake_complete=True,
                peer_id="peer-b.b32.i2p",
            )
        )
        self.assertEqual(
            sm.select_outbound_policy(
                requested_route="auto",
                connected=True,
                handshake_complete=True,
                peer_id="peer-b.b32.i2p",
            ),
            OutboundPolicy.QUEUE_THEN_RETRY_LIVE,
        )

    def test_aggregate_peer_state_is_authoritative_not_active_peer_pointer(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-a.b32.i2p")
        sm.set_active_peer("peer-a.b32.i2p")

        sm.mark_peer_failed("peer-b.b32.i2p", reason="link-fail")

        self.assertEqual(sm.get_active_peer(), "peer-a.b32.i2p")
        self.assertEqual(sm.peer_state, PeerState.SECURE)
        sm.reset_peer_lifecycle("peer-a.b32.i2p", reason="peer-a-reset")
        self.assertEqual(sm.peer_state, PeerState.FAILED)
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_b is not None
        self.assertEqual(peer_b.peer_state, PeerState.FAILED)

    def test_peer_failure_does_not_degrade_transport_when_other_peer_ready(self) -> None:
        sm = SessionManager()
        sm.transition_transport(TransportState.READY, reason="test-ready")
        sm.set_peer_handshake_complete("peer-a.b32.i2p")

        sm.mark_live_failure(reason="peer-b-fail", peer_id="peer-b.b32.i2p")

        self.assertEqual(sm.transport_state, TransportState.READY)

    def test_global_reconnect_backoff_does_not_mutate_peer_scoped_metadata(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-a.b32.i2p")

        sm.schedule_reconnect_backoff(reason="global-fail")

        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        assert peer_a is not None
        self.assertEqual(peer_a.reconnect.attempt, 0)
        self.assertEqual(sm.reconnect.attempt, 1)

    def test_reset_peer_lifecycle_is_scoped_to_target_peer(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-a.b32.i2p")
        sm.set_peer_handshake_complete("peer-b.b32.i2p")
        sm.register_inflight_message(11, peer_id="peer-a.b32.i2p")
        sm.register_inflight_message(22, peer_id="peer-b.b32.i2p")
        sm.register_stream(
            "peer-a.b32.i2p",
            state=PeerState.SECURE,
            peer_id="peer-a.b32.i2p",
        )
        sm.register_stream(
            "peer-b.b32.i2p",
            state=PeerState.SECURE,
            peer_id="peer-b.b32.i2p",
        )

        changed = sm.reset_peer_lifecycle("peer-a.b32.i2p", reason="target-reset")

        self.assertTrue(changed)
        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.peer_state, PeerState.DISCONNECTED)
        self.assertEqual(peer_a.inflight_msg_ids, set())
        self.assertEqual(peer_a.outbound_streams, {})
        self.assertEqual(peer_b.peer_state, PeerState.SECURE)
        self.assertEqual(peer_b.inflight_msg_ids, {22})
        self.assertEqual(set(peer_b.outbound_streams), {"peer-b.b32.i2p"})

    def test_reset_peer_lifecycle_can_drop_peer_without_auto_switching_active_peer(self) -> None:
        sm = SessionManager()
        sm.set_active_peer("peer-a.b32.i2p")
        sm.set_peer_handshake_complete("peer-a.b32.i2p")
        sm.set_peer_handshake_complete("peer-b.b32.i2p")

        removed = sm.reset_peer_lifecycle(
            "peer-a.b32.i2p",
            reason="drop-a",
            drop_peer=True,
        )

        self.assertTrue(removed)
        self.assertIsNone(sm.get_peer_transport("peer-a.b32.i2p"))
        self.assertIsNotNone(sm.get_peer_transport("peer-b.b32.i2p"))
        self.assertEqual(sm.get_active_peer(), "")

    def test_reset_peer_session_remains_compatibility_alias(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-a.b32.i2p")

        changed = sm.reset_peer_session("peer-a.b32.i2p", reason="compat-reset")

        self.assertTrue(changed)
        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        assert peer_a is not None
        self.assertEqual(peer_a.peer_state, PeerState.DISCONNECTED)

    def test_reset_peer_transport_remains_compatibility_alias(self) -> None:
        sm = SessionManager()
        sm.set_peer_handshake_complete("peer-a.b32.i2p")

        changed = sm.reset_peer_transport("peer-a.b32.i2p", reason="compat-reset")

        self.assertTrue(changed)
        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        assert peer_a is not None
        self.assertEqual(peer_a.peer_state, PeerState.DISCONNECTED)

    async def test_peer_reset_does_not_imply_full_manager_shutdown(self) -> None:
        sm = SessionManager()
        writer = _DummyWriter()
        sm.session_socket = (object(), writer)  # type: ignore[assignment]
        sm.set_peer_handshake_complete("peer-a.b32.i2p")
        sm.set_peer_handshake_complete("peer-b.b32.i2p")
        sm.register_inflight_message(22, peer_id="peer-b.b32.i2p")
        sm.register_stream(
            "peer-b.b32.i2p",
            state=PeerState.SECURE,
            peer_id="peer-b.b32.i2p",
        )
        sm.keepalive_task = asyncio.create_task(asyncio.sleep(60))

        changed = sm.reset_peer_lifecycle("peer-a.b32.i2p", reason="peer-a-disconnect")

        self.assertTrue(changed)
        self.assertIsNotNone(sm.session_socket)
        self.assertFalse(writer.closed)
        assert sm.keepalive_task is not None
        self.assertFalse(sm.keepalive_task.done())
        peer_a = sm.get_peer_transport("peer-a.b32.i2p")
        peer_b = sm.get_peer_transport("peer-b.b32.i2p")
        assert peer_a is not None
        assert peer_b is not None
        self.assertEqual(peer_a.peer_state, PeerState.DISCONNECTED)
        self.assertEqual(peer_b.peer_state, PeerState.SECURE)
        self.assertEqual(peer_b.inflight_msg_ids, {22})
        self.assertIn("peer-b.b32.i2p", peer_b.outbound_streams)

        await sm.cancel_tasks_and_close_session()
        self.assertIsNone(sm.session_socket)
        self.assertEqual(sm.peer_transport, {})

    async def test_cancel_tasks_and_close_session(self) -> None:
        sm = SessionManager()
        writer = _DummyWriter()
        sm.session_socket = (object(), writer)  # type: ignore[assignment]
        sm.accept_task = asyncio.create_task(asyncio.sleep(60))
        sm.tunnel_task = asyncio.create_task(asyncio.sleep(60))
        sm.keepalive_task = asyncio.create_task(asyncio.sleep(60))
        sm.handshake_watchdog_task = asyncio.create_task(asyncio.sleep(60))
        sm.disconnect_task = asyncio.create_task(asyncio.sleep(60))
        sm.transition_transport(TransportState.SHUTTING_DOWN, reason="test")
        sm.transition_peer(PeerState.HANDSHAKING, reason="test")

        await sm.cancel_tasks_and_close_session()

        self.assertTrue(writer.closed)
        self.assertIsNone(sm.session_socket)
        self.assertFalse(sm.outbound_connect_busy)
        self.assertFalse(sm.disconnecting)
        self.assertEqual(sm.peer_state, PeerState.DISCONNECTED)

    async def test_invalidate_handshake_watchdog_cancels_pending_task(self) -> None:
        sm = SessionManager()
        watchdog = asyncio.create_task(asyncio.sleep(60))
        sm.handshake_watchdog_task = watchdog
        sm.handshake_watchdog_peer_id = "peer-a.b32.i2p"

        generation = sm.invalidate_handshake_watchdog()
        await asyncio.sleep(0)

        self.assertEqual(generation, 1)
        self.assertTrue(watchdog.cancelled())
        self.assertIsNone(sm.handshake_watchdog_task)
        self.assertIsNone(sm.handshake_watchdog_peer_id)
