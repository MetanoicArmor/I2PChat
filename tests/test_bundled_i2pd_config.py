import tempfile
import unittest
from unittest import mock

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
        )
        args = BundledI2pdManager._build_launch_args("/opt/i2pd", rt)
        self.assertEqual(
            args,
            [
                "/opt/i2pd",
                "--datadir=/tmp/router-data",
                "--conf=/tmp/i2pd.conf",
                "--tunconf=/tmp/tunnels.conf",
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


if __name__ == "__main__":
    unittest.main()
