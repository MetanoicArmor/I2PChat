"""
Выпадающий список в стиле экрана выбора профиля: без нативного QComboBox popup,
кастомный ProfileComboPopup + стрелка.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from i2pchat.gui.popup_geometry import (
    apply_rounded_rect_mask,
    embedded_popup_top_left_in_window,
    global_position_popup_below_anchor,
    paint_popup_rounded_bg,
    update_popup_rounded_mask,
)


def _embedded_anchor_content_width(anchor: QtWidgets.QWidget) -> int:
    """Ширина якоря для встроенного popup: на первом кадре width() иногда ещё 0 — подхватываем geometry/sizeHint."""
    w = anchor.width()
    if w >= 4:
        return w
    gw = anchor.frameGeometry().width()
    if gw >= 4:
        return gw
    sh = anchor.sizeHint()
    if sh.width() >= 4:
        return sh.width()
    return max(200, anchor.minimumWidth()) if anchor.minimumWidth() > 0 else 264


def theme_for_styled_combo(theme_id: Optional[str]) -> str:
    raw = str(theme_id or "").strip().lower()
    if raw in {"macos", "light"}:
        raw = "ligth"
    if raw == "night":
        return "night"
    return "ligth"


def _profile_popup_list_stylesheet(
    root_selector: str,
    night: bool,
    *,
    list_background: str,
    border: str = "none",
    border_radius: str = "0px",
    padding: str = "0px",
) -> str:
    """Общий QSS для QListWidget в кастомном ProfileComboPopup."""
    if night:
        color = "#d8deea"
        # Непрозрачный фон выделения — без rgba по углам border-radius (нет светлых «точек» на macOS).
        item_sel_bg = "#3a5588"
        item_sel_fg = "#f4f7ff"
        item_hover = "#2c3039"
        sb_handle_a = "rgba(255, 255, 255, 0.20)"
        sb_handle_b = "rgba(160, 160, 160, 0.35)"
    else:
        color = "#2f3644"
        item_sel_bg = "#dbe9ff"
        item_sel_fg = "#1b4f9f"
        item_hover = "#e8eef8"
        sb_handle_a = "rgba(60, 60, 67, 0.28)"
        sb_handle_b = "rgba(70, 90, 120, 0.28)"
    rs = root_selector
    return f"""
                {rs} QScrollBar:vertical {{
                    background: transparent;
                    width: 6px;
                    margin: 0px;
                }}
                {rs} QScrollBar::handle:vertical {{
                    background-color: {sb_handle_a};
                    min-height: 24px;
                    border-radius: 999px;
                    border: none;
                    margin: 0px;
                }}
                {rs} QScrollBar::groove:vertical {{
                    background: transparent;
                    border-radius: 999px;
                }}
                {rs} QScrollBar::add-page:vertical,
                {rs} QScrollBar::sub-page:vertical {{
                    background: transparent;
                }}
                {rs} QScrollBar::add-line:vertical,
                {rs} QScrollBar::sub-line:vertical {{ height: 0px; }}
                {rs} {{
                    background: {list_background};
                    border: {border};
                    border-radius: {border_radius};
                    padding: {padding};
                    outline: none;
                    color: {color};
                    font-size: 13px;
                }}
                {rs} QScrollBar:vertical {{
                    background: transparent;
                    width: 10px;
                    margin: 0px;
                }}
                {rs} QScrollBar::handle:vertical {{
                    background: {sb_handle_b};
                    border-radius: 5px;
                }}
                {rs} QScrollBar::add-line:vertical,
                {rs} QScrollBar::sub-line:vertical,
                {rs} QScrollBar::up-arrow:vertical,
                {rs} QScrollBar::down-arrow:vertical {{
                    background: none;
                    border: none;
                    height: 0px;
                }}
                {rs}::item {{
                    border-radius: 6px;
                    padding: 4px 10px;
                    margin: 0px 2px 4px 2px;
                    border: none;
                }}
                {rs}::item:selected,
                {rs}::item:selected:active {{
                    background: {item_sel_bg};
                    color: {item_sel_fg};
                    border: none;
                }}
                {rs}::item:hover {{
                    background: {item_hover};
                    border: none;
                }}
                {rs}::item:selected:hover {{
                    background: {item_sel_bg};
                    color: {item_sel_fg};
                }}
                """


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


def _profile_popup_solid_shell_qss(night: bool, *, darwin_embedded: bool = False) -> str:
    """Непрозрачный внешний фрейм (Windows и встроенный popup на macOS — без полупрозрачности по углам)."""
    if night:
        # На macOS встроенный: более тёмная рамка — меньше светлой каймы по скруглению (субпиксели).
        border = "#2d323d" if darwin_embedded else "#4a5060"
        return f"""
                #ProfileComboPopupWindow {{
                    background: #1c1f28;
                    border: 1px solid {border};
                    border-radius: 12px;
                }}
                #ProfileComboPopupSurface {{
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                }}
                """
    border = "#d8dce6" if darwin_embedded else "#c4c4c4"
    return f"""
                #ProfileComboPopupWindow {{
                    background: #f6f7fa;
                    border: 1px solid {border};
                    border-radius: 12px;
                }}
                #ProfileComboPopupSurface {{
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                }}
                """


class _ProfilePopupItemDelegate(QtWidgets.QStyledItemDelegate):
    """
    Рисуем текст и плашку выделения вручную, чтобы на macOS плашка была
    оптически выровнена относительно текста.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._night = True

    def set_theme(self, night: bool) -> None:
        self._night = bool(night)

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        self.initStyleOption(option, index)
        fm = QtGui.QFontMetrics(option.font)
        h = 4 + 8 + fm.height()
        w = super().sizeHint(option, index).width()
        return QtCore.QSize(w, max(24, min(h, 34)))

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        self.initStyleOption(option, index)
        opt = QtWidgets.QStyleOptionViewItem(option)
        opt.text = ""
        opt.icon = QtGui.QIcon()
        widget = opt.widget
        style = (
            widget.style()
            if widget is not None
            else QtWidgets.QApplication.style()
        )

        selected = bool(opt.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(opt.state & QtWidgets.QStyle.StateFlag.State_MouseOver)

        base_opt = QtWidgets.QStyleOptionViewItem(opt)
        base_opt.state = base_opt.state & ~(
            QtWidgets.QStyle.StateFlag.State_Selected
            | QtWidgets.QStyle.StateFlag.State_MouseOver
        )
        base_opt.text = ""
        base_opt.icon = QtGui.QIcon()

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(base_opt.rect)
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            base_opt,
            painter,
            widget,
        )

        raw = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        text = str(raw) if raw is not None else ""
        if not text:
            return

        if self._night:
            sel_bg = QtGui.QColor("#3a5588")
            hov_bg = QtGui.QColor("#2c3039")
            sel_fg = QtGui.QColor("#f4f7ff")
            txt_fg = QtGui.QColor("#d8deea")
        else:
            sel_bg = QtGui.QColor("#dbe9ff")
            hov_bg = QtGui.QColor("#e8eef8")
            sel_fg = QtGui.QColor("#1b4f9f")
            txt_fg = QtGui.QColor("#2f3644")

        if selected or hovered:
            pill = opt.rect.adjusted(2, 2, -2, -2)
            if sys.platform == "darwin":
                pill.translate(0, -2)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(sel_bg if selected else hov_bg)
            painter.drawRoundedRect(QtCore.QRectF(pill), 6.0, 6.0)

        painter.setPen(sel_fg if selected else txt_fg)
        painter.setFont(opt.font)
        text_y_shift = -2 if sys.platform == "darwin" else 0
        dr = opt.rect.adjusted(12, text_y_shift, -10, text_y_shift)
        painter.drawText(
            dr,
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()


class ProfileComboPopup(QtWidgets.QFrame):
    itemChosen = QtCore.pyqtSignal(str)
    # Синхронно с #ProfileComboPopupSurface border-radius в apply_theme
    _WIN_OUTER_RADIUS = 12.0

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        minimum_popup_width: int = 264,
        as_embedded_child: bool = False,
    ) -> None:
        super().__init__(parent)
        self._as_embedded_child = as_embedded_child
        self._win_menu_chrome = sys.platform.startswith("win")
        self._opaque_popup_chrome = self._win_menu_chrome or (
            as_embedded_child and sys.platform == "darwin"
        )
        self._linux_painted_bg = (
            sys.platform.startswith("linux")
            and not self._win_menu_chrome
        )
        self._popup_bg = QtGui.QColor(246, 247, 250)
        self._popup_border = QtGui.QColor(208, 211, 218)
        if as_embedded_child:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            if self._opaque_popup_chrome:
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
            else:
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            popup_flags = (
                QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
            )
            self.setWindowFlags(popup_flags)
            if self._opaque_popup_chrome:
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
            else:
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ProfileComboPopupWindow")
        self.setMinimumWidth(max(200, int(minimum_popup_width)))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.surface = QtWidgets.QFrame(self)
        self.surface.setObjectName("ProfileComboPopupSurface")
        if as_embedded_child:
            self.surface.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True
            )
        root.addWidget(self.surface)

        inner = QtWidgets.QHBoxLayout(self.surface)
        if as_embedded_child:
            inner.setContentsMargins(6, 5, 6, 5)
        else:
            inner.setContentsMargins(10, 12, 10, 12)
        inner.setSpacing(0)

        self.list = QtWidgets.QListWidget(self.surface)
        self.list.setObjectName("ProfileComboPopupList")
        self.list.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.list.setSpacing(4)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemActivated.connect(self._on_item_clicked)
        self._item_delegate = _ProfilePopupItemDelegate(self.list)
        self.list.setItemDelegate(self._item_delegate)
        self.list.setUniformItemSizes(True)
        inner.addWidget(self.list, 1)

        self._custom_scrollbar = RoundedVerticalScrollbar(self.list.verticalScrollBar(), self.surface)
        inner.addWidget(self._custom_scrollbar, 0)
        self._keep_editor_focus_last: bool = False

    def _apply_win_popup_mask(self) -> None:
        if self._win_menu_chrome:
            apply_rounded_rect_mask(self, self._WIN_OUTER_RADIUS)
            return
        if self._as_embedded_child and sys.platform == "darwin":
            self.clearMask()
            return

    def _apply_linux_mask(self) -> None:
        if self._linux_painted_bg and not self._as_embedded_child:
            update_popup_rounded_mask(self, self._WIN_OUTER_RADIUS)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        if self._linux_painted_bg and not self._as_embedded_child:
            paint_popup_rounded_bg(
                self, self._popup_bg, self._popup_border, self._WIN_OUTER_RADIUS,
            )
        super().paintEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_win_popup_mask()
        self._apply_linux_mask()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._win_menu_chrome or (
            self._as_embedded_child and sys.platform == "darwin"
        ):
            QtCore.QTimer.singleShot(0, self._apply_win_popup_mask)
        elif self._linux_painted_bg and not self._as_embedded_child:
            QtCore.QTimer.singleShot(0, self._apply_linux_mask)

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
        align = (
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is not None:
                it.setTextAlignment(align)

    def _size_list_and_shell(self, anchor: QtWidgets.QWidget) -> tuple[int, int]:
        n = self.list.count()
        spacing = max(0, self.list.spacing())
        row_lo, row_hi = 24, 34
        lay = self.surface.layout()
        m = lay.contentsMargins() if lay is not None else QtCore.QMargins(0, 0, 0, 0)

        self.list.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        if n <= 0:
            list_h = 1
            self.list.setFixedHeight(list_h)
            self._custom_scrollbar.setFixedHeight(list_h)
            aw = (
                _embedded_anchor_content_width(anchor)
                if self._as_embedded_child
                else anchor.width()
            )
            pw = max(aw, self.minimumWidth())
            self.setFixedWidth(pw)
            self.setFixedHeight(m.top() + list_h + m.bottom())
            self._apply_win_popup_mask()
            self._custom_scrollbar.setVisible(False)
            return self.width(), self.height()

        visible = min(n, 8)
        heights: List[int] = []
        for i in range(visible):
            h = self.list.sizeHintForRow(i)
            if h < 0:
                h = row_lo
            heights.append(max(row_lo, min(int(h), row_hi)))
        list_h = sum(heights) + spacing * max(0, visible - 1)
        # 1px — зазор viewport QAbstractItemView без лишней «полосы» под одной строкой.
        list_h += 1

        self.list.setFixedHeight(list_h)
        self._custom_scrollbar.setFixedHeight(list_h)

        aw = (
            _embedded_anchor_content_width(anchor)
            if self._as_embedded_child
            else anchor.width()
        )
        pw = max(aw, self.minimumWidth())
        self.setFixedWidth(pw)
        self.setFixedHeight(m.top() + list_h + m.bottom())
        self.list.updateGeometry()
        self._apply_win_popup_mask()
        vsb = self.list.verticalScrollBar()
        # Один пункт — без полосы прокрутки (на mac иногда vsb.maximum() > 0 из‑за округления).
        self._custom_scrollbar.setVisible(n > 1 and vsb.maximum() > 0)
        return self.width(), self.height()

    def _place_embedded_below(self, anchor: QtWidgets.QWidget) -> None:
        win = anchor.window()
        if win is None:
            return
        self._size_list_and_shell(anchor)
        pos = embedded_popup_top_left_in_window(
            anchor,
            win,
            self.width(),
            self.height(),
            vertical_gap=4,
            margin=8,
            center_under_anchor=False,
        )
        self.move(pos)

    def _embedded_sync_after_layout(self, anchor: QtWidgets.QWidget) -> None:
        if not self._as_embedded_child or not self.isVisible():
            return
        self._place_embedded_below(anchor)

    def _resync_frameless_below(self, anchor: QtWidgets.QWidget) -> None:
        """После первого show: layout/viewport списка и якоря могут обновиться на следующем тике."""
        if self._as_embedded_child or not self.isVisible():
            return
        w, h = self._size_list_and_shell(anchor)
        self.move(
            global_position_popup_below_anchor(
                anchor, w, h, vertical_gap=4, align_right=False
            )
        )

    def show_below(
        self,
        anchor: QtWidgets.QWidget,
        *,
        keep_editor_focus: bool = False,
    ) -> None:
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        if self._as_embedded_child:
            self.list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            win = anchor.window()
            if win is None:
                return
            if self.parent() is not win:
                self.setParent(win)
            self._place_embedded_below(anchor)
            self.show()
            self.raise_()
            self._custom_scrollbar.update()
            # После layout диалога width() якоря может обновиться на следующем тике — пересчитать ширину/позицию.
            QtCore.QTimer.singleShot(
                0,
                lambda a=anchor: self._embedded_sync_after_layout(a),
            )
            return

        self._keep_editor_focus_last = keep_editor_focus
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating,
            keep_editor_focus,
        )
        self.list.setFocusPolicy(
            QtCore.Qt.FocusPolicy.NoFocus
            if keep_editor_focus
            else QtCore.Qt.FocusPolicy.StrongFocus
        )
        w, h = self._size_list_and_shell(anchor)
        self.move(
            global_position_popup_below_anchor(
                anchor, w, h, vertical_gap=4, align_right=False
            )
        )
        self.show()
        self._custom_scrollbar.update()
        QtCore.QTimer.singleShot(
            0,
            lambda a=anchor: self._resync_frameless_below(a),
        )
        if keep_editor_focus:
            cb: Optional[QtWidgets.QComboBox]
            if isinstance(anchor, QtWidgets.QComboBox):
                cb = anchor
            else:
                cb = getattr(anchor, "combo", None)
            if isinstance(cb, QtWidgets.QComboBox):
                le = cb.lineEdit()
                if le is not None:
                    QtCore.QTimer.singleShot(0, le.setFocus)
        else:
            # Иначе стрелки остаются в line edit, а видимый список не получает фокус.
            QtCore.QTimer.singleShot(0, self.list.setFocus)

    def apply_theme(self, theme_id: str) -> None:
        night = theme_id == "night"
        list_sel = "QListWidget#ProfileComboPopupList"
        list_night = _profile_popup_list_stylesheet(
            list_sel, True, list_background="transparent"
        )
        list_light = _profile_popup_list_stylesheet(
            list_sel, False, list_background="transparent"
        )
        if self._opaque_popup_chrome:
            shell = _profile_popup_solid_shell_qss(
                night,
                darwin_embedded=(
                    self._as_embedded_child and sys.platform == "darwin"
                ),
            )
            self.setStyleSheet(shell + (list_night if night else list_light))
        elif self._linux_painted_bg and not self._as_embedded_child:
            if night:
                self._popup_bg = QtGui.QColor(28, 31, 40, 250)
                self._popup_border = QtGui.QColor(58, 62, 74)
            else:
                self._popup_bg = QtGui.QColor(246, 247, 250)
                self._popup_border = QtGui.QColor(208, 211, 218)
            self.setStyleSheet(
                """
                #ProfileComboPopupWindow { background: transparent; }
                #ProfileComboPopupSurface {
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                }
                """
                + (list_night if night else list_light)
            )
            self.update()
        elif night:
            _border = "#3a3e4a" if self._linux_painted_bg else "rgba(255, 255, 255, 0.14)"
            self.setStyleSheet(
                f"""
                #ProfileComboPopupWindow {{ background: transparent; }}
                #ProfileComboPopupSurface {{
                    background: rgba(28, 31, 40, 0.98);
                    border: 1px solid {_border};
                    border-radius: 12px;
                }}
                """
                + list_night
            )
        else:
            _border = "#d0d3da" if self._linux_painted_bg else "rgba(0, 0, 0, 0.12)"
            self.setStyleSheet(
                f"""
                #ProfileComboPopupWindow {{ background: transparent; }}
                #ProfileComboPopupSurface {{
                    background: #f6f7fa;
                    border: 1px solid {_border};
                    border-radius: 12px;
                }}
                """
                + list_light
            )
        if night:
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(255, 255, 255, 51),
                track=QtGui.QColor(0, 0, 0, 0),
            )
        else:
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(60, 60, 67, 72),
                track=QtGui.QColor(0, 0, 0, 0),
            )
        self._item_delegate.set_theme(night)
        self.list.viewport().update()
        self._apply_win_popup_mask()
        if self._as_embedded_child:
            # macOS: встроенный слой — непрозрачный хром; тень снова даёт артефакты по краям.
            if sys.platform == "darwin":
                self.setGraphicsEffect(None)
            else:
                sh = QtWidgets.QGraphicsDropShadowEffect(self)
                sh.setBlurRadius(22)
                sh.setOffset(0, 4)
                sh.setColor(
                    QtGui.QColor(0, 0, 0, 72 if night else 48)
                )
                self.setGraphicsEffect(sh)
        elif self.graphicsEffect() is not None:
            self.setGraphicsEffect(None)


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
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = ProfileComboBox(self)
        self._popup_theme_id: str = "ligth"
        self._typing_suggest_enabled: bool = False
        self._suppress_typing_suggest: bool = False
        layout.addWidget(self.combo)
        self._arrow = QtWidgets.QLabel("∨", self)
        self.set_arrow_color("#9fa1b5")
        self._arrow.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._arrow.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.popup = ProfileComboPopup(
            self, minimum_popup_width=popup_min_width, as_embedded_child=False
        )
        self._typing_panel = ProfileComboPopup(
            None,
            minimum_popup_width=popup_min_width,
            as_embedded_child=True,
        )
        self.combo.popupRequested.connect(self._show_popup)
        self.popup.itemChosen.connect(self._on_item_chosen)
        self._typing_panel.itemChosen.connect(self._on_item_chosen)
        self.combo.setCompleter(None)

    def _popup_anchor(self) -> QtWidgets.QWidget:
        """Якорь геометрии для списков: QComboBox, а не обёртка — у строки первый кадр layout часто даёт height()==0."""
        return self.combo

    def _show_popup(self) -> None:
        self._typing_panel.hide()
        values = self._unique_combo_values()
        self.popup.set_items(values, self.combo.currentText().strip())
        self.popup.show_below(self._popup_anchor(), keep_editor_focus=False)

    def _unique_combo_values(self) -> List[str]:
        seen = set()
        out: List[str] = []
        for i in range(self.combo.count()):
            s = (self.combo.itemText(i) or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _filtered_suggestions(self, needle: str) -> List[str]:
        if not needle:
            return []
        nl = needle.lower()
        all_v = self._unique_combo_values()
        return sorted(
            [x for x in all_v if x.lower().startswith(nl)],
            key=str.lower,
        )

    def _on_typing_suggest_text(self, text: str) -> None:
        if not self._typing_suggest_enabled or self._suppress_typing_suggest:
            return
        needle = (text or "").strip()
        matches = self._filtered_suggestions(needle)
        if not matches:
            self._typing_panel.hide()
            return
        self.popup.hide()
        self._typing_panel.set_items(matches, needle)
        self._typing_panel.show_below(self._popup_anchor())

    def _hide_suggest_if_focus_left(self) -> None:
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None:
            if fw is self.popup or self.popup.isAncestorOf(fw):
                return
            if fw is self._typing_panel or self._typing_panel.isAncestorOf(fw):
                return
        if self._typing_panel.isVisible():
            self._typing_panel.hide()
        if self.popup.isVisible():
            self.popup.hide()

    def _on_item_chosen(self, text: str) -> None:
        self._suppress_typing_suggest = True
        try:
            idx = self.combo.findText(text)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
            else:
                self.combo.setCurrentText(text)
        finally:
            self._suppress_typing_suggest = False

    def enable_autocomplete(self) -> None:
        """Подсказки при наборе — тот же ProfileComboPopup; QCompleter отключён (иначе нативный список Qt на macOS/Windows)."""
        self.combo.setCompleter(None)
        self._typing_suggest_enabled = True
        le = self.combo.lineEdit()
        if le is None:
            return
        try:
            le.textChanged.disconnect(self._on_typing_suggest_text)
        except TypeError:
            pass
        le.textChanged.connect(self._on_typing_suggest_text)
        le.installEventFilter(self)
        self.combo.installEventFilter(self)

    def _list_for_line_edit_keyboard(self) -> Optional[QtWidgets.QListWidget]:
        """Список, которым нужно управлять с клавиатуры, пока фокус в поле ввода."""
        if self._typing_panel.isVisible():
            lst = self._typing_panel.list
            if lst.count() > 0:
                return lst
        if self.popup.isVisible():
            lst = self.popup.list
            if lst.count() > 0:
                return lst
        return None

    def _handle_list_key_while_editing(
        self, lst: QtWidgets.QListWidget, key: int
    ) -> bool:
        """Возврат True — событие поглощено (не отдавать QComboBox / line edit)."""
        n = lst.count()
        if n <= 0:
            return False
        if key in (QtCore.Qt.Key.Key_Down, QtCore.Qt.Key.Key_Up):
            row = lst.currentRow()
            if key == QtCore.Qt.Key.Key_Down:
                if row < 0:
                    new_row = 0
                else:
                    new_row = min(row + 1, n - 1)
            else:
                if row < 0:
                    new_row = n - 1
                else:
                    new_row = max(row - 1, 0)
            lst.setCurrentRow(new_row)
            cur = lst.currentItem()
            if cur is not None:
                lst.scrollToItem(cur)
            lst.viewport().update()
            return True
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            it = lst.currentItem()
            if it is None:
                lst.setCurrentRow(0)
                it = lst.currentItem()
            if it is not None:
                self._on_item_chosen(it.text())
            self._typing_panel.hide()
            self.popup.hide()
            return True
        return False

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        le = self.combo.lineEdit()
        if le is None:
            return False
        if obj not in (le, self.combo):
            return False
        if obj == le and event.type() == QtCore.QEvent.Type.FocusOut:
            QtCore.QTimer.singleShot(0, self._hide_suggest_if_focus_left)
            return False
        if event.type() == QtCore.QEvent.Type.KeyPress and isinstance(
            event, QtGui.QKeyEvent
        ):
            lst = self._list_for_line_edit_keyboard()
            if lst is not None and self._handle_list_key_while_editing(
                lst, int(event.key())
            ):
                return True
            if event.key() == QtCore.Qt.Key.Key_Escape:
                if self._typing_panel.isVisible():
                    self._typing_panel.hide()
                    return True
                if self.popup.isVisible():
                    self.popup.hide()
                    return True
        return False

    def set_arrow_color(self, color: str) -> None:
        self._arrow.setStyleSheet(
            f"color: {color}; font-size: 10px; background: transparent;"
        )

    def apply_popup_theme(self, theme_id: str) -> None:
        self._popup_theme_id = theme_id
        tid = theme_for_styled_combo(theme_id)
        self.popup.apply_theme(tid)
        self._typing_panel.apply_theme(tid)

    def apply_embedded_field_style(self, theme_id: str, *, combo_arrow: str) -> None:
        """Поле как в диалоге профиля, если родитель не задаёт QComboBox QSS (пикер эмодзи)."""
        tid = theme_for_styled_combo(theme_id)
        oname = self.objectName()
        if not oname:
            oname = "StandaloneProfileComboRow"
            self.setObjectName(oname)
        self.setStyleSheet(embedded_combo_row_stylesheet(oname, tid))
        self.set_arrow_color(combo_arrow)
        self._popup_theme_id = theme_id
        self.popup.apply_theme(tid)
        self._typing_panel.apply_theme(tid)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        drop_width = 28
        self._arrow.setGeometry(
            self.width() - drop_width,
            0,
            drop_width,
            self.height(),
        )
        if self._typing_panel.isVisible():
            self._typing_panel.show_below(self._popup_anchor())
        if self.popup.isVisible():
            self.popup.show_below(
                self._popup_anchor(),
                keep_editor_focus=self.popup._keep_editor_focus_last,
            )
