"""Unit tests for notification_prefs (issue 0.6.5 notification settings)."""

from __future__ import annotations

import pytest

from i2pchat.presentation.notification_prefs import (
    NotifyKind,
    notification_body_for_display,
    should_play_notification_sound,
    should_show_tray_message,
    should_suppress_for_quiet_focus,
)


@pytest.mark.parametrize(
    "kind,preview,hide,expected",
    [
        ("peer", "hello world", False, "hello world"),
        ("peer", "hello world", True, "New message"),
        ("connect", "x.b32.i2p connected", False, "x.b32.i2p connected"),
        ("connect", "Peer connected", True, "Incoming connection"),
    ],
)
def test_notification_body_for_display(
    kind: NotifyKind, preview: str, hide: bool, expected: str
) -> None:
    assert (
        notification_body_for_display(kind=kind, preview=preview, hide_body=hide)
        == expected
    )


@pytest.mark.parametrize(
    "quiet,app,win,suppress",
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_should_suppress_for_quiet_focus(
    quiet: bool, app: bool, win: bool, suppress: bool
) -> None:
    assert (
        should_suppress_for_quiet_focus(
            quiet_mode=quiet,
            is_app_active=app,
            is_window_active=win,
        )
        == suppress
    )


@pytest.mark.parametrize(
    "quiet,app,win,show",
    [
        (True, True, True, False),
        (True, True, False, True),
        (False, True, True, True),
    ],
)
def test_should_show_tray_message(quiet: bool, app: bool, win: bool, show: bool) -> None:
    assert (
        should_show_tray_message(
            quiet_mode=quiet,
            is_app_active=app,
            is_window_active=win,
        )
        == show
    )


@pytest.mark.parametrize(
    "sound,quiet,app,win,play",
    [
        (False, False, False, False, False),
        (True, True, True, True, False),
        (True, False, True, True, True),
        (True, True, True, False, True),
    ],
)
def test_should_play_notification_sound(
    sound: bool, quiet: bool, app: bool, win: bool, play: bool
) -> None:
    assert (
        should_play_notification_sound(
            sound_enabled=sound,
            quiet_mode=quiet,
            is_app_active=app,
            is_window_active=win,
        )
        == play
    )
