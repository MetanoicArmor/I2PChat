import asyncio
import errno
import json
import os
import signal
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from i2pchat.router.bundled_i2pd import (
    BundledI2pdManager,
    BundledI2pdRuntime,
    render_i2pd_conf,
    unix_reaper_main,
    _unix_find_i2pd_pids_for_runtime,
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
            loaded_rt, loaded_pid, loaded_owner, loaded_launch = manager._read_state(td)
            self.assertEqual(loaded_pid, 12345)
            self.assertIsNone(loaded_owner)
            self.assertIsNone(loaded_launch)
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
            self.assertEqual(rt.pidfile_path, os.path.join(td, "i2pd.pid"))

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
                    mock.patch("i2pchat.router.bundled_i2pd.wait_for_sam_ready", new=mock.AsyncMock()), \
                    mock.patch.object(
                        BundledI2pdManager, "_spawn_windows_parent_exit_cleanup"
                    ), \
                    mock.patch.object(
                        BundledI2pdManager, "_spawn_unix_parent_exit_cleanup"
                    ):
                adopted = asyncio.run(manager._adopt_existing_runtime_if_available(td))
            self.assertTrue(adopted)
            self.assertEqual(manager.sam_address(), ("127.0.0.1", 17656))

    def test_write_state_records_owner_pid(self) -> None:
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
            manager._write_state(rt, 22222, 33333)
            _rt, pid, owner_pid, launch_pid = manager._read_state(td)
            self.assertEqual(pid, 22222)
            self.assertEqual(owner_pid, 33333)
            self.assertIsNone(launch_pid)

    def test_write_state_records_launch_pid(self) -> None:
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
            manager._write_state(rt, 22222, 33333, 44444)
            _rt, pid, owner_pid, launch_pid = manager._read_state(td)
            self.assertEqual(pid, 22222)
            self.assertEqual(owner_pid, 33333)
            self.assertEqual(launch_pid, 44444)

    def test_read_pidfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "i2pd.pid"
            path.write_text("54321\n", encoding="utf-8")
            self.assertEqual(BundledI2pdManager._read_pidfile(str(path)), 54321)

    def test_discover_windows_runtime_pids_parses_all_matches(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=17656,
            http_proxy_port=14444,
            socks_proxy_port=14447,
            control_http_port=17070,
            data_dir="C:/Users/test/AppData/Roaming/I2PChat/router/data",
            conf_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.conf",
            tunconf_path="C:/Users/test/AppData/Roaming/I2PChat/router/tunnels.conf",
            log_path="C:/Users/test/AppData/Roaming/I2PChat/router/router.log",
            pidfile_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.pid",
        )
        with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                mock.patch(
                    "i2pchat.router.bundled_i2pd.subprocess.check_output",
                    return_value="65433\r\n65432\r\n65433\r\n",
                ):
            pids = BundledI2pdManager._discover_windows_runtime_pids(rt)
        self.assertEqual(pids, [65433, 65432])

    def test_windows_owned_i2pd_query_script_tracks_descendants_and_extra_pids(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=17656,
            http_proxy_port=14444,
            socks_proxy_port=14447,
            control_http_port=17070,
            data_dir="C:/Users/test/AppData/Roaming/I2PChat/router/data",
            conf_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.conf",
            tunconf_path="C:/Users/test/AppData/Roaming/I2PChat/router/tunnels.conf",
            log_path="C:/Users/test/AppData/Roaming/I2PChat/router/router.log",
            pidfile_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.pid",
        )
        with mock.patch.object(
            BundledI2pdManager,
            "_candidate_bundled_i2pd_binaries",
            return_value=["C:/bundle/i2pd.exe"],
        ):
            script = BundledI2pdManager._build_windows_owned_i2pd_query_script(
                rt, extra_pids=[123, 456]
            )
        self.assertIn("$extra=@(123, 456)", script)
        self.assertIn("$binaries=@('C:/bundle/i2pd.exe')", script)
        self.assertIn("$matchesBinary", script)
        self.assertIn("$proc.ExecutablePath", script)
        self.assertIn("ParentProcessId", script)
        self.assertIn("$owned.Contains($parentPid)", script)

    def test_windows_owned_i2pd_kill_script_waits_for_quiet_window(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=17656,
            http_proxy_port=14444,
            socks_proxy_port=14447,
            control_http_port=17070,
            data_dir="C:/Users/test/AppData/Roaming/I2PChat/router/data",
            conf_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.conf",
            tunconf_path="C:/Users/test/AppData/Roaming/I2PChat/router/tunnels.conf",
            log_path="C:/Users/test/AppData/Roaming/I2PChat/router/router.log",
            pidfile_path="C:/Users/test/AppData/Roaming/I2PChat/router/i2pd.pid",
        )
        script = BundledI2pdManager._build_windows_owned_i2pd_kill_script(
            rt, max_wait_ms=15000, quiet_window_ms=3000, sleep_ms=250
        )
        self.assertIn("$deadline = [DateTime]::UtcNow.AddMilliseconds(15000)", script)
        self.assertIn("$quietUntil = [DateTime]::UtcNow.AddMilliseconds(3000)", script)
        self.assertIn("$quietUntil = [DateTime]::UtcNow.AddMilliseconds(3000);", script)
        self.assertIn("$sleepMs=250;", script)

    def test_snapshot_windows_i2pd_processes_parses_json(self) -> None:
        raw = (
            '[{"ProcessId":65433,"ParentProcessId":65432,"Name":"i2pd.exe",'
            '"ExecutablePath":"C:/bundle/i2pd.exe","CommandLine":"i2pd --conf=x"}]'
        )
        with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                mock.patch(
                    "i2pchat.router.bundled_i2pd.subprocess.check_output",
                    return_value=raw,
                ):
            snapshot = BundledI2pdManager._snapshot_windows_i2pd_processes()
        self.assertEqual(
            snapshot,
            [
                {
                    "pid": 65433,
                    "parent_pid": 65432,
                    "name": "i2pd.exe",
                    "exe": "C:/bundle/i2pd.exe",
                    "cmd": "i2pd --conf=x",
                }
            ],
        )

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
            manager._write_state(rt, 45678, None, 45679)
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "linux"), \
                    mock.patch.object(BundledI2pdManager, "_terminate_pid_sync") as term:
                BundledI2pdManager.force_cleanup_runtime_root(td)
            term.assert_called_once_with(45678)
            self.assertFalse((Path(td) / "managed-process.json").exists())

    def test_force_cleanup_runtime_root_uses_windows_discovery_when_pid_missing(self) -> None:
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
            manager._write_state(rt, None)
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                BundledI2pdManager, "_terminate_windows_runtime_processes_sync"
            ) as terminate, mock.patch.object(
                BundledI2pdManager, "_spawn_windows_delayed_cleanup"
            ):
                BundledI2pdManager.force_cleanup_runtime_root(td)
            terminate.assert_called_once()
            args, kwargs = terminate.call_args
            self.assertEqual(args[0].conf_path, rt.conf_path)
            self.assertEqual(kwargs["extra_pids"], [None, None])

    def test_force_cleanup_runtime_root_uses_launch_pid_from_state(self) -> None:
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
            manager._write_state(rt, 45678, None, 45679)
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                        BundledI2pdManager, "_terminate_windows_runtime_processes_sync"
                    ) as terminate, mock.patch.object(
                        BundledI2pdManager, "_spawn_windows_delayed_cleanup"
                    ):
                BundledI2pdManager.force_cleanup_runtime_root(td)
            _args, kwargs = terminate.call_args
            self.assertEqual(kwargs["extra_pids"], [45679, 45678])

    def test_force_cleanup_runtime_root_infers_runtime_when_state_missing(self) -> None:
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
                        f"logfile = {td}/router.log",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                BundledI2pdManager, "_terminate_windows_runtime_processes_sync"
            ) as terminate, mock.patch.object(
                BundledI2pdManager, "_spawn_windows_delayed_cleanup"
            ):
                BundledI2pdManager.force_cleanup_runtime_root(td)
            terminate.assert_called_once()
            args, kwargs = terminate.call_args
            self.assertTrue(args[0].conf_path.endswith("i2pd.conf"))
            self.assertEqual(kwargs["extra_pids"], [None, None])

    def test_adopt_existing_runtime_infers_windows_pid(self) -> None:
        settings = RouterSettings(backend="bundled")
        manager = BundledI2pdManager(settings)
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
                        f"logfile = {td}/router.log",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch("i2pchat.router.bundled_i2pd.wait_for_sam_ready", new=mock.AsyncMock()), \
                    mock.patch.object(
                        BundledI2pdManager, "_spawn_windows_parent_exit_cleanup"
                    ), \
                    mock.patch.object(
                        BundledI2pdManager, "_spawn_unix_parent_exit_cleanup"
                    ), \
                    mock.patch.object(
                        BundledI2pdManager, "_discover_windows_runtime_pid", return_value=71111
                    ):
                adopted = asyncio.run(manager._adopt_existing_runtime_if_available(td))
            self.assertTrue(adopted)
            self.assertEqual(manager._managed_pid, 71111)

    def test_force_cleanup_runtime_root_spawns_windows_delayed_cleanup(self) -> None:
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
                        f"logfile = {td}/router.log",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                BundledI2pdManager, "_spawn_windows_delayed_cleanup"
            ) as delayed, mock.patch.object(
                BundledI2pdManager, "_discover_windows_runtime_pids", return_value=[]
            ):
                BundledI2pdManager.force_cleanup_runtime_root(td)
            delayed.assert_called_once()

    def test_stop_preserves_state_when_pid_survives(self) -> None:
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
            manager._runtime = rt
            manager._managed_pid = 77777
            manager._launch_pid = 66666
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                        manager,
                        "_terminate_windows_runtime_processes",
                        new=mock.AsyncMock(return_value=[88888]),
                    ), \
                    mock.patch.object(
                        BundledI2pdManager, "_spawn_windows_delayed_cleanup"
                    ), \
                    mock.patch.object(
                        BundledI2pdManager,
                        "_discover_windows_runtime_pids",
                        return_value=[88888],
                    ):
                asyncio.run(manager.stop())
            self.assertTrue((Path(td) / "managed-process.json").exists())
            self.assertEqual(manager._managed_pid, 88888)
            self.assertEqual(manager._launch_pid, 66666)
            self.assertIsNone(manager._runtime)

    def test_stop_windows_uses_launch_pid_and_managed_pid_for_force_kill(self) -> None:
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
            manager._runtime = rt
            manager._managed_pid = 77777
            manager._launch_pid = 66666
            terminate = mock.AsyncMock(return_value=[])
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                        manager,
                        "_terminate_windows_runtime_processes",
                        new=terminate,
                    ), \
                    mock.patch.object(
                        BundledI2pdManager,
                        "_discover_windows_runtime_pids",
                        return_value=[],
                    ):
                asyncio.run(manager.stop())
            _args, kwargs = terminate.call_args
            self.assertEqual(kwargs["extra_pids"], [66666, 77777])

    def test_stop_clears_state_when_pid_is_gone(self) -> None:
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
            manager._runtime = rt
            manager._managed_pid = 77777
            manager._launch_pid = 66666
            manager._write_state(rt, 77777)
            with mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "win32"), \
                    mock.patch.object(
                        manager,
                        "_terminate_windows_runtime_processes",
                        new=mock.AsyncMock(return_value=[]),
                    ), \
                    mock.patch.object(
                        BundledI2pdManager,
                        "_discover_windows_runtime_pids",
                        return_value=[],
                    ):
                asyncio.run(manager.stop())
            self.assertFalse((Path(td) / "managed-process.json").exists())
            self.assertIsNone(manager._launch_pid)

    def test_unix_find_i2pd_pids_matches_ps_command(self) -> None:
        with mock.patch(
            "i2pchat.router.bundled_i2pd._unix_ps_pid_command_lines",
            return_value=[
                (100, "sleep 1"),
                (555, "/opt/i2pd --conf=/tmp/qq/i2pd.conf"),
            ],
        ):
            pids = _unix_find_i2pd_pids_for_runtime(
                "/tmp/qq/i2pd.conf", "/tmp/qq/data", "/no/such.pid"
            )
        self.assertEqual(pids, [555])

    def test_spawn_unix_parent_exit_cleanup_popen_env(self) -> None:
        rt = BundledI2pdRuntime(
            sam_host="127.0.0.1",
            sam_port=1,
            http_proxy_port=2,
            socks_proxy_port=3,
            control_http_port=4,
            data_dir="/d",
            conf_path="/c/i2pd.conf",
            tunconf_path="/c/tunnels.conf",
            log_path="/c/log",
            pidfile_path="/c/pid",
        )
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("i2pchat.router.bundled_i2pd.sys.platform", "darwin"):
            BundledI2pdManager._spawn_unix_parent_exit_cleanup(rt, 4242)
        popen.assert_called_once()
        _a, kw = popen.call_args
        self.assertEqual(kw["env"]["I2PCHAT_ROUTER_REAPER"], "1")
        self.assertEqual(kw["env"]["I2PCHAT_REAPER_OWNER"], "4242")
        self.assertEqual(kw["env"]["I2PCHAT_REAPER_CONF"], "/c/i2pd.conf")
        self.assertIn("managed-process.json", kw["env"]["I2PCHAT_REAPER_STATE"])

    def test_unix_reaper_main_skips_when_new_session_owner(self) -> None:
        fake_owner = 12345
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "managed-process.json"
            state.write_text(
                json.dumps({"owner_pid": 99999}),
                encoding="utf-8",
            )

            def fake_kill(pid: int, sig: int) -> None:
                if pid == fake_owner and sig == 0:
                    raise OSError(errno.ESRCH, "no such process")
                if sig == signal.SIGINT:
                    return

            env = {
                "I2PCHAT_REAPER_OWNER": str(fake_owner),
                "I2PCHAT_REAPER_STATE": str(state),
                "I2PCHAT_REAPER_CONF": "",
                "I2PCHAT_REAPER_DATADIR": "",
                "I2PCHAT_REAPER_PIDFILE": "",
            }
            with mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch("os.kill", side_effect=fake_kill), \
                    mock.patch(
                        "i2pchat.router.bundled_i2pd._unix_find_i2pd_pids_for_runtime"
                    ) as find:
                unix_reaper_main()
            find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
