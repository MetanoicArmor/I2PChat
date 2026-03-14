import asyncio
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    get_profiles_dir,
    render_braille,
    render_bw,
)

try:
    from PyQt6.QtMultimedia import QSoundEffect  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - мультимедиа не везде доступно
    QSoundEffect = None  # type: ignore[assignment]


@dataclass
class ChatItem:
    kind: str
    timestamp: str
    sender: str
    text: str
    progress: float = 0.0
    file_size: int = 0
    is_sending: bool = False


class ChatListModel(QtCore.QAbstractListModel):
    """Простая модель для хранения элементов чата."""

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._items: List[ChatItem] = []

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._items)

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ):  # type: ignore[override]
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return item
        return None

    def add_item(self, item: ChatItem) -> None:
        row = len(self._items)
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self._items.append(item)
        self.endInsertRows()

    def update_item(self, row: int, item: ChatItem) -> None:
        if 0 <= row < len(self._items):
            self._items[row] = item
            index = self.index(row, 0)
            self.dataChanged.emit(index, index)


class ChatListView(QtWidgets.QListView):
    """QListView для баблов чата.

    - перераскладывает элементы при изменении ширины (для переноса строк)
    - поддерживает копирование текста (контекстное меню и Cmd/Ctrl+C)
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.doItemsLayout()
        self.viewport().update()

    def _copy_index_text(self, index: QtCore.QModelIndex, with_meta: bool = False) -> None:
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return
        if with_meta and item.timestamp:
            text = f"[{item.timestamp}] {item.sender}: {item.text}"
        else:
            text = item.text
        QtWidgets.QApplication.clipboard().setText(text)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return

        menu = QtWidgets.QMenu(self)
        act_copy = menu.addAction("Copy text")
        act_copy_meta = menu.addAction("Copy with timestamp")
        chosen = menu.exec(event.globalPos())
        if chosen == act_copy:
            self._copy_index_text(index, with_meta=False)
        elif chosen == act_copy_meta:
            self._copy_index_text(index, with_meta=True)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            index = self.currentIndex()
            if index.isValid():
                self._copy_index_text(index, with_meta=False)
                return
        super().keyPressEvent(event)


class FlowLayout(QtWidgets.QLayout):
    """
    Простейший FlowLayout: автоматически переносит виджеты на следующий ряд
    при сужении окна. Основан на примере из документации Qt.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, margin: int = 0, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QtWidgets.QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item: QtWidgets.QLayoutItem) -> None:  # type: ignore[override]
        self._items.append(item)

    def count(self) -> int:  # type: ignore[override]
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QtWidgets.QLayoutItem]:  # type: ignore[override]
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QtWidgets.QLayoutItem]:  # type: ignore[override]
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # type: ignore[override]
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def heightForWidth(self, width: int) -> int:  # type: ignore[override]
        return self._do_layout(QtCore.QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QtCore.QRect) -> None:  # type: ignore[override]
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        return self.minimumSize()

    def minimumSize(self) -> QtCore.QSize:  # type: ignore[override]
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QtCore.QSize(left + right, top + bottom)
        return size

    def _do_layout(self, rect: QtCore.QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            widget_size = item.sizeHint()
            space_x = self.spacing()
            space_y = self.spacing()
            next_x = x + widget_size.width() + space_x

            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + widget_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), widget_size))

            x = next_x
            line_height = max(line_height, widget_size.height())

        return y + line_height - rect.y() + bottom


class ChatItemDelegate(QtWidgets.QStyledItemDelegate):
    """Делегат, рисующий сообщения в виде цветных «баблов»."""

    # Базовая 8‑px сетка: все отступы и скругления кратны 4/8.
    PADDING_X = 12
    PADDING_Y = 8
    # Вертикальный зазор между баблами (меньше, чем было изначально)
    BUBBLE_SPACING_Y = 2
    BUBBLE_RADIUS = 12

    def _bubble_width(self, cell_width: int, text: str, font: QtGui.QFont) -> int:
        """
        Ширина бабла:
        - не меньше 40% строки (чтобы короткие фразы не выглядели «таблеткой» по центру)
        - не больше 75% строки (оставляем «воздух» по бокам)
        - но при этом ограничена реальной длиной текста + отступы
        """
        metrics = QtGui.QFontMetrics(font)
        text_w = metrics.horizontalAdvance(text or " ") + self.PADDING_X * 4
        min_w = int(cell_width * 0.4)
        max_w = int(cell_width * 0.75)
        return max(min_w, min(max_w, text_w))

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:  # type: ignore[override]
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return

        painter.save()

        if item.kind == "transfer":
            self._paint_transfer(painter, option, item)
            painter.restore()
            return

        is_me = item.kind in {"me", "image_braille", "image_bw"}
        rect = option.rect.adjusted(0, self.BUBBLE_SPACING_Y, 0, -self.BUBBLE_SPACING_Y)

        cell_width = rect.width()
        base_font = painter.font()
        bubble_width = self._bubble_width(cell_width, item.text, base_font)

        if is_me:
            bubble_rect = QtCore.QRectF(
                rect.right() - bubble_width,
                rect.top(),
                bubble_width,
                rect.height(),
            )
        else:
            bubble_rect = QtCore.QRectF(rect.left(), rect.top(), bubble_width, rect.height())

        if item.kind in {"me", "image_braille", "image_bw"}:
            bg_color = QtGui.QColor("#3a7afe")
            text_color = QtGui.QColor("#ffffff")
        elif item.kind == "peer":
            bg_color = QtGui.QColor("#7c3aed")  # комплиментарный фиолетовый
            text_color = QtGui.QColor("#f8f8f2")
        elif item.kind in {"system", "info"}:
            bg_color = QtGui.QColor("#282a36")
            text_color = QtGui.QColor("#8be9fd")
        elif item.kind == "error" or item.kind == "disconnect":
            bg_color = QtGui.QColor("#ff5555")
            text_color = QtGui.QColor("#f8f8f2")
        elif item.kind == "success":
            bg_color = QtGui.QColor("#50fa7b")
            text_color = QtGui.QColor("#282a36")
        elif item.kind == "file":
            bg_color = QtGui.QColor("#6272a4")
            text_color = QtGui.QColor("#f8f8f2")
        else:
            bg_color = QtGui.QColor("#44475a")
            text_color = QtGui.QColor("#f8f8f2")

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(bg_color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            self.PADDING_Y / 2,
            -self.PADDING_X / 2,
            -self.PADDING_Y / 2,
        )
        painter.drawRoundedRect(bubble_rect, self.BUBBLE_RADIUS, self.BUBBLE_RADIUS)

        inner_rect = bubble_rect.adjusted(
            self.PADDING_X, self.PADDING_Y, -self.PADDING_X, -self.PADDING_Y
        )

        painter.setPen(text_color)
        painter.setFont(base_font)

        full_text = item.text
        metrics = QtGui.QFontMetrics(base_font)

        # Если есть таймстамп – резервируем под него одну строку снизу
        if item.timestamp:
            ts_height = metrics.height()
            text_area = QtCore.QRectF(
                inner_rect.left(),
                inner_rect.top(),
                inner_rect.width(),
                inner_rect.height() - ts_height - self.PADDING_Y / 2,
            )
            ts_rect = QtCore.QRectF(
                inner_rect.left(),
                inner_rect.bottom() - ts_height,
                inner_rect.width(),
                ts_height,
            )
        else:
            text_area = inner_rect
            ts_rect = None

        text_option = QtGui.QTextOption()
        # Адреса и ключи приходят без пробелов, поэтому разрешаем перенос в любой точке
        text_option.setWrapMode(QtGui.QTextOption.WrapMode.WrapAnywhere)
        text_option.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        painter.drawText(text_area, full_text, text_option)

        if ts_rect is not None and item.timestamp:
            ts_font = QtGui.QFont(base_font)
            ts_font.setPointSize(max(base_font.pointSize() - 1, 6))
            painter.setFont(ts_font)

            # Цвет штампа делаем чуть темнее текста для контраста на ярком фоне
            if item.kind == "success":
                ts_color = QtGui.QColor("#15542d")
            elif item.kind in {"me", "image_braille", "image_bw"}:
                ts_color = QtGui.QColor("#d0e2ff")
            else:
                # слегка осветляем основной текстовый цвет
                ts_color = QtGui.QColor(text_color)
                ts_color = ts_color.lighter(130)

            painter.setPen(ts_color)
            painter.drawText(
                ts_rect,
                int(
                    QtCore.Qt.AlignmentFlag.AlignRight
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                item.timestamp,
            )

        painter.restore()

    def _paint_transfer(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        item: ChatItem,
    ) -> None:
        rect = option.rect.adjusted(0, self.BUBBLE_SPACING_Y, 0, -self.BUBBLE_SPACING_Y)
        cell_width = rect.width()
        bubble_width = int(cell_width * 0.6)
        
        is_sending = item.is_sending
        if is_sending:
            bubble_rect = QtCore.QRectF(
                rect.right() - bubble_width - self.PADDING_X,
                rect.top(),
                bubble_width,
                rect.height(),
            )
        else:
            bubble_rect = QtCore.QRectF(
                rect.left() + self.PADDING_X,
                rect.top(),
                bubble_width,
                rect.height(),
            )

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        
        bg_color = QtGui.QColor("#1e3a5f") if is_sending else QtGui.QColor("#2d1b4e")
        painter.setBrush(bg_color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            self.PADDING_Y / 2,
            -self.PADDING_X / 2,
            -self.PADDING_Y / 2,
        )
        painter.drawRoundedRect(bubble_rect, self.BUBBLE_RADIUS, self.BUBBLE_RADIUS)
        
        inner_rect = bubble_rect.adjusted(
            self.PADDING_X, self.PADDING_Y, -self.PADDING_X, -self.PADDING_Y
        )
        
        base_font = painter.font()
        metrics = QtGui.QFontMetrics(base_font)
        
        action = "↑ Sending" if is_sending else "↓ Receiving"
        header_text = f"{action}: {item.text}"
        painter.setPen(QtGui.QColor("#ffffff"))
        header_rect = QtCore.QRectF(
            inner_rect.left(),
            inner_rect.top(),
            inner_rect.width(),
            metrics.height(),
        )
        painter.drawText(
            header_rect,
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(header_text, QtCore.Qt.TextElideMode.ElideMiddle, int(header_rect.width())),
        )
        
        bar_height = 18
        bar_rect = QtCore.QRectF(
            inner_rect.left(),
            inner_rect.top() + metrics.height() + 8,
            inner_rect.width(),
            bar_height,
        )
        
        painter.setBrush(QtGui.QColor("#0d1b2a"))
        painter.drawRoundedRect(bar_rect, bar_height / 2, bar_height / 2)
        
        progress = max(0.0, min(1.0, item.progress))
        if progress > 0:
            fill_width = max(bar_height, bar_rect.width() * progress)
            fill_rect = QtCore.QRectF(bar_rect.left(), bar_rect.top(), fill_width, bar_height)
            
            gradient = QtGui.QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            if is_sending:
                gradient.setColorAt(0.0, QtGui.QColor("#0066cc"))
                gradient.setColorAt(0.5, QtGui.QColor("#3399ff"))
                gradient.setColorAt(1.0, QtGui.QColor("#0066cc"))
            else:
                gradient.setColorAt(0.0, QtGui.QColor("#7c3aed"))
                gradient.setColorAt(0.5, QtGui.QColor("#a78bfa"))
                gradient.setColorAt(1.0, QtGui.QColor("#7c3aed"))
            
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill_rect, bar_height / 2, bar_height / 2)
            
            pulse = (time.time() % 1.0)
            glow_alpha = int(40 + 30 * math.sin(pulse * math.pi * 2))
            glow_color = QtGui.QColor(255, 255, 255, glow_alpha)
            painter.setBrush(glow_color)
            painter.drawRoundedRect(fill_rect, bar_height / 2, bar_height / 2)
        
        pct = int(progress * 100)
        pct_text = f"{pct}%"
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.drawText(
            bar_rect,
            int(QtCore.Qt.AlignmentFlag.AlignCenter),
            pct_text,
        )
        
        if item.file_size > 0:
            received = int(item.file_size * progress)
            if item.file_size >= 1024 * 1024:
                size_text = f"{received / (1024*1024):.1f} / {item.file_size / (1024*1024):.1f} MB"
            elif item.file_size >= 1024:
                size_text = f"{received / 1024:.0f} / {item.file_size / 1024:.0f} KB"
            else:
                size_text = f"{received} / {item.file_size} B"
            
            small_font = QtGui.QFont(base_font)
            small_font.setPointSize(max(base_font.pointSize() - 2, 8))
            painter.setFont(small_font)
            painter.setPen(QtGui.QColor("#a0a0a0"))
            size_rect = QtCore.QRectF(
                inner_rect.left(),
                bar_rect.bottom() + 4,
                inner_rect.width(),
                metrics.height(),
            )
            painter.drawText(
                size_rect,
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop),
                size_text,
            )

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:  # type: ignore[override]
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return QtCore.QSize(0, 0)
        
        if item.kind == "transfer":
            cell_width = option.rect.width() if option.rect.width() > 0 else 600
            height = self.PADDING_Y * 4 + 18 + 18 + 20 + self.BUBBLE_SPACING_Y * 2
            return QtCore.QSize(int(cell_width), int(height))

        cell_width = option.rect.width() if option.rect.width() > 0 else 600
        font = option.font

        # Используем ту же ширину бабла, что и в paint()
        bubble_width = self._bubble_width(cell_width, item.text, font)
        available_width = max(10, bubble_width - self.PADDING_X * 2)

        text = item.text or " "

        # Используем QTextDocument с WrapAnywhere, чтобы корректно посчитать
        # высоту многострочного текста (списки, длинные строки и т.п.).
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(font)
        doc.setPlainText(text)
        text_option = QtGui.QTextOption()
        text_option.setWrapMode(QtGui.QTextOption.WrapMode.WrapAnywhere)
        doc.setDefaultTextOption(text_option)
        doc.setTextWidth(float(available_width))
        text_height = doc.size().height()

        # Высота самого бабла оставляем приблизительно как раньше —
        # добавляем вертикальные отступы вокруг текста и внешний зазор.
        height = int(text_height) + self.PADDING_Y * 3 + self.BUBBLE_SPACING_Y * 2
        if item.timestamp:
            ts_font = QtGui.QFont(font)
            ts_font.setPointSize(max(font.pointSize() - 1, 6))
            ts_metrics = QtGui.QFontMetrics(ts_font)
            height += ts_metrics.height() + self.PADDING_Y

        return QtCore.QSize(int(cell_width), int(height))


class MessageInputEdit(QtWidgets.QPlainTextEdit):
    """Многострочное поле ввода: Enter — отправить, Shift+Enter — новая строка."""
    sendRequested = QtCore.pyqtSignal()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
                return
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: Optional[str] = None) -> None:
        super().__init__()
        self.profile = profile or "default"
        # Показываем профиль через разделитель-точку;
        # если вдруг имя профиля уже содержит служебный маркер в конце (" •"),
        # аккуратно убираем его, чтобы заголовок не заканчивался кружком.
        clean_profile = self.profile.rstrip(" •")
        self.setWindowTitle(f"I2PChat • {clean_profile}")
        self.resize(900, 600)

        # Тёмная макос‑подобная гамма (в духе Big Sur)
        status_font_px = 9 if sys.platform == "win32" else 11
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #141417;
            }
            QListView {
                background: #141417;
                border: none;
                padding: 8px;
                color: #f5f5f7;
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
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QLineEdit, QPlainTextEdit {
                background: #1f1f23;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 8px 10px;
                color: #f5f5f7;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #0a84ff;
            }
            QPushButton {
                background-color: #2b2b30;
                border-radius: 8px;
                padding: 8px 14px;
                color: #f5f5f7;
            }
            QPushButton:hover {
                background-color: #3a3a40;
            }
            QPushButton:pressed {
                background-color: #0a84ff;
            }
            QLabel {
                color: #f5f5f7;
            }
            QLabel#StatusLabel {
                background-color: #1b1b1f;
                border-radius: 10px;
                padding: 4px 10px;
                color: #9fa1b5;
                font-size: %(status_font_px)spx;
            }
            """
            % {"status_font_px": status_font_px}
        )

        # UI
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(12)

        # статусная панель
        self.status_label = QtWidgets.QLabel("Status: initializing", self)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self._last_status: str = "initializing"
        self._transfer_row: Optional[int] = None

        # Таймер для анимации прогресс-бара
        self._transfer_timer = QtCore.QTimer(self)
        self._transfer_timer.timeout.connect(self._animate_transfer)
        self._transfer_timer.setInterval(50)

        # основной чат
        self.chat_view = ChatListView(self)
        self.chat_view.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.chat_view.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chat_model = ChatListModel(self.chat_view)
        self.chat_view.setModel(self.chat_model)
        self.chat_view.setItemDelegate(ChatItemDelegate(self.chat_view))

        # панель ввода
        input_layout = QtWidgets.QHBoxLayout()
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)
        self.input_edit = MessageInputEdit(self)
        self.input_edit.setPlaceholderText("Type message. Enter to send, Shift+Enter for new line.")
        self.input_edit.setMinimumHeight(56)
        font = self.input_edit.font()
        font.setPointSize(font.pointSize() + 1)
        self.input_edit.setFont(font)

        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setMinimumHeight(56)

        fixed_height = 56
        self.input_edit.setFixedHeight(fixed_height)
        self.send_button.setFixedHeight(fixed_height)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_button)

        # панель действий: простой горизонтальный ряд кнопок с полем адреса
        actions_layout = QtWidgets.QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)

        self.load_profile_button = QtWidgets.QPushButton("Load .dat", self)
        self.addr_edit = QtWidgets.QLineEdit(self)
        self.addr_edit.setPlaceholderText("Peer .b32.i2p address")
        # Адрес — главный элемент панели действий
        self.addr_edit.setMinimumWidth(220)
        self.addr_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        self.disconnect_button = QtWidgets.QPushButton("Disconnect", self)

        self.send_file_button = QtWidgets.QPushButton("Send File", self)
        self.lock_peer_button = QtWidgets.QPushButton("Lock to peer", self)
        self.copy_my_addr_button = QtWidgets.QPushButton("Copy My Addr", self)

        # Все элементы панели действий делаем одной высоты, чтобы ряд смотрелся ровно
        actions_fixed_height = 36
        self.addr_edit.setFixedHeight(actions_fixed_height)
        for btn in [
            self.load_profile_button,
            self.connect_button,
            self.disconnect_button,
            self.send_file_button,
            self.lock_peer_button,
            self.copy_my_addr_button,
        ]:
            btn.setFixedHeight(actions_fixed_height)

        actions_layout.addWidget(self.load_profile_button)
        actions_layout.addWidget(self.addr_edit)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.disconnect_button)
        actions_layout.addWidget(self.send_file_button)
        actions_layout.addWidget(self.lock_peer_button)
        actions_layout.addWidget(self.copy_my_addr_button)

        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.chat_view, 1)
        main_layout.addLayout(input_layout)
        main_layout.addLayout(actions_layout)

        # системный трей/док‑иконка для показа нативных уведомлений от Qt
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation
            )
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("I2PChat")
        self.tray_icon.show()

        # более мягкий системный звук вместо жёсткого beep,
        # там, где доступен QtMultimedia.
        self.notify_sound: Optional["QSoundEffect"] = None
        if QSoundEffect is not None:
            try:
                effect = QSoundEffect(self)
                # Для macOS берём один из стандартных системных звуков.
                if sys.platform == "darwin":
                    sound_path = "/System/Library/Sounds/Glass.aiff"
                else:
                    sound_path = ""

                if sound_path and os.path.exists(sound_path):
                    effect.setSource(QtCore.QUrl.fromLocalFile(sound_path))
                    effect.setVolume(0.7)
                    self.notify_sound = effect
            except Exception:
                self.notify_sound = None

        # сигналы
        self.send_button.clicked.connect(self.on_send_clicked)
        self.input_edit.sendRequested.connect(self.on_send_clicked)
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.send_file_button.clicked.connect(self.on_send_file_clicked)
        self.lock_peer_button.clicked.connect(self.on_lock_peer_clicked)
        self.copy_my_addr_button.clicked.connect(self.on_copy_my_addr_clicked)
        self.load_profile_button.clicked.connect(self.on_load_profile_clicked)

        # ядро
        self.core = self._create_core(self.profile)

    def _append_item(self, item: ChatItem) -> None:
        """Добавить элемент в модель и прокрутить к нему."""
        self.chat_model.add_item(item)
        row = self.chat_model.rowCount() - 1
        if row >= 0:
            index = self.chat_model.index(row, 0)
            self.chat_view.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtBottom)

    # ----- callbacks из ядра -----

    @QtCore.pyqtSlot(str)
    def handle_status(self, status: str) -> None:
        self._last_status = status
        self.refresh_status_label()

    @QtCore.pyqtSlot(object)
    def handle_message(self, msg: ChatMessage) -> None:
        kind = msg.kind
        ts = msg.timestamp.strftime("%H:%M:%S")
        text = msg.text
        if kind == "peer":
            sender = self.profile if self.profile != "default" else "Peer"
        elif kind == "me":
            sender = "Me"
        elif kind == "error":
            sender = "ERROR"
        elif kind == "success":
            sender = "OK"
        elif kind == "disconnect":
            sender = "X"
        elif kind == "help":
            sender = "HELP"
        elif kind == "info":
            sender = "INFO"
        else:
            sender = "SYSTEM"

        self._append_item(ChatItem(kind=kind, timestamp=ts, sender=sender, text=text))

    @QtCore.pyqtSlot(object)
    def handle_notify(self, msg: ChatMessage) -> None:
        """
        Колбэк уведомлений от ядра: системный тост + звук.

        Используем только для входящих peer‑сообщений.
        """
        if not isinstance(msg, ChatMessage) or msg.kind != "peer":
            return

        preview = msg.text.replace("\n", " ")
        title = "New message"
        if self.core.current_peer_addr:
            clean_peer = self.core.current_peer_addr.replace(".b32.i2p", "")
            if len(clean_peer) > 12:
                clean_peer = f"{clean_peer[:6]}..{clean_peer[-6:]}"
            title = f"New message from {clean_peer}"

        # Системное уведомление и звук показываем только если окно/приложение
        # не активно (свернуто или в фоне). Если пользователь уже в окне,
        # полагаемся на визуальный интерфейс без спама уведомлениями.
        app = QtWidgets.QApplication.instance()
        is_app_active = (
            app is not None
            and app.applicationState()
            == QtCore.Qt.ApplicationState.ApplicationActive
        )
        is_window_active = self.isActiveWindow() and not self.isMinimized()

        if not (is_app_active and is_window_active):
            # Показываем нативное уведомление через Qt (system tray / Notification Center).
            if self.tray_icon is not None:
                self.tray_icon.showMessage(
                    title,
                    preview,
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )

            # Звук: сначала пытаемся проиграть более мягкий системный звук,
            # если он доступен, иначе падаем обратно на стандартный beep.
            if self.notify_sound is not None:
                try:
                    self.notify_sound.play()
                except Exception:
                    QtWidgets.QApplication.beep()
            else:
                QtWidgets.QApplication.beep()

        # Отдельный маркер в заголовке для непрочитанных больше не используем:
        # основным индикатором служит само уведомление и содержимое чата.

    @QtCore.pyqtSlot(str)
    def handle_system(self, text: str) -> None:
        self._append_item(ChatItem(kind="system", timestamp="", sender="SYSTEM", text=text))

    @QtCore.pyqtSlot(str)
    def handle_error(self, text: str) -> None:
        self._append_item(ChatItem(kind="error", timestamp="", sender="ERROR", text=text))

    def _animate_transfer(self) -> None:
        if self._transfer_row is not None:
            index = self.chat_model.index(self._transfer_row, 0)
            self.chat_model.dataChanged.emit(index, index)

    @QtCore.pyqtSlot(object)
    def handle_file_event(self, info: FileTransferInfo) -> None:
        progress = info.received / info.size if info.size > 0 else 0.0

        # Начало передачи
        if info.received == 0 and info.size > 0:
            # Для входящих файлов спрашиваем подтверждение
            if not info.is_sending:
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Incoming file",
                    f"Accept incoming file?\n\n{info.filename} ({info.size} bytes)",
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.Yes,
                )
                if answer == QtWidgets.QMessageBox.StandardButton.No:
                    try:
                        if self.core.incoming_file:  # type: ignore[attr-defined]
                            try:
                                self.core.incoming_file.close()  # type: ignore[attr-defined]
                            except Exception:
                                pass
                        if info.filename and os.path.exists(info.filename):
                            try:
                                os.remove(info.filename)
                            except Exception:
                                pass
                        self.core.incoming_file = None  # type: ignore[attr-defined]
                        self.core.incoming_info = None  # type: ignore[attr-defined]
                    except Exception:
                        pass

                    self._append_item(
                        ChatItem(
                            kind="error",
                            timestamp="",
                            sender="FILE",
                            text=f"Incoming file rejected: {info.filename}",
                        )
                    )
                    return

            # Создаём сообщение прогресса в чате
            self._append_item(
                ChatItem(
                    kind="transfer",
                    timestamp="",
                    sender="FILE",
                    text=info.filename,
                    progress=0.0,
                    file_size=info.size,
                    is_sending=info.is_sending,
                )
            )
            self._transfer_row = self.chat_model.rowCount() - 1
            self._transfer_timer.start()
            return

        # Обновление прогресса
        if self._transfer_row is not None and 0 < info.received < info.size:
            self.chat_model.update_item(
                self._transfer_row,
                ChatItem(
                    kind="transfer",
                    timestamp="",
                    sender="FILE",
                    text=info.filename,
                    progress=progress,
                    file_size=info.size,
                    is_sending=info.is_sending,
                ),
            )
            return

        # Ошибка передачи (received=-1)
        if info.received < 0:
            self._transfer_timer.stop()
            if self._transfer_row is not None:
                self.chat_model.update_item(
                    self._transfer_row,
                    ChatItem(
                        kind="error",
                        timestamp="",
                        sender="FILE",
                        text=f"Transfer failed: {info.filename}",
                    ),
                )
                self._transfer_row = None
            return

        # Завершение передачи
        if info.received >= info.size:
            self._transfer_timer.stop()
            done_action = "sent" if info.is_sending else "received"
            if self._transfer_row is not None:
                self.chat_model.update_item(
                    self._transfer_row,
                    ChatItem(
                        kind="success",
                        timestamp="",
                        sender="FILE",
                        text=f"✔ File {done_action}: {info.filename} ({info.size:,} bytes)",
                    ),
                )
                self._transfer_row = None

    @QtCore.pyqtSlot(str)
    def handle_image_received(self, art: str) -> None:
        self._append_item(
            ChatItem(
                kind="image",
                timestamp="",
                sender="IMAGE",
                text=art,
            )
        )

    @QtCore.pyqtSlot(object)
    def handle_peer_changed(self, peer: Optional[str]) -> None:
        if peer:
            self.addr_edit.setText(peer)
        self.refresh_status_label()

    def _create_core(self, profile: Optional[str]) -> I2PChatCore:
        core = I2PChatCore(
            profile=profile or "default",
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=self.handle_file_event,
            on_image_received=self.handle_image_received,
        )
        # динамически навешиваем колбэк уведомлений,
        # чтобы не менять публичную сигнатуру конструктора ядра
        setattr(core, "on_notify", self.handle_notify)
        return core

    def refresh_status_label(self) -> None:
        """Обновить строку статуса с учётом профиля и persist-режима."""
        status = self._last_status
        mode = "PERSISTENT" if self.profile != "default" else "TRANSIENT"
        stored = self.core.stored_peer
        if stored:
            clean = stored.replace(".b32.i2p", "")
            if len(clean) > 12:
                clean = f"{clean[:6]}..{clean[-6:]}"
            stored_disp = clean + ".b32.i2p"
            # Если пользователь ещё не ввёл адрес вручную, подставляем сохранённый контакт.
            if not self.addr_edit.text().strip():
                # stored уже содержит полный адрес (с суффиксом), используем как есть.
                self.addr_edit.setText(stored)
        else:
            stored_disp = "none"

        self.status_label.setText(
            f"Status: {status} | Profile: {self.profile} ({mode}) | Stored peer: {stored_disp}"
        )

    # ----- обработчики UI -----

    @QtCore.pyqtSlot()
    def on_send_clicked(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        asyncio.create_task(self.core.send_text(text))

    @QtCore.pyqtSlot()
    def on_connect_clicked(self) -> None:
        addr = self.addr_edit.text().strip()
        if not addr:
            # Если адрес не введён, но есть сохранённый контакт, используем его.
            if self.core.stored_peer:
                addr = self.core.stored_peer
                self.addr_edit.setText(addr)
            else:
                QtWidgets.QMessageBox.warning(
                    self, "Connect", "Please enter peer address"
                )
                return
        asyncio.create_task(self.core.connect_to_peer(addr))

    @QtCore.pyqtSlot()
    def on_disconnect_clicked(self) -> None:
        asyncio.create_task(self.core.disconnect())

    @QtCore.pyqtSlot()
    def on_lock_peer_clicked(self) -> None:
        if self.profile == "default":
            QtWidgets.QMessageBox.warning(
                self,
                "Lock to peer",
                "Cannot lock in TRANSIENT mode. Restart with a profile name.",
            )
            return

        if self.core.stored_peer:
            QtWidgets.QMessageBox.information(
                self,
                "Lock to peer",
                f"Profile already locked to:\n{self.core.stored_peer}",
            )
            return

        if not self.core.current_peer_addr:
            QtWidgets.QMessageBox.warning(
                self,
                "Lock to peer",
                "Peer address not yet verified.\nEstablish a connection first.",
            )
            return

        # Всегда сохраняем .dat в общей папке профилей в домашней директории,
        # чтобы это работало и из .app, и из dev‑окружения.
        key_file = os.path.join(get_profiles_dir(), f"{self.profile}.dat")
        try:
            with open(key_file, "a", encoding="utf-8") as f:
                f.write(self.core.current_peer_addr + "\n")
            self.core.stored_peer = self.core.current_peer_addr
            self.handle_system(
                f"Identity {self.profile} is now locked to this peer."
            )
            self.refresh_status_label()
        except Exception as e:  # pragma: no cover - GUI path
            self.handle_error(f"Failed to save: {e}")

    @QtCore.pyqtSlot()
    def on_copy_my_addr_clicked(self) -> None:
        if not self.core.my_dest:
            QtWidgets.QMessageBox.warning(
                self,
                "Copy My Addr",
                "Local destination is not initialized yet.",
            )
            return

        addr = self.core.my_dest.base32 + ".b32.i2p"
        QtWidgets.QApplication.clipboard().setText(addr)
        self.handle_system("My address copied to clipboard.")

    @QtCore.pyqtSlot()
    def on_load_profile_clicked(self) -> None:
        """Выбор .dat профиля и переключение на него."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select profile (.dat)",
            get_profiles_dir(),
            "Profile files (*.dat)",
        )
        if not path:
            return
        base = os.path.splitext(os.path.basename(path))[0]

        # Копируем выбранный .dat в папку профилей, чтобы ядро его увидело
        dest_path = os.path.join(get_profiles_dir(), f"{base}.dat")
        if os.path.abspath(path) != os.path.abspath(dest_path):
            try:
                shutil.copy2(path, dest_path)
            except Exception as e:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(
                    self,
                    "Load .dat",
                    f"Не удалось скопировать профиль:\n{e}",
                )
                return

        asyncio.create_task(self.switch_profile(base))

    async def switch_profile(self, profile: str) -> None:
        """Переключиться на другой профиль (.dat)."""
        await self.core.shutdown()
        self.profile = profile
        clean_profile = self.profile.rstrip(" •")
        self.setWindowTitle(f"I2PChat • {clean_profile}")
        self.core = self._create_core(self.profile)
        self.refresh_status_label()
        await self.core.init_session()

    @QtCore.pyqtSlot()
    def on_send_file_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select file to send"
        )
        if not path:
            return
        asyncio.create_task(self.core.send_file(path))

    @QtCore.pyqtSlot()
    def on_send_img_braille_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select image to send (braille)"
        )
        if not path:
            return
        lines = render_braille(path)
        art = "\n".join(lines)
        self._append_item(
            ChatItem(
                kind="image_braille",
                timestamp="",
                sender="Me",
                text=art,
            )
        )
        asyncio.create_task(self.core.send_image_lines(lines))

    @QtCore.pyqtSlot()
    def on_send_img_bw_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select image to send (bw)"
        )
        if not path:
            return
        lines = render_bw(path)
        art = "\n".join(lines)
        self._append_item(
            ChatItem(
                kind="image_bw",
                timestamp="",
                sender="Me",
                text=art,
            )
        )
        asyncio.create_task(self.core.send_image_lines(lines))

    async def start_core(self) -> None:
        await self.core.init_session()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Останавливаем ядро и event loop при закрытии окна."""
        loop = asyncio.get_event_loop()

        async def _shutdown() -> None:
            try:
                await self.core.shutdown()
            finally:
                loop.stop()

        asyncio.ensure_future(_shutdown())
        event.accept()


def main() -> None:
    """Точка входа без qasync.run, чтобы избежать падений при завершении."""
    if hasattr(sip, "setdestroyonexit"):
        sip.setdestroyonexit(False)

    # Создаём единственный экземпляр QApplication
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # Единый стиль и шрифт для всех платформ (более предсказуемый рендеринг)
    app.setStyle("Fusion")
    # На Windows шрифты по умолчанию выглядят крупнее — задаём меньший размер
    font_pt = 10 if sys.platform == "win32" else 13
    base_font = QtGui.QFont("Inter", font_pt)
    base_font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
    app.setFont(base_font)

    # 1) если профиль передан аргументом (CLI), используем его как есть
    if len(sys.argv) > 1:
        profile: Optional[str] = sys.argv[1]
    else:
        # 2) для .app / обычного запуска без аргументов показываем диалог выбора профиля
        profiles = ["default"]
        # ищем *.dat в папке профилей в домашней директории
        for name in os.listdir(get_profiles_dir()):
            if name.endswith(".dat"):
                base = os.path.splitext(name)[0]
                if base not in profiles:
                    profiles.append(base)

        dialog = QtWidgets.QInputDialog(None)
        dialog.setWindowTitle("Select profile")
        # Короткий английский текст с дополнительным вертикальным отступом между строками
        dialog.setLabelText(
            "<html>"
            "Profile name (default = TRANSIENT).<br><br>"
            "Pick from the list,<br>"
            "or type a new name to save keys:"
            "</html>"
        )
        dialog.setComboBoxItems(profiles)
        dialog.setComboBoxEditable(True)
        # Даём тексту чуть больше воздуха по ширине, не растягивая слишком сильно
        dialog.setFixedWidth(360)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        item = dialog.textValue()
        profile = item.strip() or None
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = ChatWindow(profile=profile)
    window.show()

    # запускаем инициализацию ядра в Qt-совместимом event loop
    asyncio.ensure_future(window.start_core())

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()

