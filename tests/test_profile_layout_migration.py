"""Migration from flat profile files to profiles/<name>/ layout."""

from __future__ import annotations

import os
import tempfile
import unittest

from i2pchat.core.i2p_chat_core import (
    get_profile_data_dir,
    legacy_flat_profile_dat_basenames,
    migrate_all_legacy_profiles_if_needed,
    migrate_legacy_profile_files_if_needed,
    resolve_existing_profile_file,
)


class ProfileLayoutMigrationTests(unittest.TestCase):
    def test_migrate_moves_sidecars_into_profiles_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as app:
            with open(os.path.join(app, "alice.dat"), "wb") as f:
                f.write(b"k1\npeer\n")
            with open(os.path.join(app, "alice.trust.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            migrate_legacy_profile_files_if_needed(app_root=app, profile="alice")
            dest = get_profile_data_dir("alice", create=False, app_root=app)
            self.assertTrue(os.path.isfile(os.path.join(dest, "alice.dat")))
            self.assertTrue(os.path.isfile(os.path.join(dest, "alice.trust.json")))
            self.assertFalse(os.path.isfile(os.path.join(app, "alice.dat")))

    def test_resolve_prefers_nested_over_flat(self) -> None:
        with tempfile.TemporaryDirectory() as app:
            nest = get_profile_data_dir("bob", create=True, app_root=app)
            with open(os.path.join(nest, "bob.dat"), "wb") as f:
                f.write(b"nested\n")
            with open(os.path.join(app, "bob.dat"), "wb") as f:
                f.write(b"flat\n")
            p = resolve_existing_profile_file(app, "bob", "bob.dat")
            self.assertEqual(p, os.path.join(nest, "bob.dat"))

    def test_migrate_all_migrates_every_flat_profile(self) -> None:
        with tempfile.TemporaryDirectory() as app:
            for name in ("alice", "bob"):
                with open(os.path.join(app, f"{name}.dat"), "wb") as f:
                    f.write(b"k\n")
                with open(os.path.join(app, f"{name}.contacts.json"), "w", encoding="utf-8") as f:
                    f.write("{}")
            self.assertEqual(
                set(legacy_flat_profile_dat_basenames(app)), {"alice", "bob"}
            )
            migrate_all_legacy_profiles_if_needed(app_root=app)
            self.assertEqual(legacy_flat_profile_dat_basenames(app), [])
            for name in ("alice", "bob"):
                dest = get_profile_data_dir(name, create=False, app_root=app)
                self.assertTrue(os.path.isfile(os.path.join(dest, f"{name}.dat")))
                self.assertTrue(
                    os.path.isfile(os.path.join(dest, f"{name}.contacts.json"))
                )
                self.assertFalse(os.path.isfile(os.path.join(app, f"{name}.dat")))


if __name__ == "__main__":
    unittest.main()
