"""
Tests for i2pchat.storage.profile_export — export_profile() and import_profile().
"""

from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import unittest


class TestExportProfile(unittest.TestCase):
    def _make_profiles_dir(self, tmp: str, profile_name: str = "alice") -> str:
        profiles_dir = os.path.join(tmp, "profiles")
        os.makedirs(profiles_dir, exist_ok=True)
        # minimal .dat (two-line format: identity key + optional locked peer)
        with open(os.path.join(profiles_dir, f"{profile_name}.dat"), "wb") as f:
            f.write(b"fake-identity-key-bytes\n")
        return profiles_dir

    def test_export_creates_file(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            out_path, warning = profile_export.export_profile("alice", "s3cr3t", profiles_dir)
            self.assertTrue(os.path.exists(out_path))
            self.assertIn("private", warning.lower())

    def test_output_path_default_naming(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            self.assertTrue(out_path.endswith("alice.i2pchat-profile"))

    def test_output_path_custom(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            dest = os.path.join(tmp, "custom.i2pchat-profile")
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir, output_path=dest)
            self.assertEqual(out_path, dest)
            self.assertTrue(os.path.exists(dest))

    def test_archive_has_correct_magic_and_version(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            with open(out_path, "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:4], b"I2CP")
            (version,) = struct.unpack(">H", raw[4:6])
            self.assertEqual(version, 1)

    def test_archive_file_permissions(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            mode = oct(os.stat(out_path).st_mode)[-3:]
            self.assertEqual(mode, "600")

    def test_raises_if_dat_missing(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = os.path.join(tmp, "profiles")
            os.makedirs(profiles_dir)
            with self.assertRaises(FileNotFoundError):
                profile_export.export_profile("nonexistent", "pw", profiles_dir)

    def test_contacts_included_when_present(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            contacts_data = {"version": 2, "contacts": []}
            with open(os.path.join(profiles_dir, "alice.contacts.json"), "w") as f:
                json.dump(contacts_data, f)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            payload = profile_export._decrypt_archive(out_path, "pw")
            self.assertEqual(payload["contacts"], contacts_data)

    def test_contacts_null_when_absent(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            payload = profile_export._decrypt_archive(out_path, "pw")
            self.assertIsNone(payload["contacts"])

    def test_gui_settings_excluded_when_flag_false(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            with open(os.path.join(profiles_dir, "gui.json"), "w") as f:
                json.dump({"theme": "dark"}, f)
            out_path, _ = profile_export.export_profile(
                "alice", "pw", profiles_dir, include_gui_settings=False
            )
            payload = profile_export._decrypt_archive(out_path, "pw")
            self.assertIsNone(payload["gui_settings"])

    def test_gui_settings_included_by_default(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            with open(os.path.join(profiles_dir, "gui.json"), "w") as f:
                json.dump({"theme": "dark"}, f)
            out_path, _ = profile_export.export_profile("alice", "pw", profiles_dir)
            payload = profile_export._decrypt_archive(out_path, "pw")
            self.assertEqual(payload["gui_settings"], {"theme": "dark"})

    def test_dat_content_round_trips(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = self._make_profiles_dir(tmp)
            original_dat = b"identity-key-data-xyz\nlocked-peer\n"
            with open(os.path.join(profiles_dir, "alice.dat"), "wb") as f:
                f.write(original_dat)
            out_path, _ = profile_export.export_profile("alice", "passw0rd", profiles_dir)
            payload = profile_export._decrypt_archive(out_path, "passw0rd")
            restored = base64.b64decode(payload["dat_content"])
            self.assertEqual(restored, original_dat)


class TestDecryptArchive(unittest.TestCase):
    def _export(self, tmp: str, password: str = "pw") -> str:
        from i2pchat.storage import profile_export

        profiles_dir = os.path.join(tmp, "profiles")
        os.makedirs(profiles_dir, exist_ok=True)
        with open(os.path.join(profiles_dir, "alice.dat"), "wb") as f:
            f.write(b"key-bytes")
        out_path, _ = profile_export.export_profile("alice", password, profiles_dir)
        return out_path

    def test_wrong_password_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            out_path = self._export(tmp, password="correct")
            with self.assertRaises(ValueError) as ctx:
                profile_export._decrypt_archive(out_path, "wrong")
            self.assertIn("password", str(ctx.exception).lower())

    def test_truncated_file_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            out_path = self._export(tmp)
            with open(out_path, "wb") as f:
                f.write(b"I2CP\x00\x01")  # only 6 bytes
            with self.assertRaises(ValueError) as ctx:
                profile_export._decrypt_archive(out_path, "pw")
            self.assertIn("short", str(ctx.exception).lower())

    def test_wrong_magic_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            out_path = self._export(tmp)
            with open(out_path, "rb") as f:
                data = f.read()
            with open(out_path, "wb") as f:
                f.write(b"XXXX" + data[4:])
            with self.assertRaises(ValueError) as ctx:
                profile_export._decrypt_archive(out_path, "pw")
            self.assertIn("magic", str(ctx.exception).lower())

    def test_unsupported_version_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            out_path = self._export(tmp)
            with open(out_path, "rb") as f:
                data = f.read()
            # Patch version bytes to 99
            patched = data[:4] + struct.pack(">H", 99) + data[6:]
            with open(out_path, "wb") as f:
                f.write(patched)
            with self.assertRaises(ValueError) as ctx:
                profile_export._decrypt_archive(out_path, "pw")
            self.assertIn("version", str(ctx.exception).lower())

    def test_corrupted_ciphertext_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            out_path = self._export(tmp)
            with open(out_path, "rb") as f:
                data = f.read()
            # Flip bytes in ciphertext region
            corrupted = data[:-10] + bytes(b ^ 0xFF for b in data[-10:])
            with open(out_path, "wb") as f:
                f.write(corrupted)
            with self.assertRaises(ValueError):
                profile_export._decrypt_archive(out_path, "pw")


class TestImportProfile(unittest.TestCase):
    def _make_archive(
        self,
        tmp: str,
        profile_name: str = "alice",
        password: str = "pw",
        dat_bytes: bytes = b"identity-key",
    ) -> str:
        from i2pchat.storage import profile_export

        profiles_dir = os.path.join(tmp, "src_profiles")
        os.makedirs(profiles_dir, exist_ok=True)
        with open(os.path.join(profiles_dir, f"{profile_name}.dat"), "wb") as f:
            f.write(dat_bytes)
        out_path, _ = profile_export.export_profile(profile_name, password, profiles_dir)
        return out_path

    def test_basic_import(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp)
            dest_dir = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest_dir)
            self.assertEqual(name, "alice")
            self.assertTrue(os.path.exists(os.path.join(dest_dir, "alice.dat")))

    def test_dat_content_restored_correctly(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            original = b"real-identity-key-data"
            archive = self._make_archive(tmp, dat_bytes=original)
            dest_dir = os.path.join(tmp, "dest")
            profile_export.import_profile(archive, "pw", dest_dir)
            with open(os.path.join(dest_dir, "alice.dat"), "rb") as f:
                self.assertEqual(f.read(), original)

    def test_conflict_strategy_error_raises(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp)
            dest_dir = os.path.join(tmp, "dest")
            os.makedirs(dest_dir)
            # Pre-create the file
            with open(os.path.join(dest_dir, "alice.dat"), "wb") as f:
                f.write(b"existing")
            with self.assertRaises(FileExistsError):
                profile_export.import_profile(archive, "pw", dest_dir, "error")

    def test_conflict_strategy_rename(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp)
            dest_dir = os.path.join(tmp, "dest")
            os.makedirs(dest_dir)
            with open(os.path.join(dest_dir, "alice.dat"), "wb") as f:
                f.write(b"existing")
            name = profile_export.import_profile(archive, "pw", dest_dir, "rename")
            self.assertEqual(name, "alice_1")
            self.assertTrue(os.path.exists(os.path.join(dest_dir, "alice_1.dat")))
            # Original untouched
            with open(os.path.join(dest_dir, "alice.dat"), "rb") as f:
                self.assertEqual(f.read(), b"existing")

    def test_conflict_strategy_overwrite(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp, dat_bytes=b"new-key")
            dest_dir = os.path.join(tmp, "dest")
            os.makedirs(dest_dir)
            with open(os.path.join(dest_dir, "alice.dat"), "wb") as f:
                f.write(b"old-key")
            name = profile_export.import_profile(archive, "pw", dest_dir, "overwrite")
            self.assertEqual(name, "alice")
            with open(os.path.join(dest_dir, "alice.dat"), "rb") as f:
                self.assertEqual(f.read(), b"new-key")

    def test_contacts_restored_when_present(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            src_profiles = os.path.join(tmp, "src_profiles")
            os.makedirs(src_profiles)
            with open(os.path.join(src_profiles, "alice.dat"), "wb") as f:
                f.write(b"key")
            contacts = {"version": 2, "contacts": [{"addr": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"}]}
            with open(os.path.join(src_profiles, "alice.contacts.json"), "w") as f:
                json.dump(contacts, f)
            out_path, _ = profile_export.export_profile("alice", "pw", src_profiles)
            dest_dir = os.path.join(tmp, "dest")
            profile_export.import_profile(out_path, "pw", dest_dir)
            contacts_path = os.path.join(dest_dir, "alice.contacts.json")
            self.assertTrue(os.path.exists(contacts_path))
            with open(contacts_path) as f:
                restored = json.load(f)
            self.assertEqual(restored, contacts)

    def test_gui_settings_not_restored_by_default(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            src_profiles = os.path.join(tmp, "src_profiles")
            os.makedirs(src_profiles)
            with open(os.path.join(src_profiles, "alice.dat"), "wb") as f:
                f.write(b"key")
            with open(os.path.join(src_profiles, "gui.json"), "w") as f:
                json.dump({"theme": "dark"}, f)
            out_path, _ = profile_export.export_profile("alice", "pw", src_profiles)
            dest_dir = os.path.join(tmp, "dest")
            profile_export.import_profile(out_path, "pw", dest_dir)
            self.assertFalse(os.path.exists(os.path.join(dest_dir, "gui.json")))

    def test_gui_settings_restored_when_requested(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            src_profiles = os.path.join(tmp, "src_profiles")
            os.makedirs(src_profiles)
            with open(os.path.join(src_profiles, "alice.dat"), "wb") as f:
                f.write(b"key")
            with open(os.path.join(src_profiles, "gui.json"), "w") as f:
                json.dump({"theme": "dark"}, f)
            out_path, _ = profile_export.export_profile("alice", "pw", src_profiles)
            dest_dir = os.path.join(tmp, "dest")
            profile_export.import_profile(out_path, "pw", dest_dir, restore_gui_settings=True)
            gui_path = os.path.join(dest_dir, "gui.json")
            self.assertTrue(os.path.exists(gui_path))
            with open(gui_path) as f:
                self.assertEqual(json.load(f), {"theme": "dark"})

    def test_wrong_password_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp, password="correct")
            dest_dir = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError):
                profile_export.import_profile(archive, "wrong", dest_dir)

    def test_missing_archive_raises_file_not_found(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                profile_export.import_profile("/nonexistent/path.i2pchat-profile", "pw", tmp)

    def test_unknown_conflict_strategy_raises(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp)
            dest_dir = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError):
                profile_export.import_profile(archive, "pw", dest_dir, "bogus")  # type: ignore[arg-type]

    def test_imported_dat_has_correct_permissions(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._make_archive(tmp)
            dest_dir = os.path.join(tmp, "dest")
            profile_export.import_profile(archive, "pw", dest_dir)
            dat_path = os.path.join(dest_dir, "alice.dat")
            mode = oct(os.stat(dat_path).st_mode)[-3:]
            self.assertEqual(mode, "600")


if __name__ == "__main__":
    unittest.main()
