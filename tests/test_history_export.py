"""
Tests for i2pchat.storage.history_export — encrypted chat history export/import.
"""

import json
import os
import secrets
import struct
import tempfile
import unittest

from i2pchat import crypto
from i2pchat.storage.chat_history import HistoryEntry, load_history, save_history
from i2pchat.storage.history_export import (
    EXPORT_MAGIC,
    EXPORT_VERSION,
    EXPORT_HEADER_SIZE,
    CONFLICT_MERGE,
    CONFLICT_REPLACE,
    CONFLICT_SKIP,
    export_history,
    import_history,
    _merge_entries,
    _build_payload,
    _parse_payload,
    _dict_to_entry,
    _derive_export_key,
)


IDENTITY_KEY = secrets.token_bytes(32)
PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
PASSWORD = "test-password-1234"


def _make_entries(n: int, prefix: str = "msg") -> list[HistoryEntry]:
    return [
        HistoryEntry(
            kind="me" if i % 2 == 0 else "peer",
            text=f"{prefix}-{i}",
            ts=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
            message_id=f"mid-{prefix}-{i}",
        )
        for i in range(n)
    ]


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ExportRoundTripTests(unittest.TestCase):
    def test_export_import_roundtrip_replace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = _make_entries(5)
            save_history(td, "alice", PEER_A, entries, IDENTITY_KEY)

            archive = os.path.join(td, "export.i2hx")
            export_history("alice", IDENTITY_KEY, [PEER_A], PASSWORD, archive, td)

            self.assertTrue(os.path.exists(archive))

            # Import into fresh dir
            with tempfile.TemporaryDirectory() as td2:
                results = import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertIn(PEER_A.lower(), results)
                loaded = load_history(td2, "alice", PEER_A, IDENTITY_KEY)
                self.assertEqual(len(loaded), 5)
                for orig, got in zip(entries, loaded):
                    self.assertEqual(orig.text, got.text)
                    self.assertEqual(orig.ts, got.ts)

    def test_export_multi_peer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER_A, _make_entries(3, "a"), IDENTITY_KEY)
            save_history(td, "alice", PEER_B, _make_entries(4, "b"), IDENTITY_KEY)

            archive = os.path.join(td, "export.i2hx")
            export_history("alice", IDENTITY_KEY, [PEER_A, PEER_B], PASSWORD, archive, td)

            with tempfile.TemporaryDirectory() as td2:
                results = import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertEqual(results[PEER_A.lower()], 3)
                self.assertEqual(results[PEER_B.lower()], 4)

    def test_export_empty_peer_list_exports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = os.path.join(td, "export.i2hx")
            # No history saved, peers=[] should produce archive with no peers
            export_history("alice", IDENTITY_KEY, [], PASSWORD, archive, td)
            self.assertTrue(os.path.exists(archive))

            with tempfile.TemporaryDirectory() as td2:
                results = import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertEqual(results, {})

    def test_wrong_password_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER_A, _make_entries(2), IDENTITY_KEY)
            archive = os.path.join(td, "export.i2hx")
            export_history("alice", IDENTITY_KEY, [PEER_A], PASSWORD, archive, td)

            with tempfile.TemporaryDirectory() as td2:
                with self.assertRaises(ValueError) as ctx:
                    import_history(archive, "wrong-password", IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertIn("Decryption failed", str(ctx.exception))

    def test_profile_name_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER_A, _make_entries(2), IDENTITY_KEY)
            archive = os.path.join(td, "export.i2hx")
            export_history("alice", IDENTITY_KEY, [PEER_A], PASSWORD, archive, td)

            with tempfile.TemporaryDirectory() as td2:
                import_history(
                    archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE,
                    profile_name="bob"
                )
                loaded = load_history(td2, "bob", PEER_A, IDENTITY_KEY)
                self.assertEqual(len(loaded), 2)
                # Original profile name should have nothing
                loaded_alice = load_history(td2, "alice", PEER_A, IDENTITY_KEY)
                self.assertEqual(loaded_alice, [])


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ConflictStrategyTests(unittest.TestCase):
    def _setup_archive(self, td: str, entries: list, peer: str = PEER_A) -> str:
        archive = os.path.join(td, "export.i2hx")
        # Write entries to a temp dir to export from
        with tempfile.TemporaryDirectory() as src:
            save_history(src, "alice", peer, entries, IDENTITY_KEY)
            export_history("alice", IDENTITY_KEY, [peer], PASSWORD, archive, src)
        return archive

    def test_conflict_skip_keeps_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            existing = _make_entries(3, "existing")
            save_history(td, "alice", PEER_A, existing, IDENTITY_KEY)

            imported_entries = _make_entries(5, "imported")
            archive = self._setup_archive(td, imported_entries)

            results = import_history(archive, PASSWORD, IDENTITY_KEY, td, CONFLICT_SKIP)
            self.assertEqual(results[PEER_A.lower()], 0)

            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[0].text, "existing-0")

    def test_conflict_skip_imports_when_no_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            imported_entries = _make_entries(3, "imported")
            archive = self._setup_archive(td, imported_entries)

            with tempfile.TemporaryDirectory() as td2:
                results = import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_SKIP)
                self.assertEqual(results[PEER_A.lower()], 3)

    def test_conflict_replace_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            existing = _make_entries(3, "existing")
            save_history(td, "alice", PEER_A, existing, IDENTITY_KEY)

            imported_entries = _make_entries(2, "imported")
            archive = self._setup_archive(td, imported_entries)

            import_history(archive, PASSWORD, IDENTITY_KEY, td, CONFLICT_REPLACE)
            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].text, "imported-0")

    def test_conflict_merge_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # existing has entries 0-2, imported has entries 1-4 (1,2 overlap)
            existing = _make_entries(3, "m")
            save_history(td, "alice", PEER_A, existing, IDENTITY_KEY)

            imported_entries = _make_entries(5, "m")  # same message_ids for 0-2
            archive = self._setup_archive(td, imported_entries)

            import_history(archive, PASSWORD, IDENTITY_KEY, td, CONFLICT_MERGE)
            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            # Should have exactly 5 unique entries (0-4), no duplicates
            self.assertEqual(len(loaded), 5)

    def test_conflict_merge_appends_new(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            existing = [HistoryEntry(kind="me", text="old", ts="2026-01-01T00:00:01Z", message_id="mid-old")]
            save_history(td, "alice", PEER_A, existing, IDENTITY_KEY)

            imported_entries = [
                HistoryEntry(kind="peer", text="new", ts="2026-01-01T00:00:02Z", message_id="mid-new")
            ]
            archive = self._setup_archive(td, imported_entries)

            import_history(archive, PASSWORD, IDENTITY_KEY, td, CONFLICT_MERGE)
            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            self.assertEqual(len(loaded), 2)
            texts = [e.text for e in loaded]
            self.assertIn("old", texts)
            self.assertIn("new", texts)

    def test_invalid_conflict_strategy_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER_A, _make_entries(1), IDENTITY_KEY)
            archive = os.path.join(td, "export.i2hx")
            export_history("alice", IDENTITY_KEY, [PEER_A], PASSWORD, archive, td)
            with self.assertRaises(ValueError) as ctx:
                import_history(archive, PASSWORD, IDENTITY_KEY, td, "invalid")
            self.assertIn("conflict_strategy", str(ctx.exception))


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class FileFormatTests(unittest.TestCase):
    def _make_archive(self, td: str) -> str:
        save_history(td, "alice", PEER_A, _make_entries(2), IDENTITY_KEY)
        archive = os.path.join(td, "export.i2hx")
        export_history("alice", IDENTITY_KEY, [PEER_A], PASSWORD, archive, td)
        return archive

    def test_magic_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = self._make_archive(td)
            with open(archive, "rb") as f:
                magic = f.read(4)
            self.assertEqual(magic, EXPORT_MAGIC)

    def test_version_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = self._make_archive(td)
            with open(archive, "rb") as f:
                f.read(4)
                version = struct.unpack(">H", f.read(2))[0]
            self.assertEqual(version, EXPORT_VERSION)

    def test_bad_magic_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = self._make_archive(td)
            with open(archive, "r+b") as f:
                f.write(b"XXXX")
            with tempfile.TemporaryDirectory() as td2:
                with self.assertRaises(ValueError) as ctx:
                    import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertIn("magic", str(ctx.exception))

    def test_truncated_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = self._make_archive(td)
            with open(archive, "r+b") as f:
                f.truncate(10)
            with tempfile.TemporaryDirectory() as td2:
                with self.assertRaises(ValueError):
                    import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)

    def test_bad_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = self._make_archive(td)
            with open(archive, "r+b") as f:
                f.seek(4)
                f.write(struct.pack(">H", 99))
            with tempfile.TemporaryDirectory() as td2:
                with self.assertRaises(ValueError) as ctx:
                    import_history(archive, PASSWORD, IDENTITY_KEY, td2, CONFLICT_REPLACE)
                self.assertIn("version", str(ctx.exception))

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(OSError):
                import_history("/nonexistent/path.i2hx", PASSWORD, IDENTITY_KEY, td, CONFLICT_REPLACE)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class MergeEntriesTests(unittest.TestCase):
    def test_merge_no_overlap(self) -> None:
        a = [HistoryEntry(kind="me", text="a", ts="2026-01-01T00:00:01Z", message_id="a1")]
        b = [HistoryEntry(kind="peer", text="b", ts="2026-01-01T00:00:02Z", message_id="b1")]
        merged = _merge_entries(a, b)
        self.assertEqual(len(merged), 2)

    def test_merge_deduplicates_by_message_id_and_ts(self) -> None:
        entry = HistoryEntry(kind="me", text="same", ts="2026-01-01T00:00:01Z", message_id="same-id")
        merged = _merge_entries([entry], [entry])
        self.assertEqual(len(merged), 1)

    def test_merge_sorted_by_ts(self) -> None:
        a = [HistoryEntry(kind="me", text="later", ts="2026-01-01T00:00:02Z", message_id="m2")]
        b = [HistoryEntry(kind="peer", text="earlier", ts="2026-01-01T00:00:01Z", message_id="m1")]
        merged = _merge_entries(a, b)
        self.assertEqual(merged[0].text, "earlier")
        self.assertEqual(merged[1].text, "later")

    def test_merge_null_message_id_dedup_by_ts(self) -> None:
        # Entries without message_id dedup by ("", ts)
        a = [HistoryEntry(kind="me", text="x", ts="2026-01-01T00:00:01Z")]
        b = [HistoryEntry(kind="me", text="x", ts="2026-01-01T00:00:01Z")]
        merged = _merge_entries(a, b)
        self.assertEqual(len(merged), 1)


class PayloadParseTests(unittest.TestCase):
    def _valid_payload(self) -> bytes:
        obj = {
            "version": 1,
            "profile": "alice",
            "export_ts": "2026-01-01T00:00:00Z",
            "peers": [
                {
                    "addr": PEER_A,
                    "entries": [
                        {"kind": "me", "text": "hi", "ts": "2026-01-01T00:00:01Z"}
                    ],
                }
            ],
        }
        return json.dumps(obj).encode("utf-8")

    def test_valid_payload(self) -> None:
        parsed = _parse_payload(self._valid_payload())
        self.assertEqual(parsed["profile"], "alice")
        self.assertEqual(len(parsed["peers"]), 1)

    def test_wrong_version(self) -> None:
        obj = json.loads(self._valid_payload())
        obj["version"] = 99
        with self.assertRaises(ValueError) as ctx:
            _parse_payload(json.dumps(obj).encode("utf-8"))
        self.assertIn("version", str(ctx.exception))

    def test_missing_profile(self) -> None:
        obj = json.loads(self._valid_payload())
        del obj["profile"]
        with self.assertRaises(ValueError) as ctx:
            _parse_payload(json.dumps(obj).encode("utf-8"))
        self.assertIn("profile", str(ctx.exception))

    def test_missing_peers(self) -> None:
        obj = json.loads(self._valid_payload())
        del obj["peers"]
        with self.assertRaises(ValueError) as ctx:
            _parse_payload(json.dumps(obj).encode("utf-8"))
        self.assertIn("peers", str(ctx.exception))

    def test_entry_missing_required_field(self) -> None:
        obj = json.loads(self._valid_payload())
        del obj["peers"][0]["entries"][0]["kind"]
        with self.assertRaises(ValueError) as ctx:
            _parse_payload(json.dumps(obj).encode("utf-8"))
        self.assertIn("kind", str(ctx.exception))


class DictToEntryTests(unittest.TestCase):
    def test_valid_entry(self) -> None:
        d = {"kind": "me", "text": "hello", "ts": "2026-01-01T00:00:00Z"}
        entry = _dict_to_entry(d)
        self.assertEqual(entry.kind, "me")
        self.assertEqual(entry.text, "hello")

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ValueError):
            _dict_to_entry({"kind": "me", "text": "hello"})  # missing ts

    def test_optional_fields_default(self) -> None:
        d = {"kind": "peer", "text": "hi", "ts": "2026-01-01T00:00:00Z"}
        entry = _dict_to_entry(d)
        self.assertIsNone(entry.message_id)
        self.assertIsNone(entry.delivery_state)
        self.assertEqual(entry.delivery_hint, "")
        self.assertFalse(entry.retryable)


if __name__ == "__main__":
    unittest.main()
