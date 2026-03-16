import asyncio
import json
import math
import os
import subprocess
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    get_downloads_dir,
    get_profiles_dir,
    get_images_dir,
    render_braille,
    render_bw,
)

try:
    from PyQt6.QtMultimedia import QSoundEffect  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - мультимедиа не везде доступно
    QSoundEffect = None  # type: ignore[assignment]

def _read_version() -> str:
    for p in (__file__, "."):
        vf = os.path.join(os.path.dirname(os.path.abspath(p)), "VERSION")
        if os.path.isfile(vf):
            with open(vf) as f:
                return f.read().strip()
    return "0.0.0"

APP_VERSION = _read_version()
BUNDLED_NOTIFY_SOUND_REL = "assets/sounds/notify.wav"


def _resolve_local_asset(filename: str) -> Optional[str]:
    # 1) Bundled path for PyInstaller/Nuitka-like frozen apps.
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        candidate = os.path.join(meipass, filename)
        if os.path.isfile(candidate):
            return candidate

    # 2) Directory of executable (useful for packaged app layouts).
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate = os.path.join(exe_dir, filename)
        if os.path.isfile(candidate):
            return candidate

    # 3) Source/dev paths.
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.path.join(os.getcwd(), filename),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _default_notify_sound_path() -> Optional[str]:
    return _resolve_local_asset(BUNDLED_NOTIFY_SOUND_REL)


THEME_DEFAULT = "ligth"

THEMES: dict[str, dict[str, object]] = {
    "ligth": {
        "label": "ligth",
        "dialog_stylesheet": """
            QDialog { background-color: #f5f5f7; }
            QLabel { color: #1d1d1f; }
            QComboBox {
                background: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                color: #1d1d1f;
                min-height: 20px;
            }
            QComboBox:hover { background: #f7f8fb; }
            QComboBox:focus { background: #ffffff; border: 1px solid #0a84ff; }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 28px;
                background: transparent;
                border-left: none;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d7dbe4;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
                outline: none;
            }
            QPushButton {
                background-color: #f0f2f6;
                border-radius: 8px;
                padding: 10px 24px;
                color: #1d1d1f;
                min-width: 115px;
            }
            QPushButton:hover { background-color: #e8ecf4; }
            QPushButton:pressed { background-color: #cfd0d8; }
            QPushButton#SecondaryButton {
                background-color: #e6ebf3;
                color: #2d3442;
            }
            QPushButton#SecondaryButton:hover { background-color: #dfe6f0; }
            QPushButton#SecondaryButton:pressed { background-color: #d5deeb; }
            QPushButton#PrimaryButton { background-color: #0a84ff; color: #ffffff; }
            QPushButton#PrimaryButton:hover { background-color: #409cff; }
        """,
        "window_stylesheet": """
            QMainWindow { background-color: #eceff4; }
            QWidget#ChatSurface {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid #d6dce7;
                border-radius: 14px;
            }
            QListView {
                background: #ffffff;
                border: none;
                border-radius: 12px;
                padding: 10px;
                color: #1d1d1f;
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
            QLineEdit, QPlainTextEdit {
                background: #ffffff;
                border: none;
                border-radius: 9px;
                padding: 8px 10px;
                color: #1d1d1f;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                background: #ffffff;
                border: 1px solid #0a84ff;
            }
            QWidget#ComposeBar, QWidget#ActionToolbar {
                background: rgba(255, 255, 255, 0.92);
                border: none;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #ffffff;
                border-radius: 9px;
                padding: 8px 14px;
                color: #20232b;
                border: none;
            }
            QPushButton:hover {
                background-color: #f6f8fc;
            }
            QPushButton:pressed { background-color: #edf1f8; }
            QPushButton#PrimaryActionButton {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QPushButton#PrimaryActionButton:hover { background-color: #2d95ff; }
            QPushButton#PrimaryActionButton:pressed { background-color: #0076e9; }
            QPushButton#SecondaryActionButton {
                background-color: #f3f5f9;
                color: #3f4757;
            }
            QPushButton#SecondaryActionButton:hover { background-color: #eaedf4; }
            QPushButton#SecondaryActionButton:pressed { background-color: #dbe5f5; }
            QPushButton#DangerActionButton {
                background-color: #e6ebf3;
                color: #b94b45;
            }
            QPushButton#DangerActionButton:hover { background-color: #dfe6f0; }
            QPushButton#DangerActionButton:pressed { background-color: #d5deeb; }
            QPushButton#GhostActionButton {
                background-color: #f8f9fc;
                color: #4a5362;
            }
            QPushButton#GhostActionButton:hover { background-color: #eef2f8; }
            QToolButton#MoreActionsButton {
                background-color: #eef2f7;
                border: none;
                border-radius: 9px;
                color: #333845;
                padding: 4px 12px;
                font-size: 18px;
                min-width: 32px;
            }
            QToolButton#MoreActionsButton:hover {
                background-color: #e1e7f0;
            }
            QToolButton#MoreActionsButton:pressed {
                background-color: #d7e0ec;
            }
            QToolButton#ThemeSwitchButton {
                background-color: #ffffff;
                border: none;
                border-radius: 9px;
                color: #333845;
                padding: 2px;
                min-width: 30px;
                min-height: 30px;
            }
            QToolButton#ThemeSwitchButton:hover {
                background-color: #f6f8fc;
            }
            QToolButton#ThemeSwitchButton:pressed {
                background-color: #edf1f8;
            }
            QLabel { color: #1d1d1f; }
            QLabel#StatusLabel {
                background-color: rgba(255, 255, 255, 0.85);
                border: none;
                border-radius: 10px;
                padding: 4px 10px;
                color: #525966;
                font-size: %(status_font_px)spx;
            }
            QMessageBox { background-color: #f5f5f7; color: #1d1d1f; }
            QMessageBox QLabel { color: #1d1d1f; }
            QMessageBox QPushButton {
                background-color: #ffffff;
                border-radius: 6px;
                padding: 6px 16px;
                color: #1d1d1f;
                min-width: 70px;
                border: none;
            }
            QMessageBox QPushButton:hover { background-color: #f6f8fc; }
            QMessageBox QPushButton:pressed { background-color: #edf1f8; }
            QMenu {
                background: #f6f7fa;
                border: none;
                border-radius: 14px;
                padding: 8px;
            }
            QMenu::item {
                color: #2c3442;
                padding: 9px 14px;
                border-radius: 9px;
                background: transparent;
            }
            QMenu::item:selected {
                background: #e5eaf2;
            }
            QMenu::separator {
                height: 1px;
                margin: 6px 8px;
                background: #d6dce7;
            }
        """,
        "bubbles": {
            "me_bg": "#2f92f0",
            "me_text": "#ffffff",
            "peer_bg": "#eceef2",
            "peer_text": "#1c1c1e",
            "system_bg": "#f1f3f7",
            "system_text": "#5f6673",
            "error_bg": "#f2d8d7",
            "error_text": "#7c302c",
            "success_bg": "#d7ebdc",
            "success_text": "#245039",
            "file_bg": "#e4e8ef",
            "file_text": "#1d1d1f",
            "fallback_bg": "#e2e6ee",
            "fallback_text": "#1d1d1f",
            "transfer_send_bg": "#e5f0ff",
            "transfer_recv_bg": "#f1ecff",
            "transfer_send_grad_0": "#0a84ff",
            "transfer_send_grad_1": "#6dbdff",
            "transfer_recv_grad_0": "#7c3aed",
            "transfer_recv_grad_1": "#b49bff",
            "transfer_bar_bg": "#d6d7dd",
            "transfer_label": "#1d1d1f",
            "transfer_meta": "#5f6470",
            "cancel_text": "#0a84ff",
            "image_placeholder_bg": "#dadce4",
            "image_placeholder_text": "#3a3a40",
            "image_me_bg": "#2f92f0",
            "image_peer_bg": "#e9e9ee",
            "tick_success": "#124529",
            "tick_image": "#ffffff",
        },
        "hint_secondary": "#626875",
        "hint_muted": "#767d8b",
        "label_primary": "#444b58",
        "combo_arrow": "#8c8d94",
    },
    "night": {
        "label": "night",
        "dialog_stylesheet": """
            QDialog { background-color: #141417; }
            QLabel { color: #f5f5f7; }
            QComboBox {
                background: #1f1f23;
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                color: #f5f5f7;
                min-height: 20px;
            }
            QComboBox:hover { background: #242831; }
            QComboBox:focus { background: #1f1f23; border: 1px solid #0a84ff; }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 28px;
                background: transparent;
                border-left: none;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox QAbstractItemView {
                background: #1a1d23;
                color: #f5f5f7;
                border: 1px solid #343a46;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
                outline: none;
            }
            QPushButton {
                background-color: #2b2b30;
                border-radius: 8px;
                padding: 10px 24px;
                color: #f5f5f7;
                min-width: 115px;
            }
            QPushButton:hover { background-color: #3a3a40; }
            QPushButton:pressed { background-color: #0a84ff; }
            QPushButton#SecondaryButton {
                background-color: rgba(255, 255, 255, 0.12);
                color: #e4e9f2;
            }
            QPushButton#SecondaryButton:hover { background-color: rgba(255, 255, 255, 0.18); }
            QPushButton#SecondaryButton:pressed { background-color: rgba(255, 255, 255, 0.24); }
            QPushButton#PrimaryButton { background-color: #0a84ff; }
            QPushButton#PrimaryButton:hover { background-color: #409cff; }
        """,
        "window_stylesheet": """
            QMainWindow { background-color: #101114; }
            QWidget#ChatSurface {
                background: rgba(34, 37, 45, 0.68);
                border: 1px solid #2f3541;
                border-radius: 14px;
            }
            QListView {
                background: transparent;
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
            QScrollBar::sub-line:vertical { height: 0px; }
            QLineEdit, QPlainTextEdit {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 8px;
                padding: 8px 10px;
                color: #f5f5f7;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                background: rgba(255, 255, 255, 0.09);
                border: 1px solid rgba(10, 132, 255, 0.85);
            }
            QWidget#ComposeBar, QWidget#ActionToolbar {
                background: rgba(40, 44, 54, 0.62);
                border: 1px solid #383f4c;
                border-radius: 10px;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 9px;
                padding: 8px 14px;
                color: #f5f5f7;
                border: none;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.14); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 0.18); }
            QPushButton#PrimaryActionButton {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QPushButton#PrimaryActionButton:hover { background-color: #3a9eff; }
            QPushButton#PrimaryActionButton:pressed { background-color: #0069d9; }
            QPushButton#SecondaryActionButton {
                background-color: rgba(255, 255, 255, 0.10);
                color: #d4dbe6;
            }
            QPushButton#SecondaryActionButton:hover { background-color: rgba(255, 255, 255, 0.16); }
            QPushButton#DangerActionButton {
                background-color: rgba(255, 255, 255, 0.10);
                color: #ff8f88;
            }
            QPushButton#DangerActionButton:hover { background-color: rgba(255, 255, 255, 0.16); }
            QPushButton#GhostActionButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: #b6becb;
            }
            QPushButton#GhostActionButton:hover { background-color: rgba(255, 255, 255, 0.12); }
            QToolButton#MoreActionsButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 9px;
                color: #c6cfdb;
                padding: 4px 12px;
                font-size: 18px;
                min-width: 32px;
            }
            QToolButton#MoreActionsButton:hover { background-color: rgba(255, 255, 255, 0.14); }
            QToolButton#MoreActionsButton:pressed { background-color: rgba(255, 255, 255, 0.18); }
            QToolButton#ThemeSwitchButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 9px;
                color: #c6cfdb;
                padding: 2px;
                min-width: 30px;
                min-height: 30px;
            }
            QToolButton#ThemeSwitchButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
            QToolButton#ThemeSwitchButton:pressed {
                background-color: rgba(255, 255, 255, 0.18);
            }
            QLabel { color: #f5f5f7; }
            QLabel#StatusLabel {
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 10px;
                padding: 4px 10px;
                color: #9fa1b5;
                font-size: %(status_font_px)spx;
            }
            QMessageBox { background-color: #1f1f23; color: #f5f5f7; }
            QMessageBox QLabel { color: #f5f5f7; }
            QMessageBox QPushButton {
                background-color: #2b2b30;
                border-radius: 6px;
                padding: 6px 16px;
                color: #f5f5f7;
                min-width: 70px;
            }
            QMessageBox QPushButton:hover { background-color: #3a3a40; }
            QMessageBox QPushButton:pressed { background-color: #0a84ff; }
            QMenu {
                background: rgba(34, 37, 45, 0.96);
                border: none;
                border-radius: 14px;
                padding: 8px;
            }
            QMenu::item {
                color: #e3e8f1;
                padding: 9px 14px;
                border-radius: 9px;
                background: transparent;
            }
            QMenu::item:selected {
                background: rgba(255, 255, 255, 0.10);
            }
            QMenu::separator {
                height: 1px;
                margin: 6px 8px;
                background: #343a46;
            }
        """,
        "bubbles": {
            "me_bg": "#2f92f0",
            "me_text": "#ffffff",
            "peer_bg": "#343842",
            "peer_text": "#f2f2f7",
            "system_bg": "#2d333d",
            "system_text": "#a3acbc",
            "error_bg": "#5a3536",
            "error_text": "#ffd9d6",
            "success_bg": "#2f4d3f",
            "success_text": "#d9f2e6",
            "file_bg": "#404654",
            "file_text": "#f2f2f7",
            "fallback_bg": "#3a404c",
            "fallback_text": "#f2f2f7",
            "transfer_send_bg": "#2a415e",
            "transfer_recv_bg": "#353b47",
            "transfer_send_grad_0": "#0a84ff",
            "transfer_send_grad_1": "#4fa9ff",
            "transfer_recv_grad_0": "#636c7e",
            "transfer_recv_grad_1": "#828ba0",
            "transfer_bar_bg": "#252a33",
            "transfer_label": "#f2f2f7",
            "transfer_meta": "#a2aab7",
            "cancel_text": "#7fb9ff",
            "image_placeholder_bg": "#404654",
            "image_placeholder_text": "#f2f2f7",
            "image_me_bg": "#2f92f0",
            "image_peer_bg": "#343842",
            "tick_success": "#d5f4df",
            "tick_image": "#ffffff",
        },
        "hint_secondary": "#8d95a6",
        "hint_muted": "#a9b1c1",
        "label_primary": "#c6cfdf",
        "combo_arrow": "#9fa1b5",
    },
}


def _resolve_theme(theme_id: Optional[str]) -> str:
    raw = str(theme_id or "").strip().lower()
    if raw in {"macos", "light"}:
        raw = "ligth"
    if raw in THEMES:
        return raw
    return THEME_DEFAULT


def _ui_prefs_path() -> str:
    return os.path.join(get_profiles_dir(), "ui_prefs.json")


def _load_ui_prefs() -> dict[str, object]:
    path = _ui_prefs_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def _save_ui_prefs(data: dict[str, object]) -> None:
    path = _ui_prefs_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True, indent=2, sort_keys=True)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def load_saved_theme() -> str:
    data = _load_ui_prefs()
    return _resolve_theme(str(data.get("theme", THEME_DEFAULT)))


def save_theme(theme_id: str) -> None:
    theme_id = _resolve_theme(theme_id)
    data = _load_ui_prefs()
    data["theme"] = theme_id
    _save_ui_prefs(data)


def load_saved_notify_sound() -> Optional[str]:
    data = _load_ui_prefs()
    value = data.get("notify_sound")
    if isinstance(value, str):
        raw = value.strip()
        return raw or None
    return None


def save_notify_sound(sound_path: Optional[str]) -> None:
    data = _load_ui_prefs()
    cleaned = (sound_path or "").strip()
    if cleaned:
        data["notify_sound"] = cleaned
    else:
        data.pop("notify_sound", None)
    _save_ui_prefs(data)


@dataclass
class ChatItem:
    kind: str  # "me", "peer", "system", "error", "success", "transfer", "image_inline", etc.
    timestamp: str
    sender: str
    text: str
    progress: float = 0.0
    file_size: int = 0
    is_sending: bool = False
    image_path: Optional[str] = None  # путь к inline-изображению
    open_folder_path: Optional[str] = None  # для "File received" — открыть папку по клику
    file_name: Optional[str] = None  # имя отправленного файла/картинки для ACK
    delivered: bool = False  # галочка доставки для отправленных картинок


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
    - открывает изображения по двойному клику
    """
    cancelTransferRequested = QtCore.pyqtSignal()
    imageOpenRequested = QtCore.pyqtSignal(str)  # path to image

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

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        index = self.indexAt(event.pos())
        if index.isValid():
            item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if isinstance(item, ChatItem):
                if item.kind == "transfer":
                    delegate = self.itemDelegate()
                    if isinstance(delegate, ChatItemDelegate):
                        rect = self.visualRect(index)
                        if delegate.is_cancel_button_hit(rect, event.pos(), item):
                            self.cancelTransferRequested.emit()
                            return
                elif item.kind == "success" and item.open_folder_path and os.path.isdir(item.open_folder_path):
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(item.open_folder_path))
                    return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        index = self.indexAt(event.pos())
        if index.isValid():
            item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if isinstance(item, ChatItem) and item.kind == "image_inline" and item.image_path:
                self.imageOpenRequested.emit(item.image_path)
                return
        super().mouseDoubleClickEvent(event)


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
    
    # Настройки для inline-изображений
    IMAGE_MAX_WIDTH = 300
    IMAGE_MAX_HEIGHT = 200
    
    # Кэш для QPixmap (путь -> pixmap)
    _pixmap_cache: dict = {}

    def __init__(
        self,
        parent: Optional[QtCore.QObject] = None,
        bubble_palette: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(parent)
        palette = THEMES[THEME_DEFAULT]["bubbles"]
        self._bubble_palette = dict(palette) if isinstance(palette, dict) else {}
        if bubble_palette:
            self._bubble_palette.update(bubble_palette)

    def set_bubble_palette(self, bubble_palette: Optional[dict[str, str]]) -> None:
        palette = THEMES[THEME_DEFAULT]["bubbles"]
        self._bubble_palette = dict(palette) if isinstance(palette, dict) else {}
        if bubble_palette:
            self._bubble_palette.update(bubble_palette)

    def _c(self, name: str, fallback: str) -> QtGui.QColor:
        return QtGui.QColor(str(self._bubble_palette.get(name, fallback)))
    
    def _load_pixmap(self, path: str) -> Optional[QtGui.QPixmap]:
        """Загрузить изображение с кэшированием."""
        if path in self._pixmap_cache:
            return self._pixmap_cache[path]
        
        if not os.path.exists(path):
            return None
        
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            return None
        
        # Масштабируем если нужно
        if pixmap.width() > self.IMAGE_MAX_WIDTH or pixmap.height() > self.IMAGE_MAX_HEIGHT:
            pixmap = pixmap.scaled(
                self.IMAGE_MAX_WIDTH,
                self.IMAGE_MAX_HEIGHT,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        
        self._pixmap_cache[path] = pixmap
        return pixmap

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

        if item.kind == "image_inline" and item.image_path:
            self._paint_image(painter, option, item)
            painter.restore()
            return

        is_me = item.kind in {"me", "image_braille", "image_bw"} or (
            item.kind == "success" and getattr(item, "is_sending", False)
        )
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
            bg_color = self._c("me_bg", "#3a7afe")
            text_color = self._c("me_text", "#ffffff")
        elif item.kind == "peer":
            bg_color = self._c("peer_bg", "#7c3aed")
            text_color = self._c("peer_text", "#f8f8f2")
        elif item.kind in {"system", "info"}:
            bg_color = self._c("system_bg", "#282a36")
            text_color = self._c("system_text", "#8be9fd")
        elif item.kind == "error" or item.kind == "disconnect":
            bg_color = self._c("error_bg", "#ff5555")
            text_color = self._c("error_text", "#f8f8f2")
        elif item.kind == "success":
            bg_color = self._c("success_bg", "#50fa7b")
            text_color = self._c("success_text", "#282a36")
        elif item.kind == "file":
            bg_color = self._c("file_bg", "#6272a4")
            text_color = self._c("file_text", "#f8f8f2")
        else:
            bg_color = self._c("fallback_bg", "#44475a")
            text_color = self._c("fallback_text", "#f8f8f2")

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
                ts_color = self._c("tick_success", "#15542d")
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

        # Галочки доставки для «File sent» — тёмный цвет как у текста, хорошо читается на зелёном
        if (
            item.kind == "success"
            and getattr(item, "file_name", None)
            and getattr(item, "is_sending", False)
        ):
            tick_font = QtGui.QFont(base_font)
            tick_font.setPointSize(max(base_font.pointSize() - 2, 9))
            painter.setFont(tick_font)
            tick_rect = QtCore.QRectF(
                bubble_rect.right() - 28,
                bubble_rect.bottom() - 22,
                24,
                18,
            )
            ticks = "✓✓" if getattr(item, "delivered", False) else "✓"
            painter.setPen(self._c("tick_success", "#282a36"))
            painter.drawText(
                tick_rect,
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom),
                ticks,
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
        
        bg_color = self._c("transfer_send_bg", "#1e3a5f") if is_sending else self._c("transfer_recv_bg", "#2d1b4e")
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
        
        if item.sender == "IMAGE":
            action = "↑ Uploading image" if is_sending else "↓ Receiving image"
        else:
            action = "↑ Sending" if is_sending else "↓ Receiving"
        header_text = f"{action}: {item.text}"
        painter.setPen(self._c("transfer_label", "#ffffff"))
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
        
        painter.setBrush(self._c("transfer_bar_bg", "#0d1b2a"))
        painter.drawRoundedRect(bar_rect, bar_height / 2, bar_height / 2)
        
        progress = max(0.0, min(1.0, item.progress))
        if progress > 0:
            fill_width = max(bar_height, bar_rect.width() * progress)
            fill_rect = QtCore.QRectF(bar_rect.left(), bar_rect.top(), fill_width, bar_height)
            
            gradient = QtGui.QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            if is_sending:
                gradient.setColorAt(0.0, self._c("transfer_send_grad_0", "#0066cc"))
                gradient.setColorAt(0.5, self._c("transfer_send_grad_1", "#3399ff"))
                gradient.setColorAt(1.0, self._c("transfer_send_grad_0", "#0066cc"))
            else:
                gradient.setColorAt(0.0, self._c("transfer_recv_grad_0", "#7c3aed"))
                gradient.setColorAt(0.5, self._c("transfer_recv_grad_1", "#a78bfa"))
                gradient.setColorAt(1.0, self._c("transfer_recv_grad_0", "#7c3aed"))
            
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill_rect, bar_height / 2, bar_height / 2)
            
            pulse = (time.time() % 1.0)
            glow_alpha = int(40 + 30 * math.sin(pulse * math.pi * 2))
            glow_color = QtGui.QColor(255, 255, 255, glow_alpha)
            painter.setBrush(glow_color)
            painter.drawRoundedRect(fill_rect, bar_height / 2, bar_height / 2)
        
        pct = int(progress * 100)
        pct_text = f"{pct}%"
        painter.setPen(self._c("transfer_label", "#ffffff"))
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
            painter.setPen(self._c("transfer_meta", "#a0a0a0"))
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
        
        # Надпись Cancel снизу справа (не перекрывает имя файла)
        small_font = QtGui.QFont(base_font)
        small_font.setPointSize(max(base_font.pointSize() - 2, 8))
        painter.setFont(small_font)
        cancel_label = "Cancel"
        cancel_rect = QtCore.QRectF(
            inner_rect.left(),
            size_rect.bottom() + 2,
            inner_rect.width(),
            metrics.height(),
        )
        painter.setPen(self._c("cancel_text", "#a78bfa"))
        painter.drawText(
            cancel_rect,
            int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
            cancel_label,
        )

    def _paint_image(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        item: ChatItem,
    ) -> None:
        """Рисует бабл с inline-изображением."""
        rect = option.rect.adjusted(0, self.BUBBLE_SPACING_Y, 0, -self.BUBBLE_SPACING_Y)
        cell_width = rect.width()
        
        # Загружаем изображение
        pixmap = self._load_pixmap(item.image_path) if item.image_path else None
        
        if pixmap is None:
            # Показываем placeholder если изображение не загружено
            bubble_width = int(cell_width * 0.4)
            is_me = item.is_sending
            if is_me:
                bubble_rect = QtCore.QRectF(
                    rect.right() - bubble_width - self.PADDING_X,
                    rect.top(),
                    bubble_width,
                    60,
                )
            else:
                bubble_rect = QtCore.QRectF(
                    rect.left() + self.PADDING_X,
                    rect.top(),
                    bubble_width,
                    60,
                )
            
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(self._c("image_placeholder_bg", "#44475a"))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bubble_rect, self.BUBBLE_RADIUS, self.BUBBLE_RADIUS)
            
            painter.setPen(self._c("image_placeholder_text", "#f8f8f2"))
            painter.drawText(
                bubble_rect,
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                "[Image not found]",
            )
            return
        
        # Размеры бабла на основе размера изображения
        img_width = pixmap.width()
        img_height = pixmap.height()
        bubble_width = img_width + self.PADDING_X * 2
        bubble_height = img_height + self.PADDING_Y * 2
        
        is_me = item.is_sending
        if is_me:
            bubble_rect = QtCore.QRectF(
                rect.right() - bubble_width - self.PADDING_X,
                rect.top(),
                bubble_width,
                bubble_height,
            )
            bg_color = self._c("image_me_bg", "#3a7afe")
        else:
            bubble_rect = QtCore.QRectF(
                rect.left() + self.PADDING_X,
                rect.top(),
                bubble_width,
                bubble_height,
            )
            bg_color = self._c("image_peer_bg", "#7c3aed")
        
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(bg_color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bubble_rect, self.BUBBLE_RADIUS, self.BUBBLE_RADIUS)
        
        # Рисуем изображение внутри бабла
        img_rect = QtCore.QRectF(
            bubble_rect.left() + self.PADDING_X,
            bubble_rect.top() + self.PADDING_Y,
            img_width,
            img_height,
        )
        
        # Скругляем углы изображения
        path = QtGui.QPainterPath()
        path.addRoundedRect(img_rect, self.BUBBLE_RADIUS - 4, self.BUBBLE_RADIUS - 4)
        painter.setClipPath(path)
        painter.drawPixmap(img_rect.toRect(), pixmap)
        painter.setClipping(False)
        
        # Добавляем тонкую рамку вокруг изображения
        border_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 40))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(img_rect, self.BUBBLE_RADIUS - 4, self.BUBBLE_RADIUS - 4)

        # Галочки для отправленных картинок: одна — отправлено, две — доставлено
        if is_me:
            base_font = painter.font()
            tick_font = QtGui.QFont(base_font)
            tick_font.setPointSize(max(base_font.pointSize() - 2, 9))
            painter.setFont(tick_font)
            tick_rect = QtCore.QRectF(
                bubble_rect.right() - 28,
                bubble_rect.bottom() - 22,
                24,
                18,
            )
            ticks = "✓✓" if item.delivered else "✓"
            # Тень, чтобы галочки были видны на белой картинке
            painter.setPen(QtGui.QColor(0, 0, 0, 160))
            painter.drawText(
                tick_rect.translated(1, 1),
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom),
                ticks,
            )
            painter.setPen(self._c("tick_image", "#ffffff"))
            painter.drawText(
                tick_rect,
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom),
                ticks,
            )

    def _get_cancel_button_rect(
        self, cell_rect: QtCore.QRect, item: ChatItem
    ) -> QtCore.QRectF:
        rect = cell_rect.adjusted(0, self.BUBBLE_SPACING_Y, 0, -self.BUBBLE_SPACING_Y)
        cell_width = rect.width()
        bubble_width = int(cell_width * 0.6)
        
        if item.is_sending:
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
        
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            self.PADDING_Y / 2,
            -self.PADDING_X / 2,
            -self.PADDING_Y / 2,
        )
        inner_rect = bubble_rect.adjusted(
            self.PADDING_X, self.PADDING_Y, -self.PADDING_X, -self.PADDING_Y
        )
        
        # Прямоугольник надписи "Cancel" снизу справа (как в _paint_transfer)
        view = self.parent()
        font = view.font() if isinstance(view, QtWidgets.QWidget) else QtGui.QFont()
        small_font = QtGui.QFont(font)
        small_font.setPointSize(max(font.pointSize() - 2, 8))
        metrics = QtGui.QFontMetrics(small_font)
        bar_height = 18
        header_h = metrics.height()
        size_rect_top = inner_rect.top() + header_h + 8 + bar_height + 4
        cancel_top = size_rect_top + metrics.height() + 2
        cancel_w = metrics.horizontalAdvance("Cancel")
        cancel_h = metrics.height()
        return QtCore.QRectF(
            inner_rect.right() - cancel_w,
            cancel_top,
            cancel_w,
            cancel_h,
        )

    def is_cancel_button_hit(
        self, cell_rect: QtCore.QRect, pos: QtCore.QPoint, item: ChatItem
    ) -> bool:
        cancel_rect = self._get_cancel_button_rect(cell_rect, item)
        return cancel_rect.contains(QtCore.QPointF(pos))

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
            # заголовок + прогресс-бар + размер + надпись Cancel
            height = self.PADDING_Y * 4 + 18 + 18 + 20 + 20 + self.BUBBLE_SPACING_Y * 2
            return QtCore.QSize(int(cell_width), int(height))

        if item.kind == "image_inline" and item.image_path:
            cell_width = option.rect.width() if option.rect.width() > 0 else 600
            pixmap = self._load_pixmap(item.image_path)
            if pixmap:
                img_height = pixmap.height()
                height = img_height + self.PADDING_Y * 2 + self.BUBBLE_SPACING_Y * 2
            else:
                height = 60 + self.BUBBLE_SPACING_Y * 2
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


class ProfileComboWithArrow(QtWidgets.QWidget):
    """QComboBox с видимой стрелкой ▼ поверх области выпадающего списка."""
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QtWidgets.QComboBox(self)
        layout.addWidget(self.combo)
        self._arrow = QtWidgets.QLabel("∨", self)
        self.set_arrow_color("#9fa1b5")
        self._arrow.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._arrow.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_arrow_color(self, color: str) -> None:
        self._arrow.setStyleSheet(
            f"color: {color}; font-size: 10px; background: transparent;"
        )
    
    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        drop_width = 28
        self._arrow.setGeometry(
            self.width() - drop_width, 0,
            drop_width, self.height(),
        )


class _ClickableFolderLabel(QtWidgets.QLabel):
    """QLabel, по клику открывающий папку (как «Open downloads folder» в чате)."""

    def __init__(self, text: str, folder_path: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(text, parent)
        self._folder_path = folder_path
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and os.path.isdir(self._folder_path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self._folder_path))
            return
        super().mousePressEvent(event)


class RoundedMenu(QtWidgets.QMenu):
    """QMenu с реальной rounded-маской окна для платформенных WM."""

    def __init__(self, radius: int = 14, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._radius = max(0, int(radius))
        if sys.platform.startswith("linux"):
            # Агрессивный режим без декораций WM: иначе некоторые композиторы
            # рисуют прямоугольную рамку поверх rounded popup.
            self.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            self.setWindowFlag(QtCore.Qt.WindowType.Popup, True)
            self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
            self.setWindowFlag(QtCore.Qt.WindowType.NoDropShadowWindowHint, True)
            self.setWindowFlag(QtCore.Qt.WindowType.BypassWindowManagerHint, True)
        elif sys.platform in {"darwin", "win32"}:
            # На macOS/Windows тоже включаем прозрачный фон popup, иначе контур
            # может оставаться прямоугольным поверх QSS-скругления.
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)

    def _apply_round_mask(self) -> None:
        rect = QtCore.QRectF(self.rect())
        if rect.isEmpty():
            return
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, float(self._radius), float(self._radius))
        poly = path.toFillPolygon().toPolygon()
        self.setMask(QtGui.QRegion(poly))

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if sys.platform == "darwin":
            # На macOS финальный размер popup иногда стабилизируется после showEvent.
            # Применяем маску отложенно один раз, чтобы убрать визуальное "дёргание".
            QtCore.QTimer.singleShot(0, self._apply_round_mask)
        else:
            self._apply_round_mask()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if sys.platform != "darwin":
            self._apply_round_mask()


class ProfileSelectDialog(QtWidgets.QDialog):
    """Начальное окно выбора профиля в стиле приложения."""
    
    def __init__(
        self,
        profiles: List[str],
        theme_id: str,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme_id = _resolve_theme(theme_id)
        self.setWindowTitle("I2PChat")
        self.setMinimumSize(420, 300)
        self.setMaximumWidth(480)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(28, 28, 28, 28)
        
        title = QtWidgets.QLabel("I2PChat")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setWeight(QtGui.QFont.Weight.DemiBold)
        title.setFont(title_font)
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        subtitle = QtWidgets.QLabel("Choose profile")
        self.subtitle = subtitle
        subtitle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        
        layout.addSpacing(8)
        
        hint = QtWidgets.QLabel(
            "Use <b>default</b> for a one-time session, or enter a name to save your identity."
        )
        self.hint = hint
        hint.setWordWrap(True)
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        
        profile_label = QtWidgets.QLabel("Profile:")
        self.profile_label = profile_label
        layout.addWidget(profile_label)
        
        combo_widget = ProfileComboWithArrow(self)
        self.profile_combo_widget = combo_widget
        self.combo = combo_widget.combo
        self.combo.setEditable(True)
        self.combo.addItems(profiles)
        self.combo.setCurrentIndex(0)
        self.combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        layout.addWidget(combo_widget)
        
        combo_hint = QtWidgets.QLabel("Click the list on the right to pick an existing profile, or type a new name above.")
        self.combo_hint = combo_hint
        combo_hint.setWordWrap(True)
        layout.addWidget(combo_hint)
        
        profiles_path = get_profiles_dir()
        path_hint = _ClickableFolderLabel(f"Profiles folder: {profiles_path}", profiles_path)
        self.path_hint = path_hint
        path_hint.setWordWrap(True)
        path_hint.setToolTip("Click to open folder")
        layout.addWidget(path_hint)

        layout.addSpacing(12)
        
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setMinimumWidth(120)
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        continue_btn = QtWidgets.QPushButton("Continue")
        continue_btn.setMinimumWidth(120)
        continue_btn.setObjectName("PrimaryButton")
        continue_btn.setDefault(True)
        continue_btn.setAutoDefault(True)
        continue_btn.clicked.connect(self.accept)
        btn_layout.addWidget(continue_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        self._apply_theme_style(self._theme_id)
        self.combo.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _apply_theme_style(self, theme_id: str) -> None:
        theme = THEMES[_resolve_theme(theme_id)]
        self.setStyleSheet(str(theme["dialog_stylesheet"]))
        muted = str(theme.get("hint_muted", "#9fa1b5"))
        secondary = str(theme.get("hint_secondary", "#6c6e7e"))
        label_primary = str(theme.get("label_primary", "#e0e0e0"))
        arrow = str(theme.get("combo_arrow", "#9fa1b5"))
        self.subtitle.setStyleSheet(f"color: {muted};")
        self.hint.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self.profile_label.setStyleSheet(f"color: {label_primary}; font-size: 13px;")
        self.combo_hint.setStyleSheet(f"color: {secondary}; font-size: 11px;")
        self.path_hint.setStyleSheet(f"color: {secondary}; font-size: 11px;")
        self.profile_combo_widget.set_arrow_color(arrow)
    
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
            # На macOS Command = MetaModifier; стандартный Quit тоже проверяем
            if event.matches(QtGui.QKeySequence.StandardKey.Quit):
                QtWidgets.QApplication.quit()
                return True
            if sys.platform == "darwin" and event.key() == QtCore.Qt.Key.Key_Q and (
                event.modifiers() == QtCore.Qt.KeyboardModifier.MetaModifier
            ):
                QtWidgets.QApplication.quit()
                return True
        return super().eventFilter(obj, event)
    
    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)
    
    def selected_profile(self) -> Optional[str]:
        text = self.combo.currentText().strip() if self.combo.currentText() else ""
        return text or None

class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: Optional[str] = None, theme_id: str = THEME_DEFAULT) -> None:
        super().__init__()
        self.profile = profile or "default"
        self.theme_id = _resolve_theme(theme_id)
        self.theme = THEMES[self.theme_id]
        # Показываем профиль через разделитель-точку;
        # если вдруг имя профиля уже содержит служебный маркер в конце (" •"),
        # аккуратно убираем его, чтобы заголовок не заканчивался кружком.
        clean_profile = self.profile.rstrip(" •")
        self.setWindowTitle(f"I2PChat @ {clean_profile}")
        self.resize(900, 600)

        self._status_font_px = 9 if sys.platform == "win32" else 11

        # UI
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(10)

        self.more_actions_menu = RoundedMenu(radius=14, parent=self)

        # диагностическая строка статуса
        self.status_label = QtWidgets.QLabel("Status: initializing", self)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self.theme_switch_button = QtWidgets.QToolButton(self)
        self.theme_switch_button.setObjectName("ThemeSwitchButton")
        self.theme_switch_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.theme_switch_button.setAutoRaise(False)
        self.theme_switch_button.clicked.connect(self.on_theme_switch_clicked)
        self.status_row = QtWidgets.QWidget(self)
        status_row_layout = QtWidgets.QHBoxLayout(self.status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(6)
        status_row_layout.addWidget(self.status_label, 1)
        status_row_layout.addWidget(self.theme_switch_button)
        self._last_status: str = "initializing"
        self._transfer_row: Optional[int] = None
        self._transfer_is_image: bool = False

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
        bubble_palette = self.theme.get("bubbles", {})
        delegate_palette = (
            dict(bubble_palette) if isinstance(bubble_palette, dict) else None
        )
        self.chat_view.setItemDelegate(
            ChatItemDelegate(self.chat_view, bubble_palette=delegate_palette)
        )
        chat_surface = QtWidgets.QWidget(self)
        chat_surface.setObjectName("ChatSurface")
        chat_surface_layout = QtWidgets.QVBoxLayout(chat_surface)
        chat_surface_layout.setContentsMargins(6, 6, 6, 6)
        chat_surface_layout.setSpacing(0)
        chat_surface_layout.addWidget(self.chat_view)

        # панель ввода
        input_container = QtWidgets.QWidget(self)
        input_container.setObjectName("ComposeBar")
        input_layout = QtWidgets.QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(6)
        self.input_edit = MessageInputEdit(self)
        self.input_edit.setPlaceholderText("Type message. Enter to send, Shift+Enter for new line.")
        self.input_edit.setMinimumHeight(52)
        font = self.input_edit.font()
        font.setPointSize(font.pointSize() + 1)
        self.input_edit.setFont(font)

        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setObjectName("PrimaryActionButton")
        self.send_button.setMinimumHeight(52)

        fixed_height = 52 if sys.platform == "darwin" else 56
        self.input_edit.setFixedHeight(fixed_height)
        self.send_button.setFixedHeight(fixed_height)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_button)

        # панель действий: сегментированные группы кнопок в стиле macOS toolbar
        actions_container = QtWidgets.QWidget(self)
        actions_container.setObjectName("ActionToolbar")
        actions_layout = QtWidgets.QHBoxLayout(actions_container)
        actions_layout.setContentsMargins(8, 8, 8, 8)
        actions_layout.setSpacing(6)

        self.addr_edit = QtWidgets.QLineEdit(self)
        self.addr_edit.setPlaceholderText("Peer .b32.i2p address")
        # Адрес — главный элемент панели действий
        self.addr_edit.setMinimumWidth(220)
        self.addr_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        self.connect_button.setObjectName("PrimaryActionButton")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect", self)
        self.disconnect_button.setObjectName("DangerActionButton")

        self.more_toolbar_button = QtWidgets.QToolButton(self)
        self.more_toolbar_button.setObjectName("MoreActionsButton")
        self.more_toolbar_button.setText("⋯")
        self.more_toolbar_button.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.more_toolbar_button.setMenu(self.more_actions_menu)

        # Все элементы панели действий делаем одной высоты, чтобы ряд смотрелся ровно
        actions_fixed_height = 34 if sys.platform == "darwin" else 36
        self.addr_edit.setFixedHeight(actions_fixed_height)
        for btn in [
            self.connect_button,
            self.disconnect_button,
        ]:
            btn.setFixedHeight(actions_fixed_height)
        self.more_toolbar_button.setFixedHeight(actions_fixed_height)

        actions_layout.addWidget(self.addr_edit, 1)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.disconnect_button)
        actions_layout.addWidget(self.more_toolbar_button)

        main_layout.addWidget(self.status_row)
        main_layout.addWidget(chat_surface, 1)
        main_layout.addWidget(input_container)
        main_layout.addWidget(actions_container)

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

        # Звук уведомления: env (I2PCHAT_NOTIFY_SOUND) имеет приоритет над prefs.
        # Если путь не задан или недоступен, используем fallback на QApplication.beep().
        self.notify_sound_path: Optional[str] = None
        self.notify_sound: Optional["QSoundEffect"] = None
        env_sound_path = os.environ.get("I2PCHAT_NOTIFY_SOUND", "").strip()
        saved_sound_path = load_saved_notify_sound() or ""
        bundled_sound_path = _default_notify_sound_path() or ""
        startup_sound_path = env_sound_path or saved_sound_path or bundled_sound_path
        self._reload_notify_sound(startup_sound_path if startup_sound_path else None)

        # сигналы
        self.send_button.clicked.connect(self.on_send_clicked)
        self.input_edit.sendRequested.connect(self.on_send_clicked)
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.action_load_profile = self.more_actions_menu.addAction("Load profile (.dat)")
        self.action_send_pic = self.more_actions_menu.addAction("Send picture")
        self.action_send_file = self.more_actions_menu.addAction("Send file")
        self.more_actions_menu.addSeparator()
        self.action_lock_peer = self.more_actions_menu.addAction("Lock to peer")
        self.action_copy_addr = self.more_actions_menu.addAction("Copy my address")
        self.action_load_profile.triggered.connect(self.on_load_profile_clicked)
        self.action_send_pic.triggered.connect(self.on_send_pic_clicked)
        self.action_send_file.triggered.connect(self.on_send_file_clicked)
        self.action_lock_peer.triggered.connect(self.on_lock_peer_clicked)
        self.action_copy_addr.triggered.connect(self.on_copy_my_addr_clicked)
        self.chat_view.cancelTransferRequested.connect(self.on_cancel_transfer)
        self.chat_view.imageOpenRequested.connect(self.on_image_open_requested)

        # ядро
        self.core = self._create_core(self.profile)
        self._apply_theme(self.theme_id, persist=False)
        self.refresh_status_label()

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

        Используем для:
        - входящих peer‑сообщений
        - входящего подключения (kind="connect")
        """
        if not isinstance(msg, ChatMessage):
            return

        if msg.kind == "peer":
            preview = msg.text.replace("\n", " ")
            title = "New message"
            if self.core.current_peer_addr:
                clean_peer = self.core.current_peer_addr.replace(".b32.i2p", "")
                if len(clean_peer) > 12:
                    clean_peer = f"{clean_peer[:6]}..{clean_peer[-6:]}"
                title = f"New message from {clean_peer}"
        elif msg.kind == "connect":
            peer = (msg.text or "").strip()
            clean_peer = peer.replace(".b32.i2p", "") if peer else "peer"
            if len(clean_peer) > 12:
                clean_peer = f"{clean_peer[:6]}..{clean_peer[-6:]}"
            title = "Incoming connection"
            preview = f"{clean_peer}.b32.i2p connected" if peer else "Peer connected"
        else:
            return

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

            self._play_notification_sound()

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
            # Только Send Pic (G) помечен как inline image; Send File (F/D) — обычный файл
            is_image = getattr(info, "is_inline_image", False)
            # Для входящих файлов (не картинок) спрашиваем подтверждение
            if not info.is_sending and not is_image:
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
                        # Уведомить отправителя, что файл отклонён
                        asyncio.create_task(self.core.reject_incoming_file(info.filename))
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

            # Создаём сообщение прогресса в чате (для картинок — "Uploading/Receiving image")
            self._transfer_is_image = is_image
            self._append_item(
                ChatItem(
                    kind="transfer",
                    timestamp="",
                    sender="IMAGE" if is_image else "FILE",
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
                    sender="IMAGE" if self._transfer_is_image else "FILE",
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
                if getattr(info, "rejected_by_peer", False):
                    err_text = f"Receiver rejected the file: {info.filename}"
                else:
                    err_text = f"Transfer failed: {info.filename}"
                self.chat_model.update_item(
                    self._transfer_row,
                    ChatItem(
                        kind="error",
                        timestamp="",
                        sender="IMAGE" if self._transfer_is_image else "FILE",
                        text=err_text,
                    ),
                )
                self._transfer_row = None
            self._transfer_is_image = False
            return

        # Завершение передачи
        if info.received >= info.size:
            self._transfer_timer.stop()
            if self._transfer_row is not None:
                if self._transfer_is_image:
                    # Сначала показываем 100%, потом заменим на превью в handle_inline_image_received
                    self.chat_model.update_item(
                        self._transfer_row,
                        ChatItem(
                            kind="transfer",
                            timestamp="",
                            sender="IMAGE" if self._transfer_is_image else "FILE",
                            text=info.filename,
                            progress=1.0,
                            file_size=info.size,
                            is_sending=info.is_sending,
                        ),
                    )
                else:
                    # Сначала 100%, затем сообщение об успехе — чтобы не зависало на 99%
                    self.chat_model.update_item(
                        self._transfer_row,
                        ChatItem(
                            kind="transfer",
                            timestamp="",
                            sender="FILE",
                            text=info.filename,
                            progress=1.0,
                            file_size=info.size,
                            is_sending=info.is_sending,
                        ),
                    )
                    done_action = "sent" if info.is_sending else "received"
                    if done_action == "received":
                        downloads_dir = get_downloads_dir()
                        self.chat_model.update_item(
                            self._transfer_row,
                            ChatItem(
                                kind="success",
                                timestamp="",
                                sender="FILE",
                                text=f"✔ File received: {info.filename} ({info.size:,} bytes). Open downloads folder",
                                open_folder_path=downloads_dir,
                            ),
                        )
                    else:
                        self.chat_model.update_item(
                            self._transfer_row,
                            ChatItem(
                                kind="success",
                                timestamp="",
                                sender="FILE",
                                text=f"File sent: {info.filename} ({info.size:,} bytes)",
                                file_name=info.filename,
                                is_sending=True,
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

    @QtCore.pyqtSlot(str, bool)
    def handle_inline_image_received(self, path: str, is_from_me: bool, sent_filename: Optional[str] = None) -> None:
        """Обработчик для inline-изображений (PNG/JPEG/WebP). sent_filename — для галочки доставки."""
        ts = ""
        if is_from_me:
            sender = "Me"
        else:
            sender = "Peer"
        item_kw = dict(
            kind="image_inline",
            timestamp=ts,
            sender=sender,
            text="",
            is_sending=is_from_me,
            image_path=path,
            file_name=sent_filename if is_from_me else None,
        )
        if self._transfer_row is not None and self._transfer_is_image:
            self.chat_model.update_item(
                self._transfer_row,
                ChatItem(**item_kw),
            )
            self._transfer_row = None
            self._transfer_is_image = False
        else:
            self._append_item(ChatItem(**item_kw))

    @QtCore.pyqtSlot(str)
    def handle_image_delivered(self, filename: str) -> None:
        """Галочка доставки: адресат получил картинку с этим именем."""
        for row in range(self.chat_model.rowCount()):
            idx = self.chat_model.index(row, 0)
            item = idx.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if isinstance(item, ChatItem) and item.kind == "image_inline" and item.is_sending and item.file_name == filename:
                self.chat_model.update_item(row, replace(item, delivered=True))
                return

    @QtCore.pyqtSlot(str)
    def handle_file_delivered(self, filename: str) -> None:
        """Галочка доставки: адресат получил файл с этим именем."""
        name = os.path.basename(filename) if filename else ""
        for row in range(self.chat_model.rowCount()):
            idx = self.chat_model.index(row, 0)
            item = idx.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if not isinstance(item, ChatItem) or item.kind != "success" or not item.is_sending:
                continue
            item_name = (item.file_name or "").strip()
            if not item_name:
                continue
            if item_name == name or os.path.basename(item_name) == name:
                self.chat_model.update_item(row, replace(item, delivered=True))
                return

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
            on_inline_image_received=self.handle_inline_image_received,
            on_image_delivered=self.handle_image_delivered,
            on_file_delivered=self.handle_file_delivered,
            on_trust_decision=self.handle_trust_decision,
            legacy_compat=os.environ.get("I2PCHAT_LEGACY_COMPAT", "").strip().lower() in {"1", "true", "yes", "on"},
        )
        # динамически навешиваем колбэк уведомлений,
        # чтобы не менять публичную сигнатуру конструктора ядра
        setattr(core, "on_notify", self.handle_notify)
        return core

    def _reload_notify_sound(self, sound_path: Optional[str]) -> None:
        """Перезагрузить кастомный звук уведомлений из файла."""
        self.notify_sound = None
        cleaned = (sound_path or "").strip()
        self.notify_sound_path = cleaned or None
        if QSoundEffect is None or not self.notify_sound_path:
            return
        if not os.path.isfile(self.notify_sound_path):
            self.notify_sound_path = None
            return
        try:
            effect = QSoundEffect(self)
            effect.setSource(QtCore.QUrl.fromLocalFile(self.notify_sound_path))
            effect.setVolume(0.7)
            self.notify_sound = effect
        except Exception:
            self.notify_sound = None
            self.notify_sound_path = None

    def _play_notification_sound(self) -> None:
        """
        Воспроизвести звук уведомления с Linux-fallback.

        Порядок:
        1) QSoundEffect (если доступен),
        2) Linux system players (canberra-gtk-play / paplay / aplay),
        3) QApplication.beep().
        """
        played = False

        if self.notify_sound is not None:
            try:
                self.notify_sound.stop()
                self.notify_sound.play()
                played = True
            except Exception:
                played = False

        if not played and sys.platform.startswith("linux"):
            linux_cmds: list[list[str]] = []
            if shutil.which("canberra-gtk-play"):
                linux_cmds.append(["canberra-gtk-play", "-i", "message-new-instant"])
            if self.notify_sound_path and os.path.isfile(self.notify_sound_path):
                if shutil.which("paplay"):
                    linux_cmds.append(["paplay", self.notify_sound_path])
                if shutil.which("aplay"):
                    linux_cmds.append(["aplay", self.notify_sound_path])
            for cmd in linux_cmds:
                try:
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    played = True
                    break
                except Exception:
                    continue

        if not played:
            QtWidgets.QApplication.beep()

    def _update_theme_switch_label(self) -> None:
        next_theme = "night" if self.theme_id == "ligth" else "ligth"
        # Показываем иконку текущей темы: ligth -> sun, night -> moon.
        icon_name = "sun.max.png" if self.theme_id == "ligth" else "moon.png"
        icon_path = _resolve_local_asset(icon_name)
        tint = QtGui.QColor("#1f232b") if self.theme_id == "ligth" else QtGui.QColor("#f2f2f7")
        if icon_path:
            source = QtGui.QPixmap(icon_path)
            if not source.isNull():
                icon_px = 16 if icon_name == "moon.png" else 18
                dpr = max(1.0, self.devicePixelRatioF())
                target_w = max(1, int(icon_px * dpr))
                target_h = max(1, int(icon_px * dpr))
                source = source.scaled(
                    target_w,
                    target_h,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
                tinted = QtGui.QPixmap(source.size())
                tinted.fill(QtCore.Qt.GlobalColor.transparent)
                painter = QtGui.QPainter(tinted)
                painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(0, 0, source)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), tint)
                painter.end()
                tinted.setDevicePixelRatio(dpr)
                self.theme_switch_button.setIcon(QtGui.QIcon(tinted))
                self.theme_switch_button.setIconSize(QtCore.QSize(icon_px, icon_px))
                self.theme_switch_button.setText("")
            else:
                self.theme_switch_button.setIcon(QtGui.QIcon())
                self.theme_switch_button.setText("◐")
        else:
            self.theme_switch_button.setIcon(QtGui.QIcon())
            self.theme_switch_button.setText("◐")
        self.theme_switch_button.setToolTip(
            f"Current: {self.theme_id}. Click to switch to {next_theme}"
        )

    def _apply_theme(self, theme_id: str, persist: bool = True) -> None:
        resolved = _resolve_theme(theme_id)
        self.theme_id = resolved
        self.theme = THEMES[self.theme_id]
        self.setStyleSheet(
            str(self.theme["window_stylesheet"]) % {"status_font_px": self._status_font_px}
        )
        delegate = self.chat_view.itemDelegate()
        if isinstance(delegate, ChatItemDelegate):
            bubble_palette = self.theme.get("bubbles", {})
            delegate_palette = (
                dict(bubble_palette) if isinstance(bubble_palette, dict) else None
            )
            delegate.set_bubble_palette(delegate_palette)
            self.chat_view.viewport().update()
        if persist:
            save_theme(self.theme_id)
        self._update_theme_switch_label()

    @QtCore.pyqtSlot()
    def on_theme_switch_clicked(self) -> None:
        next_theme = "night" if self.theme_id == "ligth" else "ligth"
        self._apply_theme(next_theme, persist=True)

    def handle_trust_decision(self, peer_addr: str, fingerprint: str, signing_key_hex: str) -> bool:
        """
        TOFU-подтверждение: показать пользователю fingerprint нового ключа пира.

        Возвращает True, если пользователь доверяет ключу и согласен его закрепить.
        """
        short_addr = (peer_addr or "").strip()
        if len(short_addr) > 40:
            short_addr = f"{short_addr[:18]}...{short_addr[-18:]}"
        short_key = (signing_key_hex or "")[:24]
        msg = (
            "First contact with this peer signing key.\n\n"
            f"Peer: {short_addr}\n"
            f"Fingerprint (SHA-256, short): {fingerprint}\n"
            f"PubKey (hex, prefix): {short_key}...\n\n"
            "Trust and pin this key (TOFU)?"
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Trust on First Use (TOFU)",
            msg,
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def refresh_status_label(self) -> None:
        """Обновить строку статуса с учётом профиля и persist-режима."""
        status = self._last_status
        mode = "PERSISTENT" if self.profile != "default" else "TRANSIENT"
        ack_drop_total = 0
        try:
            telemetry = self.core.get_ack_telemetry()
            ack_drop_total = int(sum(int(v) for v in telemetry.values()))
        except Exception:
            ack_drop_total = 0
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

        ack_part = f" | ACKdrop: {ack_drop_total}" if ack_drop_total > 0 else ""
        self.status_label.setText(
            f"Status: {status} | Profile: {self.profile} ({mode}) | Stored peer: {stored_disp}{ack_part}"
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
    def on_cancel_transfer(self) -> None:
        self.core.cancel_file_transfer()
        self._transfer_timer.stop()
        if self._transfer_row is not None:
            self.chat_model.update_item(
                self._transfer_row,
                ChatItem(
                    kind="error",
                    timestamp="",
                    sender="FILE",
                    text="Transfer cancelled",
                ),
            )
            self._transfer_row = None

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
        self.setWindowTitle(f"I2PChat @ {clean_profile}")
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
    def on_send_pic_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select image to send",
            "",
            "Images (*.png *.jpg *.jpeg);;All Files (*)",
        )
        if not path:
            return
        asyncio.create_task(self.core.send_image(path))

    @QtCore.pyqtSlot(str)
    def on_image_open_requested(self, path: str) -> None:
        """Открыть изображение в системном просмотрщике."""
        if not os.path.exists(path):
            self.handle_error(f"Image not found: {path}")
            return
        
        url = QtCore.QUrl.fromLocalFile(path)
        QtGui.QDesktopServices.openUrl(url)

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
    if sys.platform == "darwin":
        # На macOS используем системный шрифт, чтобы не вызывать fallback-алиасы.
        base_font = app.font()
        base_font.setPointSize(font_pt)
    else:
        base_font = QtGui.QFont("Inter", font_pt)
    base_font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
    app.setFont(base_font)

    saved_theme = load_saved_theme()
    selected_theme = saved_theme

    # 1) если профиль передан аргументом (CLI), используем его как есть
    if len(sys.argv) > 1:
        profile: Optional[str] = sys.argv[1]
        if len(sys.argv) > 2:
            selected_theme = _resolve_theme(sys.argv[2].strip().lower())
            save_theme(selected_theme)
    else:
        # 2) для .app / обычного запуска без аргументов показываем диалог выбора профиля
        profiles = ["default"]
        try:
            for name in os.listdir(get_profiles_dir()):
                if name.endswith(".dat"):
                    base = os.path.splitext(name)[0]
                    if base not in profiles:
                        profiles.append(base)
        except OSError:
            pass

        dialog = ProfileSelectDialog(profiles, theme_id=saved_theme)
        app.installEventFilter(dialog)
        try:
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            profile = dialog.selected_profile()
        finally:
            app.removeEventFilter(dialog)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = ChatWindow(profile=profile, theme_id=selected_theme)
    window.show()

    # запускаем инициализацию ядра в Qt-совместимом event loop
    loop.create_task(window.start_core())

    try:
        loop.run_forever()
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()

