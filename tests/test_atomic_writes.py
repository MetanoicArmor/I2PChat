import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from blindbox_state import BlindBoxState, save_blindbox_state

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2p_chat_core import I2PChatCore
import crypto

VALID_PEER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


class AtomicWritesTests(unittest.TestCase):
    def test_blindbox_state_save_does_not_use_fixed_tmp_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            save_blindbox_state(state_path, BlindBoxState(send_index=1))
            self.assertTrue(os.path.exists(state_path))
            self.assertFalse(os.path.exists(state_path + ".tmp"))

    def test_trust_store_save_does_not_use_fixed_tmp_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="alice")
                core.peer_trusted_signing_keys[VALID_PEER] = "a" * 64
                core._save_trust_store()  # noqa: SLF001 - internal persistence behavior
                trust_path = os.path.join(td, "alice.trust.json")
                self.assertTrue(os.path.exists(trust_path))
                self.assertFalse(os.path.exists(trust_path + ".tmp"))

    def test_profile_dat_write_does_not_use_fixed_tmp_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="alice")
                core._write_profile_dat("priv-key", VALID_PEER)  # noqa: SLF001
                profile_path = os.path.join(td, "alice.dat")
                self.assertTrue(os.path.exists(profile_path))
                self.assertFalse(os.path.exists(profile_path + ".tmp"))

    def test_blindbox_state_save_is_single_pass_json_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {
                    "I2PCHAT_BLINDBOX_ENABLED": "1",
                    "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
                },
                clear=False,
            ):
                with patch("i2p_chat_core.get_profiles_dir", return_value=td):
                    core = I2PChatCore(profile="alice")
                    core.stored_peer = VALID_PEER
                    core.current_peer_addr = VALID_PEER
                    core.my_dest = types.SimpleNamespace(
                        base32="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
                    )
                    core._blindbox_root_secret = b"\x11" * 32
                    core._blindbox_root_epoch = 2
                    core._blindbox_root_created_at = 123
                    core._blindbox_root_send_index_base = 7
                    core._blindbox_pending_root_secret = b"\x22" * 32
                    core._blindbox_pending_root_epoch = 3
                    core._blindbox_pending_root_created_at = 456
                    core._blindbox_pending_root_send_index_base = 8
                    core._blindbox_prev_roots = [
                        {
                            "epoch": 1,
                            "secret": b"\x33" * 32,
                            "expires_at": 9999999999,
                        }
                    ]
                    with patch.object(
                        core,
                        "_blindbox_encrypt_root_secret",
                        side_effect=lambda secret, peer_id: f"enc-{secret.hex()}-{peer_id}",
                    ):
                        with patch("i2p_chat_core.atomic_write_json") as mock_write:
                            core._save_blindbox_state()  # noqa: SLF001

                    mock_write.assert_called_once()
                    payload = mock_write.call_args.args[1]
                    self.assertEqual(payload["blindbox_root_epoch"], 2)
                    self.assertEqual(payload["blindbox_pending_root_epoch"], 3)
                    self.assertEqual(len(payload["blindbox_prev_roots"]), 1)

    def test_profile_dat_fault_before_replace_keeps_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="alice")
                profile_path = os.path.join(td, "alice.dat")
                with open(profile_path, "w", encoding="utf-8") as f:
                    f.write("old-key\n")

                with patch("blindbox_state.os.replace", side_effect=OSError("boom")):
                    with self.assertRaises(OSError):
                        core._write_profile_dat("new-key", VALID_PEER)  # noqa: SLF001

                with open(profile_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), "old-key\n")

    def test_target_files_no_longer_build_path_plus_dot_tmp(self) -> None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        targets = [
            os.path.join(base, "blindbox_state.py"),
            os.path.join(base, "i2p_chat_core.py"),
            os.path.join(base, "main_qt.py"),
        ]
        for path in targets:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn('path + ".tmp"', content)

    def test_signing_seed_fallback_write_uses_atomic_write_text(self) -> None:
        if not crypto.NACL_AVAILABLE:
            self.skipTest("PyNaCl is required for signing seed persistence test")
        with tempfile.TemporaryDirectory() as td:
            with patch("i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="alice")
                with patch("i2p_chat_core._try_keyring_get", return_value=""):
                    with patch("i2p_chat_core._try_keyring_set", return_value=False):
                        with patch(
                            "i2p_chat_core.crypto.generate_signing_keypair",
                            return_value=(b"A" * 32, b"B" * 32),
                        ):
                            with patch("i2p_chat_core.atomic_write_text") as mock_write:
                                core._ensure_local_signing_key()  # noqa: SLF001
                mock_write.assert_called_once()
                self.assertTrue(mock_write.call_args.args[0].endswith("alice.signing"))
                self.assertEqual(mock_write.call_args.args[1], (b"A" * 32).hex())


if __name__ == "__main__":
    unittest.main()
