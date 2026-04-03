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
                "replicas_source": "release-builtin",
                "use_sam_for_replicas": True,
                "local_auth_token_enabled": False,
                "has_loopback_replicas": False,
                "put_quorum": 1,
                "get_quorum": 1,
                "recv_base": 0,
                "recv_window": 16,
                "cover_gets": 2,
                "padding_bucket": 256,
                "privacy_profile": "balanced",
                "send_index": 7,
                "root_epoch": 3,
                "insecure_local_mode": False,
            },
            ack={"unknown_id": 2},
        )
        self.assertIn("Profile", text)
        self.assertIn("- Name: alice", text)
        self.assertIn("Status", text)
        self.assertIn("Offline queue is ready", text)
        self.assertIn("What you can do now", text)
        self.assertIn("Replica source: release defaults", text)
        self.assertIn("Count: 2", text)
        self.assertIn("1. aa.b32.i2p:19444", text)
        self.assertIn("2. bb.b32.i2p:19444", text)
        self.assertIn("ACK issues: 2", text)

    def test_diagnostics_shows_lock_line(self) -> None:
        text = build_blindbox_diagnostics_text(
            profile="p",
            selected_peer="",
            delivery={"state": "x", "secure_live": False, "has_target": False, "stored_peer": False},
            blindbox={
                "enabled": True,
                "ready": False,
                "poller_running": False,
                "has_root_secret": False,
                "blind_boxes": 1,
                "replica_endpoints": ["z.b32.i2p:1"],
                "replicas_gui_locked": True,
                "replicas_source": "env",
                "use_sam_for_replicas": True,
                "local_auth_token_enabled": False,
                "has_loopback_replicas": False,
                "put_quorum": 1,
                "get_quorum": 1,
                "recv_base": 0,
                "recv_window": 16,
                "cover_gets": 0,
                "padding_bucket": 256,
                "privacy_profile": "high",
                "send_index": 0,
                "root_epoch": 0,
                "insecure_local_mode": False,
            },
            ack={},
        )
        self.assertIn("locked by environment", text)

    def test_diagnostics_highlights_action_for_await_live_root(self) -> None:
        text = build_blindbox_diagnostics_text(
            profile="p",
            selected_peer="peer.b32.i2p",
            delivery={
                "state": "await-live-root",
                "secure_live": False,
                "has_target": True,
                "stored_peer": True,
            },
            blindbox={
                "enabled": True,
                "ready": True,
                "poller_running": False,
                "has_root_secret": False,
                "blind_boxes": 1,
                "replica_endpoints": ["z.b32.i2p:1"],
                "replicas_gui_locked": False,
                "replicas_source": "profile-file",
                "use_sam_for_replicas": True,
                "local_auth_token_enabled": False,
                "has_loopback_replicas": False,
                "put_quorum": 1,
                "get_quorum": 1,
                "recv_base": 5,
                "recv_window": 16,
                "cover_gets": 1,
                "padding_bucket": 256,
                "privacy_profile": "high",
                "send_index": 0,
                "root_epoch": 0,
                "insecure_local_mode": False,
            },
            ack={},
        )
        self.assertIn("Offline queue is not ready yet", text)
        self.assertIn("Press Connect once", text)


if __name__ == "__main__":
    unittest.main()
