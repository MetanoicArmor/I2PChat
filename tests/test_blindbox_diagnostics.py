import unittest

from i2pchat.blindbox.blindbox_diagnostics import build_blindbox_diagnostics_text


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
                "replica_endpoints": ["aa.b32.i2p:19444", "bb.b32.i2p:19444"],
                "replicas_gui_locked": False,
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
        self.assertIn("1. aa.b32.i2p:19444", text)
        self.assertIn("2. bb.b32.i2p:19444", text)
        self.assertIn("Dropped/invalid ACK total: 2", text)

    def test_diagnostics_shows_lock_line(self) -> None:
        text = build_blindbox_diagnostics_text(
            profile="p",
            selected_peer="",
            delivery={"state": "x", "secure_live": False, "has_target": False},
            blindbox={
                "enabled": True,
                "ready": False,
                "poller_running": False,
                "has_root_secret": False,
                "blind_boxes": 1,
                "replica_endpoints": ["z.b32.i2p:1"],
                "replicas_gui_locked": True,
                "privacy_profile": "high",
                "send_index": 0,
                "root_epoch": 0,
                "insecure_local_mode": False,
            },
            ack={},
        )
        self.assertIn("locked by environment", text)


if __name__ == "__main__":
    unittest.main()
