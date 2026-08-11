"""I2PChatCore.clear_locked_peer — снятие Lock без пустого save_stored_peer."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from i2pchat.core.transient_profile import TRANSIENT_PROFILE_NAME
from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.storage.profile_dat import (
    is_encrypted_profile_dat,
    read_profile_dat_file,
)

PEER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
# First line of .dat (mock identity blob); avoid *KEY* name — gitleaks generic-api-key false positive.
MOCK_DAT_LINE1 = "dGVzdC1wcml2YXRlLWtleS1saW5lLWJhc2U2NAo="


class ClearLockedPeerTests(unittest.TestCase):
    def test_clears_second_line_leaves_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dat = os.path.join(td, "profiles", "p", "p.dat")
            os.makedirs(os.path.dirname(dat), exist_ok=True)
            with open(dat, "w", encoding="utf-8") as f:
                f.write(f"{MOCK_DAT_LINE1}\n{PEER}\n")
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="p", on_error=lambda _m: None)
                core.stored_peer = PEER
                core.my_dest = None
                core.clear_locked_peer()
            self.assertIsNone(core.stored_peer)
            with open(dat, "rb") as f:
                raw = f.read()
            self.assertTrue(is_encrypted_profile_dat(raw))
            key, legacy_peer, was_plain = read_profile_dat_file(
                dat,
                profile="p",
                profile_data_dir=os.path.dirname(dat),
            )
            self.assertEqual(key, MOCK_DAT_LINE1)
            self.assertIsNone(legacy_peer)
            self.assertFalse(was_plain)

    def test_keyring_only_peer_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dat = os.path.join(td, "profiles", "q", "q.dat")
            os.makedirs(os.path.dirname(dat), exist_ok=True)
            with open(dat, "w", encoding="utf-8") as f:
                f.write(f"{PEER}\n")
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="q", on_error=lambda _m: None)
                core.stored_peer = PEER
                core.my_dest = None
                core.clear_locked_peer()
            self.assertIsNone(core.stored_peer)
            self.assertFalse(os.path.isfile(dat))

    def test_transient_profile_clear_locked_peer_no_op(self) -> None:
        core = I2PChatCore(profile="default", on_error=lambda _m: None)
        self.assertEqual(core.profile, TRANSIENT_PROFILE_NAME)
        core.stored_peer = PEER
        core.clear_locked_peer()
        self.assertEqual(core.stored_peer, PEER)


if __name__ == "__main__":
    unittest.main()
