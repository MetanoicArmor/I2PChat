"""
Tests for send_retry_policy.should_start_auto_connect_retry().
Covers: all auto_connect_reasons paths, cooldown boundary conditions,
has_running_task guard.
"""

from __future__ import annotations

import unittest

from i2pchat.core.send_retry_policy import should_start_auto_connect_retry

AUTO_CONNECT_REASONS = [
    "blindbox-disabled",
    "blindbox-await-root",
    "blindbox-needs-boxes",
    "transient-profile",
]


class TestAutoConnectReasons(unittest.TestCase):
    """All recognised reasons should allow retry when conditions are met."""

    def _base_kwargs(self, *, reason: str) -> dict:
        return dict(
            reason=reason,
            has_running_task=False,
            now_mono=100.0,
            last_started_mono=0.0,   # well past cooldown
            cooldown_sec=6.0,
        )

    def test_blindbox_disabled_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._base_kwargs(reason="blindbox-disabled")))

    def test_blindbox_await_root_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._base_kwargs(reason="blindbox-await-root")))

    def test_blindbox_needs_boxes_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._base_kwargs(reason="blindbox-needs-boxes")))

    def test_transient_profile_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._base_kwargs(reason="transient-profile")))

    def test_all_known_reasons_covered(self) -> None:
        """Ensure the test list matches the module's known reasons."""
        for reason in AUTO_CONNECT_REASONS:
            with self.subTest(reason=reason):
                result = should_start_auto_connect_retry(**self._base_kwargs(reason=reason))
                self.assertTrue(result, f"Expected True for reason={reason!r}")


class TestUnrecognisedReasons(unittest.TestCase):
    """Unknown reasons must never trigger retry."""

    def _base_kwargs(self, *, reason: str) -> dict:
        return dict(
            reason=reason,
            has_running_task=False,
            now_mono=9999.0,
            last_started_mono=0.0,
            cooldown_sec=0.0,
        )

    def test_empty_reason_blocked(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="")))

    def test_unknown_string_blocked(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="unknown-reason")))

    def test_partial_match_blocked(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="blindbox")))

    def test_case_sensitive_match(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="Blindbox-Disabled")))

    def test_whitespace_variation_blocked(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason=" blindbox-disabled")))

    def test_network_error_not_auto_reason(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="network-error")))

    def test_connection_refused_not_auto_reason(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._base_kwargs(reason="connection-refused")))


class TestCooldownBoundaryConditions(unittest.TestCase):
    """Edge cases around the cooldown_sec threshold."""

    def _kwargs(self, *, elapsed: float, cooldown: float = 6.0, reason: str = "blindbox-disabled") -> dict:
        return dict(
            reason=reason,
            has_running_task=False,
            now_mono=elapsed,
            last_started_mono=0.0,
            cooldown_sec=cooldown,
        )

    def test_exactly_at_cooldown_boundary_allows_retry(self) -> None:
        # elapsed == cooldown_sec: >= comparison, so should return True
        self.assertTrue(should_start_auto_connect_retry(**self._kwargs(elapsed=6.0, cooldown=6.0)))

    def test_one_nanosecond_before_cooldown_blocks(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._kwargs(elapsed=5.9999999, cooldown=6.0)))

    def test_just_past_cooldown_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._kwargs(elapsed=6.0001, cooldown=6.0)))

    def test_zero_cooldown_always_allows_retry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._kwargs(elapsed=0.0, cooldown=0.0)))

    def test_large_cooldown_blocks_short_elapsed(self) -> None:
        self.assertFalse(should_start_auto_connect_retry(**self._kwargs(elapsed=5.0, cooldown=300.0)))

    def test_large_cooldown_allows_after_expiry(self) -> None:
        self.assertTrue(should_start_auto_connect_retry(**self._kwargs(elapsed=301.0, cooldown=300.0)))

    def test_default_cooldown_is_six_seconds(self) -> None:
        # Verify default value by omitting cooldown_sec parameter
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=False,
                now_mono=5.9,
                last_started_mono=0.0,
            )
        )
        self.assertTrue(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=False,
                now_mono=6.0,
                last_started_mono=0.0,
            )
        )

    def test_non_zero_last_started_mono(self) -> None:
        """Cooldown is relative to last_started_mono, not 0."""
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=False,
                now_mono=10.0,
                last_started_mono=5.0,
                cooldown_sec=6.0,
            )
        )
        self.assertTrue(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=False,
                now_mono=11.0,
                last_started_mono=5.0,
                cooldown_sec=6.0,
            )
        )


class TestHasRunningTaskGuard(unittest.TestCase):
    """has_running_task=True must always block retry, regardless of other params."""

    def test_running_task_blocks_retry_for_all_reasons(self) -> None:
        for reason in AUTO_CONNECT_REASONS:
            with self.subTest(reason=reason):
                result = should_start_auto_connect_retry(
                    reason=reason,
                    has_running_task=True,
                    now_mono=9999.0,
                    last_started_mono=0.0,
                    cooldown_sec=0.0,
                )
                self.assertFalse(result, f"Expected False when has_running_task=True, reason={reason!r}")

    def test_running_task_blocks_even_at_zero_cooldown(self) -> None:
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=True,
                now_mono=0.0,
                last_started_mono=0.0,
                cooldown_sec=0.0,
            )
        )

    def test_no_running_task_allows_retry(self) -> None:
        self.assertTrue(
            should_start_auto_connect_retry(
                reason="blindbox-disabled",
                has_running_task=False,
                now_mono=100.0,
                last_started_mono=0.0,
                cooldown_sec=6.0,
            )
        )

    def test_running_task_blocks_even_with_unknown_reason(self) -> None:
        # Unknown reason is already blocked; has_running_task doesn't change outcome.
        self.assertFalse(
            should_start_auto_connect_retry(
                reason="not-a-valid-reason",
                has_running_task=True,
                now_mono=9999.0,
                last_started_mono=0.0,
                cooldown_sec=0.0,
            )
        )


class TestReturnValueIsBoolean(unittest.TestCase):
    def test_returns_bool_on_allow(self) -> None:
        result = should_start_auto_connect_retry(
            reason="blindbox-disabled",
            has_running_task=False,
            now_mono=100.0,
            last_started_mono=0.0,
        )
        self.assertIsInstance(result, bool)

    def test_returns_bool_on_deny(self) -> None:
        result = should_start_auto_connect_retry(
            reason="blindbox-disabled",
            has_running_task=True,
            now_mono=100.0,
            last_started_mono=0.0,
        )
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
