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
                core.peer_trusted_signing_keys["peer.b32.i2p"] = "a" * 64
                core._save_trust_store()  # noqa: SLF001 - internal persistence behavior
                trust_path = os.path.join(td, "alice.trust.json")
                self.assertTrue(os.path.exists(trust_path))
                self.assertFalse(os.path.exists(trust_path + ".tmp"))

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


if __name__ == "__main__":
    unittest.main()
