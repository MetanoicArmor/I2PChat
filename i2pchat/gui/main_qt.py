import asyncio
import html
import json
import logging
import math
import os
import re
import secrets
import subprocess
import shutil
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from i2pchat.router.bundled_i2pd import BundledI2pdManager
from i2pchat.router.settings import (
    RouterSettings,
    load_router_settings,
    router_runtime_dir,
    save_router_settings,
)
from i2pchat.storage.blindbox_state import atomic_write_json
from i2pchat.storage.profile_blindbox_replicas import (
    load_profile_blindbox_replicas_bundle,
    load_profile_blindbox_replicas_list,
    normalize_replica_endpoints,
)
from i2pchat.blindbox.blindbox_diagnostics import build_blindbox_diagnostics_text
from i2pchat.blindbox.local_server_example import (
    get_production_daemon_one_shot_install_curl_command,
    get_production_daemon_one_shot_install_source,
    get_production_daemon_package_note,
    get_i2pd_blindbox_tunnel_example_note,
    get_i2pd_blindbox_tunnel_example_source,
)
from i2pchat.presentation.compose_drafts import apply_compose_draft_peer_switch
from i2pchat.protocol.message_delivery import (
    DELIVERY_STATE_DELIVERED,
    DELIVERY_STATE_FAILED,
    DELIVERY_STATE_QUEUED,
    delivery_state_label,
    normalize_loaded_delivery_state,
)
from i2pchat.presentation.reply_format import format_reply_quote
from i2pchat.core.send_retry_policy import should_start_auto_connect_retry as _should_start_auto_connect_retry
from i2pchat.presentation.status_presentation import build_status_presentation
from i2pchat.presentation.notification_prefs import (
    notification_body_for_display,
    should_play_notification_sound,
    should_show_tray_message,
)
from i2pchat.presentation.unread_counters import (
    bump_unread_for_incoming_peer_message,
    clear_unread_for_peer,
    total_unread,
)
from i2pchat.storage.chat_history import (
    DEFAULT_HISTORY_RETENTION_DAYS,
    HistoryEntry,
    apply_history_retention,
    delete_history,
    list_history_file_paths,
    load_history,
    normalize_peer_addr,
    save_history,
)
from i2pchat.storage.profile_backup import (
    BackupError,
    export_history_bundle,
    export_profile_bundle,
    import_history_bundle,
    import_profile_bundle,
)
from i2pchat.core.transient_profile import (
    TRANSIENT_PROFILE_NAME,
    coalesce_profile_name,
    is_transient_profile_name,
)
from i2pchat.core.i2p_chat_core import (
    ChatMessage,
    DEFAULT_RELEASE_BLINDBOX_ENDPOINTS,
    FileTransferInfo,
    I2PChatCore,
    ensure_valid_profile_name,
    peek_persisted_stored_peer,
    get_downloads_dir,
    get_profile_data_dir,
    get_profiles_dir,
    get_images_dir,
    import_profile_dat_atomic,
    list_profile_names_in_app_data,
    migrate_all_legacy_profiles_if_needed,
    migrate_legacy_profile_files_if_needed,
    nested_profile_dat_path,
    resolve_existing_profile_file,
    is_valid_profile_name,
    render_braille,
    render_bw,
    validate_image,
)
from i2pchat.gui import menu_manual_tooltips as menu_tt
from i2pchat.gui.rounded_qtooltip import (
    I2PChatQApplication,
    apply_tooltip_handling,
    hide_rounded_tooltip,
    install_tooltip_event_filter,
    show_rounded_tooltip_at,
)
from i2pchat.gui.popup_geometry import (
    apply_win_popup_rounded_mask,
    clamp_popup_top_left_to_available_geometry,
    disable_dwm_rounded_frame,
    global_position_popup_below_anchor,
    paint_popup_rounded_bg,
    update_popup_rounded_mask,
)
from i2pchat.gui.styled_combo_widgets import ProfileComboWithArrow

from .compose_input import (
    ComposeInputWrapper,
    _scale_pixmap_to_height_preserve_alpha,
    _tint_pixmap_with_alpha,
)
from .emoji_paths import emoji_paths_cached, normalize_emoji_glyph
from .raster_emoji_render import (
    append_plain_with_raster_emoji_at_cursor,
    compose_emoji_px,
    document_plain_with_raster_emoji_images,
    emoji_inline_px,
    fill_document_from_plain,
    insert_raster_emoji_at_cursor,
    line_horizontal_advance_raster_emoji,
    make_message_qtextdocument,
    map_plain_offset_to_qt_pos,
    map_qt_pos_to_plain_offset,
    document_needs_raster_emoji_materialize,
)
from i2pchat.storage.contact_book import (
    ContactBook,
    ContactRecord,
    load_book,
    normalize_peer_address,
    remember_peer,
    remove_peer,
    save_book,
    set_last_active_peer,
    set_peer_profile,
    touch_peer_message_meta,
)

try:
    from PyQt6.QtMultimedia import QSoundEffect  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - мультимедиа не везде доступно
    QSoundEffect = None  # type: ignore[assignment]

def _read_version() -> str:
    """VERSION в корне репозитория; при запуске из trunk cwd часто не корень — ищем вверх от main_qt.py."""
    roots: list[str] = []
    roots.append(os.path.dirname(os.path.abspath(__file__)))
    roots.append(os.path.abspath("."))
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        roots.append(meipass)
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    seen: set[str] = set()
    for start in roots:
        if start in seen:
            continue
        seen.add(start)
        d = start
        for _ in range(24):
            vf = os.path.join(d, "VERSION")
            if os.path.isfile(vf):
                with open(vf) as f:
                    return f.read().strip()
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return "0.0.0"

APP_VERSION = _read_version()
BUNDLED_NOTIFY_SOUND_REL = "assets/sounds/notify.wav"
logger = logging.getLogger("i2pchat.gui")


def _resolve_gui_icon(filename: str) -> Optional[str]:
    """
    Raster icons next to main_qt.py under gui/icons/ (dev + PyInstaller via datas).
    Falls back to _resolve_local_asset for legacy bundle layouts (root-level PNGs).
    """
    gui_dir = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(gui_dir, "icons", filename)
    if os.path.isfile(p):
        return p
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        alt = os.path.join(meipass, "i2pchat", "gui", "icons", filename)
        if os.path.isfile(alt):
            return alt
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        alt2 = os.path.join(exe_dir, "i2pchat", "gui", "icons", filename)
        if os.path.isfile(alt2):
            return alt2
    return _resolve_local_asset(filename)


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


def _utc_hms_now() -> str:
    """Compact UTC time label for GUI timestamps."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _default_notify_sound_path() -> Optional[str]:
    return _resolve_local_asset(BUNDLED_NOTIFY_SOUND_REL)


def _contacts_file_path_for_read(profile: str) -> str:
    app = get_profiles_dir()
    migrate_legacy_profile_files_if_needed(app_root=app, profile=profile)
    existing = resolve_existing_profile_file(app, profile, f"{profile}.contacts.json")
    if existing:
        return existing
    return os.path.join(
        get_profile_data_dir(profile, create=True, app_root=app),
        f"{profile}.contacts.json",
    )


def _contacts_file_path_for_write(profile: str) -> str:
    app = get_profiles_dir()
    migrate_legacy_profile_files_if_needed(app_root=app, profile=profile)
    return os.path.join(
        get_profile_data_dir(profile, create=True, app_root=app),
        f"{profile}.contacts.json",
    )


# Сайдбар узкий: полный .b32 выглядит как «стена» из одинаковых символов и не различается визуально.
_CONTACT_B32_TITLE_PREFIX_LEN = 8
_CONTACT_B32_TITLE_SUFFIX_LEN = 8


def _contact_row_address_title(addr: str) -> str:
    """Заголовок строки без display_name: компактная подпись .b32 (полный адрес — в toolTip)."""
    s = (addr or "").strip()
    low = s.lower()
    if not low.endswith(".b32.i2p"):
        return s
    host = low[: -len(".b32.i2p")]
    max_plain = _CONTACT_B32_TITLE_PREFIX_LEN + _CONTACT_B32_TITLE_SUFFIX_LEN
    if len(host) <= max_plain:
        return s
    p = _CONTACT_B32_TITLE_PREFIX_LEN
    t = _CONTACT_B32_TITLE_SUFFIX_LEN
    return f"{host[:p]}…{host[-t:]}.b32.i2p"


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


def _compose_bar_input_height_px(edit: QtWidgets.QTextEdit, *, lines: int = 2) -> int:
    """Высота поля ввода под заданное число видимых строк (padding из QSS 8+8)."""
    fm = edit.fontMetrics()
    line_px = max(int(fm.lineSpacing()), int(fm.height()))
    dm = int(float(edit.document().documentMargin()) * 2.0)
    vpad = 16  # padding: 8px сверху и снизу в темах для поля ввода
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
            QDialog {
                background-color: #f5f5f7;
                border: none;
            }
            QLabel { color: #1d1d1f; }
            QLabel#ContactDetailsSelectable {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: none;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
            }
            QLabel#RouterStatusLabel {
                color: #626875;
                font-size: 12px;
            }
            QLabel#RouterSectionTitle {
                color: #2d3442;
                font-size: 13px;
                font-weight: 600;
                margin-top: 4px;
            }
            QLabel#RouterSectionSecondaryTitle {
                color: #626875;
                font-size: 12px;
                font-weight: 600;
                margin-top: 8px;
            }
            QFrame#RouterBackendPanel {
                border: 1px solid #d8dce6;
                border-radius: 12px;
                background: #eef1f7;
            }
            QLabel#RouterBackendPickTitle {
                color: #626875;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#RouterBackendOption {
                border: 1px solid #d0d5de;
                border-radius: 10px;
                background: #ffffff;
                color: #1d1d1f;
                padding: 10px 12px;
                text-align: left;
                min-height: 48px;
                min-width: 0px;
                font-weight: 600;
            }
            QPushButton#RouterBackendOption:hover:!checked {
                background: #f7f8fb;
                border-color: #c0c8d4;
            }
            QPushButton#RouterBackendOption:checked {
                background: #e8f2ff;
                border: 2px solid #0a84ff;
                color: #0a3d7a;
                padding: 9px 11px;
            }
            QPushButton#RouterBackendOption:checked:hover {
                background: #ddeaf8;
            }
            QRadioButton {
                color: #1d1d1f;
                spacing: 8px;
            }
            QComboBox {
                background: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                color: #1d1d1f;
                min-height: 22px;
            }
            QComboBox QLineEdit {
                border: none;
                background: transparent;
                margin: 0px;
                padding: 0px 0px 6px 0px;
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
            QLineEdit {
                background: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 10px;
                color: #1d1d1f;
            }
            QLineEdit:focus { border: 1px solid #0a84ff; }
            QFrame#HistoryNumericRow {
                border: 1px solid #c8ceda;
                border-radius: 8px;
                background: #ffffff;
            }
            QFrame#HistoryNumericRow[focused="true"] {
                border: 1px solid #0a84ff;
            }
            QFrame#HistoryNumericRow QSpinBox {
                border: none;
                background: transparent;
                padding: 6px 10px;
                min-height: 28px;
                color: #1d1d1f;
                outline: none;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
            }
            QWidget#HistorySpinStepColumn {
                background: #eef1f7;
                border: none;
                border-left: 1px solid #d8dce6;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
            }
            QToolButton#HistorySpinStepUp, QToolButton#HistorySpinStepDown {
                border: none;
                background: transparent;
                color: #3f4757;
                font-size: 11px;
                min-width: 28px;
                max-width: 28px;
                min-height: 15px;
                padding: 0px;
            }
            QToolButton#HistorySpinStepUp:hover, QToolButton#HistorySpinStepDown:hover {
                background: #e4e9f2;
                color: #1d1d1f;
            }
            QToolButton#HistorySpinStepUp:pressed, QToolButton#HistorySpinStepDown:pressed {
                background: #d5dbe8;
            }
            QLabel#HistoryFieldHint {
                color: #6c6e7e;
                font-size: 11px;
            }
            QTabWidget#BlindBoxExampleTabWidget {
                border: none;
                background-color: #f5f5f7;
            }
            QTabWidget#BlindBoxExampleTabWidget::pane {
                border: none;
                padding-top: 6px;
                background: transparent;
            }
            QTabWidget#BlindBoxExampleTabWidget::tab-bar {
                border: none;
                background: transparent;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar {
                background: transparent;
                border: none;
                qproperty-drawBase: 0;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab {
                min-height: 20px;
                padding: 2px 12px;
                margin-right: 5px;
                border: none;
                outline: none;
                border-radius: 10px;
                background: #e8ebf2;
                color: #3f4757;
                font-weight: 500;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:selected {
                background: #ffffff;
                color: #1d1d1f;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:hover {
                background: #dfe4ee;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:selected:hover {
                background: #f2f4f8;
            }
            QCheckBox { color: #1d1d1f; spacing: 8px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
        """,
        "window_stylesheet": """
            QMainWindow { background-color: #e6eaf2; }
            QWidget#ChatSurface {
                background: #f2f4f8;
                border: 1px solid #ffffff;
                border-radius: 14px;
            }
            QLabel#ChatSearchStatusInline {
                color: rgba(60, 60, 67, 0.55);
                background: transparent;
                padding-right: 10px;
                font-size: 12px;
            }
            QScrollArea#ChatSearchHitsScroll {
                background: transparent;
                border: none;
            }
            QWidget#ChatSearchHitsInner {
                background: transparent;
            }
            QPushButton#ChatSearchHitRow {
                background: transparent;
                border: none;
                color: #1b4a2f;
                font-size: 11px;
                text-align: left;
                padding: 5px 8px;
                border-radius: 5px;
            }
            QPushButton#ChatSearchHitRow:hover {
                background: rgba(10, 132, 255, 0.10);
            }
            QPushButton#ChatSearchHitRow[hitSelected="true"] {
                background: rgba(10, 132, 255, 0.22);
                color: #0d2e1c;
            }
            QFrame#ChatSearchHitsConsole QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QFrame#ChatSearchHitsConsole QScrollBar::handle:vertical {
                background: rgba(60, 60, 67, 0.35);
                min-height: 20px;
                border-radius: 3px;
            }
            QFrame#ChatSearchHitsConsole QScrollBar::add-line:vertical,
            QFrame#ChatSearchHitsConsole QScrollBar::sub-line:vertical { height: 0px; }
            QListView {
                background: transparent;
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
            QLineEdit, QTextEdit {
                background: #ffffff;
                border: none;
                border-radius: 9px;
                padding: 8px 10px;
                color: #1d1d1f;
            }
            QPlainTextEdit {
                background: #ffffff;
                border: none;
                border-radius: 9px;
                padding: 8px 10px;
            }
            QPlainTextEdit#BlindBoxDiagnosticsSummary {
                color: #1d1d1f;
            }
            QLineEdit#PeerAddressEdit {
                padding: 8px 10px 8px 8px;
            }
            QLineEdit:focus, QTextEdit:focus {
                background: #ffffff;
                border: 1px solid #0a84ff;
            }
            QPlainTextEdit:focus {
                background: #ffffff;
                border: 1px solid #0a84ff;
            }
            QWidget#ComposeBar, QWidget#ActionToolbar {
                background: #eaedf4;
                border: 1px solid #ffffff;
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
                background: #f2f4f8;
                border: 1px solid #ffffff;
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
            QWidget#ComposeResizeGrip {
                background: transparent;
            }
            QWidget#ComposeResizeGrip:hover {
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
            "peer_bg": "#e2e6ef",
            "peer_text": "#1c1c1e",
            "system_bg": "#e8ecf3",
            "system_text": "#5f6673",
            "error_bg": "#f2d8d7",
            "error_text": "#7c302c",
            "success_bg": "#d7ebdc",
            "success_text": "#245039",
            "file_bg": "#dde2ec",
            "file_text": "#1d1d1f",
            "fallback_bg": "#dfe4ee",
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
            "image_placeholder_bg": "#d0d5e0",
            "image_placeholder_text": "#3a3a40",
            "image_me_bg": "#2f92f0",
            "image_peer_bg": "#d8dce6",
            "tick_success": "#124529",
            "tick_image": "#ffffff",
        },
        "hint_secondary": "#626875",
        "hint_muted": "#767d8b",
        "label_primary": "#444b58",
        "combo_arrow": "#8c8d94",
        "chat_viewport_fade": "#f2f4f8",
    },
    "night": {
        "label": "night",
        "dialog_stylesheet": """
            QDialog {
                background-color: #141417;
                border: none;
            }
            QLabel { color: #f5f5f7; }
            QLabel#ContactDetailsSelectable {
                background-color: #141417;
                color: #f5f5f7;
                border: none;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
            }
            QLabel#RouterStatusLabel {
                color: #8d95a6;
                font-size: 12px;
            }
            QLabel#RouterSectionTitle {
                color: #f5f5f7;
                font-size: 13px;
                font-weight: 600;
                margin-top: 4px;
            }
            QLabel#RouterSectionSecondaryTitle {
                color: #9aa3b5;
                font-size: 12px;
                font-weight: 600;
                margin-top: 8px;
            }
            QFrame#RouterBackendPanel {
                border: 1px solid #3d4450;
                border-radius: 12px;
                background: rgba(255, 255, 255, 0.05);
            }
            QLabel#RouterBackendPickTitle {
                color: #8d95a6;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#RouterBackendOption {
                border: 1px solid #4a505c;
                border-radius: 10px;
                background: #1f1f23;
                color: #f5f5f7;
                padding: 10px 12px;
                text-align: left;
                min-height: 48px;
                min-width: 0px;
                font-weight: 600;
            }
            QPushButton#RouterBackendOption:hover:!checked {
                background: #2a2d36;
                border-color: #5c6470;
            }
            QPushButton#RouterBackendOption:checked {
                background: rgba(10, 132, 255, 0.2);
                border: 2px solid #0a84ff;
                color: #f5f5f7;
                padding: 9px 11px;
            }
            QPushButton#RouterBackendOption:checked:hover {
                background: rgba(10, 132, 255, 0.28);
            }
            QRadioButton {
                color: #f5f5f7;
                spacing: 8px;
            }
            QComboBox {
                background: #1f1f23;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                color: #f5f5f7;
                min-height: 22px;
            }
            QComboBox QLineEdit {
                border: none;
                background: transparent;
                margin: 0px;
                padding: 0px 0px 6px 0px;
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
            QLineEdit {
                background: #1f1f23;
                border: none;
                border-radius: 8px;
                padding: 8px 10px;
                color: #f5f5f7;
            }
            QLineEdit:focus { border: 1px solid #0a84ff; }
            QFrame#HistoryNumericRow {
                border: 1px solid #3d4450;
                border-radius: 8px;
                background: #1f1f23;
            }
            QFrame#HistoryNumericRow[focused="true"] {
                border: 1px solid #0a84ff;
            }
            QFrame#HistoryNumericRow QSpinBox {
                border: none;
                background: transparent;
                padding: 6px 10px;
                min-height: 28px;
                color: #f5f5f7;
                outline: none;
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
            }
            QWidget#HistorySpinStepColumn {
                background: #2a2d36;
                border: none;
                border-left: 1px solid #3d4450;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
            }
            QToolButton#HistorySpinStepUp, QToolButton#HistorySpinStepDown {
                border: none;
                background: transparent;
                color: #c6cfdf;
                font-size: 11px;
                min-width: 28px;
                max-width: 28px;
                min-height: 15px;
                padding: 0px;
            }
            QToolButton#HistorySpinStepUp:hover, QToolButton#HistorySpinStepDown:hover {
                background: #353945;
                color: #f5f5f7;
            }
            QToolButton#HistorySpinStepUp:pressed, QToolButton#HistorySpinStepDown:pressed {
                background: #404554;
            }
            QLabel#HistoryFieldHint {
                color: #8d95a6;
                font-size: 11px;
            }
            QTabWidget#BlindBoxExampleTabWidget {
                border: none;
                background-color: #141417;
            }
            QTabWidget#BlindBoxExampleTabWidget::pane {
                border: none;
                padding-top: 6px;
                background: transparent;
            }
            QTabWidget#BlindBoxExampleTabWidget::tab-bar {
                border: none;
                background: transparent;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar {
                background: transparent;
                border: none;
                qproperty-drawBase: 0;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab {
                min-height: 20px;
                padding: 2px 12px;
                margin-right: 5px;
                border: none;
                outline: none;
                border-radius: 10px;
                background: rgba(255, 255, 255, 0.06);
                color: #b4bcc8;
                font-weight: 500;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:selected {
                background: rgba(255, 255, 255, 0.18);
                color: #f5f5f7;
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:hover {
                background: rgba(255, 255, 255, 0.11);
            }
            QTabWidget#BlindBoxExampleTabWidget QTabBar::tab:selected:hover {
                background: rgba(255, 255, 255, 0.22);
            }
            QCheckBox { color: #f5f5f7; spacing: 8px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
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
            QScrollArea#ChatSearchHitsScroll {
                background: transparent;
                border: none;
            }
            QWidget#ChatSearchHitsInner {
                background: transparent;
            }
            QPushButton#ChatSearchHitRow {
                background: transparent;
                border: none;
                color: #8fd99a;
                font-size: 11px;
                text-align: left;
                padding: 5px 8px;
                border-radius: 5px;
            }
            QPushButton#ChatSearchHitRow:hover {
                background: rgba(48, 209, 88, 0.10);
            }
            QPushButton#ChatSearchHitRow[hitSelected="true"] {
                background: rgba(48, 209, 88, 0.18);
                color: #d8ffe0;
            }
            QFrame#ChatSearchHitsConsole QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QFrame#ChatSearchHitsConsole QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.18);
                min-height: 20px;
                border-radius: 3px;
            }
            QFrame#ChatSearchHitsConsole QScrollBar::add-line:vertical,
            QFrame#ChatSearchHitsConsole QScrollBar::sub-line:vertical { height: 0px; }
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
            QLineEdit, QTextEdit {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 8px;
                padding: 8px 10px;
                color: #f5f5f7;
            }
            QPlainTextEdit {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 8px;
                padding: 8px 10px;
            }
            QPlainTextEdit#BlindBoxDiagnosticsSummary {
                color: #f5f5f7;
            }
            QLineEdit#PeerAddressEdit {
                padding: 8px 10px 8px 8px;
            }
            QLineEdit:focus, QTextEdit:focus {
                background: rgba(255, 255, 255, 0.09);
                border: 1px solid rgba(10, 132, 255, 0.85);
            }
            QPlainTextEdit:focus {
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
            QWidget#ComposeResizeGrip {
                background: transparent;
            }
            QWidget#ComposeResizeGrip:hover {
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
        # Линейная смесь rgba(34,37,45,0.68) (ChatSurface) над #101114 — как фон под прозрачным QListView.
        "chat_viewport_fade": "#1c1f25",
    },
}

# Stored in ui_prefs.json; maps to ligth/night via effective_theme_id().
THEME_PREF_AUTO = "auto"
THEME_PREFERENCE_CYCLE: tuple[str, ...] = ("ligth", "night", THEME_PREF_AUTO)


def _normalize_theme_preference(theme_id: Optional[str]) -> str:
    raw = str(theme_id or "").strip().lower()
    if raw in {"macos", "light"}:
        raw = "ligth"
    if raw in {THEME_PREF_AUTO, "system", "follow"}:
        return THEME_PREF_AUTO
    if raw in THEMES:
        return raw
    return THEME_DEFAULT


def _system_prefers_dark() -> bool:
    app = QtGui.QGuiApplication.instance()
    if app is None:
        return False
    hints = app.styleHints()
    scheme = hints.colorScheme()
    if scheme == QtCore.Qt.ColorScheme.Dark:
        return True
    if scheme == QtCore.Qt.ColorScheme.Light:
        return False
    bg = app.palette().color(QtGui.QPalette.ColorRole.Window)
    return int(bg.lightness()) < 128


def effective_theme_id(preference: Optional[str]) -> str:
    pref = _normalize_theme_preference(preference)
    if pref == THEME_PREF_AUTO:
        return "night" if _system_prefers_dark() else "ligth"
    return pref


def _resolve_theme(theme_id: Optional[str]) -> str:
    """Concrete palette key (ligth | night), including resolving system/auto preference."""
    return effective_theme_id(_normalize_theme_preference(theme_id))


# Tooltip colors for QPalette — used by i2pchat.gui.rounded_qtooltip (replaces native QTipLabel on macOS).
_TOOLTIP_THEME_COLORS: dict[str, tuple[str, str]] = {
    "ligth": ("#f2f4f8", "#1d1d1f"),
    "night": ("#22252d", "#e3e8f1"),
}


def _apply_application_tooltip_stylesheet(theme_id: Optional[str]) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    tid = _resolve_theme(theme_id)
    bg_hex, fg_hex = _TOOLTIP_THEME_COLORS.get(tid, _TOOLTIP_THEME_COLORS[THEME_DEFAULT])
    bg = QtGui.QColor(bg_hex)
    fg = QtGui.QColor(fg_hex)
    pal = QtGui.QPalette(app.palette())
    for grp in (
        QtGui.QPalette.ColorGroup.Active,
        QtGui.QPalette.ColorGroup.Inactive,
        QtGui.QPalette.ColorGroup.Disabled,
    ):
        pal.setColor(grp, QtGui.QPalette.ColorRole.ToolTipBase, bg)
        pal.setColor(grp, QtGui.QPalette.ColorRole.ToolTipText, fg)
    app.setPalette(pal)


def _apply_dialog_theme_sheet(widget: QtWidgets.QWidget, theme_id: Optional[str]) -> None:
    """Отдельный лист от QMainWindow: иначе на macOS QDialog с светлым фоном наследует QLabel { color: #f5f5f7 }."""
    theme = THEMES[_resolve_theme(theme_id)]
    widget.setStyleSheet(str(theme["dialog_stylesheet"]))


def _format_plaintext_hash_comment_lines(
    edit: QtWidgets.QPlainTextEdit, theme_id: Optional[str]
) -> None:
    """Dim lines starting with # via QTextCharFormat. Do not set QSS `color` on this QPlainTextEdit."""
    if getattr(edit, "_bb_hash_format_guard", False):
        return
    edit._bb_hash_format_guard = True
    try:
        tid = _resolve_theme(theme_id)
        theme = THEMES[tid]
        normal = QtGui.QColor("#1d1d1f" if tid == "ligth" else "#f5f5f7")
        muted = QtGui.QColor(str(theme.get("hint_muted", "#767d8b")))
        doc = edit.document()
        cur_save = edit.textCursor()
        a0, a1 = cur_save.anchor(), cur_save.position()
        lo, hi = (min(a0, a1), max(a0, a1))
        edit.blockSignals(True)
        try:
            block = doc.begin()
            cur = QtGui.QTextCursor(doc)
            while block.isValid():
                t = block.text()
                cur.setPosition(block.position())
                cur.movePosition(
                    QtGui.QTextCursor.MoveOperation.EndOfBlock,
                    QtGui.QTextCursor.MoveMode.KeepAnchor,
                )
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(
                    QtGui.QBrush(muted if t.lstrip().startswith("#") else normal)
                )
                cur.setCharFormat(fmt)
                block = block.next()
        finally:
            edit.blockSignals(False)
        cur_rest = edit.textCursor()
        cur_rest.setPosition(lo)
        cur_rest.setPosition(hi, QtGui.QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cur_rest)
    finally:
        edit._bb_hash_format_guard = False


def _contact_details_selectable_label(
    parent: QtWidgets.QWidget, html: str
) -> QtWidgets.QLabel:
    """Текст диалога Contact details: выделение мышью/клавиатурой для копирования."""
    lab = QtWidgets.QLabel(html, parent)
    lab.setObjectName("ContactDetailsSelectable")
    lab.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    lab.setWordWrap(True)
    lab.setAutoFillBackground(True)
    lab.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    lab.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
    return lab


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
    return _normalize_theme_preference(str(data.get("theme", THEME_PREF_AUTO)))


def save_theme(theme_pref: str) -> None:
    pref = _normalize_theme_preference(theme_pref)
    data = _load_ui_prefs()
    data["theme"] = pref
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


def load_compose_enter_sends() -> bool:
    data = _load_ui_prefs()
    return data.get("compose_enter_sends") is True


def save_compose_enter_sends(enter_sends: bool) -> None:
    data = _load_ui_prefs()
    if enter_sends:
        data["compose_enter_sends"] = True
    else:
        data.pop("compose_enter_sends", None)
    _save_ui_prefs(data)


def load_compose_split_bottom_height() -> Optional[int]:
    data = _load_ui_prefs()
    v = data.get("compose_split_bottom_height")
    if isinstance(v, int) and v >= 32:
        return int(v)
    return None


def save_compose_split_bottom_height(h: int) -> None:
    data = _load_ui_prefs()
    data["compose_split_bottom_height"] = max(32, int(h))
    _save_ui_prefs(data)


def load_releases_custom_url_warn_ack() -> bool:
    data = _load_ui_prefs()
    return data.get("releases_custom_url_warn_ack") is True


def save_releases_custom_url_warn_ack() -> None:
    data = _load_ui_prefs()
    data["releases_custom_url_warn_ack"] = True
    _save_ui_prefs(data)


def load_releases_custom_proxy_warn_ack() -> bool:
    data = _load_ui_prefs()
    return data.get("releases_custom_proxy_warn_ack") is True


def save_releases_custom_proxy_warn_ack() -> None:
    data = _load_ui_prefs()
    data["releases_custom_proxy_warn_ack"] = True
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


def save_history_max_messages(max_messages: int) -> None:
    value = int(max_messages)
    if value < 1:
        raise ValueError("history_max_messages must be positive")
    data = _load_ui_prefs()
    data["history_max_messages"] = value
    _save_ui_prefs(data)


def load_history_retention_days() -> int:
    data = _load_ui_prefs()
    val = data.get("history_retention_days")
    if isinstance(val, int):
        return max(0, val)
    return DEFAULT_HISTORY_RETENTION_DAYS


def save_history_retention_days(days: int) -> None:
    value = max(0, int(days))
    data = _load_ui_prefs()
    data["history_retention_days"] = value
    _save_ui_prefs(data)


def load_privacy_mode_enabled() -> bool:
    data = _load_ui_prefs()
    return data.get("privacy_mode_enabled") is True


def save_privacy_mode_enabled(enabled: bool) -> None:
    data = _load_ui_prefs()
    if enabled:
        data["privacy_mode_enabled"] = True
    else:
        data.pop("privacy_mode_enabled", None)
    _save_ui_prefs(data)


COMPOSE_DRAFTS_MAX_KEYS = 100
COMPOSE_DRAFTS_DEBOUNCE_MS = 1500


def _compose_drafts_file_path_for_read(profile: str) -> str:
    app = get_profiles_dir()
    migrate_legacy_profile_files_if_needed(app_root=app, profile=profile)
    existing = resolve_existing_profile_file(
        app, profile, f"{profile}.compose_drafts.json"
    )
    if existing:
        return existing
    return os.path.join(
        get_profile_data_dir(profile, create=True, app_root=app),
        f"{profile}.compose_drafts.json",
    )


def _compose_drafts_file_path_for_write(profile: str) -> str:
    app = get_profiles_dir()
    migrate_legacy_profile_files_if_needed(app_root=app, profile=profile)
    return os.path.join(
        get_profile_data_dir(profile, create=True, app_root=app),
        f"{profile}.compose_drafts.json",
    )


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
    message_id: Optional[str] = None
    delivery_state: Optional[str] = None
    delivery_route: Optional[str] = None
    delivery_hint: str = ""
    delivery_reason: str = ""
    retryable: bool = False
    retry_kind: Optional[str] = None
    retry_source_path: Optional[str] = None


def _chat_item_delivery_state(item: ChatItem) -> Optional[str]:
    state = (item.delivery_state or "").strip().lower()
    if state:
        return state
    if item.delivered:
        return DELIVERY_STATE_DELIVERED
    return None


def _chat_item_delivery_meta_text(item: ChatItem) -> str:
    state = _chat_item_delivery_state(item)
    label = delivery_state_label(state)
    if not item.timestamp:
        return label
    if label:
        return f"{item.timestamp} · {label}"
    return item.timestamp


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

    def remove_item(self, row: int) -> None:
        if not (0 <= row < len(self._items)):
            return
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        self._items.pop(row)
        self.endRemoveRows()

    def item_at(self, row: int) -> Optional[ChatItem]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def clear_items(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()


_BAYER4: tuple[tuple[int, int, int, int], ...] = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def _lerp_alpha_stops(t: float, stops: list[tuple[float, float]]) -> float:
    """Кусочно-линейная интерполяция альфы 0..255; stops: (t∈[0,1], alpha)."""
    if t <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        t0, a0 = stops[i - 1]
        t1, a1 = stops[i]
        if t <= t1:
            if t1 <= t0:
                return a1
            u = (t - t0) / (t1 - t0)
            return a0 + (a1 - a0) * u
    return stops[-1][1]


class _ChatViewportEdgeFade(QtWidgets.QWidget):
    """Полоса с градиентом к прозрачности — смягчает обрезку баблов у краёв viewport."""

    # Крутой подъём альфы только у самого края — уже зона полупрозрачности (меньше бендинга и лишнего смешивания с контентом ленты).
    _STOPS_TOP: list[tuple[float, float]] = [
        (0.0, 255.0),
        (0.05, 175.0),
        (0.10, 55.0),
        (0.13, 0.0),
        (1.0, 0.0),
    ]
    _STOPS_BOTTOM: list[tuple[float, float]] = [
        (0.0, 0.0),
        (0.87, 0.0),
        (0.93, 55.0),
        (0.97, 175.0),
        (1.0, 255.0),
    ]

    def __init__(self, parent: QtWidgets.QWidget, *, top: bool) -> None:
        super().__init__(parent)
        self._top = top
        self._base = QtGui.QColor("#2a2d36")
        self.setObjectName("ChatViewportEdgeFade")
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._dither_key: Optional[tuple[int, int, bool, int, int, int]] = None
        self._dither_buf: Optional[bytearray] = None
        self._dither_img: Optional[QtGui.QImage] = None

    def set_base_color(self, color: QtGui.QColor) -> None:
        self._base = QtGui.QColor(color)
        self._dither_key = None
        self.update()

    def _ideal_alpha(self, ty: float) -> float:
        stops = self._STOPS_TOP if self._top else self._STOPS_BOTTOM
        return _lerp_alpha_stops(ty, stops)

    def _rebuild_dither_image(self, w: int, h: int) -> None:
        stride = w * 4
        buf = bytearray(stride * h)
        r = int(self._base.red())
        g = int(self._base.green())
        b = int(self._base.blue())
        denom = float(max(h - 1, 1))
        # Лёгкий ordered dither по X/Y — снимает горизонтальный 8‑битный бендинг Qt-градиента.
        d_scale = 0.55
        for y in range(h):
            ty = y / denom
            ideal = self._ideal_alpha(ty)
            y3 = y & 3
            row = y * stride
            for x in range(w):
                d = (float(_BAYER4[y3][x & 3]) - 7.5) * d_scale
                ai = int(round(ideal + d))
                if ai < 0:
                    ai = 0
                elif ai > 255:
                    ai = 255
                o = row + x * 4
                buf[o] = b
                buf[o + 1] = g
                buf[o + 2] = r
                buf[o + 3] = ai
        self._dither_buf = buf
        self._dither_img = QtGui.QImage(
            buf,
            w,
            h,
            stride,
            QtGui.QImage.Format.Format_ARGB32,
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event
        w = max(0, self.width())
        h = max(0, self.height())
        if w == 0 or h == 0:
            return
        r = int(self._base.red())
        g = int(self._base.green())
        b = int(self._base.blue())
        key = (w, h, self._top, r, g, b)
        if self._dither_key != key:
            self._rebuild_dither_image(w, h)
            self._dither_key = key
        if self._dither_img is None or self._dither_img.isNull():
            return
        p = QtGui.QPainter(self)
        p.drawImage(0, 0, self._dither_img)


class ChatListView(QtWidgets.QListView):
    """QListView для баблов чата.

    - перераскладывает элементы при изменении ширины (для переноса строк)
    - поддерживает копирование текста (контекстное меню и Cmd/Ctrl+C)
    - открывает изображения по двойному клику
    """
    cancelTransferRequested = QtCore.pyqtSignal()
    imageOpenRequested = QtCore.pyqtSignal(str)  # path to image
    replyRequested = QtCore.pyqtSignal(str)  # quoted block for compose field
    retryRequested = QtCore.pyqtSignal(int)

    _EDGE_FADE_PX = 44

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.DefaultContextMenu)
        self._theme_id = THEME_DEFAULT
        self._context_popup: Optional["ActionsPopup"] = None
        self._context_popup_suppress_until_ms = 0
        self.setMouseTracking(True)
        # Только по клику — иначе частые dataChanged при прогрессе файла забирают фокус с поля ввода.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems
        )
        self._fade_top = _ChatViewportEdgeFade(self, top=True)
        self._fade_bottom = _ChatViewportEdgeFade(self, top=False)
        self._apply_edge_fade_theme_colors()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._layout_edge_fades()

    def _apply_edge_fade_theme_colors(self) -> None:
        theme = THEMES.get(self._theme_id, THEMES[THEME_DEFAULT])
        raw = theme.get("chat_viewport_fade")
        if isinstance(raw, str):
            c = QtGui.QColor(raw)
            if not c.isValid():
                c = QtGui.QColor("#2a2d36")
        else:
            c = QtGui.QColor("#2a2d36")
        self._fade_top.set_base_color(c)
        self._fade_bottom.set_base_color(c)

    def _layout_edge_fades(self) -> None:
        vp = self.viewport()
        g = vp.geometry()
        if g.width() <= 0 or g.height() <= 0:
            return
        cap = max(12, (g.height() - 8) // 2)
        h = min(self._EDGE_FADE_PX, cap)
        self._fade_top.setGeometry(g.x(), g.y(), g.width(), h)
        self._fade_bottom.setGeometry(
            g.x(), g.y() + g.height() - h, g.width(), h
        )
        self._fade_top.raise_()
        self._fade_bottom.raise_()
        sb = self.verticalScrollBar()
        if sb is not None:
            self._fade_top.stackUnder(sb)
            self._fade_bottom.stackUnder(sb)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_edge_fades()
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
        self._apply_edge_fade_theme_colors()
        self._layout_edge_fades()

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
                "Copy text",
                lambda i=index: self._copy_index_text(i, with_meta=False),
                tool_tip=menu_tt.TT_COPY_TEXT,
            )

        def add_copy_timestamp() -> None:
            self._context_popup.add_action(
                "Copy with timestamp",
                lambda i=index: self._copy_index_text(i, with_meta=True),
                tool_tip=menu_tt.TT_COPY_WITH_TIMESTAMP,
            )

        k = item.kind
        if k == "image_inline" and item.image_path:
            p = item.image_path
            self._context_popup.add_action(
                "Open",
                lambda path=p: self.imageOpenRequested.emit(path),
                tool_tip=menu_tt.TT_OPEN_IMAGE_OR_FILE,
            )
            self._context_popup.add_action(
                "Copy path",
                lambda path=p: self._copy_path(path),
                tool_tip=menu_tt.TT_COPY_PATH,
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
                    tool_tip=menu_tt.TT_OPEN_IMAGE_OR_FILE,
                )
                self._context_popup.add_action(
                    "Copy path",
                    lambda path=fp: self._copy_path(path),
                    tool_tip=menu_tt.TT_COPY_PATH,
                )
            if item.open_folder_path and os.path.isdir(item.open_folder_path):
                folder = item.open_folder_path
                self._context_popup.add_action(
                    "Open folder",
                    lambda d=folder: QtGui.QDesktopServices.openUrl(
                        QtCore.QUrl.fromLocalFile(d)
                    ),
                    tool_tip=menu_tt.TT_OPEN_FOLDER,
                )
                self._context_popup.add_action(
                    "Copy folder path",
                    lambda d=folder: self._copy_path(d),
                    tool_tip=menu_tt.TT_COPY_FOLDER_PATH,
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
                    tool_tip=menu_tt.TT_REPLY,
                )
            if item.retryable:
                self._context_popup.add_action(
                    "Retry",
                    lambda r=index.row(): self.retryRequested.emit(r),
                    tool_tip=menu_tt.TT_RETRY,
                )
        elif k == "transfer" and item.text.strip():
            fn = item.text.strip()
            self._context_popup.add_action(
                "Copy filename",
                lambda name=fn: self._copy_path(name),
                tool_tip=menu_tt.TT_COPY_FILENAME,
            )
        else:
            if item.text.strip():
                add_copy_text()
                add_copy_timestamp()
            if item.retryable:
                self._context_popup.add_action(
                    "Retry",
                    lambda r=index.row(): self.retryRequested.emit(r),
                    tool_tip=menu_tt.TT_RETRY,
                )

        detail = (item.delivery_hint or item.delivery_reason or "").strip()
        if detail:
            self._context_popup.add_action(
                "Copy delivery details",
                lambda text=detail: QtWidgets.QApplication.clipboard().setText(text),
                tool_tip=menu_tt.TT_DELIVERY_DETAIL,
            )

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

    def viewportEvent(self, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.ToolTip:
            help_event = event
            if isinstance(help_event, QtGui.QHelpEvent):
                index = self.indexAt(help_event.pos())
                if index.isValid():
                    item = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
                    if isinstance(item, ChatItem):
                        detail = (item.delivery_hint or item.delivery_reason or "").strip()
                        if detail:
                            show_rounded_tooltip_at(help_event.globalPos(), detail, owner=self)
                            return True
            hide_rounded_tooltip()
        return super().viewportEvent(event)

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
    # Вертикальный зазор между баблами (минимальный; визуальный воздух даёт padding внутри бабла)
    BUBBLE_SPACING_Y = 0
    # Внешний отступ закрашенного бабла от верха/низа ячейки (тонкий зазор между соседними баблами).
    # BUBBLE_SPACING_Y остаётся 0 — только этот inset даёт «чуть-чуть» воздуха. Внутренние PADDING у текста те же.
    BUBBLE_OUTER_MARGIN_Y = 2
    BUBBLE_RADIUS = 12
    
    # Настройки для inline-изображений
    # Макс. размер превью в бабле (масштабирование с сохранением пропорций).
    IMAGE_MAX_WIDTH = 448
    IMAGE_MAX_HEIGHT = 336
    
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

    def _text_max_line_advance(self, text: str, font: QtGui.QFont) -> int:
        """Макс. ширина среди явных строк (по \\n); растровые эмодзи — как картинки фикс. ширины."""
        metrics = QtGui.QFontMetrics(font)
        paths = emoji_paths_cached()
        px = emoji_inline_px(metrics)
        if not text:
            return int(metrics.horizontalAdvance(" "))
        best = 0
        for line in text.split("\n"):
            if paths:
                w = line_horizontal_advance_raster_emoji(line, metrics, paths, px)
            else:
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
        content_px = self._text_max_line_advance(text, font) + self.PADDING_X * 4
        max_w = int(cell_width * 0.75)
        multiline = "\n" in (text or "")
        if multiline:
            min_w = max(72, int(cell_width * 0.12))
        else:
            min_w = int(cell_width * 0.4)
        return max(min_w, min(max_w, content_px))

    def _bubble_inner_text_width_px(self, bubble_width: int) -> float:
        """Ширина для QTextDocument в бабле — как text_area.width() в paint() после двойных отступов."""
        return float(max(10, bubble_width - 3 * self.PADDING_X))

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

        delivery_state = _chat_item_delivery_state(item)

        if item.kind in {"me", "image_braille", "image_bw"}:
            if delivery_state == DELIVERY_STATE_FAILED:
                bg_color = self._c("me_failed_bg", "#7a2f2f")
                text_color = self._c("me_failed_text", "#f8f8f2")
            else:
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
            if delivery_state == DELIVERY_STATE_FAILED:
                bg_color = self._c("success_failed_bg", "#7a2f2f")
                text_color = self._c("success_failed_text", "#f8f8f2")
            else:
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
        omy = self.BUBBLE_OUTER_MARGIN_Y
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            omy,
            -self.PADDING_X / 2,
            -omy,
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

        paths = emoji_paths_cached()
        doc = make_message_qtextdocument(
            full_text,
            base_font,
            text_color,
            float(text_area.width()),
            paths,
        )
        painter.save()
        painter.translate(text_area.left(), text_area.top())
        tw, th = int(text_area.width()), int(text_area.height())
        painter.setClipRect(0, 0, tw, th)
        doc.drawContents(painter, QtCore.QRectF(0, 0, tw, th))
        painter.restore()

        meta_text = _chat_item_delivery_meta_text(item)
        if ts_rect is not None and meta_text:
            ts_font = QtGui.QFont(base_font)
            ts_font.setPointSize(max(base_font.pointSize() - 1, 6))
            painter.setFont(ts_font)

            # Цвет штампа делаем чуть темнее текста для контраста на ярком фоне
            if item.kind == "success":
                ts_color = self._c("tick_success", "#15542d")
            elif delivery_state == DELIVERY_STATE_FAILED:
                ts_color = QtGui.QColor("#ffd6d6")
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
                meta_text,
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
            ticks = "✓✓" if _chat_item_delivery_state(item) == DELIVERY_STATE_DELIVERED else "✓"
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
        omy = self.BUBBLE_OUTER_MARGIN_Y
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            omy,
            -self.PADDING_X / 2,
            -omy,
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
            ticks = "✓✓" if _chat_item_delivery_state(item) == DELIVERY_STATE_DELIVERED else "✓"
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
        
        omy = self.BUBBLE_OUTER_MARGIN_Y
        bubble_rect = bubble_rect.adjusted(
            self.PADDING_X / 2,
            omy,
            -self.PADDING_X / 2,
            -omy,
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

        bubble_width = self._bubble_width(cell_width, item.text, font)
        inner_w = self._bubble_inner_text_width_px(bubble_width)

        text = item.text or " "

        paths = emoji_paths_cached()
        dummy_color = QtGui.QColor("#000000")
        doc = make_message_qtextdocument(
            text, font, dummy_color, inner_w, paths
        )
        text_height = math.ceil(float(doc.size().height()))

        # Высота строки = высота документа + цепочка отступов как в paint():
        # rect → bubble (−2·OUTER_MARGIN_Y) → inner (−2·PY) → text_area (−ts_height − PY/2 при timestamp).
        height = int(text_height) + self.PADDING_Y * 2 + 2 * self.BUBBLE_OUTER_MARGIN_Y + self.BUBBLE_SPACING_Y * 2
        if item.timestamp:
            ts_m = QtGui.QFontMetrics(font)
            height += ts_m.height() + int(self.PADDING_Y / 2)

        return QtCore.QSize(int(cell_width), int(height))


class MessageInputEdit(QtWidgets.QTextEdit):
    """Многострочное поле ввода; Noto Emoji в документе — как в пикере (PNG).

    В протокол и черновики уходит Unicode через plainTextForSend().

    Режим по умолчанию: Enter — новая строка; Shift+Enter и Ctrl/⌘+Enter — отправка.
    Режим «Enter отправляет»: Enter — отправка; Shift+Enter — новая строка (Ctrl/⌘+Enter тоже отправка).
    """
    sendRequested = QtCore.pyqtSignal()
    imagePasteReady = QtCore.pyqtSignal(str)
    composeTextChanged = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._theme_id = THEME_DEFAULT
        self._context_popup: Optional["ActionsPopup"] = None
        self._context_popup_suppress_until_ms = 0
        self.setAcceptDrops(True)
        self.setAcceptRichText(True)
        self.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._suppress_compose_signal = False
        self._suppress_materialize = False
        self._materializing = False
        self._materialize_timer = QtCore.QTimer(self)
        self._materialize_timer.setSingleShot(True)
        self._materialize_timer.setInterval(200)
        self._materialize_timer.timeout.connect(self._materialize_raster_emojis)
        self.document().contentsChanged.connect(self._on_document_contents_changed)
        self._enter_sends = False

    def set_enter_sends(self, enter_sends: bool) -> None:
        self._enter_sends = bool(enter_sends)

    def plainTextForSend(self) -> str:
        return document_plain_with_raster_emoji_images(self.document())

    def setPlainTextForCompose(self, text: str) -> None:
        self._materialize_timer.stop()
        self._suppress_compose_signal = True
        self._suppress_materialize = True
        try:
            fill_document_from_plain(self.document(), text, self.font())
            cur = QtGui.QTextCursor(self.document())
            cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
            self.setTextCursor(cur)
        finally:
            self._suppress_materialize = False
            self._suppress_compose_signal = False

    def insert_raster_emoji(self, ch: str) -> None:
        paths = emoji_paths_cached()
        pth = paths.get(normalize_emoji_glyph(ch))
        cur = self.textCursor()
        if pth is None:
            cur.insertText(ch)
        else:
            px = compose_emoji_px(self.font())
            insert_raster_emoji_at_cursor(cur, self.document(), ch, pth, px)
        self.setTextCursor(cur)

    def _on_document_contents_changed(self) -> None:
        if not self._suppress_compose_signal:
            self.composeTextChanged.emit()
        if self._suppress_materialize or self._materializing:
            return
        if not emoji_paths_cached():
            return
        self._materialize_timer.start()

    def _rebuild_compose_emojis_from_unicode_plain(self) -> None:
        """Пересобрать документ из Unicode-представления (растровые эмодзи из manifest)."""
        self._materialize_timer.stop()
        if self._suppress_materialize or self._materializing:
            return
        self._materializing = True
        self._suppress_compose_signal = True
        try:
            plain = document_plain_with_raster_emoji_images(self.document())
            pos = self.textCursor().position()
            off = map_qt_pos_to_plain_offset(self.document(), pos)
            fill_document_from_plain(self.document(), plain, self.font())
            new_off = min(max(0, off), len(plain))
            qt_pos = map_plain_offset_to_qt_pos(self.document(), new_off)
            cur = QtGui.QTextCursor(self.document())
            cur.setPosition(qt_pos)
            self.setTextCursor(cur)
        finally:
            self._suppress_compose_signal = False
            self._materializing = False

    def _materialize_raster_emojis(self) -> None:
        if not emoji_paths_cached():
            return
        # Only rebuild while emoji are still plain text; once they are images, the serialized
        # plain still contains emoji codepoints — a string-based check would rebuild forever.
        if not document_needs_raster_emoji_materialize(self.document()):
            return
        self._rebuild_compose_emojis_from_unicode_plain()

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
        if source.hasText():
            cur = self.textCursor()
            self._suppress_materialize = True
            try:
                append_plain_with_raster_emoji_at_cursor(
                    cur, self.document(), source.text(), self.font()
                )
                self.setTextCursor(cur)
            finally:
                self._suppress_materialize = False
            return
        # Только HTML в буфере без текста: не кормим произвольный разметкой QTextDocument
        if source.hasHtml():
            raw_h = source.html()
            if not isinstance(raw_h, str):
                raw_h = str(raw_h)
            plain = html.unescape(re.sub(r"<[^>]+>", "", raw_h))
            cur = self.textCursor()
            self._suppress_materialize = True
            try:
                append_plain_with_raster_emoji_at_cursor(
                    cur, self.document(), plain, self.font()
                )
                self.setTextCursor(cur)
            finally:
                self._suppress_materialize = False
            return
        super().insertFromMimeData(source)

    def _local_urls_from_mime(self, source: QtGui.QMimeData) -> list[str]:
        paths: list[str] = []
        if not source.hasUrls():
            return paths
        for url in source.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path:
                    paths.append(path)
        return paths

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        paths = self._local_urls_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        paths = self._local_urls_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        paths = self._local_urls_from_mime(event.mimeData())
        if paths:
            parent = self.window()
            if isinstance(parent, ChatWindow):
                parent._send_local_path(paths[0])
                event.acceptProposedAction()
                return
        super().dropEvent(event)

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
        self._context_popup.add_action(
            "Undo", self.undo, enabled=self.document().isUndoAvailable(), tool_tip=menu_tt.TT_UNDO
        )
        self._context_popup.add_action(
            "Redo", self.redo, enabled=self.document().isRedoAvailable(), tool_tip=menu_tt.TT_REDO
        )
        self._context_popup.add_separator()
        has_selection = self.textCursor().hasSelection()
        self._context_popup.add_action("Cut", self.cut, enabled=has_selection, tool_tip=menu_tt.TT_CUT)
        self._context_popup.add_action("Copy", self.copy, enabled=has_selection, tool_tip=menu_tt.TT_COPY)
        self._context_popup.add_action(
            "Paste", self.paste, enabled=bool(self.canPaste()), tool_tip=menu_tt.TT_PASTE
        )
        self._context_popup.add_action(
            "Delete", self._delete_selection, enabled=has_selection, tool_tip=menu_tt.TT_DELETE
        )
        self._context_popup.add_separator()
        self._context_popup.add_action(
            "Select All",
            self.selectAll,
            enabled=not self.document().isEmpty(),
            tool_tip=menu_tt.TT_SELECT_ALL,
        )
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

            if self._enter_sends:
                wants_send = not shift_down
            else:
                # На macOS в Qt ⌘ = ControlModifier, физический Ctrl = MetaModifier.
                if sys.platform == "darwin":
                    command_like = bool(
                        modifiers
                        & (
                            QtCore.Qt.KeyboardModifier.MetaModifier
                            | QtCore.Qt.KeyboardModifier.ControlModifier
                        )
                    )
                    wants_send = shift_down or command_like
                else:
                    ctrl_down = bool(
                        modifiers & QtCore.Qt.KeyboardModifier.ControlModifier
                    )
                    wants_send = shift_down or ctrl_down

            if wants_send:
                self.sendRequested.emit()
                event.accept()
            else:
                # Пусть стандартный обработчик QTextEdit вставит перевод строки.
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
        self._context_popup.add_action(
            "Undo", self.undo, enabled=self.isUndoAvailable(), tool_tip=menu_tt.TT_UNDO
        )
        self._context_popup.add_action(
            "Redo", self.redo, enabled=self.isRedoAvailable(), tool_tip=menu_tt.TT_REDO
        )
        self._context_popup.add_separator()
        has_selection = self.hasSelectedText()
        self._context_popup.add_action("Cut", self.cut, enabled=has_selection, tool_tip=menu_tt.TT_CUT)
        self._context_popup.add_action("Copy", self.copy, enabled=has_selection, tool_tip=menu_tt.TT_COPY)
        self._context_popup.add_action(
            "Paste", self.paste, enabled=self._can_paste(), tool_tip=menu_tt.TT_PASTE
        )
        self._context_popup.add_action(
            "Delete", self._delete_selection, enabled=has_selection, tool_tip=menu_tt.TT_DELETE
        )
        self._context_popup.add_separator()
        self._context_popup.add_action(
            "Select All", self.selectAll, enabled=bool(self.text()), tool_tip=menu_tt.TT_SELECT_ALL
        )
        self._context_popup.show_at_global(event.globalPos())

    @QtCore.pyqtSlot()
    def _on_context_popup_closed(self) -> None:
        self._context_popup_suppress_until_ms = (
            int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 180
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


class _PeerLockIndicatorLabel(QtWidgets.QLabel):
    """Иконка замка слева от поля адреса: клик = то же, что ⋯ → Lock to peer."""

    clicked = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)


class ActionsPopupButton(QtWidgets.QFrame):
    """Строка пункта ActionsPopup: не QPushButton — на macOS дочерние QLabel внутри кнопки не рисуются."""

    clicked = QtCore.pyqtSignal()

    @staticmethod
    def _app_menu_fonts() -> tuple[QtGui.QFont, QtGui.QFont]:
        """Шрифты в пунктах (pt), без font-size в QSS px — иначе на HiDPI текст часто выглядит размытым."""
        app = QtWidgets.QApplication.instance()
        base = QtGui.QFont(app.font() if app is not None else QtGui.QFont())
        title_f = QtGui.QFont(base)
        if title_f.pointSizeF() <= 0:
            title_f.setPointSize(13)
        sc_f = QtGui.QFont(base)
        step = 1.25 if base.pointSizeF() > 10.5 else 1.0
        sc_f.setPointSizeF(max(9.0, base.pointSizeF() - step))
        return title_f, sc_f

    def __init__(
        self,
        text: str,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        shortcut_hint: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ActionsPopupItem")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._shortcut_hint = (shortcut_hint or "").strip()

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        self._title_label = QtWidgets.QLabel(text, self)
        self._title_label.setObjectName("ActionsPopupItemTitle")
        self._title_label.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        lay.addWidget(self._title_label, 1)

        self._shortcut_label = QtWidgets.QLabel(self)
        self._shortcut_label.setObjectName("ActionsPopupItemShortcut")
        self._shortcut_label.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._shortcut_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        lay.addWidget(self._shortcut_label, 0)
        self.set_shortcut_hint(self._shortcut_hint)
        tf, sf = self._app_menu_fonts()
        self._title_label.setFont(tf)
        self._shortcut_label.setFont(sf)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    def set_shortcut_hint(self, hint: Optional[str]) -> None:
        self._shortcut_hint = (hint or "").strip()
        self._shortcut_label.setText(self._shortcut_hint)
        self._shortcut_label.setVisible(bool(self._shortcut_hint))

    def setText(self, text: str) -> None:
        self._title_label.setText(text)

    def text(self) -> str:
        """Как у QPushButton — для тестов и кода, ожидающего ``.text()``."""
        return self._title_label.text()

    def apply_action_row_colors(self, *, night: bool) -> None:
        """Явные цвета на QLabel: на macOS QSS с родителя ActionsPopup часто не доходит до детей внутри QFrame."""
        if night:
            title, title_dis = "#eceff4", "#8b93a5"
            sc, sc_dis = "#8a93a8", "#5f6778"
        else:
            title, title_dis = "#1d1d1f", "#8e8e93"
            sc, sc_dis = "#5c5c63", "#aeaeb2"
        self._title_label.setStyleSheet(
            f"""
            QLabel#ActionsPopupItemTitle {{ color: {title}; }}
            QLabel#ActionsPopupItemTitle:disabled {{ color: {title_dis}; }}
            """
        )
        self._shortcut_label.setStyleSheet(
            f"""
            QLabel#ActionsPopupItemShortcut {{ color: {sc}; }}
            QLabel#ActionsPopupItemShortcut:disabled {{ color: {sc_dis}; }}
            """
        )

    def set_keyboard_row_highlight(self, on: bool) -> None:
        """Подсветка строки при навигации с клавиатуры (без Qt focus на строке — без рамок ОС)."""
        self.setProperty("keyboardRowHighlight", bool(on))
        st = self.style()
        if st is not None:
            st.unpolish(self)
            st.polish(self)

    def enterEvent(self, event: QtGui.QEnterEvent) -> None:  # type: ignore[override]
        host = getattr(self, "_actions_popup_host", None)
        if isinstance(host, ActionsPopup):
            host._cancel_keyboard_navigation_highlight()
        super().enterEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self.isEnabled()
            and self.rect().contains(event.pos())
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ActionsPopup(QtWidgets.QFrame):
    """Кастомный popup вместо QMenu для одинаковой отрисовки на всех ОС."""
    closed = QtCore.pyqtSignal()
    # Синхронно с #ActionsPopupSurface border-radius в apply_theme
    _WIN_OUTER_RADIUS = 14.0

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        # На Windows и Linux: WA_TranslucentBackground + anti-aliased paintEvent
        # (рисуем rounded rect вручную). На macOS достаточно QSS border-radius.
        self._win_menu_chrome = False
        self._linux_painted_bg = not sys.platform.startswith("darwin")
        popup_flags = QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint
        if sys.platform.startswith("win"):
            popup_flags |= QtCore.Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._dwm_patched = False
        self.setObjectName("ActionsPopupWindow")
        self.setMinimumWidth(236)
        self._popup_bg = QtGui.QColor(246, 247, 250)
        self._popup_border = QtGui.QColor(208, 211, 218)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.surface = QtWidgets.QFrame(self)
        self.surface.setObjectName("ActionsPopupSurface")
        root.addWidget(self.surface)

        self.surface_layout = QtWidgets.QVBoxLayout(self.surface)
        self.surface_layout.setContentsMargins(8, 8, 8, 8)
        self.surface_layout.setSpacing(4)
        self._actions_popup_theme_night: bool = False
        self._kb_button_index: int = -1
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.surface.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    def _iter_action_buttons(self) -> list[ActionsPopupButton]:
        out: list[ActionsPopupButton] = []
        for i in range(self.surface_layout.count()):
            w = self.surface_layout.itemAt(i).widget()
            if isinstance(w, ActionsPopupButton):
                out.append(w)
        return out

    def _cancel_keyboard_navigation_highlight(self) -> None:
        self._kb_button_index = -1
        for b in self._iter_action_buttons():
            b.set_keyboard_row_highlight(False)

    def _enabled_button_indices(self) -> list[int]:
        return [i for i, b in enumerate(self._iter_action_buttons()) if b.isEnabled()]

    def _kb_nav_move(self, delta: int) -> None:
        buttons = self._iter_action_buttons()
        enabled = self._enabled_button_indices()
        if not buttons or not enabled:
            return
        if self._kb_button_index not in enabled:
            nxt = enabled[-1] if delta < 0 else enabled[0]
        else:
            pos = enabled.index(self._kb_button_index)
            pos = (pos + delta) % len(enabled)
            nxt = enabled[pos]
        self._cancel_keyboard_navigation_highlight()
        self._kb_button_index = nxt
        buttons[nxt].set_keyboard_row_highlight(True)

    def _kb_activate_current(self) -> bool:
        if self._kb_button_index < 0:
            return False
        buttons = self._iter_action_buttons()
        if self._kb_button_index >= len(buttons):
            return False
        b = buttons[self._kb_button_index]
        if not b.isEnabled():
            return False
        b.clicked.emit()
        return True

    def _request_popup_keyboard_focus(self) -> None:
        if self.isVisible():
            self.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
            self._kb_nav_move(-1 if key == QtCore.Qt.Key.Key_Up else 1)
            event.accept()
            return
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if not (
                event.modifiers()
                & (
                    QtCore.Qt.KeyboardModifier.ShiftModifier
                    | QtCore.Qt.KeyboardModifier.ControlModifier
                    | QtCore.Qt.KeyboardModifier.AltModifier
                    | QtCore.Qt.KeyboardModifier.MetaModifier
                )
            ):
                if self._kb_activate_current():
                    event.accept()
                    return
        if key == QtCore.Qt.Key.Key_Escape and (
            event.modifiers() == QtCore.Qt.KeyboardModifier.NoModifier
        ):
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def _refresh_all_action_row_colors(self) -> None:
        for i in range(self.surface_layout.count()):
            lay_item = self.surface_layout.itemAt(i)
            w = lay_item.widget()
            if isinstance(w, ActionsPopupButton):
                w.apply_action_row_colors(night=self._actions_popup_theme_night)

    def add_action(
        self,
        text: str,
        callback: Callable[[], None],
        enabled: bool = True,
        *,
        tool_tip: Optional[str] = None,
        shortcut_hint: Optional[str] = None,
    ) -> ActionsPopupButton:
        btn = ActionsPopupButton(
            text,
            self.surface,
            shortcut_hint=shortcut_hint,
        )
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(enabled)
        if tool_tip:
            btn.setToolTip(tool_tip)
        btn._actions_popup_host = self  # type: ignore[attr-defined]
        btn.clicked.connect(lambda: (self.hide(), callback()))
        self.surface_layout.addWidget(btn)
        btn.apply_action_row_colors(night=self._actions_popup_theme_night)
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

    def _apply_win_popup_mask(self) -> None:
        if not self._win_menu_chrome:
            return
        apply_win_popup_rounded_mask(self, self._WIN_OUTER_RADIUS)

    def _apply_linux_mask(self) -> None:
        if self._linux_painted_bg and sys.platform.startswith("linux"):
            update_popup_rounded_mask(self, self._WIN_OUTER_RADIUS)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._linux_painted_bg:
            paint_popup_rounded_bg(self, self._popup_bg, self._popup_border, self._WIN_OUTER_RADIUS)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_win_popup_mask()
        self._apply_linux_mask()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._dwm_patched:
            disable_dwm_rounded_frame(self)
            self._dwm_patched = True
        if self._win_menu_chrome:
            QtCore.QTimer.singleShot(0, self._apply_win_popup_mask)
        elif self._linux_painted_bg:
            QtCore.QTimer.singleShot(0, self._apply_linux_mask)

    def show_below(self, anchor: QtWidgets.QWidget) -> None:
        self.adjustSize()
        self._apply_win_popup_mask()
        w, h = self.width(), self.height()
        self.move(
            global_position_popup_below_anchor(
                anchor, w, h, vertical_gap=6, align_right=True
            )
        )
        self.show()
        QtCore.QTimer.singleShot(0, self._request_popup_keyboard_focus)

    def show_at_global(self, global_pos: QtCore.QPoint) -> None:
        self.adjustSize()
        self._apply_win_popup_mask()
        pos = QtCore.QPoint(global_pos)
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            pos = clamp_popup_top_left_to_available_geometry(
                pos, self.width(), self.height(), geom
            )
        self.move(pos)
        self.show()
        QtCore.QTimer.singleShot(0, self._request_popup_keyboard_focus)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self._cancel_keyboard_navigation_highlight()
        super().hideEvent(event)
        self.closed.emit()

    def apply_theme(self, theme_id: str) -> None:
        night = theme_id == "night"
        self._actions_popup_theme_night = night
        items_night = """
                QFrame#ActionsPopupItem {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                    padding: 0px;
                }
                QFrame#ActionsPopupItem:hover {
                    background: rgba(255, 255, 255, 0.10);
                }
                QFrame#ActionsPopupItem:pressed {
                    background: rgba(255, 255, 255, 0.16);
                }
                QFrame#ActionsPopupItem:disabled {
                    background: transparent;
                }
                QFrame#ActionsPopupItem[keyboardRowHighlight="true"] {
                    background: rgba(255, 255, 255, 0.10);
                }
                QFrame#ActionsPopupItem[keyboardRowHighlight="true"]:disabled {
                    background: transparent;
                }
                QFrame#ActionsPopupSeparator {
                    background: #343a46;
                    max-height: 1px;
                    min-height: 1px;
                    border: none;
                    margin: 4px 8px;
                }
                """
        items_light = """
                QFrame#ActionsPopupItem {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                    padding: 0px;
                }
                QFrame#ActionsPopupItem:hover {
                    background: #e5eaf2;
                }
                QFrame#ActionsPopupItem:pressed {
                    background: #dfe6f0;
                }
                QFrame#ActionsPopupItem:disabled {
                    background: transparent;
                }
                QFrame#ActionsPopupItem[keyboardRowHighlight="true"] {
                    background: #e5eaf2;
                }
                QFrame#ActionsPopupItem[keyboardRowHighlight="true"]:disabled {
                    background: transparent;
                }
                QFrame#ActionsPopupSeparator {
                    background: #d6dce7;
                    max-height: 1px;
                    min-height: 1px;
                    border: none;
                    margin: 4px 8px;
                }
                """
        if self._win_menu_chrome:
            if night:
                shell = """
                #ActionsPopupWindow {
                    background: #22252d;
                    border: 1px solid #4a5060;
                    border-radius: 14px;
                }
                #ActionsPopupSurface {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                """
            else:
                shell = """
                #ActionsPopupWindow {
                    background: #f6f7fa;
                    border: 1px solid #c4c4c4;
                    border-radius: 14px;
                }
                #ActionsPopupSurface {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                """
            self.setStyleSheet(shell + (items_night if night else items_light))
        elif self._linux_painted_bg:
            if night:
                self._popup_bg = QtGui.QColor(34, 37, 45, 244)
                self._popup_border = QtGui.QColor(58, 62, 74)
            else:
                self._popup_bg = QtGui.QColor(246, 247, 250)
                self._popup_border = QtGui.QColor(208, 211, 218)
            self.setStyleSheet(
                """
                #ActionsPopupWindow { background: transparent; }
                #ActionsPopupSurface {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                """
                + (items_night if night else items_light)
            )
            self.update()
        elif night:
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
                """
                + items_night
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
                """
                + items_light
            )
        self._refresh_all_action_row_colors()
        self._apply_win_popup_mask()


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
            f"Use <b>{TRANSIENT_PROFILE_NAME}</b> for a one-time session, or enter a name to save your identity.<br>"
            f"<b>Security note:</b> in <b>{TRANSIENT_PROFILE_NAME}</b> mode, TOFU trust is not persisted between app restarts."
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
        self.combo.setCompleter(None)
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
        path_hint = _ClickableFolderLabel(
            f"Data folder: {profiles_path} (each profile: profiles/<name>/)",
            profiles_path,
        )
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
        _apply_dialog_theme_sheet(self, theme_id)
        theme = THEMES[_resolve_theme(theme_id)]
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
            # На macOS ⌘Q в Qt: ControlModifier + Q (StandardKey.Quit тоже).
            if event.matches(QtGui.QKeySequence.StandardKey.Quit):
                QtWidgets.QApplication.quit()
                return True
            if sys.platform == "darwin" and event.key() == QtCore.Qt.Key.Key_Q and (
                event.modifiers() == QtCore.Qt.KeyboardModifier.ControlModifier
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
        if is_transient_profile_name(selected):
            confirm = QtWidgets.QMessageBox.question(
                self,
                "Transient profile warning",
                f"You selected the transient profile '{TRANSIENT_PROFILE_NAME}'.\n\n"
                "TOFU trust pins are not persisted between app restarts in this mode.\n"
                "For persistent trust continuity, use a named profile.\n\n"
                f"Continue with '{TRANSIENT_PROFILE_NAME}' anyway?",
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
        self._badges = ""
        self._display_name = record.display_name.strip()
        self._base_sub = (record.last_preview or record.note or "").strip()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        self._full_title = record.display_name.strip() or _contact_row_address_title(
            record.addr
        )
        self._full_sub = self._base_sub
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
        self._sync_row_tooltip()

    @property
    def contact_addr(self) -> str:
        return self._addr

    def _sync_row_tooltip(self) -> None:
        addr = (self._addr or "").strip()
        name = self._display_name
        badge_text = f"\n{self._badges}" if self._badges else ""
        if name:
            self.setToolTip(f"{name}\n{addr}{badge_text}")
        else:
            self.setToolTip(f"{addr}{badge_text}")

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
        self._display_name = record.display_name.strip()
        self._base_sub = (record.last_preview or record.note or "").strip()
        self._full_title = record.display_name.strip() or _contact_row_address_title(
            record.addr
        )
        self._full_sub = self._base_sub
        self._sync_row_tooltip()
        self.updateGeometry()
        self._apply_elide()

    def set_status_badges(self, *, pinned: bool, locked: bool) -> None:
        bits: list[str] = []
        if pinned:
            bits.append("Pinned key")
        if locked:
            bits.append("Locked")
        self._badges = " · ".join(bits)
        sub_text = self._base_sub
        if self._badges:
            self._full_sub = f"{sub_text} · {self._badges}" if sub_text else self._badges
        else:
            self._full_sub = sub_text
        self._sync_row_tooltip()
        self._apply_elide()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activate.emit(self._addr)
        super().mouseReleaseEvent(event)


def _add_centered_dialog_buttons(
    vbox: QtWidgets.QVBoxLayout, bb: QtWidgets.QDialogButtonBox
) -> None:
    bb.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Fixed,
    )
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    row.addWidget(bb, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
    row.addStretch(1)
    vbox.addLayout(row)


def _add_centered_dialog_buttons_form(
    form: QtWidgets.QFormLayout, bb: QtWidgets.QDialogButtonBox
) -> None:
    bb.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Fixed,
    )
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    row.addWidget(bb, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
    row.addStretch(1)
    wrap = QtWidgets.QWidget()
    wrap.setLayout(row)
    form.addRow(wrap)


class _HistorySpinFocusFilter(QtCore.QObject):
    """Подсветка QFrame#HistoryNumericRow при фокусе на QSpinBox внутри."""

    def __init__(self, row: QtWidgets.QFrame) -> None:
        super().__init__(row)
        self._row = row

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.FocusIn:
            self._row.setProperty("focused", True)
        elif event.type() == QtCore.QEvent.Type.FocusOut:
            self._row.setProperty("focused", False)
        else:
            return False
        self._row.style().unpolish(self._row)
        self._row.style().polish(self._row)
        self._row.update()
        return False


def _history_field_label_block(title: str, hint: str) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    w.setObjectName("HistoryFieldLabelBlock")
    vl = QtWidgets.QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(4)
    vl.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
    t = QtWidgets.QLabel(title)
    t.setObjectName("HistoryFieldTitle")
    t.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    hi = QtWidgets.QLabel(hint)
    hi.setObjectName("HistoryFieldHint")
    hi.setWordWrap(True)
    hi.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
    )
    vl.addWidget(t)
    vl.addWidget(hi)
    return w


def _wrap_history_numeric_row(spin: QtWidgets.QSpinBox) -> QtWidgets.QFrame:
    spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    frame = QtWidgets.QFrame()
    frame.setObjectName("HistoryNumericRow")
    frame.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    frame.setProperty("focused", False)
    frame.setMinimumWidth(260)
    h = QtWidgets.QHBoxLayout(frame)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)
    h.addWidget(spin, 1)
    step_col = QtWidgets.QWidget()
    step_col.setObjectName("HistorySpinStepColumn")
    v = QtWidgets.QVBoxLayout(step_col)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    up = QtWidgets.QToolButton(step_col)
    up.setObjectName("HistorySpinStepUp")
    up.setText("▲")
    up.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    up.setAutoRepeat(True)
    up.setAutoRepeatDelay(400)
    up.setAutoRepeatInterval(120)
    down = QtWidgets.QToolButton(step_col)
    down.setObjectName("HistorySpinStepDown")
    down.setText("▼")
    down.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    down.setAutoRepeat(True)
    down.setAutoRepeatDelay(400)
    down.setAutoRepeatInterval(120)

    def _do_up() -> None:
        spin.stepUp()
        spin.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _do_down() -> None:
        spin.stepDown()
        spin.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    up.clicked.connect(_do_up)
    down.clicked.connect(_do_down)
    v.addWidget(up)
    v.addWidget(down)
    h.addWidget(step_col, 0)
    spin.installEventFilter(_HistorySpinFocusFilter(frame))
    return frame


class _ContactNameNoteDialog(QtWidgets.QDialog):
    """Локальные display_name и note для записи в contact book."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        *,
        display_name: str,
        note: str,
        theme_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        _apply_dialog_theme_sheet(self, theme_id)
        self.setWindowTitle("Edit name & note")
        lay = QtWidgets.QFormLayout(self)
        self._name = QtWidgets.QLineEdit(display_name)
        self._note = QtWidgets.QLineEdit(note)
        self._name.setPlaceholderText("Optional label in Saved peers list")
        self._note.setPlaceholderText("Short note (shown under title when no preview)")
        lay.addRow("Display name", self._name)
        lay.addRow("Note", self._note)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        _add_centered_dialog_buttons_form(lay, bb)

    def profile_values(self) -> tuple[str, str]:
        return self._name.text().strip(), self._note.text().strip()


class _RemoveSavedPeerDialog(QtWidgets.QDialog):
    """Подтверждение удаления контакта с опциями побочных данных."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        *,
        peer_addr: str,
        show_lock_checkbox: bool,
        show_blindbox_checkbox: bool,
        theme_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        _apply_dialog_theme_sheet(self, theme_id)
        self.setWindowTitle("Remove from saved peers")
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(
            QtWidgets.QLabel(
                "Remove this peer from Saved peers?\n\n" + peer_addr,
                self,
            )
        )
        self._cb_history = QtWidgets.QCheckBox(
            "Also delete encrypted chat history for this peer"
        )
        self._cb_pin = QtWidgets.QCheckBox("Also remove TOFU pin for this peer")
        self._cb_lock = QtWidgets.QCheckBox("Also clear profile lock (Lock to peer)")
        self._cb_lock.setVisible(show_lock_checkbox)
        self._cb_bb = QtWidgets.QCheckBox(
            "Also remove BlindBox local state file for this peer"
        )
        self._cb_bb.setVisible(show_blindbox_checkbox)
        v.addWidget(self._cb_history)
        v.addWidget(self._cb_pin)
        v.addWidget(self._cb_lock)
        v.addWidget(self._cb_bb)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Remove")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        _add_centered_dialog_buttons(v, bb)

    def options(
        self,
    ) -> tuple[bool, bool, bool, bool]:
        return (
            self._cb_history.isChecked(),
            self._cb_pin.isChecked(),
            self._cb_lock.isChecked(),
            self._cb_bb.isChecked(),
        )


class _HistoryRetentionDialog(QtWidgets.QDialog):
    """Настройка лимитов истории в стиле приложения (вместо QInputDialog)."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        *,
        max_messages: int,
        max_age_days: int,
        theme_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        _apply_dialog_theme_sheet(self, theme_id)
        self.setWindowTitle("History retention")
        self.setModal(True)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.DontWrapRows
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )
        self._sp_messages = QtWidgets.QSpinBox()
        self._sp_messages.setRange(1, 100000)
        self._sp_messages.setSingleStep(50)
        self._sp_messages.setValue(max_messages)
        self._sp_days = QtWidgets.QSpinBox()
        self._sp_days.setRange(0, 3650)
        self._sp_days.setSingleStep(1)
        self._sp_days.setValue(max_age_days)
        form.addRow(
            _history_field_label_block(
                "Max saved messages per peer",
                "Older entries are dropped when this count is exceeded.",
            ),
            _wrap_history_numeric_row(self._sp_messages),
        )
        form.addRow(
            _history_field_label_block(
                "Max age in days",
                "0 = keep only by message count above (ignore age).",
            ),
            _wrap_history_numeric_row(self._sp_days),
        )
        v.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName(
            "PrimaryButton"
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "SecondaryButton"
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        _add_centered_dialog_buttons(v, bb)

    def values(self) -> tuple[int, int]:
        return self._sp_messages.value(), self._sp_days.value()


def _router_form_section_label(text: str, *, secondary: bool = False) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setObjectName(
        "RouterSectionSecondaryTitle" if secondary else "RouterSectionTitle"
    )
    lab.setWordWrap(True)
    return lab


class _RouterSettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        *,
        settings: RouterSettings,
        bundled_status: str,
        theme_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        _apply_dialog_theme_sheet(self, theme_id)
        self.setWindowTitle("I2P router")
        self.setModal(True)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        self.setMinimumWidth(560)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.DontWrapRows
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )

        self._system_host = QtWidgets.QLineEdit(settings.system_sam_host, self)
        self._system_port = QtWidgets.QSpinBox(self)
        self._system_port.setRange(1, 65535)
        self._system_port.setValue(int(settings.system_sam_port))

        self._bundled_sam_port = QtWidgets.QSpinBox(self)
        self._bundled_sam_port.setRange(1, 65535)
        self._bundled_sam_port.setValue(int(settings.bundled_sam_port))

        self._bundled_http_proxy_port = QtWidgets.QSpinBox(self)
        self._bundled_http_proxy_port.setRange(1, 65535)
        self._bundled_http_proxy_port.setValue(int(settings.bundled_http_proxy_port))

        self._bundled_socks_proxy_port = QtWidgets.QSpinBox(self)
        self._bundled_socks_proxy_port.setRange(1, 65535)
        self._bundled_socks_proxy_port.setValue(int(settings.bundled_socks_proxy_port))

        self._system_host.setMinimumWidth(260)

        form.addRow(_router_form_section_label("Built-in router (Bundled i2pd)"))
        form.addRow("SAM port", _wrap_history_numeric_row(self._bundled_sam_port))
        form.addRow("HTTP proxy", _wrap_history_numeric_row(self._bundled_http_proxy_port))
        form.addRow("SOCKS proxy", _wrap_history_numeric_row(self._bundled_socks_proxy_port))
        form.addRow(
            _router_form_section_label(
                "External router (System i2pd)", secondary=True
            )
        )
        form.addRow("SAM host", self._system_host)
        form.addRow("SAM port", _wrap_history_numeric_row(self._system_port))

        form_wrap = QtWidgets.QWidget(self)
        form_outer = QtWidgets.QVBoxLayout(form_wrap)
        form_outer.setContentsMargins(0, 0, 0, 0)
        form_outer.addLayout(form)

        backend_panel = QtWidgets.QFrame(self)
        backend_panel.setObjectName("RouterBackendPanel")
        backend_panel.setFixedWidth(212)
        bp_lay = QtWidgets.QVBoxLayout(backend_panel)
        bp_lay.setContentsMargins(12, 12, 12, 12)
        bp_lay.setSpacing(10)
        pick_title = QtWidgets.QLabel("Router source", backend_panel)
        pick_title.setObjectName("RouterBackendPickTitle")
        pick_title.setWordWrap(True)
        bp_lay.addWidget(pick_title)

        self._opt_bundled = QtWidgets.QPushButton(
            "Bundled i2pd\nIncluded with I2PChat", backend_panel
        )
        self._opt_bundled.setObjectName("RouterBackendOption")
        self._opt_bundled.setCheckable(True)
        self._opt_bundled.setAutoDefault(False)
        self._opt_bundled.setDefault(False)
        self._opt_bundled.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self._opt_system = QtWidgets.QPushButton(
            "System i2pd\nExisting install", backend_panel
        )
        self._opt_system.setObjectName("RouterBackendOption")
        self._opt_system.setCheckable(True)
        self._opt_system.setAutoDefault(False)
        self._opt_system.setDefault(False)
        self._opt_system.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self._backend_group = QtWidgets.QButtonGroup(self)
        self._backend_group.setExclusive(True)
        self._backend_group.addButton(self._opt_bundled, 0)
        self._backend_group.addButton(self._opt_system, 1)
        if settings.backend == "bundled":
            self._opt_bundled.setChecked(True)
        else:
            self._opt_system.setChecked(True)

        bp_lay.addWidget(self._opt_bundled)
        bp_lay.addWidget(self._opt_system)

        # Right column: vertical slack split ~2:1 (top:bottom) so the panel sits slightly
        # lower than pure center — matches optical weight of the form (labels + fields).
        router_pick_column = QtWidgets.QWidget(self)
        router_pick_lay = QtWidgets.QVBoxLayout(router_pick_column)
        router_pick_lay.setContentsMargins(0, 0, 0, 0)
        router_pick_lay.setSpacing(0)
        router_pick_lay.addStretch(2)
        router_pick_lay.addWidget(
            backend_panel,
            0,
            QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        router_pick_lay.addStretch(1)

        body_row = QtWidgets.QHBoxLayout()
        body_row.setSpacing(18)
        body_row.addWidget(form_wrap, 1)
        body_row.addWidget(router_pick_column, 0)
        v.addLayout(body_row)

        self._status_label = QtWidgets.QLabel(bundled_status, self)
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("RouterStatusLabel")
        v.addWidget(self._status_label)

        actions_row = QtWidgets.QHBoxLayout()
        actions_row.setSpacing(8)
        self._btn_open_data_dir = QtWidgets.QPushButton("Open data dir", self)
        self._btn_open_data_dir.setObjectName("SecondaryButton")
        self._btn_open_log = QtWidgets.QPushButton("Open log", self)
        self._btn_open_log.setObjectName("SecondaryButton")
        self._btn_restart = QtWidgets.QPushButton("Restart bundled router", self)
        self._btn_restart.setObjectName("SecondaryButton")
        actions_row.addWidget(self._btn_open_data_dir)
        actions_row.addWidget(self._btn_open_log)
        actions_row.addWidget(self._btn_restart)
        actions_row.addStretch(1)
        v.addLayout(actions_row)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Save and apply")
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName(
            "PrimaryButton"
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "SecondaryButton"
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        _add_centered_dialog_buttons(v, bb)

        self._backend_group.idClicked.connect(lambda _i: self._sync_enabled())
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        use_system = self._opt_system.isChecked()
        self._system_host.setEnabled(use_system)
        self._system_port.setEnabled(use_system)
        self._bundled_sam_port.setEnabled(not use_system)
        self._bundled_http_proxy_port.setEnabled(not use_system)
        self._bundled_socks_proxy_port.setEnabled(not use_system)
        self._btn_restart.setEnabled(not use_system)

    def settings(self) -> RouterSettings:
        backend = "bundled" if self._opt_bundled.isChecked() else "system"
        return RouterSettings(
            backend=backend,
            system_sam_host=self._system_host.text().strip() or "127.0.0.1",
            system_sam_port=int(self._system_port.value()),
            bundled_sam_host="127.0.0.1",
            bundled_sam_port=int(self._bundled_sam_port.value()),
            bundled_http_proxy_port=int(self._bundled_http_proxy_port.value()),
            bundled_socks_proxy_port=int(self._bundled_socks_proxy_port.value()),
            bundled_control_http_port=17070,
            bundled_auto_start=True,
        )


class _BackupPassphraseDialog(QtWidgets.QDialog):
    """Парольная фраза для бэкапа в стиле приложения."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        *,
        title: str,
        confirm: bool,
        theme_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        _apply_dialog_theme_sheet(self, theme_id)
        self.setWindowTitle(title)
        self.setModal(True)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        self._pw1 = QtWidgets.QLineEdit()
        self._pw1.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        f1 = QtWidgets.QFormLayout()
        f1.addRow("Backup passphrase:", self._pw1)
        v.addLayout(f1)
        self._pw2: Optional[QtWidgets.QLineEdit] = None
        if confirm:
            self._pw2 = QtWidgets.QLineEdit()
            self._pw2.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            f2 = QtWidgets.QFormLayout()
            f2.addRow("Confirm passphrase:", self._pw2)
            v.addLayout(f2)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName(
            "PrimaryButton"
        )
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "SecondaryButton"
        )
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        _add_centered_dialog_buttons(v, bb)

    def _on_accept(self) -> None:
        s1 = self._pw1.text().strip()
        if not s1:
            QtWidgets.QMessageBox.warning(
                self, self.windowTitle(), "Passphrase must not be empty."
            )
            return
        if self._pw2 is not None:
            s2 = self._pw2.text().strip()
            if s1 != s2:
                QtWidgets.QMessageBox.warning(
                    self, self.windowTitle(), "Passphrases do not match."
                )
                return
        self.accept()

    def passphrase(self) -> str:
        return self._pw1.text().strip()


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
            mx = self._host._CONTACTS_SIDEBAR_MAX_OPEN_PX if self._host else 520
            new_w = max(mn, min(mx, self._start_sidebar_w + self._delta_sign * dx))
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


class _ComposeVerticalResizeGrip(QtWidgets.QWidget):
    """Горизонтальная полоса между лентой чата и полем ввода: тянем по вертикали."""

    def __init__(
        self,
        splitter: QtWidgets.QSplitter,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        host: Optional["ChatWindow"] = None,
    ) -> None:
        super().__init__(parent)
        self._splitter = splitter
        self._host = host
        self._dragging = False
        self._start_global_y = 0
        self._start_bottom = 0
        self.setObjectName("ComposeResizeGrip")
        gh = host._COMPOSE_SPLIT_GRIP_PX if host is not None else 4
        self.setFixedHeight(max(3, int(gh)))
        # Горизонтальная граница между панелями — вертикальное изменение размера (↑↓).
        self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_global_y = int(event.globalPosition().y())
            sizes = self._splitter.sizes()
            self._start_bottom = int(sizes[1]) if len(sizes) > 1 else 0
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging and self._host is not None:
            dy = int(event.globalPosition().y()) - self._start_global_y
            # Как у ручки QSplitter: тянем разделитель вниз — нижняя панель (ввод) растёт.
            # Глобальный Y растёт вниз; для нижнего виджета вертикального сплиттера нужен −dy,
            # иначе на полосе с tool tip направление ощущается инвертированным относительно ручки.
            self._host._compose_resize_drag_to(self._start_bottom, -dy)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging and event.button() == QtCore.Qt.MouseButton.LeftButton:
            sizes = self._splitter.sizes()
            if len(sizes) > 1:
                save_compose_split_bottom_height(int(sizes[1]))
        self._dragging = False
        super().mouseReleaseEvent(event)


class ChatSearchHitsConsoleFrame(QtWidgets.QFrame):
    """Консоль совпадений поиска: полупрозрачность и рамка через QPainter (QSS rgba на macOS часто без альфы)."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        theme_id: str,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChatSearchHitsConsole")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._theme_id = _resolve_theme(theme_id)

    def set_console_theme(self, theme_id: str) -> None:
        self._theme_id = _resolve_theme(theme_id)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        w, h = self.width(), self.height()
        if w < 3 or h < 3:
            return
        inset = 0.75
        rect = QtCore.QRectF(inset, inset, w - 2 * inset, h - 2 * inset)
        r = 10.0
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, r, r)
        if self._theme_id == "night":
            fill = QtGui.QColor(18, 22, 28, 115)
            stroke = QtGui.QColor(255, 255, 255, 82)
        else:
            fill = QtGui.QColor(232, 236, 244, 130)
            stroke = QtGui.QColor(60, 60, 67, 110)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.fillPath(path, fill)
        border_pen = QtGui.QPen(stroke)
        border_pen.setWidthF(1.35)
        border_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(border_pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawPath(path)


def _native_shortcut_text(portable_sequence: str) -> str:
    seq = QtGui.QKeySequence(portable_sequence)
    return seq.toString(QtGui.QKeySequence.SequenceFormat.NativeText)


def _tooltip_with_portable_shortcut(base: str, portable_sequence: str) -> str:
    """Дополняет tooltip нативной подписью хоткея; Ctrl+… на macOS в Qt даёт ⌘ (кроме явных Meta+…)."""
    native = _native_shortcut_text(portable_sequence)
    if not native:
        return base
    return f"{base}\n\nShortcut: {native}"


def _privacy_mode_shortcut_portable() -> str:
    """macOS: ⌘H занято системой (Hide); используем физический Control+H → в QKeySequence это Meta+H."""
    return "Meta+H" if sys.platform == "darwin" else "Ctrl+H"


# В QKeySequence строка Ctrl+… на macOS даёт ⌘ (как остальные хоткеи окна).
_CONNECT_SHORTCUT_PORTABLE = "Ctrl+1"
_DISCONNECT_SHORTCUT_PORTABLE = "Ctrl+0"
_MORE_MENU_SHORTCUT_PORTABLE = "Ctrl+."
_LOCK_TO_PEER_SHORTCUT_PORTABLE = "Ctrl+L"
_EMOJI_PICKER_SHORTCUT_PORTABLE = "Ctrl+;"


def _event_matches_portable_ctrl_only(modifiers: QtCore.Qt.KeyboardModifier) -> bool:
    """«Как Ctrl в хоткеях»: на macOS в Qt это ⌘ Command (ControlModifier), не физический Ctrl."""
    m = modifiers & (
        QtCore.Qt.KeyboardModifier.ShiftModifier
        | QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.AltModifier
        | QtCore.Qt.KeyboardModifier.MetaModifier
    )
    return m == QtCore.Qt.KeyboardModifier.ControlModifier


def _event_matches_portable_ctrl_shift(modifiers: QtCore.Qt.KeyboardModifier) -> bool:
    """⌘⇧… на macOS (Qt: Control+Shift), Ctrl+Shift… на Windows/Linux."""
    m = modifiers & (
        QtCore.Qt.KeyboardModifier.ShiftModifier
        | QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.AltModifier
        | QtCore.Qt.KeyboardModifier.MetaModifier
    )
    if sys.platform == "darwin":
        return m == (
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.ShiftModifier
        )
    return m == (
        QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.ShiftModifier
    )


def _event_matches_privacy_hotkey(modifiers: QtCore.Qt.KeyboardModifier) -> bool:
    """Privacy: физический Ctrl+H. На macOS в Qt физический Ctrl = MetaModifier (⌘ = ControlModifier)."""
    m = modifiers & (
        QtCore.Qt.KeyboardModifier.ShiftModifier
        | QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.AltModifier
        | QtCore.Qt.KeyboardModifier.MetaModifier
    )
    if sys.platform == "darwin":
        return m == QtCore.Qt.KeyboardModifier.MetaModifier
    return m == QtCore.Qt.KeyboardModifier.ControlModifier


def _linux_evdev_scan_matches(
    event: QtGui.QKeyEvent, evdev_code: int, *, allow_below: bool = True
) -> bool:
    """Скан-код как в Linux evdev; +8 часто даёт X11 keycode на той же физической клавише.

    allow_below=False: не использовать evdev−8 — иначе KEY_H (35) даёт 27, то же что
    KEY_R+8 (19+8) на X11, и Ctrl+R ошибочно срабатывает как privacy до разбора роутера.
    """
    sc = int(event.nativeScanCode())
    if sc == 0:
        return False
    if allow_below:
        return sc in (evdev_code, evdev_code + 8, evdev_code - 8)
    return sc in (evdev_code, evdev_code + 8)


def _physical_key_matches(
    event: QtGui.QKeyEvent,
    *,
    win_vk: int,
    mac_vk: int,
    linux_evdev: int,
    linux_allow_evdev_below: bool = True,
) -> bool:
    """
    Совпадение по физической позиции (раскладка US QWERTY): русская и др. не ломают хоткеи.
    Windows: nativeVirtualKey = VK_*; macOS: kVK_ANSI_*; Linux: evdev / X11 keycode.
    """
    if sys.platform == "win32":
        return int(event.nativeVirtualKey()) == win_vk
    if sys.platform == "darwin":
        return int(event.nativeVirtualKey()) == mac_vk
    return _linux_evdev_scan_matches(
        event, linux_evdev, allow_below=linux_allow_evdev_below
    )


def _compose_input_placeholder_text(*, enter_sends: bool) -> str:
    """Подсказка в поле ввода: только релевантный модификатор (⌘ на macOS, Ctrl иначе)."""
    send_mod = "⌘" if sys.platform == "darwin" else "Ctrl"
    if enter_sends:
        return (
            "Type message. Enter = send; Shift+Enter = new line. "
            "Drag and drop images or files to send."
        )
    return (
        f"Type message. Enter = new line; Shift+Enter or {send_mod}+Enter = send. "
        "Drag and drop images or files to send."
    )


class _UpdateCheckThread(QtCore.QThread):
    finished_with_result = QtCore.pyqtSignal(object)

    def __init__(
        self,
        current_version: str,
        *,
        proxy_url: Optional[str] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._current_version = current_version
        self._proxy_url = proxy_url

    def run(self) -> None:
        from i2pchat.updates.release_index import check_for_updates_sync

        self.finished_with_result.emit(
            check_for_updates_sync(self._current_version, proxy_url=self._proxy_url)
        )


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
    # Стартовая ширина до ручного ресайза: не шире этого (на широких окнах панель остаётся узкой).
    _CONTACTS_SIDEBAR_DEFAULT_OPEN_PX = 240
    # Выезжающая панель совпадений поиска (рамка + список, анимация по maximumHeight).
    _CHAT_SEARCH_CONSOLE_OPEN_H = 128
    # Верхняя граница ширины панели (как при перетаскивании разделителя).
    _CONTACTS_SIDEBAR_MAX_OPEN_PX = 520
    _CONTACTS_SIDEBAR_ANIM_MS = 200
    _SPLITTER_RIGHT_MIN_PX = 200
    _COMPOSE_SPLIT_GRIP_PX = 4
    _COMPOSE_SPLIT_MIN_CHAT_PX = 120
    _COMPOSE_SPLIT_MAX_AREA_FRAC = 0.78
    _COMPOSE_SPLIT_INPUT_MAX_LINES = 16

    def __init__(self, profile: Optional[str] = None, theme_id: str = THEME_DEFAULT) -> None:
        super().__init__()
        cp = coalesce_profile_name(profile)
        self.profile = (
            TRANSIENT_PROFILE_NAME
            if cp == TRANSIENT_PROFILE_NAME
            else ensure_valid_profile_name(cp)
        )
        self._theme_preference = _normalize_theme_preference(theme_id)
        self.theme_id = effective_theme_id(self._theme_preference)
        self.theme = THEMES[self.theme_id]
        # Показываем профиль через разделитель-точку;
        # если вдруг имя профиля уже содержит служебный маркер в конце (" •"),
        # аккуратно убираем его, чтобы заголовок не заканчивался кружком.
        clean_profile = self.profile.rstrip(" •")
        self._window_title_base = f"I2PChat @ {clean_profile}"
        self._unread_by_peer: dict[str, int] = {}
        self._status_send_in_flight = False
        # closeEvent: один раз планируем async shutdown; не вызываем event.accept()
        # до его завершения — иначе Qt закрывает окно и qasync выходит из run_forever()
        # раньше, чем отработают core/router shutdown.
        self._close_shutdown_scheduled = False
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
        self.saved_peers_context_popup = ActionsPopup(self)
        self._more_actions_suppress_until_ms = 0
        self._update_check_thread: Optional[QtCore.QThread] = None
        self.more_actions_popup.closed.connect(self._on_more_actions_popup_closed)

        self._history_loaded_for_peer: Optional[str] = None
        self._history_entries: list[HistoryEntry] = []
        self._history_dirty = False
        self._history_save_error_reported = False
        self._history_flush_timer = QtCore.QTimer(self)
        self._history_flush_timer.setInterval(60_000)
        self._history_flush_timer.timeout.connect(self._flush_history)
        self._privacy_mode_enabled = load_privacy_mode_enabled()
        # Privacy mode задаёт hide_body + quiet (focused); отдельных пунктов меню больше нет.
        if not self._privacy_mode_enabled:
            if load_notify_hide_body() or load_notify_quiet_mode():
                self._privacy_mode_enabled = True
                save_privacy_mode_enabled(True)
        self._notify_hide_body = bool(self._privacy_mode_enabled)
        self._notify_quiet_mode = bool(self._privacy_mode_enabled)
        save_notify_hide_body(self._notify_hide_body)
        save_notify_quiet_mode(self._notify_quiet_mode)

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
        _app_inst = QtWidgets.QApplication.instance()
        if _app_inst is not None:
            try:
                _app_inst.styleHints().colorSchemeChanged.connect(
                    self._on_system_color_scheme_changed
                )
            except AttributeError:
                pass
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
            f"Net:starting | Prof:{TRANSIENT_PROFILE_NAME} (T) | Link:offline | Peer:none | St:none | Sec:off | "
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
        # 0 — ещё не задавали вручную: при открытии узкая панель (см. _contacts_sidebar_open_target_px).
        self._contacts_sidebar_width_saved = 0
        self._contacts_sidebar_anim: Optional[QtCore.QVariantAnimation] = None

        # Таймер для анимации прогресс-бара
        self._transfer_timer = QtCore.QTimer(self)
        self._transfer_timer.timeout.connect(self._animate_transfer)
        self._transfer_timer.setInterval(50)

        self._history_enabled = load_history_enabled()

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
        self._chat_search_console_anim: Optional[QtCore.QPropertyAnimation] = None
        self._chat_search_hit_buttons: list[QtWidgets.QPushButton] = []
        self._chat_search_debounce = QtCore.QTimer(self)
        self._chat_search_debounce.setSingleShot(True)
        self._chat_search_debounce.setInterval(200)
        self._chat_search_debounce.timeout.connect(self._rebuild_chat_search_matches)

        # Вложенный QHBoxLayout + обёртка вокруг QLineEdit: на macOS поле часто не
        # забирает горизонтальное растяжение (остаётся пустота справа от ◀▶).
        # ChatSurface: левый margin = col_left, правый = g — компенсируем здесь,
        # чтобы строка поиска визуально имела симметричные боковые отступы.
        self._chat_search_header = QtWidgets.QWidget(chat_surface)
        _hdr_layout = QtWidgets.QVBoxLayout(self._chat_search_header)
        _hdr_layout.setContentsMargins(0, 0, 0, 5)
        _hdr_layout.setSpacing(0)
        self._chat_search_row = QtWidgets.QWidget(self._chat_search_header)
        search_h = QtWidgets.QHBoxLayout(self._chat_search_row)
        search_h.setContentsMargins(
            max(0, g - col_left), 0, 0, max(2, g // 2)
        )
        search_h.setSpacing(self._UI_GRID_PX)
        self._chat_search_field_wrap = QtWidgets.QWidget(self._chat_search_row)
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
        self._chat_search_prev = QtWidgets.QPushButton("◀", self._chat_search_row)
        self._chat_search_prev.setFixedWidth(36)
        self._chat_search_prev.setFixedHeight(_search_row_h)
        self._chat_search_prev.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._chat_search_prev.setToolTip("Previous match")
        self._chat_search_next = QtWidgets.QPushButton("▶", self._chat_search_row)
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
        _hdr_layout.addWidget(self._chat_search_row)
        self._chat_search_console = ChatSearchHitsConsoleFrame(
            chat_surface, self.theme_id
        )
        self._chat_search_console.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._chat_search_console.setMaximumHeight(0)
        self._chat_search_console.hide()
        _cs_lay = QtWidgets.QVBoxLayout(self._chat_search_console)
        _cs_lay.setContentsMargins(8, 5, 8, 7)
        _cs_lay.setSpacing(0)
        # QListWidget = QAbstractItemView: на macOS даёт вертикальные «маски» как у ленты чата.
        self._chat_search_scroll = QtWidgets.QScrollArea(self._chat_search_console)
        self._chat_search_scroll.setObjectName("ChatSearchHitsScroll")
        self._chat_search_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._chat_search_scroll.setWidgetResizable(True)
        self._chat_search_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chat_search_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._chat_search_scroll.setMaximumHeight(
            max(88, self._CHAT_SEARCH_CONSOLE_OPEN_H - 20)
        )
        self._chat_search_scroll.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        self._chat_search_scroll.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        _csv = self._chat_search_scroll.viewport()
        _csv.setAutoFillBackground(False)
        _csv.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        _hits_inner = QtWidgets.QWidget()
        _hits_inner.setObjectName("ChatSearchHitsInner")
        _hits_inner.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        _hits_inner.setAutoFillBackground(False)
        self._chat_search_hits_layout = QtWidgets.QVBoxLayout(_hits_inner)
        self._chat_search_hits_layout.setContentsMargins(2, 2, 2, 2)
        self._chat_search_hits_layout.setSpacing(3)
        self._chat_search_scroll.setWidget(_hits_inner)
        _hits_style = QtWidgets.QStyleFactory.create("Fusion")
        if _hits_style is not None:
            self._chat_search_scroll.setStyle(_hits_style)
            _hits_inner.setStyle(_hits_style)
        _fmono = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont
        )
        if not QtGui.QFontInfo(_fmono).fixedPitch():
            _fmono = QtGui.QFont("Consolas", 11)
        if _fmono.pointSize() <= 0:
            _fmono.setPointSize(11)
        elif _fmono.pointSize() > 12:
            _fmono.setPointSize(11)
        self._chat_search_hits_font = _fmono
        _cs_lay.addWidget(self._chat_search_scroll)
        self._chat_search_edit.textChanged.connect(self._schedule_chat_search_rebuild)
        self._chat_search_prev.clicked.connect(lambda: self._step_chat_search(-1))
        self._chat_search_next.clicked.connect(lambda: self._step_chat_search(1))
        chat_surface_layout.addWidget(self._chat_search_header)
        chat_surface_layout.addWidget(self._chat_search_console)
        chat_surface_layout.addWidget(self.chat_view, 1)

        # панель ввода (нижняя зона вертикального сплиттера «чат / ввод»)
        self._compose_split_bottom = QtWidgets.QWidget(self)
        input_container = QtWidgets.QWidget(self._compose_split_bottom)
        input_container.setObjectName("ComposeBar")
        input_layout = QtWidgets.QHBoxLayout(input_container)
        # Симметрично с правым краем (раньше слева был col_left — визуально уже).
        input_layout.setContentsMargins(g, g, g, g)
        input_layout.setSpacing(self._UI_GRID_PX)
        self.compose_input_wrap = ComposeInputWrapper(input_container)
        self.input_edit = MessageInputEdit(self.compose_input_wrap)
        self.compose_input_wrap.attach_input(self.input_edit)
        self.compose_input_wrap.set_emoji_shortcut_portable(_EMOJI_PICKER_SHORTCUT_PORTABLE)
        self._compose_enter_sends = load_compose_enter_sends()
        self.input_edit.set_enter_sends(self._compose_enter_sends)
        self.input_edit.setPlaceholderText(
            _compose_input_placeholder_text(enter_sends=self._compose_enter_sends)
        )
        font = self.input_edit.font()
        font.setPointSize(font.pointSize() + 1)
        self.input_edit.setFont(font)

        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setObjectName("PrimaryActionButton")

        _compose_min_h = _compose_bar_input_height_px(self.input_edit, lines=1)
        self.input_edit.setMinimumHeight(_compose_min_h)
        self.input_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.compose_input_wrap.setMinimumHeight(_compose_min_h)
        self.compose_input_wrap.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.send_button.setMinimumHeight(_compose_min_h)
        self.send_button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Minimum,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        input_layout.addWidget(self.compose_input_wrap, 1)
        input_layout.addWidget(self.send_button, 0)

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
        self.more_toolbar_button.setToolTip(
            _tooltip_with_portable_shortcut(
                menu_tt.TT_MORE_ACTIONS_BUTTON, _MORE_MENU_SHORTCUT_PORTABLE
            )
        )

        # Одна «толщина» со строкой статуса и кнопкой темы (_STATUS_ROW_HEIGHT_PX).
        actions_fixed_height = self._STATUS_ROW_HEIGHT_PX
        self.peer_lock_label = _PeerLockIndicatorLabel(self)
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
        self.contacts_list.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.contacts_list.customContextMenuRequested.connect(
            self._on_saved_peers_context_menu
        )
        sidebar_layout.addWidget(contacts_title)
        sidebar_layout.addWidget(self.contacts_list, 1)

        self.contacts_right_pack = QtWidgets.QWidget(self.contacts_splitter)
        self.right_chat_column = QtWidgets.QWidget(self.contacts_right_pack)
        right_column_layout = QtWidgets.QVBoxLayout(self.right_chat_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(self._UI_GRID_PX)

        self._compose_vertical_splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Vertical, self.right_chat_column
        )
        self._compose_vertical_splitter.setHandleWidth(0)
        self._compose_vertical_splitter.setChildrenCollapsible(False)

        _compose_col = QtWidgets.QVBoxLayout(self._compose_split_bottom)
        _compose_col.setContentsMargins(0, 0, 0, 0)
        _compose_col.setSpacing(0)
        self._compose_resize_grip = _ComposeVerticalResizeGrip(
            self._compose_vertical_splitter,
            self._compose_split_bottom,
            host=self,
        )
        self._compose_resize_grip.setToolTip("Drag to resize the message field")
        _compose_col.addWidget(self._compose_resize_grip)
        _compose_col.addWidget(input_container, 1)

        self._compose_vertical_splitter.addWidget(chat_surface)
        self._compose_vertical_splitter.addWidget(self._compose_split_bottom)
        self._compose_vertical_splitter.setStretchFactor(0, 1)
        self._compose_vertical_splitter.setStretchFactor(1, 0)
        _v_split_cursor = QtCore.Qt.CursorShape.SizeVerCursor
        for _hi in range(self._compose_vertical_splitter.count() - 1):
            self._compose_vertical_splitter.handle(_hi).setCursor(_v_split_cursor)

        right_column_layout.addWidget(self._compose_vertical_splitter, 1)
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
        self.contacts_toggle_btn.setToolTip(
            _tooltip_with_portable_shortcut("Show or hide saved peers", "Ctrl+B")
        )
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
            _app.installEventFilter(self)

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
        self.addr_edit.textChanged.connect(lambda _t: self._update_peer_lock_indicator())
        self.peer_lock_label.clicked.connect(self.on_lock_peer_clicked)
        self.addr_edit.editingFinished.connect(self._on_addr_editing_finished_for_drafts)
        self.input_edit.composeTextChanged.connect(self._on_compose_text_changed)
        self.more_actions_popup.add_action(
            "Load profile (.dat)",
            self.on_load_profile_clicked,
            tool_tip=_tooltip_with_portable_shortcut(menu_tt.TT_LOAD_PROFILE_DAT, "Ctrl+O"),
            shortcut_hint=_native_shortcut_text("Ctrl+O"),
        )
        self.more_actions_popup.add_action(
            "Send picture",
            self.on_send_pic_clicked,
            tool_tip=_tooltip_with_portable_shortcut(menu_tt.TT_SEND_PICTURE, "Ctrl+P"),
            shortcut_hint=_native_shortcut_text("Ctrl+P"),
        )
        self.more_actions_popup.add_action(
            "Send file",
            self.on_send_file_clicked,
            tool_tip=_tooltip_with_portable_shortcut(menu_tt.TT_SEND_FILE, "Ctrl+F"),
            shortcut_hint=_native_shortcut_text("Ctrl+F"),
        )
        self.more_actions_popup.add_action(
            "BlindBox diagnostics",
            self._show_blindbox_diagnostics,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_BLINDBOX_DIAGNOSTICS, "Ctrl+D"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+D"),
        )
        self.more_actions_popup.add_action(
            "Export profile backup…",
            self._export_profile_backup,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_EXPORT_PROFILE_BACKUP, "Ctrl+E"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+E"),
        )
        self.more_actions_popup.add_action(
            "Import profile backup…",
            self._import_profile_backup,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_IMPORT_PROFILE_BACKUP, "Ctrl+I"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+I"),
        )
        self.more_actions_popup.add_action(
            "Export history backup…",
            self._export_history_backup,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_EXPORT_HISTORY_BACKUP, "Ctrl+Shift+E"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+Shift+E"),
        )
        self.more_actions_popup.add_action(
            "Import history backup…",
            self._import_history_backup,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_IMPORT_HISTORY_BACKUP, "Ctrl+Shift+I"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+Shift+I"),
        )
        self.more_actions_popup.add_action(
            "Check for updates…",
            self._on_check_for_updates_clicked,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_CHECK_UPDATES, "Ctrl+U"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+U"),
        )
        self.more_actions_popup.add_action(
            "Open App dir",
            self._on_open_app_dir_clicked,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_OPEN_APP_DIR, "Ctrl+Shift+A"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+Shift+A"),
        )
        self.more_actions_popup.add_action(
            "I2P router…",
            self._open_router_settings_dialog,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_I2P_ROUTER, "Ctrl+R"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+R"),
        )
        self.more_actions_popup.add_separator()
        self.more_actions_popup.add_action(
            "Lock to peer",
            self.on_lock_peer_clicked,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_LOCK_TO_PEER, _LOCK_TO_PEER_SHORTCUT_PORTABLE
            ),
            shortcut_hint=_native_shortcut_text(_LOCK_TO_PEER_SHORTCUT_PORTABLE),
        )
        self.more_actions_popup.add_action(
            "Forget pinned peer key",
            self.on_forget_pinned_peer_key_clicked,
            tool_tip=menu_tt.TT_FORGET_PINNED_PEER_KEY,
        )
        self.more_actions_popup.add_action(
            "Copy my address",
            self.on_copy_my_addr_clicked,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_COPY_MY_ADDRESS, "Ctrl+Shift+C"
            ),
            shortcut_hint=_native_shortcut_text("Ctrl+Shift+C"),
        )
        self.more_actions_popup.add_separator()
        self._history_toggle_btn = self.more_actions_popup.add_action(
            self._history_toggle_label(),
            self._on_toggle_history_clicked,
            tool_tip=menu_tt.TT_CHAT_HISTORY_TOGGLE,
        )
        self._sync_chat_search_header_with_history()
        self.more_actions_popup.add_action(
            "Clear history", self._on_clear_history_clicked, tool_tip=menu_tt.TT_CLEAR_HISTORY
        )
        self.more_actions_popup.add_action(
            "History retention…",
            self._configure_history_retention,
            tool_tip=menu_tt.TT_HISTORY_RETENTION,
        )
        self._privacy_mode_toggle_btn = self.more_actions_popup.add_action(
            self._privacy_mode_toggle_label(),
            self._on_toggle_privacy_mode_clicked,
            tool_tip=_tooltip_with_portable_shortcut(
                menu_tt.TT_PRIVACY_MODE_TOGGLE, _privacy_mode_shortcut_portable()
            ),
            shortcut_hint=_native_shortcut_text(_privacy_mode_shortcut_portable()),
        )
        self._compose_enter_sends_toggle_btn = self.more_actions_popup.add_action(
            self._compose_enter_sends_toggle_label(),
            self._on_toggle_compose_enter_sends_clicked,
            tool_tip=menu_tt.TT_COMPOSE_ENTER_SENDS_TOGGLE,
        )
        self.more_actions_popup.add_separator()
        self._notify_sound_enabled = load_notify_sound_enabled()
        self._notify_sound_toggle_btn = self.more_actions_popup.add_action(
            self._notify_sound_toggle_label(),
            self._on_toggle_notify_sound_clicked,
            tool_tip=menu_tt.TT_NOTIFICATION_SOUND_TOGGLE,
        )
        self._setup_more_actions_shortcuts()
        self.chat_view.cancelTransferRequested.connect(self.on_cancel_transfer)
        self.chat_view.imageOpenRequested.connect(self.on_image_open_requested)
        self.chat_view.replyRequested.connect(self._on_reply_requested)
        self.chat_view.retryRequested.connect(self._on_retry_requested)

        self._router_settings: RouterSettings = load_router_settings()
        self._bundled_router_manager: Optional[BundledI2pdManager] = None
        self._active_sam_address: Optional[tuple[str, int]] = None
        self._active_http_proxy_address: Optional[tuple[str, int]] = None

        # ядро
        self.core = self._create_core(self.profile, ("127.0.0.1", 7656))
        self._load_compose_drafts_from_disk()
        self._load_contacts_book()
        self._ensure_stored_peer_in_contact_book()
        self._refresh_contacts_list()
        self._apply_startup_peer_from_book()
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self._apply_theme(self._theme_preference, persist=False)
        self._apply_contacts_sidebar_startup_state()
        self._sync_contacts_right_pack_left_margin()
        self._update_peer_lock_indicator()
        self.refresh_status_label()
        self._refresh_connection_buttons()
        QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)
        QtCore.QTimer.singleShot(0, self._balance_compose_splitter_initial)

    def _load_contacts_book(self) -> None:
        self._contact_book = load_book(_contacts_file_path_for_read(self.profile))

    def _ensure_stored_peer_in_contact_book(self) -> None:
        """Lock-пир из .dat всегда есть в Saved peers, даже если contacts.json пустой."""
        raw = (self.core.stored_peer or "").strip() or (
            peek_persisted_stored_peer(self.profile) or ""
        )
        sp = normalize_peer_address(raw)
        if not sp:
            return
        changed = False
        if remember_peer(self._contact_book, sp):
            changed = True
        if set_last_active_peer(self._contact_book, sp):
            changed = True
        if changed:
            self._save_contacts_book()

    def _save_contacts_book(self) -> None:
        save_book(_contacts_file_path_for_write(self.profile), self._contact_book)

    def _stop_contacts_sidebar_animation(self) -> None:
        if self._contacts_sidebar_anim is None:
            return
        self._contacts_sidebar_anim.stop()
        self._contacts_sidebar_anim.deleteLater()
        self._contacts_sidebar_anim = None

    def _contacts_sidebar_open_target_px(self, total: int) -> int:
        """Ширина открытой панели: сохранённая или узкий дефолт (¼ ширины, не больше DEFAULT)."""
        rmin = self._SPLITTER_RIGHT_MIN_PX
        avail = max(0, total - rmin)
        mn = self._CONTACTS_SIDEBAR_MIN_OPEN_PX
        mx = self._CONTACTS_SIDEBAR_MAX_OPEN_PX
        cap = self._CONTACTS_SIDEBAR_DEFAULT_OPEN_PX
        saved = int(self._contacts_sidebar_width_saved)
        if saved <= 0:
            quarter = max(0, total // 4)
            raw = max(mn, min(quarter, cap))
            return min(raw, mx, avail)
        return min(max(mn, saved), avail)

    def _balance_contacts_splitter_initial(self) -> None:
        total = max(400, self.contacts_splitter.width() or self.width() or 900)
        if self._contacts_sidebar_collapsed or not self.contacts_sidebar.isVisible():
            self.contacts_splitter.setSizes([0, total])
            self._sync_contacts_right_pack_left_margin()
            return
        sw = self._contacts_sidebar_open_target_px(total)
        self.contacts_splitter.setSizes([sw, total - sw])
        self._sync_contacts_right_pack_left_margin()

    def _compose_split_bottom_min_height(self) -> int:
        g = self._UI_GRID_PX
        inner = _compose_bar_input_height_px(self.input_edit, lines=1)
        return self._COMPOSE_SPLIT_GRIP_PX + 2 * g + inner

    def _compose_split_bottom_default_height(self) -> int:
        g = self._UI_GRID_PX
        inner = _compose_bar_input_height_px(self.input_edit, lines=2)
        return self._COMPOSE_SPLIT_GRIP_PX + 2 * g + inner

    def _compose_split_bottom_max_height(self, total_split: int) -> int:
        g = self._UI_GRID_PX
        inner_max = _compose_bar_input_height_px(
            self.input_edit, lines=self._COMPOSE_SPLIT_INPUT_MAX_LINES
        )
        cap = self._COMPOSE_SPLIT_GRIP_PX + 2 * g + inner_max
        frac_cap = int(max(80, total_split * self._COMPOSE_SPLIT_MAX_AREA_FRAC))
        return max(
            self._compose_split_bottom_min_height(),
            min(cap, frac_cap),
        )

    def _compose_split_clamp_and_apply(self, total: int, target_bottom: int) -> None:
        sp = getattr(self, "_compose_vertical_splitter", None)
        if sp is None:
            return
        mn = self._compose_split_bottom_min_height()
        mx = self._compose_split_bottom_max_height(total)
        min_chat = self._COMPOSE_SPLIT_MIN_CHAT_PX
        if int(total) < mn + min_chat:
            b = min(mn, int(total))
            sp.setSizes([int(total) - b, b])
            return
        b = max(mn, min(mx, int(target_bottom)))
        t = int(total) - b
        if t < min_chat:
            t = min_chat
            b = int(total) - t
            if b < mn:
                b = mn
                t = int(total) - b
        sp.setSizes([t, b])

    def _compose_resize_drag_to(self, start_bottom: int, dy: int) -> None:
        sp = getattr(self, "_compose_vertical_splitter", None)
        if sp is None:
            return
        sz = sp.sizes()
        if len(sz) < 2:
            return
        total = int(sz[0]) + int(sz[1])
        self._compose_split_clamp_and_apply(total, start_bottom + dy)

    def _balance_compose_splitter_initial(self) -> None:
        sp = getattr(self, "_compose_vertical_splitter", None)
        if sp is None:
            return
        total = sum(sp.sizes()) or sp.height() or 1
        total = max(200, int(total))
        saved = load_compose_split_bottom_height()
        default_b = self._compose_split_bottom_default_height()
        target = default_b if saved is None else int(saved)
        self._compose_split_clamp_and_apply(total, target)

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

    def _apply_peer_address_after_profile_switch(self) -> None:
        """После смены профиля выставить адрес пира из .dat / contact book.

        `refresh_status_label` заполняет поле только если addr_edit пустой; при переключении
        профиля иначе остаётся адрес предыдущего профиля — не обновляются lock UI и Saved peers.
        """
        sp = (self.core.stored_peer or "").strip()
        if sp:
            self.addr_edit.setText(sp)
            return
        lap = (self._contact_book.last_active_peer or "").strip()
        if lap:
            n = normalize_peer_address(lap)
            self.addr_edit.setText(n or lap)
            return
        self.addr_edit.clear()

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

        sw1 = self._contacts_sidebar_open_target_px(total)
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
        # clear() с setItemWidget иногда оставляет «залипшие» строки на некоторых стеках Qt;
        # takeItem снимает строки явно.
        self.contacts_list.setUpdatesEnabled(False)
        try:
            while self.contacts_list.count() > 0:
                self.contacts_list.takeItem(self.contacts_list.count() - 1)
        finally:
            self.contacts_list.setUpdatesEnabled(True)
        locked_peer = normalize_peer_address(self.core.stored_peer or "")
        for rec in self._contact_book.contacts:
            item = QtWidgets.QListWidgetItem()
            row = ContactRowWidget(rec)
            info = self.core.get_peer_trust_info(rec.addr)
            row.set_status_badges(
                pinned=bool(info and info.pinned),
                locked=bool(
                    locked_peer and normalize_peer_address(rec.addr) == locked_peer
                ),
            )
            row.activate.connect(self._on_contact_row_activated)
            self.contacts_list.addItem(item)
            self.contacts_list.setItemWidget(item, row)
            hint = row.sizeHint()
            item.setSizeHint(QtCore.QSize(hint.width(), max(56, hint.height())))
        self._sync_contacts_list_selection()
        self.contacts_list.viewport().update()

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
                "This profile is locked to a different peer. You cannot switch contacts "
                "from the list while Lock to peer is in effect.",
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

    def _on_saved_peers_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.contacts_list.itemAt(pos)
        if item is None:
            return
        w = self.contacts_list.itemWidget(item)
        if not isinstance(w, ContactRowWidget):
            return
        addr = w.contact_addr
        popup = self.saved_peers_context_popup
        popup.clear_actions()
        popup.apply_theme(self.theme_id)
        popup.add_action(
            "Edit name & note…",
            lambda a=addr: self._saved_peer_edit_name_note(a),
            tool_tip=menu_tt.TT_EDIT_NAME_NOTE,
        )
        popup.add_action(
            "Contact details…",
            lambda a=addr: self._saved_peer_contact_details(a),
            tool_tip=menu_tt.TT_CONTACT_DETAILS,
        )
        popup.add_separator()
        popup.add_action(
            "Remove from saved peers…",
            lambda a=addr: self._saved_peer_remove(a),
            tool_tip=menu_tt.TT_REMOVE_SAVED_PEER,
        )
        popup.show_at_global(self.contacts_list.mapToGlobal(pos))

    def _saved_peer_edit_name_note(self, addr: str) -> None:
        norm = normalize_peer_address(addr)
        if not norm:
            return
        rec = self._contact_book.get(norm)
        d = _ContactNameNoteDialog(
            self,
            display_name=(rec.display_name if rec else ""),
            note=(rec.note if rec else ""),
            theme_id=self.theme_id,
        )
        if d.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, note = d.profile_values()
        if set_peer_profile(self._contact_book, norm, display_name=name, note=note):
            self._save_contacts_book()
            self._refresh_contacts_list()

    def _saved_peer_contact_details(self, addr: str) -> None:
        norm = normalize_peer_address(addr) or (addr or "").strip().lower()
        if not norm:
            return
        info = self.core.get_peer_trust_info(norm)
        dlg = QtWidgets.QDialog(self)
        _apply_dialog_theme_sheet(dlg, self.theme_id)
        dlg.setWindowTitle("Contact details")
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(_contact_details_selectable_label(dlg, f"<b>Address</b><br>{norm}"))
        if info is None:
            v.addWidget(_contact_details_selectable_label(dlg, "Invalid address."))
        elif info.pinned:
            key_hex = info.signing_key_hex or ""
            short_key = f"{key_hex[:24]}…{key_hex[-16:]}" if len(key_hex) > 48 else key_hex
            v.addWidget(
                _contact_details_selectable_label(
                    dlg,
                    f"<b>TOFU</b>: pinned<br>"
                    f"Fingerprint (SHA-256, short): {info.fingerprint_short or '—'}<br>"
                    f"Signing key (hex, truncated): {short_key}",
                )
            )
        else:
            v.addWidget(
                _contact_details_selectable_label(
                    dlg, "No TOFU pin stored for this peer."
                )
            )

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        copy_btn = QtWidgets.QPushButton("Copy address", dlg)
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(norm))
        row.addWidget(copy_btn)
        if info is not None and info.pinned:
            forget_btn = QtWidgets.QPushButton("Remove pin…", dlg)

            def forget() -> None:
                dlg.accept()
                self._forget_pinned_peer_key_for_address(norm)

            forget_btn.clicked.connect(forget)
            row.addWidget(forget_btn)
        row.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close", dlg)
        close_btn.setDefault(True)
        close_btn.setAutoDefault(True)
        close_btn.clicked.connect(dlg.accept)
        row.addWidget(close_btn)
        v.addLayout(row)
        dlg.exec()

    def _forget_pinned_peer_key_for_address(self, peer_addr: str) -> None:
        if not peer_addr:
            return
        try:
            normalized = self.core._normalize_peer_addr(peer_addr)
        except Exception:
            QtWidgets.QMessageBox.warning(
                self, "Forget pinned peer key", "Invalid peer address."
            )
            return
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

    def _saved_peer_ui_targets_peer(self, norm_cb: str) -> bool:
        k = normalize_peer_addr(norm_cb)
        if not k:
            return False
        u = self.addr_edit.text().strip()
        if u and normalize_peer_addr(u) == k:
            return True
        cur_hist = self._current_history_peer()
        if cur_hist and normalize_peer_addr(cur_hist) == k:
            return True
        return False

    def _peer_matches_active_connection(self, norm_cb: str) -> bool:
        if self.core.conn is None:
            return False
        cur = (self.core.current_peer_addr or "").strip()
        if not cur:
            return False
        try:
            return normalize_peer_address(norm_cb) == normalize_peer_address(cur)
        except Exception:
            return False

    def _blindbox_state_path_for_peer_b32(self, peer_b32: str) -> str:
        host = (
            peer_b32[: -len(".b32.i2p")]
            if peer_b32.endswith(".b32.i2p")
            else peer_b32
        )
        safe = re.sub(r"[^a-z0-9._-]", "_", host.lower())
        app = get_profiles_dir()
        migrate_legacy_profile_files_if_needed(app_root=app, profile=self.profile)
        return os.path.join(
            get_profile_data_dir(self.profile, create=True, app_root=app),
            f"{self.profile}.blindbox.{safe}.json",
        )

    def _saved_peer_remove(self, addr: str) -> None:
        norm_cb = normalize_peer_address(addr)
        if not norm_cb:
            return
        if self._peer_matches_active_connection(norm_cb):
            QtWidgets.QMessageBox.warning(
                self,
                "Remove saved peer",
                "Disconnect from this peer first, then remove it from Saved peers.",
            )
            return
        stored_n = normalize_peer_address(self.core.stored_peer or "")
        show_lock = self.profile != TRANSIENT_PROFILE_NAME and bool(stored_n) and stored_n == norm_cb
        show_bb = self.profile != TRANSIENT_PROFILE_NAME
        dlg = _RemoveSavedPeerDialog(
            self,
            peer_addr=norm_cb,
            show_lock_checkbox=show_lock,
            show_blindbox_checkbox=show_bb,
            theme_id=self.theme_id,
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        del_hist, del_pin, del_lock, del_bb = dlg.options()
        if del_hist:
            delete_history(
                self.core.get_profile_data_dir(),
                self.profile,
                norm_cb,
                app_data_root=self.core.get_profiles_dir(),
            )
            if norm_cb == self._history_loaded_for_peer:
                self._history_entries = []
                self._history_dirty = False
                self._history_loaded_for_peer = None
        if del_pin:
            try:
                self.core.forget_pinned_peer_key(norm_cb)
            except Exception as e:  # pragma: no cover
                self.handle_error(f"Failed to remove pin: {e}")
        if del_lock and show_lock:
            try:
                self.core.clear_locked_peer()
            except Exception as e:  # pragma: no cover
                self.handle_error(f"Failed to clear lock: {e}")
            self._update_peer_lock_indicator()
            self._set_contacts_sidebar_collapsed(False, animated=False)
            QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)
            self.handle_system("Profile lock cleared.")
        if del_bb and show_bb:
            p = self._blindbox_state_path_for_peer_b32(norm_cb)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError as e:  # pragma: no cover
                logger.debug("blindbox state remove: %s", e)

        draft_key = normalize_peer_addr(norm_cb)
        self._compose_drafts.pop(draft_key, None)
        self._compose_drafts.pop(norm_cb, None)
        clear_unread_for_peer(self._unread_by_peer, draft_key)
        clear_unread_for_peer(self._unread_by_peer, norm_cb)

        remove_peer(self._contact_book, norm_cb)
        self._save_contacts_book()
        self._refresh_contacts_list()

        if self._saved_peer_ui_targets_peer(norm_cb):
            self._save_history_if_needed()
            self._history_flush_timer.stop()
            self._history_loaded_for_peer = None
            self._history_entries = []
            self._history_dirty = False
            self.chat_model.clear_items()
            self.addr_edit.clear()
            self._sync_compose_draft_to_peer_key(None)
        self._schedule_compose_drafts_persist()
        self._update_unread_chrome()
        self.refresh_status_label()
        self._refresh_connection_buttons()
        self.handle_system(f"Removed from saved peers: {norm_cb}")

    def _update_peer_lock_indicator(self) -> None:
        # stored_peer в ядре появляется после async init_session; до этого читаем .dat (как сайдбар при старте).
        locked = bool(
            self.core.stored_peer or peek_persisted_stored_peer(self.profile)
        )
        peer_raw = self.addr_edit.text().strip() or (self.core.current_peer_addr or "")
        info = self.core.get_peer_trust_info(peer_raw) if peer_raw else None
        light = self.theme_id == "ligth"
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = _peer_lock_indicator_pixmap(locked=locked, light_theme=light, dpr=dpr)
        self.peer_lock_label.setPixmap(pm)
        tooltip_lines = [
            (
                "Profile is locked to one peer (Lock to peer). Click for status."
                if locked
                else "Profile is not locked: you may select any saved contact. "
                "Click to lock after a verified connection (same as ⋯ → Lock to peer)."
            )
        ]
        if info is not None:
            tooltip_lines.append(
                "TOFU pin: present" if info.pinned else "TOFU pin: not stored"
            )
        self.peer_lock_label.setToolTip(
            _tooltip_with_portable_shortcut(
                "\n".join(tooltip_lines), _LOCK_TO_PEER_SHORTCUT_PORTABLE
            )
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
        connect_base = c_tip or "Establish a live secure connection to the peer."
        self.connect_button.setToolTip(
            _tooltip_with_portable_shortcut(connect_base, _CONNECT_SHORTCUT_PORTABLE)
        )
        can_disconnect = connected
        self.disconnect_button.setEnabled(can_disconnect)
        disconnect_base = (
            "End the current live session."
            if can_disconnect
            else "No active connection."
        )
        self.disconnect_button.setToolTip(
            _tooltip_with_portable_shortcut(
                disconnect_base, _DISCONNECT_SHORTCUT_PORTABLE
            )
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

    def _remove_item(self, row: int) -> None:
        self.chat_model.remove_item(row)
        if not self._chat_search_sync_suppressed:
            self._sync_chat_search_after_model_change()

    def _update_history_delivery_state(
        self,
        message_id: str,
        *,
        delivery_state: str,
        delivery_hint: str = "",
        delivery_reason: str = "",
        retryable: Optional[bool] = None,
    ) -> None:
        if not message_id:
            return
        updated = False
        for idx in range(len(self._history_entries) - 1, -1, -1):
            entry = self._history_entries[idx]
            if entry.message_id != message_id:
                continue
            self._history_entries[idx] = replace(
                entry,
                delivery_state=delivery_state,
                delivery_hint=delivery_hint or entry.delivery_hint,
                delivery_reason=delivery_reason or entry.delivery_reason,
                retryable=entry.retryable if retryable is None else retryable,
            )
            updated = True
            break
        if updated:
            self._history_dirty = True

    def _append_failed_text_message(self, text: str, *, reason: str, hint: str) -> None:
        self._append_item(
            ChatItem(
                kind="me",
                timestamp=_utc_hms_now(),
                sender="Me",
                text=text,
                delivery_state=DELIVERY_STATE_FAILED,
                delivery_route="blocked",
                delivery_hint=hint,
                delivery_reason=reason,
                retryable=(reason == "send-failed"),
                retry_kind="text",
            )
        )

    def _schedule_chat_search_rebuild(self, _t: str = "") -> None:
        if not self._history_enabled:
            return
        self._chat_search_debounce.start()

    def _sync_chat_search_header_with_history(self) -> None:
        """Строка поиска по ленте показывается только при включённом сохранении истории."""
        header = getattr(self, "_chat_search_header", None)
        if header is None:
            return
        header.setVisible(self._history_enabled)
        if not self._history_enabled:
            self._chat_search_debounce.stop()
            self._stop_chat_search_console_anim()
            self._chat_search_console.hide()
            self._chat_search_console.setMaximumHeight(0)
            self._clear_chat_search_hit_rows()
            self._chat_search_match_rows = []
            self._chat_search_cur = -1
            self._chat_search_edit.blockSignals(True)
            self._chat_search_edit.clear()
            self._chat_search_edit.blockSignals(False)
            self._chat_search_status_label.clear()
            self._chat_search_status_label.hide()
            self._chat_search_edit.setTextMargins(
                self._chat_search_lineedit_left_pad_px(), 0, 0, 0
            )
            self._chat_search_console_anim = None

    def _stop_chat_search_console_anim(self) -> None:
        anim = getattr(self, "_chat_search_console_anim", None)
        if isinstance(anim, QtCore.QPropertyAnimation):
            anim.stop()

    def _run_chat_search_console_anim(
        self,
        start_h: int,
        end_h: int,
        *,
        hide_on_finish: bool = False,
    ) -> None:
        frame = self._chat_search_console
        self._stop_chat_search_console_anim()
        anim = QtCore.QPropertyAnimation(frame, b"maximumHeight", self)
        anim.setDuration(170)
        anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        anim.setStartValue(max(0, int(start_h)))
        anim.setEndValue(max(0, int(end_h)))
        if hide_on_finish:

            def _on_done() -> None:
                if getattr(self, "_chat_search_console_anim", None) is anim:
                    self._chat_search_console_anim = None
                frame.hide()
                frame.setMaximumHeight(0)

            anim.finished.connect(_on_done)
        else:

            def _on_done_open() -> None:
                if getattr(self, "_chat_search_console_anim", None) is anim:
                    self._chat_search_console_anim = None

            anim.finished.connect(_on_done_open)
        self._chat_search_console_anim = anim
        anim.start()

    def _close_chat_search_console(self) -> None:
        frame = getattr(self, "_chat_search_console", None)
        if frame is None:
            return
        self._stop_chat_search_console_anim()
        h = int(frame.maximumHeight())
        if not frame.isVisible() and h == 0:
            return
        if h <= 0 or not frame.isVisible():
            frame.hide()
            frame.setMaximumHeight(0)
            return
        self._run_chat_search_console_anim(h, 0, hide_on_finish=True)

    def _open_chat_search_console(self) -> None:
        frame = self._chat_search_console
        open_h = self._CHAT_SEARCH_CONSOLE_OPEN_H
        self._stop_chat_search_console_anim()
        frame.show()
        cur = int(frame.maximumHeight())
        if cur >= open_h - 6:
            frame.setMaximumHeight(open_h)
            return
        self._run_chat_search_console_anim(cur, open_h)

    def _clear_chat_search_hit_rows(self) -> None:
        lay = getattr(self, "_chat_search_hits_layout", None)
        if lay is None:
            self._chat_search_hit_buttons.clear()
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._chat_search_hit_buttons.clear()

    def _rebuild_chat_search_matches(self) -> None:
        if not self._history_enabled:
            self._chat_search_debounce.stop()
            self._clear_chat_search_hit_rows()
            self._chat_search_match_rows = []
            self._chat_search_cur = -1
            self._close_chat_search_console()
            self._update_chat_search_chrome()
            return
        q = self._chat_search_edit.text().strip().casefold()
        self._clear_chat_search_hit_rows()
        self._chat_search_match_rows = []
        self._chat_search_cur = -1
        if not q:
            self._close_chat_search_console()
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
                label = head + snippet
                btn = QtWidgets.QPushButton(label, self._chat_search_scroll.widget())
                btn.setObjectName("ChatSearchHitRow")
                btn.setFlat(True)
                btn.setFont(self._chat_search_hits_font)
                btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                btn.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Fixed,
                )
                btn.clicked.connect(
                    lambda _checked=False, r=row: self._on_chat_search_hit_clicked(r)
                )
                self._chat_search_hits_layout.addWidget(btn)
                self._chat_search_hit_buttons.append(btn)
        n_matches = len(self._chat_search_match_rows)
        if n_matches == 0:
            self._close_chat_search_console()
        elif n_matches == 1:
            # Один результат: консоль только дублирует чат — прячем.
            self._close_chat_search_console()
            self._chat_search_cur = 0
            self._scroll_chat_to_row(self._chat_search_match_rows[0])
        else:
            self._open_chat_search_console()
        self._update_chat_search_chrome()

    def _escape_dismisses_chat_search(self) -> bool:
        if not getattr(self, "_history_enabled", False):
            return False
        if not self.isActiveWindow():
            return False
        header = getattr(self, "_chat_search_header", None)
        if header is None or not header.isVisible():
            return False
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None and not self.isAncestorOf(fw):
            return False
        edit = getattr(self, "_chat_search_edit", None)
        if edit is not None and edit.text().strip():
            return True
        console = getattr(self, "_chat_search_console", None)
        if console is not None and (
            console.isVisible() or int(console.maximumHeight()) > 0
        ):
            return True
        if fw is not None and header is not None and header.isAncestorOf(fw):
            return True
        return False

    def _dismiss_chat_search(self) -> None:
        edit = getattr(self, "_chat_search_edit", None)
        if edit is None:
            return
        self._chat_search_debounce.stop()
        edit.blockSignals(True)
        edit.clear()
        edit.blockSignals(False)
        self._rebuild_chat_search_matches()
        if self.chat_view is not None:
            self.chat_view.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _is_more_menu_keyboard_toggle(self, event: QtGui.QKeyEvent) -> bool:
        """Ctrl/Cmd + . — то же сочетание, что открывает меню ⋯."""
        if event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
            return False
        if not _event_matches_portable_ctrl_only(event.modifiers()):
            return False
        return _physical_key_matches(
            event, win_vk=0xBE, mac_vk=0x2F, linux_evdev=52
        )

    def _is_emoji_picker_keyboard_toggle(self, event: QtGui.QKeyEvent) -> bool:
        """Ctrl/Cmd + ; — физическая клавиша `;` (US QWERTY), раскладка не мешает."""
        if event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
            return False
        if not _event_matches_portable_ctrl_only(event.modifiers()):
            return False
        return _physical_key_matches(
            event, win_vk=0xBA, mac_vk=0x29, linux_evdev=39
        )

    def _try_layout_independent_shortcut(
        self, event: QtGui.QKeyEvent, watched: QtCore.QObject
    ) -> bool:
        """
        Хоткеи по физической клавише (US QWERTY), чтобы русская и другие раскладки
        не подменяли Qt::Key_* и не ломали QShortcut.
        """
        if not isinstance(watched, QtWidgets.QWidget):
            return False
        if watched.window() is not self:
            return False
        if event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
            return False

        mod = event.modifiers()

        if _event_matches_portable_ctrl_shift(mod):
            if _physical_key_matches(event, win_vk=0x45, mac_vk=0x0E, linux_evdev=18):
                self._export_history_backup()
                return True
            if _physical_key_matches(event, win_vk=0x49, mac_vk=0x22, linux_evdev=23):
                self._import_history_backup()
                return True
            if _physical_key_matches(event, win_vk=0x41, mac_vk=0x00, linux_evdev=30):
                self._on_open_app_dir_clicked()
                return True
            if _physical_key_matches(event, win_vk=0x43, mac_vk=0x08, linux_evdev=46):
                self.on_copy_my_addr_clicked()
                return True
            return False

        # Только Ctrl+H (физ.): нельзя после «не H» делать return False — иначе на
        # Windows/Linux любой Ctrl+буква сойдёт за «privacy-модификаторы» и остальные
        # хоткеи (Ctrl+O, …) никогда не обработаются.
        if _event_matches_privacy_hotkey(mod) and _physical_key_matches(
            event,
            win_vk=0x48,
            mac_vk=0x04,
            linux_evdev=35,
            linux_allow_evdev_below=False,
        ):
            self._on_toggle_privacy_mode_clicked()
            return True

        if not _event_matches_portable_ctrl_only(mod):
            return False

        chat_focus = watched in (
            self.chat_view,
            self.chat_view.viewport(),
        )
        if chat_focus and (
            _physical_key_matches(event, win_vk=0x43, mac_vk=0x08, linux_evdev=46)
            or event.matches(QtGui.QKeySequence.StandardKey.Copy)
        ):
            idx = self.chat_view.currentIndex()
            if idx.isValid():
                self.chat_view._copy_index_text(idx, with_meta=False)
                return True
            return False

        if _physical_key_matches(event, win_vk=0x31, mac_vk=0x12, linux_evdev=2):
            self._shortcut_connect_if_enabled()
            return True
        if _physical_key_matches(event, win_vk=0x30, mac_vk=0x1D, linux_evdev=11):
            self._shortcut_disconnect_if_enabled()
            return True
        if self._is_more_menu_keyboard_toggle(event):
            self.on_more_actions_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x4F, mac_vk=0x1F, linux_evdev=24):
            self.on_load_profile_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x50, mac_vk=0x23, linux_evdev=25):
            self.on_send_pic_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x46, mac_vk=0x03, linux_evdev=33):
            self.on_send_file_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x44, mac_vk=0x02, linux_evdev=32):
            self._show_blindbox_diagnostics()
            return True
        if _physical_key_matches(event, win_vk=0x45, mac_vk=0x0E, linux_evdev=18):
            self._export_profile_backup()
            return True
        if _physical_key_matches(event, win_vk=0x49, mac_vk=0x22, linux_evdev=23):
            self._import_profile_backup()
            return True
        if _physical_key_matches(event, win_vk=0x4C, mac_vk=0x25, linux_evdev=38):
            self.on_lock_peer_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x54, mac_vk=0x11, linux_evdev=20):
            self.on_theme_switch_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x42, mac_vk=0x0B, linux_evdev=48):
            self._toggle_contacts_sidebar()
            return True
        if _physical_key_matches(event, win_vk=0x55, mac_vk=0x20, linux_evdev=22):
            self._on_check_for_updates_clicked()
            return True
        if _physical_key_matches(event, win_vk=0x52, mac_vk=0x0F, linux_evdev=19):
            self._open_router_settings_dialog()
            return True
        if self._is_emoji_picker_keyboard_toggle(event):
            self.compose_input_wrap.toggle_emoji_picker()
            return True
        return False

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.KeyPress:
            ke = event
            if isinstance(ke, QtGui.QKeyEvent):
                if isinstance(obj, QtWidgets.QWidget):
                    tw = obj.window()
                    # Popup ⋯ — отдельное окно: при фокусе в нём главное окно не «self»,
                    # иначе повторное ⌘/. не доходило бы до _try_layout_independent_shortcut.
                    if (
                        tw is self.more_actions_popup
                        and self.more_actions_popup.isVisible()
                        and self._is_more_menu_keyboard_toggle(ke)
                    ):
                        self.on_more_actions_clicked()
                        return True
                    # Меню ⋯ — отдельное окно: watched.window() не self, иначе ⌘O/⌘P/… не доходят
                    # до _try_layout_independent_shortcut. Подставляем self как «логический» target.
                    if (
                        tw is self.more_actions_popup
                        and self.more_actions_popup.isVisible()
                        and self._try_layout_independent_shortcut(ke, self)
                    ):
                        self.more_actions_popup.hide()
                        return True
                    _emoji_pop = getattr(
                        self.compose_input_wrap, "_popup", None
                    )
                    if (
                        _emoji_pop is not None
                        and tw is _emoji_pop
                        and _emoji_pop.isVisible()
                        and self._is_emoji_picker_keyboard_toggle(ke)
                    ):
                        self.compose_input_wrap.toggle_emoji_picker()
                        return True
                    if tw is self and self._try_layout_independent_shortcut(ke, obj):
                        return True
                if ke.key() == QtCore.Qt.Key.Key_Escape and (
                    ke.modifiers() == QtCore.Qt.KeyboardModifier.NoModifier
                ):
                    _emoji_pop_esc = getattr(
                        self.compose_input_wrap, "_popup", None
                    )
                    tw_esc = (
                        obj.window()
                        if isinstance(obj, QtWidgets.QWidget)
                        else None
                    )
                    if (
                        self.more_actions_popup.isVisible()
                        and tw_esc is self.more_actions_popup
                    ):
                        self.more_actions_popup.hide()
                        return True
                    if (
                        _emoji_pop_esc is not None
                        and _emoji_pop_esc.isVisible()
                        and tw_esc is not None
                        and (tw_esc is self or tw_esc is _emoji_pop_esc)
                    ):
                        _emoji_pop_esc.hide()
                        return True
                    if self._escape_dismisses_chat_search():
                        self._dismiss_chat_search()
                        return True
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

    def _update_chat_search_hit_highlight(self) -> None:
        for i, btn in enumerate(self._chat_search_hit_buttons):
            sel = self._chat_search_cur == i
            if btn.property("hitSelected") != sel:
                btn.setProperty("hitSelected", sel)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        if 0 <= self._chat_search_cur < len(self._chat_search_hit_buttons):
            self._chat_search_scroll.ensureWidgetVisible(
                self._chat_search_hit_buttons[self._chat_search_cur]
            )

    def _step_chat_search(self, delta: int) -> None:
        if not self._history_enabled:
            return
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
        self._update_chat_search_hit_highlight()

    def _on_chat_search_hit_clicked(self, chat_row: int) -> None:
        if chat_row in self._chat_search_match_rows:
            self._chat_search_cur = self._chat_search_match_rows.index(chat_row)
        self._scroll_chat_to_row(chat_row)
        self._update_chat_search_chrome()
        self._update_chat_search_hit_highlight()

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
            sender = self.profile if self.profile != TRANSIENT_PROFILE_NAME else "Peer"
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
                HistoryEntry(
                    kind=kind,
                    text=text,
                    ts=ts_iso,
                    message_id=msg.message_id,
                    delivery_state=msg.delivery_state,
                    delivery_route=msg.delivery_route,
                    delivery_hint=msg.delivery_hint,
                    delivery_reason=msg.delivery_reason,
                    retryable=msg.retryable,
                )
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

        self._append_item(
            ChatItem(
                kind=kind,
                timestamp=ts,
                sender=sender,
                text=text,
                message_id=msg.message_id,
                delivery_state=msg.delivery_state,
                delivery_route=msg.delivery_route,
                delivery_hint=msg.delivery_hint,
                delivery_reason=msg.delivery_reason,
                retryable=msg.retryable,
                retry_kind="text" if kind == "me" and msg.retryable else None,
            )
        )
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
        t0 = time.perf_counter()
        _xfer_dbg = os.environ.get("I2PCHAT_FILE_XFER_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            self._handle_file_event_impl(info)
        finally:
            if _xfer_dbg:
                dt = time.perf_counter() - t0
                if dt >= 0.05:
                    logger.info(
                        "file xfer UI: handle_file_event took %.3fs received=%s size=%s",
                        dt,
                        info.received,
                        info.size,
                    )

    def _handle_file_event_impl(self, info: FileTransferInfo) -> None:
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
                        kind="me" if info.is_sending else "error",
                        timestamp=_utc_hms_now() if info.is_sending else "",
                        sender="Me" if info.is_sending else ("IMAGE" if self._transfer_is_image else "FILE"),
                        text=err_text,
                        delivery_state=DELIVERY_STATE_FAILED,
                        delivery_hint=err_text,
                        delivery_reason="send-failed" if info.is_sending else "transfer-failed",
                        retryable=bool(info.is_sending and info.source_path),
                        retry_kind=(
                            "image"
                            if info.is_sending and self._transfer_is_image and info.source_path
                            else "file"
                            if info.is_sending and info.source_path
                            else None
                        ),
                        retry_source_path=info.source_path,
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
                                timestamp=_utc_hms_now(),
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
                                timestamp=_utc_hms_now(),
                                sender="FILE",
                                text=f"File sent: {info.filename} ({info.size:,} bytes)",
                                file_name=info.filename,
                                is_sending=True,
                                delivery_state="sending",
                                delivery_hint="Waiting for peer delivery ACK.",
                                delivery_reason="awaiting-ack",
                                retry_source_path=info.source_path,
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
        # Не блокируем всё окно чата: можно писать в поле ввода, пока решаете принять файл.
        box.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        box.setModal(False)
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
        box.raise_()
        box.activateWindow()
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
        ts = _utc_hms_now()
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
            delivery_state="sending" if is_from_me else None,
            delivery_hint="Waiting for peer delivery ACK." if is_from_me else "",
            delivery_reason="awaiting-ack" if is_from_me else "",
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
    def handle_text_delivered(self, message_id: str) -> None:
        if not message_id:
            return
        for row in range(self.chat_model.rowCount()):
            idx = self.chat_model.index(row, 0)
            item = idx.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if (
                isinstance(item, ChatItem)
                and item.kind == "me"
                and item.message_id == message_id
            ):
                self.chat_model.update_item(
                    row,
                    replace(
                        item,
                        delivered=True,
                        delivery_state=DELIVERY_STATE_DELIVERED,
                        retryable=False,
                    ),
                )
                break
        self._update_history_delivery_state(
            message_id,
            delivery_state=DELIVERY_STATE_DELIVERED,
            delivery_hint="Message delivered by peer ACK.",
            retryable=False,
        )

    @QtCore.pyqtSlot(str)
    def handle_image_delivered(self, filename: str) -> None:
        """Галочка доставки: адресат получил картинку с этим именем."""
        for row in range(self.chat_model.rowCount()):
            idx = self.chat_model.index(row, 0)
            item = idx.data(QtCore.Qt.ItemDataRole.DisplayRole)
            if isinstance(item, ChatItem) and item.kind == "image_inline" and item.is_sending and item.file_name == filename:
                self.chat_model.update_item(
                    row,
                    replace(
                        item,
                        delivered=True,
                        delivery_state=DELIVERY_STATE_DELIVERED,
                        retryable=False,
                    ),
                )
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
                self.chat_model.update_item(
                    row,
                    replace(
                        item,
                        delivered=True,
                        delivery_state=DELIVERY_STATE_DELIVERED,
                        retryable=False,
                    ),
                )
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

    def _create_core(
        self, _profile: Optional[str], sam_address: tuple[str, int]
    ) -> I2PChatCore:
        # A/B: I2PCHAT_QT_FILE_EVENT_NOOP=1 — отключить колбэки прогресса файла (диагностика подвисаний UI).
        _file_event_noop = os.environ.get("I2PCHAT_QT_FILE_EVENT_NOOP", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        on_file = (lambda _i: None) if _file_event_noop else self.handle_file_event
        core = I2PChatCore(
            profile=self.profile,
            sam_address=sam_address,
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=on_file,
            on_file_offer=self.ask_incoming_file_accept,
            on_image_received=self.handle_image_received,
            on_inline_image_received=self.handle_inline_image_received,
            on_text_delivered=self.handle_text_delivered,
            on_image_delivered=self.handle_image_delivered,
            on_file_delivered=self.handle_file_delivered,
            on_trust_decision=self.handle_trust_decision,
            on_trust_mismatch_decision=self.handle_trust_mismatch_decision,
        )
        # динамически навешиваем колбэк уведомлений,
        # чтобы не менять публичную сигнатуру конструктора ядра
        setattr(core, "on_notify", self.handle_notify)
        return core

    async def _ensure_router_backend_ready(self) -> tuple[str, int]:
        settings = self._router_settings
        save_router_settings(settings)

        if settings.backend == "system":
            self._active_http_proxy_address = ("127.0.0.1", 4444)
            return (settings.system_sam_host, int(settings.system_sam_port))

        if self._bundled_router_manager is None:
            self._bundled_router_manager = BundledI2pdManager(settings)

        sam_address = await self._bundled_router_manager.start()
        self._active_http_proxy_address = (
            self._bundled_router_manager.http_proxy_address()
        )
        return sam_address

    async def _shutdown_router_backend(self) -> None:
        if self._bundled_router_manager is None:
            return
        try:
            await self._bundled_router_manager.stop()
        finally:
            self._bundled_router_manager = None
            self._active_sam_address = None
            self._active_http_proxy_address = None

    def _bundled_router_status_text(self) -> str:
        if self._bundled_router_manager is None:
            return "Bundled router is not running."
        try:
            host, port = self._bundled_router_manager.sam_address()
            return f"Bundled router is running. SAM: {host}:{port}"
        except Exception:
            return "Bundled router is configured but not running."

    def _open_router_data_dir_clicked(self) -> None:
        path = router_runtime_dir()
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.warning(
                self,
                "I2P router",
                f"Router data directory is not available:\n{path}",
            )
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "I2P router",
                f"Could not open directory:\n{path}",
            )
            return
        self.handle_system(f"Opened router data directory: {path}")

    def _open_router_log_clicked(self) -> None:
        path = os.path.join(router_runtime_dir(), "router.log")
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.information(
                self,
                "I2P router",
                f"Router log file does not exist yet:\n{path}",
            )
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "I2P router",
                f"Could not open log file:\n{path}",
            )
            return
        self.handle_system(f"Opened router log: {path}")

    def _restart_bundled_router_clicked(self) -> None:
        async def _restart_router_async() -> None:
            try:
                if self.core is not None:
                    await self.core.shutdown()
                await self._shutdown_router_backend()
                sam_address = await self._ensure_router_backend_ready()
                self._active_sam_address = sam_address
                self.core = self._create_core(self.profile, sam_address)
                await self.core.init_session()
                self._update_peer_lock_indicator()
                self.refresh_status_label()
                self._refresh_connection_buttons()
                self.handle_system("Bundled router restarted.")
            except Exception as e:
                logger.exception("restart bundled router failed")
                QtWidgets.QMessageBox.warning(
                    self,
                    "I2P router",
                    str(e).strip() or type(e).__name__,
                )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            QtWidgets.QMessageBox.warning(
                self,
                "I2P router",
                "No asyncio event loop (qasync). Restart the app and try again.",
            )
            return
        asyncio.create_task(_restart_router_async())

    def _active_update_proxy_url(self) -> Optional[str]:
        if self._router_settings.backend != "bundled":
            return None
        if self._active_http_proxy_address is None:
            return None
        host, port = self._active_http_proxy_address
        return f"http://{host}:{port}"

    def _open_router_settings_dialog(self) -> None:
        dlg = _RouterSettingsDialog(
            self,
            settings=self._router_settings,
            bundled_status=self._bundled_router_status_text(),
            theme_id=self.theme_id,
        )
        dlg._btn_open_data_dir.clicked.connect(self._open_router_data_dir_clicked)
        dlg._btn_open_log.clicked.connect(self._open_router_log_clicked)
        dlg._btn_restart.clicked.connect(self._restart_bundled_router_clicked)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        new_settings = dlg.settings()
        if new_settings == self._router_settings:
            return

        async def _apply_router_settings_async() -> None:
            old_settings = self._router_settings
            self._router_settings = new_settings
            save_router_settings(self._router_settings)
            restart_bundled = new_settings.backend == "bundled"
            try:
                if self.core is not None:
                    await self.core.shutdown()
                if self._bundled_router_manager is not None:
                    await self._shutdown_router_backend()
                if restart_bundled:
                    self._bundled_router_manager = None
                sam_address = await self._ensure_router_backend_ready()
                self._active_sam_address = sam_address
                self.core = self._create_core(self.profile, sam_address)
                await self.core.init_session()
                self._update_peer_lock_indicator()
                self.refresh_status_label()
                self._refresh_connection_buttons()
                self.handle_system(
                    f"I2P router backend applied: {self._router_settings.backend}"
                )
            except Exception as e:
                logger.exception("apply router settings failed")
                self._router_settings = old_settings
                save_router_settings(self._router_settings)
                try:
                    if self._bundled_router_manager is not None:
                        await self._shutdown_router_backend()
                    sam_address = await self._ensure_router_backend_ready()
                    self._active_sam_address = sam_address
                    self.core = self._create_core(self.profile, sam_address)
                    await self.core.init_session()
                    self._update_peer_lock_indicator()
                    self.refresh_status_label()
                    self._refresh_connection_buttons()
                except Exception:
                    logger.exception("router settings rollback failed")
                QtWidgets.QMessageBox.warning(
                    self,
                    "I2P router",
                    str(e).strip() or type(e).__name__,
                )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            QtWidgets.QMessageBox.warning(
                self,
                "I2P router",
                "No asyncio event loop (qasync). Restart the app and try again.",
            )
            return
        asyncio.create_task(_apply_router_settings_async())

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
        try:
            _ti = THEME_PREFERENCE_CYCLE.index(self._theme_preference)
        except ValueError:
            _ti = 0
        _next_pref = THEME_PREFERENCE_CYCLE[
            (_ti + 1) % len(THEME_PREFERENCE_CYCLE)
        ]

        def _pref_tooltip_label(pref: str) -> str:
            if pref == THEME_PREF_AUTO:
                return "system"
            return pref

        _next_lbl = _pref_tooltip_label(_next_pref)
        if self._theme_preference == THEME_PREF_AUTO:
            _cur_lbl = (
                f"system ({'dark' if self.theme_id == 'night' else 'light'})"
            )
        else:
            _cur_lbl = _pref_tooltip_label(self._theme_preference)
        # Показываем иконку текущей *эффективной* темы: ligth -> sun, night -> moon.
        icon_name = "sun.max.png" if self.theme_id == "ligth" else "moon.png"
        icon_path = _resolve_gui_icon(icon_name)
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
            _tooltip_with_portable_shortcut(
                f"Theme: {_cur_lbl}. Click for: {_next_lbl}",
                "Ctrl+T",
            )
        )

    def _on_system_color_scheme_changed(self, *_args: object) -> None:
        if sip.isdeleted(self):
            return
        if self._theme_preference != THEME_PREF_AUTO:
            return
        self._apply_theme(THEME_PREF_AUTO, persist=False)

    def _apply_theme(self, theme_pref: str, persist: bool = True) -> None:
        pref = _normalize_theme_preference(theme_pref)
        self._theme_preference = pref
        resolved = effective_theme_id(pref)
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
            save_theme(pref)
        _apply_application_tooltip_stylesheet(resolved)
        self._update_theme_switch_label()
        self.more_actions_popup.apply_theme(self.theme_id)
        self.saved_peers_context_popup.apply_theme(self.theme_id)
        self.chat_view.set_theme(self.theme_id)
        self.input_edit.set_theme(self.theme_id)
        self.compose_input_wrap.set_theme(self.theme_id)
        self.addr_edit.set_theme(self.theme_id)
        self._update_peer_lock_indicator()
        self._refresh_connection_buttons()
        self._chat_search_console.set_console_theme(self.theme_id)

    def _setup_more_actions_shortcuts(self) -> None:
        """Горячие клавиши обрабатываются в eventFilter → _try_layout_independent_shortcut
        (физические коды клавиш, чтобы русская и др. раскладки не ломали хоткеи)."""

    @QtCore.pyqtSlot()
    def _on_check_for_updates_clicked(self) -> None:
        if self._update_check_thread is not None and self._update_check_thread.isRunning():
            return
        custom_url = (os.environ.get("I2PCHAT_RELEASES_PAGE_URL") or "").strip()
        custom_proxy = (os.environ.get("I2PCHAT_UPDATE_HTTP_PROXY") or "").strip()
        need_url_ack = bool(custom_url) and not load_releases_custom_url_warn_ack()
        need_proxy_ack = bool(custom_proxy) and not load_releases_custom_proxy_warn_ack()
        if need_url_ack or need_proxy_ack:
            warn = QtWidgets.QMessageBox(self)
            warn.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            warn.setWindowTitle("Update check overrides")
            parts: list[str] = []
            if need_url_ack:
                parts.append(
                    "I2PCHAT_RELEASES_PAGE_URL is set. The update check trusts whatever that "
                    "server returns over HTTP. Only use URLs you fully trust."
                )
            if need_proxy_ack:
                parts.append(
                    "I2PCHAT_UPDATE_HTTP_PROXY is set. Update requests go through that proxy; "
                    "use only proxies you trust. Together with a custom releases URL, both "
                    "affect what you are shown as the latest version."
                )
            parts.append("See the user manual §4.12 (Verifying downloads).")
            warn.setText("\n\n".join(parts))
            warn.setStandardButtons(
                QtWidgets.QMessageBox.StandardButton.Ok
                | QtWidgets.QMessageBox.StandardButton.Cancel
            )
            warn.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Ok)
            if warn.exec() != QtWidgets.QMessageBox.StandardButton.Ok:
                return
            if need_url_ack:
                save_releases_custom_url_warn_ack()
            if need_proxy_ack:
                save_releases_custom_proxy_warn_ack()
        th = _UpdateCheckThread(
            APP_VERSION,
            proxy_url=self._active_update_proxy_url(),
            parent=self,
        )
        self._update_check_thread = th
        th.finished_with_result.connect(self._on_update_check_finished)
        th.finished.connect(th.deleteLater)
        th.start()

    @QtCore.pyqtSlot(object)
    def _on_update_check_finished(self, result: object) -> None:
        from i2pchat.updates.release_index import UpdateCheckResult, downloads_page_url

        self._update_check_thread = None
        if not isinstance(result, UpdateCheckResult):
            return
        display_message = result.message
        if result.ok and result.kind == "update_available":
            display_message += (
                "\n\nBefore installing a build you download: verify SHA256SUMS and the GPG "
                "detached signature. The in-app check only compares version numbers parsed "
                "from the release page HTML (see manual §4.12)."
            )
        elif result.ok and result.kind == "no_artifact":
            display_message += (
                "\n\nIf you download a build manually, verify SHA256SUMS and GPG "
                "(manual §4.12)."
            )
        mb = QtWidgets.QMessageBox(self)
        mb.setWindowTitle("Check for updates")
        mb.setText(display_message)
        if not result.ok:
            mb.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        else:
            mb.setIcon(QtWidgets.QMessageBox.Icon.Information)
        open_btn: Optional[QtWidgets.QAbstractButton] = None
        if (
            result.kind == "update_available"
            or result.kind == "no_artifact"
            or not result.ok
        ):
            open_btn = mb.addButton(
                "Open downloads page",
                QtWidgets.QMessageBox.ButtonRole.ActionRole,
            )
        mb.addButton(QtWidgets.QMessageBox.StandardButton.Ok)
        mb.exec()
        if open_btn is not None and mb.clickedButton() == open_btn:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(downloads_page_url()))

    @QtCore.pyqtSlot()
    def on_theme_switch_clicked(self) -> None:
        try:
            i = THEME_PREFERENCE_CYCLE.index(self._theme_preference)
        except ValueError:
            i = 0
        next_pref = THEME_PREFERENCE_CYCLE[(i + 1) % len(THEME_PREFERENCE_CYCLE)]
        self._apply_theme(next_pref, persist=True)

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

    def handle_trust_mismatch_decision(
        self,
        peer_addr: str,
        old_fingerprint: str,
        new_fingerprint: str,
        old_signing_key_hex: str,
        new_signing_key_hex: str,
    ) -> bool:
        short_addr = (peer_addr or "").strip()
        if len(short_addr) > 40:
            short_addr = f"{short_addr[:18]}...{short_addr[-18:]}"
        msg = (
            "Trusted peer signing key changed.\n\n"
            f"Peer: {short_addr}\n"
            f"Previously trusted fingerprint: {old_fingerprint}\n"
            f"New fingerprint: {new_fingerprint}\n"
            f"Old key prefix: {(old_signing_key_hex or '')[:24]}...\n"
            f"New key prefix: {(new_signing_key_hex or '')[:24]}...\n\n"
            "Only trust the new key if you have verified the change out-of-band.\n"
            "Trust and replace the pinned key?"
        )
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Trusted key changed",
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
                        blindbox_hint = (
                            "using release built-in Blind Box pair "
                            "(DEFAULT_RELEASE_BLINDBOX_ENDPOINTS in i2pchat/core/i2p_chat_core.py)"
                        )
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
                if self.profile == TRANSIENT_PROFILE_NAME:
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
            is_transient_profile=self.profile == TRANSIENT_PROFILE_NAME,
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
        text = self.input_edit.plainTextForSend().strip()
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
                self.input_edit.setPlainTextForCompose(text)
                self.input_edit.setFocus()
                if result.delivery_state == DELIVERY_STATE_FAILED:
                    self._append_failed_text_message(
                        text,
                        reason=result.reason,
                        hint=result.hint or "Message send failed.",
                    )
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
                    self.input_edit.setPlainTextForCompose(text)
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
    def _shortcut_connect_if_enabled(self) -> None:
        if self.connect_button.isEnabled():
            self.on_connect_clicked()

    @QtCore.pyqtSlot()
    def _shortcut_disconnect_if_enabled(self) -> None:
        if self.disconnect_button.isEnabled():
            self.on_disconnect_clicked()

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
        if self.profile == TRANSIENT_PROFILE_NAME:
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

    @QtCore.pyqtSlot()
    def _on_open_app_dir_clicked(self) -> None:
        app_dir = get_profiles_dir()
        try:
            os.makedirs(app_dir, exist_ok=True)
        except Exception:
            pass
        if not os.path.isdir(app_dir):
            QtWidgets.QMessageBox.warning(
                self,
                "Open App dir",
                f"App directory is not available:\n{app_dir}",
            )
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(app_dir))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "Open App dir",
                f"Could not open directory:\n{app_dir}",
            )
            return
        self.handle_system(f"Opened app directory: {app_dir}")

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
            self._compose_drafts[self._compose_draft_active_key] = self.input_edit.plainTextForSend()

    def _load_compose_drafts_from_disk(self) -> None:
        self._compose_drafts = {}
        path = _compose_drafts_file_path_for_read(self.profile)
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
                _compose_drafts_file_path_for_write(self.profile),
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
            self._compose_drafts[self._compose_draft_active_key] = self.input_edit.plainTextForSend()
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
            input_plain=self.input_edit.plainTextForSend(),
            drafts=self._compose_drafts,
        )
        self._compose_drafts = out
        self._compose_draft_active_key = active
        self.input_edit.setPlainTextForCompose(text)
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
            self.core.get_profile_data_dir(),
            self.core.profile,
            peer,
            identity_key,
            app_data_root=self.core.get_profiles_dir(),
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
                        sender = self.profile if self.profile != TRANSIENT_PROFILE_NAME else "Peer"
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
                    loaded_state = normalize_loaded_delivery_state(e.delivery_state)
                    can_retry = bool(
                        e.kind == "me"
                        and loaded_state == DELIVERY_STATE_FAILED
                        and e.retryable
                    )
                    self._append_item(
                        ChatItem(
                            kind=e.kind,
                            timestamp=ts_display,
                            sender=sender,
                            text=e.text,
                            message_id=e.message_id,
                            delivery_state=loaded_state,
                            delivery_route=e.delivery_route,
                            delivery_hint=e.delivery_hint,
                            delivery_reason=e.delivery_reason,
                            retryable=can_retry,
                            retry_kind="text" if can_retry else None,
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
        entries, _ = apply_history_retention(
            self._history_entries,
            max_messages=load_history_max_messages(),
            max_age_days=load_history_retention_days(),
        )
        if entries:
            try:
                save_history(
                    self.core.get_profile_data_dir(),
                    self.core.profile,
                    peer,
                    entries,
                    identity_key,
                    max_messages=load_history_max_messages(),
                    app_data_root=self.core.get_profiles_dir(),
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

    def _privacy_mode_toggle_label(self) -> str:
        return "Privacy mode: ON" if self._privacy_mode_enabled else "Privacy mode: OFF"

    def _compose_enter_sends_toggle_label(self) -> str:
        return (
            "Enter sends message: ON"
            if self._compose_enter_sends
            else "Enter sends message: OFF"
        )

    def _refresh_compose_placeholder_shortcuts(self) -> None:
        self.input_edit.setPlaceholderText(
            _compose_input_placeholder_text(enter_sends=self._compose_enter_sends)
        )

    @QtCore.pyqtSlot()
    def _on_toggle_compose_enter_sends_clicked(self) -> None:
        self._compose_enter_sends = not self._compose_enter_sends
        save_compose_enter_sends(self._compose_enter_sends)
        self.input_edit.set_enter_sends(self._compose_enter_sends)
        self._refresh_compose_placeholder_shortcuts()
        self._compose_enter_sends_toggle_btn.setText(
            self._compose_enter_sends_toggle_label()
        )

    @QtCore.pyqtSlot()
    def _on_toggle_notify_sound_clicked(self) -> None:
        self._notify_sound_enabled = not self._notify_sound_enabled
        save_notify_sound_enabled(self._notify_sound_enabled)
        self._notify_sound_toggle_btn.setText(self._notify_sound_toggle_label())

    @QtCore.pyqtSlot()
    def _on_toggle_privacy_mode_clicked(self) -> None:
        self._privacy_mode_enabled = not self._privacy_mode_enabled
        save_privacy_mode_enabled(self._privacy_mode_enabled)
        self._privacy_mode_toggle_btn.setText(self._privacy_mode_toggle_label())
        if self._privacy_mode_enabled:
            self._notify_hide_body = True
            self._notify_quiet_mode = True
            save_notify_hide_body(True)
            save_notify_quiet_mode(True)
            self.handle_system(
                "Privacy mode ON: tray hides message text; while this window is focused, "
                "no tray toasts or notification sounds."
            )
        else:
            self._notify_hide_body = False
            self._notify_quiet_mode = False
            save_notify_hide_body(False)
            save_notify_quiet_mode(False)
            self.handle_system("Privacy mode OFF.")

    @QtCore.pyqtSlot()
    def _configure_history_retention(self) -> None:
        dlg = _HistoryRetentionDialog(
            self,
            max_messages=load_history_max_messages(),
            max_age_days=load_history_retention_days(),
            theme_id=self.theme_id,
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        limit, days = dlg.values()
        save_history_max_messages(limit)
        save_history_retention_days(days)
        retained, _ = apply_history_retention(
            self._history_entries,
            max_messages=limit,
            max_age_days=days,
        )
        if len(retained) != len(self._history_entries):
            self._history_entries = retained
            self._history_dirty = True
        self.handle_system(
            f"History retention updated: {limit} messages per peer, {days} day(s) max age."
        )

    def _prompt_backup_passphrase(self, *, title: str, confirm: bool) -> Optional[str]:
        dlg = _BackupPassphraseDialog(
            self, title=title, confirm=confirm, theme_id=self.theme_id
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return dlg.passphrase()

    def _send_local_path(self, path: str) -> None:
        low = path.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".webp")):
            ok, err, _ = validate_image(path)
            if not ok:
                self.handle_error(err or "Invalid image file")
                return
            asyncio.create_task(self.core.send_image(path))
            return
        asyncio.create_task(self.core.send_file(path))

    @QtCore.pyqtSlot()
    def _export_profile_backup(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export profile backup",
            os.path.join(get_profiles_dir(), f"{self.profile}.i2pchat-profile-backup"),
            "I2PChat backup (*.i2pchat-profile-backup);;All Files (*)",
        )
        if not path:
            return
        passphrase = self._prompt_backup_passphrase(title="Export profile backup", confirm=True)
        if passphrase is None:
            return
        try:
            summary = export_profile_bundle(
                path,
                self.core.get_profiles_dir(),
                self.profile,
                passphrase,
                include_history=True,
            )
        except BackupError as exc:
            QtWidgets.QMessageBox.critical(self, "Export profile backup", str(exc))
            return
        self.handle_system(
            f"Profile backup exported: {summary.file_count} file(s), {summary.history_files} history file(s)."
        )

    @QtCore.pyqtSlot()
    def _import_profile_backup(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import profile backup",
            get_profiles_dir(),
            "I2PChat backup (*.i2pchat-profile-backup);;All Files (*)",
        )
        if not path:
            return
        passphrase = self._prompt_backup_passphrase(title="Import profile backup", confirm=False)
        if passphrase is None:
            return
        try:
            summary = import_profile_bundle(
                path,
                self.core.get_profiles_dir(),
                passphrase,
            )
        except BackupError as exc:
            QtWidgets.QMessageBox.critical(self, "Import profile backup", str(exc))
            return
        self.handle_system(
            f"Profile backup imported as '{summary.target_profile}' ({summary.restored_files} file(s))."
        )
        self._run_switch_profile_task(summary.target_profile)

    @QtCore.pyqtSlot()
    def _export_history_backup(self) -> None:
        history_files = list_history_file_paths(
            self.core.get_profile_data_dir(),
            self.profile,
            app_data_root=self.core.get_profiles_dir(),
        )
        if not history_files:
            QtWidgets.QMessageBox.information(
                self,
                "Export history backup",
                "No saved history files were found for the current profile.",
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export history backup",
            os.path.join(get_profiles_dir(), f"{self.profile}.i2pchat-history-backup"),
            "I2PChat history backup (*.i2pchat-history-backup);;All Files (*)",
        )
        if not path:
            return
        passphrase = self._prompt_backup_passphrase(title="Export history backup", confirm=True)
        if passphrase is None:
            return
        try:
            summary = export_history_bundle(
                path,
                self.core.get_profiles_dir(),
                self.profile,
                passphrase,
            )
        except BackupError as exc:
            QtWidgets.QMessageBox.critical(self, "Export history backup", str(exc))
            return
        self.handle_system(
            f"History backup exported: {summary.history_files} history file(s)."
        )

    @QtCore.pyqtSlot()
    def _import_history_backup(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import history backup",
            get_profiles_dir(),
            "I2PChat history backup (*.i2pchat-history-backup);;All Files (*)",
        )
        if not path:
            return
        passphrase = self._prompt_backup_passphrase(title="Import history backup", confirm=False)
        if passphrase is None:
            return
        overwrite = (
            QtWidgets.QMessageBox.question(
                self,
                "Import history backup",
                "Overwrite existing history files for matching peers?\n\n"
                "Choose Yes to overwrite, No to keep existing files and import only missing ones.",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.No,
            )
        )
        if overwrite == QtWidgets.QMessageBox.StandardButton.Cancel:
            return
        conflict_mode = "overwrite" if overwrite == QtWidgets.QMessageBox.StandardButton.Yes else "skip"
        try:
            summary = import_history_bundle(
                path,
                self.core.get_profiles_dir(),
                self.profile,
                passphrase,
                conflict_mode=conflict_mode,
            )
        except BackupError as exc:
            QtWidgets.QMessageBox.critical(self, "Import history backup", str(exc))
            return
        self.handle_system(
            f"History backup imported: restored {summary.restored_files}, skipped {summary.skipped_files}."
        )

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
        self._sync_chat_search_header_with_history()

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
        deleted = delete_history(
            self.core.get_profile_data_dir(),
            self.core.profile,
            peer,
            app_data_root=self.core.get_profiles_dir(),
        )
        if deleted:
            if peer == self._history_loaded_for_peer:
                self._history_entries = []
                self._history_dirty = False
            self.handle_system("History cleared for this peer.")
        else:
            self.handle_system("No saved history found for this peer.")

    def _show_blindbox_diagnostics(self) -> None:
        dlg = QtWidgets.QDialog(self)
        _apply_dialog_theme_sheet(dlg, self.theme_id)
        dlg.setWindowTitle("BlindBox diagnostics")
        dlg.resize(720, 580)
        layout = QtWidgets.QVBoxLayout(dlg)
        locked = self.core.blindbox_replicas_gui_locked()
        bb_on = bool(self.core.blindbox_enabled)
        intro = QtWidgets.QLabel(
            "Diagnostics for offline / delayed delivery. "
            + (
                "Replica endpoints are read-only (environment or local-auto controls the list)."
                if locked
                else (
                    "You can edit Blind Box endpoints below when BlindBox is enabled for this profile."
                    if bb_on
                    else "BlindBox is off for this profile; endpoint list is shown for reference only."
                )
            ),
            dlg,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        summary = QtWidgets.QPlainTextEdit(dlg)
        summary.setObjectName("BlindBoxDiagnosticsSummary")
        summary.setReadOnly(True)

        def refresh_summary() -> None:
            delivery = self.core.get_delivery_telemetry()
            blindbox = self.core.get_blindbox_telemetry()
            ack = self.core.get_ack_telemetry()
            selected_peer = (
                self.addr_edit.text().strip()
                or self.core.current_peer_addr
                or self.core.stored_peer
                or ""
            )
            summary.setPlainText(
                build_blindbox_diagnostics_text(
                    profile=self.profile,
                    selected_peer=selected_peer,
                    delivery=delivery,
                    blindbox=blindbox,
                    ack=ack,
                )
            )

        refresh_summary()
        summary.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(summary, 1)
        layout.addWidget(
            QtWidgets.QLabel(
                "Blind Box endpoints (one per line, e.g. *.b32.i2p:19444):",
                dlg,
            )
        )

        def _bb_replica_endpoint_lines() -> list[str]:
            if self.profile != TRANSIENT_PROFILE_NAME:
                app = get_profiles_dir()
                migrate_legacy_profile_files_if_needed(app_root=app, profile=self.profile)
                disk = load_profile_blindbox_replicas_list(
                    get_profile_data_dir(self.profile, create=False, app_root=app),
                    self.profile,
                )
                if disk:
                    return list(disk)
            return list(self.core.get_blindbox_replica_endpoints_readonly())

        def _bb_same_as_release_builtin(endpoints: list[str]) -> bool:
            if not DEFAULT_RELEASE_BLINDBOX_ENDPOINTS:
                return False
            norm = normalize_replica_endpoints(endpoints)
            want = list(DEFAULT_RELEASE_BLINDBOX_ENDPOINTS)
            return len(norm) == len(want) and set(norm) == set(want)

        def _bb_show_default_servers_note() -> bool:
            lines = _bb_replica_endpoint_lines()
            src = str(self.core.get_blindbox_telemetry().get("replicas_source") or "")
            return src == "release-builtin" or _bb_same_as_release_builtin(lines)

        def _blindbox_replica_field_text() -> str:
            lines = _bb_replica_endpoint_lines()
            if _bb_show_default_servers_note():
                return "\n".join(["# default servers", *lines])
            return "\n".join(lines)

        replica_edit = QtWidgets.QPlainTextEdit(dlg)
        replica_edit.setObjectName("BlindBoxReplicaEndpointsEdit")
        replica_edit.setPlainText(_blindbox_replica_field_text())
        _format_plaintext_hash_comment_lines(replica_edit, self.theme_id)
        can_edit = bb_on and not locked
        replica_edit.setReadOnly(not can_edit)
        _prof = (self.profile or "").strip()
        if is_transient_profile_name(_prof):
            _bb_rep_tip = menu_tt.TT_BLINDBOX_REPLICA_EDITOR_TRANSIENT_PROFILE
        elif locked:
            _bb_rep_tip = menu_tt.TT_BLINDBOX_REPLICA_EDITOR_ENV_LOCKED
        else:
            _bb_rep_tip = menu_tt.TT_BLINDBOX_REPLICA_EDITOR
        replica_edit.setToolTip(_bb_rep_tip)
        replica_edit.viewport().setToolTip(_bb_rep_tip)
        _bb_fm = replica_edit.fontMetrics()
        _bb_line = max(_bb_fm.lineSpacing(), _bb_fm.height())
        replica_edit.setMinimumHeight(_bb_line * 2 + 20)
        replica_edit.setMaximumHeight(_bb_line * 5 + 24)
        replica_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        layout.addWidget(replica_edit, 0)
        replica_edit.textChanged.connect(
            lambda: _format_plaintext_hash_comment_lines(replica_edit, self.theme_id)
        )

        def _bb_replica_auth_field_text() -> str:
            if is_transient_profile_name(self.profile):
                return ""
            app = get_profiles_dir()
            migrate_legacy_profile_files_if_needed(app_root=app, profile=self.profile)
            _, auth_disk = load_profile_blindbox_replicas_bundle(
                get_profile_data_dir(self.profile, create=False, app_root=app),
                self.profile,
            )
            if not auth_disk:
                return ""
            return "\n".join(f"{k}\t{v}" for k, v in sorted(auth_disk.items()))

        def _parse_replica_auth_tab_lines(lines: list[str]) -> dict[str, str]:
            out: dict[str, str] = {}
            for line in lines:
                s = (line or "").strip()
                if not s or s.startswith("#"):
                    continue
                if "\t" not in s:
                    continue
                ep, tok = s.split("\t", 1)
                ep = ep.strip()
                tok = tok.strip()
                if ep and tok:
                    out[ep] = tok
            return out

        _auth_cap_row = QtWidgets.QHBoxLayout()
        _auth_cap_row.setContentsMargins(0, 0, 0, 0)
        _auth_cap_row.setSpacing(6)
        _al_pre = QtWidgets.QLabel(
            "Replica auth (optional): one line per replica —",
            dlg,
        )
        _al_pre.setWordWrap(False)
        _tab_icon_path = _resolve_gui_icon("tab.png")
        _tab_pm = (
            QtGui.QPixmap(_tab_icon_path)
            if _tab_icon_path
            else QtGui.QPixmap()
        )
        _tab_ic = QtWidgets.QLabel(dlg)
        _tab_ic.setStyleSheet("background-color: transparent;")
        if not _tab_pm.isNull():
            _th = max(18, min(_bb_fm.height() + 4, 30))
            _scr = dlg.screen()
            if _scr is None:
                _app = QtWidgets.QApplication.instance()
                if _app is not None:
                    _scr = _app.primaryScreen()
            _dpr = 1.0
            if _scr is not None:
                _dpr = max(1.0, min(3.0, float(_scr.devicePixelRatio())))
            _phys_h = max(1, int(round(_th * _dpr)))
            # Через QImage: иначе scaledToHeight может «запечь» альфу → белый/светлый прямоугольник.
            _tab_pm = _scale_pixmap_to_height_preserve_alpha(_tab_pm, _phys_h)
            _tab_pm.setDevicePixelRatio(_dpr)
            _tid_auth = _resolve_theme(self.theme_id)
            _tint = QtGui.QColor(
                "#1d1d1f" if _tid_auth == "ligth" else "#f5f5f7"
            )
            _tab_pm = _tint_pixmap_with_alpha(_tab_pm, _tint)
            _tab_ic.setPixmap(_tab_pm)
            _lw = max(1, int(round(_tab_pm.width() / _tab_pm.devicePixelRatio())))
            _lh = max(1, int(round(_tab_pm.height() / _tab_pm.devicePixelRatio())))
            _tab_ic.setFixedSize(_lw, _lh)
        else:
            _tab_ic.setText("Tab")
        _tab_ic.setToolTip("Tab key on the keyboard")
        _al_post = QtWidgets.QLabel("token endpoint", dlg)
        _al_post.setWordWrap(False)
        _auth_cap_row.addWidget(_al_pre, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        _auth_cap_row.addWidget(_tab_ic, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        _auth_cap_row.addWidget(_al_post, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        _auth_cap_row.addStretch(1)
        layout.addLayout(_auth_cap_row)
        auth_edit = QtWidgets.QPlainTextEdit(dlg)
        auth_edit.setObjectName("BlindBoxReplicaAuthEdit")
        auth_edit.setPlainText(_bb_replica_auth_field_text())
        auth_edit.setReadOnly(not can_edit)
        _bb_auth_tip = menu_tt.TT_BLINDBOX_REPLICA_AUTH_EDITOR
        if is_transient_profile_name(_prof):
            _bb_auth_tip = (
                menu_tt.TT_BLINDBOX_REPLICA_EDITOR_TRANSIENT_PROFILE
                + " Per-replica auth is saved with named profiles only."
            )
        elif locked:
            _bb_auth_tip = menu_tt.TT_BLINDBOX_REPLICA_EDITOR_ENV_LOCKED
        auth_edit.setToolTip(_bb_auth_tip)
        auth_edit.viewport().setToolTip(_bb_auth_tip)
        auth_edit.setMinimumHeight(_bb_line * 2 + 16)
        auth_edit.setMaximumHeight(_bb_line * 4 + 20)
        auth_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        layout.addWidget(auth_edit, 0)

        def reload_replica_field() -> None:
            replica_edit.setPlainText(_blindbox_replica_field_text())
            auth_edit.setPlainText(_bb_replica_auth_field_text())

        example_btn = QtWidgets.QPushButton("Example server…", dlg)
        example_btn.setToolTip(
            "Step-by-step: Python, i2pd, systemd, and ~/.i2pchat-blindbox/.env (one scenario)."
        )
        example_btn.clicked.connect(
            lambda: self._show_blindbox_local_server_example_dialog(dlg)
        )
        reload_btn = QtWidgets.QPushButton("Reload", dlg)
        reload_btn.setToolTip("Reload the endpoint list from your saved profile file.")
        reload_btn.clicked.connect(reload_replica_field)
        save_btn = QtWidgets.QPushButton("Save and restart", dlg)
        save_btn.setToolTip(
            "Write endpoints and optional per-replica auth to the profile file "
            "and restart the BlindBox runtime."
        )
        save_btn.setEnabled(can_edit)

        async def _save_replicas_async() -> None:
            try:
                lines = replica_edit.toPlainText().splitlines()
                auth_map = _parse_replica_auth_tab_lines(
                    auth_edit.toPlainText().splitlines()
                )
                err = await self.core.apply_blindbox_replica_endpoints(
                    lines, auth_map
                )
            except Exception as e:
                logger.exception("apply_blindbox_replica_endpoints failed")
                QtWidgets.QMessageBox.warning(
                    dlg,
                    "BlindBox replicas",
                    str(e).strip() or type(e).__name__,
                )
                return
            if err:
                QtWidgets.QMessageBox.warning(dlg, "BlindBox replicas", err)
            else:
                refresh_summary()
                reload_replica_field()

        def _schedule_save_replicas() -> None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                QtWidgets.QMessageBox.warning(
                    dlg,
                    "BlindBox replicas",
                    "No asyncio event loop (qasync). Restart the app and try again.",
                )
                return
            asyncio.create_task(_save_replicas_async())

        save_btn.clicked.connect(_schedule_save_replicas)
        close_btn = QtWidgets.QPushButton("Close", dlg)
        close_btn.clicked.connect(dlg.accept)
        layout.addSpacing(8)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(example_btn)
        row.addStretch(1)
        row.addWidget(reload_btn)
        row.addStretch(1)
        row.addWidget(save_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
        dlg.exec()

    def _show_blindbox_local_server_example_dialog(
        self, parent: QtWidgets.QWidget
    ) -> None:
        sub = QtWidgets.QDialog(parent)
        _apply_dialog_theme_sheet(sub, self.theme_id)
        sub.setWindowTitle("Blind Box setup examples")
        sub.resize(680, 520)
        v = QtWidgets.QVBoxLayout(sub)
        _bb_example_pad = 8  # same as tab page pl margins — align footer with editor block
        tabs = QtWidgets.QTabWidget(sub)
        tabs.setObjectName("BlindBoxExampleTabWidget")
        tabs.setDocumentMode(True)
        _bb_tab_bar = tabs.tabBar()
        _bb_tab_bar.setUsesScrollButtons(False)
        _bb_tab_bar.setExpanding(False)
        _bb_tab_bar.setDrawBase(False)

        def _tab_page(note: str, body_text: str) -> tuple[QtWidgets.QWidget, QtWidgets.QPlainTextEdit]:
            page = QtWidgets.QWidget(sub)
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(
                _bb_example_pad,
                _bb_example_pad,
                _bb_example_pad,
                _bb_example_pad,
            )
            hl = QtWidgets.QLabel(note, page)
            hl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            hl.setWordWrap(True)
            hl.setOpenExternalLinks(False)
            pl.addWidget(hl)
            te = QtWidgets.QPlainTextEdit(page)
            te.setObjectName("BlindBoxExampleSourceEdit")
            te.setReadOnly(True)
            te.setPlainText(body_text)
            _format_plaintext_hash_comment_lines(te, self.theme_id)
            te.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            pl.addWidget(te, 1)
            return page, te

        install_page, install_edit = _tab_page(
            get_production_daemon_package_note(),
            get_production_daemon_one_shot_install_source(),
        )
        tabs.addTab(install_page, "install.sh")
        i2p_page, i2p_edit = _tab_page(
            get_i2pd_blindbox_tunnel_example_note(),
            get_i2pd_blindbox_tunnel_example_source(),
        )
        tabs.addTab(i2p_page, "I2pd")
        v.addWidget(tabs, 1)
        edits = (
            install_edit,
            i2p_edit,
        )
        v.addSpacing(6)
        brow = QtWidgets.QHBoxLayout()
        brow.setSpacing(10)
        brow.setContentsMargins(_bb_example_pad, 0, _bb_example_pad, 0)
        get_install_btn = QtWidgets.QPushButton("Get install", sub)
        get_install_btn.setToolTip(
            "Save the one-shot install.sh locally so you can copy it to a server and run it there."
        )

        def _save_blindbox_install_script() -> None:
            default_dir = os.path.expanduser("~")
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                sub,
                "Save BlindBox install.sh",
                os.path.join(default_dir, "install.sh"),
                "Shell script (*.sh);;All Files (*)",
            )
            if not path:
                return
            try:
                script_text = get_production_daemon_one_shot_install_source()
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(script_text)
                try:
                    os.chmod(path, 0o755)
                except OSError:
                    pass
            except OSError as exc:
                QtWidgets.QMessageBox.critical(
                    sub,
                    "Get install",
                    str(exc).strip() or type(exc).__name__,
                )
                return
            QtWidgets.QMessageBox.information(
                sub,
                "Get install",
                f"Saved install.sh to:\n{path}\n\nCopy it to your server and run:\n\nsudo bash install.sh",
            )

        get_install_btn.clicked.connect(_save_blindbox_install_script)
        copy_curl_btn = QtWidgets.QPushButton("Copy curl", sub)
        copy_curl_btn.setToolTip(
            "Copy the one-liner: download install.sh from GitHub and run it on the server."
        )
        copy_curl_btn.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(
                get_production_daemon_one_shot_install_curl_command()
            )
        )
        copy_btn = QtWidgets.QPushButton("Copy all", sub)
        copy_btn.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(
                edits[tabs.currentIndex()].toPlainText()
            )
        )
        brow.addStretch(1)
        brow.addWidget(get_install_btn)
        brow.addSpacing(4)
        brow.addWidget(copy_curl_btn)
        brow.addSpacing(4)
        brow.addWidget(copy_btn)
        close_sub = QtWidgets.QPushButton("Close", sub)
        close_sub.clicked.connect(sub.accept)
        brow.addWidget(close_sub)
        v.addLayout(brow)
        sub.exec()

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
        self._forget_pinned_peer_key_for_address(peer_addr)

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
        dest_path = nested_profile_dat_path(profiles_dir, target_base)
        if os.path.abspath(path) != os.path.abspath(dest_path):
            try:
                target_base = import_profile_dat_atomic(path, profiles_dir, source_base)
                dest_path = nested_profile_dat_path(profiles_dir, target_base)
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

        # Следующий кадр: гарантируем активный qasync-цикл и избегаем re-entrancy из меню.
        QtCore.QTimer.singleShot(
            0, lambda p=target_base: self._run_switch_profile_task(p)
        )

    def _run_switch_profile_task(self, profile_name: str) -> None:
        async def _run() -> None:
            try:
                await self.switch_profile(profile_name)
            except Exception as e:  # pragma: no cover - GUI path
                logger.exception("switch_profile failed")
                QtWidgets.QMessageBox.critical(
                    self,
                    "Load .dat",
                    f"Не удалось переключить профиль:\n{type(e).__name__}: {e}",
                )

        try:
            asyncio.get_event_loop().create_task(_run())
        except RuntimeError:
            logger.exception("switch_profile: no asyncio event loop")
            QtWidgets.QMessageBox.critical(
                self,
                "Load .dat",
                "Нет активного asyncio-цикла (qasync). Перезапустите приложение.",
            )

    def _deferred_saved_peers_refresh_after_switch(self) -> None:
        """Повторная синхронизация Saved peers со следующим тиком event loop (обход залипания Qt)."""
        try:
            self._load_contacts_book()
            self._ensure_stored_peer_in_contact_book()
            self._refresh_contacts_list()
            self._sync_contacts_list_selection()
            self._update_peer_lock_indicator()
        except Exception:
            logger.exception("deferred saved peers refresh after profile switch")

    async def switch_profile(self, profile: str) -> None:
        """Переключиться на другой профиль (.dat)."""
        profile_in = ensure_valid_profile_name(profile)
        profile_resolved = (
            TRANSIENT_PROFILE_NAME
            if is_transient_profile_name(profile_in)
            else profile_in
        )
        logger.info("switch_profile: -> %r", profile_resolved)
        self._flush_compose_drafts_to_disk()
        self._save_history_if_needed()
        self._history_flush_timer.stop()
        await self.core.shutdown()
        self._history_loaded_for_peer = None
        self._history_entries = []
        self._history_dirty = False
        self.chat_model.clear_items()
        self.profile = profile_resolved
        clean_profile = self.profile.rstrip(" •")
        self._window_title_base = f"I2PChat @ {clean_profile}"
        self._unread_by_peer = {}
        sam_address = await self._ensure_router_backend_ready()
        self._active_sam_address = sam_address
        self.core = self._create_core(self.profile, sam_address)
        # До init_session() accept_loop не крутится; иначе handle_peer_changed во время
        # await внутри init_session сохранит в файл НОВОГО профиля ещё СТАРУЮ книгу в памяти.
        self._load_contacts_book()
        self._load_compose_drafts_from_disk()
        self._compose_draft_active_key = None
        self._update_unread_chrome()
        self.refresh_status_label()
        self._refresh_connection_buttons()
        await self.core.init_session()
        # После init — снова с диска (возможны записи книги во время длинной инициализации).
        self._load_contacts_book()
        self._ensure_stored_peer_in_contact_book()
        self._apply_peer_address_after_profile_switch()
        self._sync_compose_draft_to_peer_key(self._compose_peer_key_from_ui())
        self._refresh_contacts_list()
        self._apply_contacts_sidebar_startup_state()
        self._sync_contacts_list_selection()
        self._update_peer_lock_indicator()
        self.refresh_status_label()
        self._refresh_connection_buttons()
        QtCore.QTimer.singleShot(0, self._deferred_saved_peers_refresh_after_switch)
        QtCore.QTimer.singleShot(0, self._balance_contacts_splitter_initial)

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
        cur = self.input_edit.plainTextForSend()
        sep = "\n\n" if cur.strip() else ""
        self.input_edit.setPlainTextForCompose(f"{cur}{sep}{block}")
        self.input_edit.setFocus()
        self._on_compose_text_changed()

    @QtCore.pyqtSlot(int)
    def _on_retry_requested(self, row: int) -> None:
        item = self.chat_model.item_at(row)
        if item is None or not item.retryable:
            return
        if item.retry_kind == "text":
            self._remove_item(row)
            asyncio.create_task(self._send_text_ui_flow(item.text))
            return
        if item.retry_kind == "file" and item.retry_source_path:
            self._remove_item(row)
            asyncio.create_task(self.core.send_file(item.retry_source_path))
            return
        if item.retry_kind == "image" and item.retry_source_path:
            self._remove_item(row)
            asyncio.create_task(self.core.send_image(item.retry_source_path))

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
        sam_address = await self._ensure_router_backend_ready()
        self._active_sam_address = sam_address
        self.core = self._create_core(self.profile, sam_address)
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
        if self._close_shutdown_scheduled:
            event.ignore()
            return
        self._close_shutdown_scheduled = True
        # Не закрываем окно в Qt до конца async shutdown — иначе цикл событий
        # может завершиться до await core/router (Windows/qasync).
        event.ignore()
        _app = QtWidgets.QApplication.instance()
        if _app is not None:
            _app.removeEventFilter(self)
        self._history_flush_timer.stop()
        self._compose_drafts_save_timer.stop()
        self._flush_compose_drafts_to_disk()
        self._save_history_if_needed()

        loop = asyncio.get_event_loop()

        async def _shutdown() -> None:
            try:
                try:
                    if self.core is not None:
                        await asyncio.wait_for(self.core.shutdown(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("core shutdown timed out during closeEvent")
                except Exception:
                    logger.exception("core shutdown failed during closeEvent")
                try:
                    await asyncio.wait_for(self._shutdown_router_backend(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("bundled router shutdown timed out during closeEvent")
                except Exception:
                    logger.exception("bundled router shutdown failed during closeEvent")
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


def main() -> None:
    """Точка входа без qasync.run, чтобы избежать падений при завершении."""
    # Сразу убираем плоскую раскладку для всех профилей с `<имя>.dat` в корне —
    # не только для того, который откроют в этой сессии.
    migrate_all_legacy_profiles_if_needed()
    if hasattr(sip, "setdestroyonexit"):
        sip.setdestroyonexit(False)

    # На macOS отключаем native menu windows, иначе вокруг QMenu может
    # появляться системная прямоугольная рамка поверх наших скруглений.
    if sys.platform == "darwin":
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_DontUseNativeMenuWindows, True
        )

    # Создаём единственный экземпляр QApplication (подкласс перехватывает QHelpEvent ToolTip).
    # I2PChatQApplication перехватывает notify() для кастомных tooltip'ов, но на Linux
    # это блокирует конструирование тяжёлого ChatWindow (каждое событие проходит через Python).
    # На Linux используем обычный QApplication + monkey-patch QToolTip (легковесно).
    if sys.platform.startswith("linux"):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance() or I2PChatQApplication(sys.argv)
    apply_tooltip_handling(app)

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
    _apply_application_tooltip_stylesheet(saved_theme)

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
            selected_theme = _normalize_theme_preference(
                sys.argv[2].strip().lower()
            )
            save_theme(selected_theme)
    else:
        # 2) для .app / обычного запуска без аргументов показываем диалог выбора профиля
        profiles = [TRANSIENT_PROFILE_NAME] + list_profile_names_in_app_data()

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
    _apply_application_tooltip_stylesheet(selected_theme)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = ChatWindow(profile=profile, theme_id=selected_theme)
    window.show()

    if isinstance(app, I2PChatQApplication):
        app.enable_tooltip_intercept()
    else:
        install_tooltip_event_filter(app)

    loop.create_task(window.start_core())

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.warning("GUI event loop interrupted during shutdown", exc_info=True)
    finally:
        try:
            hide_rounded_tooltip()
        except Exception:
            logger.debug("failed to hide rounded tooltip during shutdown", exc_info=True)
        try:
            loop.close()
        except KeyboardInterrupt:
            logger.warning("GUI event loop close interrupted", exc_info=True)
        except RuntimeError:
            logger.warning("GUI event loop close raised RuntimeError", exc_info=True)
        try:
            BundledI2pdManager.force_cleanup_runtime_root()
        except Exception:
            logger.warning("final bundled router cleanup failed", exc_info=True)


if __name__ == "__main__":
    main()
