import unittest

from blindbox_diagnostics import build_blindbox_diagnostics_text


class BlindBoxDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_text_includes_core_sections(self) -> None:
        text = build_blindbox_diagnostics_text(
            profile="alice",
            selected_peer="peer.b32.i2p",
            delivery={
                "state": "offline-ready",
                "secure_live": False,
                "has_target": True,
            },
            blindbox={
                "enabled": True,
                "ready": True,
                "poller_running": True,
                "has_root_secret": True,
                "blind_boxes": 2,
                "privacy_profile": "balanced",
                "send_index": 7,
                "root_epoch": 3,
                "insecure_local_mode": False,
            },
            ack={"unknown_id": 2},
        )
        self.assertIn("Profile: alice", text)
        self.assertIn("State: offline-ready", text)
        self.assertIn("Blind Boxes configured: 2", text)
        self.assertIn("Dropped/invalid ACK total: 2", text)


if __name__ == "__main__":
    unittest.main()
