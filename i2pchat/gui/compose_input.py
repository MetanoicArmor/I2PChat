"""Обёртка поля ввода с кнопкой пикера эмодзи (Unicode в сообщение, UTF-8 в протоколе)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from i2pchat.gui.popup_geometry import apply_win_popup_rounded_mask
from i2pchat.gui.styled_combo_widgets import RoundedVerticalScrollbar

from .emoji_data import EMOJI_CHARS

_ICONS_DIR = Path(__file__).resolve().parent / "icons"


def format_emoji_picker_button_tooltip(portable_shortcut: str = "Ctrl+;") -> str:
    """Подпись хоткея как в остальном UI (⌘ на macOS для Ctrl+… в QKeySequence)."""
    native = QtGui.QKeySequence(portable_shortcut).toString(
        QtGui.QKeySequence.SequenceFormat.NativeText
    )
    base = (
        "Open emoji panel.\n"
        "In the panel: arrow keys to move; Enter or Space to insert; Esc to close."
    )
    if not native:
        return base
    return f"{base}\n\nShortcut: {native}"


def _resolve_gui_icon_file(filename: str) -> Optional[Path]:
    """Путь к PNG в пакете или в PyInstaller _MEIPASS."""
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        bundled = Path(meipass) / "i2pchat" / "gui" / "icons" / filename
        if bundled.is_file():
            return bundled
    p = _ICONS_DIR / filename
    return p if p.is_file() else None


def _picker_emoji_icon(png_path: Path, logical_side: int) -> QtGui.QIcon:
    """Квадратная иконка с PNG по центру — без смещения/обрезки стилем QPushButton на macOS."""
    src = QtGui.QPixmap(str(png_path))
    if src.isNull():
        return QtGui.QIcon()
    app = QtWidgets.QApplication.instance()
    dpr = 1.0
    if app is not None:
        scr = app.primaryScreen()
        if scr is not None:
            dpr = max(1.0, min(3.0, float(scr.devicePixelRatio())))
    phys = max(1, int(round(logical_side * dpr)))
    canvas = QtGui.QPixmap(phys, phys)
    canvas.fill(QtCore.Qt.GlobalColor.transparent)
    scaled = src.scaled(
        phys,
        phys,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    p = QtGui.QPainter(canvas)
    p.drawPixmap((phys - scaled.width()) // 2, (phys - scaled.height()) // 2, scaled)
    p.end()
    canvas.setDevicePixelRatio(dpr)
    return QtGui.QIcon(canvas)


def _tint_pixmap_with_alpha(source: QtGui.QPixmap, color: QtGui.QColor) -> QtGui.QPixmap:
    """Монохромная иконка на альфе: заливка цветом темы (как инверсия по смыслу для контраста)."""
    if source.isNull():
        return source
    out = QtGui.QPixmap(source.size())
    out.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(out)
    p.fillRect(out.rect(), color)
    p.setCompositionMode(
        QtGui.QPainter.CompositionMode.CompositionMode_DestinationIn
    )
    p.drawPixmap(0, 0, source)
    p.end()
    return out


def emoji_picker_toolbar_icon(theme_id: str) -> QtGui.QIcon:
    """Одна иконка face.dashed.png, тон как у placeholder (как QLabel#ChatSearchStatusInline в main_qt)."""
    path = _resolve_gui_icon_file("face.dashed.png")
    if path is None:
        return QtGui.QIcon()
    pm = QtGui.QPixmap(str(path))
    if pm.isNull():
        return QtGui.QIcon()
    tid = (theme_id or "").strip().lower()
    if tid == "night":
        # rgba(245, 245, 247, 0.55) на тёмном поле ~ как подсказка
        c = QtGui.QColor(245, 245, 247, 140)
    else:
        # rgba(60, 60, 67, 0.55) на белом поле
        c = QtGui.QColor(60, 60, 67, 140)
    return QtGui.QIcon(_tint_pixmap_with_alpha(pm, c))


class EmojiPickerPopup(QtWidgets.QFrame):
    """Всплывающая сетка эмодзи; в сообщение уходит Unicode-символ.

    macOS/Linux: прозрачное окно + скруглённая surface.
    Windows: без WA_TranslucentBackground — иначе нет нормальной рамки; вид «как у меню» (фон + border + тень DWM).
    """

    emojiChosen = QtCore.pyqtSignal(str)
    pickerHidden = QtCore.pyqtSignal()

    _COLS = 8
    # Должен совпадать с border-radius у QFrame#EmojiPickerPopupWindow в ветке _win_menu_chrome
    _WIN_OUTER_RADIUS = 8.0

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._win_menu_chrome = sys.platform.startswith("win")
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(popup_flags)
        if self._win_menu_chrome:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        else:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setObjectName("EmojiPickerPopupWindow")
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self._theme_id = "ligth"
        self._grid_layout: Optional[QtWidgets.QGridLayout] = None
        self._emoji_buttons: list[QtWidgets.QToolButton] = []
        self._focus_idx: int = 0

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._surface = QtWidgets.QFrame(self)
        self._surface.setObjectName("EmojiPickerPopupSurface")
        self._surface.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        root.addWidget(self._surface)

        surf_lay = QtWidgets.QVBoxLayout(self._surface)
        surf_lay.setContentsMargins(6, 6, 6, 6)
        surf_lay.setSpacing(0)

        self._scroll = QtWidgets.QScrollArea(self._surface)
        self._scroll.setObjectName("EmojiPickerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFixedHeight(260)
        self._scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        inner = QtWidgets.QWidget()
        inner.setObjectName("EmojiPickerGridHost")
        inner.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._grid_layout = QtWidgets.QGridLayout(inner)
        self._grid_layout.setSpacing(4)
        self._grid_layout.setContentsMargins(8, 6, 8, 6)

        self._scroll.setWidget(inner)
        self._custom_scrollbar = RoundedVerticalScrollbar(
            self._scroll.verticalScrollBar(), self._surface
        )
        self._custom_scrollbar.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        scroll_row = QtWidgets.QHBoxLayout()
        scroll_row.setContentsMargins(0, 0, 0, 0)
        scroll_row.setSpacing(4)
        scroll_row.addWidget(self._scroll, 1)
        scroll_row.addWidget(self._custom_scrollbar, 0)
        surf_lay.addLayout(scroll_row)
        self._scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_a: self._sync_emoji_scrollbar()
        )
        self._repopulate_grid()
        # +10 под кастомный скролл (как у ProfileComboPopup) рядом с QScrollArea
        self.setFixedWidth(min(self._COLS * 44 + 34, 392))
        self.apply_theme(self._theme_id)

    def _apply_win_rounded_mask(self) -> None:
        if not self._win_menu_chrome:
            return
        apply_win_popup_rounded_mask(self, self._WIN_OUTER_RADIUS)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_win_rounded_mask()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._win_menu_chrome:
            QtCore.QTimer.singleShot(0, self._apply_win_rounded_mask)

    def _repopulate_grid(self) -> None:
        from i2pchat.gui import emoji_paths as _ep

        grid = self._grid_layout
        if grid is None:
            return
        while (item := grid.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._emoji_buttons.clear()
        paths = _ep.emoji_paths_cached()
        icon_logical = 30
        icon_sz = QtCore.QSize(icon_logical, icon_logical)
        for i, ch in enumerate(EMOJI_CHARS):
            r, c = divmod(i, self._COLS)
            btn = QtWidgets.QToolButton()
            btn.setObjectName("EmojiCell")
            btn.setAutoRaise(True)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            btn.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            btn.setFixedSize(40, 40)
            btn.setAccessibleName(ch)
            btn.setToolTip(ch)
            fp = paths.get(_ep.normalize_emoji_glyph(ch))
            ic = QtGui.QIcon()
            if fp is not None:
                ic = _picker_emoji_icon(fp, icon_logical)
            if not ic.isNull():
                btn.setToolButtonStyle(
                    QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
                )
                btn.setIcon(ic)
                btn.setIconSize(icon_sz)
            else:
                btn.setToolButtonStyle(
                    QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
                )
                btn.setText(ch)
                f = QtGui.QFont(btn.font())
                f.setPointSize(16)
                btn.setFont(f)
            btn.clicked.connect(lambda _=False, sym=ch: self._pick(sym))
            self._emoji_buttons.append(btn)
            grid.addWidget(btn, r, c)
        self._sync_emoji_scrollbar()

    def _sync_emoji_scrollbar(self) -> None:
        cs = getattr(self, "_custom_scrollbar", None)
        if cs is None:
            return
        vsb = self._scroll.verticalScrollBar()
        cs.setVisible(vsb.maximum() > 0)
        cs.update()

    def apply_theme(self, theme_id: str) -> None:
        self._theme_id = (theme_id or "").strip().lower()
        if self._win_menu_chrome:
            if self._theme_id == "night":
                self.setStyleSheet(
                    """
                    QFrame#EmojiPickerPopupWindow {
                        background: #2c2c2c;
                        border: 1px solid #5c5c5c;
                        border-radius: 8px;
                    }
                    QFrame#EmojiPickerPopupSurface {
                        background: transparent;
                        border: none;
                        border-radius: 8px;
                    }
                    QScrollArea#EmojiPickerScroll {
                        border: none;
                        background: transparent;
                    }
                    QScrollArea#EmojiPickerScroll > QWidget > QWidget {
                        background: transparent;
                    }
                    QWidget#EmojiPickerGridHost {
                        background: transparent;
                    }
                    QToolButton#EmojiCell {
                        background: transparent;
                        border: none;
                        border-radius: 8px;
                        padding: 0px;
                        margin: 0px;
                        color: #e8e8e8;
                        outline: none;
                    }
                    QToolButton#EmojiCell:hover {
                        background: rgba(255, 255, 255, 0.12);
                    }
                    QToolButton#EmojiCell[emojiNavFocus="true"] {
                        background: #505458;
                        border: none;
                        outline: none;
                    }
                    """
                )
            else:
                self.setStyleSheet(
                    """
                    QFrame#EmojiPickerPopupWindow {
                        background: #ffffff;
                        border: 1px solid #c4c4c4;
                        border-radius: 8px;
                    }
                    QFrame#EmojiPickerPopupSurface {
                        background: transparent;
                        border: none;
                        border-radius: 8px;
                    }
                    QScrollArea#EmojiPickerScroll {
                        border: none;
                        background: transparent;
                    }
                    QScrollArea#EmojiPickerScroll > QWidget > QWidget {
                        background: transparent;
                    }
                    QWidget#EmojiPickerGridHost {
                        background: transparent;
                    }
                    QToolButton#EmojiCell {
                        background: transparent;
                        border: none;
                        border-radius: 8px;
                        padding: 0px;
                        margin: 0px;
                        color: #1f1f1f;
                        outline: none;
                    }
                    QToolButton#EmojiCell:hover {
                        background: #e8e8e8;
                    }
                    QToolButton#EmojiCell[emojiNavFocus="true"] {
                        background: #b8c4d4;
                        border: none;
                        outline: none;
                    }
                    """
                )
            if self._theme_id == "night":
                self._custom_scrollbar.set_colors(
                    thumb=QtGui.QColor(255, 255, 255, 51),
                    track=QtGui.QColor(0, 0, 0, 0),
                )
            else:
                self._custom_scrollbar.set_colors(
                    thumb=QtGui.QColor(60, 60, 67, 72),
                    track=QtGui.QColor(0, 0, 0, 0),
                )
            self._sync_emoji_scrollbar()
            return
        if self._theme_id == "night":
            self.setStyleSheet(
                """
                QFrame#EmojiPickerPopupWindow {
                    background: transparent;
                }
                QFrame#EmojiPickerPopupSurface {
                    background: rgba(34, 37, 45, 0.96);
                    border: none;
                    border-radius: 14px;
                }
                QScrollArea#EmojiPickerScroll {
                    border: none;
                    background: transparent;
                }
                QScrollArea#EmojiPickerScroll > QWidget > QWidget {
                    background: transparent;
                }
                QWidget#EmojiPickerGridHost {
                    background: transparent;
                }
                QToolButton#EmojiCell {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                    padding: 0px;
                    margin: 0px;
                    color: #e3e8f1;
                    outline: none;
                }
                QToolButton#EmojiCell:hover {
                    background: rgba(255, 255, 255, 0.10);
                }
                QToolButton#EmojiCell[emojiNavFocus="true"] {
                    background: #3d424d;
                    border: none;
                    outline: none;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#EmojiPickerPopupWindow {
                    background: transparent;
                }
                QFrame#EmojiPickerPopupSurface {
                    background: #f6f7fa;
                    border: none;
                    border-radius: 14px;
                }
                QScrollArea#EmojiPickerScroll {
                    border: none;
                    background: transparent;
                }
                QScrollArea#EmojiPickerScroll > QWidget > QWidget {
                    background: transparent;
                }
                QWidget#EmojiPickerGridHost {
                    background: transparent;
                }
                QToolButton#EmojiCell {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                    padding: 0px;
                    margin: 0px;
                    color: #2c3442;
                    outline: none;
                }
                QToolButton#EmojiCell:hover {
                    background: #e5eaf2;
                }
                QToolButton#EmojiCell[emojiNavFocus="true"] {
                    background: #c5d0e0;
                    border: none;
                    outline: none;
                }
                """
            )
        if self._theme_id == "night":
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(255, 255, 255, 51),
                track=QtGui.QColor(0, 0, 0, 0),
            )
        else:
            self._custom_scrollbar.set_colors(
                thumb=QtGui.QColor(60, 60, 67, 72),
                track=QtGui.QColor(0, 0, 0, 0),
            )
        self._sync_emoji_scrollbar()

    def _sync_emoji_focus_visual(self) -> None:
        n = len(self._emoji_buttons)
        if n == 0:
            return
        self._focus_idx = max(0, min(self._focus_idx, n - 1))
        for i, btn in enumerate(self._emoji_buttons):
            btn.setProperty("emojiNavFocus", i == self._focus_idx)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._scroll.ensureWidgetVisible(self._emoji_buttons[self._focus_idx])

    def _emoji_post_show_focus(self) -> None:
        if not self.isVisible():
            return
        self._sync_emoji_focus_visual()
        self.activateWindow()
        self.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        n = len(self._emoji_buttons)
        n_sym = len(EMOJI_CHARS)
        if n == 0 or n_sym == 0:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == QtCore.Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        if key in (
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
            QtCore.Qt.Key.Key_Space,
        ):
            if 0 <= self._focus_idx < n_sym:
                self._pick(EMOJI_CHARS[self._focus_idx])
            event.accept()
            return
        cols = self._COLS
        row, col = divmod(self._focus_idx, cols)
        moved = False
        if key == QtCore.Qt.Key.Key_Left and col > 0:
            self._focus_idx -= 1
            moved = True
        elif key == QtCore.Qt.Key.Key_Right and col < cols - 1:
            if self._focus_idx + 1 < n:
                self._focus_idx += 1
                moved = True
        elif key == QtCore.Qt.Key.Key_Up and self._focus_idx >= cols:
            self._focus_idx -= cols
            moved = True
        elif key == QtCore.Qt.Key.Key_Down and self._focus_idx + cols < n:
            self._focus_idx += cols
            moved = True
        if moved:
            self._sync_emoji_focus_visual()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.pickerHidden.emit()

    def _pick(self, sym: str) -> None:
        self.emojiChosen.emit(sym)
        self.hide()

    def show_near_anchor(self, anchor: QtWidgets.QWidget, theme_id: str) -> None:
        self.apply_theme(theme_id)
        self.adjustSize()
        self._apply_win_rounded_mask()
        top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        pw = self.width()
        ph = self.height()
        x = top_left.x() + anchor.width() - pw
        y = top_left.y() - ph - 6
        if y < 0:
            y = top_left.y() + anchor.height() + 6
        screen = QtWidgets.QApplication.screenAt(QtCore.QPoint(int(x), int(y)))
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            self.move(int(x), int(y))
        else:
            geo = screen.availableGeometry()
            margin = 6
            x = max(geo.left() + margin, min(int(x), geo.right() - pw - margin + 1))
            y = max(geo.top() + margin, min(int(y), geo.bottom() - ph - margin + 1))
            self.move(x, y)
        self._sync_emoji_scrollbar()
        self._focus_idx = 0
        self.show()
        self.raise_()
        QtCore.QTimer.singleShot(0, self._emoji_post_show_focus)


class ComposeInputWrapper(QtWidgets.QWidget):
    """Полноразмерное поле ввода (QTextEdit) и кнопка пикера в правом верхнем углу."""

    _BTN_SIDE = 28
    _CORNER_MARGIN = 6
    _VIEWPORT_PAD_RIGHT = 8

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._edit: Optional[QtWidgets.QTextEdit] = None
        self._theme_id = "ligth"
        self._popup: Optional[EmojiPickerPopup] = None
        self._emoji_shortcut_portable = "Ctrl+;"

        self._emoji_btn = QtWidgets.QToolButton(self)
        self._emoji_btn.setObjectName("EmojiPickerButton")
        self._emoji_btn.setAutoRaise(True)
        self._emoji_btn.setToolTip(
            format_emoji_picker_button_tooltip(self._emoji_shortcut_portable)
        )
        self._emoji_btn.setAccessibleName("Emoji picker")
        self._emoji_btn.setFixedSize(self._BTN_SIDE, self._BTN_SIDE)
        self._emoji_btn.setIconSize(QtCore.QSize(17, 17))
        self._emoji_btn.clicked.connect(self._on_emoji_clicked)
        self._apply_emoji_button_style()
        self._refresh_emoji_icon()

    def attach_input(self, edit: QtWidgets.QTextEdit) -> None:
        self._edit = edit
        edit.setParent(self)
        self._apply_viewport_margins()

    def input_widget(self) -> Optional[QtWidgets.QTextEdit]:
        return self._edit

    def set_theme(self, theme_id: str) -> None:
        self._theme_id = (theme_id or "").strip().lower()
        self._apply_emoji_button_style()
        self._refresh_emoji_icon()

    def set_emoji_shortcut_portable(self, portable: str) -> None:
        s = (portable or "").strip()
        self._emoji_shortcut_portable = s if s else "Ctrl+;"
        self._emoji_btn.setToolTip(
            format_emoji_picker_button_tooltip(self._emoji_shortcut_portable)
        )

    def toggle_emoji_picker(self) -> None:
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
            return
        self._on_emoji_clicked()

    def _apply_emoji_button_style(self) -> None:
        if self._theme_id == "night":
            self._emoji_btn.setStyleSheet(
                """
                QToolButton#EmojiPickerButton {
                    background: transparent;
                    border: none;
                    padding: 0px;
                    border-radius: 6px;
                }
                QToolButton#EmojiPickerButton:hover {
                    background: rgba(255, 255, 255, 0.12);
                }
                """
            )
        else:
            self._emoji_btn.setStyleSheet(
                """
                QToolButton#EmojiPickerButton {
                    background: transparent;
                    border: none;
                    padding: 0px;
                    border-radius: 6px;
                }
                QToolButton#EmojiPickerButton:hover {
                    background: rgba(10, 132, 255, 0.12);
                }
                """
            )

    def _refresh_emoji_icon(self) -> None:
        icon = emoji_picker_toolbar_icon(self._theme_id)
        if icon.isNull():
            self._emoji_btn.setIcon(QtGui.QIcon())
            self._emoji_btn.setText("🙂")
        else:
            self._emoji_btn.setIcon(icon)
            self._emoji_btn.setText("")

    def _apply_viewport_margins(self) -> None:
        if self._edit is None:
            return
        right = self._BTN_SIDE + self._CORNER_MARGIN + self._VIEWPORT_PAD_RIGHT
        self._edit.setViewportMargins(0, 4, right, 0)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if self._edit is not None:
            self._edit.setGeometry(0, 0, w, h)
        self._emoji_btn.move(
            w - self._emoji_btn.width() - self._CORNER_MARGIN,
            self._CORNER_MARGIN,
        )
        self._emoji_btn.raise_()

    def _on_emoji_clicked(self) -> None:
        host = self.window()
        parent_popup = host if isinstance(host, QtWidgets.QWidget) else self
        if self._popup is None:
            self._popup = EmojiPickerPopup(parent_popup)
            self._popup.emojiChosen.connect(self._on_emoji_chosen)
            self._popup.pickerHidden.connect(self._on_emoji_picker_hidden)
        self._popup.show_near_anchor(self._emoji_btn, self._theme_id)

    def _on_emoji_picker_hidden(self) -> None:
        if self._edit is not None:
            self._edit.setFocus()

    def _on_emoji_chosen(self, ch: str) -> None:
        if self._edit is None:
            return
        self._edit.setFocus()
        ins = getattr(self._edit, "insert_raster_emoji", None)
        if callable(ins):
            ins(ch)
        else:
            cur = self._edit.textCursor()
            cur.insertText(ch)
            self._edit.setTextCursor(cur)
