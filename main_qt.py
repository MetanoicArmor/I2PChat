import asyncio
import json
import logging
import os
import secrets
import subprocess
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from blindbox_state import atomic_write_json
from compose_drafts import apply_compose_draft_peer_switch
from reply_format import format_reply_quote
from send_retry_policy import should_start_auto_connect_retry as _should_start_auto_connect_retry
from status_presentation import build_status_presentation
from notification_prefs import (
    notification_body_for_display,
    should_play_notification_sound,
    should_show_tray_message,
)
from unread_counters import (
    bump_unread_for_incoming_peer_message,
    clear_unread_for_peer,
    total_unread,
)
from chat_history import (
    HistoryEntry,
    delete_history,
    load_history,
    normalize_peer_addr,
    save_history,
)
from i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    ensure_valid_profile_name,
    peek_persisted_stored_peer,
    get_downloads_dir,
    get_profiles_dir,
    get_images_dir,
    import_profile_dat_atomic,
    is_valid_profile_name,
    render_braille,
    render_bw,
    validate_image,
)
from contact_book import (
    ContactBook,
    ContactRecord,
    load_book,
    normalize_peer_address,
    remember_peer,
    save_book,
    set_last_active_peer,
    touch_peer_message_meta,
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
logger = logging.getLogger("i2pchat.gui")


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


def _contacts_file_path(profile: str) -> str:
    return os.path.join(get_profiles_dir(), f"{profile}.contacts.json")


def _short_b32_display(addr: str) -> str:
    clean = (addr or "").replace(".b32.i2p", "").strip()
    if len(clean) > 12:
        clean = f"{clean[:6]}..{clean[-6:]}"
    return clean + ".b32.i2p"


def _peer_lock_indicator_pixmap(
    *, locked: bool, light_theme: bool, dpr: float = 1.0
) -> QtGui.QPixmap:
    dpr = max(1.0, float(dpr))
    px = max(1, int(18 * dpr))
    pm = QtGui.QPixmap(px, px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    if locked:
        pen = QtGui.QPen(QtGui.QColor(0, 150, 72) if light_theme else QtGui.QColor(80, 220, 120))
    else:
        pen = QtGui.QPen(QtGui.QColor(120, 128, 140) if light_theme else QtGui.QColor(150, 155, 170))
    pen.setWidthF(max(1.2, 1.8 * dpr / 2))
    p.setPen(pen)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    scale = px / 18.0
    p.scale(scale, scale)
    p.drawArc(5, 3, 8, 7, 35 * 16, 110 * 16)
    p.drawRoundedRect(4, 8, 10, 9, 2, 2)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _network_status_display(code: str) -> str:
    """Человекочитаемая подпись для внутреннего кода сетевого статуса (core)."""
    return {
        "initializing": "starting",
        # Локально/SAM готовы, но видимость адреса в сети не подтверждена (туннели/восстановление).
        "local_ok": "pending",
        "visible": "visible",
    }.get(code, code)


def _blindbox_status_bar_and_tooltip(
    *,
    enabled: bool,
    state: str,
    sync: str,
    queue: str,
    epoch: str,
    privacy: str,
    hint: str,
    telemetry_ok: bool,
    insecure_local: bool = False,
) -> tuple[str, str]:
    """
    Короткая строка для статус-бара и текст для toolTip: что такое BlindBox + техника + подсказка.
    """
    preamble = (
        "What is BlindBox?\n"
        "Offline / delayed delivery: encrypted blobs are placed on shared I2P Blind Box "
        "servers. When the peer is away, messages can still be queued "
        "and picked up later while the app polls those boxes.\n"
    )
    tech = (
        f"Technical: enabled={enabled} state={state} poller={sync} "
        f"send_queue_index={queue} root_epoch={epoch} privacy_profile={privacy}"
    )
    warning = ""
    if insecure_local:
        warning = (
            "Warning: insecure local BlindBox mode is enabled "
            "(loopback replica without auth token; "
            "I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1)."
        )
    tail = "\n".join(x for x in (warning, tech, hint) if x)
    tooltip_block = preamble + tail

    if not telemetry_ok:
        return "BlindBox: status unknown", tooltip_block
    if not enabled:
        return "BlindBox: off", tooltip_block
    if insecure_local:
        return "BlindBox: insecure local", tooltip_block
    if state == "ready":
        if sync == "poll":
            return "BlindBox: on (polling Blind Boxes)", tooltip_block
        return "BlindBox: on", tooltip_block
    if state == "await-root":
        return "BlindBox: need live chat once", tooltip_block
    if state == "on":
        return "BlindBox: starting…", tooltip_block
    return "BlindBox: off", tooltip_block


def _delivery_status_bar_and_tooltip(state: str) -> tuple[str, str]:
    if state == "connecting-handshake":
        return (
            "Send: wait secure",
            "Live connection exists, secure handshake is still in progress.",
        )
    if state == "online-live":
        return (
            "Send: live",
            "Send route: live secure session (peer is connected).",
        )
    if state == "offline-ready":
        return (
            "Send: offline queue",
            "Send route: BlindBox queue. Peer may be offline; message will be delivered later.",
        )
    if state == "await-live-root":
        return (
            "Send: need Connect once",
            "BlindBox is enabled but not initialized for this peer yet. "
            "Run one successful live secure Connect to bootstrap offline delivery.",
        )
    if state == "blindbox-needs-locked-peer":
        return (
            "Send: lock peer first",
            "BlindBox requires a locked peer in the current profile.",
        )
    if state == "blindbox-needs-boxes":
        return (
            "Send: configure Blind Boxes",
            "No Blind Box servers are configured. Set I2PCHAT_BLINDBOX_REPLICAS, "
            "I2PCHAT_BLINDBOX_DEFAULT_REPLICAS, or I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE, "
            "or unset I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS to use release defaults (2 boxes).",
        )
    if state == "blindbox-starting-local-session":
        return (
            "Send: wait local I2P",
            "Local I2P session is still starting. Wait for Pending/Visible.",
        )
    if state == "blindbox-disabled-transient":
        return (
            "Send: live only",
            "TRANSIENT profile: BlindBox offline queue is disabled.",
        )
    if state == "blindbox-disabled":
        return (
            "Send: live only",
            "BlindBox is disabled by configuration (I2PCHAT_BLINDBOX_ENABLED=0).",
        )
    if state == "blindbox-initializing":
        return (
            "Send: offline starting",
            "BlindBox offline path is still initializing.",
        )
    return ("Send: unavailable", "Delivery route is currently unavailable.")


def _save_pasted_qimage_to_images_dir(image: QtGui.QImage) -> Optional[str]:
    """Сохранить картинку из буфера в каталог images/ как PNG (универсально для validate_image)."""
    if image.isNull():
        return None
    img_dir = get_images_dir()
    try:
        os.makedirs(img_dir, exist_ok=True)
    except OSError:
        return None
    name = f"paste_{int(time.time() * 1000)}_{secrets.token_hex(4)}.png"
    path = os.path.join(img_dir, name)
    if image.save(path, "PNG"):
        return path
    return None


def _compose_bar_input_height_px(edit: QtWidgets.QPlainTextEdit, *, lines: int = 2) -> int:
    """Высота поля ввода под заданное число видимых строк (padding из QSS QPlainTextEdit 8+8)."""
    fm = edit.fontMetrics()
    line_px = max(int(fm.lineSpacing()), int(fm.height()))
    dm = int(float(edit.document().documentMargin()) * 2.0)
    vpad = 16  # padding: 8px сверху и снизу в темах для QPlainTextEdit
    return max(48, vpad + dm + line_px * lines + 4)


def _is_path_within_directory(path: str, directory: str) -> bool:
    """Проверить, что путь после realpath остается внутри directory."""
    try:
        real_path = os.path.realpath(path)
        real_dir = os.path.realpath(directory)
        return os.path.commonpath([real_path, real_dir]) == real_dir
    except (OSError, ValueError):
        return False


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
                border: none;
                border-radius: 10px;
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
            QMainWindow { background-color: #e6eaf2; }
            QWidget#ChatSurface {
                background: #f2f4f8;
                border: 1px solid #c5cdd9;
                border-radius: 14px;
            }
            QLabel#ChatSearchStatusInline {
                color: rgba(60, 60, 67, 0.55);
                background: transparent;
                padding-right: 10px;
                font-size: 12px;
            }
            QListView {
                background: #ffffff;
                border: none;
                border-radius: 12px;
                padding: %(ui_grid_px)dpx;
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
                background: #f5f6f8;
                border: none;
                border-radius: 9px;
                padding: 8px 10px;
                color: #1d1d1f;
            }
            QLineEdit#PeerAddressEdit {
                padding: 8px 10px 8px 8px;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                background: #ffffff;
                border: 1px solid #0a84ff;
            }
            QWidget#ComposeBar, QWidget#ActionToolbar {
                background: #eaedf4;
                border: 1px solid #cdd4e0;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #f8f9fc;
                border-radius: 9px;
                padding: 8px 14px;
                color: #20232b;
                border: none;
            }
            QPushButton:hover {
                background-color: #f0f3f8;
            }
            QPushButton:pressed { background-color: #e4e9f2; }
            QPushButton#PrimaryActionButton {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QPushButton#PrimaryActionButton:hover { background-color: #2d95ff; }
            QPushButton#PrimaryActionButton:pressed { background-color: #0076e9; }
            QPushButton#ConnectPeerButton {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QPushButton#ConnectPeerButton:hover { background-color: #2d95ff; }
            QPushButton#ConnectPeerButton:pressed { background-color: #0076e9; }
            QPushButton#ConnectPeerButton:disabled {
                background-color: #c8d9f2;
                color: rgba(255, 255, 255, 0.82);
            }
            QPushButton#DisconnectPeerButton {
                background-color: #e6ebf3;
                color: #b94b45;
            }
            QPushButton#DisconnectPeerButton:hover { background-color: #dfe6f0; }
            QPushButton#DisconnectPeerButton:pressed { background-color: #d5deeb; }
            QPushButton#DisconnectPeerButton:disabled {
                background-color: #eef1f6;
                color: rgba(185, 75, 69, 0.38);
            }
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
                background-color: #e2e7f0;
                border: none;
                border-radius: 9px;
                color: #333845;
                padding: 4px 12px;
                font-size: 18px;
                min-width: 32px;
            }
            QToolButton#MoreActionsButton:hover {
                background-color: #d5dce8;
            }
            QToolButton#MoreActionsButton:pressed {
                background-color: #c9d2e0;
            }
            QToolButton#ThemeSwitchButton {
                background-color: #f8f9fc;
                border: none;
                border-radius: 10px;
                color: #525966;
                padding: 0px;
                min-width: %(status_row_height_px)spx;
                min-height: %(status_row_height_px)spx;
            }
            QToolButton#ThemeSwitchButton:hover {
                background-color: #ffffff;
            }
            QToolButton#ThemeSwitchButton:pressed {
                background-color: #eef1f6;
            }
            QWidget#ContactsSidebar {
                background: #ebecef;
                border: 1px solid #d5d9e0;
                border-radius: 14px;
            }
            QLabel#ContactsSidebarTitle {
                color: #525966;
                font-size: 12px;
                font-weight: 600;
            }
            QListWidget#ContactsList {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget#ContactsList::item {
                background: transparent;
                padding: 0px;
            }
            QListWidget#ContactsList::item:selected {
                background: rgba(10, 132, 255, 0.12);
                border-radius: 8px;
            }
            QLabel#ContactRowTitle {
                color: #1d1d1f;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#ContactRowSubtitle {
                color: #6b7280;
                font-size: 11px;
            }
            QWidget#ContactsResizeGrip {
                background: transparent;
            }
            QWidget#ContactsResizeGrip:hover {
                background: rgba(0, 0, 0, 0.06);
            }
            QPushButton#ContactsSidebarToggle {
                background-color: #f8f9fc;
                border: none;
                border-radius: 10px;
                color: #525966;
                font-size: %(status_font_px)spx;
                font-weight: normal;
                min-width: %(contacts_toggle_btn_width_px)spx;
                max-width: %(contacts_toggle_btn_width_px)spx;
                min-height: %(status_row_height_px)spx;
                padding: 0px;
            }
            QPushButton#ContactsSidebarToggle:hover {
                background-color: #ffffff;
            }
            QPushButton#ContactsSidebarToggle:pressed {
                background-color: #eef1f6;
            }
            QLabel#PeerLockIndicator {
                min-width: 22px;
                max-width: 22px;
            }
            QLabel { color: #1d1d1f; }
            QLabel#StatusLabel {
                background-color: #f8f9fc;
                border: none;
                border-radius: 10px;
                padding: 0px %(ui_grid_px)dpx;
                min-height: 30px;
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
            "peer_bg": "#e8ebf1",
            "peer_text": "#1c1c1e",
            "system_bg": "#eef1f6",
            "system_text": "#5f6673",
            "error_bg": "#f2d8d7",
            "error_text": "#7c302c",
            "success_bg": "#d7ebdc",
            "success_text": "#245039",
            "file_bg": "#e4e8f0",
            "file_text": "#1d1d1f",
            "fallback_bg": "#e6eaf2",
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
            "image_placeholder_bg": "#d8dce5",
            "image_placeholder_text": "#3a3a40",
            "image_me_bg": "#2f92f0",
            "image_peer_bg": "#e0e3eb",
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
                border: none;
                border-radius: 10px;
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
            QLabel#ChatSearchStatusInline {
                color: rgba(245, 245, 247, 0.55);
                background: transparent;
                padding-right: 10px;
                font-size: 12px;
            }
            QListView {
                background: transparent;
                border: none;
                padding: %(ui_grid_px)dpx;
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
            QLineEdit#PeerAddressEdit {
                padding: 8px 10px 8px 8px;
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
            QPushButton#ConnectPeerButton {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QPushButton#ConnectPeerButton:hover { background-color: #3a9eff; }
            QPushButton#ConnectPeerButton:pressed { background-color: #0069d9; }
            QPushButton#ConnectPeerButton:disabled {
                background-color: rgba(10, 132, 255, 0.32);
                color: rgba(255, 255, 255, 0.5);
            }
            QPushButton#DisconnectPeerButton {
                background-color: rgba(255, 255, 255, 0.10);
                color: #ff8f88;
            }
            QPushButton#DisconnectPeerButton:hover { background-color: rgba(255, 255, 255, 0.16); }
            QPushButton#DisconnectPeerButton:pressed { background-color: rgba(255, 255, 255, 0.22); }
            QPushButton#DisconnectPeerButton:disabled {
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 143, 136, 0.32);
            }
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
                padding: 0px;
                min-width: %(status_row_height_px)spx;
                min-height: %(status_row_height_px)spx;
            }
            QToolButton#ThemeSwitchButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
            QToolButton#ThemeSwitchButton:pressed {
                background-color: rgba(255, 255, 255, 0.18);
            }
            QWidget#ContactsSidebar {
                background: rgba(34, 37, 45, 0.72);
                border: 1px solid #2f3541;
                border-radius: 14px;
            }
            QLabel#ContactsSidebarTitle {
                color: #9fa1b5;
                font-size: 12px;
                font-weight: 600;
            }
            QListWidget#ContactsList {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget#ContactsList::item {
                background: transparent;
                padding: 0px;
            }
            QListWidget#ContactsList::item:selected {
                background: rgba(10, 132, 255, 0.22);
                border-radius: 8px;
            }
            QLabel#ContactRowTitle {
                color: #f5f5f7;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#ContactRowSubtitle {
                color: #9fa1b5;
                font-size: 11px;
            }
            QWidget#ContactsResizeGrip {
                background: transparent;
            }
            QWidget#ContactsResizeGrip:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton#ContactsSidebarToggle {
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 10px;
                color: #9fa1b5;
                font-size: %(status_font_px)spx;
                font-weight: normal;
                min-width: %(contacts_toggle_btn_width_px)spx;
                max-width: %(contacts_toggle_btn_width_px)spx;
                min-height: %(status_row_height_px)spx;
                padding: 0px;
            }
            QPushButton#ContactsSidebarToggle:hover {
                background-color: rgba(255, 255, 255, 0.10);
            }
            QPushButton#ContactsSidebarToggle:pressed {
                background-color: rgba(255, 255, 255, 0.14);
            }
            QLabel#PeerLockIndicator {
                min-width: 22px;
                max-width: 22px;
            }
            QLabel { color: #f5f5f7; }
            QLabel#StatusLabel {
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 10px;
                padding: 0px %(ui_grid_px)dpx;
                min-height: 30px;
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
    try:
        atomic_write_json(path, data)
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


def load_notify_sound_enabled() -> bool:
    data = _load_ui_prefs()
    return data.get("notify_sound_enabled") is not False


def save_notify_sound_enabled(enabled: bool) -> None:
    data = _load_ui_prefs()
    if enabled:
        data.pop("notify_sound_enabled", None)
    else:
        data["notify_sound_enabled"] = False
    _save_ui_prefs(data)


def load_notify_hide_body() -> bool:
    data = _load_ui_prefs()
    return data.get("notify_hide_body") is True


def save_notify_hide_body(hide: bool) -> None:
    data = _load_ui_prefs()
    if hide:
        data["notify_hide_body"] = True
    else:
        data.pop("notify_hide_body", None)
    _save_ui_prefs(data)


def load_notify_quiet_mode() -> bool:
    data = _load_ui_prefs()
    return data.get("notify_quiet_mode") is True


def save_notify_quiet_mode(quiet: bool) -> None:
    data = _load_ui_prefs()
    if quiet:
        data["notify_quiet_mode"] = True
    else:
        data.pop("notify_quiet_mode", None)
    _save_ui_prefs(data)


def load_history_enabled() -> bool:
    data = _load_ui_prefs()
    val = data.get("history_enabled")
    return val is not False


def save_history_enabled(enabled: bool) -> None:
    data = _load_ui_prefs()
    data["history_enabled"] = enabled
    _save_ui_prefs(data)


def load_history_max_messages() -> int:
    data = _load_ui_prefs()
    val = data.get("history_max_messages")
    if isinstance(val, int) and val > 0:
        return val
    return 1000


COMPOSE_DRAFTS_MAX_KEYS = 100
COMPOSE_DRAFTS_DEBOUNCE_MS = 1500


def _compose_drafts_file_path(profile: str) -> str:
    return os.path.join(get_profiles_dir(), f"{profile}.compose_drafts.json")


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
    saved_file_path: Optional[str] = None  # абсолютный путь к полученному файлу на диске
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

    def item_at(self, row: int) -> Optional[ChatItem]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None


class ChatListView(QtWidgets.QListView):
    """QListView для баблов чата.

    - перераскладывает элементы при изменении ширины (для переноса строк)
    - поддерживает копирование текста (контекстное меню и Cmd/Ctrl+C)
    - открывает изображения по двойному клику
    """
    cancelTransferRequested = QtCore.pyqtSignal()
    imageOpenRequested = QtCore.pyqtSignal(str)  # path to image
    replyRequested = QtCore.pyqtSignal(str)  # quoted block for compose field

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.DefaultContextMenu)
        self._theme_id = THEME_DEFAULT
        self._context_popup: Optional["ActionsPopup"] = None
        self._context_popup_suppress_until_ms = 0
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

    def _copy_path(self, path: str) -> None:
        QtWidgets.QApplication.clipboard().setText(path)

    def set_theme(self, theme_id: str) -> None:
        self._theme_id = _resolve_theme(theme_id)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not isinstance(item, ChatItem):
            return

        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        if now_ms < self._context_popup_suppress_until_ms:
            return
        if self._context_popup is None:
            popup_parent = self.window() if isinstance(self.window(), QtWidgets.QWidget) else self
            self._context_popup = ActionsPopup(popup_parent)
            self._context_popup.closed.connect(self._on_context_popup_closed)
        elif self._context_popup.isVisible():
            self._context_popup.hide()
            return
        self._context_popup.clear_actions()
        self._context_popup.apply_theme(self._theme_id)

        def add_copy_text() -> None:
            self._context_popup.add_action(
                "Copy text", lambda i=index: self._copy_index_text(i, with_meta=False)
            )

        def add_copy_timestamp() -> None:
            self._context_popup.add_action(
                "Copy with timestamp", lambda i=index: self._copy_index_text(i, with_meta=True)
            )

        k = item.kind
        if k == "image_inline" and item.image_path:
            p = item.image_path
            self._context_popup.add_action(
                "Open",
                lambda path=p: self.imageOpenRequested.emit(path),
            )
            self._context_popup.add_action(
                "Copy path", lambda path=p: self._copy_path(path),
            )
            if item.text.strip():
                add_copy_text()
                add_copy_timestamp()
        elif k == "success":
            if item.saved_file_path and os.path.isfile(item.saved_file_path):
                fp = item.saved_file_path
                self._context_popup.add_action(
                    "Open",
                    lambda path=fp: QtGui.QDesktopServices.openUrl(
                        QtCore.QUrl.fromLocalFile(path)
                    ),
                )
                self._context_popup.add_action(
                    "Copy path", lambda path=fp: self._copy_path(path),
                )
            if item.open_folder_path and os.path.isdir(item.open_folder_path):
                folder = item.open_folder_path
                self._context_popup.add_action(
                    "Open folder",
                    lambda d=folder: QtGui.QDesktopServices.openUrl(
                        QtCore.QUrl.fromLocalFile(d)
                    ),
                )
                self._context_popup.add_action(
                    "Copy folder path", lambda d=folder: self._copy_path(d)
                )
            if item.text.strip():
                add_copy_text()
                add_copy_timestamp()
        elif k in ("me", "peer"):
            add_copy_text()
            add_copy_timestamp()
            if item.text.strip():
                self._context_popup.add_action(
                    "Reply",
                    lambda it=item: self.replyRequested.emit(
                        format_reply_quote(it.sender, it.text)
                    ),
                )
        elif k == "transfer" and item.text.strip():
            fn = item.text.strip()
            self._context_popup.add_action(
                "Copy filename", lambda name=fn: self._copy_path(name)
            )
        else:
            if item.text.strip():
                add_copy_text()
                add_copy_timestamp()

        if self._context_popup.surface_layout.count() > 0:
            self._context_popup.show_at_global(event.globalPos())

    @QtCore.pyqtSlot()
    def _on_context_popup_closed(self) -> None:
        self._context_popup_suppress_until_ms = (
            int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 180
        )

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
    # Макс. размер превью в бабле (масштабирование с сохранением пропорций).
    IMAGE_MAX_WIDTH = 560
    IMAGE_MAX_HEIGHT = 420
    
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
        real_path = os.path.realpath(path)
        if real_path in self._pixmap_cache:
            return self._pixmap_cache[real_path]

        if not _is_path_within_directory(real_path, get_images_dir()):
            return None

        pixmap = QtGui.QPixmap(real_path)
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
        
        self._pixmap_cache[real_path] = pixmap
        return pixmap

    def _text_max_line_advance(self, text: str, metrics: QtGui.QFontMetrics) -> int:
        """Макс. ширина среди явных строк (по \\n); для одной строки = её длина."""
        if not text:
            return int(metrics.horizontalAdvance(" "))
        best = 0
        for line in text.split("\n"):
            w = int(metrics.horizontalAdvance(line if line else " "))
            best = max(best, w)
        return best if best > 0 else int(metrics.horizontalAdvance(" "))

    def _bubble_width(self, cell_width: int, text: str, font: QtGui.QFont) -> int:
        """
        Ширина бабла:
        - не больше 75% строки списка
        - по ширине текста: максимальная из строк (перевод строки = несколько строк)
        - для одной короткой строки — минимум ~40% (визуально «таблетка»)
        - для многострочных сообщений — не растягиваем до 40%: только контент + небольшой пол
        """
        metrics = QtGui.QFontMetrics(font)
        content_px = self._text_max_line_advance(text, metrics) + self.PADDING_X * 4
        max_w = int(cell_width * 0.75)
        multiline = "\n" in (text or "")
        if multiline:
            min_w = max(72, int(cell_width * 0.12))
        else:
            min_w = int(cell_width * 0.4)
        return max(min_w, min(max_w, content_px))

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
        bubble_width = int(cell_width * 0.58)
        
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
        border_color = bg_color.darker(112)
        gloss_color = QtGui.QColor(255, 255, 255, 22 if is_sending else 16)
        painter.setBrush(bg_color)
        painter.setPen(QtGui.QPen(border_color, 1.0))
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            self.PADDING_Y / 2,
            -self.PADDING_X / 2,
            -self.PADDING_Y / 2,
        )
        painter.drawRoundedRect(bubble_rect, 14, 14)
        gloss_rect = QtCore.QRectF(
            bubble_rect.left() + 1,
            bubble_rect.top() + 1,
            bubble_rect.width() - 2,
            max(8.0, bubble_rect.height() * 0.42),
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(gloss_color)
        painter.drawRoundedRect(gloss_rect, 13, 13)

        inner_rect = bubble_rect.adjusted(
            self.PADDING_X, self.PADDING_Y, -self.PADDING_X, -self.PADDING_Y
        )
        
        base_font = painter.font()
        metrics = QtGui.QFontMetrics(base_font)
        
        if item.sender == "IMAGE":
            action = "Uploading image" if is_sending else "Receiving image"
        else:
            action = "Sending file" if is_sending else "Receiving file"
        header_text = f"{action}: {item.text}"
        label_color = self._c("transfer_label", "#ffffff")
        painter.setPen(label_color)
        header_font = QtGui.QFont(base_font)
        header_font.setWeight(QtGui.QFont.Weight.DemiBold)
        painter.setFont(header_font)
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
        
        bar_height = 12
        pct_w = 42
        pct_gap = 8
        bar_width = max(60.0, inner_rect.width() - pct_w - pct_gap)
        bar_rect = QtCore.QRectF(
            inner_rect.left(),
            inner_rect.top() + metrics.height() + 8,
            bar_width,
            bar_height,
        )
        pct_rect = QtCore.QRectF(
            bar_rect.right() + pct_gap,
            bar_rect.top() - 1,
            pct_w,
            bar_height + 2,
        )

        bar_bg = self._c("transfer_bar_bg", "#0d1b2a")
        corner = bar_height / 2
        painter.setPen(QtGui.QPen(bar_bg.darker(106), 1.0))
        painter.setBrush(bar_bg)
        painter.drawRoundedRect(bar_rect, corner, corner)

        progress = max(0.0, min(1.0, item.progress))
        if progress > 0:
            fill_width = max(2.0, bar_rect.width() * progress)
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
            
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill_rect, corner, corner)
            sheen = QtGui.QColor(255, 255, 255, 38)
            painter.setBrush(sheen)
            painter.drawRoundedRect(
                QtCore.QRectF(fill_rect.left(), fill_rect.top(), fill_rect.width(), fill_rect.height() * 0.5),
                corner,
                corner,
            )

        pct = int(progress * 100)
        pct_text = f"{pct}%"
        pct_font = QtGui.QFont(base_font)
        pct_font.setPointSize(max(base_font.pointSize() - 1, 8))
        painter.setPen(self._c("transfer_meta", "#a0a0a0"))
        painter.setFont(pct_font)
        painter.drawText(
            pct_rect,
            int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
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
        else:
            size_rect = QtCore.QRectF(
                inner_rect.left(),
                bar_rect.bottom() + 4,
                inner_rect.width(),
                metrics.height(),
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
    """Многострочное поле ввода: Enter — новая строка.

    Отправка: Shift+Enter (везде), а также Ctrl+Enter (Windows/Linux) или ⌘+Enter (macOS).
    """
    sendRequested = QtCore.pyqtSignal()
    # Путь к изображению после вставки из буфера (картинка или локальный файл)
    imagePasteReady = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._theme_id = THEME_DEFAULT
        self._context_popup: Optional["ActionsPopup"] = None
        self._context_popup_suppress_until_ms = 0

    def canPaste(self) -> bool:  # type: ignore[override]
        mime = QtWidgets.QApplication.clipboard().mimeData()
        if mime is not None and mime.hasImage():
            return True
        return super().canPaste()

    def paste(self) -> None:  # type: ignore[override]
        mime = QtWidgets.QApplication.clipboard().mimeData()
        if mime is not None and mime.hasImage():
            self.insertFromMimeData(mime)
            return
        super().paste()

    def insertFromMimeData(self, source: QtGui.QMimeData) -> None:  # type: ignore[override]
        if source.hasImage():
            raw = source.imageData()
            qimg = QtGui.QImage()
            if isinstance(raw, QtGui.QImage):
                qimg = raw
            elif isinstance(raw, QtGui.QPixmap):
                qimg = raw.toImage()
            if not qimg.isNull():
                path = _save_pasted_qimage_to_images_dir(qimg)
                if path:
                    self.imagePasteReady.emit(path)
                return
        if source.hasUrls():
            for url in source.urls():
                if not url.isLocalFile():
                    continue
                fpath = url.toLocalFile()
                low = fpath.lower()
                if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                ok, _, _ = validate_image(fpath)
                if ok:
                    self.imagePasteReady.emit(fpath)
                    return
        super().insertFromMimeData(source)

    def set_theme(self, theme_id: str) -> None:
        self._theme_id = _resolve_theme(theme_id)

    def _delete_selection(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self.setTextCursor(cursor)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        if now_ms < self._context_popup_suppress_until_ms:
            return
        if self._context_popup is None:
            popup_parent = self.window() if isinstance(self.window(), QtWidgets.QWidget) else self
            self._context_popup = ActionsPopup(popup_parent)
            self._context_popup.closed.connect(self._on_context_popup_closed)
        elif self._context_popup.isVisible():
            self._context_popup.hide()
            return
        self._context_popup.clear_actions()
        self._context_popup.apply_theme(self._theme_id)
        self._context_popup.add_action("Undo", self.undo, enabled=self.document().isUndoAvailable())
        self._context_popup.add_action("Redo", self.redo, enabled=self.document().isRedoAvailable())
        self._context_popup.add_separator()
        has_selection = self.textCursor().hasSelection()
        self._context_popup.add_action("Cut", self.cut, enabled=has_selection)
        self._context_popup.add_action("Copy", self.copy, enabled=has_selection)
        self._context_popup.add_action("Paste", self.paste, enabled=bool(self.canPaste()))
        self._context_popup.add_action("Delete", self._delete_selection, enabled=has_selection)
        self._context_popup.add_separator()
        self._context_popup.add_action("Select All", self.selectAll, enabled=not self.document().isEmpty())
        self._context_popup.show_at_global(event.globalPos())

    @QtCore.pyqtSlot()
    def _on_context_popup_closed(self) -> None:
        self._context_popup_suppress_until_ms = (
            int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 180
        )

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            modifiers = event.modifiers()
            shift_down = bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier)

            # На macOS Command = MetaModifier.
            if sys.platform == "darwin":
                # На части macOS-конфигураций ⌘+Enter приходит как ControlModifier.
                # Поэтому для отправки учитываем и Meta, и Control.
                command_like = bool(
                    modifiers
                    & (
                        QtCore.Qt.KeyboardModifier.MetaModifier
                        | QtCore.Qt.KeyboardModifier.ControlModifier
                    )
                )
                command_down = command_like
                wants_send = shift_down or command_down
            else:
                ctrl_down = bool(modifiers & QtCore.Qt.KeyboardModifier.ControlModifier)
                wants_send = shift_down or ctrl_down

            if wants_send:
                self.sendRequested.emit()
                event.accept()
            else:
                # Пусть стандартный обработчик QPlainTextEdit вставит перевод строки.
                super().keyPressEvent(event)
            return

        super().keyPressEvent(event)


class AddressLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._theme_id = THEME_DEFAULT
        self._context_popup: Optional["ActionsPopup"] = None
        self._context_popup_suppress_until_ms = 0

    def set_theme(self, theme_id: str) -> None:
        self._theme_id = _resolve_theme(theme_id)

    def _delete_selection(self) -> None:
        if self.hasSelectedText():
            self.del_()

    def _can_paste(self) -> bool:
        if self.isReadOnly() or not self.isEnabled():
            return False
        mime = QtWidgets.QApplication.clipboard().mimeData()
        return bool(mime and mime.hasText())

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:  # type: ignore[override]
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        if now_ms < self._context_popup_suppress_until_ms:
            return
        if self._context_popup is None:
            popup_parent = self.window() if isinstance(self.window(), QtWidgets.QWidget) else self
            self._context_popup = ActionsPopup(popup_parent)
            self._context_popup.closed.connect(self._on_context_popup_closed)
        elif self._context_popup.isVisible():
            self._context_popup.hide()
            return
        self._context_popup.clear_actions()
        self._context_popup.apply_theme(self._theme_id)
        self._context_popup.add_action("Undo", self.undo, enabled=self.isUndoAvailable())
        self._context_popup.add_action("Redo", self.redo, enabled=self.isRedoAvailable())
        self._context_popup.add_separator()
        has_selection = self.hasSelectedText()
        self._context_popup.add_action("Cut", self.cut, enabled=has_selection)
        self._context_popup.add_action("Copy", self.copy, enabled=has_selection)
        self._context_popup.add_action("Paste", self.paste, enabled=self._can_paste())
        self._context_popup.add_action("Delete", self._delete_selection, enabled=has_selection)
        self._context_popup.add_separator()
        self._context_popup.add_action("Select All", self.selectAll, enabled=bool(self.text()))
        self._context_popup.show_at_global(event.globalPos())

    @QtCore.pyqtSlot()
    def _on_context_popup_closed(self) -> None:
        self._context_popup_suppress_until_ms = (
            int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 180
        )


class ProfileComboBox(QtWidgets.QComboBox):
    popupRequested = QtCore.pyqtSignal()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popupRequested.emit()


class RoundedVerticalScrollbar(QtWidgets.QWidget):
    """Кастомный скроллбар для popup-списка профилей (точные “пилюльные” концы)."""

    def __init__(
        self,
        linked_scrollbar: QtWidgets.QScrollBar,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sb = linked_scrollbar

        # Цвета будут проставлены в apply_theme().
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
        # rangeChanged есть у QScrollBar, но типизация может отличаться в разных сборках.
        self._sb.rangeChanged.connect(self.update)  # type: ignore[attr-defined]
        # У QScrollBar/Qt не везде есть сигнал pageStepChanged.
        # В вычислении thumb используем pageStep() (если доступен) при перерисовке.

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

        # pageStep влияет на “размер” видимой области.
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

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        if sys.platform.startswith("win"):
            # Avoid native DWM shadow/frame artifacts around translucent popup on Windows.
            popup_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ProfileComboPopupWindow")
        self.setMinimumWidth(220)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.surface = QtWidgets.QFrame(self)
        self.surface.setObjectName("ProfileComboPopupSurface")
        root.addWidget(self.surface)

        inner = QtWidgets.QHBoxLayout(self.surface)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.setSpacing(0)

        self.list = QtWidgets.QListWidget(self.surface)
        self.list.setObjectName("ProfileComboPopupList")
        self.list.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Нативный вертикальный скроллбар скрываем: рисуем кастомный справа.
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
        row_h = max(24, self.list.sizeHintForRow(0))
        visible = min(max(1, self.list.count()), 8)
        content_h = visible * row_h + 12
        self.list.setMinimumHeight(content_h)
        self.list.setMaximumHeight(content_h)
        self.setFixedWidth(max(anchor.width(), self.minimumWidth()))
        self.adjustSize()
        w, h = self.width(), self.height()
        self.move(
            _global_position_popup_below_anchor(
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
                /* Скроллбар внутри dropdown-попапа должен выглядеть как macOS-пилюля */
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
                    padding: 6px 10px;
                    margin: 1px 2px;
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
                thumb=QtGui.QColor(255, 255, 255, 51),  # 0.20 alpha
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
                /* Скроллбар внутри dropdown-попапа должен выглядеть как macOS-пилюля */
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
                    padding: 6px 10px;
                    margin: 1px 2px;
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
                thumb=QtGui.QColor(60, 60, 67, 72),  # ~0.28 alpha
                track=QtGui.QColor(0, 0, 0, 0),
            )


class ProfileComboWithArrow(QtWidgets.QWidget):
    """QComboBox с видимой стрелкой ▼ поверх области выпадающего списка."""
    
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = ProfileComboBox(self)
        self._completer: Optional[QtWidgets.QCompleter] = None
        layout.addWidget(self.combo)
        self._arrow = QtWidgets.QLabel("∨", self)
        self.set_arrow_color("#9fa1b5")
        self._arrow.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._arrow.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.popup = ProfileComboPopup(self)
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
        # Позволяет подбирать существующие профили по мере набора текста.
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
        self.popup.apply_theme(_resolve_theme(theme_id))
    
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


def _popup_screen_for_anchor(anchor: QtWidgets.QWidget) -> Optional[QtGui.QScreen]:
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


def _clamp_popup_top_left_to_available_geometry(
    top_left: QtCore.QPoint, popup_w: int, popup_h: int, geom: QtCore.QRect
) -> QtCore.QPoint:
    """Удерживает левый верх угла popup внутри availableGeometry (как у контекстного меню)."""
    x = max(geom.left(), min(top_left.x(), geom.right() - popup_w + 1))
    y = max(geom.top(), min(top_left.y(), geom.bottom() - popup_h + 1))
    return QtCore.QPoint(x, y)


def _global_position_popup_below_anchor(
    anchor: QtWidgets.QWidget,
    popup_w: int,
    popup_h: int,
    *,
    vertical_gap: int,
    align_right: bool,
) -> QtCore.QPoint:
    """
    Глобальный top-left: сначала под якорем; если снизу не помещается — над якорем.
    Затем поджатие к availableGeometry выбранного экрана.
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

    screen = _popup_screen_for_anchor(anchor)
    if screen is None:
        return pos_below
    geom = screen.availableGeometry()
    max_top_for_below = geom.bottom() - popup_h + 1

    if pos_below.y() > max_top_for_below and pos_above.y() >= geom.top():
        pos = pos_above
    else:
        pos = pos_below

    return _clamp_popup_top_left_to_available_geometry(pos, popup_w, popup_h, geom)


class ActionsPopup(QtWidgets.QFrame):
    """Кастомный popup вместо QMenu для одинаковой отрисовки на всех ОС."""
    closed = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        if sys.platform.startswith("win"):
            # Avoid native DWM shadow/frame artifacts around translucent popup on Windows.
            popup_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ActionsPopupWindow")
        self.setMinimumWidth(236)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.surface = QtWidgets.QFrame(self)
        self.surface.setObjectName("ActionsPopupSurface")
        root.addWidget(self.surface)

        self.surface_layout = QtWidgets.QVBoxLayout(self.surface)
        self.surface_layout.setContentsMargins(8, 8, 8, 8)
        self.surface_layout.setSpacing(4)

    def add_action(self, text: str, callback: Callable[[], None], enabled: bool = True) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text, self.surface)
        btn.setObjectName("ActionsPopupItem")
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(enabled)
        btn.clicked.connect(lambda: (self.hide(), callback()))
        self.surface_layout.addWidget(btn)
        return btn

    def add_separator(self) -> None:
        sep = QtWidgets.QFrame(self.surface)
        sep.setObjectName("ActionsPopupSeparator")
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
        self.surface_layout.addWidget(sep)

    def clear_actions(self) -> None:
        while self.surface_layout.count():
            item = self.surface_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def show_below(self, anchor: QtWidgets.QWidget) -> None:
        self.adjustSize()
        w, h = self.width(), self.height()
        self.move(
            _global_position_popup_below_anchor(
                anchor, w, h, vertical_gap=6, align_right=True
            )
        )
        self.show()

    def show_at_global(self, global_pos: QtCore.QPoint) -> None:
        self.adjustSize()
        pos = QtCore.QPoint(global_pos)
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            pos = _clamp_popup_top_left_to_available_geometry(
                pos, self.width(), self.height(), geom
            )
        self.move(pos)
        self.show()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.closed.emit()

    def apply_theme(self, theme_id: str) -> None:
        if theme_id == "night":
            self.setStyleSheet(
                """
                #ActionsPopupWindow {
                    background: transparent;
                }
                #ActionsPopupSurface {
                    background: rgba(34, 37, 45, 0.96);
                    border: none;
                    border-radius: 14px;
                }
                QPushButton#ActionsPopupItem {
                    background: transparent;
                    color: #e3e8f1;
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                    padding: 8px 12px;
                    font-size: 13px;
                }
                QPushButton#ActionsPopupItem:hover {
                    background: rgba(255, 255, 255, 0.10);
                }
                QPushButton#ActionsPopupItem:pressed {
                    background: rgba(255, 255, 255, 0.16);
                }
                QPushButton#ActionsPopupItem:disabled {
                    color: #7d8798;
                }
                QFrame#ActionsPopupSeparator {
                    background: #343a46;
                    max-height: 1px;
                    min-height: 1px;
                    border: none;
                    margin: 4px 8px;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                #ActionsPopupWindow {
                    background: transparent;
                }
                #ActionsPopupSurface {
                    background: #f6f7fa;
                    border: none;
                    border-radius: 14px;
                }
                QPushButton#ActionsPopupItem {
                    background: transparent;
                    color: #2c3442;
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                    padding: 8px 12px;
                    font-size: 13px;
                }
                QPushButton#ActionsPopupItem:hover {
                    background: #e5eaf2;
                }
                QPushButton#ActionsPopupItem:pressed {
                    background: #dfe6f0;
                }
                QPushButton#ActionsPopupItem:disabled {
                    color: #9da5b2;
                }
                QFrame#ActionsPopupSeparator {
                    background: #d6dce7;
                    max-height: 1px;
                    min-height: 1px;
                    border: none;
                    margin: 4px 8px;
                }
                """
            )


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
            "Use <b>default</b> for a one-time session, or enter a name to save your identity.<br>"
            "<b>Security note:</b> in <b>default</b> mode, TOFU trust is not persisted between app restarts."
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
        self.profile_combo_widget.enable_autocomplete()
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
        self.profile_combo_widget.apply_popup_theme(self._theme_id)
    
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

    def accept(self) -> None:  # type: ignore[override]
        selected = self.combo.currentText().strip() if self.combo.currentText() else ""
        if selected == "default":
            confirm = QtWidgets.QMessageBox.question(
                self,
                "Transient profile warning",
                "You selected the transient profile 'default'.\n\n"
                "TOFU trust pins are not persisted between app restarts in this mode.\n"
                "For persistent trust continuity, use a named profile.\n\n"
                "Continue with 'default' anyway?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        super().accept()
    
    def selected_profile(self) -> Optional[str]:
        text = self.combo.currentText().strip() if self.combo.currentText() else ""
        if not text:
            return None
        return ensure_valid_profile_name(text)


class ContactRowWidget(QtWidgets.QWidget):
    """Строка контакта в боковом списке (две строки: имя / превью)."""

    activate = QtCore.pyqtSignal(str)

    def __init__(self, record: ContactRecord, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._addr = record.addr
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        self._full_title = record.display_name.strip() or _short_b32_display(record.addr)
        self._full_sub = (record.last_preview or record.note or "").strip()
        self._title = QtWidgets.QLabel(self._full_title)
        self._title.setObjectName("ContactRowTitle")
        self._title.setWordWrap(False)
        self._title.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        f = self._title.font()
        f.setBold(True)
        self._title.setFont(f)
        sub_text = self._full_sub if self._full_sub else " "
        self._subtitle = QtWidgets.QLabel(sub_text)
        self._subtitle.setObjectName("ContactRowSubtitle")
        self._subtitle.setWordWrap(False)
        self._subtitle.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        sf = self._subtitle.font()
        sf.setPointSize(max(9, sf.pointSize() - 1))
        self._subtitle.setFont(sf)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )

    @property
    def contact_addr(self) -> str:
        return self._addr

    def _apply_elide(self) -> None:
        w = max(1, self.width() - 20)
        fm = self._title.fontMetrics()
        self._title.setText(
            fm.elidedText(self._full_title, QtCore.Qt.TextElideMode.ElideMiddle, w)
        )
        fm2 = self._subtitle.fontMetrics()
        sub_text = self._full_sub if self._full_sub else " "
        self._subtitle.setText(
            fm2.elidedText(sub_text, QtCore.Qt.TextElideMode.ElideRight, w)
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def set_record(self, record: ContactRecord) -> None:
        self._addr = record.addr
        self._full_title = record.display_name.strip() or _short_b32_display(record.addr)
        self._full_sub = (record.last_preview or record.note or "").strip()
        self.updateGeometry()
        self._apply_elide()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activate.emit(self._addr)
        super().mouseReleaseEvent(event)


class _ContactsSidebarResizeGrip(QtWidgets.QWidget):
    """Узкая зона для изменения ширины первой панели QSplitter."""

    def __init__(
        self,
        splitter: QtWidgets.QSplitter,
        delta_sign: int,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        host: Optional["ChatWindow"] = None,
        strip_width_px: int = 6,
    ) -> None:
        super().__init__(parent)
        self._splitter = splitter
        self._host = host
        self._delta_sign = 1 if delta_sign >= 0 else -1
        self._dragging = False
        self._start_global_x = 0
        self._start_sidebar_w = 0
        self.setObjectName("ContactsResizeGrip")
        self.setFixedWidth(max(1, strip_width_px))
        self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_global_x = int(event.globalPosition().x())
            sizes = self._splitter.sizes()
            self._start_sidebar_w = sizes[0] if sizes else 0
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging:
            dx = int(event.globalPosition().x()) - self._start_global_x
            rmin = self._host._SPLITTER_RIGHT_MIN_PX if self._host else 200
            mn = self._host._CONTACTS_SIDEBAR_MIN_OPEN_PX if self._host else 160
            new_w = max(mn, min(520, self._start_sidebar_w + self._delta_sign * dx))
            total = max(400, sum(self._splitter.sizes()) or self._splitter.width())
            self._splitter.setSizes([new_w, max(rmin, total - new_w)])
            if self._host is not None and new_w >= mn:
                self._host._expand_contacts_sidebar_from_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        self._dragging = False
        if self._host is not None:
            sizes = self._splitter.sizes()
            if sizes and sizes[0] >= self._host._CONTACTS_SIDEBAR_MIN_OPEN_PX:
                self._host._contacts_sidebar_width_saved = int(sizes[0])
        super().mouseReleaseEvent(event)


class ChatWindow(QtWidgets.QMainWindow):
    # Ниже этого порога ширины лейбла статуса показывается сокращённая строка.
    _STATUS_LABEL_COMPACT_PX = 700
    _STATUS_ROW_HEIGHT_PX = 30
    # Иконка луны/солнца внутри кнопки темы (кнопка остаётся _STATUS_ROW_HEIGHT_PX).
    _THEME_SWITCH_ICON_PX = 16
    # Единый шаг сетки отступов (окно, блоки колонки чата, статус-бар, QSS списка).
    _UI_GRID_PX = 8
    _UI_STRIP_VERTICAL_GAP_PX = _UI_GRID_PX
    _UI_STRIP_SIDE_GUTTER_PX = _UI_GRID_PX
    # Свёрнуто: 0 — левый край ◀ совпадает с левым краем колонки и строки статуса (см. main_layout).
    _CONTACTS_STRIP_EDGE_COLLAPSED_PX = 0
    # Развёрнуто: половина grid слева от ◀ и как левый inset колонки чата (визуально ровно по бокам ◀).
    _CONTACTS_STRIP_EDGE_EXPANDED_PX = _UI_GRID_PX // 2
    # Ширина как у кнопки темы в строке статуса (квадрат _STATUS_ROW_HEIGHT_PX).
    _CONTACTS_TOGGLE_BTN_WIDTH_PX = _STATUS_ROW_HEIGHT_PX
    # Ближе к шагу сетки, чем 3px — визуально ровнее зазор ◀↔чат.
    _CONTACTS_RESIZE_GRIP_WIDTH_PX = 4
    _CONTACTS_SIDEBAR_MIN_OPEN_PX = 160
    _CONTACTS_SIDEBAR_ANIM_MS = 200
    _SPLITTER_RIGHT_MIN_PX = 200

    def __init__(self, profile: Optional[str] = None, theme_id: str = THEME_DEFAULT) -> None:
        super().__init__()
        self.profile = profile or "default"
        self.theme_id = _resolve_theme(theme_id)
        self.theme = THEMES[self.theme_id]
        # Показываем профиль через разделитель-точку;
        # если вдруг имя профиля уже содержит служебный маркер в конце (" •"),
        # аккуратно убираем его, чтобы заголовок не заканчивался кружком.
        clean_profile = self.profile.rstrip(" •")
        self._window_title_base = f"I2PChat @ {clean_profile}"
        self._unread_by_peer: dict[str, int] = {}
        self._status_send_in_flight = False
        self.setWindowTitle(self._window_title_base)
        self.resize(900, 600)

        self._status_font_px = 10 if sys.platform == "win32" else 11

        # UI
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        g = self._UI_GRID_PX
        main_layout.setContentsMargins(g, g, g, g)
        main_layout.setSpacing(self._UI_GRID_PX)

        self.more_actions_popup = ActionsPopup(self)
        self._more_actions_suppress_until_ms = 0
        self.more_actions_popup.closed.connect(self._on_more_actions_popup_closed)

        self._history_loaded_for_peer: Optional[str] = None
        self._history_entries: list[HistoryEntry] = []
        self._history_dirty = False
        self._history_save_error_reported = False
        self._history_flush_timer = QtCore.QTimer(self)
        self._history_flush_timer.setInterval(60_000)
        self._history_flush_timer.timeout.connect(self._flush_history)

        # диагностическая строка статуса
        self.status_label = QtWidgets.QLabel("Status: initializing", self)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        # Горизонтальные поля плашки задаются в QSS (симметрично); indent не дублируем.
        self.status_label.setIndent(0)
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        # Важно для узких окон: строка статуса не должна задавать min-width окна.
        self.status_label.setMinimumWidth(0)
        self.status_label.setFixedHeight(self._STATUS_ROW_HEIGHT_PX)
        self.theme_switch_button = QtWidgets.QToolButton(self)
        self.theme_switch_button.setObjectName("ThemeSwitchButton")
        self.theme_switch_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.theme_switch_button.setAutoRaise(False)
        self.theme_switch_button.setFixedHeight(self._STATUS_ROW_HEIGHT_PX)
        self.theme_switch_button.setFixedWidth(self._STATUS_ROW_HEIGHT_PX)
        self.theme_switch_button.clicked.connect(self.on_theme_switch_clicked)
        self.status_row = QtWidgets.QWidget(self)
        status_row_layout = QtWidgets.QHBoxLayout(self.status_row)
        # Горизонтальные поля только у main_layout; иначе статус «уезжает» вправо относительно сплиттера/сайдбара.
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(self._UI_GRID_PX)
        status_row_layout.addWidget(self.status_label, 1)
        status_row_layout.addWidget(
            self.theme_switch_button,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )
        # Строка статус-бара (средняя детализация); подсказка при наведении отключена.
        self._status_line_text: str = (
            "Net:starting | Prof:default (T) | Link:offline | Peer:none | St:none | Sec:off | "
            "BlindBox: off | ACKdrop:0"
        )
        self._status_line_compact_text: str = (
            "Net:starting | offline | none | Sec:off | BB:off | ACKdrop:0"
        )
        self._last_status: str = "initializing"
        self._status_focus_signature: Optional[tuple[str, str, str, str]] = None
        self._status_force_expand_until_mono: float = 0.0
        self._status_event_text: str = ""
        self._status_focus_timer = QtCore.QTimer(self)
        self._status_focus_timer.setSingleShot(True)
        self._status_focus_timer.timeout.connect(self._on_status_focus_timeout)
        self._transfer_row: Optional[int] = None
        self._transfer_is_image: bool = False
        self._active_file_offer_boxes: list[QtWidgets.QMessageBox] = []
        self._auto_retry_send_task: Optional[asyncio.Task[None]] = None
        self._last_auto_retry_started_at: float = 0.0
        self._last_error_text: str = ""
        self._last_error_ts: float = 0.0

        self._compose_drafts: dict[str, str] = {}
        self._compose_draft_active_key: Optional[str] = None
        self._compose_drafts_save_timer = QtCore.QTimer(self)
        self._compose_drafts_save_timer.setSingleShot(True)
        self._compose_drafts_save_timer.setInterval(COMPOSE_DRAFTS_DEBOUNCE_MS)
        self._compose_drafts_save_timer.timeout.connect(self._flush_compose_drafts_to_disk)

        self._contact_book = ContactBook()
        self._contacts_sidebar_collapsed = False
        self._contacts_sidebar_width_saved = 280
        self._contacts_sidebar_anim: Optional[QtCore.QVariantAnimation] = None

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
        g = self._UI_GRID_PX
        col_left = self._CONTACTS_STRIP_EDGE_EXPANDED_PX
        chat_surface_layout.setContentsMargins(col_left, g, g, g)
        chat_surface_layout.setSpacing(0)
        self._chat_search_match_rows: list[int] = []
        self._chat_search_cur: int = -1
        self._chat_search_sync_suppressed: bool = False
        self._chat_search_debounce = QtCore.QTimer(self)
        self._chat_search_debounce.setSingleShot(True)
        self._chat_search_debounce.setInterval(200)
        self._chat_search_debounce.timeout.connect(self._rebuild_chat_search_matches)

        # Вложенный QHBoxLayout + обёртка вокруг QLineEdit: на macOS поле часто не
        # забирает горизонтальное растяжение (остаётся пустота справа от ◀▶).
        # ChatSurface: левый margin = col_left, правый = g — компенсируем здесь,
        # чтобы строка поиска визуально имела симметричные боковые отступы.
        search_h = QtWidgets.QHBoxLayout()
        search_h.setContentsMargins(
            max(0, g - col_left), 0, 0, max(2, g // 2)
        )
        search_h.setSpacing(self._UI_GRID_PX)
        self._chat_search_field_wrap = QtWidgets.QWidget(chat_surface)
        self._chat_search_field_wrap.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        _wrap_lay = QtWidgets.QHBoxLayout(self._chat_search_field_wrap)
        _wrap_lay.setContentsMargins(0, 0, 0, 0)
        _wrap_lay.setSpacing(0)
        self._chat_search_edit = QtWidgets.QLineEdit(self._chat_search_field_wrap)
        self._chat_search_edit.setPlaceholderText("Search in this chat…")
        self._chat_search_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._chat_search_edit.setMinimumWidth(0)
        _search_fm = self._chat_search_edit.fontMetrics()
        _search_row_h = max(34, _search_fm.height() + 14)
        self._chat_search_edit.setMinimumHeight(_search_row_h)
        self._chat_search_field_wrap.setMinimumHeight(_search_row_h)
        self._chat_search_edit.setTextMargins(
            self._chat_search_lineedit_left_pad_px(), 0, 0, 0
        )
        _wrap_lay.addWidget(self._chat_search_edit, 1)
        # QLabel внутри полосы поиска: QAction+TrailingPosition на macOS часто не рисует текст.
        self._chat_search_status_label = QtWidgets.QLabel("", self._chat_search_field_wrap)
        self._chat_search_status_label.setObjectName("ChatSearchStatusInline")
        self._chat_search_status_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._chat_search_status_label.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._chat_search_status_label.hide()
        self._chat_search_field_wrap.installEventFilter(self)
        self._chat_search_prev = QtWidgets.QPushButton("◀", chat_surface)
        self._chat_search_prev.setFixedWidth(36)
        self._chat_search_prev.setFixedHeight(_search_row_h)
        self._chat_search_prev.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._chat_search_prev.setToolTip("Previous match")
        self._chat_search_next = QtWidgets.QPushButton("▶", chat_surface)
        self._chat_search_next.setFixedWidth(36)
        self._chat_search_next.setFixedHeight(_search_row_h)
        self._chat_search_next.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._chat_search_next.setToolTip("Next match")
        search_h.addWidget(self._chat_search_field_wrap, 1)
        search_h.addWidget(self._chat_search_prev)
        search_h.addWidget(self._chat_search_next)
        search_h.setStretch(0, 1)
        self._chat_search_list = QtWidgets.QListWidget(chat_surface)
        self._chat_search_list.setMaximumHeight(100)
        self._chat_search_list.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        self._chat_search_list.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._chat_search_list.hide()
        self._chat_search_list.itemClicked.connect(self._on_chat_search_item_clicked)
        self._chat_search_list.itemActivated.connect(self._on_chat_search_item_clicked)
        self._chat_search_edit.textChanged.connect(self._schedule_chat_search_rebuild)
        self._chat_search_prev.clicked.connect(lambda: self._step_chat_search(-1))
        self._chat_search_next.clicked.connect(lambda: self._step_chat_search(1))
        chat_surface_layout.addLayout(search_h)
        chat_surface_layout.addWidget(self._chat_search_list)
        chat_surface_layout.addWidget(self.chat_view, 1)

        # панель ввода
        input_container = QtWidgets.QWidget(self)
        input_container.setObjectName("ComposeBar")
        input_layout = QtWidgets.QHBoxLayout(input_container)
        # Симметрично с правым краем (раньше слева был col_left — визуально уже).
        input_layout.setContentsMargins(g, g, g, g)
        input_layout.setSpacing(self._UI_GRID_PX)
        self.input_edit = MessageInputEdit(self)
        self.input_edit.setPlaceholderText(
            "Type message. Enter = new line; Shift+Enter or Ctrl/⌘+Enter = send."
        )
        font = self.input_edit.font()
        font.setPointSize(font.pointSize() + 1)
        self.input_edit.setFont(font)

        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setObjectName("PrimaryActionButton")

        compose_h = _compose_bar_input_height_px(self.input_edit, lines=2)
        self.input_edit.setMinimumHeight(compose_h)
        self.input_edit.setFixedHeight(compose_h)
        self.send_button.setMinimumHeight(compose_h)
        self.send_button.setFixedHeight(compose_h)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_button)

        # панель действий: сегментированные группы кнопок в стиле macOS toolbar
        actions_container = QtWidgets.QWidget(self)
        actions_container.setObjectName("ActionToolbar")
        actions_layout = QtWidgets.QHBoxLayout(actions_container)
        actions_layout.setContentsMargins(col_left, g, g, g)
        actions_layout.setSpacing(self._UI_GRID_PX)

        self.addr_edit = AddressLineEdit(self)
        self.addr_edit.setObjectName("PeerAddressEdit")
        self.addr_edit.setPlaceholderText("Peer .b32.i2p address")
        # Адрес — главный элемент панели действий
        self.addr_edit.setMinimumWidth(220)
        self.addr_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        self.connect_button.setObjectName("ConnectPeerButton")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect", self)
        self.disconnect_button.setObjectName("DisconnectPeerButton")

        self.more_toolbar_button = QtWidgets.QToolButton(self)
        self.more_toolbar_button.setObjectName("MoreActionsButton")
        self.more_toolbar_button.setText("⋯")
        self.more_toolbar_button.clicked.connect(self.on_more_actions_clicked)

        # Одна «толщина» со строкой статуса и кнопкой темы (_STATUS_ROW_HEIGHT_PX).
        actions_fixed_height = self._STATUS_ROW_HEIGHT_PX
        self.peer_lock_label = QtWidgets.QLabel(self)
        self.peer_lock_label.setObjectName("PeerLockIndicator")
        self.peer_lock_label.setFixedSize(22, actions_fixed_height)
        self.peer_lock_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.peer_lock_label.setScaledContents(False)
        self.addr_edit.setFixedHeight(actions_fixed_height)
        for btn in [
            self.connect_button,
            self.disconnect_button,
        ]:
            btn.setFixedHeight(actions_fixed_height)
        self.more_toolbar_button.setFixedHeight(actions_fixed_height)

        # Замок + адрес: половина сетки между ними (компромисс между 0 и полным _UI_GRID_PX).
        _peer_addr_strip = QtWidgets.QWidget(actions_container)
        _peer_addr_lay = QtWidgets.QHBoxLayout(_peer_addr_strip)
        _peer_addr_lay.setContentsMargins(0, 0, 0, 0)
        _peer_addr_lay.setSpacing(max(1, self._UI_GRID_PX // 2))
        _peer_addr_lay.addWidget(self.peer_lock_label, 0)
        _peer_addr_lay.addWidget(self.addr_edit, 1)
        actions_layout.addWidget(_peer_addr_strip, 1)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.disconnect_button)
        actions_layout.addWidget(self.more_toolbar_button)

        self.contacts_splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Horizontal, self
        )
        self.contacts_splitter.setHandleWidth(0)
        self.contacts_splitter.setChildrenCollapsible(False)

        self.contacts_sidebar = QtWidgets.QWidget(self.contacts_splitter)
        self.contacts_sidebar.setObjectName("ContactsSidebar")
        # 0: иначе QSplitter не даст ширину 0 при свёрнутой панели; минимум при открытии задаём в логике.
        self.contacts_sidebar.setMinimumWidth(0)
        sidebar_layout = QtWidgets.QVBoxLayout(self.contacts_sidebar)
        # Справа — как у гриппа ◀↔чат, чтобы зазор слева от кнопки не казался шире правого.
        sidebar_layout.setContentsMargins(
            g, g, self._CONTACTS_RESIZE_GRIP_WIDTH_PX, g
        )
        sidebar_layout.setSpacing(self._UI_GRID_PX)
        contacts_title = QtWidgets.QLabel("Saved peers", self.contacts_sidebar)
        contacts_title.setObjectName("ContactsSidebarTitle")
        contacts_title.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        contacts_title.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.contacts_list = QtWidgets.QListWidget(self.contacts_sidebar)
        self.contacts_list.setObjectName("ContactsList")
        self.contacts_list.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.contacts_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.contacts_list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        sidebar_layout.addWidget(contacts_title)
        sidebar_layout.addWidget(self.contacts_list, 1)

        self.contacts_right_pack = QtWidgets.QWidget(self.contacts_splitter)
        self.right_chat_column = QtWidgets.QWidget(self.contacts_right_pack)
        right_column_layout = QtWidgets.QVBoxLayout(self.right_chat_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(self._UI_GRID_PX)
        right_column_layout.addWidget(chat_surface, 1)
        right_column_layout.addWidget(input_container)
        right_column_layout.addWidget(actions_container)

        right_pack_layout = QtWidgets.QHBoxLayout(self.contacts_right_pack)
        right_pack_layout.setContentsMargins(self._CONTACTS_STRIP_EDGE_COLLAPSED_PX, 0, 0, 0)
        right_pack_layout.setSpacing(0)

        self.contacts_toggle_btn = QtWidgets.QPushButton(self.contacts_right_pack)
        self.contacts_toggle_btn.setObjectName("ContactsSidebarToggle")
        self.contacts_toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.contacts_toggle_btn.setFlat(True)
        self.contacts_toggle_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.contacts_toggle_btn.setFixedWidth(self._CONTACTS_TOGGLE_BTN_WIDTH_PX)
        self.contacts_toggle_btn.setMinimumHeight(self._STATUS_ROW_HEIGHT_PX)
        self.contacts_toggle_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.contacts_toggle_btn.setText("◀")
        self.contacts_toggle_btn.setToolTip("Show or hide saved peers")
        self.contacts_toggle_btn.clicked.connect(self._toggle_contacts_sidebar)

        # Грипп между кнопкой и чатом (узкая полоса).
        self._contacts_sidebar_resize_grip = _ContactsSidebarResizeGrip(
            self.contacts_splitter,
            1,
            self.contacts_right_pack,
            host=self,
            strip_width_px=self._CONTACTS_RESIZE_GRIP_WIDTH_PX,
        )
        right_pack_layout.addWidget(self.contacts_toggle_btn)
        right_pack_layout.addWidget(self._contacts_sidebar_resize_grip)
        right_pack_layout.addWidget(self.right_chat_column, 1)

        self.contacts_splitter.addWidget(self.contacts_sidebar)
        self.contacts_splitter.addWidget(self.contacts_right_pack)
        self.contacts_splitter.setStretchFactor(0, 0)
        self.contacts_splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.status_row)
        main_layout.addWidget(self.contacts_splitter, 1)

        # системный трей/док‑иконка для показа нативных уведомлений от Qt
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation
            )
        self.tray_icon.setIcon(icon)
        self._update_unread_chrome()
        self.tray_icon.show()

        _app = QtWidgets.QApplication.instance()
        if _app is not None:
            _app.applicationStateChanged.connect(
                self._on_app_state_changed_clear_unread
            )

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
        self.input_edit.imagePasteReady.connect(self.on_clipboard_image_ready)
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.addr_edit.textChanged.connect(lambda _t: self._refresh_connection_buttons())
        self.addr_edit.textChanged.connect(lambda _t: self._sync_contacts_list_selection())
        self.addr_edit.editingFinished.connect(self._on_addr_editing_finished_for_drafts)
        self.input_edit.textChanged.connect(self._on_compose_text_changed)
        self.more_actions_popup.add_action("Load profile (.dat)", self.on_load_profile_clicked)
        self.more_actions_popup.add_action("Send picture", self.on_send_pic_clicked)
        self.more_actions_popup.add_action("Send file", self.on_send_file_clicked)
        self.more_actions_popup.add_separator()
        self.more_actions_popup.add_action("Lock to peer", self.on_lock_peer_clicked)
        self.more_actions_popup.add_action("Forget pinned peer key", self.on_forget_pinned_peer_key_clicked)
        self.more_actions_popup.add_action("Copy my address", self.on_copy_my_addr_clicked)
        self.more_actions_popup.add_separator()
        self._history_enabled = load_history_enabled()
        self._history_toggle_btn = self.more_actions_popup.add_action(
            self._history_toggle_label(), self._on_toggle_history_clicked,
        )
        self.more_actions_popup.add_action("Clear history", self._on_clear_history_clicked)
        self.more_actions_popup.add_separator()
        self._notify_sound_enabled = load_notify_sound_enabled()
        self._notify_hide_body = load_notify_hide_body()
        self._notify_quiet_mode = load_notify_quiet_mode()
        self._notify_sound_toggle_btn = self.more_actions_popup.add_action(
            self._notify_sound_toggle_label(),
            self._on_toggle_notify_sound_clicked,
        )
        self._notify_sound_toggle_btn.setToolTip(
            "When off, notification sounds are never played (custom sound path is kept)."
        )
        self._notify_hide_body_toggle_btn = self.more_actions_popup.add_action(
            self._notify_hide_body_toggle_label(),
            self._on_toggle_notify_hide_body_clicked,
        )
        self._notify_hide_body_toggle_btn.setToolTip(
            "When on, the tray message hides the message text; the title may still name the peer."
        )
        self._notify_quiet_toggle_btn = self.more_actions_popup.add_action(
            self._notify_quiet_toggle_label(),
            self._on_toggle_notify_quiet_clicked,
        )
        self._notify_quiet_toggle_btn.setToolTip(
            "While the app window is focused, suppress tray toasts and sounds (other chats included)."
        )
        self.chat_view.cancelTransferRequested.connect(self.on_cancel_transfer)
        self.chat_view.imageOpenRequested.connect(self.on_image_open_requested)
        self.chat_view.replyRequested.connect(self._on_reply_requested)

        # ядро
        self.core = self._create_core(self.profile)
        self._load_compose_drafts_from_disk()
        self._load_contacts_book()
        self._refresh_contacts_list()
        self._apply_startup_peer_from_book()
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self._apply_theme(self.theme_id, persist=False)
        self._apply_contacts_sidebar_startup_state()
        self._sync_contacts_right_pack_left_margin()
        self._update_peer_lock_indicator()
        self.refresh_status_label()
        self._refresh_connection_buttons()
        QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)

    def _load_contacts_book(self) -> None:
        self._contact_book = load_book(_contacts_file_path(self.profile))

    def _save_contacts_book(self) -> None:
        save_book(_contacts_file_path(self.profile), self._contact_book)

    def _stop_contacts_sidebar_animation(self) -> None:
        if self._contacts_sidebar_anim is None:
            return
        self._contacts_sidebar_anim.stop()
        self._contacts_sidebar_anim.deleteLater()
        self._contacts_sidebar_anim = None

    def _balance_contacts_splitter_initial(self) -> None:
        total = max(400, self.contacts_splitter.width() or self.width() or 900)
        if self._contacts_sidebar_collapsed or not self.contacts_sidebar.isVisible():
            self.contacts_splitter.setSizes([0, total])
            self._sync_contacts_right_pack_left_margin()
            return
        avail = max(0, total - self._SPLITTER_RIGHT_MIN_PX)
        sw = min(
            max(self._CONTACTS_SIDEBAR_MIN_OPEN_PX, int(self._contacts_sidebar_width_saved)),
            avail,
        )
        self.contacts_splitter.setSizes([sw, total - sw])
        self._sync_contacts_right_pack_left_margin()

    def _sync_contacts_right_pack_left_margin(self) -> None:
        lay = self.contacts_right_pack.layout()
        if not isinstance(lay, QtWidgets.QHBoxLayout):
            return
        left = (
            self._CONTACTS_STRIP_EDGE_COLLAPSED_PX
            if self._contacts_sidebar_collapsed
            else self._CONTACTS_STRIP_EDGE_EXPANDED_PX
        )
        m = lay.contentsMargins()
        if m.left() == left:
            return
        lay.setContentsMargins(left, m.top(), m.right(), m.bottom())

    def _apply_startup_peer_from_book(self) -> None:
        if self.core.stored_peer:
            return
        lap = self._contact_book.last_active_peer
        if lap and not self.addr_edit.text().strip():
            self.addr_edit.setText(lap)

    def _apply_contacts_sidebar_startup_state(self) -> None:
        # stored_peer в ядре выставляется только в async init; до этого читаем .dat синхронно.
        locked = bool(
            self.core.stored_peer or peek_persisted_stored_peer(self.profile)
        )
        if locked:
            self._set_contacts_sidebar_collapsed(True, animated=False)
        else:
            self._set_contacts_sidebar_collapsed(False, animated=False)

    def _set_contacts_sidebar_collapsed(self, collapsed: bool, *, animated: bool) -> None:
        self._stop_contacts_sidebar_animation()
        sz = self.contacts_splitter.sizes()
        tw = self.contacts_splitter.width()
        total = tw if tw > 0 else max(400, sum(sz) if sz else 900)
        rmin = self._SPLITTER_RIGHT_MIN_PX

        if collapsed:
            sw0 = int(sz[0]) if sz else 0
            if sw0 <= 0:
                self._contacts_sidebar_collapsed = True
                self.contacts_toggle_btn.setText("▶")
                self.contacts_sidebar.hide()
                tot = max(rmin, self.contacts_splitter.width())
                self.contacts_splitter.setSizes([0, tot])
                self._sync_contacts_right_pack_left_margin()
                return
            self._contacts_sidebar_width_saved = max(self._CONTACTS_SIDEBAR_MIN_OPEN_PX, sw0)
            self._contacts_sidebar_collapsed = True
            self.contacts_toggle_btn.setText("▶")

            def apply_collapse(sw_live: object) -> None:
                sw_i = int(float(sw_live))
                self.contacts_splitter.setSizes(
                    [sw_i, max(rmin, total - sw_i)]
                )

            def finish_collapse() -> None:
                self.contacts_sidebar.hide()
                tot = max(rmin, self.contacts_splitter.width())
                self.contacts_splitter.setSizes([0, tot])
                self._contacts_sidebar_anim = None
                self._sync_contacts_right_pack_left_margin()
                QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)

            if not animated:
                self.contacts_sidebar.hide()
                tot = max(rmin, self.contacts_splitter.width())
                self.contacts_splitter.setSizes([0, tot])
                self._sync_contacts_right_pack_left_margin()
                QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)
                return
            self._sync_contacts_right_pack_left_margin()
            anim_c = QtCore.QVariantAnimation(self)
            anim_c.setDuration(self._CONTACTS_SIDEBAR_ANIM_MS)
            anim_c.setStartValue(float(sw0))
            anim_c.setEndValue(0.0)
            anim_c.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
            anim_c.valueChanged.connect(apply_collapse)
            anim_c.finished.connect(finish_collapse)
            self._contacts_sidebar_anim = anim_c
            anim_c.start()
            return

        avail = max(0, total - rmin)
        sw1 = min(
            max(self._CONTACTS_SIDEBAR_MIN_OPEN_PX, int(self._contacts_sidebar_width_saved)),
            avail,
        )
        self._contacts_sidebar_collapsed = False
        self.contacts_toggle_btn.setText("◀")
        self.contacts_sidebar.show()
        self._sync_contacts_right_pack_left_margin()

        def apply_expand(sw_live: object) -> None:
            sw_i = int(float(sw_live))
            self.contacts_splitter.setSizes([sw_i, max(rmin, total - sw_i)])

        def finish_expand() -> None:
            self.contacts_splitter.setSizes([sw1, max(rmin, total - sw1)])
            self._contacts_sidebar_anim = None
            self._sync_contacts_right_pack_left_margin()
            QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)

        if not animated:
            self.contacts_splitter.setSizes([sw1, max(rmin, total - sw1)])
            self._sync_contacts_right_pack_left_margin()
            QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)
            return
        anim_e = QtCore.QVariantAnimation(self)
        anim_e.setDuration(self._CONTACTS_SIDEBAR_ANIM_MS)
        anim_e.setStartValue(0.0)
        anim_e.setEndValue(float(sw1))
        anim_e.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        anim_e.valueChanged.connect(apply_expand)
        anim_e.finished.connect(finish_expand)
        self._contacts_sidebar_anim = anim_e
        apply_expand(0)
        anim_e.start()

    def _toggle_contacts_sidebar(self) -> None:
        self._set_contacts_sidebar_collapsed(
            not self._contacts_sidebar_collapsed, animated=True
        )

    def _expand_contacts_sidebar_from_drag(self) -> None:
        if not self._contacts_sidebar_collapsed:
            return
        self._contacts_sidebar_collapsed = False
        self.contacts_toggle_btn.setText("◀")
        self.contacts_sidebar.show()
        self._sync_contacts_right_pack_left_margin()

    def _refresh_contacts_list(self) -> None:
        self.contacts_list.clear()
        for rec in self._contact_book.contacts:
            item = QtWidgets.QListWidgetItem()
            row = ContactRowWidget(rec)
            row.activate.connect(self._on_contact_row_activated)
            self.contacts_list.addItem(item)
            self.contacts_list.setItemWidget(item, row)
            hint = row.sizeHint()
            item.setSizeHint(QtCore.QSize(hint.width(), max(56, hint.height())))
        self._sync_contacts_list_selection()

    def _peer_key_for_contact_selection(self) -> Optional[str]:
        raw = (
            (self.core.stored_peer or "").strip()
            or (self.core.current_peer_addr or "").strip()
            or self.addr_edit.text().strip()
        )
        if not raw:
            return None
        return normalize_peer_addr(raw)

    def _sync_contacts_list_selection(self) -> None:
        key = self._peer_key_for_contact_selection()
        if not key:
            self.contacts_list.clearSelection()
            return
        norm = normalize_peer_address(key)
        target = norm or key
        for i in range(self.contacts_list.count()):
            it = self.contacts_list.item(i)
            w = self.contacts_list.itemWidget(it)
            if isinstance(w, ContactRowWidget) and normalize_peer_addr(
                w.contact_addr
            ) == normalize_peer_addr(target):
                self.contacts_list.setCurrentRow(i)
                return
        self.contacts_list.clearSelection()

    def _on_contact_row_activated(self, addr: str) -> None:
        norm = normalize_peer_address(addr)
        if not norm:
            return
        stored = normalize_peer_address(self.core.stored_peer or "")
        if stored and norm != stored:
            QtWidgets.QMessageBox.information(
                self,
                "Saved peers",
                "Профиль закреплён за другим пиром. Смена контакта из списка недоступна, "
                "пока действует Lock to peer.",
            )
            return
        self.addr_edit.setText(norm)
        changed = False
        if remember_peer(self._contact_book, norm):
            changed = True
        if set_last_active_peer(self._contact_book, norm):
            changed = True
        if changed:
            self._save_contacts_book()
        self._refresh_contacts_list()
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self.refresh_status_label()
        self._refresh_connection_buttons()

    def _update_peer_lock_indicator(self) -> None:
        # stored_peer в ядре появляется после async init_session; до этого читаем .dat (как сайдбар при старте).
        locked = bool(
            self.core.stored_peer or peek_persisted_stored_peer(self.profile)
        )
        light = self.theme_id == "ligth"
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = _peer_lock_indicator_pixmap(locked=locked, light_theme=light, dpr=dpr)
        self.peer_lock_label.setPixmap(pm)
        self.peer_lock_label.setToolTip(
            "Профиль закреплён за пиром (Lock to peer)"
            if locked
            else "Профиль не закреплён: можно выбрать любой контакт"
        )

    def _peer_target_available(self) -> bool:
        return bool(self.addr_edit.text().strip()) or bool(self.core.stored_peer)

    def _refresh_connection_buttons(self) -> None:
        """Connect — когда сеть уже Pending/Visible, есть адрес и нет сессии; Disconnect — при активной сессии."""
        connected = self.core.conn is not None
        busy = self.core.is_outbound_connect_busy()
        has_target = self._peer_target_available()
        # Исходящий I2P-connect имеет смысл только после local_ok (в UI — pending) или visible.
        network_ready = self.core.network_status in ("local_ok", "visible")
        can_connect = (
            (not connected) and (not busy) and has_target and network_ready
        )
        self.connect_button.setEnabled(can_connect)
        if connected:
            c_tip = "Already connected — use Disconnect to end the session."
        elif busy:
            c_tip = "Connecting… please wait."
        elif not has_target:
            c_tip = "Enter a peer .b32.i2p address (or lock profile to a stored peer)."
        elif not network_ready:
            c_tip = (
                "Wait until status shows Pending or Visible — I2P session is still starting."
            )
        else:
            delivery_state = str(self.core.get_delivery_telemetry().get("state", "unknown"))
            if delivery_state == "offline-ready":
                c_tip = (
                    "Optional: Connect starts a live chat. Send can already queue offline via BlindBox."
                )
            elif delivery_state == "await-live-root":
                c_tip = (
                    "Required once: Connect a live secure session to initialize BlindBox root."
                )
            else:
                c_tip = ""
        self.connect_button.setToolTip(c_tip)
        can_disconnect = connected
        self.disconnect_button.setEnabled(can_disconnect)
        self.disconnect_button.setToolTip(
            "" if can_disconnect else "No active connection."
        )
        self._refresh_send_controls()

    def _refresh_send_controls(self) -> None:
        delivery = self.core.get_delivery_telemetry()
        state = str(delivery.get("state", "unknown"))
        short_route, route_tip = _delivery_status_bar_and_tooltip(state)
        if state == "offline-ready" and not bool(delivery.get("secure_live")):
            self.send_button.setText("Send\noffline")
        else:
            self.send_button.setText("Send")
        self.send_button.setToolTip(route_tip)
        self.input_edit.setToolTip(
            "Current mode: " + short_route + ". " + route_tip
        )

    def _append_item(self, item: ChatItem) -> None:
        """Добавить элемент в модель и прокрутить к нему."""
        self.chat_model.add_item(item)
        row = self.chat_model.rowCount() - 1
        if row >= 0:
            index = self.chat_model.index(row, 0)
            self.chat_view.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtBottom)
        if not self._chat_search_sync_suppressed:
            self._sync_chat_search_after_model_change()

    def _schedule_chat_search_rebuild(self, _t: str = "") -> None:
        self._chat_search_debounce.start()

    def _rebuild_chat_search_matches(self) -> None:
        q = self._chat_search_edit.text().strip().casefold()
        self._chat_search_list.clear()
        self._chat_search_match_rows = []
        self._chat_search_cur = -1
        if not q:
            self._chat_search_list.hide()
            self._update_chat_search_chrome()
            return
        n = self.chat_model.rowCount()
        for row in range(n):
            it = self.chat_model.item_at(row)
            if it is None:
                continue
            blob = f"{it.timestamp} {it.sender} {it.text}".casefold()
            if q in blob:
                self._chat_search_match_rows.append(row)
                snippet = (it.text or "").replace("\n", " ")
                if len(snippet) > 100:
                    snippet = snippet[:99] + "…"
                head = (
                    f"[{it.timestamp}] {it.sender}: "
                    if (it.timestamp or it.sender)
                    else ""
                )
                lw_it = QtWidgets.QListWidgetItem(head + snippet)
                lw_it.setData(QtCore.Qt.ItemDataRole.UserRole, row)
                self._chat_search_list.addItem(lw_it)
        if self._chat_search_match_rows:
            self._chat_search_list.show()
        else:
            self._chat_search_list.hide()
        self._update_chat_search_chrome()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if (
            obj is getattr(self, "_chat_search_field_wrap", None)
            and event.type() == QtCore.QEvent.Type.Resize
        ):
            self._layout_chat_search_status_overlay()
        return super().eventFilter(obj, event)

    def _chat_search_lineedit_left_pad_px(self) -> int:
        return max(4, self._UI_GRID_PX // 2 + 2)

    def _layout_chat_search_status_overlay(self) -> None:
        edit = getattr(self, "_chat_search_edit", None)
        lbl = getattr(self, "_chat_search_status_label", None)
        wrap = getattr(self, "_chat_search_field_wrap", None)
        if edit is None or lbl is None or wrap is None:
            return
        pad_l = self._chat_search_lineedit_left_pad_px()
        if not lbl.isVisible() or not lbl.text():
            edit.setTextMargins(pad_l, 0, 0, 0)
            return
        lbl.adjustSize()
        pad_r = lbl.width() + 12
        edit.setTextMargins(pad_l, 0, pad_r, 0)
        eg = edit.geometry()
        x = eg.x() + eg.width() - lbl.width() - 8
        y = eg.y() + max(0, (eg.height() - lbl.height()) // 2)
        lbl.move(x, y)
        lbl.raise_()

    def _update_chat_search_chrome(self) -> None:
        edit = self._chat_search_edit
        lbl = self._chat_search_status_label
        total = len(self._chat_search_match_rows)
        if not edit.text().strip():
            lbl.clear()
            lbl.hide()
            edit.setTextMargins(self._chat_search_lineedit_left_pad_px(), 0, 0, 0)
            return
        if total == 0:
            lbl.setText("No matches")
        elif self._chat_search_cur >= 0:
            lbl.setText(f"{self._chat_search_cur + 1}/{total}")
        else:
            lbl.setText(f"{total} match(es)")
        lbl.show()
        self._layout_chat_search_status_overlay()
        QtCore.QTimer.singleShot(0, self._layout_chat_search_status_overlay)

    def _highlight_chat_search_list_selection(self) -> None:
        if not (0 <= self._chat_search_cur < self._chat_search_list.count()):
            return
        it = self._chat_search_list.item(self._chat_search_cur)
        if it is not None:
            self._chat_search_list.setCurrentItem(it)

    def _step_chat_search(self, delta: int) -> None:
        rows = self._chat_search_match_rows
        if not rows:
            return
        if self._chat_search_cur < 0:
            self._chat_search_cur = 0 if delta >= 0 else len(rows) - 1
        else:
            self._chat_search_cur = (self._chat_search_cur + delta) % len(rows)
        r = rows[self._chat_search_cur]
        self._scroll_chat_to_row(r)
        self._update_chat_search_chrome()
        self._highlight_chat_search_list_selection()

    def _on_chat_search_item_clicked(
        self, item: Optional[QtWidgets.QListWidgetItem] = None
    ) -> None:
        if item is None:
            return
        row = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if row is None:
            return
        try:
            r = int(row)
        except (TypeError, ValueError):
            return
        if r in self._chat_search_match_rows:
            self._chat_search_cur = self._chat_search_match_rows.index(r)
        self._scroll_chat_to_row(r)
        self._update_chat_search_chrome()

    def _scroll_chat_to_row(self, row: int) -> None:
        if row < 0 or row >= self.chat_model.rowCount():
            return
        idx = self.chat_model.index(row, 0)
        self.chat_view.scrollTo(
            idx, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter
        )
        self.chat_view.setCurrentIndex(idx)

    def _sync_chat_search_after_model_change(self) -> None:
        if self._chat_search_edit.text().strip():
            self._rebuild_chat_search_matches()

    # ----- callbacks из ядра -----

    @QtCore.pyqtSlot(str)
    def handle_status(self, status: str) -> None:
        self._last_status = status
        self.refresh_status_label()
        self._refresh_connection_buttons()

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

        if kind in ("me", "peer") and self._history_enabled:
            ts_iso = msg.timestamp.isoformat()
            self._history_entries.append(
                HistoryEntry(kind=kind, text=text, ts=ts_iso)
            )
            max_messages = load_history_max_messages()
            if len(self._history_entries) > max_messages:
                self._history_entries = self._history_entries[-max_messages:]
            self._history_dirty = True

        if kind == "success" and "Secure channel" in text:
            self._try_load_history()

        if kind == "disconnect":
            self._save_history_if_needed()
            self._history_loaded_for_peer = None
            self._history_entries = []
            self._history_dirty = False
            self._history_flush_timer.stop()

        self._append_item(ChatItem(kind=kind, timestamp=ts, sender=sender, text=text))
        self.refresh_status_label()
        self._refresh_connection_buttons()
        if kind == "peer" and msg.source_peer:
            sp = normalize_peer_address(msg.source_peer)
            if sp:
                book_changed = False
                if remember_peer(self._contact_book, sp):
                    book_changed = True
                ts_iso = msg.timestamp.isoformat()
                preview = (text or "").replace("\n", " ")
                if touch_peer_message_meta(self._contact_book, sp, preview, ts_iso):
                    book_changed = True
                if book_changed:
                    self._save_contacts_book()
                    self._refresh_contacts_list()
            bump_unread_for_incoming_peer_message(
                self._unread_by_peer,
                active_key=self._compose_peer_key_from_ui(),
                msg_peer_key=normalize_peer_addr(msg.source_peer),
                chat_is_foreground=self._peer_chat_is_foreground(),
            )
            self._update_unread_chrome()

    def _peer_chat_is_foreground(self) -> bool:
        """True when the user is likely looking at the active chat (no unread bump for same peer)."""
        app = QtWidgets.QApplication.instance()
        is_app_active = (
            app is not None
            and app.applicationState()
            == QtCore.Qt.ApplicationState.ApplicationActive
        )
        is_window_active = self.isActiveWindow() and not self.isMinimized()
        return bool(is_app_active and is_window_active)

    def _update_unread_chrome(self) -> None:
        n = total_unread(self._unread_by_peer)
        suffix = f" ({n})" if n else ""
        self.setWindowTitle(self._window_title_base + suffix)
        if self.tray_icon is not None:
            if n:
                self.tray_icon.setToolTip(f"{self._window_title_base} — {n} unread")
            else:
                self.tray_icon.setToolTip(self._window_title_base)

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
            notify_kind = "peer"
            preview = (msg.text or "").replace("\n", " ")
            title = "New message"
            peer_addr = msg.source_peer or self.core.current_peer_addr
            if peer_addr:
                clean_peer = peer_addr.replace(".b32.i2p", "")
                if len(clean_peer) > 12:
                    clean_peer = f"{clean_peer[:6]}..{clean_peer[-6:]}"
                title = f"New message from {clean_peer}"
        elif msg.kind == "connect":
            notify_kind = "connect"
            peer = (msg.text or "").strip()
            clean_peer = peer.replace(".b32.i2p", "") if peer else "peer"
            if len(clean_peer) > 12:
                clean_peer = f"{clean_peer[:6]}..{clean_peer[-6:]}"
            title = "Incoming connection"
            preview = f"{clean_peer}.b32.i2p connected" if peer else "Peer connected"
        else:
            return

        # Peer: не спамим, если фокус на этом же диалоге; иначе — тост даже при активном окне.
        # connect: как раньше — только если окно не в фокусе.
        app = QtWidgets.QApplication.instance()
        is_app_active = (
            app is not None
            and app.applicationState()
            == QtCore.Qt.ApplicationState.ApplicationActive
        )
        is_window_active = self.isActiveWindow() and not self.isMinimized()

        if msg.kind == "peer":
            msg_key = (
                normalize_peer_addr(msg.source_peer) if msg.source_peer else None
            )
            active = self._compose_peer_key_from_ui()
            same_chat = (
                msg_key is not None
                and active is not None
                and msg_key == active
            )
            if is_app_active and is_window_active and same_chat:
                return
        elif msg.kind == "connect":
            if is_app_active and is_window_active:
                return

        body = notification_body_for_display(
            kind=notify_kind,
            preview=preview,
            hide_body=self._notify_hide_body,
        )
        if should_show_tray_message(
            quiet_mode=self._notify_quiet_mode,
            is_app_active=is_app_active,
            is_window_active=is_window_active,
        ):
            if self.tray_icon is not None:
                self.tray_icon.showMessage(
                    title,
                    body,
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )

        if should_play_notification_sound(
            sound_enabled=self._notify_sound_enabled,
            quiet_mode=self._notify_quiet_mode,
            is_app_active=is_app_active,
            is_window_active=is_window_active,
        ):
            self._play_notification_sound()

    @QtCore.pyqtSlot(str)
    def handle_system(self, text: str) -> None:
        self._append_item(ChatItem(kind="system", timestamp="", sender="SYSTEM", text=text))
        self.refresh_status_label()
        self._refresh_connection_buttons()

    @QtCore.pyqtSlot(str)
    def handle_error(self, text: str) -> None:
        now = time.monotonic()
        # Guard against repeated identical transport errors flooding the chat.
        if text == self._last_error_text and (now - self._last_error_ts) < 6.0:
            self.refresh_status_label()
            self._refresh_connection_buttons()
            return
        self._last_error_text = text
        self._last_error_ts = now
        self._append_item(ChatItem(kind="error", timestamp="", sender="ERROR", text=text))
        self.refresh_status_label()
        self._focus_status_bar(duration_ms=4600, event_text=f"Error: {text}")
        self._refresh_connection_buttons()

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
                        saved_fp: Optional[str] = None
                        abs_fn = os.path.abspath(info.filename)
                        if os.path.isfile(abs_fn):
                            saved_fp = abs_fn
                        disp_name = os.path.basename(abs_fn)
                        self.chat_model.update_item(
                            self._transfer_row,
                            ChatItem(
                                kind="success",
                                timestamp="",
                                sender="FILE",
                                text=(
                                    f"✔ File received: {disp_name} ({info.size:,} bytes). "
                                    "Open downloads folder"
                                ),
                                open_folder_path=downloads_dir,
                                saved_file_path=saved_fp,
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

    async def ask_incoming_file_accept(self, filename: str, size: int) -> bool:
        """Асинхронный запрос подтверждения входящего файла для core."""
        loop = asyncio.get_running_loop()
        decision: asyncio.Future[bool] = loop.create_future()

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Incoming file")
        box.setText(f"Accept incoming file?\n\n{filename} ({size} bytes)")
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Yes)
        box.setModal(True)
        self._active_file_offer_boxes.append(box)

        def _finished(result: int) -> None:
            try:
                self._active_file_offer_boxes.remove(box)
            except ValueError:
                pass
            if not decision.done():
                decision.set_result(
                    result == int(QtWidgets.QMessageBox.StandardButton.Yes)
                )
            box.deleteLater()

        box.finished.connect(_finished)
        box.open()
        return await decision

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

        if not is_from_me and self.core.current_peer_addr:
            bump_unread_for_incoming_peer_message(
                self._unread_by_peer,
                active_key=self._compose_peer_key_from_ui(),
                msg_peer_key=normalize_peer_addr(self.core.current_peer_addr),
                chat_is_foreground=self._peer_chat_is_foreground(),
            )
            self._update_unread_chrome()

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
            n = normalize_peer_address(peer)
            if n:
                changed = False
                if remember_peer(self._contact_book, n):
                    changed = True
                if set_last_active_peer(self._contact_book, n):
                    changed = True
                if changed:
                    self._save_contacts_book()
                self._refresh_contacts_list()
        else:
            self._sync_contacts_list_selection()
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self.refresh_status_label()
        self._refresh_connection_buttons()

    def _create_core(self, profile: Optional[str]) -> I2PChatCore:
        core = I2PChatCore(
            profile=profile or "default",
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=self.handle_file_event,
            on_file_offer=self.ask_incoming_file_accept,
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
            canberra_path = shutil.which("canberra-gtk-play")
            if canberra_path:
                linux_cmds.append([canberra_path, "-i", "message-new-instant"])
            if self.notify_sound_path and os.path.isfile(self.notify_sound_path):
                paplay_path = shutil.which("paplay")
                if paplay_path:
                    linux_cmds.append([paplay_path, self.notify_sound_path])
                aplay_path = shutil.which("aplay")
                if aplay_path:
                    linux_cmds.append([aplay_path, self.notify_sound_path])
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
                icon_px = self._THEME_SWITCH_ICON_PX
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
            str(self.theme["window_stylesheet"])
            % {
                "status_font_px": self._status_font_px,
                "status_row_height_px": self._STATUS_ROW_HEIGHT_PX,
                "contacts_toggle_btn_width_px": self._CONTACTS_TOGGLE_BTN_WIDTH_PX,
                "ui_grid_px": self._UI_GRID_PX,
            }
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
        self.more_actions_popup.apply_theme(self.theme_id)
        self.chat_view.set_theme(self.theme_id)
        self.input_edit.set_theme(self.theme_id)
        self.addr_edit.set_theme(self.theme_id)
        self._update_peer_lock_indicator()
        self._refresh_connection_buttons()

    @QtCore.pyqtSlot()
    def on_theme_switch_clicked(self) -> None:
        next_theme = "night" if self.theme_id == "ligth" else "ligth"
        self._apply_theme(next_theme, persist=True)

    @QtCore.pyqtSlot()
    def _on_more_actions_popup_closed(self) -> None:
        # На Windows клик, закрывающий popup, может тут же повторно открыть его.
        # Небольшой "debounce" убирает ложный reopen тем же событием мыши.
        self._more_actions_suppress_until_ms = (
            int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 180
        )

    @QtCore.pyqtSlot()
    def on_more_actions_clicked(self) -> None:
        now_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch())
        if now_ms < self._more_actions_suppress_until_ms:
            return
        if self.more_actions_popup.isVisible():
            self.more_actions_popup.hide()
            return
        self.more_actions_popup.show_below(self.more_toolbar_button)

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
            "Warning: TOFU pins only the signing key.\n"
            "Identity is NOT OOB-verified yet.\n"
            "Verify fingerprint over an independent channel before trusting this peer.\n\n"
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

    def _set_status_text(self, line_text_full: str, line_text_compact: str) -> None:
        """Полная и компактная строка; в лейбле — по ширине окна (без всплывающей подсказки)."""
        self._status_line_text = line_text_full
        self._status_line_compact_text = line_text_compact
        self._update_status_label_visible_text()

    def _on_status_focus_timeout(self) -> None:
        self._status_event_text = ""
        self._status_force_expand_until_mono = 0.0
        self._update_status_label_visible_text()

    def _focus_status_bar(self, duration_ms: int = 2800, event_text: str = "") -> None:
        self._status_event_text = event_text.strip()
        self._status_force_expand_until_mono = time.monotonic() + max(1.2, duration_ms / 1000.0)
        self._status_focus_timer.start(max(1200, int(duration_ms)))
        self._update_status_label_visible_text()

    def _update_status_label_visible_text(self) -> None:
        w = self.status_label.width()
        now = time.monotonic()
        forced_expanded = now < self._status_force_expand_until_mono
        if self._status_event_text and forced_expanded:
            raw = self._status_event_text
        else:
            use_compact = (0 < w < self._STATUS_LABEL_COMPACT_PX) and (not forced_expanded)
            raw = self._status_line_compact_text if use_compact else self._status_line_text
        available = max(40, w - 14)
        elided = self.status_label.fontMetrics().elidedText(
            raw,
            QtCore.Qt.TextElideMode.ElideRight,
            available,
        )
        self.status_label.setText(elided)
        self.status_label.setToolTip("")

    def refresh_status_label(self) -> None:
        """Обновить строку статуса с учётом профиля и persist-режима."""
        status = _network_status_display(self._last_status)
        ack_drop_total = 0
        try:
            telemetry = self.core.get_ack_telemetry()
            ack_drop_total = int(sum(int(v) for v in telemetry.values()))
        except Exception:
            ack_drop_total = 0
        def _short_addr(addr: Optional[str]) -> str:
            if not addr:
                return "none"
            clean = addr.replace(".b32.i2p", "")
            if len(clean) > 12:
                clean = f"{clean[:6]}..{clean[-6:]}"
            return clean + ".b32.i2p"

        stored = self.core.stored_peer
        if stored and not self.addr_edit.text().strip():
            # stored уже содержит полный адрес (с суффиксом), используем как есть.
            self.addr_edit.setText(stored)

        link_state = "online" if self.core.conn else "offline"
        secure_state = "on" if self.core.handshake_complete else "off"
        blindbox_state = "off"
        blindbox_sync = "idle"
        blindbox_queue = "0"
        blindbox_epoch = "0"
        blindbox_hint = ""
        bb_enabled = False
        telemetry_ok = True
        try:
            bb = self.core.get_blindbox_telemetry()
            bb_profile = str(bb.get("privacy_profile", "high"))
            blindbox_epoch = str(int(bb.get("root_epoch", 0)))
            bb_enabled = bool(bb.get("enabled"))
            insecure_local_mode = bool(bb.get("insecure_local_mode"))
            bb_enabled_source = str(bb.get("enabled_source", "default"))
            bb_replicas_source = str(
                bb.get("blind_boxes_source") or bb.get("replicas_source", "none")
            )
            if bb.get("enabled"):
                if bb.get("ready"):
                    if bb.get("has_root_secret"):
                        blindbox_state = "ready"
                    else:
                        blindbox_state = "await-root"
                        blindbox_hint = "waiting for initial BlindBox root exchange over a secure live session"
                else:
                    blindbox_state = "on"
                    if not self.core.stored_peer:
                        blindbox_hint = "lock profile to a peer to activate BlindBox"
                    elif int(bb.get("blind_boxes", bb.get("replicas", 0))) <= 0:
                        blindbox_hint = (
                            "configure Blind Box servers via I2PCHAT_BLINDBOX_REPLICAS "
                            "or I2PCHAT_BLINDBOX_DEFAULT_REPLICAS/"
                            "I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE "
                            "(or unset I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS for release defaults)"
                        )
                    elif bb_replicas_source == "release-builtin":
                        blindbox_hint = "using release built-in Blind Box pair (i2p_chat_core.py)"
                    elif bb_replicas_source == "local-auto":
                        blindbox_hint = (
                            "using local Blind Box fallback (single-host, "
                            "I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1)"
                        )
                    else:
                        blindbox_hint = "initializing local BlindBox runtime"
                blindbox_sync = "poll" if bb.get("poller_running") else "idle"
                blindbox_queue = str(int(bb.get("send_index", 0)))
            else:
                if self.profile == "default":
                    blindbox_hint = "BlindBox is disabled in TRANSIENT mode"
                elif bb_enabled_source == "env-disabled":
                    blindbox_hint = "BlindBox is disabled by I2PCHAT_BLINDBOX_ENABLED=0"
                else:
                    blindbox_hint = "BlindBox disabled by runtime policy"
        except Exception:
            telemetry_ok = False
            bb_enabled = False
            insecure_local_mode = False
            blindbox_state = "off"
            blindbox_sync = "idle"
            blindbox_queue = "0"
            blindbox_hint = "BlindBox telemetry unavailable"
            bb_profile = "high"
            blindbox_epoch = "0"
        blindbox_bar, blindbox_detail = _blindbox_status_bar_and_tooltip(
            enabled=bb_enabled,
            state=blindbox_state,
            sync=blindbox_sync,
            queue=blindbox_queue,
            epoch=blindbox_epoch,
            privacy=bb_profile,
            hint=blindbox_hint,
            telemetry_ok=telemetry_ok,
            insecure_local=insecure_local_mode,
        )
        delivery = self.core.get_delivery_telemetry()
        delivery_state = str(delivery.get("state", "unknown"))
        delivery_bar, _ = _delivery_status_bar_and_tooltip(delivery_state)
        peer_for_status = self.core.current_peer_addr or stored
        current_peer_disp = _short_addr(peer_for_status)
        stored_disp = _short_addr(stored)
        ack_part = f"ACKdrop:{ack_drop_total}" if ack_drop_total > 0 else "ACKdrop:0"

        pres = build_status_presentation(
            network_status_raw=self._last_status,
            connected=bool(self.core.conn),
            handshake_complete=bool(self.core.handshake_complete),
            outbound_connect_busy=bool(self.core.is_outbound_connect_busy()),
            delivery_state=delivery_state,
            send_in_flight=bool(self._status_send_in_flight),
            profile_name=self.profile,
            is_transient_profile=self.profile == "default",
            peer_short=current_peer_disp,
            stored_short=stored_disp,
            link_state=link_state,
            secure_state=secure_state,
            delivery_bar=delivery_bar,
            blindbox_bar=blindbox_bar,
            blindbox_detail=blindbox_detail,
            ack_part=ack_part,
        )
        primary_full = pres.primary_full
        if ack_drop_total > 0:
            primary_full = f"{primary_full} | {ack_part}"
        self._set_status_text(primary_full, pres.primary_short)
        current_signature = (status, link_state, secure_state, delivery_state)
        if (
            self._status_focus_signature is not None
            and current_signature != self._status_focus_signature
        ):
            self._focus_status_bar(duration_ms=2800)
        self._status_focus_signature = current_signature

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_status_label_visible_text()

    # ----- обработчики UI -----

    @QtCore.pyqtSlot()
    def on_send_clicked(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        asyncio.create_task(self._send_text_ui_flow(text))

    async def _send_text_ui_flow(self, text: str) -> None:
        self._status_send_in_flight = True
        self.refresh_status_label()
        try:
            draft_key = self._compose_peer_key_from_ui()
            result = await self.core.send_text(text)
            if result.accepted:
                if draft_key:
                    self._compose_drafts.pop(draft_key, None)
                self.input_edit.clear()
                self._schedule_compose_drafts_persist()
            else:
                # Keep text in the input when blocked (especially await-live-root),
                # so user can press Connect and retry without retyping.
                self.input_edit.setPlainText(text)
                cursor = self.input_edit.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                self.input_edit.setTextCursor(cursor)
                self.input_edit.setFocus()
                if _should_start_auto_connect_retry(
                    reason=result.reason,
                    has_running_task=self._auto_retry_send_task is not None,
                    now_mono=time.monotonic(),
                    last_started_mono=self._last_auto_retry_started_at,
                ):
                    self._last_auto_retry_started_at = time.monotonic()
                    self._auto_retry_send_task = asyncio.create_task(
                        self._auto_connect_and_retry_send(text)
                    )
        finally:
            self._status_send_in_flight = False
            self.refresh_status_label()
            self._refresh_connection_buttons()

    async def _auto_connect_and_retry_send(self, text: str) -> None:
        """Single-click flow: try live connect, then retry send once."""
        try:
            addr = self.addr_edit.text().strip() or (self.core.stored_peer or "")
            if not addr:
                self.handle_system("Auto-connect failed, message kept in input.")
                return
            self.handle_system("Auto-connect started for this message...")
            if not self.core.conn and not self.core.is_outbound_connect_busy():
                await self.core.connect_to_peer(addr)
            deadline = time.monotonic() + 75.0
            while time.monotonic() < deadline:
                if self.core.conn is not None and self.core.handshake_complete:
                    break
                if (
                    self.core.conn is None
                    and not self.core.is_outbound_connect_busy()
                ):
                    self.handle_system("Auto-connect failed, message kept in input.")
                    return
                await asyncio.sleep(0.25)
            if self.core.conn is not None and self.core.handshake_complete:
                retry = await self.core.send_text(text)
                if retry.accepted:
                    dk = self._compose_peer_key_from_ui()
                    if dk:
                        self._compose_drafts.pop(dk, None)
                    self.input_edit.clear()
                    self._schedule_compose_drafts_persist()
                    self.handle_system("Auto-connect succeeded, message sent.")
                else:
                    self.input_edit.setPlainText(text)
                    cursor = self.input_edit.textCursor()
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                    self.input_edit.setTextCursor(cursor)
                    self.input_edit.setFocus()
                    self.handle_system("Auto-connect finished, but send is still blocked. Message kept.")
            else:
                self.handle_system("Auto-connect timed out, message kept in input.")
        finally:
            self._auto_retry_send_task = None
            self.refresh_status_label()
            self._refresh_connection_buttons()

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
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        asyncio.create_task(self.core.connect_to_peer(addr))
        QtCore.QTimer.singleShot(0, self._refresh_connection_buttons)

    @QtCore.pyqtSlot()
    def on_disconnect_clicked(self) -> None:
        self._save_history_if_needed()
        self._history_flush_timer.stop()
        self._history_loaded_for_peer = None
        self._history_entries = []
        self._history_dirty = False
        asyncio.create_task(self.core.disconnect())
        QtCore.QTimer.singleShot(0, self._refresh_connection_buttons)

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
        if not self.core.is_current_peer_verified_for_lock():
            QtWidgets.QMessageBox.warning(
                self,
                "Lock to peer",
                "Identity binding is not cryptographically verified yet.\n"
                "Complete secure handshake and verify peer fingerprint out-of-band first.",
            )
            return

        try:
            self.core.save_stored_peer(self.core.current_peer_addr)
            asyncio.create_task(self.core.ensure_blindbox_runtime_started())
            self.handle_system(
                f"Identity {self.profile} is now locked to this peer."
            )
            n = normalize_peer_address(self.core.current_peer_addr or "")
            if n:
                remember_peer(self._contact_book, n)
                set_last_active_peer(self._contact_book, n)
                self._save_contacts_book()
                self._refresh_contacts_list()
            self._set_contacts_sidebar_collapsed(True, animated=False)
            QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)
            self._update_peer_lock_indicator()
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

    def _current_history_peer(self) -> Optional[str]:
        return (
            self.core.current_peer_addr
            or self.core.stored_peer
            or self.addr_edit.text().strip()
            or None
        )

    def _compose_peer_key_from_ui(self) -> Optional[str]:
        peer = self._current_history_peer()
        if not peer:
            return None
        return normalize_peer_addr(peer)

    def _merge_active_input_into_compose_drafts(self) -> None:
        if self._compose_draft_active_key is not None:
            self._compose_drafts[self._compose_draft_active_key] = self.input_edit.toPlainText()

    def _load_compose_drafts_from_disk(self) -> None:
        self._compose_drafts = {}
        path = _compose_drafts_file_path(self.profile)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        raw = data.get("drafts")
        if not isinstance(raw, dict):
            return
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str):
                self._compose_drafts[k] = v

    def _flush_compose_drafts_to_disk(self) -> None:
        self._merge_active_input_into_compose_drafts()
        while len(self._compose_drafts) > COMPOSE_DRAFTS_MAX_KEYS:
            del self._compose_drafts[next(iter(self._compose_drafts))]
        try:
            atomic_write_json(
                _compose_drafts_file_path(self.profile),
                {"version": 1, "drafts": dict(self._compose_drafts)},
            )
        except Exception:
            logger.debug("failed to save compose drafts", exc_info=True)

    def _schedule_compose_drafts_persist(self) -> None:
        self._compose_drafts_save_timer.stop()
        self._compose_drafts_save_timer.start()

    @QtCore.pyqtSlot()
    def _on_compose_text_changed(self) -> None:
        if self._compose_draft_active_key is not None:
            self._compose_drafts[self._compose_draft_active_key] = self.input_edit.toPlainText()
        self._schedule_compose_drafts_persist()

    @QtCore.pyqtSlot()
    def _on_addr_editing_finished_for_drafts(self) -> None:
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())

    def _sync_compose_draft_to_peer_key(self, new_key: Optional[str]) -> None:
        if new_key == self._compose_draft_active_key:
            return
        active, text, out = apply_compose_draft_peer_switch(
            old_active_key=self._compose_draft_active_key,
            new_key=new_key,
            input_plain=self.input_edit.toPlainText(),
            drafts=self._compose_drafts,
        )
        self._compose_drafts = out
        self._compose_draft_active_key = active
        self.input_edit.blockSignals(True)
        self.input_edit.setPlainText(text)
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.input_edit.setTextCursor(cursor)
        self.input_edit.blockSignals(False)
        self._schedule_compose_drafts_persist()
        clear_unread_for_peer(self._unread_by_peer, active)
        self._update_unread_chrome()

    def _try_load_history(self) -> None:
        if not self._history_enabled:
            return
        peer = self._current_history_peer()
        if not peer or peer == self._history_loaded_for_peer:
            return
        identity_key = self.core.get_identity_key_bytes()
        if not identity_key:
            return
        entries = load_history(
            self.core.get_profiles_dir(), self.core.profile, peer, identity_key,
        )
        self._history_entries = list(entries)
        self._history_dirty = False
        if entries:
            self._chat_search_sync_suppressed = True
            try:
                self._append_item(ChatItem(
                    kind="system", timestamp="", sender="",
                    text=f"--- {len(entries)} message(s) from history ---",
                ))
                for e in entries:
                    if e.kind == "me":
                        sender = "Me"
                    elif e.kind == "peer":
                        sender = self.profile if self.profile != "default" else "Peer"
                    else:
                        continue
                    ts_display = ""
                    if e.ts:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(e.ts.replace("Z", "+00:00"))
                            ts_display = dt.strftime("%H:%M:%S")
                        except Exception:
                            ts_display = e.ts[:8]
                    self._append_item(
                        ChatItem(
                            kind=e.kind,
                            timestamp=ts_display,
                            sender=sender,
                            text=e.text,
                        )
                    )
                self._append_item(ChatItem(
                    kind="system", timestamp="", sender="",
                    text="--- end of history ---",
                ))
            finally:
                self._chat_search_sync_suppressed = False
            self._sync_chat_search_after_model_change()
        self._history_loaded_for_peer = peer
        self._history_flush_timer.start()
        clear_unread_for_peer(self._unread_by_peer, normalize_peer_addr(peer))
        self._update_unread_chrome()

    def _save_history_if_needed(self) -> None:
        if not self._history_enabled or not self._history_dirty:
            return
        peer = self._history_loaded_for_peer or self._current_history_peer()
        if not peer:
            return
        identity_key = self.core.get_identity_key_bytes()
        if not identity_key:
            return
        entries = self._history_entries[-load_history_max_messages():]
        if entries:
            try:
                save_history(
                    self.core.get_profiles_dir(),
                    self.core.profile,
                    peer,
                    entries,
                    identity_key,
                    max_messages=load_history_max_messages(),
                )
            except Exception as e:
                logger.warning("Failed to save chat history: %s", e, exc_info=True)
                if not self._history_save_error_reported:
                    self.handle_system(f"Warning: failed to save chat history: {e}")
                    self._history_save_error_reported = True
                # Keep dirty state to retry on next flush/disconnect.
                return
            self._history_save_error_reported = False
        self._history_dirty = False

    @QtCore.pyqtSlot()
    def _flush_history(self) -> None:
        if self._history_dirty:
            self._save_history_if_needed()

    def _history_toggle_label(self) -> str:
        return "Chat history: ON" if self._history_enabled else "Chat history: OFF"

    def _notify_sound_toggle_label(self) -> str:
        return (
            "Notification sound: ON"
            if self._notify_sound_enabled
            else "Notification sound: OFF"
        )

    def _notify_hide_body_toggle_label(self) -> str:
        return (
            "Hide message in notifications: ON"
            if self._notify_hide_body
            else "Hide message in notifications: OFF"
        )

    def _notify_quiet_toggle_label(self) -> str:
        return "Quiet mode (focused): ON" if self._notify_quiet_mode else "Quiet mode (focused): OFF"

    @QtCore.pyqtSlot()
    def _on_toggle_notify_sound_clicked(self) -> None:
        self._notify_sound_enabled = not self._notify_sound_enabled
        save_notify_sound_enabled(self._notify_sound_enabled)
        self._notify_sound_toggle_btn.setText(self._notify_sound_toggle_label())

    @QtCore.pyqtSlot()
    def _on_toggle_notify_hide_body_clicked(self) -> None:
        self._notify_hide_body = not self._notify_hide_body
        save_notify_hide_body(self._notify_hide_body)
        self._notify_hide_body_toggle_btn.setText(self._notify_hide_body_toggle_label())

    @QtCore.pyqtSlot()
    def _on_toggle_notify_quiet_clicked(self) -> None:
        self._notify_quiet_mode = not self._notify_quiet_mode
        save_notify_quiet_mode(self._notify_quiet_mode)
        self._notify_quiet_toggle_btn.setText(self._notify_quiet_toggle_label())

    @QtCore.pyqtSlot()
    def _on_toggle_history_clicked(self) -> None:
        self._history_enabled = not self._history_enabled
        save_history_enabled(self._history_enabled)
        self._history_toggle_btn.setText(self._history_toggle_label())
        if self._history_enabled:
            if self.core.conn is not None and self.core.handshake_complete:
                self._try_load_history()
            self.handle_system("Chat history saving enabled.")
        else:
            self._history_flush_timer.stop()
            self._history_entries = []
            self._history_dirty = False
            self._history_loaded_for_peer = None
            self.handle_system("Chat history saving disabled.")

    @QtCore.pyqtSlot()
    def _on_clear_history_clicked(self) -> None:
        peer = (
            self.core.current_peer_addr
            or self.core.stored_peer
            or self.addr_edit.text().strip()
        )
        if not peer:
            self.handle_system("No peer to clear history for.")
            return
        deleted = delete_history(self.core.get_profiles_dir(), self.core.profile, peer)
        if deleted:
            if peer == self._history_loaded_for_peer:
                self._history_entries = []
                self._history_dirty = False
            self.handle_system("History cleared for this peer.")
        else:
            self.handle_system("No saved history found for this peer.")

    @QtCore.pyqtSlot()
    def on_forget_pinned_peer_key_clicked(self) -> None:
        peer_addr = (
            self.addr_edit.text().strip()
            or (self.core.current_peer_addr or "").strip()
            or (self.core.stored_peer or "").strip()
        )
        if not peer_addr:
            QtWidgets.QMessageBox.warning(
                self,
                "Forget pinned peer key",
                "No peer address is available.\nEnter a peer address or connect first.",
            )
            return
        normalized = self.core._normalize_peer_addr(peer_addr)
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Forget pinned peer key",
            "Remove the pinned signing key for this peer?\n\n"
            f"{normalized}\n\n"
            "On the next secure connect, this peer will be trusted again via TOFU.\n"
            "Only continue if you expect the key change and have verified the fingerprint out-of-band.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.core.forget_pinned_peer_key(peer_addr)
        except Exception as e:  # pragma: no cover - GUI path
            self.handle_error(f"Failed to forget pinned peer key: {e}")
            return
        if removed:
            self.handle_system(f"Forgot pinned peer key for {normalized}.")
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Forget pinned peer key",
                f"No pinned key stored for:\n{normalized}",
            )

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
        source_base = os.path.splitext(os.path.basename(path))[0]
        if not is_valid_profile_name(source_base):
            QtWidgets.QMessageBox.warning(
                self,
                "Load .dat",
                "Invalid profile name in selected file.\n"
                "Allowed: a-z A-Z 0-9 . _ - (1..64 chars).",
            )
            return

        # Копируем выбранный .dat в папку профилей, чтобы ядро его увидело
        profiles_dir = get_profiles_dir()
        target_base = source_base
        dest_path = os.path.join(profiles_dir, f"{target_base}.dat")
        if os.path.abspath(path) != os.path.abspath(dest_path):
            try:
                target_base = import_profile_dat_atomic(path, profiles_dir, source_base)
                dest_path = os.path.join(profiles_dir, f"{target_base}.dat")
                if target_base != source_base:
                    QtWidgets.QMessageBox.information(
                        self,
                        "Load .dat",
                        f"Профиль '{source_base}' уже существует.\n"
                        f"Импортирован как '{target_base}'.",
                    )
            except Exception as e:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(
                    self,
                    "Load .dat",
                    f"Не удалось скопировать профиль:\n{e}",
                )
                return

        asyncio.create_task(self.switch_profile(target_base))

    async def switch_profile(self, profile: str) -> None:
        """Переключиться на другой профиль (.dat)."""
        profile = ensure_valid_profile_name(profile)
        self._flush_compose_drafts_to_disk()
        await self.core.shutdown()
        self.profile = profile
        clean_profile = self.profile.rstrip(" •")
        self._window_title_base = f"I2PChat @ {clean_profile}"
        self._unread_by_peer = {}
        self.core = self._create_core(self.profile)
        self._load_compose_drafts_from_disk()
        self._compose_draft_active_key = None
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self._update_unread_chrome()
        self.refresh_status_label()
        self._refresh_connection_buttons()
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
            "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)",
        )
        if not path:
            return
        asyncio.create_task(self.core.send_image(path))

    @QtCore.pyqtSlot(str)
    def on_clipboard_image_ready(self, path: str) -> None:
        """Вставка изображения из буфера или путь к локальному файлу из clipboard URLs."""
        ok, err, _ = validate_image(path)
        if not ok:
            if _is_path_within_directory(path, get_images_dir()) and os.path.basename(
                path
            ).startswith("paste_"):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self.handle_error(err or "Invalid image from clipboard")
            return
        asyncio.create_task(self.core.send_image(path))

    @QtCore.pyqtSlot(str)
    def _on_reply_requested(self, block: str) -> None:
        """Вставить цитату в поле ввода (конец черновика)."""
        cur = self.input_edit.toPlainText()
        sep = "\n\n" if cur.strip() else ""
        self.input_edit.setPlainText(f"{cur}{sep}{block}")
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.input_edit.setTextCursor(cursor)
        self.input_edit.setFocus()
        self._on_compose_text_changed()

    @QtCore.pyqtSlot(str)
    def on_image_open_requested(self, path: str) -> None:
        """Открыть изображение в системном просмотрщике."""
        if not _is_path_within_directory(path, get_images_dir()):
            self.handle_error(f"Refusing to open file outside images directory: {path}")
            return

        real_path = os.path.realpath(path)
        if not os.path.isfile(real_path):
            self.handle_error(f"Image not found: {path}")
            return

        url = QtCore.QUrl.fromLocalFile(real_path)
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
        QtCore.QTimer.singleShot(0, self._update_peer_lock_indicator)

    def _on_app_state_changed_clear_unread(
        self, state: QtCore.Qt.ApplicationState
    ) -> None:
        if state != QtCore.Qt.ApplicationState.ApplicationActive:
            return
        QtCore.QTimer.singleShot(0, self._clear_unread_if_active_chat_visible)

    def _clear_unread_if_active_chat_visible(self) -> None:
        if not self._peer_chat_is_foreground():
            return
        clear_unread_for_peer(self._unread_by_peer, self._compose_peer_key_from_ui())
        self._update_unread_chrome()

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowActivate:
            QtCore.QTimer.singleShot(0, self._clear_unread_if_active_chat_visible)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Останавливаем ядро и event loop при закрытии окна."""
        self._history_flush_timer.stop()
        self._compose_drafts_save_timer.stop()
        self._flush_compose_drafts_to_disk()
        self._save_history_if_needed()

        loop = asyncio.get_event_loop()

        async def _shutdown() -> None:
            try:
                await self.core.shutdown()
            finally:
                # Отменяем остальные задачи пока цикл ещё крутится; иначе в main()
                # finally вызов run_until_complete после loop.stop() даёт RuntimeError
                # с qasync/Qt («Event loop stopped before Future completed»).
                me = asyncio.current_task()
                pending = [
                    t
                    for t in asyncio.all_tasks(loop)
                    if not t.done() and t is not me
                ]
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                loop.stop()

        asyncio.ensure_future(_shutdown())
        event.accept()


def main() -> None:
    """Точка входа без qasync.run, чтобы избежать падений при завершении."""
    if hasattr(sip, "setdestroyonexit"):
        sip.setdestroyonexit(False)

    # На macOS отключаем native menu windows, иначе вокруг QMenu может
    # появляться системная прямоугольная рамка поверх наших скруглений.
    if sys.platform == "darwin":
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_DontUseNativeMenuWindows, True
        )

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
        raw_profile = sys.argv[1]
        if not is_valid_profile_name(raw_profile):
            QtWidgets.QMessageBox.critical(
                None,
                "Invalid profile",
                "Invalid profile name from CLI.\n"
                "Allowed: a-z A-Z 0-9 . _ - (1..64 chars).",
            )
            return
        profile: Optional[str] = ensure_valid_profile_name(raw_profile)
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
            try:
                profile = dialog.selected_profile()
            except ValueError as e:
                QtWidgets.QMessageBox.warning(
                    None,
                    "Invalid profile",
                    str(e),
                )
                return
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
        loop.close()


if __name__ == "__main__":
    main()

