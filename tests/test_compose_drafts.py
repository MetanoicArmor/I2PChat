"""Unit tests for compose draft peer switching (issue #6)."""

import unittest

from i2pchat.presentation.compose_drafts import apply_compose_draft_peer_switch


class ApplyComposeDraftPeerSwitchTests(unittest.TestCase):
    def test_noop_same_key(self) -> None:
        d = {"a.b32.i2p": "x"}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key="a.b32.i2p",
            new_key="a.b32.i2p",
            input_plain="hello",
            drafts=d,
        )
        self.assertEqual(ak, "a.b32.i2p")
        self.assertEqual(text, "hello")
        self.assertIs(out, d)

    def test_save_old_load_new(self) -> None:
        d = {"b.b32.i2p": "draft-b"}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key="a.b32.i2p",
            new_key="b.b32.i2p",
            input_plain="typing-a",
            drafts=d,
        )
        self.assertEqual(ak, "b.b32.i2p")
        self.assertEqual(text, "draft-b")
        self.assertEqual(out["a.b32.i2p"], "typing-a")
        self.assertEqual(out["b.b32.i2p"], "draft-b")

    def test_orphan_text_when_gaining_first_key(self) -> None:
        d: dict[str, str] = {}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key=None,
            new_key="peer.b32.i2p",
            input_plain="  typed before peer  ",
            drafts=d,
        )
        self.assertEqual(ak, "peer.b32.i2p")
        self.assertEqual(text, "  typed before peer  ")
        self.assertEqual(out, {})

    def test_saved_draft_wins_over_orphan(self) -> None:
        d = {"p.b32.i2p": "from-disk"}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key=None,
            new_key="p.b32.i2p",
            input_plain="orphan",
            drafts=d,
        )
        self.assertEqual(ak, "p.b32.i2p")
        self.assertEqual(text, "from-disk")
        self.assertEqual(out, d)

    def test_whitespace_only_saved_uses_orphan(self) -> None:
        d = {"p.b32.i2p": "   \n"}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key=None,
            new_key="p.b32.i2p",
            input_plain="real",
            drafts=d,
        )
        self.assertEqual(text, "real")

    def test_switch_to_none_clears(self) -> None:
        d = {"a.b32.i2p": "keep"}
        ak, text, out = apply_compose_draft_peer_switch(
            old_active_key="a.b32.i2p",
            new_key=None,
            input_plain="last",
            drafts=d,
        )
        self.assertIsNone(ak)
        self.assertEqual(text, "")
        self.assertEqual(out["a.b32.i2p"], "last")


class ComposeDraftsAtRestTests(unittest.TestCase):
    def test_encrypted_roundtrip_hides_draft_text(self) -> None:
        from pathlib import Path
        import tempfile

        from i2pchat.storage.compose_drafts_store import (
            COMPOSE_DRAFTS_MAGIC,
            load_compose_drafts,
            save_compose_drafts,
        )

        identity_key = b"\x24" * 32
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "p.compose_drafts.json")
            save_compose_drafts(
                path,
                {"peer-a": "unsent secret"},
                identity_key=identity_key,
            )
            raw = Path(path).read_bytes()
            self.assertTrue(raw.startswith(COMPOSE_DRAFTS_MAGIC))
            self.assertNotIn(b"unsent secret", raw)
            loaded = load_compose_drafts(path, identity_key=identity_key)
            self.assertEqual(loaded["peer-a"], "unsent secret")
            self.assertEqual(load_compose_drafts(path), {})

    def test_plaintext_migrates_on_encrypted_save(self) -> None:
        from pathlib import Path
        import tempfile

        from i2pchat.storage.compose_drafts_store import (
            COMPOSE_DRAFTS_MAGIC,
            load_compose_drafts,
            save_compose_drafts,
        )

        identity_key = b"\x33" * 32
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "p.compose_drafts.json")
            Path(path).write_text(
                '{"version": 1, "drafts": {"peer-a": "legacy draft"}}',
                encoding="utf-8",
            )
            loaded = load_compose_drafts(path)
            self.assertEqual(loaded["peer-a"], "legacy draft")
            save_compose_drafts(path, loaded, identity_key=identity_key)
            raw = Path(path).read_bytes()
            self.assertTrue(raw.startswith(COMPOSE_DRAFTS_MAGIC))
            self.assertNotIn(b"legacy draft", raw)
            self.assertEqual(
                load_compose_drafts(path, identity_key=identity_key)["peer-a"],
                "legacy draft",
            )

    def test_save_without_key_does_not_overwrite_encrypted(self) -> None:
        from pathlib import Path
        import tempfile

        from i2pchat.storage.compose_drafts_store import (
            load_compose_drafts,
            save_compose_drafts,
        )

        identity_key = b"\x24" * 32
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "p.compose_drafts.json")
            save_compose_drafts(
                path, {"peer-a": "keep me"}, identity_key=identity_key
            )
            raw = Path(path).read_bytes()
            save_compose_drafts(path, {"peer-a": "wipe"})
            self.assertEqual(Path(path).read_bytes(), raw)
            self.assertEqual(
                load_compose_drafts(path, identity_key=identity_key)["peer-a"],
                "keep me",
            )


if __name__ == "__main__":
    unittest.main()
