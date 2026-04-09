from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
