import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat.core.i2p_chat_core import I2PChatCore


class BlindBoxCoreTelemetryTests(unittest.IsolatedAsyncioTestCase):
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
            self.assertEqual(str(telemetry["poll_mode"]), "idle")
            self.assertEqual(float(telemetry["poll_min_sec"]), 20.0)
            self.assertEqual(float(telemetry["poll_max_sec"]), 30.0)
            self.assertEqual(float(telemetry["poll_hot_sec"]), 2.5)
            self.assertEqual(float(telemetry["poll_hot_window_sec"]), 20.0)
            self.assertEqual(float(telemetry["poll_cooldown_sec"]), 5.0)
            self.assertEqual(float(telemetry["poll_cooldown_window_sec"]), 20.0)
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
        old_lookahead = os.environ.get("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        os.environ["I2PCHAT_BLINDBOX_RECV_LOOKAHEAD"] = "0"
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
            if old_lookahead is None:
                os.environ.pop("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_RECV_LOOKAHEAD"] = old_lookahead

    def test_blindbox_recv_candidates_default_lookahead_extends_window(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_lookahead = os.environ.get("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        os.environ.pop("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD", None)
        try:
            core = I2PChatCore(profile="alice")
            core._blindbox_state.recv_base = 3  # noqa: SLF001 - internal behavior test
            core._blindbox_state.recv_window = 16  # noqa: SLF001 - internal behavior test
            candidates = core._blindbox_recv_candidates()  # noqa: SLF001 - internal behavior test
            self.assertIn(3, candidates)
            self.assertIn(63, candidates)
            self.assertIn(66, candidates)
            self.assertNotIn(67, candidates)
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_lookahead is None:
                os.environ.pop("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_RECV_LOOKAHEAD"] = old_lookahead

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

    def test_blindbox_require_sam_rejects_direct_replicas(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_require_sam = os.environ.get("I2PCHAT_BLINDBOX_REQUIRE_SAM")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "127.0.0.1:19444"
        os.environ["I2PCHAT_BLINDBOX_REQUIRE_SAM"] = "1"
        try:
            with self.assertRaises(ValueError):
                I2PChatCore(profile="alice")
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_require_sam is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REQUIRE_SAM", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REQUIRE_SAM"] = old_require_sam

    def test_blindbox_local_direct_requires_token_by_default(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_local_token = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_TOKEN")
        old_allow_insecure = os.environ.get("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "127.0.0.1:19444"
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_TOKEN", None)
        os.environ.pop("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL", None)
        try:
            with self.assertRaises(ValueError):
                I2PChatCore(profile="alice")
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_local_token is None:
                os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_TOKEN", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_LOCAL_TOKEN"] = old_local_token
            if old_allow_insecure is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL"] = old_allow_insecure

    def test_blindbox_local_direct_opt_out_allows_insecure_mode(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_local_token = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_TOKEN")
        old_allow_insecure = os.environ.get("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "127.0.0.1:19444"
        os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_TOKEN", None)
        os.environ["I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL"] = "1"
        try:
            core = I2PChatCore(profile="alice")
            telemetry = core.get_blindbox_telemetry()
            self.assertFalse(bool(telemetry["local_auth_token_enabled"]))
            self.assertTrue(bool(telemetry["allow_insecure_local_replicas"]))
            self.assertTrue(bool(telemetry["has_loopback_replicas"]))
            self.assertTrue(bool(telemetry["insecure_local_mode"]))
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas
            if old_local_token is None:
                os.environ.pop("I2PCHAT_BLINDBOX_LOCAL_TOKEN", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_LOCAL_TOKEN"] = old_local_token
            if old_allow_insecure is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL"] = old_allow_insecure

    def test_blindbox_seen_hashes_are_bounded(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        old_limit = os.environ.get("I2PCHAT_BLINDBOX_MAX_SEEN_HASHES")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        os.environ["I2PCHAT_BLINDBOX_MAX_SEEN_HASHES"] = "3"
        try:
            core = I2PChatCore(profile="alice")
            core._remember_blindbox_seen_hash("h1")  # noqa: SLF001 - internal behavior test
            core._remember_blindbox_seen_hash("h2")  # noqa: SLF001 - internal behavior test
            core._remember_blindbox_seen_hash("h3")  # noqa: SLF001 - internal behavior test
            core._remember_blindbox_seen_hash("h4")  # noqa: SLF001 - internal behavior test
            self.assertEqual(core._blindbox_seen_hashes, {"h2", "h3", "h4"})  # noqa: SLF001
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
                os.environ.pop("I2PCHAT_BLINDBOX_MAX_SEEN_HASHES", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_MAX_SEEN_HASHES"] = old_limit

    def test_blindbox_poll_mode_transitions_from_hot_to_cooldown_to_idle(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            with patch("time.monotonic", return_value=100.0):
                core._trigger_blindbox_hot_poll("test")  # noqa: SLF001
            with patch("time.monotonic", return_value=110.0):
                self.assertEqual(core._blindbox_poll_mode(), "hot")  # noqa: SLF001
                self.assertEqual(core.get_blindbox_telemetry()["poll_mode"], "hot")
            with patch("time.monotonic", return_value=125.0):
                self.assertEqual(core._blindbox_poll_mode(), "cooldown")  # noqa: SLF001
                self.assertEqual(
                    core.get_blindbox_telemetry()["poll_mode"], "cooldown"
                )
            with patch("time.monotonic", return_value=145.0):
                self.assertEqual(core._blindbox_poll_mode(), "idle")  # noqa: SLF001
                self.assertEqual(core.get_blindbox_telemetry()["poll_mode"], "idle")

    async def test_successful_offline_send_triggers_hot_poll_window(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            peer = "g" * 52 + ".b32.i2p"
            core.current_peer_addr = peer
            core.my_dest = types.SimpleNamespace(base32="f" * 52)
            core._blindbox_root_secret = b"x" * 32  # noqa: SLF001
            core._blindbox_client = types.SimpleNamespace(put=AsyncMock())
            core._save_blindbox_state = lambda: None  # type: ignore[method-assign]
            with patch("time.monotonic", return_value=200.0):
                result = await core._send_text_via_blindbox("hello")  # noqa: SLF001
            self.assertIsNotNone(result)
            self.assertEqual(core._blindbox_poll_mode(now_mono=205.0), "hot")  # noqa: SLF001
            self.assertEqual(
                core._blindbox_poll_mode(now_mono=225.0), "cooldown"
            )  # noqa: SLF001
            self.assertTrue(core._blindbox_poll_wakeup.is_set())  # noqa: SLF001

    def test_normalize_peer_addr_rejects_forbidden_chars(self) -> None:
        core = I2PChatCore(profile="default")
        with self.assertRaises(ValueError):
            core._normalize_peer_addr("abc\nxyz")  # noqa: SLF001 - validation behavior
        with self.assertRaises(ValueError):
            core._normalize_peer_addr("abc=xyz")  # noqa: SLF001 - validation behavior

    def test_normalize_peer_addr_accepts_base32_host(self) -> None:
        core = I2PChatCore(profile="default")
        self.assertEqual(
            core._normalize_peer_addr("a" * 52),  # noqa: SLF001 - validation behavior
            ("a" * 52) + ".b32.i2p",
        )

    def test_normalize_peer_addr_extracts_from_pasted_line(self) -> None:
        core = I2PChatCore(profile="default")
        host52 = "a" * 52
        full = f"{host52}.b32.i2p"
        self.assertEqual(
            core._normalize_peer_addr(f"  My Addr: {full}  "),  # noqa: SLF001
            full,
        )
        self.assertEqual(
            core._normalize_peer_addr(f"peer {full} trailing"),  # noqa: SLF001
            full,
        )


if __name__ == "__main__":
    unittest.main()
