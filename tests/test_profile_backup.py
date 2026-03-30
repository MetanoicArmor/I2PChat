import os
import secrets
import tempfile
import unittest

from i2pchat import crypto
from i2pchat.storage.chat_history import HistoryEntry, load_history, save_history
from i2pchat.storage.contact_book import save_book, ContactBook, ContactRecord
from i2pchat.storage.profile_backup import (
    BackupError,
    export_history_bundle,
    export_profile_bundle,
    import_history_bundle,
    import_profile_bundle,
)


IDENTITY_KEY = secrets.token_bytes(32)
PEER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ProfileBackupTests(unittest.TestCase):
    def _seed_profile(self, profiles_dir: str, profile: str) -> None:
        with open(os.path.join(profiles_dir, f"{profile}.dat"), "wb") as f:
            f.write(b"profile-secret")
        save_book(
            os.path.join(profiles_dir, f"{profile}.contacts.json"),
            ContactBook(
                contacts=[ContactRecord(addr=PEER, display_name="Alice", note="trusted")],
                last_active_peer=PEER,
            ),
        )
        save_history(
            profiles_dir,
            profile,
            PEER,
            [HistoryEntry(kind="me", text="hello", ts="2026-03-30T10:00:00Z")],
            IDENTITY_KEY,
        )

    def test_profile_backup_roundtrip_restores_sidecars_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as src_td, tempfile.TemporaryDirectory() as dst_td:
            self._seed_profile(src_td, "alice")
            bundle_path = os.path.join(src_td, "alice.i2pchat-profile-backup")
            summary = export_profile_bundle(bundle_path, src_td, "alice", "passphrase", include_history=True)
            self.assertEqual(summary.bundle_type, "profile")
            self.assertGreaterEqual(summary.file_count, 3)

            imported = import_profile_bundle(bundle_path, dst_td, "passphrase")
            self.assertEqual(imported.target_profile, "alice")
            self.assertTrue(os.path.isfile(os.path.join(dst_td, "alice.dat")))
            self.assertTrue(os.path.isfile(os.path.join(dst_td, "alice.contacts.json")))
            loaded = load_history(dst_td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual([x.text for x in loaded], ["hello"])

    def test_history_backup_roundtrip_skip_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as src_td, tempfile.TemporaryDirectory() as dst_td:
            self._seed_profile(src_td, "alice")
            self._seed_profile(dst_td, "alice")
            save_history(
                dst_td,
                "alice",
                PEER,
                [HistoryEntry(kind="me", text="existing", ts="2026-03-30T12:00:00Z")],
                IDENTITY_KEY,
            )
            bundle_path = os.path.join(src_td, "alice.i2pchat-history-backup")
            export_history_bundle(bundle_path, src_td, "alice", "passphrase")

            imported = import_history_bundle(
                bundle_path,
                dst_td,
                "alice",
                "passphrase",
                conflict_mode="skip",
            )
            self.assertEqual(imported.skipped_files, 1)
            loaded = load_history(dst_td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual([x.text for x in loaded], ["existing"])

    def test_history_backup_roundtrip_overwrite_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as src_td, tempfile.TemporaryDirectory() as dst_td:
            self._seed_profile(src_td, "alice")
            self._seed_profile(dst_td, "alice")
            save_history(
                dst_td,
                "alice",
                PEER,
                [HistoryEntry(kind="me", text="existing", ts="2026-03-30T12:00:00Z")],
                IDENTITY_KEY,
            )
            bundle_path = os.path.join(src_td, "alice.i2pchat-history-backup")
            export_history_bundle(bundle_path, src_td, "alice", "passphrase")

            imported = import_history_bundle(
                bundle_path,
                dst_td,
                "alice",
                "passphrase",
                conflict_mode="overwrite",
            )
            self.assertEqual(imported.skipped_files, 0)
            loaded = load_history(dst_td, "alice", PEER, IDENTITY_KEY)
            self.assertEqual([x.text for x in loaded], ["hello"])

    def test_wrong_passphrase_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as src_td, tempfile.TemporaryDirectory() as dst_td:
            self._seed_profile(src_td, "alice")
            bundle_path = os.path.join(src_td, "alice.i2pchat-profile-backup")
            export_profile_bundle(bundle_path, src_td, "alice", "passphrase", include_history=True)
            with self.assertRaises(BackupError):
                import_profile_bundle(bundle_path, dst_td, "wrong-passphrase")


if __name__ == "__main__":
    unittest.main()
