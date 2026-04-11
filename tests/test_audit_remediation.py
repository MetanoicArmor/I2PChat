import json
import os
import re
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel_path: str) -> str:
    with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as f:
        return f.read()


class AuditRemediationPolicyTests(unittest.TestCase):
    def test_handshake_uses_hkdf_key_separation(self) -> None:
        crypto_content = _read("i2pchat/crypto.py")
        core_content = _read("i2pchat/core/i2p_chat_core.py")
        self.assertIn("def derive_handshake_subkeys(", crypto_content)
        self.assertIn("hkdf_extract(", crypto_content)
        self.assertIn("hkdf_expand(", crypto_content)
        self.assertIn(
            "sess.shared_key, sess.shared_mac_key = self._compute_session_subkeys(",
            core_content,
        )

    def test_padding_profile_balanced_is_default(self) -> None:
        content = _read("i2pchat/core/i2p_chat_core.py")
        self.assertIn('PADDING_PROFILE_BALANCED = "balanced"', content)
        self.assertIn('PADDING_BALANCED_BLOCK = 128', content)
        self.assertIn('os.environ.get(\n            "I2PCHAT_PADDING_PROFILE", PADDING_PROFILE_BALANCED\n        )', content)
        self.assertIn("def _apply_padding_profile(self, body: bytes) -> bytes:", content)
        self.assertIn("PADDING_ENVELOPE_MAGIC", content)

    def test_metadata_padding_docs_present(self) -> None:
        readme = _read("README.md")
        ru_manual = _read("docs/MANUAL_RU.md")
        en_manual = _read("docs/MANUAL_EN.md")
        self.assertIn("Protocol metadata and padding profile", readme)
        self.assertIn("I2PCHAT_PADDING_PROFILE=off", readme)
        self.assertIn("Метаданные протокола и padding", ru_manual)
        self.assertIn("I2PCHAT_PADDING_PROFILE=off", ru_manual)
        self.assertIn("Protocol metadata and padding", en_manual)
        self.assertIn("I2PCHAT_PADDING_PROFILE=off", en_manual)

    def test_internal_sam_backend_package_present(self) -> None:
        sam_init = _read("i2pchat/sam/__init__.py")
        self.assertIn("create_session", sam_init)
        proto = _read("i2pchat/sam/protocol.py")
        self.assertIn("build_session_create", proto)

    def test_flake_lock_exists_and_pins_nixpkgs_rev(self) -> None:
        lock_content = _read("flake.lock")
        data = json.loads(lock_content)
        self.assertEqual(data.get("version"), 7)
        nixpkgs_rev = data["nodes"]["nixpkgs"]["locked"]["rev"]
        self.assertTrue(isinstance(nixpkgs_rev, str) and len(nixpkgs_rev) >= 7)

    def test_build_scripts_use_frozen_uv_sync(self) -> None:
        for rel_path in ("build-linux.sh", "build-macos.sh", "build-windows.ps1"):
            content = _read(rel_path)
            self.assertIn("uv sync", content, rel_path)
            self.assertIn("--frozen", content, rel_path)
            if rel_path.endswith(".ps1"):
                self.assertIn('"--group", "build"', content, rel_path)
            else:
                self.assertIn("--group build", content, rel_path)
            self.assertNotIn("install pyinstaller", content.lower(), rel_path)

    def test_uv_lock_pins_pyinstaller(self) -> None:
        content = _read("uv.lock")
        self.assertRegex(content, r'name = "pyinstaller"')
        self.assertIn("hash = ", content)

    def test_security_workflow_uses_uv_export_and_pip_audit(self) -> None:
        content = _read(".github/workflows/security-audit.yml")
        self.assertIn("uv export", content)
        self.assertIn("pip-audit", content)
        self.assertNotRegex(content, r"(?m)^\s*python\s+-m\s+pip\s+install\s+pip-audit\b")

    def test_test_gate_workflow_uses_uv(self) -> None:
        content = _read(".github/workflows/test-gate.yml")
        self.assertIn("uv sync", content)
        self.assertIn("astral-sh/setup-uv", content)

    def test_i2plib_is_not_declared_dependency(self) -> None:
        pp = _read("pyproject.toml")
        self.assertNotRegex(pp, r"(?mi)\"i2plib\"")
        self.assertNotRegex(pp, r"(?mi)^i2plib\s*=")

    def test_build_scripts_generate_signed_checksum_artifacts(self) -> None:
        for rel_path in ("build-linux.sh", "build-macos.sh", "build-windows.ps1"):
            content = _read(rel_path)
            self.assertIn("SHA256SUMS", content, rel_path)
            self.assertIn("gpg", content, rel_path)
            self.assertRegex(content, r"(?i)detach-sign", rel_path)
            self.assertRegex(content, r"(?i)SHA256(?:SUMS(?:\\.asc)?|_FILE)", rel_path)

    def test_secret_scan_workflow_exists_and_is_hardened(self) -> None:
        content = _read(".github/workflows/secret-scan.yml")
        self.assertIn("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", content)
        self.assertIn("permissions:", content)
        self.assertIn("contents: read", content)
        self.assertIn("gitleaks", content)
        self.assertIn("pull_request:", content)

    def test_gui_open_image_has_path_confinement(self) -> None:
        content = _read("i2pchat/gui/main_qt.py")
        self.assertIn("if not _is_path_within_directory(path, get_images_dir()):", content)
        self.assertIn("real_path = os.path.realpath(path)", content)
        self.assertIn("if not os.path.isfile(real_path):", content)
        self.assertIn("QtCore.QUrl.fromLocalFile(real_path)", content)

    def test_load_pixmap_no_exists_then_open_pattern(self) -> None:
        content = _read("i2pchat/gui/main_qt.py")
        self.assertNotIn("if not os.path.exists(path):", content)
        self.assertIn("if not _is_path_within_directory(real_path, get_images_dir()):", content)
        self.assertIn("pixmap = QtGui.QPixmap(real_path)", content)

    def test_linux_helpers_use_absolute_paths(self) -> None:
        gui_content = _read("i2pchat/gui/main_qt.py")
        notif_content = _read("i2pchat/platform/notifications.py")

        self.assertIn('canberra_path = shutil.which("canberra-gtk-play")', gui_content)
        self.assertIn('paplay_path = shutil.which("paplay")', gui_content)
        self.assertIn('aplay_path = shutil.which("aplay")', gui_content)
        self.assertIn('linux_cmds.append([canberra_path, "-i", "message-new-instant"])', gui_content)
        self.assertIn("linux_cmds.append([paplay_path, self.notify_sound_path])", gui_content)
        self.assertIn("linux_cmds.append([aplay_path, self.notify_sound_path])", gui_content)

        self.assertIn('notify_send_path = shutil.which("notify-send")', notif_content)
        self.assertIn("[notify_send_path, title, message]", notif_content)


if __name__ == "__main__":
    unittest.main()
