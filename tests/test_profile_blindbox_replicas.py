import json
import os
import tempfile
import unittest

from i2pchat.storage.profile_blindbox_replicas import (
    PROFILE_BLINDBOX_REPLICAS_VERSION,
    _CURRENT_RELEASE_BLINDBOX_REPLICA,
    _DEPRECATED_RELEASE_BLINDBOX_REPLICA,
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
            self.assertEqual(disk.get("replica_auth"), {})

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

    def test_old_release_builtin_pair_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = profile_blindbox_replicas_path(td, "migr")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": PROFILE_BLINDBOX_REPLICAS_VERSION,
                        "replicas": [
                            _DEPRECATED_RELEASE_BLINDBOX_REPLICA,
                            _CURRENT_RELEASE_BLINDBOX_REPLICA,
                        ],
                        "replica_auth": {},
                    },
                    f,
                )
            reps, auth = load_profile_blindbox_replicas_bundle(td, "migr")
            self.assertEqual(reps, [_CURRENT_RELEASE_BLINDBOX_REPLICA])
            self.assertEqual(auth, {})
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["replicas"], [_CURRENT_RELEASE_BLINDBOX_REPLICA])

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
