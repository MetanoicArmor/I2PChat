import os
import tempfile
import unittest
from unittest import mock
import json

from i2pchat.router.settings import (
    RouterSettings,
    bundled_i2pd_allowed,
    load_router_settings,
    normalize_router_settings,
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

    def test_load_missing_prefs_defaults_to_system(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("i2pchat.router.settings.get_profiles_dir", return_value=td):
                loaded = load_router_settings()
                self.assertEqual(loaded.backend, "system")
                self.assertFalse(loaded.bundled_auto_start)

    def test_disable_bundled_env_forces_system_backend(self) -> None:
        with mock.patch.dict("os.environ", {"I2PCHAT_DISABLE_BUNDLED_I2PD": "1"}):
            settings = normalize_router_settings(
                RouterSettings(backend="bundled", bundled_auto_start=True)
            )
            self.assertFalse(bundled_i2pd_allowed())
            self.assertEqual(settings.backend, "system")
            self.assertFalse(settings.bundled_auto_start)

    def test_disable_bundled_marker_forces_system_backend(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch(
                "i2pchat.router.settings.os.path.isfile",
                side_effect=lambda path: path == "/usr/share/i2pchat/system-router-only",
            ):
                settings = normalize_router_settings(
                    RouterSettings(backend="bundled", bundled_auto_start=True)
                )
                self.assertFalse(bundled_i2pd_allowed())
                self.assertEqual(settings.backend, "system")
                self.assertFalse(settings.bundled_auto_start)

    def test_runtime_dir_created(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("i2pchat.router.settings.get_profiles_dir", return_value=td):
                runtime = router_runtime_dir()
                self.assertTrue(runtime.startswith(td))
                self.assertTrue(os.path.isdir(runtime))

    def test_load_coerces_string_and_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("i2pchat.router.settings.get_profiles_dir", return_value=td):
                with mock.patch("i2pchat.router.settings.bundled_i2pd_allowed", return_value=True):
                    with open(router_settings_path(), "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "backend": "bundled",
                                "system_sam_host": " router.local ",
                                "system_sam_port": "70000",
                                "bundled_sam_host": " 127.0.0.2 ",
                                "bundled_sam_port": "17657",
                                "bundled_http_proxy_port": "not-a-port",
                                "bundled_socks_proxy_port": 14448,
                                "bundled_control_http_port": "20004",
                                "bundled_auto_start": "yes",
                            },
                            f,
                        )

                    loaded = load_router_settings()

                self.assertEqual(loaded.backend, "bundled")
                self.assertEqual(loaded.system_sam_host, "router.local")
                self.assertEqual(loaded.system_sam_port, 7656)
                self.assertEqual(loaded.bundled_sam_host, "127.0.0.2")
                self.assertEqual(loaded.bundled_sam_port, 17657)
                self.assertEqual(loaded.bundled_http_proxy_port, 14444)
                self.assertEqual(loaded.bundled_socks_proxy_port, 14448)
                self.assertEqual(loaded.bundled_control_http_port, 20004)
                self.assertTrue(loaded.bundled_auto_start)


if __name__ == "__main__":
    unittest.main()
