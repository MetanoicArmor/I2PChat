import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import I2PChatCore, _BlindBoxPeerSnapshot
from i2pchat.core.session_manager import PeerState
from i2pchat.core.send_retry_policy import should_start_auto_connect_retry
from i2pchat.storage.blindbox_state import BlindBoxState

from tests.live_session_helpers import attach_mock_live_session

DUMMY_DEST_B32 = "ffffffffffffffffffffffffffffffffffffffff"
STORED_PEER_1 = "gggggggggggggggggggggggggggggggggggggggg"
STORED_PEER_2 = "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"
STORED_PEER_3 = "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii"
STORED_PEER_4 = "jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj"


class _DummyWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class _DummyDest:
    def __init__(self) -> None:
        self.base32 = DUMMY_DEST_B32


class SendTextRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_activate_peer_context_reloads_blindbox_state_for_selected_peer(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest()
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._trigger_blindbox_hot_poll = lambda reason="": None  # type: ignore[method-assign]

                core._save_blindbox_peer_snapshot(
                    _BlindBoxPeerSnapshot(
                        peer_addr=STORED_PEER_1,
                        peer_id=STORED_PEER_1,
                        state=BlindBoxState(),
                        root_secret=b"a" * 32,
                        root_epoch=1,
                    )
                )
                core._save_blindbox_peer_snapshot(
                    _BlindBoxPeerSnapshot(
                        peer_addr=STORED_PEER_2,
                        peer_id=STORED_PEER_2,
                        state=BlindBoxState(),
                        root_secret=b"b" * 32,
                        root_epoch=2,
                    )
                )

                core.activate_peer_context(STORED_PEER_1)
                self.assertEqual(core._blindbox_root_secret, b"a" * 32)
                self.assertEqual(core._blindbox_root_epoch, 1)

                core.activate_peer_context(STORED_PEER_2)
                self.assertEqual(core._blindbox_root_secret, b"b" * 32)
                self.assertEqual(core._blindbox_root_epoch, 2)

    async def test_send_text_live_route_blocks_without_session(self) -> None:
        core = I2PChatCore(profile="alice")
        result = await core.send_text("x", route="live")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "needs-live-session")

    async def test_send_text_live_route_blocks_during_handshake(self) -> None:
        core = I2PChatCore(profile="alice")
        attach_mock_live_session(
            core, STORED_PEER_1, (object(), _DummyWriter()), handshake_complete=False
        )
        core._send_text_via_blindbox = AsyncMock(return_value=99)  # type: ignore[method-assign]
        result = await core.send_text("x", route="live")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "handshake-in-progress")
        core._send_text_via_blindbox.assert_not_called()  # type: ignore[attr-defined]

    async def test_send_text_offline_route_uses_blindbox_when_live_connected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_1
            core.my_dest = _DummyDest()
            attach_mock_live_session(core, STORED_PEER_1, (object(), _DummyWriter()))
            core.session_manager.set_peer_handshake_complete(
                core._normalize_peer_addr(STORED_PEER_1)
            )
            core._send_text_via_blindbox = AsyncMock(return_value=99)  # type: ignore[method-assign]
            result = await core.send_text("q-offline-while-live", route="offline")
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "offline-queued")
            self.assertEqual(result.message_id, "99")
            core._send_text_via_blindbox.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_send_text_uses_live_route_when_secure_connected(self) -> None:
        core = I2PChatCore(profile="alice")
        attach_mock_live_session(core, STORED_PEER_1, (object(), _DummyWriter()))
        core.session_manager.set_peer_handshake_complete(
            core._normalize_peer_addr(STORED_PEER_1)
        )
        result = await core.send_text("hello-live")
        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertEqual(result.reason, "live-session")
        self.assertEqual(result.delivery_state, "sending")
        self.assertFalse(result.retryable)
        self.assertIsNotNone(result.message_id)

    async def test_send_text_splits_long_live_into_multiple_frames(self) -> None:
        core = I2PChatCore(profile="alice")
        writer = _DummyWriter()
        attach_mock_live_session(core, STORED_PEER_1, (object(), writer))
        core.session_manager.set_peer_handshake_complete(
            core._normalize_peer_addr(STORED_PEER_1)
        )
        long_text = "L" * 5000
        result = await core.send_text(long_text)
        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertGreaterEqual(len(writer.frames), 2)

    async def test_send_text_auto_keeps_live_route_even_when_peer_marked_stale(self) -> None:
        core = I2PChatCore(profile="alice")
        writer = _DummyWriter()
        attach_mock_live_session(core, STORED_PEER_1, (object(), writer))
        core.session_manager.set_peer_handshake_complete(
            core._normalize_peer_addr(STORED_PEER_1)
        )
        core.session_manager.transition_peer(PeerState.STALE, reason="test")
        result = await core.send_text("hello-after-stale")
        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")

    async def test_send_text_auto_prefers_live_even_when_blindbox_busy(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_1
            writer = _DummyWriter()
            attach_mock_live_session(core, STORED_PEER_1, (object(), writer))
            core.session_manager.set_peer_connected(
                STORED_PEER_1, state=PeerState.HANDSHAKING
            )
            core.session_manager.set_peer_handshake_complete(STORED_PEER_1)
            await core._blindbox_send_lock.acquire()  # noqa: SLF001 - route preference behavior
            try:
                result = await core.send_text("hello-live-while-blindbox-busy")
            finally:
                core._blindbox_send_lock.release()  # noqa: SLF001
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "online-live")

    async def test_send_text_auto_queues_offline_during_handshake_when_blindbox_ready(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_2
            core.my_dest = _DummyDest()
            attach_mock_live_session(
                core, STORED_PEER_2, (object(), _DummyWriter()), handshake_complete=False
            )
            core.session_manager.set_peer_connected(
                STORED_PEER_2, state=PeerState.HANDSHAKING
            )
            core._send_text_via_blindbox = AsyncMock(return_value=123)  # type: ignore[method-assign]
            result = await core.send_text("hello-handshaking-auto")
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "offline-queued")
            self.assertEqual(result.reason, "blindbox-ready")
            core._send_text_via_blindbox.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_send_text_queues_offline_when_blindbox_ready(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_1
            core.my_dest = _DummyDest()
            core._send_text_via_blindbox = AsyncMock(return_value=41)  # type: ignore[method-assign]
            result = await core.send_text("hello-offline")
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "offline-queued")
            self.assertEqual(result.reason, "blindbox-ready")
            self.assertEqual(result.delivery_state, "queued")
            self.assertEqual(result.message_id, "41")

    async def test_send_text_offline_explicit_peer_does_not_fallback_to_current_peer_root(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest()
                core.current_peer_addr = STORED_PEER_1
                core._blindbox_root_secret = b"a" * 32  # noqa: SLF001
                core._blindbox_root_epoch = 1  # noqa: SLF001
                core._blindbox_client = types.SimpleNamespace(  # noqa: SLF001
                    is_runtime_ready=lambda: True
                )
                core._put_blindbox_frame_with_slot_retry = AsyncMock()  # type: ignore[method-assign]

                result = await core.send_text(
                    "hello-offline-explicit",
                    route="offline",
                    peer_address=STORED_PEER_2,
                )

                self.assertFalse(result.accepted)
                core._put_blindbox_frame_with_slot_retry.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_send_text_offline_explicit_peer_uses_target_snapshot(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest()
                core.current_peer_addr = STORED_PEER_1
                core._blindbox_root_secret = b"a" * 32  # noqa: SLF001
                core._blindbox_root_epoch = 1  # noqa: SLF001
                core._blindbox_state = BlindBoxState(send_index=7)  # noqa: SLF001
                core._blindbox_client = types.SimpleNamespace(  # noqa: SLF001
                    is_runtime_ready=lambda: True
                )
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._save_blindbox_peer_snapshot(
                    _BlindBoxPeerSnapshot(
                        peer_addr=STORED_PEER_2,
                        peer_id=STORED_PEER_2,
                        state=BlindBoxState(send_index=41),
                        root_secret=b"b" * 32,
                        root_epoch=2,
                    )
                )
                core._put_blindbox_frame_with_slot_retry = AsyncMock()  # type: ignore[method-assign]

                result = await core.send_text(
                    "hello-offline-target-snapshot",
                    route="offline",
                    peer_address=STORED_PEER_2,
                )

                self.assertTrue(result.accepted)
                self.assertEqual(result.route, "offline-queued")
                kwargs = core._put_blindbox_frame_with_slot_retry.await_args.kwargs  # type: ignore[attr-defined]
                self.assertEqual(kwargs["root_secret"], b"b" * 32)
                self.assertEqual(kwargs["root_epoch"], 2)
                self.assertEqual(kwargs["peer_id"], STORED_PEER_2)
                self.assertEqual(kwargs["state"].send_index, 41)
                self.assertIsNot(kwargs["state"], core._blindbox_state)

    async def test_send_text_blocked_requires_connect_for_initial_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.current_peer_addr = STORED_PEER_2
            core.my_dest = _DummyDest()
            core._blindbox_root_secret = None
            core._send_text_via_blindbox = AsyncMock(return_value=None)  # type: ignore[method-assign]
            result = await core.send_text("hello-await-root")
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-await-root")
            self.assertIn("Connect once", result.hint)
            self.assertEqual(result.delivery_state, "failed")

    async def test_send_text_blocked_when_blindbox_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "0",
                "I2PCHAT_BLINDBOX_REPLICAS": "",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.current_peer_addr = STORED_PEER_3
            core._send_text_via_blindbox = AsyncMock(return_value=None)  # type: ignore[method-assign]
            result = await core.send_text("hello-disabled")
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-disabled")
            self.assertEqual(result.delivery_state, "failed")

    async def test_send_text_auto_offline_fails_fast_when_blindbox_send_busy(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_3
            core.my_dest = _DummyDest()
            await core._blindbox_send_lock.acquire()  # noqa: SLF001 - concurrency behavior
            try:
                result = await core.send_text("hello-busy-auto")
            finally:
                core._blindbox_send_lock.release()  # noqa: SLF001
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-send-busy")
            self.assertEqual(result.delivery_state, "failed")

    async def test_send_text_offline_fails_fast_when_blindbox_send_busy(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_4
            core.my_dest = _DummyDest()
            await core._blindbox_send_lock.acquire()  # noqa: SLF001 - concurrency behavior
            try:
                result = await core.send_text("hello-busy-offline", route="offline")
            finally:
                core._blindbox_send_lock.release()  # noqa: SLF001
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-send-busy")
            self.assertEqual(result.delivery_state, "failed")

    def test_delivery_telemetry_states(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_4
            core.my_dest = _DummyDest()
            core._blindbox_root_secret = b"x" * 32
            self.assertEqual(core.get_delivery_telemetry()["state"], "offline-ready")
            core._blindbox_root_secret = None
            self.assertEqual(core.get_delivery_telemetry()["state"], "await-live-root")
            attach_mock_live_session(
                core, STORED_PEER_1, (object(), _DummyWriter()), handshake_complete=False
            )
            self.assertEqual(
                core.get_delivery_telemetry()["state"], "connecting-handshake"
            )

    def test_delivery_telemetry_uses_selected_peer_transport_snapshot(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core.stored_peer = STORED_PEER_1
            core.current_peer_addr = STORED_PEER_1

            core.session_manager.set_active_peer(STORED_PEER_2)
            core.session_manager.set_peer_connected(
                STORED_PEER_2, state=PeerState.HANDSHAKING
            )
            core.session_manager.register_stream(
                STORED_PEER_2,
                state=PeerState.HANDSHAKING,
                peer_id=STORED_PEER_2,
            )
            core.session_manager.schedule_reconnect_backoff(
                reason="other-peer-fail",
                peer_id=STORED_PEER_2,
            )

            core.session_manager.set_peer_connected(
                STORED_PEER_1, state=PeerState.CONNECTING
            )

            telemetry = core.get_delivery_telemetry()
            self.assertEqual(telemetry["peer_state"], PeerState.CONNECTING.value)
            self.assertEqual(telemetry["outbound_streams"], 0)
            self.assertEqual(telemetry["reconnect_attempt"], 0)

    def test_auto_connect_retry_policy_is_single_attempt_with_cooldown(self) -> None:
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="send-failed",
                has_running_task=False,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertTrue(
            should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=False,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=True,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=False,
                now_mono=21.0,
                last_started_mono=20.0,
                cooldown_sec=6.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
