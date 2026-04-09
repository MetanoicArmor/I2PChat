from __future__ import annotations

import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock

if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.groups import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupEnvelope,
    GroupRecipientDeliveryMetadata,
    GroupState,
    GroupTransportOutcome,
)
from i2pchat.groups.wire import encode_group_transport_text

ALICE_B32 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
BOB_B32 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
CAROL_B32 = "cccccccccccccccccccccccccccccccccccccccc.b32.i2p"


class _DummyDest:
    def __init__(self, base32: str) -> None:
        self.base32 = base32


class _DummyWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class GroupCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_group_text_from_core_through_group_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            core.session_manager.set_peer_handshake_complete(BOB_B32)
            group_state = core.create_group(
                title="Core group",
                members=[BOB_B32, CAROL_B32],
                group_id="core-group-1",
                epoch=7,
            )
            core.group_manager._send_live = AsyncMock(  # type: ignore[attr-defined]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core.group_manager._send_offline = AsyncMock(  # type: ignore[attr-defined]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-carol",
                )
            )

            result = await core.send_group_text(group_state.group_id, "hello group")
            history = core.load_group_history(group_state.group_id)
            reloaded_state = core.load_group_state(group_state.group_id)

            assert reloaded_state is not None
            self.assertEqual(result.envelope.epoch, 7)
            self.assertEqual(
                result.delivery_results[BOB_B32].status,
                GroupDeliveryStatus.DELIVERED_LIVE,
            )
            self.assertEqual(
                result.delivery_results[CAROL_B32].status,
                GroupDeliveryStatus.QUEUED_OFFLINE,
            )
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].text, "hello group")
            self.assertEqual(history[0].epoch, 7)
            self.assertEqual(reloaded_state.epoch, 7)

    def test_incoming_group_text_imports_into_local_group_history_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            messages: list[object] = []
            core = I2PChatCore(profile="alice", on_message=messages.append)
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            state = GroupState(
                group_id="core-group-2",
                epoch=4,
                members=(ALICE_B32, BOB_B32),
                title="Imported group",
            )
            envelope = GroupEnvelope(
                group_id=state.group_id,
                epoch=state.epoch,
                msg_id="group-msg-1",
                sender_id=BOB_B32,
                group_seq=2,
                content_type=GroupContentType.GROUP_TEXT,
                payload="hello from bob",
            )
            wire_text = encode_group_transport_text(
                state,
                envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_B32,
                    delivery_id="group-msg-1:alice",
                ),
            )

            handled = core.import_group_transport_text(
                wire_text,
                source_peer=BOB_B32,
            )

            self.assertTrue(handled)
            loaded_state = core.load_group_state("core-group-2")
            history = core.load_group_history("core-group-2")
            assert loaded_state is not None
            self.assertEqual(loaded_state.title, "Imported group")
            self.assertEqual(loaded_state.epoch, 4)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].msg_id, "group-msg-1")
            self.assertEqual(history[0].text, "hello from bob")
            self.assertTrue(messages)

    def test_duplicate_import_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            state = GroupState(
                group_id="core-group-3",
                epoch=2,
                members=(ALICE_B32, BOB_B32),
                title="Dup test",
            )
            envelope = GroupEnvelope(
                group_id=state.group_id,
                epoch=2,
                msg_id="dup-msg",
                sender_id=BOB_B32,
                group_seq=1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="once only",
            )
            wire_text = encode_group_transport_text(
                state,
                envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_B32,
                    delivery_id="dup-msg:alice",
                ),
            )

            self.assertTrue(core.import_group_transport_text(wire_text, source_peer=BOB_B32))
            self.assertTrue(core.import_group_transport_text(wire_text, source_peer=BOB_B32))
            self.assertEqual(len(core.load_group_history("core-group-3")), 1)

    async def test_direct_chat_behavior_still_works(self) -> None:
        core = I2PChatCore(profile="alice")
        core.conn = (object(), _DummyWriter())
        core.handshake_complete = True

        result = await core.send_text("hello direct")

        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertEqual(result.reason, "live-session")

    def test_plain_text_is_not_interpreted_as_group_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            self.assertFalse(core.import_group_transport_text("plain direct text"))


if __name__ == "__main__":
    unittest.main()
