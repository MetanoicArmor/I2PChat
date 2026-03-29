"""
Smoke tests for unread UI wiring (ChatWindow) without a real I2P session.

Uses Qt offscreen platform so no visible window is required (CI / agents).
"""

from __future__ import annotations

import os

# Before any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from i2p_chat_core import ChatMessage


PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_unread_bump_when_message_peer_differs_from_addr_field(qapp: QApplication) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    base = w._window_title_base
    ts = datetime.now(timezone.utc)

    w.addr_edit.setText(PEER_B)
    w._on_addr_editing_finished_for_drafts()
    w.handle_message(
        ChatMessage(kind="peer", text="hi", timestamp=ts, source_peer=PEER_A)
    )
    assert w.windowTitle() == f"{base} (1)"
    tip = w.tray_icon.toolTip() or ""
    assert "1 unread" in tip

    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    assert w.windowTitle() == base
    assert w.tray_icon.toolTip() == base

    # Do not w.close(): closeEvent expects qasync loop + shutdown (see main_qt.ChatWindow).


def test_unread_no_bump_when_active_peer_matches_message(qapp: QApplication) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    base = w._window_title_base
    ts = datetime.now(timezone.utc)

    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    w.handle_message(
        ChatMessage(kind="peer", text="hi", timestamp=ts, source_peer=PEER_A)
    )
    assert w.windowTitle() == base
    assert w.tray_icon.toolTip() == base


def test_handle_notify_peer_same_chat_does_not_throw(qapp: QApplication) -> None:
    """Sanity: notify path runs without exception (full toast behavior is OS-specific)."""
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    ts = datetime.now(timezone.utc)
    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    w.handle_notify(
        ChatMessage(kind="peer", text="ping", timestamp=ts, source_peer=PEER_A)
    )
