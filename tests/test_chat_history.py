import os
import secrets
import struct
import tempfile
import unittest

import crypto
from chat_history import (
    DEFAULT_MAX_MESSAGES,
    HEADER_SIZE,
    HISTORY_MAGIC,
    HISTORY_VERSION,
    HistoryEntry,
    delete_history,
    derive_history_key,
    load_history,
    save_history,
)


def _make_entries(n: int) -> list[HistoryEntry]:
    entries = []
    for i in range(n):
        kind = "me" if i % 2 == 0 else "peer"
        entries.append(HistoryEntry(kind=kind, text=f"msg-{i}", ts=f"2026-01-01T00:00:{i:02d}Z"))
    return entries


IDENTITY_KEY = secrets.token_bytes(32)
PEER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryRoundTripTests(unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = _make_entries(5)
            save_history(td, "alice", PEER, entries, IDENTITY_KEY)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(len(loaded), 5)
            for orig, got in zip(entries, loaded):
                self.assertEqual(orig.kind, got.kind)
                self.assertEqual(orig.text, got.text)
                self.assertEqual(orig.ts, got.ts)

    def test_empty_entries_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, [], IDENTITY_KEY)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_load_nonexistent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_salt_reused_across_saves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            with open(os.path.join(td, os.listdir(td)[0]), "rb") as f:
                f.read(6)
                salt1 = f.read(32)

            save_history(td, "alice", PEER, _make_entries(5), IDENTITY_KEY)
            with open(os.path.join(td, os.listdir(td)[0]), "rb") as f:
                f.read(6)
                salt2 = f.read(32)

            self.assertEqual(salt1, salt2)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryCorruptionTests(unittest.TestCase):
    def test_corrupted_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            path = os.path.join(td, os.listdir(td)[0])
            with open(path, "r+b") as f:
                f.seek(HEADER_SIZE + 10)
                f.write(b"\xff" * 16)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_wrong_key_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            wrong_key = secrets.token_bytes(32)
            loaded = load_history(td, "alice", PEER, wrong_key)
            self.assertEqual(loaded, [])

    def test_truncated_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            path = os.path.join(td, os.listdir(td)[0])
            with open(path, "r+b") as f:
                f.truncate(10)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_bad_magic_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            path = os.path.join(td, os.listdir(td)[0])
            with open(path, "r+b") as f:
                f.write(b"XXXX")
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_unsupported_version_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            path = os.path.join(td, os.listdir(td)[0])
            with open(path, "r+b") as f:
                f.seek(4)
                f.write(struct.pack(">H", 99))
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryFIFOTests(unittest.TestCase):
    def test_fifo_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = _make_entries(DEFAULT_MAX_MESSAGES + 50)
            save_history(td, "alice", PEER, entries, IDENTITY_KEY, max_messages=DEFAULT_MAX_MESSAGES)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(len(loaded), DEFAULT_MAX_MESSAGES)
            self.assertEqual(loaded[0].text, f"msg-50")
            self.assertEqual(loaded[-1].text, f"msg-{DEFAULT_MAX_MESSAGES + 49}")

    def test_custom_max_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = _make_entries(20)
            save_history(td, "alice", PEER, entries, IDENTITY_KEY, max_messages=10)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(len(loaded), 10)
            self.assertEqual(loaded[0].text, "msg-10")


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryDeleteTests(unittest.TestCase):
    def test_delete_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            self.assertTrue(delete_history(td, "alice", PEER))
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(loaded, [])

    def test_delete_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(delete_history(td, "alice", PEER))


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryPeerIsolationTests(unittest.TestCase):
    def test_different_peers_have_separate_histories(self) -> None:
        peer_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
        peer_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
        with tempfile.TemporaryDirectory() as td:
            save_history(
                td,
                "alice",
                peer_a,
                [HistoryEntry(kind="me", text="from-a", ts="2026-01-01T00:00:01Z")],
                IDENTITY_KEY,
            )
            save_history(
                td,
                "alice",
                peer_b,
                [HistoryEntry(kind="me", text="from-b", ts="2026-01-01T00:00:02Z")],
                IDENTITY_KEY,
            )

            loaded_a = load_history(td, "alice", peer_a, IDENTITY_KEY)
            loaded_b = load_history(td, "alice", peer_b, IDENTITY_KEY)

            self.assertEqual([x.text for x in loaded_a], ["from-a"])
            self.assertEqual([x.text for x in loaded_b], ["from-b"])

    def test_same_peer_rewrite_does_not_append_old_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(
                td,
                "alice",
                PEER,
                [HistoryEntry(kind="me", text="old", ts="2026-01-01T00:00:01Z")],
                IDENTITY_KEY,
            )
            save_history(
                td,
                "alice",
                PEER,
                [HistoryEntry(kind="me", text="new", ts="2026-01-01T00:00:02Z")],
                IDENTITY_KEY,
            )

            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual([x.text for x in loaded], ["new"])


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryAtomicityTests(unittest.TestCase):
    def test_interrupted_save_preserves_old_file(self) -> None:
        """If atomic_write_bytes raises during replace, old data survives."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, _make_entries(3), IDENTITY_KEY)
            original = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(len(original), 3)

            with patch("chat_history.atomic_write_bytes", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_history(td, "alice", PEER, _make_entries(10), IDENTITY_KEY)

            still_there = load_history(td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual(len(still_there), 3)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class DeriveHistoryKeyTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        k1 = derive_history_key(IDENTITY_KEY)
        k2 = derive_history_key(IDENTITY_KEY)
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 32)

    def test_different_identity_different_key(self) -> None:
        k1 = derive_history_key(IDENTITY_KEY)
        k2 = derive_history_key(secrets.token_bytes(32))
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
