"""Геометрия всплывающих окон относительно якоря (без зависимости от main_qt)."""

from __future__ import annotations

import sys
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets


def disable_dwm_rounded_frame(widget: QtWidgets.QWidget) -> None:
    """Windows 11: убрать системную рамку/скругления DWM вокруг popup-окна.

    Без этого DWM рисует прямоугольную 1 px кайму и/или свои скругления,
    которые не совпадают с нашим QPainter-рендером и «торчат» наружу.
    Безопасно вызывать на любой ОС — на не-Windows просто ничего не делает.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33; DWMWCP_DONOTROUND = 1
        pref = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref),
        )
    except Exception:
        pass


def win_rounded_window_region(width: int, height: int, radius: float) -> QtGui.QRegion:
    """Регион для QWidget.setMask: обрезка Win32-popup по скруглению (убирает «острые» углы подложки).

    NOTE: бинарная маска неизбежно даёт «ступеньки» — на Windows popup-ы
    теперь используют WA_TranslucentBackground + QSS border-radius (anti-aliased),
    а эта функция остаётся как fallback для Linux без композитора.
    """
    path = QtGui.QPainterPath()
    path.addRoundedRect(
        QtCore.QRectF(0, 0, float(width), float(height)), radius, radius
    )
    poly_f = path.toFillPolygon()
    return QtGui.QRegion(poly_f.toPolygon())


def apply_rounded_rect_mask(widget: QtWidgets.QWidget, radius: float) -> None:
    """Обрезка прямоугольного виджета по скруглённому контуру (убирает артефакты углов на прозрачном фоне)."""
    w, h = widget.width(), widget.height()
    if w < 2 or h < 2:
        return
    widget.setMask(win_rounded_window_region(w, h, radius))


def apply_win_popup_rounded_mask(widget: QtWidgets.QWidget, radius: float) -> None:
    if not sys.platform.startswith("win"):
        return
    apply_rounded_rect_mask(widget, radius)


def paint_popup_rounded_bg(
    widget: QtWidgets.QWidget,
    bg: QtGui.QColor,
    border: QtGui.QColor,
    radius: float,
) -> None:
    """Anti-aliased rounded background + 1 px border.

    Explicitly clears to transparent first (CompositionMode_Source) so that
    corners are guaranteed transparent even when the backing pixmap is not
    pre-filled with alpha=0 (e.g. QGraphicsDropShadowEffect on Windows).
    """
    p = QtGui.QPainter(widget)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
    p.fillRect(widget.rect(), QtGui.QColor(0, 0, 0, 0))
    p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
    r = QtCore.QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    p.setPen(QtGui.QPen(border, 1.0))
    p.setBrush(QtGui.QBrush(bg))
    p.drawRoundedRect(r, radius, radius)
    p.end()


def update_popup_rounded_mask(widget: QtWidgets.QWidget, radius: float) -> None:
    """Integer-based mask (fallback for non-composited Linux desktops)."""
    w, h = widget.width(), widget.height()
    if w < 2 or h < 2:
        return
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(0, 0, float(w), float(h)), radius, radius)
    widget.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))


def popup_screen_for_anchor(anchor: QtWidgets.QWidget) -> Optional[QtGui.QScreen]:
    """Экран, на котором находится якорь (несколько мониторов); иначе primary."""
    if anchor.width() > 0 and anchor.height() > 0:
        center = anchor.mapToGlobal(
            QtCore.QPoint(anchor.width() // 2, anchor.height() // 2)
        )
        s = QtGui.QGuiApplication.screenAt(center)
        if s is not None:
            return s
    for pt in (
        anchor.mapToGlobal(QtCore.QPoint(0, 0)),
        anchor.mapToGlobal(
            QtCore.QPoint(max(0, anchor.width() - 1), max(0, anchor.height() - 1))
        ),
    ):
        s = QtGui.QGuiApplication.screenAt(pt)
        if s is not None:
            return s
    return QtGui.QGuiApplication.primaryScreen()


def clamp_popup_top_left_to_available_geometry(
    top_left: QtCore.QPoint, popup_w: int, popup_h: int, geom: QtCore.QRect
) -> QtCore.QPoint:
    """Удерживает левый верх угла popup внутри availableGeometry."""
    x = max(geom.left(), min(top_left.x(), geom.right() - popup_w + 1))
    y = max(geom.top(), min(top_left.y(), geom.bottom() - popup_h + 1))
    return QtCore.QPoint(x, y)


def embedded_popup_top_left_in_window(
    anchor: QtWidgets.QWidget,
    window: QtWidgets.QWidget,
    popup_w: int,
    popup_h: int,
    *,
    vertical_gap: int = 4,
    margin: int = 8,
    center_under_anchor: bool = False,
) -> QtCore.QPoint:
    """
    Левый верх встроенного popup в координатах ``window`` (родитель — то же окно).
    Под якорём; если снизу не хватает места — над якорём; не вылезает за края клиентской области.
    center_under_anchor: горизонтально центрировать относительно якоря (полезно, если ширина popup ≠ якоря).
    """
    wrect = window.rect()
    p0 = anchor.mapTo(window, QtCore.QPoint(0, 0))
    below_y = p0.y() + anchor.height() + vertical_gap
    above_y = p0.y() - popup_h - vertical_gap
    bottom_limit = wrect.bottom() - margin
    top_limit = wrect.top() + margin

    if below_y + popup_h <= bottom_limit:
        y = below_y
    elif above_y >= top_limit:
        y = above_y
    else:
        y = max(top_limit, min(below_y, bottom_limit - popup_h))

    if center_under_anchor:
        x = int(p0.x() + max(0, (anchor.width() - popup_w) // 2))
    else:
        x = int(p0.x())
    x = max(margin, min(x, int(wrect.right()) - margin - popup_w + 1))
    return QtCore.QPoint(x, int(y))


def global_position_popup_below_anchor(
    anchor: QtWidgets.QWidget,
    popup_w: int,
    popup_h: int,
    *,
    vertical_gap: int,
    align_right: bool,
) -> QtCore.QPoint:
    """
    Глобальный top-left: сначала под якорем; если снизу не помещается — над якорем.
    align_right=True: правый край popup совпадает с правым краем якоря.
    """
    if align_right:
        x_local = max(0, anchor.width() - popup_w)
    else:
        x_local = 0
    pos_below = anchor.mapToGlobal(
        QtCore.QPoint(x_local, anchor.height() + vertical_gap)
    )
    pos_above = anchor.mapToGlobal(
        QtCore.QPoint(x_local, -popup_h - vertical_gap)
    )

    screen = popup_screen_for_anchor(anchor)
    if screen is None:
        return pos_below
    geom = screen.availableGeometry()
    max_top_for_below = geom.bottom() - popup_h + 1

    if pos_below.y() > max_top_for_below and pos_above.y() >= geom.top():
        pos = pos_above
    else:
        pos = pos_below

    return clamp_popup_top_left_to_available_geometry(pos, popup_w, popup_h, geom)
