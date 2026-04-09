import os
import sys
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

from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.core.send_retry_policy import should_start_auto_connect_retry

DUMMY_DEST_B32 = "ffffffffffffffffffffffffffffffffffffffff.b32.i2p"
STORED_PEER_1 = "gggggggggggggggggggggggggggggggggggggggg.b32.i2p"
STORED_PEER_2 = "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh.b32.i2p"
STORED_PEER_3 = "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii.b32.i2p"
STORED_PEER_4 = "jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj.b32.i2p"


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
    async def test_send_text_live_route_blocks_without_session(self) -> None:
        core = I2PChatCore(profile="alice")
        result = await core.send_text("x", route="live")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "needs-live-session")

    async def test_send_text_live_route_blocks_during_handshake(self) -> None:
        core = I2PChatCore(profile="alice")
        core.conn = (object(), _DummyWriter())
        core.handshake_complete = False
        result = await core.send_text("x", route="live")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "handshake-in-progress")

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
            core.conn = (object(), _DummyWriter())
            core.handshake_complete = True
            core._send_text_via_blindbox = AsyncMock(return_value=99)  # type: ignore[method-assign]
            result = await core.send_text("q-offline-while-live", route="offline")
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "offline-queued")
            self.assertEqual(result.message_id, "99")
            core._send_text_via_blindbox.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_send_text_uses_live_route_when_secure_connected(self) -> None:
        core = I2PChatCore(profile="alice")
        core.conn = (object(), _DummyWriter())
        core.handshake_complete = True
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
        core.conn = (object(), writer)
        core.handshake_complete = True
        long_text = "L" * 5000
        result = await core.send_text(long_text)
        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertGreaterEqual(len(writer.frames), 2)

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
            core.stored_peer = STORED_PEER_2
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
            core.stored_peer = STORED_PEER_3
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
            core.conn = (object(), _DummyWriter())
            core.handshake_complete = False
            self.assertEqual(
                core.get_delivery_telemetry()["state"], "connecting-handshake"
            )

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
