from __future__ import annotations

import json
import os
import tempfile
import unittest

from i2pchat.groups import GroupContentType, GroupState
from i2pchat.storage.group_store import (
    GroupHistoryEntry,
    append_group_history_entry,
    load_group_conversation,
    load_group_state,
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


if __name__ == "__main__":
    unittest.main()
