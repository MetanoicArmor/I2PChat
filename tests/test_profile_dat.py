"""Encrypted at-rest profile .dat + plaintext migration."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.storage.profile_dat import (
    PROFILE_DAT_MAGIC,
    decrypt_profile_dat,
    encrypt_profile_dat,
    get_or_create_dat_wrap_key,
    is_encrypted_profile_dat,
    plaintext_key_bytes_for_backup,
    read_profile_dat_file,
    write_encrypted_profile_dat,
)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl is required")
class ProfileDatEncryptionTests(unittest.TestCase):
    def test_roundtrip_encrypt_decrypt(self) -> None:
        wrap = b"\x42" * 32
        # Plain fixture string (not a real key); avoid base64 blobs that trip secret scanners.
        key_b64 = "test-private-key-line-fixture"
        blob = encrypt_profile_dat(key_b64, wrap)
        self.assertTrue(is_encrypted_profile_dat(blob))
        self.assertEqual(blob[:4], PROFILE_DAT_MAGIC)
        self.assertNotIn(key_b64.encode("ascii"), blob)
        self.assertEqual(decrypt_profile_dat(blob, wrap), key_b64)

    def test_wrong_wrap_key_fails(self) -> None:
        blob = encrypt_profile_dat("abc123", b"\x01" * 32)
        with self.assertRaises(ValueError):
            decrypt_profile_dat(blob, b"\x02" * 32)

    def test_write_and_read_migrates_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "alice.dat")
            plaintext_key = "plain-legacy-key-base64value"
            with open(path, "w", encoding="utf-8") as f:
                f.write(plaintext_key + "\n")
            key, peer, was_plain = read_profile_dat_file(
                path, profile="alice", profile_data_dir=td
            )
            self.assertEqual(key, plaintext_key)
            self.assertIsNone(peer)
            self.assertTrue(was_plain)
            write_encrypted_profile_dat(
                path, key or "", profile="alice", profile_data_dir=td
            )
            with open(path, "rb") as f:
                raw = f.read()
            self.assertTrue(is_encrypted_profile_dat(raw))
            self.assertNotIn(plaintext_key.encode("utf-8"), raw)
            key2, peer2, was_plain2 = read_profile_dat_file(
                path, profile="alice", profile_data_dir=td
            )
            self.assertEqual(key2, plaintext_key)
            self.assertIsNone(peer2)
            self.assertFalse(was_plain2)
            self.assertTrue(os.path.isfile(os.path.join(td, "alice.dat.wrap")))

    def test_backup_export_returns_portable_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bob.dat")
            key = "backup-portable-key"
            write_encrypted_profile_dat(path, key, profile="bob", profile_data_dir=td)
            out = plaintext_key_bytes_for_backup(
                path, profile="bob", profile_data_dir=td
            )
            self.assertEqual(out, (key + "\n").encode("utf-8"))

    def test_core_write_profile_dat_is_encrypted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="carol", on_error=lambda _m: None)
                core._write_profile_dat("core-written-key", None)
                path = core._profile_path()
                with open(path, "rb") as f:
                    raw = f.read()
                self.assertTrue(is_encrypted_profile_dat(raw))
                self.assertNotIn(b"core-written-key", raw)

    def test_wrap_key_stable_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            a = get_or_create_dat_wrap_key("dave", td)
            b = get_or_create_dat_wrap_key("dave", td)
            self.assertEqual(a, b)
            self.assertEqual(len(a), 32)


if __name__ == "__main__":
    unittest.main()
