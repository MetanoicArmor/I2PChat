import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    render_braille,
    render_bw,
)


def get_profiles_dir() -> str:
    """Директория, где лежат/хранятся .dat профили (общая для dev и .app)."""
    base_dir = os.path.join(os.path.expanduser("~"), ".i2pchat")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


@dataclass
class ChatItem:
    kind: str
    timestamp: str
    sender: str
    text: str


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


class ChatListView(QtWidgets.QListView):
    """QListView, который перераскладывает элементы при изменении ширины.

    Это обеспечивает корректный перенос строк в баблах при ресайзе окна.
    """

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.doItemsLayout()
        self.viewport().update()


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
    BUBBLE_SPACING_Y = 10
    BUBBLE_RADIUS = 12

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

        is_me = item.kind in {"me", "image_braille", "image_bw"}
        rect = option.rect.adjusted(0, self.BUBBLE_SPACING_Y, 0, -self.BUBBLE_SPACING_Y)

        cell_width = rect.width()
        base_font = painter.font()
        # Бабл подстраивается под ширину строки; перенос текста зависит от окна
        bubble_width = max(80, cell_width - self.PADDING_X * 2)

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

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:  # type: ignore[override]
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return QtCore.QSize(0, 0)

        cell_width = option.rect.width() if option.rect.width() > 0 else 600
        font = option.font

        # Используем ту же ширину бабла, что и в paint()
        bubble_width = max(80, cell_width - self.PADDING_X * 2)
        available_width = max(10, bubble_width - self.PADDING_X * 2)

        metrics = QtGui.QFontMetrics(font)
        text = item.text or " "
        text_rect = metrics.boundingRect(
            0,
            0,
            int(available_width),
            1000,
            int(QtCore.Qt.TextFlag.TextWrapAnywhere),
            text,
        )
        # Делаем запас по высоте, чтобы текст не «прилипал» к краям
        height = text_rect.height() + self.PADDING_Y * 3 + self.BUBBLE_SPACING_Y * 2
        if item.timestamp:
            ts_font = QtGui.QFont(font)
            ts_font.setPointSize(max(font.pointSize() - 1, 6))
            ts_metrics = QtGui.QFontMetrics(ts_font)
            height += ts_metrics.height() + self.PADDING_Y

        return QtCore.QSize(int(cell_width), int(height))


class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("I2PChat")
        self.resize(900, 600)

        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #1e1f29;
            }
            QListView {
                background: #1e1f29;
                border: none;
                padding: 8px;
                color: #f8f8f2;
            }
            QScrollBar:vertical {
                background: #1e1f29;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #44475a;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QLineEdit {
                background: #282a36;
                border: 1px solid #44475a;
                border-radius: 4px;
                padding: 8px 10px;
                color: #f8f8f2;
            }
            QLineEdit:focus {
                border-color: #6272a4;
            }
            QPushButton {
                background-color: #44475a;
                border-radius: 4px;
                padding: 8px 10px;
                color: #f8f8f2;
            }
            QPushButton:hover {
                background-color: #6272a4;
            }
            QPushButton:pressed {
                background-color: #3a7afe;
            }
            QLabel {
                color: #f8f8f2;
            }
            """
        )

        self.profile = profile or "default"

        # UI
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(12)

        # статусная панель
        self.status_label = QtWidgets.QLabel("Status: initializing", self)
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self._last_status: str = "initializing"

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
        self.input_edit = QtWidgets.QLineEdit(self)
        self.input_edit.setPlaceholderText("Type message and press Enter...")
        self.input_edit.setMinimumHeight(56)
        font = self.input_edit.font()
        font.setPointSize(font.pointSize() + 1)
        self.input_edit.setFont(font)

        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setMinimumHeight(56)

        # Жёстко фиксируем высоту, чтобы поле и кнопка были на одном уровне
        fixed_height = 56
        self.input_edit.setFixedHeight(fixed_height)
        self.send_button.setFixedHeight(fixed_height)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_button)

        # панель действий (flow layout: кнопки переносятся на следующий ряд)
        actions_layout = FlowLayout()

        self.load_profile_button = QtWidgets.QPushButton("Load .dat", self)
        self.addr_edit = QtWidgets.QLineEdit(self)
        self.addr_edit.setPlaceholderText("Peer .b32.i2p address")
        # Адрес — главный элемент панели действий, даём ему больше ширины
        self.addr_edit.setMinimumWidth(260)

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        self.disconnect_button = QtWidgets.QPushButton("Disconnect", self)

        self.send_file_button = QtWidgets.QPushButton("Send File", self)
        self.send_img_braille_button = QtWidgets.QPushButton("Send Image (braille)", self)
        self.send_img_bw_button = QtWidgets.QPushButton("Send Image (bw)", self)
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
            self.send_img_braille_button,
            self.send_img_bw_button,
            self.lock_peer_button,
            self.copy_my_addr_button,
        ]:
            btn.setFixedHeight(actions_fixed_height)

        actions_layout.addWidget(self.load_profile_button)
        actions_layout.addWidget(self.addr_edit)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.disconnect_button)
        actions_layout.addWidget(self.send_file_button)
        actions_layout.addWidget(self.send_img_braille_button)
        actions_layout.addWidget(self.send_img_bw_button)
        actions_layout.addWidget(self.lock_peer_button)
        actions_layout.addWidget(self.copy_my_addr_button)

        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.chat_view, 1)
        main_layout.addLayout(input_layout)
        main_layout.addLayout(actions_layout)

        # сигналы
        self.send_button.clicked.connect(self.on_send_clicked)
        self.input_edit.returnPressed.connect(self.on_send_clicked)
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.send_file_button.clicked.connect(self.on_send_file_clicked)
        self.send_img_braille_button.clicked.connect(self.on_send_img_braille_clicked)
        self.send_img_bw_button.clicked.connect(self.on_send_img_bw_clicked)
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

    @QtCore.pyqtSlot(str)
    def handle_system(self, text: str) -> None:
        self._append_item(ChatItem(kind="system", timestamp="", sender="SYSTEM", text=text))

    @QtCore.pyqtSlot(str)
    def handle_error(self, text: str) -> None:
        self._append_item(ChatItem(kind="error", timestamp="", sender="ERROR", text=text))

    @QtCore.pyqtSlot(object)
    def handle_file_event(self, info: FileTransferInfo) -> None:
        self._append_item(
            ChatItem(
                kind="file",
                timestamp="",
                sender="FILE",
                text=f"{info.filename}: {info.received}/{info.size} bytes",
            )
        )

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
        return I2PChatCore(
            profile=profile or "default",
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=self.handle_file_event,
            on_image_received=self.handle_image_received,
        )

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
        else:
            stored_disp = "none"

        self.status_label.setText(
            f"Status: {status} | Profile: {self.profile} ({mode}) | Stored peer: {stored_disp}"
        )

    # ----- обработчики UI -----

    @QtCore.pyqtSlot()
    def on_send_clicked(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        asyncio.create_task(self.core.send_text(text))

    @QtCore.pyqtSlot()
    def on_connect_clicked(self) -> None:
        addr = self.addr_edit.text().strip()
        if not addr:
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

        key_file = f"{self.profile}.dat"
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
    base_font = QtGui.QFont("Inter", 13)
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

        item, ok = QtWidgets.QInputDialog.getItem(
            None,
            "Select profile",
            "Profile name (default = TRANSIENT):",
            profiles,
            0,
            True,  # editable: можно ввести новое имя
        )
        if not ok:
            return
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

