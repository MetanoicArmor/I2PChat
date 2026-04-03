import asyncio
import importlib
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch


server_example = importlib.import_module("i2pchat.blindbox.blindbox_server_example")
local_example = importlib.import_module("i2pchat.blindbox.local_server_example")
daemon_service = importlib.import_module("i2pchat.blindbox.daemon.service")


class BlindBoxServerExampleTests(unittest.TestCase):
    def test_production_daemon_service_exports_main(self) -> None:
        self.assertTrue(callable(daemon_service.main))

    def test_prune_store_drops_expired_and_oldest_for_quota(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_base = server_example.BASE
            old_store = server_example.STORE
            old_ttl = server_example.TTL_SEC
            old_max_files = server_example.MAX_FILES
            old_max_total = server_example.MAX_TOTAL_BYTES
            try:
                server_example.BASE = td
                server_example.STORE = os.path.join(td, "store")
                server_example.TTL_SEC = 10
                server_example.MAX_FILES = 2
                server_example.MAX_TOTAL_BYTES = 12
                server_example._ensure_store_layout()

                expired = server_example.path_for_key("expired")
                oldest = server_example.path_for_key("oldest")
                newest = server_example.path_for_key("newest")
                server_example._atomic_write_blob(expired, b"1111")
                server_example._atomic_write_blob(oldest, b"2222")
                server_example._atomic_write_blob(newest, b"3333")
                os.utime(expired, (100.0, 100.0))
                os.utime(oldest, (195.0, 195.0))
                os.utime(newest, (198.0, 198.0))

                ok = server_example._prune_store(incoming_bytes=5, now_ts=200.0)
                self.assertTrue(ok)
                self.assertFalse(os.path.exists(expired))
                self.assertFalse(os.path.exists(oldest))
                self.assertTrue(os.path.exists(newest))
            finally:
                server_example.BASE = old_base
                server_example.STORE = old_store
                server_example.TTL_SEC = old_ttl
                server_example.MAX_FILES = old_max_files
                server_example.MAX_TOTAL_BYTES = old_max_total

    def test_prune_store_rejects_incoming_blob_larger_than_total_budget(self) -> None:
        old_max_total = server_example.MAX_TOTAL_BYTES
        try:
            server_example.MAX_TOTAL_BYTES = 64
            self.assertFalse(server_example._prune_store(incoming_bytes=65))
        finally:
            server_example.MAX_TOTAL_BYTES = old_max_total

    def test_atomic_write_blob_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_base = server_example.BASE
            old_store = server_example.STORE
            try:
                server_example.BASE = td
                server_example.STORE = os.path.join(td, "store")
                path = server_example.path_for_key("perm-test")
                server_example._atomic_write_blob(path, b"payload")
                mode = stat.S_IMODE(os.stat(path).st_mode)
                self.assertEqual(mode, 0o600)
            finally:
                server_example.BASE = old_base
                server_example.STORE = old_store

    def test_status_line_reports_limits_and_auth_flag(self) -> None:
        old_auth = server_example._AUTH_TOKEN
        old_admin = server_example.ADMIN_TOKEN
        old_put_rate = server_example.RATE_LIMIT_PUTS_PER_MINUTE
        old_bytes_rate = server_example.RATE_LIMIT_BYTES_PER_MINUTE
        old_prefix_files = server_example.MAX_PREFIX_FILES
        old_prefix_bytes = server_example.MAX_PREFIX_BYTES
        try:
            server_example._AUTH_TOKEN = "sekrit"
            server_example.ADMIN_TOKEN = "adm"
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = 7
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = 1234
            server_example.MAX_PREFIX_FILES = 5
            server_example.MAX_PREFIX_BYTES = 4321
            status = server_example._status_line()
            self.assertIn("auth=1", status)
            self.assertIn("puts_per_min=7", status)
            self.assertIn("bytes_per_min=1234", status)
            self.assertIn("max_prefix_files=5", status)
            self.assertIn("max_prefix_bytes=4321", status)
        finally:
            server_example._AUTH_TOKEN = old_auth
            server_example.ADMIN_TOKEN = old_admin
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = old_put_rate
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = old_bytes_rate
            server_example.MAX_PREFIX_FILES = old_prefix_files
            server_example.MAX_PREFIX_BYTES = old_prefix_bytes

    def test_status_json_line_is_machine_readable(self) -> None:
        payload = json.loads(server_example._status_json_line())
        self.assertIn("files", payload)
        self.assertIn("bytes", payload)
        self.assertIn("max_prefix_files", payload)
        self.assertIn("max_prefix_bytes", payload)
        self.assertIn("admin_auth", payload)

    def test_admin_token_ok_uses_dedicated_admin_token(self) -> None:
        old_admin = server_example.ADMIN_TOKEN
        old_auth = server_example._AUTH_TOKEN
        try:
            server_example.ADMIN_TOKEN = "admin-secret"
            server_example._AUTH_TOKEN = "replica-secret"
            self.assertTrue(server_example._admin_token_ok("admin-secret"))
            self.assertFalse(server_example._admin_token_ok("replica-secret"))
            self.assertFalse(server_example._admin_token_ok(""))
        finally:
            server_example.ADMIN_TOKEN = old_admin
            server_example._AUTH_TOKEN = old_auth

    def test_http_response_and_token_helpers(self) -> None:
        resp = server_example._http_response(200, "text/plain", b"ok\n")
        self.assertTrue(resp.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertEqual(
            server_example._extract_http_bearer_token(
                {"authorization": "Bearer admin-token"}
            ),
            "admin-token",
        )
        self.assertEqual(server_example._extract_http_bearer_token({}), "")

    def test_http_route_response_covers_expected_paths(self) -> None:
        code, ctype, body = server_example._http_route_response("/healthz")
        self.assertEqual(code, 200)
        self.assertIn("text/plain", ctype)
        self.assertEqual(body, b"ok\n")
        code, ctype, body = server_example._http_route_response("/status.json")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        json.loads(body.decode("utf-8"))
        code, ctype, body = server_example._http_route_response("/metrics")
        self.assertEqual(code, 200)
        self.assertIn("text/plain", ctype)
        self.assertIn("blindbox_files", body.decode("utf-8"))
        code, _, _ = server_example._http_route_response("/nope")
        self.assertEqual(code, 404)

    def test_prometheus_metrics_text_includes_counters(self) -> None:
        old_metrics = server_example._METRICS
        try:
            server_example._METRICS = server_example.collections.Counter(
                {"put_ok": 3, "auth_fail": 1}
            )
            text = server_example._prometheus_metrics_text()
            self.assertIn('blindbox_events_total{event="put_ok"} 3', text)
            self.assertIn('blindbox_events_total{event="auth_fail"} 1', text)
        finally:
            server_example._METRICS = old_metrics

    def test_admit_put_enforces_count_limit(self) -> None:
        old_put_rate = server_example.RATE_LIMIT_PUTS_PER_MINUTE
        old_bytes_rate = server_example.RATE_LIMIT_BYTES_PER_MINUTE
        old_lock = server_example._PUT_RATE_LOCK
        old_timestamps = server_example._PUT_TIMESTAMPS
        old_sizes = server_example._PUT_SIZES
        try:
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = 2
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = 0
            server_example._PUT_RATE_LOCK = asyncio.Lock()
            server_example._PUT_TIMESTAMPS = server_example.collections.deque()
            server_example._PUT_SIZES = server_example.collections.deque()
            self.assertTrue(asyncio.run(server_example._admit_put(1, now_ts=100.0)))
            self.assertTrue(asyncio.run(server_example._admit_put(1, now_ts=110.0)))
            self.assertFalse(asyncio.run(server_example._admit_put(1, now_ts=120.0)))
            self.assertTrue(asyncio.run(server_example._admit_put(1, now_ts=161.0)))
        finally:
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = old_put_rate
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = old_bytes_rate
            server_example._PUT_RATE_LOCK = old_lock
            server_example._PUT_TIMESTAMPS = old_timestamps
            server_example._PUT_SIZES = old_sizes

    def test_admit_put_enforces_byte_limit(self) -> None:
        old_put_rate = server_example.RATE_LIMIT_PUTS_PER_MINUTE
        old_bytes_rate = server_example.RATE_LIMIT_BYTES_PER_MINUTE
        old_lock = server_example._PUT_RATE_LOCK
        old_timestamps = server_example._PUT_TIMESTAMPS
        old_sizes = server_example._PUT_SIZES
        try:
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = 0
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = 10
            server_example._PUT_RATE_LOCK = asyncio.Lock()
            server_example._PUT_TIMESTAMPS = server_example.collections.deque()
            server_example._PUT_SIZES = server_example.collections.deque()
            self.assertTrue(asyncio.run(server_example._admit_put(4, now_ts=100.0)))
            self.assertTrue(asyncio.run(server_example._admit_put(6, now_ts=101.0)))
            self.assertFalse(asyncio.run(server_example._admit_put(1, now_ts=102.0)))
            self.assertTrue(asyncio.run(server_example._admit_put(5, now_ts=161.0)))
        finally:
            server_example.RATE_LIMIT_PUTS_PER_MINUTE = old_put_rate
            server_example.RATE_LIMIT_BYTES_PER_MINUTE = old_bytes_rate
            server_example._PUT_RATE_LOCK = old_lock
            server_example._PUT_TIMESTAMPS = old_timestamps
            server_example._PUT_SIZES = old_sizes

    def test_admit_prefix_put_enforces_prefix_limits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_base = server_example.BASE
            old_store = server_example.STORE
            old_prefix_files = server_example.MAX_PREFIX_FILES
            old_prefix_bytes = server_example.MAX_PREFIX_BYTES
            try:
                server_example.BASE = td
                server_example.STORE = os.path.join(td, "store")
                server_example.MAX_PREFIX_FILES = 1
                server_example.MAX_PREFIX_BYTES = 10
                server_example._ensure_store_layout()
                key = "same-prefix-key"
                path = server_example.path_for_key(key)
                server_example._atomic_write_blob(path, b"12345")
                self.assertFalse(server_example._admit_prefix_put(key, 1))
                server_example.MAX_PREFIX_FILES = 0
                self.assertTrue(server_example._admit_prefix_put(key, 5))
                self.assertFalse(server_example._admit_prefix_put(key, 6))
            finally:
                server_example.BASE = old_base
                server_example.STORE = old_store
                server_example.MAX_PREFIX_FILES = old_prefix_files
                server_example.MAX_PREFIX_BYTES = old_prefix_bytes

    def test_write_metrics_exports_writes_json_and_prom_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_json = server_example.METRICS_JSON_PATH
            old_prom = server_example.METRICS_PROM_PATH
            old_metrics = server_example._METRICS
            try:
                server_example.METRICS_JSON_PATH = os.path.join(td, "metrics.json")
                server_example.METRICS_PROM_PATH = os.path.join(td, "metrics.prom")
                server_example._METRICS = server_example.collections.Counter({"put_ok": 2})
                server_example._write_metrics_exports()
                with open(server_example.METRICS_JSON_PATH, encoding="utf-8") as f:
                    payload = json.load(f)
                self.assertEqual(payload["events"]["put_ok"], 2)
                with open(server_example.METRICS_PROM_PATH, encoding="utf-8") as f:
                    prom = f.read()
                self.assertIn('blindbox_events_total{event="put_ok"} 2', prom)
            finally:
                server_example.METRICS_JSON_PATH = old_json
                server_example.METRICS_PROM_PATH = old_prom
                server_example._METRICS = old_metrics

    def test_append_audit_line_rotates_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_path = server_example.AUDIT_LOG_PATH
            old_max = server_example.AUDIT_LOG_MAX_BYTES
            old_backups = server_example.AUDIT_LOG_BACKUPS
            try:
                server_example.AUDIT_LOG_PATH = os.path.join(td, "audit.log")
                server_example.AUDIT_LOG_MAX_BYTES = 16
                server_example.AUDIT_LOG_BACKUPS = 2
                server_example._append_audit_line("first-line-123456\n")
                server_example._append_audit_line("second-line-abcdef\n")
                self.assertTrue(os.path.exists(server_example.AUDIT_LOG_PATH))
                self.assertTrue(os.path.exists(server_example.AUDIT_LOG_PATH + ".1"))
            finally:
                server_example.AUDIT_LOG_PATH = old_path
                server_example.AUDIT_LOG_MAX_BYTES = old_max
                server_example.AUDIT_LOG_BACKUPS = old_backups

    def test_emit_fail2ban_writes_reason_to_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_path = server_example.AUDIT_LOG_PATH
            old_json = server_example.LOG_JSON
            try:
                server_example.AUDIT_LOG_PATH = os.path.join(td, "audit.log")
                server_example.LOG_JSON = False
                with patch("sys.stderr") as fake_stderr:
                    server_example._emit_fail2ban(
                        "BLINDBOX_AUTH_FAIL", remote_host="127.0.0.1", remote_port=19444
                    )
                    self.assertTrue(fake_stderr.write.called)
                with open(server_example.AUDIT_LOG_PATH, encoding="utf-8") as f:
                    data = f.read()
                self.assertIn("FAIL2BAN", data)
                self.assertIn("BLINDBOX_AUTH_FAIL", data)
            finally:
                server_example.AUDIT_LOG_PATH = old_path
                server_example.LOG_JSON = old_json


class LocalServerExampleStringsTests(unittest.TestCase):
    def test_systemd_example_includes_hardening_directives(self) -> None:
        text = local_example.get_systemd_blindbox_unit_example_source()
        self.assertIn("UMask=0077", text)
        self.assertIn("NoNewPrivileges=yes", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ReadWritePaths=%h/.i2pchat-blindbox", text)
        self.assertIn("STATUS_JSON", text)
        self.assertIn("METRICS", text)
        self.assertIn("BLINDBOX_HTTP_STATUS=1", text)

    def test_dotenv_note_keeps_token_optional_for_public_replicas(self) -> None:
        note = local_example.get_blindbox_dotenv_example_note()
        self.assertIn("token empty", note)
        env_text = local_example.get_blindbox_dotenv_example_source()
        self.assertIn("BLINDBOX_MAX_TOTAL_BYTES=536870912", env_text)
        self.assertIn("BLINDBOX_MAX_FILES=4096", env_text)
        self.assertIn("BLINDBOX_MAX_PREFIX_BYTES=33554432", env_text)
        self.assertIn("BLINDBOX_MAX_PREFIX_FILES=256", env_text)
        self.assertIn("BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE=240", env_text)
        self.assertIn("BLINDBOX_ADMIN_TOKEN=", env_text)
        self.assertIn("BLINDBOX_METRICS_JSON_PATH=", env_text)
        self.assertIn("BLINDBOX_METRICS_PROM_PATH=", env_text)

    def test_standalone_launcher_example_is_available(self) -> None:
        text = local_example.get_blindbox_standalone_launcher_source()
        self.assertIn("blindbox_server_example.py", text)
        self.assertIn("importlib.util", text)
        note = local_example.get_blindbox_standalone_launcher_note()
        self.assertIn("Standalone wrapper", note)

    def test_production_daemon_package_assets_are_available(self) -> None:
        note = local_example.get_production_daemon_package_note()
        self.assertIn("python3 -m i2pchat.blindbox.daemon", note)
        systemd_text = local_example.get_production_daemon_systemd_source()
        self.assertIn("ExecStart=/usr/bin/python3 -m i2pchat.blindbox.daemon", systemd_text)
        env_text = local_example.get_production_daemon_env_source()
        self.assertIn("BLINDBOX_ADMIN_TOKEN=", env_text)
        self.assertIn("daemon.env", env_text)
        install_text = local_example.get_production_daemon_install_script_source()
        self.assertIn("systemctl --user enable --now i2pchat-blindbox.service", install_text)
        self.assertIn("daemon.env", install_text)
        one_shot = local_example.get_production_daemon_one_shot_install_source()
        self.assertIn("BlindBox mode [public/token]", one_shot)
        self.assertIn("install.sh [public|token]", one_shot)
        self.assertIn("python3 -m i2pchat.blindbox.daemon", one_shot)
        bundle_text = local_example.get_production_daemon_package_script_source()
        self.assertIn("I2PChat-BlindBox-daemon-v", bundle_text)
        self.assertIn("install/install.sh", bundle_text)
        self.assertIn("blindbox_server_example.py", bundle_text)

    def test_fail2ban_examples_are_available(self) -> None:
        filter_text = local_example.get_fail2ban_filter_example_source()
        self.assertIn("BLINDBOX_AUTH_FAIL", filter_text)
        self.assertIn("BLINDBOX_RATE_LIMIT", filter_text)
        jail_text = local_example.get_fail2ban_jail_example_source()
        self.assertIn("[i2pchat-blindbox]", jail_text)
        self.assertIn("audit.log", jail_text)
        self.assertIn("fail2ban", local_example.get_fail2ban_filter_example_note().lower())


if __name__ == "__main__":
    unittest.main()
