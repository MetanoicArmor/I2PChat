import asyncio
import unittest

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

    def test_stream_registry_tracks_peer_state(self) -> None:
        sm = SessionManager()
        sm.register_stream("peer.b32.i2p", state=PeerState.CONNECTING)
        self.assertEqual(sm.peer_state, PeerState.CONNECTING)
        sm.update_stream_state("peer.b32.i2p", PeerState.HANDSHAKING)
        self.assertEqual(sm.peer_state, PeerState.HANDSHAKING)
        sm.unregister_stream("peer.b32.i2p")
        self.assertEqual(sm.peer_state, PeerState.DISCONNECTED)

    def test_is_live_path_alive_keeps_legacy_routing_semantics(self) -> None:
        sm = SessionManager()
        sm.transition_peer(PeerState.FAILED, reason="test")
        self.assertTrue(
            sm.is_live_path_alive(connected=True, handshake_complete=True)
        )
        self.assertFalse(
            sm.is_live_path_alive(connected=True, handshake_complete=False)
        )

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
