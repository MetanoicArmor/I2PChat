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

from i2p_chat_core import I2PChatCore
from main_qt import _should_start_auto_connect_retry

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
    async def test_send_text_uses_live_route_when_secure_connected(self) -> None:
        core = I2PChatCore(profile="alice")
        core.conn = (object(), _DummyWriter())
        core.handshake_complete = True
        result = await core.send_text("hello-live")
        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertEqual(result.reason, "live-session")

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
            core._send_text_via_blindbox = AsyncMock(return_value=True)  # type: ignore[method-assign]
            result = await core.send_text("hello-offline")
            self.assertTrue(result.accepted)
            self.assertEqual(result.route, "offline-queued")
            self.assertEqual(result.reason, "blindbox-ready")

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
            core._send_text_via_blindbox = AsyncMock(return_value=False)  # type: ignore[method-assign]
            result = await core.send_text("hello-await-root")
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-await-root")
            self.assertIn("Connect once", result.hint)

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
            core._send_text_via_blindbox = AsyncMock(return_value=False)  # type: ignore[method-assign]
            result = await core.send_text("hello-disabled")
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "blindbox-disabled")

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
        self.assertTrue(
            _should_start_auto_connect_retry(
                reason="send-failed",
                has_running_task=False,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertTrue(
            _should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=False,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertFalse(
            _should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=True,
                now_mono=20.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )
        self.assertFalse(
            _should_start_auto_connect_retry(
                reason="blindbox-await-root",
                has_running_task=False,
                now_mono=21.0,
                last_started_mono=20.0,
                cooldown_sec=6.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
