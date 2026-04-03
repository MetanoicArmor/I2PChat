import asyncio
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from i2pchat.router.bundled_i2pd import (
    BundledI2pdManager,
    BundledI2pdRuntime,
    render_i2pd_conf,
)
from i2pchat.router.settings import RouterSettings


class BundledI2pdConfigTests(unittest.TestCase):
    def test_render_i2pd_conf_contains_isolated_ports(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=17656,
            http_proxy_port=14444,
            socks_proxy_port=14447,
            control_http_port=17070,
            data_dir="/tmp/router-data",
            conf_path="/tmp/i2pd.conf",
            tunconf_path="/tmp/tunnels.conf",
            log_path="/tmp/router.log",
            pidfile_path="/tmp/i2pd.pid",
        )
        text = render_i2pd_conf(rt)
        self.assertIn("daemon = false", text)
        self.assertIn("service = false", text)
        self.assertIn("sam.port = 17656", text)
        self.assertIn("httpproxy.port = 14444", text)
        self.assertIn("socksproxy.port = 14447", text)
        self.assertIn("http.port = 17070", text)
        self.assertIn("logfile = /tmp/router.log", text)

    def test_build_launch_args_use_isolated_paths(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=17656,
            http_proxy_port=14444,
            socks_proxy_port=14447,
            control_http_port=17070,
            data_dir="/tmp/router-data",
            conf_path="/tmp/i2pd.conf",
            tunconf_path="/tmp/tunnels.conf",
            log_path="/tmp/router.log",
            pidfile_path="/tmp/i2pd.pid",
        )
        args = BundledI2pdManager._build_launch_args("/opt/i2pd", rt)
        self.assertEqual(
            args,
            [
                "/opt/i2pd",
                "--datadir=/tmp/router-data",
                "--conf=/tmp/i2pd.conf",
                "--tunconf=/tmp/tunnels.conf",
                "--pidfile=/tmp/i2pd.pid",
            ],
        )

    def test_build_runtime_uses_free_ports_when_preferred_is_busy(self) -> None:
        settings = RouterSettings(
            backend="bundled",
            bundled_sam_port=17656,
            bundled_http_proxy_port=14444,
            bundled_socks_proxy_port=14447,
            bundled_control_http_port=17070,
        )
        manager = BundledI2pdManager(settings)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("i2pchat.router.bundled_i2pd.router_runtime_dir", return_value=td), \
                mock.patch("i2pchat.router.bundled_i2pd.is_tcp_open", return_value=True), \
                mock.patch(
                    "i2pchat.router.bundled_i2pd.pick_free_tcp_port",
                    side_effect=[20001, 20002, 20003, 20004],
                ):
            rt = manager._build_runtime()
        self.assertEqual(rt.sam_port, 20001)
        self.assertEqual(rt.http_proxy_port, 20002)
        self.assertEqual(rt.socks_proxy_port, 20003)
        self.assertEqual(rt.control_http_port, 20004)

    def test_state_roundtrip(self) -> None:
        settings = RouterSettings(backend="bundled")
        manager = BundledI2pdManager(settings)
        with tempfile.TemporaryDirectory() as td:
            rt = BundledI2pdRuntime(
                sam_host="127.0.0.1",
                sam_port=17656,
                http_proxy_port=14444,
                socks_proxy_port=14447,
                control_http_port=17070,
                data_dir=f"{td}/data",
                conf_path=f"{td}/i2pd.conf",
                tunconf_path=f"{td}/tunnels.conf",
                log_path=f"{td}/router.log",
                pidfile_path=f"{td}/i2pd.pid",
            )
            manager._write_state(rt, 12345)
            loaded_rt, loaded_pid = manager._read_state(td)
            self.assertEqual(loaded_pid, 12345)
            self.assertIsNotNone(loaded_rt)
            assert loaded_rt is not None
            self.assertEqual(loaded_rt.sam_port, 17656)
            self.assertEqual(loaded_rt.http_proxy_port, 14444)
            self.assertEqual(loaded_rt.pidfile_path, f"{td}/i2pd.pid")

    def test_infer_runtime_from_existing_conf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "i2pd.conf"
            conf.write_text(
                "\n".join(
                    [
                        "sam.address = 127.0.0.1",
                        "sam.port = 17656",
                        "http.port = 17070",
                        "httpproxy.port = 14444",
                        "socksproxy.port = 14447",
                        "logfile = /tmp/router.log",
                    ]
                ),
                encoding="utf-8",
            )
            rt = BundledI2pdManager._infer_runtime_from_existing_conf(td)
            self.assertIsNotNone(rt)
            assert rt is not None
            self.assertEqual(rt.sam_port, 17656)
            self.assertEqual(rt.http_proxy_port, 14444)
            self.assertEqual(rt.log_path, "/tmp/router.log")
            self.assertEqual(rt.pidfile_path, f"{td}/i2pd.pid")

    def test_adopt_existing_runtime_from_state(self) -> None:
        settings = RouterSettings(backend="bundled")
        manager = BundledI2pdManager(settings)
        with tempfile.TemporaryDirectory() as td:
            rt = BundledI2pdRuntime(
                sam_host="127.0.0.1",
                sam_port=17656,
                http_proxy_port=14444,
                socks_proxy_port=14447,
                control_http_port=17070,
                data_dir=f"{td}/data",
                conf_path=f"{td}/i2pd.conf",
                tunconf_path=f"{td}/tunnels.conf",
                log_path=f"{td}/router.log",
                pidfile_path=f"{td}/i2pd.pid",
            )
            manager._write_state(rt, 43210)
            with mock.patch.object(manager, "_pid_alive", return_value=True), \
                    mock.patch("i2pchat.router.bundled_i2pd.wait_for_sam_ready", new=mock.AsyncMock()):
                adopted = asyncio.run(manager._adopt_existing_runtime_if_available(td))
            self.assertTrue(adopted)
            self.assertEqual(manager.sam_address(), ("127.0.0.1", 17656))

    def test_read_pidfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "i2pd.pid"
            path.write_text("54321\n", encoding="utf-8")
            self.assertEqual(BundledI2pdManager._read_pidfile(str(path)), 54321)

    def test_force_cleanup_runtime_root_uses_state_pid(self) -> None:
        settings = RouterSettings(backend="bundled")
        manager = BundledI2pdManager(settings)
        with tempfile.TemporaryDirectory() as td:
            rt = BundledI2pdRuntime(
                sam_host="127.0.0.1",
                sam_port=17656,
                http_proxy_port=14444,
                socks_proxy_port=14447,
                control_http_port=17070,
                data_dir=f"{td}/data",
                conf_path=f"{td}/i2pd.conf",
                tunconf_path=f"{td}/tunnels.conf",
                log_path=f"{td}/router.log",
                pidfile_path=f"{td}/i2pd.pid",
            )
            manager._write_state(rt, 45678)
            with mock.patch.object(BundledI2pdManager, "_terminate_pid_sync") as term:
                BundledI2pdManager.force_cleanup_runtime_root(td)
            term.assert_called_once_with(45678)
            self.assertFalse((Path(td) / "managed-process.json").exists())


if __name__ == "__main__":
    unittest.main()
