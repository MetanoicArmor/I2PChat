"""
Pure logic for notification display flags (backlog: notification settings).

Qt layer in main_qt.py applies results to tray toasts and sound playback.
"""

from __future__ import annotations

from typing import Literal

NotifyKind = Literal["peer", "connect"]


def notification_body_for_display(
    *,
    kind: NotifyKind,
    preview: str,
    hide_body: bool,
) -> str:
    if not hide_body:
        return preview
    if kind == "peer":
        return "New message"
    return "Incoming connection"


def should_suppress_for_quiet_focus(
    *,
    quiet_mode: bool,
    is_app_active: bool,
    is_window_active: bool,
) -> bool:
    """When True, skip tray message and sound (user is focused on the app window)."""
    return quiet_mode and is_app_active and is_window_active


def should_show_tray_message(
    *,
    quiet_mode: bool,
    is_app_active: bool,
    is_window_active: bool,
) -> bool:
    return not should_suppress_for_quiet_focus(
        quiet_mode=quiet_mode,
        is_app_active=is_app_active,
        is_window_active=is_window_active,
    )


def should_play_notification_sound(
    *,
    sound_enabled: bool,
    quiet_mode: bool,
    is_app_active: bool,
    is_window_active: bool,
) -> bool:
    if not sound_enabled:
        return False
    return not should_suppress_for_quiet_focus(
        quiet_mode=quiet_mode,
        is_app_active=is_app_active,
        is_window_active=is_window_active,
    )
