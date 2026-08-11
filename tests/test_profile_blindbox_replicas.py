import json
import os
import tempfile
import unittest

from i2pchat.storage.profile_blindbox_replicas import (
    PROFILE_BLINDBOX_REPLICAS_VERSION,
    load_profile_blindbox_replicas_bundle,
    load_profile_blindbox_replicas_list,
    normalize_replica_endpoints,
    profile_blindbox_replicas_path,
    save_profile_blindbox_replicas_bundle,
    save_profile_blindbox_replicas_list,
)


class ProfileBlindboxReplicasTests(unittest.TestCase):
    def test_normalize_dedupes_and_trims(self) -> None:
        self.assertEqual(
            normalize_replica_endpoints([" a.b32.i2p:1 ", "a.b32.i2p:1", "b:2"]),
            ["a.b32.i2p:1", "b:2"],
        )

    def test_normalize_skips_hash_comment_lines(self) -> None:
        self.assertEqual(
            normalize_replica_endpoints(
                ["# note", "  # indented", "x.b32.i2p:1", "# trailing dup ignored"]
            ),
            ["x.b32.i2p:1"],
        )

    def test_roundtrip_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = profile_blindbox_replicas_path(td, "myprof")
            self.assertTrue(p.endswith("myprof.blindbox_replicas.json"))
            save_profile_blindbox_replicas_list(
                td,
                "myprof",
                ["x.b32.i2p:19444", "127.0.0.1:19444"],
            )
            self.assertTrue(os.path.isfile(p))
            loaded = load_profile_blindbox_replicas_list(td, "myprof")
            self.assertEqual(
                loaded,
                ["x.b32.i2p:19444", "127.0.0.1:19444"],
            )
            with open(p, "r", encoding="utf-8") as f:
                disk = json.load(f)
            self.assertEqual(disk.get("version"), PROFILE_BLINDBOX_REPLICAS_VERSION)
            # v3: empty auth is not persisted as a plaintext field anymore.
            self.assertNotIn("replica_auth", disk)
            self.assertNotIn("replica_auth_enc", disk)

    def test_bundle_encrypts_replica_auth_at_rest_with_identity_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            identity_key = b"\x11" * 32
            save_profile_blindbox_replicas_bundle(
                td,
                "p3",
                ["x.b32.i2p:1"],
                {"x.b32.i2p:1": "supersecret-token"},
                identity_key=identity_key,
            )
            path = profile_blindbox_replicas_path(td, "p3")
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
            # Token must not appear in plaintext anywhere on disk.
            self.assertNotIn("replica_auth", disk)
            self.assertIn("replica_auth_enc", disk)
            with open(path, "r", encoding="utf-8") as f:
                self.assertNotIn("supersecret-token", f.read())
            # Without the key, auth cannot be recovered.
            _, auth_nokey = load_profile_blindbox_replicas_bundle(td, "p3")
            self.assertEqual(auth_nokey, {})
            # With the key, it roundtrips.
            _, auth = load_profile_blindbox_replicas_bundle(
                td, "p3", identity_key=identity_key
            )
            self.assertEqual(auth, {"x.b32.i2p:1": "supersecret-token"})
            # Wrong key fails closed (no partial leak).
            _, auth_wrong = load_profile_blindbox_replicas_bundle(
                td, "p3", identity_key=b"\x22" * 32
            )
            self.assertEqual(auth_wrong, {})

    def test_load_v1_no_replica_auth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = profile_blindbox_replicas_path(td, "legacy")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "replicas": ["a.b32.i2p:1"]}, f)
            reps, auth = load_profile_blindbox_replicas_bundle(td, "legacy")
            self.assertEqual(reps, ["a.b32.i2p:1"])
            self.assertEqual(auth, {})

    def test_bundle_roundtrip_replica_auth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_profile_blindbox_replicas_bundle(
                td,
                "p2",
                ["x.b32.i2p:1", "127.0.0.1:2"],
                {
                    "x.b32.i2p:1": "tok1",
                    "127.0.0.1:2": "tok2",
                    "unknown.example:9": "drop",
                },
            )
            reps, auth = load_profile_blindbox_replicas_bundle(td, "p2")
            self.assertEqual(reps, ["x.b32.i2p:1", "127.0.0.1:2"])
            self.assertEqual(
                auth,
                {"x.b32.i2p:1": "tok1", "127.0.0.1:2": "tok2"},
            )

    def test_load_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_profile_blindbox_replicas_list(td, "nope"), [])

    def test_transient_profile_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                profile_blindbox_replicas_path(td, "default")
            with self.assertRaises(ValueError):
                profile_blindbox_replicas_path(td, "random_address")


if __name__ == "__main__":
    unittest.main()
