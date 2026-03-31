"""Обёртка поля ввода с кнопкой пикера эмодзи (Unicode в сообщение, UTF-8 в протоколе)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .emoji_data import EMOJI_CHARS

_ICONS_DIR = Path(__file__).resolve().parent / "icons"


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

    Отрисовка как у ActionsPopup: прозрачное окно + скруглённая surface (одинаково на ОС).
    """

    emojiChosen = QtCore.pyqtSignal(str)

    _COLS = 8

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        if sys.platform.startswith("win"):
            popup_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setObjectName("EmojiPickerPopupWindow")
        self._theme_id = "ligth"
        self._grid_layout: Optional[QtWidgets.QGridLayout] = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._surface = QtWidgets.QFrame(self)
        self._surface.setObjectName("EmojiPickerPopupSurface")
        root.addWidget(self._surface)

        surf_lay = QtWidgets.QVBoxLayout(self._surface)
        surf_lay.setContentsMargins(6, 6, 6, 6)
        surf_lay.setSpacing(0)

        scroll = QtWidgets.QScrollArea(self._surface)
        scroll.setObjectName("EmojiPickerScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setFixedHeight(260)

        inner = QtWidgets.QWidget()
        inner.setObjectName("EmojiPickerGridHost")
        self._grid_layout = QtWidgets.QGridLayout(inner)
        self._grid_layout.setSpacing(4)
        self._grid_layout.setContentsMargins(8, 6, 8, 6)
        self._repopulate_grid()

        scroll.setWidget(inner)
        surf_lay.addWidget(scroll)
        self.setFixedWidth(min(self._COLS * 44 + 24, 380))
        self.apply_theme(self._theme_id)

    def _repopulate_grid(self) -> None:
        from i2pchat.gui import emoji_paths as _ep

        grid = self._grid_layout
        if grid is None:
            return
        while (item := grid.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.deleteLater()
        paths = _ep.emoji_paths_cached()
        icon_logical = 30
        icon_sz = QtCore.QSize(icon_logical, icon_logical)
        for i, ch in enumerate(EMOJI_CHARS):
            r, c = divmod(i, self._COLS)
            btn = QtWidgets.QToolButton()
            btn.setObjectName("EmojiCell")
            btn.setAutoRaise(True)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            btn.setFixedSize(40, 40)
            btn.setAccessibleName(ch)
            btn.setToolTip(ch)
            fp = paths.get(ch)
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
            grid.addWidget(btn, r, c)

    def apply_theme(self, theme_id: str) -> None:
        self._theme_id = (theme_id or "").strip().lower()
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
                }
                QToolButton#EmojiCell:hover {
                    background: rgba(255, 255, 255, 0.10);
                }
                QScrollBar:vertical {
                    background: transparent;
                    width: 8px;
                    margin: 0px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(255, 255, 255, 0.20);
                    min-height: 24px;
                    border-radius: 4px;
                }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
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
                }
                QToolButton#EmojiCell:hover {
                    background: #e5eaf2;
                }
                QScrollBar:vertical {
                    background: transparent;
                    width: 8px;
                    margin: 0px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(60, 60, 67, 0.35);
                    min-height: 24px;
                    border-radius: 4px;
                }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
                """
            )

    def _pick(self, sym: str) -> None:
        self.emojiChosen.emit(sym)
        self.hide()

    def show_near_anchor(self, anchor: QtWidgets.QWidget, theme_id: str) -> None:
        self.apply_theme(theme_id)
        self.adjustSize()
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
            self.show()
            self.raise_()
            return
        geo = screen.availableGeometry()
        margin = 6
        x = max(geo.left() + margin, min(int(x), geo.right() - pw - margin + 1))
        y = max(geo.top() + margin, min(int(y), geo.bottom() - ph - margin + 1))
        self.move(x, y)
        self.show()
        self.raise_()


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

        self._emoji_btn = QtWidgets.QToolButton(self)
        self._emoji_btn.setObjectName("EmojiPickerButton")
        self._emoji_btn.setAutoRaise(True)
        self._emoji_btn.setToolTip("Emoji")
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
        self._popup.show_near_anchor(self._emoji_btn, self._theme_id)

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
