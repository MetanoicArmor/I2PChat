import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2p_chat_core import I2PChatCore


class BlindBoxCoreTelemetryTests(unittest.TestCase):
    def test_blindbox_disabled_for_transient_profile(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        try:
            core = I2PChatCore(profile="default")
            telemetry = core.get_blindbox_telemetry()
            self.assertFalse(telemetry["enabled"])
            self.assertFalse(telemetry["ready"])
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled

    def test_blindbox_enabled_with_persistent_profile_and_replicas(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_privacy = os.environ.get("I2PCHAT_BLINDBOX_PRIVACY_PROFILE")
        old_poll_min = os.environ.get("I2PCHAT_BLINDBOX_POLL_MIN_SEC")
        old_poll_max = os.environ.get("I2PCHAT_BLINDBOX_POLL_MAX_SEC")
        old_cover = os.environ.get("I2PCHAT_BLINDBOX_COVER_GETS")
        old_padding = os.environ.get("I2PCHAT_BLINDBOX_PADDING_BUCKET")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p,r2.b32.i2p"
        os.environ.pop("I2PCHAT_BLINDBOX_PRIVACY_PROFILE", None)
        os.environ.pop("I2PCHAT_BLINDBOX_POLL_MIN_SEC", None)
        os.environ.pop("I2PCHAT_BLINDBOX_POLL_MAX_SEC", None)
        os.environ.pop("I2PCHAT_BLINDBOX_COVER_GETS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_PADDING_BUCKET", None)
        try:
            core = I2PChatCore(profile="alice")
            telemetry = core.get_blindbox_telemetry()
            self.assertTrue(telemetry["enabled"])
            self.assertEqual(telemetry["replicas"], 2)
            self.assertEqual(telemetry["blind_boxes"], 2)
            self.assertFalse(telemetry["has_root_secret"])
            self.assertEqual(telemetry["send_index"], 0)
            self.assertEqual(telemetry["privacy_profile"], "high")
            self.assertEqual(float(telemetry["poll_min_sec"]), 5.0)
            self.assertEqual(float(telemetry["poll_max_sec"]), 12.0)
            self.assertEqual(int(telemetry["cover_gets"]), 2)
            self.assertEqual(int(telemetry["padding_bucket"]), 1024)
            self.assertEqual(int(telemetry["root_epoch"]), 0)
            self.assertEqual(int(telemetry["root_rotate_messages"]), 256)
            self.assertEqual(int(telemetry["root_rotate_seconds"]), 6 * 60 * 60)
            self.assertEqual(int(telemetry["max_previous_roots"]), 2)
            self.assertEqual(int(telemetry["previous_roots_loaded"]), 0)
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_privacy is None:
                os.environ.pop("I2PCHAT_BLINDBOX_PRIVACY_PROFILE", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_PRIVACY_PROFILE"] = old_privacy
            if old_poll_min is None:
                os.environ.pop("I2PCHAT_BLINDBOX_POLL_MIN_SEC", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_POLL_MIN_SEC"] = old_poll_min
            if old_poll_max is None:
                os.environ.pop("I2PCHAT_BLINDBOX_POLL_MAX_SEC", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_POLL_MAX_SEC"] = old_poll_max
            if old_cover is None:
                os.environ.pop("I2PCHAT_BLINDBOX_COVER_GETS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_COVER_GETS"] = old_cover
            if old_padding is None:
                os.environ.pop("I2PCHAT_BLINDBOX_PADDING_BUCKET", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_PADDING_BUCKET"] = old_padding

    def test_blindbox_default_on_for_persistent_profile(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_default_replicas = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS")
        old_default_file = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE")
        old_no_builtin = os.environ.get("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS")
        old_local_fallback = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_FALLBACK")
        os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
        os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", None)
        os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
        try:
            core = I2PChatCore(profile="alice")
            telemetry = core.get_blindbox_telemetry()
            self.assertTrue(telemetry["enabled"])
            self.assertEqual(str(telemetry["enabled_source"]), "default")
            self.assertEqual(str(telemetry["replicas_source"]), "release-builtin")
            self.assertEqual(int(telemetry["replicas"]), 2)
            self.assertEqual(int(telemetry["blind_boxes"]), 2)
            self.assertTrue(bool(telemetry["use_sam_for_replicas"]))
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_default_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS"] = old_default_replicas
            if old_default_file is None:
                os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE"] = old_default_file
            if old_no_builtin is None:
                os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS"] = old_no_builtin
            if old_local_fallback is None:
                os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_LOCAL_FALLBACK"] = old_local_fallback

    def test_blindbox_skips_release_builtin_when_no_builtin_env_set(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_default_replicas = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS")
        old_default_file = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE")
        old_no_builtin = os.environ.get("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS")
        old_local_fallback = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_FALLBACK")
        os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
        os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", None)
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
        os.environ["I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS"] = "1"
        try:
            core = I2PChatCore(profile="alice")
            telemetry = core.get_blindbox_telemetry()
            self.assertEqual(str(telemetry["replicas_source"]), "none")
            self.assertEqual(int(telemetry["replicas"]), 0)
            self.assertEqual(int(telemetry["blind_boxes"]), 0)
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_default_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS"] = old_default_replicas
            if old_default_file is None:
                os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE"] = old_default_file
            if old_no_builtin is None:
                os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS"] = old_no_builtin
            if old_local_fallback is None:
                os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_LOCAL_FALLBACK"] = old_local_fallback

    def test_blindbox_uses_default_replicas_env_when_specific_is_missing(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_default_replicas = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS")
        old_local_fallback = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_FALLBACK")
        old_no_builtin = os.environ.get("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS")
        os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
        os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
        os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS"] = (
            "r1.b32.i2p,r2.b32.i2p,r3.b32.i2p"
        )
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
        os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
        try:
            core = I2PChatCore(profile="alice")
            telemetry = core.get_blindbox_telemetry()
            self.assertTrue(telemetry["enabled"])
            self.assertEqual(str(telemetry["replicas_source"]), "env-default")
            self.assertEqual(int(telemetry["replicas"]), 3)
            self.assertTrue(bool(telemetry["use_sam_for_replicas"]))
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_default_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS"] = old_default_replicas
            if old_local_fallback is None:
                os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_LOCAL_FALLBACK"] = old_local_fallback
            if old_no_builtin is None:
                os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS"] = old_no_builtin

    def test_blindbox_uses_default_replicas_file_when_env_missing(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_default_replicas = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS")
        old_default_file = os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE")
        old_local_fallback = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_FALLBACK")
        old_no_builtin = os.environ.get("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS")
        os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
        os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
        os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            replicas_file = os.path.join(tmpdir, "blindbox_replicas.txt")
            with open(replicas_file, "w", encoding="utf-8") as f:
                f.write("r1.b32.i2p\n")
                f.write("r2.b32.i2p\n")
                f.write("r3.b32.i2p\n")
            os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE"] = replicas_file
            try:
                core = I2PChatCore(profile="alice")
                telemetry = core.get_blindbox_telemetry()
                self.assertTrue(telemetry["enabled"])
                self.assertEqual(str(telemetry["replicas_source"]), "file-default")
                self.assertEqual(int(telemetry["replicas"]), 3)
                self.assertTrue(bool(telemetry["use_sam_for_replicas"]))
            finally:
                if old_enabled is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
                if old_replicas is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
                if old_default_replicas is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS"] = old_default_replicas
                if old_default_file is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE"] = old_default_file
                if old_local_fallback is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_FALLBACK", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_LOCAL_FALLBACK"] = old_local_fallback
                if old_no_builtin is None:
                    os.environ.pop("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", None)
                else:
                    os.environ["I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS"] = old_no_builtin

    def test_blindbox_recv_candidates_skip_consumed_indexes(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        try:
            core = I2PChatCore(profile="alice")
            core._blindbox_state.recv_base = 10  # noqa: SLF001 - internal behavior test
            core._blindbox_state.recv_window = 5  # noqa: SLF001 - internal behavior test
            core._blindbox_state.consumed_recv = {11, 14}  # noqa: SLF001 - internal behavior test
            candidates = core._blindbox_recv_candidates()  # noqa: SLF001 - internal behavior test
            self.assertEqual(set(candidates), {10, 12, 13})
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas

    def test_previous_roots_pruned_by_limit(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_limit = os.environ.get("I2PCHAT_BLINDBOX_MAX_PREVIOUS_ROOTS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        os.environ["I2PCHAT_BLINDBOX_MAX_PREVIOUS_ROOTS"] = "2"
        try:
            core = I2PChatCore(profile="alice")
            now_ts = 2_000_000_000
            core._blindbox_prev_roots = [  # noqa: SLF001 - internal behavior test
                {"epoch": 1, "secret": b"a" * 32, "expires_at": now_ts + 10},
                {"epoch": 2, "secret": b"b" * 32, "expires_at": now_ts + 10},
                {"epoch": 3, "secret": b"c" * 32, "expires_at": now_ts + 10},
            ]
            with patch("time.time", return_value=float(now_ts)):
                core._blindbox_prune_previous_roots()  # noqa: SLF001 - internal behavior test
            epochs = [int(item["epoch"]) for item in core._blindbox_prev_roots]  # noqa: SLF001
            self.assertEqual(epochs, [3, 2])
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_limit is None:
                os.environ.pop("I2PCHAT_BLINDBOX_MAX_PREVIOUS_ROOTS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_MAX_PREVIOUS_ROOTS"] = old_limit


if __name__ == "__main__":
    unittest.main()
