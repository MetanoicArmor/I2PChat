import os
import tempfile
import unittest
from unittest import mock

from i2pchat.router.settings import (
    RouterSettings,
    load_router_settings,
    router_runtime_dir,
    router_settings_path,
    save_router_settings,
)


class RouterSettingsTests(unittest.TestCase):
    def test_roundtrip_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("i2pchat.router.settings.get_profiles_dir", return_value=td):
                settings = RouterSettings(
                    backend="bundled",
                    bundled_sam_port=17657,
                    bundled_http_proxy_port=14445,
                )
                save_router_settings(settings)
                self.assertTrue(os.path.isfile(router_settings_path()))
                loaded = load_router_settings()
                self.assertEqual(loaded.backend, "bundled")
                self.assertEqual(loaded.bundled_sam_port, 17657)
                self.assertEqual(loaded.bundled_http_proxy_port, 14445)

    def test_runtime_dir_created(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("i2pchat.router.settings.get_profiles_dir", return_value=td):
                runtime = router_runtime_dir()
                self.assertTrue(runtime.startswith(td))
                self.assertTrue(os.path.isdir(runtime))


if __name__ == "__main__":
    unittest.main()
