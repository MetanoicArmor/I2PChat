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
    def test_save_group_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            state = GroupState(
                group_id="core-group-save",
                epoch=11,
                members=(ALICE_B32, BOB_B32),
                title="Saved title",
            )

            saved = core.save_group_state(state, next_group_seq=9)
            loaded = core.load_group_state("core-group-save")
            history = core.load_group_history("core-group-save")

            assert loaded is not None
            self.assertEqual(saved.group_id, "core-group-save")
            self.assertEqual(loaded.title, "Saved title")
            self.assertEqual(loaded.epoch, 11)
            self.assertEqual(history, [])

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
            self.assertEqual(history[0].group_seq, result.envelope.group_seq)
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
            self.assertEqual(history[0].group_seq, 2)
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

    def test_invalid_group_text_payload_is_rejected_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            messages: list[object] = []
            core = I2PChatCore(profile="alice", on_message=messages.append)
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            bad_wire = (
                '__I2PCHAT_GROUP__:{"content_type":"GROUP_TEXT","created_at":"2026-04-09T10:00:00+00:00",'
                '"delivery_id":"bad-1:alice","epoch":1,"group_id":"core-group-bad-text","group_seq":1,'
                '"members":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"],'
                '"msg_id":"bad-1","payload":{"text":"not-a-string"},"recipient_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p",'
                '"sender_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","transport":"group","version":1}'
            )

            self.assertTrue(core.import_group_transport_text(bad_wire, source_peer=BOB_B32))
            self.assertIsNone(core.load_group_state("core-group-bad-text"))
            self.assertEqual(core.load_group_history("core-group-bad-text"), [])
            self.assertTrue(messages)
            self.assertEqual(getattr(messages[-1], "kind", None), "error")

    def test_invalid_group_control_recipient_is_rejected_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            bad_wire = (
                '__I2PCHAT_GROUP__:{"content_type":"GROUP_CONTROL","created_at":"2026-04-09T10:00:00+00:00",'
                '"delivery_id":"bad-2:carol","epoch":2,"group_id":"core-group-bad-control","group_seq":2,'
                '"members":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","cccccccccccccccccccccccccccccccccccccccc.b32.i2p"],'
                '"msg_id":"bad-2","payload":{"op":"rename","title":"Nope"},"recipient_id":"cccccccccccccccccccccccccccccccccccccccc.b32.i2p",'
                '"sender_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","transport":"group","version":1}'
            )

            self.assertTrue(core.import_group_transport_text(bad_wire, source_peer=BOB_B32))
            self.assertIsNone(core.load_group_state("core-group-bad-control"))
            self.assertEqual(core.load_group_history("core-group-bad-control"), [])

    async def test_group_control_can_be_persisted_and_imported_minimally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_B32)
            group_state = core.create_group(
                title="Original title",
                members=[BOB_B32],
                group_id="core-group-4",
                epoch=3,
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
                    transport_message_id="queue-bob",
                )
            )

            sent = await core.send_group_control(
                group_state.group_id,
                {"op": "rename", "title": "Renamed title", "epoch": 4},
            )
            after_send_state = core.load_group_state(group_state.group_id)
            after_send_history = core.load_group_history(group_state.group_id)

            assert after_send_state is not None
            self.assertEqual(sent.envelope.content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(after_send_state.title, "Renamed title")
            self.assertEqual(after_send_state.epoch, 4)
            self.assertEqual(after_send_history[-1].kind, "me")
            self.assertEqual(after_send_history[-1].content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(after_send_history[-1].group_seq, sent.envelope.group_seq)

            imported_state = GroupState(
                group_id=group_state.group_id,
                epoch=5,
                members=(ALICE_B32, BOB_B32, CAROL_B32),
                title="Renamed title",
            )
            imported_envelope = GroupEnvelope(
                group_id=group_state.group_id,
                epoch=5,
                msg_id="control-import-1",
                sender_id=BOB_B32,
                group_seq=sent.envelope.group_seq + 1,
                content_type=GroupContentType.GROUP_CONTROL,
                payload={
                    "op": "rename",
                    "title": "Imported title",
                    "members": [ALICE_B32, BOB_B32, CAROL_B32],
                    "epoch": 5,
                },
            )
            imported_wire = encode_group_transport_text(
                imported_state,
                imported_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_B32,
                    delivery_id="control-import-1:alice",
                ),
            )

            self.assertTrue(
                core.import_group_transport_text(imported_wire, source_peer=BOB_B32)
            )
            final_state = core.load_group_state(group_state.group_id)
            final_history = core.load_group_history(group_state.group_id)

            assert final_state is not None
            self.assertEqual(final_state.title, "Imported title")
            self.assertEqual(final_state.epoch, 5)
            self.assertIn(CAROL_B32, final_state.members)
            self.assertEqual(final_history[-1].kind, "peer")
            self.assertEqual(final_history[-1].content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(final_history[-1].msg_id, "control-import-1")
            self.assertEqual(final_history[-1].group_seq, imported_envelope.group_seq)

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
