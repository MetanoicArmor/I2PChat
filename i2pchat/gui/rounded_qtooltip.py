"""Rounded tooltips: PyQt6 monkey-patch of QToolTip.showText often misses the native Qt path.

We intercept QEvent.Type.ToolTip (QHelpEvent) in QApplication.notify and paint our own QWidget."""

from __future__ import annotations

import sys
from typing import Callable, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from i2pchat.gui.popup_geometry import clamp_popup_top_left_to_available_geometry

_RADIUS_PX = 12.0
_MAX_LABEL_WIDTH = 440

_panel: Optional["RoundedTooltipWindow"] = None
_orig_show_text: Optional[Callable[..., None]] = None
_orig_hide_text: Optional[Callable[[], None]] = None
_current_owner: Optional[QtWidgets.QWidget] = None


def _tooltip_outline_color(bg: QtGui.QColor, fg: QtGui.QColor) -> QtGui.QColor:
    """Тонкий контур: слегка подмешиваем цвет текста к фону (без второй «коробки»)."""
    t = 0.18
    return QtGui.QColor(
        int(round(bg.red() * (1.0 - t) + fg.red() * t)),
        int(round(bg.green() * (1.0 - t) + fg.green() * t)),
        int(round(bg.blue() * (1.0 - t) + fg.blue() * t)),
    )


def _clamp_global_top_left(top_left: QtCore.QPoint, w: int, h: int) -> QtCore.QPoint:
    screen = QtGui.QGuiApplication.screenAt(top_left)
    if screen is None:
        screen = QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        return top_left
    return clamp_popup_top_left_to_available_geometry(
        top_left, w, h, screen.availableGeometry()
    )


def _tooltip_window_flags() -> QtCore.Qt.WindowType:
    flags = (
        QtCore.Qt.WindowType.ToolTip
        | QtCore.Qt.WindowType.FramelessWindowHint
        | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        | QtCore.Qt.WindowType.WindowStaysOnTopHint
    )
    # macOS: drop shadow is drawn as a square plate around the window — looks like an outer frame.
    if sys.platform == "darwin":
        flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
    return flags


class RoundedTooltipWindow(QtWidgets.QWidget):
    """Own top-level tip: one rounded fill, no QFrame border, no native QTipLabel."""

    def __init__(self) -> None:
        super().__init__(None, _tooltip_window_flags())
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._radius = _RADIUS_PX
        self._label = QtWidgets.QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(_MAX_LABEL_WIDTH)
        self._label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.NoTextInteraction
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(0)
        lay.addWidget(self._label)
        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def _update_mask(self) -> None:
        r = self.rect()
        if r.width() < 2 or r.height() < 2:
            return
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(r), self._radius, self._radius)
        self.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        app = QtWidgets.QApplication.instance()
        pal = app.palette() if app else self.palette()
        bg = pal.color(
            QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.ToolTipBase
        )
        fg = pal.color(
            QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.ToolTipText
        )
        edge = _tooltip_outline_color(bg, fg)
        pen = QtGui.QPen(edge)
        pen.setWidth(1)
        pen.setCosmetic(True)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, self._radius, self._radius)
        p.end()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_mask()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._update_mask)

    def present(
        self,
        global_top_left: QtCore.QPoint,
        text: str,
        msec: int,
        *,
        owner: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        app = QtWidgets.QApplication.instance()
        pal = app.palette() if app else self.palette()
        fg = pal.color(
            QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.ToolTipText
        )
        self._label.setStyleSheet(
            f"color: {fg.name(QtGui.QColor.NameFormat.HexRgb)}; "
            "background: transparent; border: none; margin: 0; padding: 0;"
        )
        global _current_owner
        _current_owner = owner
        self._label.setText(text)
        self.adjustSize()
        w, h = self.width(), self.height()
        pos = _clamp_global_top_left(global_top_left, w, h)
        self.move(pos)
        self._update_mask()
        self.show()
        self.raise_()
        self._hide_timer.stop()
        # Qt hides msec=-1 tips on mouse move; we approximate with a long timeout.
        hide_ms = msec if msec > 0 else 30000
        self._hide_timer.start(hide_ms)


def _ensure_panel() -> RoundedTooltipWindow:
    global _panel
    if _panel is None:
        _panel = RoundedTooltipWindow()
    return _panel


def show_rounded_tooltip_at(
    global_pos: QtCore.QPoint,
    text: str,
    *,
    msec: int = -1,
    owner: Optional[QtWidgets.QWidget] = None,
) -> None:
    """Show the shared rounded tip at global coordinates (for custom ToolTip handlers)."""
    if not text.strip():
        hide_rounded_tooltip()
        return
    _ensure_panel().present(global_pos, text.strip(), msec, owner=owner)


def hide_rounded_tooltip() -> None:
    global _panel, _current_owner
    _current_owner = None
    if _panel is not None:
        _panel._hide_timer.stop()
        _panel.hide()


def _fallback_show_text(
    pos: QtCore.QPoint,
    text: Optional[str],
    widget: Optional[QtWidgets.QWidget] = None,
    rect: QtCore.QRect = QtCore.QRect(),
    msecShowTime: int = -1,
) -> None:
    if not text:
        hide_rounded_tooltip()
        return
    if widget is not None and not rect.isNull():
        br = widget.mapToGlobal(rect.bottomLeft())
        global_pos = QtCore.QPoint(br.x(), br.y() + 2)
    else:
        global_pos = pos
    _ensure_panel().present(global_pos, text, msecShowTime, owner=widget)


def _fallback_hide_text() -> None:
    hide_rounded_tooltip()


def _install_monkey_patch_if_needed() -> None:
    global _orig_show_text, _orig_hide_text
    if _orig_show_text is not None:
        return
    _orig_show_text = QtWidgets.QToolTip.showText
    _orig_hide_text = QtWidgets.QToolTip.hideText
    QtWidgets.QToolTip.showText = _fallback_show_text  # type: ignore[assignment]
    QtWidgets.QToolTip.hideText = _fallback_hide_text  # type: ignore[assignment]


_HIDE_ON_EVENTS = frozenset((
    QtCore.QEvent.Type.Leave,
    QtCore.QEvent.Type.Hide,
    QtCore.QEvent.Type.Close,
    QtCore.QEvent.Type.MouseMove,
    QtCore.QEvent.Type.MouseButtonPress,
    QtCore.QEvent.Type.Wheel,
    QtCore.QEvent.Type.KeyPress,
    QtCore.QEvent.Type.FocusOut,
    QtCore.QEvent.Type.WindowDeactivate,
))


class I2PChatQApplication(QtWidgets.QApplication):
    """Intercept standard QWidget tooltips before Qt opens native QTipLabel."""

    _tooltip_intercept_enabled: bool = False

    def enable_tooltip_intercept(self) -> None:
        self._tooltip_intercept_enabled = True

    def notify(self, receiver: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not self._tooltip_intercept_enabled:
            return super().notify(receiver, event)

        et = event.type()
        if et == QtCore.QEvent.Type.ToolTip and isinstance(event, QtGui.QHelpEvent) and isinstance(receiver, QtWidgets.QWidget):
            tip = (receiver.toolTip() or "").strip()
            if tip:
                _ensure_panel().present(event.globalPos(), tip, -1, owner=receiver)
                return True
            hide_rounded_tooltip()
            return True

        if _panel is not None and _panel.isVisible() and et in _HIDE_ON_EVENTS:
            hide_rounded_tooltip()

        return super().notify(receiver, event)


def apply_tooltip_handling(app: Optional[QtWidgets.QApplication] = None) -> None:
    """If app is not I2PChatQApplication (e.g. tests), fall back to QToolTip.showText patch."""
    a = app or QtWidgets.QApplication.instance()
    if a is None:
        return
    if not isinstance(a, I2PChatQApplication):
        _install_monkey_patch_if_needed()
