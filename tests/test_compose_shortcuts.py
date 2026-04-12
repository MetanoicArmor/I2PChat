from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _press_enter(
    widget: object,
    *,
    modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
) -> list[bool]:
    from i2pchat.gui.main_qt import MessageInputEdit

    assert isinstance(widget, MessageInputEdit)
    calls: list[bool] = []
    widget.sendRequested.connect(lambda: calls.append(True))
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Return,
        modifiers,
    )
    widget.keyPressEvent(event)
    return calls


def test_load_compose_enter_sends_defaults_to_macos(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt, "_load_ui_prefs", lambda: {})
    monkeypatch.setattr(main_qt.sys, "platform", "darwin")

    assert main_qt.load_compose_enter_sends() is True


def test_load_compose_enter_sends_respects_explicit_false(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt, "_load_ui_prefs", lambda: {"compose_enter_sends": False})
    monkeypatch.setattr(main_qt.sys, "platform", "darwin")

    assert main_qt.load_compose_enter_sends() is False


def test_save_compose_enter_sends_persists_false(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from i2pchat.gui import main_qt

    saved: dict[str, object] = {}
    monkeypatch.setattr(main_qt, "_load_ui_prefs", lambda: {})
    monkeypatch.setattr(main_qt, "_save_ui_prefs", lambda data: saved.update(data))

    main_qt.save_compose_enter_sends(False)

    assert saved["compose_enter_sends"] is False


def test_message_input_windows_linux_mode_uses_ctrl_enter_to_send(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt.sys, "platform", "linux")
    widget = main_qt.MessageInputEdit()
    widget.set_enter_sends(False)

    assert _press_enter(widget) == []
    assert widget.toPlainText() == "\n"

    widget.clear()
    assert _press_enter(
        widget,
        modifiers=QtCore.Qt.KeyboardModifier.ShiftModifier,
    ) == []
    assert widget.toPlainText() == "\n"

    widget.clear()
    assert _press_enter(
        widget,
        modifiers=QtCore.Qt.KeyboardModifier.ControlModifier,
    ) == [True]
    assert widget.toPlainText() == ""


def test_message_input_macos_mode_accepts_command_or_ctrl_enter(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt.sys, "platform", "darwin")
    widget = main_qt.MessageInputEdit()
    widget.set_enter_sends(False)

    assert _press_enter(widget) == []
    assert widget.toPlainText() == "\n"

    widget.clear()
    assert _press_enter(
        widget,
        modifiers=QtCore.Qt.KeyboardModifier.ControlModifier,
    ) == [True]
    assert widget.toPlainText() == ""

    widget.clear()
    assert _press_enter(
        widget,
        modifiers=QtCore.Qt.KeyboardModifier.MetaModifier,
    ) == [True]
    assert widget.toPlainText() == ""


def test_message_input_enter_sends_mode_keeps_shift_for_new_line(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt.sys, "platform", "linux")
    widget = main_qt.MessageInputEdit()
    widget.set_enter_sends(True)

    assert _press_enter(widget) == [True]
    assert widget.toPlainText() == ""

    assert _press_enter(
        widget,
        modifiers=QtCore.Qt.KeyboardModifier.ShiftModifier,
    ) == []
    assert widget.toPlainText() == "\n"


def test_compose_placeholder_mentions_platform_shortcuts(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    monkeypatch.setattr(main_qt.sys, "platform", "darwin")
    assert "Command+Enter or Ctrl+Enter" in main_qt._compose_input_placeholder_text(
        enter_sends=False
    )

    monkeypatch.setattr(main_qt.sys, "platform", "linux")
    assert "Ctrl+Enter = send" in main_qt._compose_input_placeholder_text(
        enter_sends=False
    )


def test_theme_switch_click_flips_effective_theme_from_auto(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    saved: list[str] = []
    monkeypatch.setattr(main_qt, "_system_prefers_dark", lambda: False)
    monkeypatch.setattr(main_qt, "save_theme", lambda pref: saved.append(pref))

    window = main_qt.ChatWindow(
        profile="default",
        theme_id=main_qt.THEME_PREF_AUTO,
    )

    assert window._theme_preference == main_qt.THEME_PREF_AUTO
    assert window.theme_id == "ligth"

    window.on_theme_switch_clicked()
    assert window._theme_preference == "night"
    assert window.theme_id == "night"
    assert saved[-1] == "night"

    window.on_theme_switch_clicked()
    assert window._theme_preference == "ligth"
    assert window.theme_id == "ligth"
    assert saved[-1] == "ligth"


def test_save_theme_round_trips_across_reload(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    stored: dict[str, object] = {}
    monkeypatch.setattr(main_qt, "_load_ui_prefs", lambda: dict(stored))
    monkeypatch.setattr(
        main_qt,
        "_save_ui_prefs",
        lambda data: stored.update(dict(data)),
    )

    main_qt.save_theme("night")
    assert main_qt.load_saved_theme() == "night"

    main_qt.save_theme(main_qt.THEME_PREF_AUTO)
    assert main_qt.load_saved_theme() == main_qt.THEME_PREF_AUTO


def test_system_prefers_dark_uses_macos_defaults_fallback(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from i2pchat.gui import main_qt

    class _UnknownHints:
        def colorScheme(self) -> QtCore.Qt.ColorScheme:
            return QtCore.Qt.ColorScheme.Unknown

    class _DummyApp:
        def styleHints(self) -> _UnknownHints:
            return _UnknownHints()

        def palette(self) -> QtGui.QPalette:
            pal = QtGui.QPalette()
            pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#ffffff"))
            return pal

    monkeypatch.setattr(main_qt.sys, "platform", "darwin")
    monkeypatch.setattr(
        main_qt.QtGui.QGuiApplication,
        "instance",
        lambda: _DummyApp(),
    )
    monkeypatch.setattr(
        main_qt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Dark\n",
            stderr="",
        ),
    )

    assert main_qt._system_prefers_dark() is True
