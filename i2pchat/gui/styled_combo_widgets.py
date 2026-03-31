"""
Выпадающий список в стиле экрана выбора профиля: без нативного QComboBox popup,
кастомный ProfileComboPopup + стрелка.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from i2pchat.gui.popup_geometry import global_position_popup_below_anchor


def theme_for_styled_combo(theme_id: Optional[str]) -> str:
    raw = str(theme_id or "").strip().lower()
    if raw in {"macos", "light"}:
        raw = "ligth"
    if raw == "night":
        return "night"
    return "ligth"


def embedded_combo_row_stylesheet(object_name: str, theme_id: str) -> str:
    """Те же правила QComboBox, что в THEMES dialog_stylesheet (встраивание без QDialog)."""
    on = object_name.strip()
    if theme_id == "night":
        return f"""
            #{on} QComboBox {{
                background: #1f1f23;
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                color: #f5f5f7;
                min-height: 20px;
            }}
            #{on} QComboBox:hover {{ background: #242831; }}
            #{on} QComboBox:focus {{ background: #1f1f23; border: 1px solid #0a84ff; }}
            #{on} QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 28px;
                background: transparent;
                border-left: none;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
        """
    return f"""
        #{on} QComboBox {{
            background: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 10px 12px;
            color: #1d1d1f;
            min-height: 20px;
        }}
        #{on} QComboBox:hover {{ background: #f7f8fb; }}
        #{on} QComboBox:focus {{ background: #ffffff; border: 1px solid #0a84ff; }}
        #{on} QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 28px;
            background: transparent;
            border-left: none;
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
        }}
    """


class RoundedVerticalScrollbar(QtWidgets.QWidget):
    """Кастомный скроллбар для popup-списка (точные «пилюльные» концы)."""

    def __init__(
        self,
        linked_scrollbar: QtWidgets.QScrollBar,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sb = linked_scrollbar

        self._thumb_color = QtGui.QColor(60, 60, 67, 160)
        self._track_color = QtGui.QColor(0, 0, 0, 0)

        self._dragging = False
        self._drag_offset_y = 0

        self.setFixedWidth(6)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        self._sb.valueChanged.connect(self.update)
        self._sb.rangeChanged.connect(self.update)  # type: ignore[attr-defined]

    def set_colors(self, thumb: QtGui.QColor, track: QtGui.QColor) -> None:
        self._thumb_color = thumb
        self._track_color = track
        self.update()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.update()

    def _compute_thumb(self) -> QtCore.QRectF:
        track_h = max(0, self.height())
        if track_h <= 0:
            return QtCore.QRectF(0, 0, float(self.width()), 0)

        sb_min = self._sb.minimum()
        sb_max = self._sb.maximum()
        sb_range = max(0, sb_max - sb_min)
        value = self._sb.value()

        try:
            page = int(self._sb.pageStep())  # type: ignore[attr-defined]
        except Exception:
            page = 0
        total = sb_range + max(0, page)
        visible_ratio = (max(0, page) / total) if total > 0 else 1.0
        visible_ratio = max(0.05, min(1.0, float(visible_ratio)))

        thumb_h = max(16.0, min(float(track_h), float(track_h) * visible_ratio))

        if sb_range <= 0:
            progress = 0.0
        else:
            progress = float(value - sb_min) / float(sb_range)
            progress = max(0.0, min(1.0, progress))

        travel = max(0.0, float(track_h) - thumb_h)
        thumb_y = travel * progress

        return QtCore.QRectF(0.0, thumb_y, float(self.width()), thumb_h)

    def _set_value_from_thumb_y(self, thumb_y: float, thumb_h: float) -> None:
        sb_min = self._sb.minimum()
        sb_max = self._sb.maximum()
        sb_range = max(0, sb_max - sb_min)
        if sb_range <= 0:
            return

        track_h = max(1, self.height())
        travel = max(0.0, float(track_h) - float(thumb_h))
        if travel <= 0.0:
            self._sb.setValue(int(sb_min))
            return

        progress = max(0.0, min(1.0, float(thumb_y) / travel))
        new_value = sb_min + progress * float(sb_range)
        self._sb.setValue(int(round(new_value)))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        if self._track_color.alpha() > 0:
            track_rect = QtCore.QRectF(0.0, 0.0, float(self.width()), float(self.height()))
            radius = float(self.width()) / 2.0
            painter.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))  # type: ignore[arg-type]
            painter.setBrush(QtGui.QBrush(self._track_color))
            painter.drawRoundedRect(track_rect, radius, radius)

        thumb = self._compute_thumb()
        radius = float(self.width()) / 2.0
        painter.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))  # type: ignore[arg-type]
        painter.setBrush(QtGui.QBrush(self._thumb_color))
        painter.drawRoundedRect(thumb, radius, radius)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        thumb = self._compute_thumb()
        if thumb.height() <= 0:
            return

        self._dragging = True
        if thumb.contains(event.pos()):
            self._drag_offset_y = int(event.pos().y() - thumb.y())
        else:
            self._drag_offset_y = int(thumb.height() / 2.0)

        target_thumb_y = float(event.pos().y() - self._drag_offset_y)
        target_thumb_y = max(0.0, min(float(self.height()) - float(thumb.height()), target_thumb_y))
        self._set_value_from_thumb_y(target_thumb_y, float(thumb.height()))

        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if not self._dragging:
            return

        thumb = self._compute_thumb()
        if thumb.height() <= 0:
            return

        target_thumb_y = float(event.pos().y() - self._drag_offset_y)
        target_thumb_y = max(0.0, min(float(self.height()) - float(thumb.height()), target_thumb_y))
        self._set_value_from_thumb_y(target_thumb_y, float(thumb.height()))
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        self._dragging = False
        event.accept()


class ProfileComboPopup(QtWidgets.QFrame):
    itemChosen = QtCore.pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        minimum_popup_width: int = 264,
    ) -> None:
        super().__init__(parent)
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        if sys.platform.startswith("win"):
            popup_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ProfileComboPopupWindow")
        self.setMinimumWidth(max(200, int(minimum_popup_width)))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.surface = QtWidgets.QFrame(self)
        self.surface.setObjectName("ProfileComboPopupSurface")
        root.addWidget(self.surface)

        inner = QtWidgets.QHBoxLayout(self.surface)
        inner.setContentsMargins(10, 12, 10, 12)
        inner.setSpacing(0)

        self.list = QtWidgets.QListWidget(self.surface)
        self.list.setObjectName("ProfileComboPopupList")
        self.list.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.list.setSpacing(4)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._on_item_clicked)
        inner.addWidget(self.list, 1)

        self._custom_scrollbar = RoundedVerticalScrollbar(self.list.verticalScrollBar(), self.surface)
        inner.addWidget(self._custom_scrollbar, 0)

    def _on_item_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        self.itemChosen.emit(item.text())
        self.hide()

    def set_items(self, values: List[str], selected_text: str) -> None:
        unique: List[str] = []
        seen = set()
        for v in values:
            s = (v or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            unique.append(s)
        self.list.clear()
        for v in unique:
            self.list.addItem(v)
        selected_row = -1
        for i, v in enumerate(unique):
            if v == selected_text:
                selected_row = i
                break
        if selected_row >= 0:
            self.list.setCurrentRow(selected_row)
        elif unique:
            self.list.setCurrentRow(0)

    def show_below(self, anchor: QtWidgets.QWidget) -> None:
        n = self.list.count()
        visible = min(max(1, n), 8)
        row_h = 30
        for i in range(min(n, visible)):
            row_h = max(row_h, self.list.sizeHintForRow(i))
        # Запас под padding/margin/spacing пунктов, внутренние отступы surface и скругления.
        gap = max(0, self.list.spacing()) * max(0, visible - 1)
        content_h = visible * row_h + gap + 36
        self.list.setMinimumHeight(content_h)
        self.list.setMaximumHeight(content_h)
        self.list.updateGeometry()
        self.setFixedWidth(max(anchor.width(), self.minimumWidth()))
        self.adjustSize()
        vsb = self.list.verticalScrollBar()
        self._custom_scrollbar.setVisible(vsb.maximum() > 0)
        w, h = self.width(), self.height()
        self.move(
            global_position_popup_below_anchor(
                anchor, w, h, vertical_gap=4, align_right=False
            )
        )
        self.show()
        self._custom_scrollbar.update()

    def apply_theme(self, theme_id: str) -> None:
        if theme_id == "night":
            self.setStyleSheet(
                """
                #ProfileComboPopupWindow { background: transparent; }
                #ProfileComboPopupSurface {
                    background: rgba(28, 31, 40, 0.98);
                    border: none;
                    border-radius: 12px;
                }
                QListWidget#ProfileComboPopupList QScrollBar:vertical {
                    background: transparent;
                    width: 6px;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::handle:vertical {
                    background-color: rgba(255, 255, 255, 0.20);
                    min-height: 24px;
                    border-radius: 999px;
                    border: none;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::groove:vertical {
                    background: transparent;
                    border-radius: 999px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-page:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-page:vertical {
                    background: transparent;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-line:vertical { height: 0px; }
                QListWidget#ProfileComboPopupList {
                    background: transparent;
                    border: none;
                    outline: none;
                    color: #d8deea;
                    font-size: 13px;
                }
                QListWidget#ProfileComboPopupList QScrollBar:vertical {
                    background: transparent;
                    width: 10px;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::handle:vertical {
                    background: rgba(160, 160, 160, 0.35);
                    border-radius: 5px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::up-arrow:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::down-arrow:vertical {
                    background: none;
                    border: none;
                    height: 0px;
                }
                QListWidget#ProfileComboPopupList::item {
                    border-radius: 8px;
                    padding: 8px 12px;
                    margin: 2px 4px;
                }
                QListWidget#ProfileComboPopupList::item:selected {
                    background: rgba(72, 138, 255, 0.35);
                    color: #f4f7ff;
                }
                QListWidget#ProfileComboPopupList::item:hover {
                    background: rgba(255, 255, 255, 0.10);
                }
                """
            )
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(255, 255, 255, 51),
                track=QtGui.QColor(0, 0, 0, 0),
            )
        else:
            self.setStyleSheet(
                """
                #ProfileComboPopupWindow { background: transparent; }
                #ProfileComboPopupSurface {
                    background: #f6f7fa;
                    border: none;
                    border-radius: 12px;
                }
                QListWidget#ProfileComboPopupList QScrollBar:vertical {
                    background: transparent;
                    width: 6px;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::handle:vertical {
                    background-color: rgba(60, 60, 67, 0.28);
                    min-height: 24px;
                    border-radius: 999px;
                    border: none;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::groove:vertical {
                    background: transparent;
                    border-radius: 999px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-page:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-page:vertical {
                    background: transparent;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-line:vertical { height: 0px; }
                QListWidget#ProfileComboPopupList {
                    background: transparent;
                    border: none;
                    outline: none;
                    color: #2f3644;
                    font-size: 13px;
                }
                QListWidget#ProfileComboPopupList QScrollBar:vertical {
                    background: transparent;
                    width: 10px;
                    margin: 0px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::handle:vertical {
                    background: rgba(70, 90, 120, 0.28);
                    border-radius: 5px;
                }
                QListWidget#ProfileComboPopupList QScrollBar::add-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::sub-line:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::up-arrow:vertical,
                QListWidget#ProfileComboPopupList QScrollBar::down-arrow:vertical {
                    background: none;
                    border: none;
                    height: 0px;
                }
                QListWidget#ProfileComboPopupList::item {
                    border-radius: 8px;
                    padding: 8px 12px;
                    margin: 2px 4px;
                }
                QListWidget#ProfileComboPopupList::item:selected {
                    background: #dbe9ff;
                    color: #1b4f9f;
                }
                QListWidget#ProfileComboPopupList::item:hover {
                    background: #e8eef8;
                }
                """
            )
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(60, 60, 67, 72),
                track=QtGui.QColor(0, 0, 0, 0),
            )


class ProfileComboBox(QtWidgets.QComboBox):
    popupRequested = QtCore.pyqtSignal()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popupRequested.emit()


class ProfileComboWithArrow(QtWidgets.QWidget):
    """QComboBox с видимой стрелкой ▼; список — кастомный ProfileComboPopup."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        root_object_name: Optional[str] = None,
        popup_min_width: int = 264,
    ) -> None:
        super().__init__(parent)
        if root_object_name:
            self.setObjectName(root_object_name)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = ProfileComboBox(self)
        self._completer: Optional[QtWidgets.QCompleter] = None
        layout.addWidget(self.combo)
        self._arrow = QtWidgets.QLabel("∨", self)
        self.set_arrow_color("#9fa1b5")
        self._arrow.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._arrow.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.popup = ProfileComboPopup(self, minimum_popup_width=popup_min_width)
        self.combo.popupRequested.connect(self._show_popup)
        self.popup.itemChosen.connect(self._on_item_chosen)

    def _show_popup(self) -> None:
        values = [self.combo.itemText(i) for i in range(self.combo.count())]
        self.popup.set_items(values, self.combo.currentText().strip())
        self.popup.show_below(self.combo)

    def _on_item_chosen(self, text: str) -> None:
        idx = self.combo.findText(text)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        else:
            self.combo.setCurrentText(text)

    def enable_autocomplete(self) -> None:
        self._completer = QtWidgets.QCompleter(self.combo.model(), self.combo)
        self._completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(
            QtWidgets.QCompleter.CompletionMode.PopupCompletion
        )
        self._completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        self.combo.setCompleter(self._completer)

    def set_arrow_color(self, color: str) -> None:
        self._arrow.setStyleSheet(
            f"color: {color}; font-size: 10px; background: transparent;"
        )

    def apply_popup_theme(self, theme_id: str) -> None:
        self.popup.apply_theme(theme_for_styled_combo(theme_id))

    def apply_embedded_field_style(self, theme_id: str, *, combo_arrow: str) -> None:
        """Поле как в диалоге профиля, если родитель не задаёт QComboBox QSS (пикер эмодзи)."""
        tid = theme_for_styled_combo(theme_id)
        oname = self.objectName()
        if not oname:
            oname = "StandaloneProfileComboRow"
            self.setObjectName(oname)
        self.setStyleSheet(embedded_combo_row_stylesheet(oname, tid))
        self.set_arrow_color(combo_arrow)
        self.popup.apply_theme(tid)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        drop_width = 28
        self._arrow.setGeometry(
            self.width() - drop_width,
            0,
            drop_width,
            self.height(),
        )
