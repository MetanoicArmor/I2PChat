"""
Pure logic for when the UI may auto-retry outbound Connect after send failures.

Qt layer in main_qt.py calls this; tests import without pulling PyQt6.
"""

from __future__ import annotations


def should_start_auto_connect_retry(
    *,
    reason: str,
    has_running_task: bool,
    now_mono: float,
    last_started_mono: float,
    cooldown_sec: float = 6.0,
) -> bool:
    auto_connect_reasons = {
        "blindbox-disabled",
        "blindbox-await-root",
        "blindbox-needs-boxes",
        "transient-profile",
    }
    if reason not in auto_connect_reasons:
        return False
    if has_running_task:
        return False
    return (now_mono - last_started_mono) >= cooldown_sec
