"""
Pure logic for file/media transfer retry policy and UX state labels.

Qt layer in i2pchat/gui/main_qt.py drives retry scheduling; this module is
intentionally UI-free so tests can validate semantics without PyQt6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Transfer state constants
# ---------------------------------------------------------------------------

TRANSFER_STATE_PREPARING = "preparing"
TRANSFER_STATE_SENDING = "sending"
TRANSFER_STATE_PAUSED = "paused"
TRANSFER_STATE_FAILED = "failed"
TRANSFER_STATE_COMPLETED = "completed"

# Conditions that allow automatic retry
_RETRYABLE_REASONS = frozenset({
    "connection_lost",
    "timeout",
    "peer_busy",
})

# Conditions that are permanent failures
_NON_RETRYABLE_REASONS = frozenset({
    "peer_rejected",
    "file_not_found",
    "size_exceeded",
    "user_cancelled",
})


# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransferRetryPolicy:
    max_retries: int = 3
    backoff_base_sec: float = 2.0
    max_backoff_sec: float = 30.0


# ---------------------------------------------------------------------------
# Core retry logic
# ---------------------------------------------------------------------------

def should_retry_transfer(
    attempt: int,
    reason: str,
    policy: TransferRetryPolicy,
) -> tuple[bool, float]:
    """Return (retry, delay_sec) for a failed transfer.

    *attempt* is 1-based (first failure = attempt 1).
    delay_sec is 0.0 when retry is False.
    """
    if reason in _NON_RETRYABLE_REASONS:
        return False, 0.0
    if attempt > policy.max_retries:
        return False, 0.0
    if reason not in _RETRYABLE_REASONS:
        return False, 0.0

    # Exponential backoff: base * 2^(attempt-1), capped at max
    delay = min(
        policy.backoff_base_sec * (2.0 ** (attempt - 1)),
        policy.max_backoff_sec,
    )
    return True, delay


# ---------------------------------------------------------------------------
# User-friendly error messages
# ---------------------------------------------------------------------------

_FAILURE_MESSAGES: dict[str, str] = {
    "connection_lost": "Connection lost — will retry",
    "timeout": "Transfer timed out — will retry",
    "peer_busy": "Peer is busy — will retry shortly",
    "peer_rejected": "Recipient declined the transfer",
    "file_not_found": "File no longer exists on disk",
    "size_exceeded": "File exceeds the maximum allowed size",
    "user_cancelled": "Transfer cancelled",
    "peer_rejected": "Recipient declined the transfer",
}


def transfer_failure_reason(error: str) -> str:
    """Map an internal error key to a user-friendly message."""
    return _FAILURE_MESSAGES.get(error, f"Transfer failed: {error}")


# ---------------------------------------------------------------------------
# State label
# ---------------------------------------------------------------------------

def transfer_state_label(state: Optional[str]) -> str:
    """Return a human-readable label for a transfer state string."""
    return {
        TRANSFER_STATE_PREPARING: "Preparing",
        TRANSFER_STATE_SENDING: "Sending",
        TRANSFER_STATE_PAUSED: "Paused",
        TRANSFER_STATE_FAILED: "Failed",
        TRANSFER_STATE_COMPLETED: "Completed",
    }.get((state or "").strip().lower(), "")


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def transfer_progress_percent(received: int, total: int) -> float:
    """Return progress as a float in [0.0, 100.0]."""
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, received / total * 100.0))


def transfer_speed_label(bytes_per_sec: float) -> str:
    """Return a human-readable transfer speed string."""
    if bytes_per_sec < 0:
        return ""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def transfer_timeout_exceeded(
    elapsed_sec: float,
    received: int,
    timeout_sec: float = 60.0,
) -> bool:
    """Return True if a transfer appears stuck (no progress within timeout)."""
    return elapsed_sec >= timeout_sec and received <= 0
