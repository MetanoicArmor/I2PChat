"""Tests for i2pchat.updates.release_index (HTML parsing, version compare)."""

from __future__ import annotations

import io
import os
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from i2pchat.updates import release_index as ri


SAMPLE_HTML = """
<html><body>
<a href="I2PChat-linux-x86_64-v1.0.0.zip">old</a>
<a href="I2PChat-linux-x86_64-v1.0.1.zip">new</a>
<a href="I2PChat-macOS-arm64-v2.0.0.zip">mac</a>
<tr><td>I2PChat-linux-x86_64-v1.0.2.zip</td></tr>
<span>noise I2PChat-bad-name.zip</span>
</body></html>
"""


class ReleaseIndexParseTests(unittest.TestCase):
    def test_parse_valid_rows_dedupes_and_orders_by_scan(self) -> None:
        rows = ri.parse_valid_release_rows(SAMPLE_HTML)
        versions = {v for _, v, _ in rows}
        self.assertIn("1.0.0", versions)
        self.assertIn("1.0.1", versions)
        self.assertIn("1.0.2", versions)
        self.assertIn("2.0.0", versions)
        self.assertEqual(len({fn for fn, _, _ in rows}), len(rows))

    def test_find_latest_for_prefix_linux(self) -> None:
        got = ri.find_latest_for_prefix(SAMPLE_HTML, "I2PChat-linux-x86_64")
        self.assertIsNotNone(got)
        ver, name = got
        self.assertEqual(ver, "1.0.2")
        self.assertEqual(name, "I2PChat-linux-x86_64-v1.0.2.zip")

    def test_find_latest_for_prefix_mac(self) -> None:
        got = ri.find_latest_for_prefix(SAMPLE_HTML, "I2PChat-macOS-arm64")
        self.assertEqual(got, ("2.0.0", "I2PChat-macOS-arm64-v2.0.0.zip"))

    def test_find_latest_missing_prefix(self) -> None:
        self.assertIsNone(
            ri.find_latest_for_prefix(SAMPLE_HTML, "I2PChat-linux-arm64")
        )

    def test_release_zip_re_rejects_bad_names(self) -> None:
        self.assertIsNone(ri.RELEASE_ZIP_RE.match("I2PChat-Linux-x86_64-v1.0.1.zip"))
        self.assertIsNone(ri.RELEASE_ZIP_RE.match("I2PChat-linux-x86_64-1.0.1.zip"))
        self.assertIsNotNone(
            ri.RELEASE_ZIP_RE.match("I2PChat-windows-x64-v1.0.1.zip")
        )

    def test_compare_version_strings(self) -> None:
        self.assertEqual(ri.compare_version_strings("1.0.0", "1.0.1"), -1)
        self.assertEqual(ri.compare_version_strings("1.0.1", "1.0.1"), 0)
        self.assertEqual(ri.compare_version_strings("2.0.0", "1.9.9"), 1)


class ReleaseIndexCheckSyncTests(unittest.TestCase):
    def test_up_to_date(self) -> None:
        def fake_open(req, timeout=0, **_k):
            return io.BytesIO(SAMPLE_HTML.encode())

        with patch.object(ri, "expected_artifact_prefix", return_value="I2PChat-linux-x86_64"):
            r = ri.check_for_updates_sync(
                "1.0.2",
                page_url="http://test.i2p/",
                opener=fake_open,
            )
        self.assertTrue(r.ok)
        self.assertEqual(r.kind, "up_to_date")

    def test_update_available(self) -> None:
        def fake_open(req, timeout=0, **_k):
            return io.BytesIO(SAMPLE_HTML.encode())

        with patch.object(ri, "expected_artifact_prefix", return_value="I2PChat-linux-x86_64"):
            r = ri.check_for_updates_sync(
                "1.0.0",
                page_url="http://test.i2p/",
                opener=fake_open,
            )
        self.assertTrue(r.ok)
        self.assertEqual(r.kind, "update_available")
        self.assertEqual(r.remote_version, "1.0.2")

    def test_no_artifact_for_prefix(self) -> None:
        def fake_open(req, timeout=0, **_k):
            return io.BytesIO(SAMPLE_HTML.encode())

        with patch.object(ri, "expected_artifact_prefix", return_value="I2PChat-windows-x64"):
            r = ri.check_for_updates_sync(
                "1.0.0",
                page_url="http://test.i2p/",
                opener=fake_open,
            )
        self.assertTrue(r.ok)
        self.assertEqual(r.kind, "no_artifact")

    def test_url_error(self) -> None:
        def boom(req, timeout=0, **_k):
            raise urllib.error.URLError("refused")

        with patch.object(ri, "expected_artifact_prefix", return_value="I2PChat-linux-x86_64"):
            r = ri.check_for_updates_sync("1.0.0", page_url="http://x/", opener=boom)
        self.assertFalse(r.ok)
        self.assertEqual(r.kind, "network")

    def test_unsupported_platform(self) -> None:
        with patch.object(ri, "expected_artifact_prefix", return_value=None):
            r = ri.check_for_updates_sync("1.0.0")
        self.assertFalse(r.ok)
        self.assertEqual(r.kind, "unsupported")


class OpenerSelectionTests(unittest.TestCase):
    def test_proxy_override_forces_proxy_handler(self) -> None:
        op = ri._opener_for_update_fetch(
            "http://x.b32.i2p/", proxy_url="http://127.0.0.1:14444"
        )
        self.assertNotEqual(op, urllib.request.urlopen)

    def test_i2p_uses_proxy_opener_when_no_env_proxy(self) -> None:
        with patch.object(ri, "_env_http_proxy_explicit", return_value=False):
            op = ri._opener_for_update_fetch("http://x.b32.i2p/")
        self.assertNotEqual(op, urllib.request.urlopen)

    def test_i2p_uses_urlopen_when_env_proxy_set(self) -> None:
        with patch.object(ri, "_env_http_proxy_explicit", return_value=True):
            op = ri._opener_for_update_fetch("http://x.b32.i2p/")
        self.assertEqual(op, urllib.request.urlopen)

    def test_non_i2p_no_proxy_injection(self) -> None:
        with patch.object(ri, "_env_http_proxy_explicit", return_value=False):
            op = ri._opener_for_update_fetch("https://example.com/")
        self.assertEqual(op, urllib.request.urlopen)

    def test_i2pchat_update_http_proxy_off_disables(self) -> None:
        with patch.dict(os.environ, {"I2PCHAT_UPDATE_HTTP_PROXY": "off"}):
            op = ri._opener_for_update_fetch("http://x.b32.i2p/")
        self.assertEqual(op, urllib.request.urlopen)


class DownloadsUrlTests(unittest.TestCase):
    def test_downloads_page_adds_fragment(self) -> None:
        with patch.dict("os.environ", {"I2PCHAT_RELEASES_PAGE_URL": "http://example.i2p/rel/"}):
            self.assertEqual(
                ri.downloads_page_url(),
                "http://example.i2p/rel/#downloads",
            )


if __name__ == "__main__":
    unittest.main()
