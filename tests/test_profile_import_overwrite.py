import os
import tempfile
import unittest

from i2p_chat_core import allocate_unique_profile_name


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


if __name__ == "__main__":
    unittest.main()
