"""Геометрия всплывающих окон относительно якоря (без зависимости от main_qt)."""

from __future__ import annotations

import sys
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets


def win_rounded_window_region(width: int, height: int, radius: float) -> QtGui.QRegion:
    """Регион для QWidget.setMask: обрезка Win32-popup по скруглению (убирает «острые» углы подложки)."""
    path = QtGui.QPainterPath()
    path.addRoundedRect(
        QtCore.QRectF(0, 0, float(width), float(height)), radius, radius
    )
    return QtGui.QRegion(path.toFillPolygon())


def apply_win_popup_rounded_mask(widget: QtWidgets.QWidget, radius: float) -> None:
    if not sys.platform.startswith("win"):
        return
    w, h = widget.width(), widget.height()
    if w < 2 or h < 2:
        return
    widget.setMask(win_rounded_window_region(w, h, radius))


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
