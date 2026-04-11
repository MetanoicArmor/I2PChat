from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from i2pchat.groups import GroupContentType, GroupState
from i2pchat.storage.blindbox_state import BlindBoxState
from i2pchat.storage.group_store import (
    GroupBlindBoxChannel,
    GroupHistoryEntry,
    GroupPendingBlindBoxMessage,
    GroupPendingDelivery,
    StoredGroupConversation,
    append_group_history_entry,
    delete_group_record,
    load_group_conversation,
    load_group_state,
    save_group_conversation,
    upsert_group_state,
)


class GroupStoreTests(unittest.TestCase):
    def test_create_load_save_group_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-1",
                epoch=3,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Store test",
            )

            upsert_group_state(tmpdir, "alice", state, next_group_seq=4)
            loaded_state = load_group_state(tmpdir, "alice", "group-store-1")
            conversation = load_group_conversation(tmpdir, "alice", "group-store-1")

            assert loaded_state is not None
            assert conversation is not None
            self.assertEqual(loaded_state.group_id, "group-store-1")
            self.assertEqual(loaded_state.title, "Store test")
            self.assertEqual(loaded_state.epoch, 3)
            self.assertEqual(loaded_state.members, ("alice.b32.i2p", "bob.b32.i2p"))
            self.assertEqual(conversation.next_group_seq, 4)

    def test_duplicate_group_message_id_is_ignored_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-2",
                epoch=1,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Dedup test",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)
            entry = GroupHistoryEntry(
                kind="peer",
                sender_id="bob.b32.i2p",
                content_type=GroupContentType.GROUP_TEXT,
                text="hello",
                msg_id="msg-1",
                group_seq=1,
                epoch=1,
            )

            first, first_imported = append_group_history_entry(
                tmpdir,
                "alice",
                state,
                entry,
                next_group_seq=2,
            )
            second, second_imported = append_group_history_entry(
                tmpdir,
                "alice",
                state,
                entry,
                next_group_seq=2,
            )

            self.assertTrue(first_imported)
            self.assertFalse(second_imported)
            self.assertEqual(len(first.history), 1)
            self.assertEqual(len(second.history), 1)
            self.assertEqual(second.seen_msg_ids, ("msg-1",))

    def test_persist_and_load_group_text_history_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-3",
                epoch=5,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="History test",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)

            append_group_history_entry(
                tmpdir,
                "alice",
                state,
                GroupHistoryEntry(
                    kind="me",
                    sender_id="alice.b32.i2p",
                    content_type=GroupContentType.GROUP_TEXT,
                    text="persist me",
                    msg_id="persist-msg",
                    group_seq=7,
                    epoch=5,
                ),
                next_group_seq=8,
            )

            loaded = load_group_conversation(tmpdir, "alice", "group-store-3")

            assert loaded is not None
            self.assertEqual(len(loaded.history), 1)
            self.assertEqual(loaded.history[0].text, "persist me")
            self.assertEqual(loaded.history[0].group_seq, 7)
            self.assertEqual(loaded.history[0].epoch, 5)
            self.assertEqual(loaded.next_group_seq, 8)

    def test_duplicate_append_preserves_state_and_next_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            initial_state = GroupState(
                group_id="group-store-4",
                epoch=1,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Initial title",
            )
            upsert_group_state(tmpdir, "alice", initial_state, next_group_seq=2)
            entry = GroupHistoryEntry(
                kind="peer",
                sender_id="bob.b32.i2p",
                content_type=GroupContentType.GROUP_CONTROL,
                payload={"op": "rename", "title": "Renamed title", "epoch": 3},
                msg_id="control-1",
                group_seq=2,
                epoch=3,
            )
            append_group_history_entry(
                tmpdir,
                "alice",
                initial_state,
                entry,
                next_group_seq=3,
            )

            updated_state = GroupState(
                group_id="group-store-4",
                epoch=3,
                members=("alice.b32.i2p", "bob.b32.i2p", "carol.b32.i2p"),
                title="Renamed title",
            )
            conversation, imported = append_group_history_entry(
                tmpdir,
                "alice",
                updated_state,
                entry,
                next_group_seq=6,
            )

            self.assertFalse(imported)
            self.assertEqual(len(conversation.history), 1)
            self.assertEqual(conversation.state.title, "Renamed title")
            self.assertEqual(conversation.state.epoch, 3)
            self.assertIn("carol.b32.i2p", conversation.state.members)
            self.assertEqual(conversation.next_group_seq, 6)

    def test_duplicate_detection_backfills_seen_ids_from_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-legacy-dedupe",
                epoch=2,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Legacy dedupe",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=2)
            entry = GroupHistoryEntry(
                kind="peer",
                sender_id="bob.b32.i2p",
                content_type=GroupContentType.GROUP_TEXT,
                text="legacy hello",
                payload="legacy hello",
                msg_id="legacy-msg-1",
                group_seq=1,
                epoch=2,
            )
            append_group_history_entry(
                tmpdir,
                "alice",
                state,
                entry,
                next_group_seq=2,
            )

            record_path = os.path.join(
                tmpdir,
                next(name for name in os.listdir(tmpdir) if name.startswith("alice.group.")),
            )
            with open(record_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["seen_msg_ids"] = []
            with open(record_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            conversation, imported = append_group_history_entry(
                tmpdir,
                "alice",
                state,
                entry,
                next_group_seq=4,
            )

            self.assertFalse(imported)
            self.assertEqual(len(conversation.history), 1)
            self.assertEqual(conversation.seen_msg_ids, ("legacy-msg-1",))
            self.assertEqual(conversation.next_group_seq, 4)

    def test_load_conversation_backfills_next_group_seq_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-5",
                epoch=9,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Reload sequence",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)
            append_group_history_entry(
                tmpdir,
                "alice",
                state,
                GroupHistoryEntry(
                    kind="me",
                    sender_id="alice.b32.i2p",
                    content_type=GroupContentType.GROUP_TEXT,
                    text="persisted",
                    payload="persisted",
                    msg_id="persisted-1",
                    group_seq=5,
                    epoch=9,
                ),
                next_group_seq=6,
            )

            record_path = os.path.join(
                tmpdir,
                next(name for name in os.listdir(tmpdir) if name.startswith("alice.group.")),
            )
            with open(record_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["next_group_seq"] = 1
            with open(record_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            conversation = load_group_conversation(tmpdir, "alice", "group-store-5")

            assert conversation is not None
            self.assertEqual(len(conversation.history), 1)
            self.assertEqual(conversation.history[0].group_seq, 5)
            self.assertEqual(conversation.next_group_seq, 6)

    def test_group_history_entries_are_normalized_on_append_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-6",
                epoch=4,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Normalized history",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)

            append_group_history_entry(
                tmpdir,
                "alice",
                state,
                GroupHistoryEntry(
                    kind=" PEER ",
                    sender_id="  BOB.B32.I2P ",
                    content_type="GROUP_TEXT",
                    text="",
                    payload="hello normalized",
                    msg_id="  msg-normalized  ",
                    group_seq=3,
                    epoch=4,
                    created_at=datetime(2026, 4, 9, 10, 0, 0),
                    source_peer="  bob.b32.i2p  ",
                    delivery_results={"  ALICE.B32.I2P  ": " delivered_live "},
                ),
                next_group_seq=4,
            )

            conversation = load_group_conversation(tmpdir, "alice", "group-store-6")

            assert conversation is not None
            assert conversation.history
            entry = conversation.history[0]
            self.assertEqual(entry.kind, "peer")
            self.assertEqual(entry.sender_id, "bob.b32.i2p")
            self.assertEqual(entry.content_type, GroupContentType.GROUP_TEXT)
            self.assertEqual(entry.text, "hello normalized")
            self.assertEqual(entry.payload, "hello normalized")
            self.assertEqual(entry.msg_id, "msg-normalized")
            self.assertEqual(entry.source_peer, "bob.b32.i2p")
            self.assertEqual(
                entry.delivery_results,
                {"alice.b32.i2p": "delivered_live"},
            )
            self.assertEqual(entry.created_at.tzinfo, timezone.utc)
            self.assertEqual(conversation.seen_msg_ids, ("msg-normalized",))
            self.assertEqual(conversation.next_group_seq, 4)

    def test_group_history_persists_delivery_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-reasons",
                epoch=1,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Reasons",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)
            append_group_history_entry(
                tmpdir,
                "alice",
                state,
                GroupHistoryEntry(
                    kind="me",
                    sender_id="alice.b32.i2p",
                    content_type=GroupContentType.GROUP_TEXT,
                    text="hi",
                    msg_id="msg-r1",
                    group_seq=1,
                    epoch=1,
                    created_at=datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc),
                    delivery_results={"bob.b32.i2p": "failed"},
                    delivery_reasons={"bob.b32.i2p": "blindbox-await-root"},
                ),
                next_group_seq=2,
            )
            conversation = load_group_conversation(tmpdir, "alice", "group-store-reasons")
            assert conversation is not None
            entry = conversation.history[0]
            self.assertEqual(entry.delivery_results, {"bob.b32.i2p": "failed"})
            self.assertEqual(
                entry.delivery_reasons,
                {"bob.b32.i2p": "blindbox-await-root"},
            )

    def test_group_pending_deliveries_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-pending",
                epoch=4,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Pending",
            )
            conversation = StoredGroupConversation(
                state=state,
                next_group_seq=7,
                pending_deliveries=(
                    GroupPendingDelivery(
                        group_id=state.group_id,
                        group_title=state.title,
                        group_members=state.members,
                        sender_id="alice.b32.i2p",
                        recipient_id="bob.b32.i2p",
                        delivery_id="msg-pending-1:bob",
                        msg_id="msg-pending-1",
                        group_seq=6,
                        epoch=4,
                        content_type=GroupContentType.GROUP_TEXT,
                        payload="queued hello",
                    ),
                ),
            )

            save_group_conversation(tmpdir, "alice", conversation)
            loaded = load_group_conversation(tmpdir, "alice", state.group_id)

            assert loaded is not None
            self.assertEqual(loaded.next_group_seq, 7)
            self.assertEqual(len(loaded.pending_deliveries), 1)
            pending = loaded.pending_deliveries[0]
            self.assertEqual(pending.group_id, state.group_id)
            self.assertEqual(pending.group_title, "Pending")
            self.assertEqual(
                pending.group_members,
                ("alice.b32.i2p", "bob.b32.i2p"),
            )
            self.assertEqual(pending.sender_id, "alice.b32.i2p")
            self.assertEqual(pending.recipient_id, "bob.b32.i2p")
            self.assertEqual(pending.delivery_id, "msg-pending-1:bob")
            self.assertEqual(pending.msg_id, "msg-pending-1")
            self.assertEqual(pending.group_seq, 6)
            self.assertEqual(pending.epoch, 4)
            self.assertEqual(pending.content_type, GroupContentType.GROUP_TEXT)
            self.assertEqual(pending.payload, "queued hello")

    def test_group_blindbox_channel_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-blindbox-channel",
                epoch=9,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="BlindBox channel",
            )
            conversation = StoredGroupConversation(
                state=state,
                blindbox_channel=GroupBlindBoxChannel(
                    channel_id="group:group-store-blindbox-channel",
                    group_epoch=9,
                    state=BlindBoxState(send_index=5, recv_base=2, recv_window=16),
                    root_secret_enc="aa11",
                    root_epoch=4,
                    root_created_at=123,
                    root_send_index_base=3,
                    pending_root_secret_enc="bb22",
                    pending_root_epoch=5,
                    pending_root_created_at=456,
                    pending_root_send_index_base=7,
                    pending_root_target_members=("bob.b32.i2p",),
                    pending_root_acked_members=("bob.b32.i2p",),
                    prev_roots=(
                        {
                            "group_epoch": 8,
                            "root_epoch": 3,
                            "expires_at": 999,
                            "secret_enc": "cc33",
                        },
                    ),
                ),
            )

            save_group_conversation(tmpdir, "alice", conversation)
            loaded = load_group_conversation(tmpdir, "alice", state.group_id)

            assert loaded is not None
            assert loaded.blindbox_channel is not None
            channel = loaded.blindbox_channel
            self.assertEqual(channel.channel_id, "group:group-store-blindbox-channel")
            self.assertEqual(channel.group_epoch, 9)
            self.assertEqual(channel.state.send_index, 5)
            self.assertEqual(channel.root_secret_enc, "aa11")
            self.assertEqual(channel.root_epoch, 4)
            self.assertEqual(channel.pending_root_secret_enc, "bb22")
            self.assertEqual(channel.pending_root_epoch, 5)
            self.assertEqual(channel.pending_root_target_members, ("bob.b32.i2p",))
            self.assertEqual(channel.pending_root_acked_members, ("bob.b32.i2p",))
            self.assertEqual(channel.prev_roots[0]["root_epoch"], 3)
            self.assertEqual(channel.prev_roots[0]["secret_enc"], "cc33")

    def test_pending_group_blindbox_messages_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-pending-group-blindbox",
                epoch=6,
                members=("alice.b32.i2p", "bob.b32.i2p", "carol.b32.i2p"),
                title="Pending group blindbox",
            )
            conversation = StoredGroupConversation(
                state=state,
                pending_group_blindbox_messages=(
                    GroupPendingBlindBoxMessage(
                        group_id=state.group_id,
                        group_title=state.title,
                        group_members=state.members,
                        sender_id="alice.b32.i2p",
                        msg_id="queued-group-msg-1",
                        group_seq=4,
                        epoch=6,
                        content_type=GroupContentType.GROUP_TEXT,
                        payload="group queued hello",
                    ),
                ),
            )

            save_group_conversation(tmpdir, "alice", conversation)
            loaded = load_group_conversation(tmpdir, "alice", state.group_id)

            assert loaded is not None
            self.assertEqual(len(loaded.pending_group_blindbox_messages), 1)
            pending = loaded.pending_group_blindbox_messages[0]
            self.assertEqual(pending.group_id, state.group_id)
            self.assertEqual(pending.group_title, state.title)
            self.assertEqual(pending.group_members, state.members)
            self.assertEqual(pending.sender_id, "alice.b32.i2p")
            self.assertEqual(pending.msg_id, "queued-group-msg-1")
            self.assertEqual(pending.group_seq, 4)
            self.assertEqual(pending.epoch, 6)
            self.assertEqual(pending.payload, "group queued hello")

    def test_legacy_group_record_without_blindbox_channel_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-store-legacy-no-blindbox",
                epoch=2,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Legacy blindboxless",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=3)
            record_path = os.path.join(
                tmpdir,
                next(name for name in os.listdir(tmpdir) if name.startswith("alice.group.")),
            )
            with open(record_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload.pop("blindbox_channel", None)
            payload.pop("pending_group_blindbox_messages", None)
            with open(record_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            conversation = load_group_conversation(
                tmpdir,
                "alice",
                "group-store-legacy-no-blindbox",
            )

            assert conversation is not None
            self.assertIsNone(conversation.blindbox_channel)
            self.assertEqual(conversation.pending_group_blindbox_messages, ())
            self.assertEqual(conversation.next_group_seq, 3)

    def test_delete_group_record_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = GroupState(
                group_id="group-to-delete",
                epoch=1,
                members=("alice.b32.i2p", "bob.b32.i2p"),
                title="Del",
            )
            upsert_group_state(tmpdir, "alice", state, next_group_seq=1)
            self.assertIsNotNone(load_group_state(tmpdir, "alice", "group-to-delete"))
            self.assertTrue(delete_group_record(tmpdir, "alice", "group-to-delete"))
            self.assertIsNone(load_group_state(tmpdir, "alice", "group-to-delete"))
            self.assertFalse(delete_group_record(tmpdir, "alice", "group-to-delete"))
            self.assertFalse(delete_group_record(tmpdir, "alice", ""))


if __name__ == "__main__":
    unittest.main()
