"""
Smoke tests for unread UI wiring (ChatWindow) without a real I2P session.

Uses Qt offscreen platform so no visible window is required (CI / agents).

Manual QA checklist (two peers, notifications / 0.6.5 behavior)
----------------------------------------------------------------
1. Add two saved peers; make peer A the active chat (address field matches A, session as usual).
2. Put I2PChat in the background (another app focused). Receive a message for peer B
   (e.g. offline/BlindBox delivery for B, or connect to B after switching): expect tray title
   to reference B and window title suffix ``(N)`` for unread.
3. Focus I2PChat with Quiet mode OFF: while viewing chat A, a new message for A should not
   toast (same chat). Switch the address field to B; behavior should follow B as active key.
4. Turn Quiet mode ON (focused): expect no tray toasts and no notification sound while the
   window is focused, for any peer.
5. Turn Quiet mode OFF, minimize the window, receive a message for the connected peer:
   expect tray toast and sound according to sound / hide-body toggles.
"""

from __future__ import annotations

import os

# Before any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore
from PyQt6.QtCore import QMimeData, QUrl
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


def test_unread_no_bump_when_active_peer_matches_message(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    base = w._window_title_base
    ts = datetime.now(timezone.utc)

    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    monkeypatch.setattr(w, "_peer_chat_is_foreground", lambda: True)
    w.handle_message(
        ChatMessage(kind="peer", text="hi", timestamp=ts, source_peer=PEER_A)
    )
    assert w.windowTitle() == base
    assert w.tray_icon.toolTip() == base


def test_unread_bump_same_peer_when_not_foreground(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    base = w._window_title_base
    ts = datetime.now(timezone.utc)

    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    monkeypatch.setattr(w, "_peer_chat_is_foreground", lambda: False)
    w.handle_message(
        ChatMessage(kind="peer", text="hi", timestamp=ts, source_peer=PEER_A)
    )
    assert w.windowTitle() == f"{base} (1)"
    assert "1 unread" in (w.tray_icon.toolTip() or "")


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


def _patch_focused_chat_window(
    w: object, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """App + window considered active/focused (matches handle_notify gating)."""
    monkeypatch.setattr(w, "isActiveWindow", lambda: True)
    monkeypatch.setattr(w, "isMinimized", lambda: False)
    monkeypatch.setattr(
        qapp,
        "applicationState",
        lambda: QtCore.Qt.ApplicationState.ApplicationActive,
    )


def test_handle_notify_same_chat_suppresses_tray_when_focused(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    w._notify_quiet_mode = False
    ts = datetime.now(timezone.utc)
    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    _patch_focused_chat_window(w, qapp, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(w.tray_icon, "showMessage", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(w, "_play_notification_sound", lambda: None)

    w.handle_notify(
        ChatMessage(kind="peer", text="ping", timestamp=ts, source_peer=PEER_A)
    )
    assert calls == []


def test_handle_notify_cross_peer_shows_tray_when_focused(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Message for B while UI active key is A: not same_chat, tray path should run."""
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    w._notify_quiet_mode = False
    ts = datetime.now(timezone.utc)
    w.addr_edit.setText(PEER_A)
    w._on_addr_editing_finished_for_drafts()
    _patch_focused_chat_window(w, qapp, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(w.tray_icon, "showMessage", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(w, "_play_notification_sound", lambda: None)

    w.handle_notify(
        ChatMessage(kind="peer", text="hello", timestamp=ts, source_peer=PEER_B)
    )
    assert len(calls) == 1
    args, _kw = calls[0]
    title = args[0]
    assert "New message" in title
    assert "bbbbbb" in title


def test_privacy_mode_toggle_enables_hidden_notifications(qapp: QApplication) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    w._notify_hide_body = False
    w._notify_quiet_mode = False

    w._on_toggle_privacy_mode_clicked()

    assert w._privacy_mode_enabled is True
    assert w._notify_hide_body is True
    assert w._notify_quiet_mode is True
    assert w._privacy_mode_toggle_btn.text() == "Privacy mode: ON"


def test_message_input_drop_local_file_routes_to_sender(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from main_qt import THEME_DEFAULT, ChatWindow

    w = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    dropped = tmp_path / "note.txt"
    dropped.write_text("hello", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(w, "_send_local_path", lambda path: calls.append(path))

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QtGui.QDropEvent(
        QtCore.QPointF(5, 5),
        QtCore.Qt.DropAction.CopyAction,
        mime,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )

    w.input_edit.dropEvent(event)

    assert calls == [str(dropped)]
