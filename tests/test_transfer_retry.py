"""Unit tests for transfer_retry module (issue #25)."""

from __future__ import annotations

import math
import unittest

from transfer_retry import (
    TRANSFER_STATE_COMPLETED,
    TRANSFER_STATE_FAILED,
    TRANSFER_STATE_PAUSED,
    TRANSFER_STATE_PREPARING,
    TRANSFER_STATE_SENDING,
    TransferRetryPolicy,
    should_retry_transfer,
    transfer_failure_reason,
    transfer_progress_percent,
    transfer_speed_label,
    transfer_state_label,
    transfer_timeout_exceeded,
)


class TestPolicyDefaults(unittest.TestCase):
    def test_defaults(self):
        p = TransferRetryPolicy()
        self.assertEqual(p.max_retries, 3)
        self.assertAlmostEqual(p.backoff_base_sec, 2.0)
        self.assertAlmostEqual(p.max_backoff_sec, 30.0)


class TestShouldRetryTransfer(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            (1, "connection_lost", True, 2.0),
            (2, "connection_lost", True, 4.0),
            (3, "connection_lost", True, 8.0),
            (4, "connection_lost", False, 0.0),   # exceeds max_retries=3
            (1, "timeout", True, 2.0),
            (1, "peer_busy", True, 2.0),
            (1, "peer_rejected", False, 0.0),      # non-retryable
            (1, "file_not_found", False, 0.0),
            (1, "size_exceeded", False, 0.0),
            (1, "unknown_error", False, 0.0),      # unknown → not retryable
        ]
        policy = TransferRetryPolicy()
        for attempt, reason, retry, delay in cases:
            with self.subTest(attempt=attempt, reason=reason):
                got_retry, got_delay = should_retry_transfer(attempt, reason, policy)
                self.assertEqual(got_retry, retry)
                self.assertAlmostEqual(got_delay, delay)

    def test_backoff_capped_at_max(self):
        policy = TransferRetryPolicy(max_retries=10, backoff_base_sec=2.0, max_backoff_sec=30.0)
        _, delay = should_retry_transfer(10, "connection_lost", policy)
        self.assertAlmostEqual(delay, 30.0)

    def test_custom_policy(self):
        policy = TransferRetryPolicy(max_retries=1, backoff_base_sec=5.0, max_backoff_sec=5.0)
        retry, delay = should_retry_transfer(1, "timeout", policy)
        self.assertTrue(retry)
        self.assertAlmostEqual(delay, 5.0)
        retry2, _ = should_retry_transfer(2, "timeout", policy)
        self.assertFalse(retry2)


class TestTransferFailureReason(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            ("connection_lost", "retry"),
            ("timeout", "retry"),
            ("peer_busy", "retry"),
            ("peer_rejected", "declined"),
            ("file_not_found", "longer exists"),
            ("size_exceeded", "maximum"),
            ("some_unknown", "some_unknown"),
        ]
        for error, substring in cases:
            with self.subTest(error=error):
                msg = transfer_failure_reason(error)
                self.assertTrue(
                    substring in msg.lower() or substring in msg,
                    f"{substring!r} not found in {msg!r}",
                )


class TestTransferStateLabel(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            (TRANSFER_STATE_PREPARING, "Preparing"),
            (TRANSFER_STATE_SENDING, "Sending"),
            (TRANSFER_STATE_PAUSED, "Paused"),
            (TRANSFER_STATE_FAILED, "Failed"),
            (TRANSFER_STATE_COMPLETED, "Completed"),
            ("unknown", ""),
            (None, ""),
            ("", ""),
            ("  SENDING  ", "Sending"),
        ]
        for state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(transfer_state_label(state), expected)


class TestTransferProgressPercent(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            (0, 100, 0.0),
            (50, 100, 50.0),
            (100, 100, 100.0),
            (0, 0, 0.0),
            (-1, 100, 0.0),
            (200, 100, 100.0),  # clamped
        ]
        for received, total, expected in cases:
            with self.subTest(received=received, total=total):
                self.assertAlmostEqual(transfer_progress_percent(received, total), expected)


class TestTransferSpeedLabel(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            (512, "B/s"),
            (2048, "KB/s"),
            (2 * 1024 * 1024, "MB/s"),
            (-1, ""),
        ]
        for bps, substring in cases:
            with self.subTest(bps=bps):
                label = transfer_speed_label(bps)
                self.assertIn(substring, label)


class TestTransferTimeoutExceeded(unittest.TestCase):
    def test_not_exceeded(self):
        self.assertFalse(transfer_timeout_exceeded(30.0, 0, timeout_sec=60.0))

    def test_exceeded_no_progress(self):
        self.assertTrue(transfer_timeout_exceeded(61.0, 0, timeout_sec=60.0))

    def test_not_exceeded_with_progress(self):
        self.assertFalse(transfer_timeout_exceeded(61.0, 100, timeout_sec=60.0))


if __name__ == "__main__":
    unittest.main()
