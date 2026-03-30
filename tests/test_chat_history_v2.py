import secrets
import tempfile
import unittest

import crypto
from chat_history import HistoryEntry, load_history, save_history


IDENTITY_KEY = secrets.token_bytes(32)
PEER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ChatHistoryV2Tests(unittest.TestCase):
    def test_save_load_preserves_delivery_metadata(self) -> None:
        entry = HistoryEntry(
            kind="me",
            text="hello",
            ts="2026-03-30T10:00:00Z",
            message_id="42",
            delivery_state="queued",
            delivery_route="offline-queued",
            delivery_hint="Message queued for offline delivery.",
            delivery_reason="blindbox-ready",
            retryable=False,
        )
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER, [entry], IDENTITY_KEY)
            loaded = load_history(td, "alice", PEER, IDENTITY_KEY)
        self.assertEqual(len(loaded), 1)
        got = loaded[0]
        self.assertEqual(got.message_id, "42")
        self.assertEqual(got.delivery_state, "queued")
        self.assertEqual(got.delivery_route, "offline-queued")
        self.assertEqual(got.delivery_reason, "blindbox-ready")


if __name__ == "__main__":
    unittest.main()
