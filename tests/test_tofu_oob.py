"""TOFU full fingerprint / safety number / OOB verification."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from i2pchat.core.i2p_chat_core import I2PChatCore

PEER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class TofuOobTests(unittest.IsolatedAsyncioTestCase):
    def test_fingerprint_is_full_sha256_hex(self) -> None:
        fp = I2PChatCore._fingerprint_pubkey(b"\x11" * 32)
        self.assertEqual(len(fp), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))
        self.assertEqual(I2PChatCore._fingerprint_pubkey_short(b"\x11" * 32), fp[:16])

    def test_format_fingerprint_grouped(self) -> None:
        grouped = I2PChatCore.format_fingerprint_grouped("aabbccdd11223344")
        self.assertEqual(grouped, "aabb ccdd 1122 3344")

    def test_safety_number_deterministic_and_symmetric(self) -> None:
        a = b"\x01" * 32
        b = b"\x02" * 32
        sn1 = I2PChatCore.format_safety_number(a, b)
        sn2 = I2PChatCore.format_safety_number(b, a)
        self.assertEqual(sn1, sn2)
        lines = sn1.splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            parts = line.split()
            self.assertEqual(len(parts), 4)
            for p in parts:
                self.assertEqual(len(p), 5)
                self.assertTrue(p.isdigit())

    def test_trust_store_v2_roundtrip_with_oob_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="alice", on_error=lambda _m: None)
                core.peer_trusted_signing_keys[PEER] = "ab" * 32
                core.peer_oob_verified[PEER] = True
                core._save_trust_store()
                path = core._trust_store_path()
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["version"], 2)
                self.assertTrue(data["pins"][PEER]["oob_verified"])

                core2 = I2PChatCore(profile="alice", on_error=lambda _m: None)
                core2._load_trust_store()
                self.assertEqual(core2.peer_trusted_signing_keys[PEER], "ab" * 32)
                self.assertTrue(core2.peer_oob_verified[PEER])
                info = core2.get_peer_trust_info(PEER)
                assert info is not None
                self.assertTrue(info.oob_verified)
                self.assertEqual(len(info.fingerprint_full or ""), 64)
                self.assertEqual(info.fingerprint_short, (info.fingerprint_full or "")[:16])

    def test_legacy_v1_trust_store_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=td):
                core = I2PChatCore(profile="bob", on_error=lambda _m: None)
                path = core._trust_store_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({PEER: "cd" * 32}, f)
                core._load_trust_store()
                self.assertEqual(core.peer_trusted_signing_keys[PEER], "cd" * 32)
                self.assertFalse(core.peer_oob_verified.get(PEER, False))

    async def test_ui_approval_marks_oob_verified(self) -> None:
        async def approve(peer: str, fp: str, key: str) -> bool:
            self.assertEqual(len(fp), 64)  # full fingerprint passed to UI
            return True

        core = I2PChatCore(
            profile="default",
            on_trust_decision=approve,
            on_error=lambda _m: None,
        )
        ok = await core._pin_or_verify_peer_signing_key(PEER, b"\x44" * 32)
        self.assertTrue(ok)
        self.assertTrue(core.peer_oob_verified.get(PEER))

    async def test_auto_pin_is_not_oob_verified(self) -> None:
        with patch.dict(os.environ, {"I2PCHAT_TRUST_AUTO": "1"}, clear=False):
            core = I2PChatCore(profile="default", on_error=lambda _m: None)
            ok = await core._pin_or_verify_peer_signing_key(PEER, b"\x55" * 32)
        self.assertTrue(ok)
        self.assertFalse(core.peer_oob_verified.get(PEER, False))


if __name__ == "__main__":
    unittest.main()
