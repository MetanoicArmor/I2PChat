"""
Tests for import_profile() in i2pchat.storage.profile_export.
Focuses on: conflict strategies, field validation, error messages, atomic behaviour.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import unittest

from i2pchat.core.i2p_chat_core import get_profile_data_dir


def _dat_in_dest(dest: str, name: str) -> str:
    return os.path.join(
        get_profile_data_dir(name, create=True, app_root=dest), f"{name}.dat"
    )


def _make_archive(
    profiles_dir: str,
    profile_name: str = "alice",
    password: str = "pw",
    dat_bytes: bytes = b"identity-key",
    contacts: object = None,
    gui_settings: object = None,
) -> str:
    """Helper: build a valid .i2pchat-profile archive without going through export_profile()."""
    from nacl.pwhash import argon2id
    from nacl.secret import SecretBox
    import secrets as _secrets

    payload: dict = {
        "version": 1,
        "export_ts": "2026-01-01T00:00:00+00:00",
        "dat_content": base64.b64encode(dat_bytes).decode("ascii"),
        "contacts": contacts,
        "gui_settings": gui_settings,
    }
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    salt = _secrets.token_bytes(16)
    pw = password.encode("utf-8") if isinstance(password, str) else password
    key = argon2id.kdf(32, pw, salt, opslimit=argon2id.OPSLIMIT_MODERATE, memlimit=argon2id.MEMLIMIT_MODERATE)
    box = SecretBox(key)
    ciphertext = bytes(box.encrypt(plaintext))

    header = b"I2CP" + struct.pack(">H", 1) + salt
    archive_path = os.path.join(profiles_dir, f"{profile_name}.i2pchat-profile")
    with open(archive_path, "wb") as f:
        f.write(header + ciphertext)
    return archive_path


class TestImportConflictStrategies(unittest.TestCase):
    def test_error_strategy_raises_when_dat_exists(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            os.makedirs(dest)
            open(_dat_in_dest(dest, "alice"), "wb").close()
            with self.assertRaises(FileExistsError):
                profile_export.import_profile(archive, "pw", dest, "error")

    def test_error_strategy_succeeds_when_no_conflict(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest, "error")
            self.assertEqual(name, "alice")

    def test_rename_strategy_suffixes_on_collision(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            os.makedirs(dest)
            open(_dat_in_dest(dest, "alice"), "wb").close()
            name = profile_export.import_profile(archive, "pw", dest, "rename")
            self.assertEqual(name, "alice_1")
            self.assertTrue(os.path.exists(_dat_in_dest(dest, "alice_1")))

    def test_rename_strategy_increments_further(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            os.makedirs(dest)
            open(_dat_in_dest(dest, "alice"), "wb").close()
            open(_dat_in_dest(dest, "alice_1"), "wb").close()
            name = profile_export.import_profile(archive, "pw", dest, "rename")
            self.assertEqual(name, "alice_2")

    def test_rename_strategy_does_not_touch_original(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            os.makedirs(dest)
            sentinel = b"do-not-overwrite"
            with open(_dat_in_dest(dest, "alice"), "wb") as f:
                f.write(sentinel)
            profile_export.import_profile(archive, "pw", dest, "rename")
            with open(_dat_in_dest(dest, "alice"), "rb") as f:
                self.assertEqual(f.read(), sentinel)

    def test_overwrite_strategy_replaces_existing(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            dat = b"new-identity"
            archive = _make_archive(tmp, dat_bytes=dat)
            dest = os.path.join(tmp, "dest")
            os.makedirs(dest)
            with open(_dat_in_dest(dest, "alice"), "wb") as f:
                f.write(b"old-identity")
            name = profile_export.import_profile(archive, "pw", dest, "overwrite")
            self.assertEqual(name, "alice")
            with open(_dat_in_dest(dest, "alice"), "rb") as f:
                self.assertEqual(f.read(), dat)

    def test_unknown_strategy_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            with self.assertRaises(ValueError):
                profile_export.import_profile(archive, "pw", tmp, "bogus")  # type: ignore[arg-type]


class TestImportDataRestoration(unittest.TestCase):
    def test_dat_bytes_restored_exactly(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            original = b"line1\nline2-locked-peer\n"
            archive = _make_archive(tmp, dat_bytes=original)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            with open(_dat_in_dest(dest, name), "rb") as f:
                self.assertEqual(f.read(), original)

    def test_contacts_json_restored_when_present(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            contacts = {"version": 2, "contacts": []}
            archive = _make_archive(tmp, contacts=contacts)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            pdir = get_profile_data_dir(name, create=False, app_root=dest)
            contacts_path = os.path.join(pdir, f"{name}.contacts.json")
            self.assertTrue(os.path.exists(contacts_path))
            with open(contacts_path) as f:
                self.assertEqual(json.load(f), contacts)

    def test_contacts_json_not_written_when_null(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp, contacts=None)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            pdir = get_profile_data_dir(name, create=False, app_root=dest)
            self.assertFalse(os.path.exists(os.path.join(pdir, f"{name}.contacts.json")))

    def test_gui_settings_not_restored_by_default(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp, gui_settings={"theme": "dark"})
            dest = os.path.join(tmp, "dest")
            profile_export.import_profile(archive, "pw", dest)
            self.assertFalse(os.path.exists(os.path.join(dest, "gui.json")))

    def test_gui_settings_restored_when_flag_set(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp, gui_settings={"theme": "dark"})
            dest = os.path.join(tmp, "dest")
            profile_export.import_profile(archive, "pw", dest, restore_gui_settings=True)
            gui_path = os.path.join(dest, "gui.json")
            self.assertTrue(os.path.exists(gui_path))
            with open(gui_path) as f:
                self.assertEqual(json.load(f), {"theme": "dark"})

    def test_dat_file_permissions_are_600(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            mode = oct(os.stat(_dat_in_dest(dest, name)).st_mode)[-3:]
            self.assertEqual(mode, "600")

    def test_returned_name_matches_written_dat(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            self.assertTrue(os.path.exists(_dat_in_dest(dest, name)))


class TestImportErrorMessages(unittest.TestCase):
    def test_wrong_password_mentions_password(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp, password="correct")
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(archive, "wrong", dest)
            self.assertIn("password", str(ctx.exception).lower())

    def test_missing_archive_raises_file_not_found(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                profile_export.import_profile("/no/such/file.i2pchat-profile", "pw", tmp)

    def test_truncated_archive_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.i2pchat-profile")
            with open(path, "wb") as f:
                f.write(b"I2CP\x00\x01")  # too short
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(path, "pw", dest)
            self.assertIn("short", str(ctx.exception).lower())

    def test_wrong_magic_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            with open(archive, "rb") as f:
                data = f.read()
            with open(archive, "wb") as f:
                f.write(b"XXXX" + data[4:])
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(archive, "pw", dest)
            self.assertIn("magic", str(ctx.exception).lower())

    def test_version_mismatch_raises_value_error(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            archive = _make_archive(tmp)
            with open(archive, "rb") as f:
                data = f.read()
            patched = data[:4] + struct.pack(">H", 99) + data[6:]
            with open(archive, "wb") as f:
                f.write(patched)
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(archive, "pw", dest)
            self.assertIn("version", str(ctx.exception).lower())

    def test_missing_dat_content_field_raises(self) -> None:
        """Archive with payload missing dat_content should raise on validation."""
        from i2pchat.storage import profile_export
        from nacl.pwhash import argon2id
        from nacl.secret import SecretBox
        import secrets as _secrets

        with tempfile.TemporaryDirectory() as tmp:
            payload = {"version": 1, "export_ts": "2026-01-01T00:00:00+00:00"}  # missing dat_content
            plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            salt = _secrets.token_bytes(16)
            key = argon2id.kdf(32, b"pw", salt, opslimit=argon2id.OPSLIMIT_MODERATE, memlimit=argon2id.MEMLIMIT_MODERATE)
            box = SecretBox(key)
            ciphertext = bytes(box.encrypt(plaintext))
            archive_path = os.path.join(tmp, "bad.i2pchat-profile")
            with open(archive_path, "wb") as f:
                f.write(b"I2CP" + struct.pack(">H", 1) + salt + ciphertext)
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(archive_path, "pw", dest)
            self.assertIn("dat_content", str(ctx.exception))

    def test_invalid_base64_dat_content_raises(self) -> None:
        from i2pchat.storage import profile_export
        from nacl.pwhash import argon2id
        from nacl.secret import SecretBox
        import secrets as _secrets

        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "version": 1,
                "export_ts": "2026-01-01T00:00:00+00:00",
                "dat_content": "!!!not-base64!!!",
                "contacts": None,
                "gui_settings": None,
            }
            plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            salt = _secrets.token_bytes(16)
            key = argon2id.kdf(32, b"pw", salt, opslimit=argon2id.OPSLIMIT_MODERATE, memlimit=argon2id.MEMLIMIT_MODERATE)
            box = SecretBox(key)
            ciphertext = bytes(box.encrypt(plaintext))
            archive_path = os.path.join(tmp, "bad.i2pchat-profile")
            with open(archive_path, "wb") as f:
                f.write(b"I2CP" + struct.pack(">H", 1) + salt + ciphertext)
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError) as ctx:
                profile_export.import_profile(archive_path, "pw", dest)
            self.assertIn("base64", str(ctx.exception).lower())


class TestImportIsImmediatelyUsable(unittest.TestCase):
    """Imported profile must be readable without extra repair."""

    def test_imported_profile_dat_is_readable(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            original = b"identity-key-data\nlocked-peer\n"
            archive = _make_archive(tmp, dat_bytes=original)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            dat_path = _dat_in_dest(dest, name)
            with open(dat_path, "rb") as f:
                content = f.read()
            self.assertEqual(content, original)

    def test_imported_contacts_is_valid_json(self) -> None:
        from i2pchat.storage import profile_export

        with tempfile.TemporaryDirectory() as tmp:
            contacts = {"version": 2, "contacts": []}
            archive = _make_archive(tmp, contacts=contacts)
            dest = os.path.join(tmp, "dest")
            name = profile_export.import_profile(archive, "pw", dest)
            pdir = get_profile_data_dir(name, create=False, app_root=dest)
            contacts_path = os.path.join(pdir, f"{name}.contacts.json")
            with open(contacts_path) as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)


if __name__ == "__main__":
    unittest.main()
