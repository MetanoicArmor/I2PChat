import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from i2pchat.core.i2p_chat_core import allocate_unique_profile_name, import_profile_dat_atomic


class ProfileImportSafetyTests(unittest.TestCase):
    def test_collision_returns_suffixed_profile_name(self) -> None:
        with tempfile.TemporaryDirectory() as profiles_dir:
            with open(os.path.join(profiles_dir, "alice.dat"), "wb") as f:
                f.write(b"old")

            candidate = allocate_unique_profile_name(profiles_dir, "alice")
            self.assertEqual(candidate, "alice_1")

    def test_no_collision_keeps_original_profile_name(self) -> None:
        with tempfile.TemporaryDirectory() as profiles_dir:
            candidate = allocate_unique_profile_name(profiles_dir, "bob")
            self.assertEqual(candidate, "bob")

    def test_long_profile_name_suffix_stays_within_limit(self) -> None:
        with tempfile.TemporaryDirectory() as profiles_dir:
            base = "a" * 64
            with open(os.path.join(profiles_dir, f"{base}.dat"), "wb") as f:
                f.write(b"x")

            candidate = allocate_unique_profile_name(profiles_dir, base)
            self.assertEqual(len(candidate), 64)
            self.assertTrue(candidate.endswith("_1"))

    def test_atomic_import_is_race_safe_for_same_profile(self) -> None:
        with tempfile.TemporaryDirectory() as profiles_dir:
            source = os.path.join(profiles_dir, "incoming.dat")
            with open(source, "wb") as f:
                f.write(b"secret-profile-bytes")

            start = threading.Barrier(2)

            def do_import() -> str:
                start.wait()
                return import_profile_dat_atomic(source, profiles_dir, "alice")

            with ThreadPoolExecutor(max_workers=2) as pool:
                names = list(pool.map(lambda _: do_import(), range(2)))

            self.assertEqual(sorted(names), ["alice", "alice_1"])
            for name in names:
                dat_path = os.path.join(
                    profiles_dir, "profiles", name, f"{name}.dat"
                )
                with open(dat_path, "rb") as f:
                    self.assertEqual(f.read(), b"secret-profile-bytes")

    def test_atomic_import_rejects_symlink_source(self) -> None:
        with tempfile.TemporaryDirectory() as profiles_dir:
            outside_source = os.path.join(profiles_dir, "outside.dat")
            with open(outside_source, "wb") as f:
                f.write(b"payload")
            symlink_source = os.path.join(profiles_dir, "incoming.dat")
            os.symlink(outside_source, symlink_source)

            with self.assertRaises(ValueError):
                import_profile_dat_atomic(symlink_source, profiles_dir, "alice")


if __name__ == "__main__":
    unittest.main()
