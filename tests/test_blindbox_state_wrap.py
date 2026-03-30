import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat import crypto
import i2pchat.core.i2p_chat_core as core_module
from i2pchat.storage.blindbox_state import BLINDBOX_STATE_V1
from i2pchat.core.i2p_chat_core import (
    BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
    BLINDBOX_LOCAL_WRAP_VERSION_LEGACY,
    I2PChatCore,
)


class BlindBoxStateWrapTests(unittest.TestCase):
    def test_local_wrap_key_depends_on_signing_seed(self) -> None:
        core_a = I2PChatCore(profile="default")
        core_b = I2PChatCore(profile="default")
        core_a.my_signing_seed = b"A" * 32
        core_b.my_signing_seed = b"B" * 32

        key_a = core_a._blindbox_local_wrap_key("peer-1")
        key_b = core_b._blindbox_local_wrap_key("peer-1")

        self.assertNotEqual(key_a, key_b)

    def test_legacy_blindbox_state_is_migrated_to_wrap_v2(self) -> None:
        if not crypto.NACL_AVAILABLE:
            self.skipTest("PyNaCl is required for BlindBox state encryption migration test")
        original_get_profiles_dir = core_module.get_profiles_dir
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "peer-box.b32.i2p"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                core_module.get_profiles_dir = lambda: tmp_dir  # type: ignore[assignment]
                bootstrap = I2PChatCore(profile="alice")
                bootstrap.my_signing_seed = b"C" * 32
                bootstrap.stored_peer = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
                peer_id = bootstrap._blindbox_peer_id()
                assert peer_id is not None
                path = bootstrap._blindbox_state_path()

                root_secret = bytes(range(32))
                prev_secret = bytes(range(32, 64))
                legacy_root = crypto.encrypt_message(
                    bootstrap._blindbox_local_wrap_key(
                        peer_id, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
                    ),
                    root_secret,
                ).hex()
                legacy_prev = crypto.encrypt_message(
                    bootstrap._blindbox_local_wrap_key(
                        peer_id, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
                    ),
                    prev_secret,
                ).hex()

                with open(path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "version": BLINDBOX_STATE_V1,
                            "send_index": 7,
                            "recv_base": 0,
                            "recv_window": 16,
                            "consumed_recv": [],
                            "updated_at": 1700000000,
                            "blindbox_root_secret_enc": legacy_root,
                            "blindbox_root_epoch": 3,
                            "blindbox_root_created_at": 1700000000,
                            "blindbox_root_send_index_base": 4,
                            "blindbox_prev_roots": [
                                {
                                    "epoch": 2,
                                    "expires_at": 4102444800,
                                    "secret_enc": legacy_prev,
                                }
                            ],
                        },
                        f,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )

                migrated = I2PChatCore(profile="alice")
                migrated.my_signing_seed = b"C" * 32
                migrated.stored_peer = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
                migrated._load_blindbox_state()

                self.assertEqual(migrated._blindbox_root_secret, root_secret)
                self.assertEqual(len(migrated._blindbox_prev_roots), 1)
                self.assertEqual(migrated._blindbox_prev_roots[0]["secret"], prev_secret)

                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(
                    int(saved["blindbox_wrap_version"]),
                    BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
                )
                self.assertNotEqual(saved["blindbox_root_secret_enc"], legacy_root)
        finally:
            core_module.get_profiles_dir = original_get_profiles_dir  # type: ignore[assignment]
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas

    def test_load_blindbox_state_reads_single_snapshot(self) -> None:
        original_get_profiles_dir = core_module.get_profiles_dir
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "peer-box.b32.i2p"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                core_module.get_profiles_dir = lambda: tmp_dir  # type: ignore[assignment]
                core = I2PChatCore(profile="alice")
                core.stored_peer = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
                path = core._blindbox_state_path()
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "version": BLINDBOX_STATE_V1,
                            "send_index": 2,
                            "recv_base": 1,
                            "recv_window": 16,
                            "consumed_recv": [],
                            "updated_at": 1700000000,
                            "blindbox_wrap_version": BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
                            "blindbox_root_epoch": 0,
                        },
                        f,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )

                with patch("i2pchat.core.i2p_chat_core.open", wraps=open) as mock_open:
                    core._load_blindbox_state()
                load_calls = [
                    c
                    for c in mock_open.call_args_list
                    if c.args and c.args[0] == path and "r" in str(c.args[1])
                ]
                self.assertEqual(len(load_calls), 1)
        finally:
            core_module.get_profiles_dir = original_get_profiles_dir  # type: ignore[assignment]
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas


if __name__ == "__main__":
    unittest.main()
