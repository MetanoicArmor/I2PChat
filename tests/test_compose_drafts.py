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


if __name__ == "__main__":
    unittest.main()
