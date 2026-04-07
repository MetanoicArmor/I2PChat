import asyncio
import json
import os
import re
import shlex
import sys
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich import box
from rich.align import Align
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, RadioSet, RichLog, Static, Switch, TextArea

from i2pchat.blindbox.blindbox_diagnostics import build_blindbox_diagnostics_text
from i2pchat.router.bundled_i2pd import BundledI2pdManager
from i2pchat.router.settings import (
    RouterSettings,
    bundled_i2pd_allowed,
    load_router_settings,
    normalize_router_settings,
    router_settings_path,
    save_router_settings,
)

from i2pchat.core.i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    ensure_valid_profile_name,
    get_profile_data_dir,
    get_profiles_dir,
    import_profile_dat_atomic,
    list_profile_names_in_app_data,
    migrate_all_legacy_profiles_if_needed,
    peek_persisted_stored_peer,
    render_braille,
    render_bw,
    resolve_existing_profile_file,
)
from i2pchat.core.transient_profile import (
    TRANSIENT_PROFILE_NAME,
    coalesce_profile_name,
    is_transient_profile_name,
)
from i2pchat.presentation.compose_drafts import apply_compose_draft_peer_switch
from i2pchat.presentation.reply_format import format_reply_quote
from i2pchat.presentation.status_presentation import build_status_presentation
from i2pchat.protocol.message_delivery import (
    DELIVERY_STATE_DELIVERED,
    delivery_state_label,
    normalize_loaded_delivery_state,
)
from i2pchat.storage.blindbox_state import atomic_write_json
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
from i2pchat.storage.profile_backup import (
    BackupError,
    export_history_bundle,
    export_profile_bundle,
    import_history_bundle,
    import_profile_bundle,
)
from i2pchat.updates.release_index import check_for_updates_sync

try:
    import pyperclip
except Exception:  # pragma: no cover - optional dependency at runtime
    pyperclip = None


COMPOSE_DRAFTS_MAX_KEYS = 100
_PROFILE_IMPORT_EXT = ".dat"


@dataclass
class RecentMessageRef:
    ref_id: int
    sender: str
    text: str
    peer: Optional[str]
    timestamp: str
    message_id: Optional[str] = None
    delivery_state: Optional[str] = None


@dataclass(frozen=True)
class TuiStatusSnapshot:
    short: str
    full: str
    technical: str
    blindbox_bar: str
    ack_total: int


@dataclass(frozen=True)
class TransferEventRef:
    kind: str
    filename: str
    detail: str
    timestamp: str


class I2PChat(App):
    """Textual TUI frontend for I2PChatCore with command-driven parity features."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "send_message", "Send"),
        ("ctrl+enter", "send_message", "Send"),
        ("f1", "show_actions", "Actions"),
        ("f2", "show_contacts", "Contacts"),
        ("f3", "show_media", "Media"),
        ("f4", "show_settings", "Settings"),
        ("f5", "show_router", "Router"),
        ("f6", "show_history_screen", "History"),
        ("f7", "show_diagnostics_screen", "Diagnostics"),
        ("ctrl+b", "show_contacts", "Contacts"),
        ("ctrl+d", "show_actions", "Actions"),
        ("ctrl+p", "show_media", "Media"),
    ]

    CSS = """
    RichLog {
        height: 1fr;
        border: solid white;
        background: $surface;
    }
    Footer {
        dock: bottom;
    }
    #compose_wrap {
        dock: bottom;
        height: auto;
        background: $surface;
    }
    TextArea {
        height: 8;
        border: solid white;
        background: $surface;
    }
    #status_bar {
        dock: top;
        height: 4;
        margin: 0 0;
        content-align: center middle;
        background: $surface;
        color: $text;
    }
    """

    peer_b32 = reactive("Waiting for incoming connections...")
    network_status = reactive("initializing")

    def __init__(self) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        arg = sys.argv[1] if len(sys.argv) > 1 else ""
        self.profile = coalesce_profile_name(arg)
        if self.profile != TRANSIENT_PROFILE_NAME:
            self.profile = ensure_valid_profile_name(self.profile)
        migrate_all_legacy_profiles_if_needed()

        self.selected_peer: Optional[str] = peek_persisted_stored_peer(self.profile)
        self._history_enabled = self._load_history_enabled()
        self._privacy_mode_enabled = self._load_privacy_mode_enabled()
        self._contact_book = ContactBook()
        self._compose_drafts: dict[str, str] = {}
        self._compose_draft_active_key: Optional[str] = None
        self._history_loaded_for_peer: Optional[str] = None
        self._history_entries: list[HistoryEntry] = []
        self._history_dirty = False
        self._recent_messages: deque[RecentMessageRef] = deque(maxlen=400)
        self._message_ref_counter = 0
        self._message_ref_by_id: dict[str, int] = {}
        self._last_status_snapshot = TuiStatusSnapshot(
            short="Initializing…",
            full="Initializing…",
            technical="Initializing…",
            blindbox_bar="BlindBox: off",
            ack_total=0,
        )
        self._transfer_progress_buckets: dict[str, int] = {}
        self._recent_transfers: deque[TransferEventRef] = deque(maxlen=80)
        self._router_settings_explicit = self._tui_router_prefs_exist()
        self._router_settings: RouterSettings = self._load_tui_router_settings()
        self._bundled_router_manager: Optional[BundledI2pdManager] = None
        self._active_sam_address: Optional[tuple[str, int]] = None
        self._active_http_proxy_address: Optional[tuple[str, int]] = None
        self._core_init_task: Optional[asyncio.Task[bool]] = None

        self.core = self._create_core(self.profile, (
            self._router_settings.system_sam_host,
            int(self._router_settings.system_sam_port),
        ))

    @staticmethod
    def _tui_router_prefs_exist() -> bool:
        try:
            return os.path.isfile(router_settings_path())
        except Exception:
            return False

    @classmethod
    def _load_tui_router_settings(cls) -> RouterSettings:
        """Keep TUI startup fast by defaulting to external/system SAM until prefs explicitly exist."""
        settings = normalize_router_settings(load_router_settings())
        if cls._tui_router_prefs_exist():
            return settings
        return replace(settings, backend="system", bundled_auto_start=False)

    # ----- compose / widgets -----

    def compose(self) -> ComposeResult:
        yield Static(id="status_bar")
        yield RichLog(id="chat_window", highlight=False, markup=True)
        with Vertical(id="compose_wrap"):
            yield TextArea(
                "",
                id="compose_box",
                soft_wrap=True,
                placeholder=(
                    "Compose message. Enter = newline, Ctrl+S / Ctrl+Enter = send. "
                    "Type /help for commands."
                ),
            )
        yield Footer()

    @property
    def chat_log(self) -> RichLog:
        return self.query_one("#chat_window", RichLog)

    @property
    def compose_box(self) -> TextArea:
        return self.query_one("#compose_box", TextArea)

    def _safe_compose_box(self) -> Optional[TextArea]:
        try:
            return self.query_one("#compose_box", TextArea)
        except Exception:
            return None

    def _compose_text_snapshot(self) -> str:
        box = self._safe_compose_box()
        return box.text if box is not None else ""

    def action_send_message(self) -> None:
        self.run_worker(self._submit_compose(), exclusive=True)

    def action_show_launcher(self) -> None:
        self._show_launcher_screen()

    def action_show_contacts(self) -> None:
        self._show_contacts()

    def action_show_actions(self) -> None:
        self._show_actions_screen()

    def action_show_media(self) -> None:
        self._show_media_screen()

    def action_copy_my_address(self) -> None:
        asyncio.create_task(self._execute_command("/copyaddr"))

    def action_lock_to_peer(self) -> None:
        asyncio.create_task(self._execute_command("/save"))

    def action_show_router(self) -> None:
        self._show_router_screen()

    def action_show_backups(self) -> None:
        self._show_backups_screen()

    def action_show_settings(self) -> None:
        self._show_settings_screen()

    def action_show_history_screen(self) -> None:
        self._show_history_screen()

    def action_show_diagnostics_screen(self) -> None:
        self._show_diagnostics_screen()

    async def _submit_compose(self) -> None:
        raw = self._compose_text_snapshot()
        text = raw.rstrip("\n")
        stripped = text.strip()
        if not stripped:
            return
        if "\n" not in stripped and stripped.startswith("/"):
            await self._execute_command(stripped)
            return
        result = await self.core.send_text(text)
        if result.accepted:
            self._set_compose_text("")
            if self._compose_draft_active_key is not None:
                self._compose_drafts[self._compose_draft_active_key] = ""
            self._flush_compose_drafts_to_disk()
        else:
            self.post(
                "error",
                result.hint or "Message was not accepted. Your draft was kept in the compose box.",
            )
        self._refresh_status_bar()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "compose_box":
            return
        if self._compose_draft_active_key is not None:
            self._compose_drafts[self._compose_draft_active_key] = self._compose_text_snapshot()

    # ----- ui prefs / profile-sidecar files -----

    def _ui_prefs_path(self) -> str:
        return os.path.join(get_profiles_dir(), "ui_prefs.json")

    def _load_ui_prefs(self) -> dict[str, object]:
        try:
            with open(self._ui_prefs_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
        return {}

    def _save_ui_prefs(self, data: dict[str, object]) -> None:
        try:
            atomic_write_json(self._ui_prefs_path(), data)
        except Exception:
            pass

    def _load_history_enabled(self) -> bool:
        return self._load_ui_prefs().get("history_enabled") is not False

    def _save_history_enabled(self, enabled: bool) -> None:
        data = self._load_ui_prefs()
        data["history_enabled"] = bool(enabled)
        self._save_ui_prefs(data)

    def _load_history_max_messages(self) -> int:
        value = self._load_ui_prefs().get("history_max_messages")
        if isinstance(value, int) and value > 0:
            return int(value)
        return 1000

    def _save_history_max_messages(self, max_messages: int) -> None:
        data = self._load_ui_prefs()
        data["history_max_messages"] = max(1, int(max_messages))
        self._save_ui_prefs(data)

    def _load_history_retention_days(self) -> int:
        value = self._load_ui_prefs().get("history_retention_days")
        if isinstance(value, int):
            return max(0, int(value))
        return DEFAULT_HISTORY_RETENTION_DAYS

    def _save_history_retention_days(self, days: int) -> None:
        data = self._load_ui_prefs()
        data["history_retention_days"] = max(0, int(days))
        self._save_ui_prefs(data)

    def _load_privacy_mode_enabled(self) -> bool:
        return self._load_ui_prefs().get("privacy_mode_enabled") is True

    def _save_privacy_mode_enabled(self, enabled: bool) -> None:
        data = self._load_ui_prefs()
        if enabled:
            data["privacy_mode_enabled"] = True
        else:
            data.pop("privacy_mode_enabled", None)
        self._save_ui_prefs(data)

    def _compose_drafts_file_path_for_read(self, profile: str) -> str:
        app = get_profiles_dir()
        existing = resolve_existing_profile_file(app, profile, f"{profile}.compose_drafts.json")
        if existing:
            return existing
        return os.path.join(get_profile_data_dir(profile, create=True, app_root=app), f"{profile}.compose_drafts.json")

    def _compose_drafts_file_path_for_write(self, profile: str) -> str:
        app = get_profiles_dir()
        return os.path.join(get_profile_data_dir(profile, create=True, app_root=app), f"{profile}.compose_drafts.json")

    def _contacts_file_path_for_read(self, profile: str) -> str:
        app = get_profiles_dir()
        existing = resolve_existing_profile_file(app, profile, f"{profile}.contacts.json")
        if existing:
            return existing
        return os.path.join(get_profile_data_dir(profile, create=True, app_root=app), f"{profile}.contacts.json")

    def _contacts_file_path_for_write(self, profile: str) -> str:
        app = get_profiles_dir()
        return os.path.join(get_profile_data_dir(profile, create=True, app_root=app), f"{profile}.contacts.json")

    # ----- core wiring -----

    def _create_core(self, profile: str, sam_address: tuple[str, int]) -> I2PChatCore:
        return I2PChatCore(
            profile=profile,
            sam_address=sam_address,
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=self.handle_file_event,
            on_file_offer=self.handle_file_offer,
            on_image_received=self.handle_image_received,
            on_inline_image_received=self.handle_inline_image_received,
            on_text_delivered=self.handle_text_delivered,
            on_image_delivered=self.handle_image_delivered,
            on_file_delivered=self.handle_file_delivered,
            on_trust_decision=self.handle_trust_decision,
            on_trust_mismatch_decision=self.handle_trust_mismatch_decision,
        )

    async def _ensure_router_backend_ready(self) -> tuple[str, int]:
        settings = normalize_router_settings(self._router_settings)
        self._router_settings = settings
        if self._router_settings_explicit:
            save_router_settings(settings)
        if settings.backend == "system":
            self._active_http_proxy_address = ("127.0.0.1", 4444)
            return (settings.system_sam_host, int(settings.system_sam_port))
        if self._bundled_router_manager is None:
            self._bundled_router_manager = BundledI2pdManager(settings)
        sam_address = await self._bundled_router_manager.start()
        self._active_http_proxy_address = self._bundled_router_manager.http_proxy_address()
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

    def _active_update_proxy_url(self) -> Optional[str]:
        if self._router_settings.backend != "bundled":
            return None
        if self._active_http_proxy_address is None:
            return None
        host, port = self._active_http_proxy_address
        return f"http://{host}:{port}"

    def handle_file_offer(self, filename: str, size: int) -> bool:
        self.post(
            "system",
            f"Incoming file offer: {filename} ({size} bytes). TUI auto-accepts incoming files.",
        )
        return True

    async def handle_trust_decision(
        self, peer_addr: str, fingerprint: str, signing_key_hex: str
    ) -> bool:
        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[bool] = loop.create_future()

        def _done(result: bool | None) -> None:
            if not decision_future.done():
                decision_future.set_result(bool(result))

        self.push_screen(
            TuiTrustDecisionScreen(
                title="Trust new peer key?",
                peer_addr=peer_addr,
                body_lines=[
                    f"Fingerprint: {fingerprint}",
                    f"Key: {signing_key_hex[:24]}…",
                    "Verify out-of-band before trusting if possible.",
                ],
                confirm_label="Trust",
            ),
            callback=_done,
        )
        return await decision_future

    async def handle_trust_mismatch_decision(
        self,
        peer_addr: str,
        old_fingerprint: str,
        new_fingerprint: str,
        old_signing_key_hex: str,
        new_signing_key_hex: str,
    ) -> bool:
        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[bool] = loop.create_future()

        def _done(result: bool | None) -> None:
            if not decision_future.done():
                decision_future.set_result(bool(result))

        self.push_screen(
            TuiTrustDecisionScreen(
                title="Trusted key changed",
                peer_addr=peer_addr,
                body_lines=[
                    f"Old fingerprint: {old_fingerprint}",
                    f"New fingerprint: {new_fingerprint}",
                    f"Old key: {old_signing_key_hex[:24]}…",
                    f"New key: {new_signing_key_hex[:24]}…",
                    "Trusting the new key will replace the old TOFU pin.",
                ],
                confirm_label="Trust new key",
            ),
            callback=_done,
        )
        return await decision_future

    # ----- status -----

    def watch_network_status(self, _: str) -> None:
        self._refresh_status_bar()

    def watch_peer_b32(self, _: str) -> None:
        self._refresh_status_bar()

    def _blindbox_bar(self, blindbox: dict[str, object]) -> str:
        if not blindbox.get("enabled"):
            return "BlindBox: off"
        if blindbox.get("insecure_local_mode"):
            return "BlindBox: insecure local"
        if blindbox.get("ready"):
            if blindbox.get("poller_running"):
                return "BlindBox: on (polling)"
            return "BlindBox: on"
        if blindbox.get("has_root_secret"):
            return "BlindBox: starting…"
        return "BlindBox: need live chat once"

    def _delivery_bar(self, state: str) -> str:
        return {
            "connecting-handshake": "Send: wait secure",
            "online-live": "Send: live",
            "offline-ready": "Send: offline queue",
            "await-live-root": "Send: need Connect once",
            "blindbox-needs-locked-peer": "Send: lock peer first",
            "blindbox-needs-boxes": "Send: configure Blind Boxes",
            "blindbox-starting-local-session": "Send: wait local I2P",
            "blindbox-disabled-transient": "Send: live only",
            "blindbox-disabled": "Send: live only",
            "blindbox-initializing": "Send: offline starting",
        }.get(state, "Send: unavailable")

    def _short_peer(self, value: Optional[str]) -> str:
        if not value:
            return "—"
        clean = value.replace(".b32.i2p", "")
        if len(clean) <= 12:
            return clean
        return f"{clean[:6]}…{clean[-6:]}"

    def _build_status_snapshot(self) -> TuiStatusSnapshot:
        delivery = self.core.get_delivery_telemetry()
        blindbox = self.core.get_blindbox_telemetry()
        ack = self.core.get_ack_telemetry()
        ack_total = int(sum(int(v) for v in ack.values()))
        blindbox_bar = self._blindbox_bar(blindbox)
        stored_short = self._short_peer(self.core.stored_peer)
        peer_short = self._short_peer(self._current_target_peer())
        send_in_flight = any(
            entry.delivery_state == "sending" for entry in list(self._recent_messages)[-5:]
        )
        link_state = "connected" if self.core.conn is not None else "disconnected"
        if self.core.conn is not None and not self.core.handshake_complete:
            link_state = "handshake"
        secure_state = "verified" if self.core.proven else (
            "secure" if self.core.handshake_complete else "none"
        )
        my_short = self._short_peer(
            (self.core.my_dest.base32 + ".b32.i2p")
            if self.core.my_dest is not None
            else None
        )
        presentation = build_status_presentation(
            network_status_raw=self.network_status,
            connected=self.core.conn is not None,
            handshake_complete=self.core.handshake_complete,
            outbound_connect_busy=self.core.is_outbound_connect_busy(),
            delivery_state=str(delivery.get("state", "unknown")),
            send_in_flight=send_in_flight,
            profile_name=self.profile,
            is_transient_profile=is_transient_profile_name(self.profile),
            my_short=my_short,
            peer_short=peer_short,
            stored_short=stored_short,
            link_state=link_state,
            secure_state=secure_state,
            delivery_bar=self._delivery_bar(str(delivery.get("state", "unknown"))),
            blindbox_bar=blindbox_bar,
            blindbox_detail=build_blindbox_diagnostics_text(
                profile=self.profile,
                selected_peer=self._current_target_peer() or "",
                delivery=delivery,
                blindbox=blindbox,
                ack=ack,
            ),
            ack_part=(
                f"ACK issues total: {ack_total}"
                + (
                    " | "
                    + ", ".join(f"{k}={v}" for k, v in sorted(ack.items()))
                    if ack
                    else ""
                )
            ),
        )
        return TuiStatusSnapshot(
            short=presentation.primary_short,
            full=presentation.primary_full,
            technical=presentation.technical_detail,
            blindbox_bar=blindbox_bar,
            ack_total=ack_total,
        )

    def _launcher_summary_text(self) -> str:
        snap = self._build_status_snapshot()
        peer = self._current_target_peer() or "—"
        return (
            f"{snap.full}\n"
            f"Current peer: {peer}\n"
            f"{self._router_status_block()}\n"
            f"{snap.blindbox_bar}"
        )

    def _actions_summary_text(self) -> str:
        snap = self._build_status_snapshot()
        peer = self._current_target_peer() or "—"
        connected = "yes" if self.core.conn is not None else "no"
        verified = "yes" if self.core.proven else "no"
        locked = "yes" if self.core.stored_peer else "no"
        return (
            f"Current peer: {peer}\n"
            f"Connected: {connected}\n"
            f"Verified: {verified}\n"
            f"Locked peer: {locked}\n"
            f"{snap.full}\n"
            f"{snap.blindbox_bar}"
        )

    def _media_summary_text(self) -> str:
        snap = self._build_status_snapshot()
        return (
            f"Current peer: {self._current_target_peer() or '—'}\n"
            f"{snap.full}\n"
            f"{snap.blindbox_bar}"
        )

    def _refresh_status_bar(self) -> None:
        self._last_status_snapshot = self._build_status_snapshot()
        is_active = bool(self.core.current_peer_addr or self.selected_peer)
        if self.core.proven:
            border_col, title = "green", "VERIFIED SESSION"
        elif is_active:
            border_col, title = "cyan", "ACTIVE SESSION"
        else:
            border_col, title = "yellow", "TUNNELS READY"

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=2)
        grid.add_column(justify="right", ratio=1)

        mode_tag = "P" if self.profile != TRANSIENT_PROFILE_NAME else "T"
        tag_bg = "green" if mode_tag == "P" else "grey62"
        left = (
            f"[black on {tag_bg}] [bold]{mode_tag}[/] [/] "
            f"[bold]{self.profile.upper()}[/]  [white]{escape(self._last_status_snapshot.short)}[/]"
        )
        right = (
            f"[green]{self._short_peer(self.core.my_dest.base32 if self.core.my_dest else None)}[/]"
            f" [white]→[/] [cyan]{self._short_peer(self._current_target_peer())}[/]"
        )
        if self._last_status_snapshot.ack_total > 0:
            right += f" [dim]ACKdrop:{self._last_status_snapshot.ack_total}[/]"

        grid.add_row(left, right)
        status_panel = Panel(
            grid,
            title=f"[bold {border_col}]{title}[/]",
            subtitle=f"[dim]{escape(self._last_status_snapshot.blindbox_bar)}[/]",
            border_style=border_col,
            box=box.ROUNDED,
            style="default",
        )
        try:
            self.query_one("#status_bar").update(status_panel)
        except Exception:
            pass

    # ----- rendering helpers -----

    def post(self, type_name: str, message: str, *, allow_markup: bool = False) -> None:
        styles = {
            "info": "[bold blue]STATUS:[/] [white]{}[/]",
            "error": "[bold red]ERROR:[/] [red]{}[/]",
            "system": "[#878700]SYSTEM:[/] [dim #9f9f9f italic]{}[/]",
            "success": "[bold green]✔[/] [white]{}[/]",
            "disconnect": "[bold red]X[/] [white]{}[/]",
            "help": "[dim]HELP:[/] [gray62]{}[/]",
        }

        safe_message = str(message) if allow_markup else escape(str(message))
        address_pattern = r"([a-z0-9]+\.b32\.i2p|[a-z0-9]+\.i2p)"
        formatted_msg = re.sub(address_pattern, r"[bold cyan]\1[/]", safe_message)
        content = styles.get(type_name, "{}").format(formatted_msg)
        try:
            self.chat_log.write(content)
        except Exception:
            return

    def post_panel(
        self,
        title: str,
        body: object,
        *,
        border_style: str = "blue",
        align: str = "left",
    ) -> None:
        panel = Panel(body, title=title, border_style=border_style, box=box.ROUNDED)
        try:
            self.chat_log.write(Align(panel, align=align), expand=True)
        except Exception:
            return

    def _peer_display_name(self, peer: Optional[str]) -> str:
        if not peer:
            return "Peer"
        record = self._contact_book.get(peer)
        if record and record.display_name.strip():
            return record.display_name.strip()
        return self._short_peer(peer)

    def _record_recent_message(
        self,
        sender: str,
        text: str,
        *,
        peer: Optional[str],
        message_id: Optional[str],
        delivery_state: Optional[str],
        timestamp: str,
    ) -> int:
        self._message_ref_counter += 1
        ref = RecentMessageRef(
            ref_id=self._message_ref_counter,
            sender=sender,
            text=text,
            peer=peer,
            timestamp=timestamp,
            message_id=message_id,
            delivery_state=delivery_state,
        )
        self._recent_messages.append(ref)
        if message_id:
            self._message_ref_by_id[message_id] = ref.ref_id
        return ref.ref_id

    def _find_recent_ref(self, ref_id: int) -> Optional[RecentMessageRef]:
        for item in reversed(self._recent_messages):
            if item.ref_id == ref_id:
                return item
        return None

    def _render_chat_message(
        self,
        *,
        sender_label: str,
        text: str,
        align: str,
        border_color: str,
        timestamp: str,
        ref_id: int,
        delivery_state: Optional[str] = None,
        history: bool = False,
    ) -> None:
        suffix_parts = [f"#{ref_id}"]
        if delivery_state:
            label = delivery_state_label(delivery_state)
            if label:
                suffix_parts.append(label)
        if history:
            suffix_parts.append("history")
        suffix = " · ".join(suffix_parts)
        panel = Panel(
            f"[white]{escape(text)}[/]",
            title=f"[#5f5f5f][{timestamp} UTC][/] [bold {border_color}]{escape(sender_label)}[/] [dim]{escape(suffix)}[/]",
            title_align="left",
            border_style=border_color,
            box=box.ROUNDED,
            expand=False,
        )
        self.chat_log.write(Align(panel, align=align), expand=True)

    def _render_inline_image(self, path: str, *, is_from_me: bool, label: str) -> None:
        try:
            art = "\n".join(render_bw(path))
        except Exception:
            art = f"Saved image: {path}"
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        border = "green" if is_from_me else "cyan"
        align = "left" if is_from_me else "right"
        panel = Panel(
            art,
            title=(
                f"[#5f5f5f][{now_utc} UTC][/] [bold {border}]{escape(label)}[/] "
                f"[dim]{escape(os.path.basename(path))}[/]"
            ),
            title_align="left",
            border_style=border,
            box=box.ROUNDED,
            expand=False,
        )
        self.chat_log.write(Align(panel, align=align), expand=True)
        self.post("system", f"Image saved at {path}")

    def _set_compose_text(self, text: str) -> None:
        box = self._safe_compose_box()
        if box is not None:
            box.load_text(text)

    # ----- contacts / drafts / history -----

    def _load_contacts_book(self) -> None:
        self._contact_book = load_book(self._contacts_file_path_for_read(self.profile))

    def _save_contacts_book(self) -> None:
        save_book(self._contacts_file_path_for_write(self.profile), self._contact_book)

    def _ensure_stored_peer_in_contact_book(self) -> None:
        stored = self.core.stored_peer or peek_persisted_stored_peer(self.profile) or ""
        if not stored:
            return
        changed = False
        if remember_peer(self._contact_book, stored):
            changed = True
        if set_last_active_peer(self._contact_book, stored):
            changed = True
        if changed:
            self._save_contacts_book()

    def _merge_active_compose_into_drafts(self) -> None:
        if self._compose_draft_active_key is not None:
            self._compose_drafts[self._compose_draft_active_key] = self._compose_text_snapshot()

    def _load_compose_drafts_from_disk(self) -> None:
        self._compose_drafts = {}
        path = self._compose_drafts_file_path_for_read(self.profile)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        drafts = data.get("drafts")
        if not isinstance(drafts, dict):
            return
        for key, value in drafts.items():
            if isinstance(key, str) and isinstance(value, str):
                self._compose_drafts[key] = value

    def _flush_compose_drafts_to_disk(self) -> None:
        self._merge_active_compose_into_drafts()
        while len(self._compose_drafts) > COMPOSE_DRAFTS_MAX_KEYS:
            del self._compose_drafts[next(iter(self._compose_drafts))]
        try:
            atomic_write_json(
                self._compose_drafts_file_path_for_write(self.profile),
                {"version": 1, "drafts": dict(self._compose_drafts)},
            )
        except Exception:
            pass

    def _sync_compose_draft_to_peer_key(self, new_key: Optional[str]) -> None:
        if new_key == self._compose_draft_active_key:
            return
        active, text, updated = apply_compose_draft_peer_switch(
            old_active_key=self._compose_draft_active_key,
            new_key=new_key,
            input_plain=self._compose_text_snapshot(),
            drafts=self._compose_drafts,
        )
        self._compose_drafts = updated
        self._compose_draft_active_key = active
        self._set_compose_text(text)
        self._flush_compose_drafts_to_disk()

    def _current_target_peer(self) -> Optional[str]:
        return self.core.current_peer_addr or self.core.stored_peer or self.selected_peer or None

    def _set_selected_peer(
        self,
        peer: Optional[str],
        *,
        remember: bool,
        announce: Optional[str] = None,
    ) -> bool:
        normalized = normalize_peer_address(peer or "") if peer else None
        if peer and not normalized:
            return False
        old = self.selected_peer
        if old == normalized:
            return True
        self._save_history_if_needed()
        self.selected_peer = normalized
        self.peer_b32 = normalized or "Waiting for incoming connections..."
        self._sync_compose_draft_to_peer_key(normalized)
        if normalized and remember:
            changed = False
            if remember_peer(self._contact_book, normalized):
                changed = True
            if set_last_active_peer(self._contact_book, normalized):
                changed = True
            if changed:
                self._save_contacts_book()
        self._try_load_history(force=True)
        if announce and normalized:
            self.post("system", f"{announce}: {normalized}")
        self._refresh_status_bar()
        return True

    def _append_history_entry(self, msg: ChatMessage) -> None:
        if not self._history_enabled or msg.kind not in {"me", "peer"}:
            return
        peer = msg.source_peer if msg.kind == "peer" else self._current_target_peer()
        if not peer:
            return
        normalized_peer = normalize_peer_addr(peer)
        if self._history_loaded_for_peer != normalized_peer:
            self._try_load_history(force=True)
        if self._history_loaded_for_peer != normalized_peer:
            self._history_loaded_for_peer = normalized_peer
            self._history_entries = []
        self._history_entries.append(
            HistoryEntry(
                kind=msg.kind,
                text=msg.text,
                ts=msg.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                message_id=msg.message_id,
                delivery_state=msg.delivery_state,
                delivery_route=msg.delivery_route,
                delivery_hint=msg.delivery_hint,
                delivery_reason=msg.delivery_reason,
                retryable=bool(msg.retryable),
            )
        )
        self._history_dirty = True
        self._save_history_if_needed()

    def _update_history_delivery_state(self, message_id: str, new_state: str) -> None:
        if not message_id or not self._history_entries:
            return
        updated = False
        for idx in range(len(self._history_entries) - 1, -1, -1):
            entry = self._history_entries[idx]
            if entry.message_id != message_id:
                continue
            self._history_entries[idx] = replace(entry, delivery_state=new_state, retryable=False)
            updated = True
            break
        if updated:
            self._history_dirty = True
            self._save_history_if_needed()

    def _try_load_history(self, *, force: bool = False) -> None:
        if not self._history_enabled:
            return
        peer = self._current_target_peer()
        if not peer:
            return
        normalized_peer = normalize_peer_addr(peer)
        if not force and normalized_peer == self._history_loaded_for_peer:
            return
        identity_key = self.core.get_identity_key_bytes()
        if not identity_key:
            return
        entries = load_history(
            self.core.get_profile_data_dir(),
            self.core.profile,
            normalized_peer,
            identity_key,
            app_data_root=self.core.get_profiles_dir(),
        )
        self._history_loaded_for_peer = normalized_peer
        self._history_entries = list(entries)
        self._history_dirty = False
        if not entries:
            return
        self.post("system", f"Loaded {len(entries)} history message(s) for {normalized_peer}.")
        tail = entries[-20:]
        if len(entries) > len(tail):
            self.post("system", f"Showing the latest {len(tail)} history message(s). Use /history show for more.")
        for entry in tail:
            ts_display = entry.ts
            try:
                ts_display = datetime.fromisoformat(entry.ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
            except Exception:
                ts_display = entry.ts[:8]
            sender = "Me" if entry.kind == "me" else self._peer_display_name(normalized_peer)
            ref_id = self._record_recent_message(
                sender,
                entry.text,
                peer=normalized_peer,
                message_id=entry.message_id,
                delivery_state=normalize_loaded_delivery_state(entry.delivery_state),
                timestamp=ts_display,
            )
            self._render_chat_message(
                sender_label=sender,
                text=entry.text,
                align="left" if entry.kind == "me" else "right",
                border_color="green" if entry.kind == "me" else "cyan",
                timestamp=ts_display,
                ref_id=ref_id,
                delivery_state=normalize_loaded_delivery_state(entry.delivery_state),
                history=True,
            )

    def _save_history_if_needed(self) -> None:
        if not self._history_enabled or not self._history_dirty:
            return
        peer = self._history_loaded_for_peer or self._current_target_peer()
        if not peer:
            return
        identity_key = self.core.get_identity_key_bytes()
        if not identity_key:
            return
        entries, _ = apply_history_retention(
            self._history_entries,
            max_messages=self._load_history_max_messages(),
            max_age_days=self._load_history_retention_days(),
        )
        self._history_entries = list(entries)
        if entries:
            save_history(
                self.core.get_profile_data_dir(),
                self.core.profile,
                peer,
                entries,
                identity_key,
                max_messages=self._load_history_max_messages(),
                max_age_days=self._load_history_retention_days(),
                app_data_root=self.core.get_profiles_dir(),
            )
        self._history_dirty = False

    # ----- callbacks from core -----

    def handle_status(self, status: str) -> None:
        self.network_status = status

    def handle_message(self, msg: ChatMessage) -> None:
        if msg.kind in {"me", "peer"}:
            timestamp = msg.timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
            peer = msg.source_peer if msg.kind == "peer" else self._current_target_peer()
            sender = "Me" if msg.kind == "me" else self._peer_display_name(peer)
            ref_id = self._record_recent_message(
                sender,
                msg.text,
                peer=peer,
                message_id=msg.message_id,
                delivery_state=msg.delivery_state,
                timestamp=timestamp,
            )
            self._render_chat_message(
                sender_label=sender,
                text=msg.text,
                align="left" if msg.kind == "me" else "right",
                border_color="green" if msg.kind == "me" else "cyan",
                timestamp=timestamp,
                ref_id=ref_id,
                delivery_state=msg.delivery_state,
            )
            if peer:
                ts_iso = msg.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                changed = False
                if remember_peer(self._contact_book, peer):
                    changed = True
                if set_last_active_peer(self._contact_book, peer):
                    changed = True
                if touch_peer_message_meta(self._contact_book, peer, msg.text, ts_iso):
                    changed = True
                if changed:
                    self._save_contacts_book()
                if msg.kind == "peer":
                    self._set_selected_peer(peer, remember=True)
            self._append_history_entry(msg)
            self._refresh_status_bar()
            return
        self.post(msg.kind, msg.text)
        self._refresh_status_bar()

    def handle_system(self, text: str) -> None:
        self.post("system", text)
        self._refresh_status_bar()

    def handle_error(self, text: str) -> None:
        self.post("error", text)
        self._refresh_status_bar()

    def handle_file_event(self, info: FileTransferInfo) -> None:
        label = "Image" if info.is_inline_image else "File"
        key = f"{label}:{info.filename}"
        if info.rejected_by_peer:
            self._record_transfer_event(label.lower(), info.filename, "rejected by peer")
            self.post("error", f"{label} rejected by peer: {info.filename}")
            return
        if info.received < 0:
            self._record_transfer_event(label.lower(), info.filename, "transfer failed")
            self.post("error", f"{label} transfer failed: {info.filename}")
            return
        percent = int((info.received / info.size) * 100) if info.size > 0 else 0
        bucket = min(100, (percent // 10) * 10)
        last_bucket = self._transfer_progress_buckets.get(key)
        if info.received == 0 or info.received >= info.size or last_bucket != bucket:
            direction = "sending" if info.is_sending else "receiving"
            self.post(
                "system",
                f"{label} {direction}: {os.path.basename(info.filename)} ({info.received}/{info.size} bytes, {percent}%)",
            )
            self._transfer_progress_buckets[key] = bucket
            self._record_transfer_event(
                label.lower(),
                info.filename,
                f"{direction} {percent}%",
            )
        if info.received >= info.size:
            self._transfer_progress_buckets.pop(key, None)

    def handle_image_received(self, art: str) -> None:
        self._record_transfer_event("image", "inline-image", "received")
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        panel = Panel(
            art,
            title=f"[#5f5f5f][{now_utc} UTC][/] [bold cyan]{escape(self._peer_display_name(self._current_target_peer()))}[/] [dim]ASCII image[/]",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
        self.chat_log.write(Align(panel, align="right"), expand=True)

    def handle_inline_image_received(
        self, path: str, is_from_me: bool, sent_filename: Optional[str] = None
    ) -> None:
        self._record_transfer_event(
            "image",
            sent_filename or path,
            "sent inline preview" if is_from_me else "received inline preview",
        )
        label = "Me" if is_from_me else self._peer_display_name(self._current_target_peer())
        if sent_filename:
            label = f"{label} · {sent_filename}"
        self._render_inline_image(path, is_from_me=is_from_me, label=label)

    def handle_peer_changed(self, peer: Optional[str]) -> None:
        if peer:
            self._set_selected_peer(peer, remember=True, announce="Active peer")

    def handle_text_delivered(self, message_id: str) -> None:
        ref = self._message_ref_by_id.get(message_id)
        if ref is not None:
            self.post("success", f"Message #{ref} delivered.")
        else:
            self.post("success", f"Message delivered ({message_id}).")
        self._update_history_delivery_state(message_id, DELIVERY_STATE_DELIVERED)
        self._refresh_status_bar()

    def handle_image_delivered(self, filename: str) -> None:
        self._record_transfer_event("image", filename, "delivered")
        self.post("success", f"Image delivered: {filename}")

    def handle_file_delivered(self, filename: str) -> None:
        self._record_transfer_event("file", filename, "delivered")
        self.post("success", f"File delivered: {filename}")

    # ----- lifecycle -----

    async def on_mount(self) -> None:
        self._load_contacts_book()
        self._load_compose_drafts_from_disk()
        if not self.selected_peer and self._contact_book.last_active_peer:
            self.selected_peer = self._contact_book.last_active_peer
        if self.selected_peer:
            self._sync_compose_draft_to_peer_key(self.selected_peer)

        self.network_status = "initializing"
        self.peer_b32 = self.selected_peer or "Initializing SAM Session..."
        self.post(
            "system",
            f"Initializing Profile: [bold yellow]{escape(self.profile)}[/]",
            allow_markup=True,
        )
        self.post(
            "system",
            f"Mode: {'PERSISTENT' if self.profile != TRANSIENT_PROFILE_NAME else 'TRANSIENT'}",
        )
        if self.selected_peer:
            self.post("system", f"Selected peer: {self.selected_peer}")
        self._start_core_session_init_background()

    async def on_unmount(self) -> None:
        if self._core_init_task is not None and not self._core_init_task.done():
            self._core_init_task.cancel()
        self._save_history_if_needed()
        self._flush_compose_drafts_to_disk()
        self._save_contacts_book()
        await self.core.shutdown()
        await self._shutdown_router_backend()

    # ----- command helpers -----

    def _resolve_selector_to_peer(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return self._current_target_peer()
        raw = token.strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(self._contact_book.contacts):
                return self._contact_book.contacts[idx].addr
            return None
        return normalize_peer_address(raw)

    def _show_contacts(self) -> None:
        self.push_screen(TuiContactsScreen())

    def _show_contacts_table(self) -> None:
        if not self._contact_book.contacts:
            self.post_panel(
                "Saved peers",
                "No saved peers yet.\n\nUse:\n"
                "/contacts add <b32-address> [display_name] [note]\n"
                "/contacts use <index|b32-address>",
                border_style="magenta",
            )
            return
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=3)
        table.add_column("Sel", width=3)
        table.add_column("Name")
        table.add_column("Address")
        table.add_column("Flags", width=10)
        table.add_column("Last preview")
        for idx, record in enumerate(self._contact_book.contacts, start=1):
            flags: list[str] = []
            if record.addr == self.core.stored_peer:
                flags.append("lock")
            trust = self.core.get_peer_trust_info(record.addr)
            if trust and trust.pinned:
                flags.append("pin")
            if record.addr == self.selected_peer:
                flags.append("active")
            title = record.display_name or "—"
            preview = record.last_preview or record.note or "—"
            table.add_row(
                str(idx),
                "▶" if record.addr == self.selected_peer else "",
                title,
                self._short_peer(record.addr),
                ",".join(flags) or "—",
                preview,
            )
        self.post_panel("Saved peers", table, border_style="magenta")

    def _show_router_screen(self) -> None:
        self.push_screen(TuiRouterScreen())

    def _show_contact_editor(self, *, peer: Optional[str] = None) -> None:
        self.push_screen(TuiContactEditorScreen(peer=peer))

    def _show_backups_screen(self) -> None:
        self.push_screen(TuiBackupsScreen())

    def _show_settings_screen(self) -> None:
        self.push_screen(TuiSettingsScreen())

    def _show_actions_screen(self) -> None:
        self.push_screen(TuiActionsScreen())

    def _show_media_screen(self) -> None:
        self.push_screen(TuiMediaScreen())

    def _show_launcher_screen(self) -> None:
        self.push_screen(TuiLauncherScreen())

    def _show_history_screen(self) -> None:
        self.push_screen(TuiHistoryScreen())

    def _show_diagnostics_screen(self) -> None:
        self.push_screen(TuiDiagnosticsScreen())

    def _show_recent(self, count: int) -> None:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Ref", width=5)
        table.add_column("When", width=10)
        table.add_column("Sender", width=20)
        table.add_column("State", width=10)
        table.add_column("Text")
        for item in list(self._recent_messages)[-max(1, count):]:
            table.add_row(
                str(item.ref_id),
                item.timestamp,
                item.sender,
                delivery_state_label(item.delivery_state) or "—",
                item.text.replace("\n", " ⏎ "),
            )
        self.post_panel("Recent messages", table, border_style="cyan")

    def _show_history(self, count: int) -> None:
        peer = self._current_target_peer()
        if not peer:
            self.post("error", "No active or selected peer.")
            return
        self._try_load_history(force=self._history_loaded_for_peer != normalize_peer_addr(peer))
        entries = self._history_entries[-max(1, count):]
        if not entries:
            self.post("system", f"No saved history for {peer}.")
            return
        table = Table(show_header=True, header_style="bold green")
        table.add_column("When", width=10)
        table.add_column("Sender", width=16)
        table.add_column("State", width=10)
        table.add_column("Text")
        for entry in entries:
            try:
                ts = datetime.fromisoformat(entry.ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
            except Exception:
                ts = entry.ts[:8]
            sender = "Me" if entry.kind == "me" else self._peer_display_name(peer)
            table.add_row(ts, sender, delivery_state_label(normalize_loaded_delivery_state(entry.delivery_state)) or "—", entry.text.replace("\n", " ⏎ "))
        self.post_panel(f"History for {peer}", table, border_style="green")

    def _show_contact_info(self, peer: str) -> None:
        record = self._contact_book.get(peer) or ContactRecord(addr=peer)
        trust = self.core.get_peer_trust_info(peer)
        lines = [
            f"Address: {peer}",
            f"Display name: {record.display_name or '—'}",
            f"Note: {record.note or '—'}",
            f"Last preview: {record.last_preview or '—'}",
            f"Last activity: {record.last_activity_ts or '—'}",
            f"Locked peer: {'yes' if peer == self.core.stored_peer else 'no'}",
            f"Pinned key: {'yes' if trust and trust.pinned else 'no'}",
        ]
        if trust and trust.fingerprint_short:
            lines.append(f"Fingerprint: {trust.fingerprint_short}")
        self.post_panel("Contact details", "\n".join(lines), border_style="yellow")

    def _show_blindbox_diagnostics(self) -> None:
        text = build_blindbox_diagnostics_text(
            profile=self.profile,
            selected_peer=self._current_target_peer() or "",
            delivery=self.core.get_delivery_telemetry(),
            blindbox=self.core.get_blindbox_telemetry(),
            ack=self.core.get_ack_telemetry(),
        )
        self.post_panel("BlindBox diagnostics", text, border_style="blue")

    async def _switch_profile(self, new_profile: str) -> None:
        target = coalesce_profile_name(new_profile)
        if target != TRANSIENT_PROFILE_NAME:
            target = ensure_valid_profile_name(target)
        if target == self.profile:
            self.post("system", f"Already using profile {target}.")
            return
        self.post("system", f"Switching profile to {target}…")
        self._save_history_if_needed()
        self._flush_compose_drafts_to_disk()
        self._save_contacts_book()
        await self.core.shutdown()

        self.profile = target
        self.selected_peer = peek_persisted_stored_peer(self.profile)
        self._contact_book = ContactBook()
        self._compose_drafts = {}
        self._compose_draft_active_key = None
        self._history_loaded_for_peer = None
        self._history_entries = []
        self._history_dirty = False
        self._recent_messages.clear()
        self._message_ref_by_id.clear()
        self._transfer_progress_buckets.clear()
        self.chat_log.clear()

        self.core = self._create_core(self.profile, self._active_sam_address or (self._router_settings.system_sam_host, int(self._router_settings.system_sam_port)))
        self._load_contacts_book()
        self._load_compose_drafts_from_disk()
        if not self.selected_peer and self._contact_book.last_active_peer:
            self.selected_peer = self._contact_book.last_active_peer
        self._sync_compose_draft_to_peer_key(self.selected_peer)
        self.network_status = "initializing"
        self.peer_b32 = self.selected_peer or "Initializing SAM Session..."
        self.post(
            "system",
            f"Initializing Profile: [bold yellow]{escape(self.profile)}[/]",
            allow_markup=True,
        )
        self._start_core_session_init_background()

    def _start_core_session_init_background(self) -> None:
        if self._core_init_task is not None and not self._core_init_task.done():
            return

        async def _runner() -> bool:
            try:
                return await self._initialize_core_session()
            finally:
                self._core_init_task = None

        self._core_init_task = asyncio.create_task(_runner())

    async def _initialize_core_session(self) -> bool:
        try:
            sam_address = await self._ensure_router_backend_ready()
            if self._active_sam_address != sam_address:
                self._active_sam_address = sam_address
                self.core = self._create_core(self.profile, sam_address)
            await self.core.init_session()
        except Exception as exc:
            self.post("error", str(exc))
            self.post(
                "system",
                "TUI stays open. Start an I2P router with SAM, then run /init to retry session initialization.",
            )
            self._refresh_status_bar()
            return False
        self._ensure_stored_peer_in_contact_book()
        if self.core.stored_peer and self.core.stored_peer != self.selected_peer:
            self._set_selected_peer(self.core.stored_peer, remember=True, announce="Stored peer")
        else:
            self._try_load_history(force=True)
        self._refresh_status_bar()
        return True

    async def _check_updates(self) -> None:
        self.post("system", "Checking for updates…")
        version = self._current_version()
        result = await asyncio.to_thread(check_for_updates_sync, version, proxy_url=self._active_update_proxy_url())
        level = "success" if result.ok else "error"
        self.post(level, result.message)

    async def _export_profile_backup_ui(self, path: str, passphrase: str) -> None:
        summary = await asyncio.to_thread(
            export_profile_bundle,
            path,
            get_profiles_dir(),
            self.profile,
            passphrase,
            include_history=True,
        )
        self.post(
            "success",
            f"Profile backup exported: {summary.file_count} file(s), {summary.history_files} history file(s).",
        )

    async def _import_profile_backup_ui(self, path: str, passphrase: str) -> None:
        summary = await asyncio.to_thread(
            import_profile_bundle,
            path,
            get_profiles_dir(),
            passphrase,
        )
        self.post("success", f"Profile backup imported as '{summary.target_profile}'.")
        await self._switch_profile(summary.target_profile)

    async def _export_history_backup_ui(self, path: str, passphrase: str) -> None:
        if not list_history_file_paths(
            self.core.get_profile_data_dir(),
            self.profile,
            app_data_root=self.core.get_profiles_dir(),
        ):
            self.post("system", "No saved history files were found for the current profile.")
            return
        summary = await asyncio.to_thread(
            export_history_bundle,
            path,
            get_profiles_dir(),
            self.profile,
            passphrase,
        )
        self.post("success", f"History backup exported: {summary.history_files} history file(s).")

    async def _import_history_backup_ui(
        self,
        path: str,
        passphrase: str,
        *,
        conflict_mode: str = "skip",
    ) -> None:
        summary = await asyncio.to_thread(
            import_history_bundle,
            path,
            get_profiles_dir(),
            self.profile,
            passphrase,
            conflict_mode=conflict_mode,
        )
        self.post(
            "success",
            f"History backup imported: restored {summary.restored_files}, skipped {summary.skipped_files}.",
        )
        self._try_load_history(force=True)

    def _run_backup_action(self, action: str, path: str, passphrase: str, conflict_mode: str = "skip") -> None:
        async def _runner() -> None:
            try:
                if action == "profile-export":
                    await self._export_profile_backup_ui(path, passphrase)
                elif action == "profile-import":
                    await self._import_profile_backup_ui(path, passphrase)
                elif action == "history-export":
                    await self._export_history_backup_ui(path, passphrase)
                elif action == "history-import":
                    await self._import_history_backup_ui(
                        path,
                        passphrase,
                        conflict_mode=conflict_mode,
                    )
                else:
                    self.post("error", f"Unsupported backup action: {action}")
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        asyncio.create_task(_runner())

    def _apply_settings_ui(
        self,
        *,
        history_enabled: bool,
        max_messages: int,
        max_days: int,
    ) -> None:
        self._history_enabled = history_enabled
        self._save_history_enabled(history_enabled)
        self._save_history_max_messages(max_messages)
        self._save_history_retention_days(max_days)
        retained, _ = apply_history_retention(
            self._history_entries,
            max_messages=max_messages,
            max_age_days=max_days,
        )
        self._history_entries = list(retained)
        self._history_dirty = True
        self._save_history_if_needed()
        if history_enabled:
            self._try_load_history(force=True)

    def _run_profile_import_action(self, path: str, name: str) -> None:
        async def _runner() -> None:
            try:
                imported = await asyncio.to_thread(
                    import_profile_dat_atomic,
                    path,
                    get_profiles_dir(),
                    ensure_valid_profile_name(name),
                )
            except Exception as exc:
                self.post("error", f"Failed to import profile .dat: {exc}")
                return
            self.post("success", f"Imported profile as {imported}.")
            await self._switch_profile(imported)

        asyncio.create_task(_runner())

    def _current_version(self) -> str:
        version_path = Path(__file__).resolve().parents[2] / "VERSION"
        try:
            return version_path.read_text(encoding="utf-8").strip() or "0.0.0"
        except Exception:
            return "0.0.0"

    @staticmethod
    def _normalize_command_alias(cmd: str) -> str:
        aliases = {
            "blindbox-diagnostics": "blindbox",
            "check-updates": "updates",
            "copy-my-address": "copyaddr",
            "open-app-dir": "appdir",
            "lock-peer": "save",
            "forget-pinned-peer-key": "forget-pin",
            "saved-peers": "contacts",
            "send-picture": "sendpic",
            "send-file": "sendfile",
            "media": "transfers",
            "transfer": "transfers",
            "clear-history": "history-clear",
            "history-retention": "history-retention",
            "load-profile": "profile-import-dat",
            "export-profile-backup": "backup-profile-export",
            "import-profile-backup": "backup-profile-import",
            "export-history-backup": "backup-history-export",
            "import-history-backup": "backup-history-import",
            "backup-ui": "backups",
            "preferences": "settings",
            "menu": "launcher",
            "history-browser": "history-screen",
            "diagnostics": "diagnostics-screen",
            "diag": "diagnostics-screen",
        }
        return aliases.get(cmd, cmd)

    def _record_transfer_event(self, kind: str, filename: str, detail: str) -> None:
        self._recent_transfers.appendleft(
            TransferEventRef(
                kind=kind,
                filename=os.path.basename(filename),
                detail=detail,
                timestamp=datetime.now(timezone.utc).strftime("%H:%M:%S"),
            )
        )

    def _router_status_line(self) -> str:
        configured = self._router_settings.backend
        source = "saved" if self._router_settings_explicit else "tui-default"
        active_runtime = (
            "bundled i2pd (managed by TUI)"
            if self._bundled_router_manager is not None
            else "external/system SAM"
        )
        sam = self._active_sam_address or (
            self._router_settings.system_sam_host,
            int(self._router_settings.system_sam_port),
        )
        return (
            f"Configured router: {configured} ({source}) | "
            f"Active runtime: {active_runtime} | SAM: {sam}"
        )

    def _router_status_block(self) -> str:
        configured = self._router_settings.backend
        source = "saved choice" if self._router_settings_explicit else "TUI default"
        if self._bundled_router_manager is not None:
            active_runtime = "bundled i2pd started by TUI"
        elif configured == "bundled":
            active_runtime = "bundled i2pd selected but not started yet"
        elif self._active_sam_address is not None:
            active_runtime = "external/system SAM"
        else:
            active_runtime = "waiting for external/system SAM"
        sam = self._active_sam_address or (
            self._router_settings.system_sam_host,
            int(self._router_settings.system_sam_port),
        )
        return (
            f"Configured backend: {configured}\n"
            f"Selection source: {source}\n"
            f"Active runtime: {active_runtime}\n"
            f"Active SAM address: {sam}"
        )

    async def _apply_router_settings(
        self,
        settings: RouterSettings,
        *,
        explicit: bool,
        reason: str,
    ) -> None:
        self._router_settings = settings
        self._router_settings_explicit = explicit
        if explicit:
            save_router_settings(settings)
        await self._restart_router_runtime(reason)

    async def _restart_router_runtime(self, reason: str) -> None:
        self.post("system", reason)
        await self.core.shutdown()
        await self._shutdown_router_backend()
        self._active_sam_address = None
        self._active_http_proxy_address = None
        self.network_status = "initializing"
        self.peer_b32 = self.selected_peer or "Initializing SAM Session..."
        self.core = self._create_core(
            self.profile,
            (
                self._router_settings.system_sam_host,
                int(self._router_settings.system_sam_port),
            ),
        )
        await self._initialize_core_session()

    async def _set_router_backend(self, backend: str) -> None:
        normalized = backend.strip().lower()
        if normalized not in {"system", "bundled", "default"}:
            self.post("error", "Usage: /router [status|system|bundled|default|restart]")
            return
        if normalized == "bundled" and not bundled_i2pd_allowed():
            self.post(
                "error",
                "Bundled router is disabled in this build; use /router system.",
            )
            return
        if normalized == "restart":
            await self._restart_router_runtime(
                f"Restarting router backend: {self._router_settings.backend}…"
            )
            return
        if normalized == "default":
            try:
                prefs_path = router_settings_path()
            except Exception:
                prefs_path = ""
            if prefs_path:
                try:
                    os.remove(prefs_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.post("error", f"Could not remove router_prefs.json: {exc}")
                    return
            await self._apply_router_settings(
                self._load_tui_router_settings(),
                explicit=False,
                reason=
                "Router backend reset to TUI default (system SAM unless prefs are saved)."
            )
            return
        if (
            self._router_settings.backend == normalized
            and self._router_settings_explicit
        ):
            self.post("system", f"Router backend already set to {normalized}.")
            return
        await self._apply_router_settings(
            replace(
                self._router_settings,
                backend=normalized,
                bundled_auto_start=(normalized == "bundled"),
            ),
            explicit=True,
            reason=f"Switching router backend to {normalized}…",
        )

    async def _execute_contacts_subcommand(self, args: list[str]) -> bool:
        if not args or args[0] in {"show", "list"}:
            self._show_contacts()
            return True
        sub = args[0]
        rest = args[1:]
        if sub == "use":
            if not rest:
                self.post("error", "Usage: /contacts use <index|b32-address>")
                return True
            peer = self._resolve_selector_to_peer(rest[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return True
            self._set_selected_peer(peer, remember=True, announce="Selected peer")
            return True
        if sub == "add":
            if not rest:
                self.post("error", "Usage: /contacts add <b32-address> [display_name] [note]")
                return True
            peer = normalize_peer_address(rest[0])
            if not peer:
                self.post("error", "Invalid peer address.")
                return True
            remember_peer(self._contact_book, peer)
            if len(rest) >= 2:
                display_name = rest[1]
                note = rest[2] if len(rest) >= 3 else ""
                set_peer_profile(
                    self._contact_book,
                    peer,
                    display_name=display_name,
                    note=note,
                )
            set_last_active_peer(self._contact_book, peer)
            self._save_contacts_book()
            self._set_selected_peer(peer, remember=True, announce="Selected peer")
            self.post("success", f"Saved peer {peer}.")
            return True
        if sub == "edit":
            if len(rest) < 1:
                self.post("error", "Usage: /contacts edit <index|b32-address> [display_name] [note]")
                return True
            peer = self._resolve_selector_to_peer(rest[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return True
            display_name = rest[1] if len(rest) >= 2 else ""
            note = rest[2] if len(rest) >= 3 else ""
            changed = set_peer_profile(
                self._contact_book,
                peer,
                display_name=display_name,
                note=note,
            )
            if changed:
                self._save_contacts_book()
                self.post("success", f"Updated saved peer {peer}.")
            else:
                self.post("system", f"No contact changes for {peer}.")
            return True
        if sub == "remove":
            if not rest:
                self.post("error", "Usage: /contacts remove <index|b32-address>")
                return True
            peer = self._resolve_selector_to_peer(rest[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return True
            if remove_peer(self._contact_book, peer):
                self._save_contacts_book()
                if self.selected_peer == peer:
                    self._set_selected_peer(None, remember=False)
                self.post("success", f"Removed saved peer {peer}.")
            else:
                self.post("system", f"Saved peer {peer} was not present.")
            return True
        if sub == "info":
            peer = self._resolve_selector_to_peer(rest[0] if rest else None)
            if not peer:
                self.post("error", "No active or selected peer.")
                return True
            self._show_contact_info(peer)
            return True
        return False

    async def _execute_command(self, raw: str) -> None:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            self.post("error", f"Command parse error: {exc}")
            return
        if not parts:
            return
        cmd = parts[0][1:].lower()
        args = parts[1:]
        cmd = self._normalize_command_alias(cmd)

        if cmd == "connect":
            target = self._resolve_selector_to_peer(args[0]) if args else self._current_target_peer()
            if not target:
                self.post("error", "Usage: /connect <b32-address> or select a saved peer first.")
                return
            if not self._set_selected_peer(target, remember=True, announce="Connecting to"):
                self.post("error", f"Invalid peer address: {target}")
                return
            self.run_worker(self.core.connect_to_peer(target))

        elif cmd == "disconnect":
            self.run_worker(self.core.disconnect())

        elif cmd == "save":
            if self.profile == TRANSIENT_PROFILE_NAME:
                self.post("error", "Cannot lock in TRANSIENT mode. Switch to a named profile.")
                return
            if self.core.stored_peer:
                self.post("error", f"Profile already locked to: {self.core.stored_peer}")
                return
            if not self.core.is_current_peer_verified_for_lock():
                self.post("error", "Peer address is not yet verified by a secure session.")
                return
            self.core.save_stored_peer(self.core.current_peer_addr or "")
            await self.core.ensure_blindbox_runtime_started()
            self._ensure_stored_peer_in_contact_book()
            self.post("success", f"Identity {self.profile} is now locked to this peer.")
            self._refresh_status_bar()

        elif cmd == "unlock":
            if not self.core.stored_peer:
                self.post("system", "Profile is not locked to a peer.")
                return
            unlocked = self.core.stored_peer
            self.core.clear_locked_peer()
            self.post("success", f"Removed Lock to peer for {unlocked}.")
            self._refresh_status_bar()

        elif cmd == "status":
            snap = self._build_status_snapshot()
            router_line = self._router_status_block()
            self.post_panel("Status", f"{snap.full}\n\n{router_line}\n\n{snap.technical}", border_style="blue")

        elif cmd == "contacts":
            handled = await self._execute_contacts_subcommand(args)
            if not handled:
                self.post("error", "Usage: /contacts [show|list|use|add|edit|remove|info] …")

        elif cmd == "contact-add":
            if not args:
                self.post("error", "Usage: /contact-add <b32-address> [display_name] [note]")
                return
            peer = normalize_peer_address(args[0])
            if not peer:
                self.post("error", "Invalid peer address.")
                return
            remember_peer(self._contact_book, peer)
            if len(args) >= 2:
                display_name = args[1]
                note = args[2] if len(args) >= 3 else ""
                set_peer_profile(self._contact_book, peer, display_name=display_name, note=note)
            set_last_active_peer(self._contact_book, peer)
            self._save_contacts_book()
            self._set_selected_peer(peer, remember=True, announce="Selected peer")
            self.post("success", f"Saved peer {peer}.")

        elif cmd == "contact-use":
            if not args:
                self.post("error", "Usage: /contact-use <index|b32-address>")
                return
            peer = self._resolve_selector_to_peer(args[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return
            self._set_selected_peer(peer, remember=True, announce="Selected peer")

        elif cmd == "contact-edit":
            if len(args) < 1:
                self.post("error", "Usage: /contact-edit <index|b32-address> [display_name] [note]")
                return
            peer = self._resolve_selector_to_peer(args[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return
            display_name = args[1] if len(args) >= 2 else ""
            note = args[2] if len(args) >= 3 else ""
            changed = set_peer_profile(self._contact_book, peer, display_name=display_name, note=note)
            if changed:
                self._save_contacts_book()
                self.post("success", f"Updated saved peer {peer}.")
            else:
                self.post("system", f"No contact changes for {peer}.")

        elif cmd == "contact-remove":
            if not args:
                self.post("error", "Usage: /contact-remove <index|b32-address>")
                return
            peer = self._resolve_selector_to_peer(args[0])
            if not peer:
                self.post("error", "Unknown contact selector.")
                return
            if remove_peer(self._contact_book, peer):
                self._save_contacts_book()
                if self.selected_peer == peer:
                    self._set_selected_peer(None, remember=False)
                self.post("success", f"Removed saved peer {peer}.")
            else:
                self.post("system", f"Saved peer {peer} was not present.")

        elif cmd == "contact-info":
            peer = self._resolve_selector_to_peer(args[0] if args else None)
            if not peer:
                self.post("error", "No active or selected peer.")
                return
            self._show_contact_info(peer)

        elif cmd == "recent":
            count = int(args[0]) if args and args[0].isdigit() else 10
            self._show_recent(count)

        elif cmd == "reply":
            if not args or not args[0].isdigit():
                self.post("error", "Usage: /reply <message-ref>. Use /recent to list refs.")
                return
            ref = self._find_recent_ref(int(args[0]))
            if ref is None:
                self.post("error", f"Unknown message ref #{args[0]}.")
                return
            prefix = format_reply_quote(ref.sender, ref.text)
            existing = self.compose_box.text
            self._set_compose_text(prefix + existing)
            if self._compose_draft_active_key is not None:
                self._compose_drafts[self._compose_draft_active_key] = self._compose_text_snapshot()
            self.post("system", f"Inserted reply quote for message #{ref.ref_id} into compose box.")

        elif cmd == "sendfile":
            if not args:
                self.post("error", "Usage: /sendfile <path>")
                return
            path = os.path.expanduser(args[0])
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return
            self.run_worker(self.core.send_file(path))

        elif cmd == "sendpic":
            if not args:
                self.post("error", "Usage: /sendpic <path>")
                return
            path = os.path.expanduser(args[0])
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return
            self.run_worker(self.core.send_image(path))

        elif cmd == "img":
            if not args:
                self.post("error", "Usage: /img <path>")
                return
            path = os.path.expanduser(args[0])
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return
            lines = render_braille(path)
            await self._send_image_me(lines)
            self.run_worker(self.core.send_image_lines(lines))

        elif cmd == "img-bw":
            if not args:
                self.post("error", "Usage: /img-bw <path>")
                return
            path = os.path.expanduser(args[0])
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return
            lines = render_bw(path)
            await self._send_image_me(lines)
            self.run_worker(self.core.send_image_lines(lines))

        elif cmd == "history":
            if not args or args[0] == "show":
                count = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 20
                self._show_history(count)
            elif args[0] in {"on", "off"}:
                self._history_enabled = args[0] == "on"
                self._save_history_enabled(self._history_enabled)
                if self._history_enabled:
                    self.post("success", "Chat history saving enabled.")
                    self._try_load_history(force=True)
                else:
                    self.post("system", "Chat history saving disabled.")
                    self._history_entries = []
                    self._history_dirty = False
                    self._history_loaded_for_peer = None
            elif args[0] == "clear":
                peer = self._resolve_selector_to_peer(args[1] if len(args) >= 2 else None)
                if not peer:
                    self.post("error", "No active or selected peer.")
                    return
                deleted = delete_history(
                    self.core.get_profile_data_dir(),
                    self.core.profile,
                    peer,
                    app_data_root=self.core.get_profiles_dir(),
                )
                if deleted:
                    if self._history_loaded_for_peer == normalize_peer_addr(peer):
                        self._history_entries = []
                        self._history_dirty = False
                    self.post("success", f"History cleared for {peer}.")
                else:
                    self.post("system", f"No saved history found for {peer}.")
            elif args[0] == "retention" and len(args) >= 3:
                try:
                    max_messages = int(args[1])
                    max_days = int(args[2])
                except ValueError:
                    self.post("error", "Usage: /history retention <max_messages> <days>")
                    return
                self._save_history_max_messages(max_messages)
                self._save_history_retention_days(max_days)
                retained, _ = apply_history_retention(
                    self._history_entries,
                    max_messages=max_messages,
                    max_age_days=max_days,
                )
                self._history_entries = list(retained)
                self._history_dirty = True
                self._save_history_if_needed()
                self.post("success", f"History retention updated: {max_messages} messages, {max_days} day(s).")
            else:
                self.post("error", "Usage: /history show [count] | on | off | clear [peer] | retention <max_messages> <days>")

        elif cmd == "history-clear":
            peer = self._resolve_selector_to_peer(args[0] if args else None)
            if not peer:
                self.post("error", "Usage: /clear-history [peer]")
                return
            deleted = delete_history(
                self.core.get_profile_data_dir(),
                self.core.profile,
                peer,
                app_data_root=self.core.get_profiles_dir(),
            )
            if deleted:
                if self._history_loaded_for_peer == normalize_peer_addr(peer):
                    self._history_entries = []
                    self._history_dirty = False
                self.post("success", f"History cleared for {peer}.")
            else:
                self.post("system", f"No saved history found for {peer}.")

        elif cmd == "history-retention":
            if len(args) < 2:
                self.post("error", "Usage: /history-retention <max_messages> <days>")
                return
            try:
                max_messages = int(args[0])
                max_days = int(args[1])
            except ValueError:
                self.post("error", "Usage: /history-retention <max_messages> <days>")
                return
            self._save_history_max_messages(max_messages)
            self._save_history_retention_days(max_days)
            retained, _ = apply_history_retention(
                self._history_entries,
                max_messages=max_messages,
                max_age_days=max_days,
            )
            self._history_entries = list(retained)
            self._history_dirty = True
            self._save_history_if_needed()
            self.post(
                "success",
                f"History retention updated: {max_messages} messages, {max_days} day(s).",
            )

        elif cmd == "blindbox":
            self._show_blindbox_diagnostics()

        elif cmd == "actions":
            self._show_actions_screen()

        elif cmd == "transfers":
            self._show_media_screen()

        elif cmd == "launcher":
            self._show_launcher_screen()

        elif cmd == "trust-info":
            peer = self._resolve_selector_to_peer(args[0] if args else None)
            if not peer:
                self.post("error", "No active or selected peer.")
                return
            info = self.core.get_peer_trust_info(peer)
            if not info:
                self.post("error", f"Invalid peer address: {peer}")
                return
            text = (
                f"Peer: {info.peer_normalized}\n"
                f"Pinned: {'yes' if info.pinned else 'no'}\n"
                f"Fingerprint: {info.fingerprint_short or '—'}"
            )
            self.post_panel("Trust info", text, border_style="yellow")

        elif cmd == "forget-pin":
            peer = self._resolve_selector_to_peer(args[0] if args else None)
            if not peer:
                self.post("error", "No active or selected peer.")
                return
            removed = self.core.forget_pinned_peer_key(peer)
            if removed:
                self.post("success", f"Removed trusted key pin for {peer}.")
            else:
                self.post("system", f"No trusted key pin stored for {peer}.")

        elif cmd == "copyaddr":
            if not self.core.my_dest:
                self.post("error", "Local address is not ready yet.")
                return
            addr = self.core.my_dest.base32 + ".b32.i2p"
            if pyperclip is not None:
                try:
                    pyperclip.copy(addr)
                    self.post("success", f"Copied local address to clipboard: {addr}")
                except Exception:
                    self.post("system", f"Local address: {addr}")
            else:
                self.post("system", f"Local address: {addr}")

        elif cmd == "appdir":
            self.post("system", f"Application data directory: {get_profiles_dir()}")

        elif cmd == "router":
            if not args or args[0] == "status":
                self._show_router_screen()
            elif args[0] == "restart":
                await self._restart_router_runtime(
                    f"Restarting router backend: {self._router_settings.backend}…"
                )
            else:
                await self._set_router_backend(args[0])

        elif cmd == "backups":
            self._show_backups_screen()

        elif cmd == "settings":
            self._show_settings_screen()

        elif cmd == "history-screen":
            self._show_history_screen()

        elif cmd == "diagnostics-screen":
            self._show_diagnostics_screen()

        elif cmd == "profiles":
            rows = [TRANSIENT_PROFILE_NAME] + [p for p in list_profile_names_in_app_data() if p != TRANSIENT_PROFILE_NAME]
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Current", width=7)
            table.add_column("Profile")
            for item in rows:
                table.add_row("*" if item == self.profile else "", item)
            self.post_panel("Profiles", table, border_style="magenta")

        elif cmd == "profile":
            if not args:
                self.post("error", "Usage: /profile switch <name> | import-dat <path> [name]")
                return
            if args[0] == "switch" and len(args) >= 2:
                await self._switch_profile(args[1])
            elif args[0] == "import-dat" and len(args) >= 2:
                source_path = os.path.expanduser(args[1])
                if not os.path.exists(source_path):
                    self.post("error", f"Profile file not found: {source_path}")
                    return
                base_name = args[2] if len(args) >= 3 else Path(source_path).stem
                try:
                    imported = await asyncio.to_thread(
                        import_profile_dat_atomic,
                        source_path,
                        get_profiles_dir(),
                        ensure_valid_profile_name(base_name),
                    )
                except Exception as exc:
                    self.post("error", f"Failed to import profile .dat: {exc}")
                    return
                self.post("success", f"Imported profile as {imported}.")
                await self._switch_profile(imported)
            else:
                self.post("error", "Usage: /profile switch <name> | import-dat <path> [name]")

        elif cmd == "profile-import-dat":
            if not args:
                self.post("error", "Usage: /load-profile <path> [name]")
                return
            source_path = os.path.expanduser(args[0])
            if not os.path.exists(source_path):
                self.post("error", f"Profile file not found: {source_path}")
                return
            base_name = args[1] if len(args) >= 2 else Path(source_path).stem
            try:
                imported = await asyncio.to_thread(
                    import_profile_dat_atomic,
                    source_path,
                    get_profiles_dir(),
                    ensure_valid_profile_name(base_name),
                )
            except Exception as exc:
                self.post("error", f"Failed to import profile .dat: {exc}")
                return
            self.post("success", f"Imported profile as {imported}.")
            await self._switch_profile(imported)

        elif cmd == "backup":
            if len(args) < 4:
                self.post(
                    "error",
                    "Usage: /backup profile export <path> <passphrase> | /backup profile import <path> <passphrase> | /backup history export <path> <passphrase> | /backup history import <path> <passphrase> [overwrite|skip]",
                )
                return
            domain, action, path, passphrase = args[:4]
            path = os.path.expanduser(path)
            try:
                if domain == "profile" and action == "export":
                    await self._export_profile_backup_ui(path, passphrase)
                elif domain == "profile" and action == "import":
                    await self._import_profile_backup_ui(path, passphrase)
                elif domain == "history" and action == "export":
                    await self._export_history_backup_ui(path, passphrase)
                elif domain == "history" and action == "import":
                    conflict_mode = args[4] if len(args) >= 5 else "skip"
                    await self._import_history_backup_ui(
                        path,
                        passphrase,
                        conflict_mode=conflict_mode,
                    )
                else:
                    self.post("error", "Unsupported backup command.")
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        elif cmd == "backup-profile-export":
            if len(args) < 2:
                self.post("error", "Usage: /export-profile-backup <path> <passphrase>")
                return
            path = os.path.expanduser(args[0])
            passphrase = args[1]
            try:
                await self._export_profile_backup_ui(path, passphrase)
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        elif cmd == "backup-profile-import":
            if len(args) < 2:
                self.post("error", "Usage: /import-profile-backup <path> <passphrase>")
                return
            path = os.path.expanduser(args[0])
            passphrase = args[1]
            try:
                await self._import_profile_backup_ui(path, passphrase)
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        elif cmd == "backup-history-export":
            if len(args) < 2:
                self.post("error", "Usage: /export-history-backup <path> <passphrase>")
                return
            path = os.path.expanduser(args[0])
            passphrase = args[1]
            try:
                await self._export_history_backup_ui(path, passphrase)
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        elif cmd == "backup-history-import":
            if len(args) < 2:
                self.post("error", "Usage: /import-history-backup <path> <passphrase> [overwrite|skip]")
                return
            path = os.path.expanduser(args[0])
            passphrase = args[1]
            conflict_mode = args[2] if len(args) >= 3 else "skip"
            try:
                await self._import_history_backup_ui(
                    path,
                    passphrase,
                    conflict_mode=conflict_mode,
                )
            except BackupError as exc:
                self.post("error", str(exc))
            except Exception as exc:
                self.post("error", f"Backup command failed: {exc}")

        elif cmd == "init":
            self._start_core_session_init_background()

        elif cmd == "updates":
            self.run_worker(self._check_updates())

        elif cmd == "help":
            self.show_help()

        else:
            self.post("error", f"Unknown command: /{cmd}. Type /help.")

        if cmd != "reply":
            self._set_compose_text("")
            if self._compose_draft_active_key is not None:
                self._compose_drafts[self._compose_draft_active_key] = ""
            self._flush_compose_drafts_to_disk()
        self._refresh_status_bar()

    async def _send_image_me(self, lines: list[str]) -> None:
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        img_text = "\n".join(lines)
        panel = Panel(
            img_text,
            title=f"[#5f5f5f][{now_utc} UTC][/] [bold green]Me[/] [dim]ASCII image[/]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
        self.chat_log.write(Align(panel, align="left"), expand=True)

    def show_help(self) -> None:
        self.post("help", "Compose: Enter = newline, Ctrl+S / Ctrl+Enter = send current compose buffer.")
        self.post("help", "Primary function keys: F1 actions, F2 contacts, F3 media, F4 settings, F5 history, F6 diagnostics.")
        self.post("help", "Connection: /connect <addr>, /disconnect, /save, /unlock, /status")
        self.post("help", "Messaging/media: /sendfile <path> (/send-file), /sendpic <path> (/send-picture), /img <path>, /img-bw <path>, /recent [count], /reply <ref>")
        self.post("help", "Launcher: optional via /launcher (/menu) if you want the quick launcher overlay.")
        self.post("help", "Contacts: F2 / Ctrl+B or /contacts to view the book. Also: /contacts use|add|edit|remove|info … (legacy /contact-* commands still work).")
        self.post("help", "Media/transfers: F3 / Ctrl+P or /transfers (/media) for send file/picture and recent transfer events.")
        self.post("help", "Router: F5 or /router status|system|bundled|default|restart.")
        self.post("help", "History: F6 or /history-browser for the history browser. Diagnostics: F7 or /diagnostics for BlindBox/trust/updates/app-data.")
        self.post("help", "History: /history show [count], /history on|off, /history clear [peer], /history retention <max_messages> <days>, /clear-history [peer], /history-retention <max_messages> <days>")
        self.post("help", "Profiles/data: /profiles, /profile switch <name>, /profile import-dat <path> [name], /load-profile <path> [name], /backup …")
        self.post("help", "Actions: F1 or Ctrl+D /actions for connect/disconnect, lock peer and copy address.")
        self.post("help", "Backups: open from Settings (F4) → Backups… or use /backups. Also /export-profile-backup <path> <passphrase>, /import-profile-backup <path> <passphrase>, /export-history-backup <path> <passphrase>, /import-history-backup <path> <passphrase> [overwrite|skip]")
        self.post("help", "Settings: F4 or /settings for history/profile-import form; backups are nested there.")
        self.post("help", "TOFU: TUI now shows trust dialogs for new peer keys and key mismatches during handshake.")
        self.post("help", "Utility: /help, q or Ctrl+Q to quit")


class TuiContactsScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("enter", "use_selected", "Use"),
        ("a", "add_contact", "Add"),
        ("e", "edit_selected", "Edit"),
        ("i", "show_info", "Info"),
        ("d", "remove_selected", "Remove"),
        ("r", "refresh", "Refresh"),
    ]

    CSS = """
    TuiContactsScreen {
        align: center middle;
    }
    #contacts_dialog {
        width: 88;
        height: 28;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #contacts_table {
        height: 1fr;
        margin: 1 0;
    }
    #contacts_help {
        color: $text-muted;
        height: auto;
    }
    #contacts_buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="contacts_dialog"):
            yield Static("Saved peers", id="contacts_title")
            yield DataTable(id="contacts_table")
            yield Static(
                "Enter use · I info · D remove · Esc close\n"
                "For add/edit use commands: /contacts add|edit …",
                id="contacts_help",
            )
            with Horizontal(id="contacts_buttons"):
                yield Button("Add", id="contacts_add")
                yield Button("Edit", id="contacts_edit")
                yield Button("Use", id="contacts_use", variant="primary")
                yield Button("Info", id="contacts_info")
                yield Button("Remove", id="contacts_remove", variant="warning")
                yield Button("Close", id="contacts_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    @property
    def table(self) -> DataTable:
        return self.query_one("#contacts_table", DataTable)

    def on_mount(self) -> None:
        self._refresh_table()
        self.table.focus()

    def _selected_peer(self) -> Optional[str]:
        if not self.host._contact_book.contacts:
            return None
        row = self.table.cursor_row
        if row < 0 or row >= len(self.host._contact_book.contacts):
            return None
        return self.host._contact_book.contacts[row].addr

    def _refresh_table(self) -> None:
        table = self.table
        table.clear(columns=True)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("#", "Sel", "Name", "Address", "Flags", "Preview")
        contacts = self.host._contact_book.contacts
        if not contacts:
            table.add_row("—", "", "No saved peers", "Use /contacts add …", "", "", key="empty")
            table.disabled = True
            return
        table.disabled = False
        for idx, record in enumerate(contacts, start=1):
            flags: list[str] = []
            if record.addr == self.host.core.stored_peer:
                flags.append("lock")
            trust = self.host.core.get_peer_trust_info(record.addr)
            if trust and trust.pinned:
                flags.append("pin")
            if record.addr == self.host.selected_peer:
                flags.append("active")
            table.add_row(
                str(idx),
                "▶" if record.addr == self.host.selected_peer else "",
                record.display_name or "—",
                self.host._short_peer(record.addr),
                ",".join(flags) or "—",
                record.last_preview or record.note or "—",
                key=record.addr,
            )
        table.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh_table()

    def action_add_contact(self) -> None:
        self.host._show_contact_editor()

    def action_edit_selected(self) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        self.host._show_contact_editor(peer=peer)

    def action_use_selected(self) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        self.host._set_selected_peer(peer, remember=True, announce="Selected peer")
        self.dismiss(None)

    def action_show_info(self) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        self.host._show_contact_info(peer)

    def action_remove_selected(self) -> None:
        peer = self._selected_peer()
        if not peer:
            return
        self.host.push_screen(
            TuiConfirmScreen(
                title="Remove saved peer?",
                message=f"Remove {peer} from saved peers?",
                confirm_label="Remove",
            ),
            callback=lambda confirmed: self._handle_remove_confirmed(peer, confirmed),
        )

    def _handle_remove_confirmed(self, peer: str, confirmed: bool | None) -> None:
        if not confirmed:
            return
        if remove_peer(self.host._contact_book, peer):
            self.host._save_contacts_book()
            if self.host.selected_peer == peer:
                self.host._set_selected_peer(None, remember=False)
            self.host.post("success", f"Removed saved peer {peer}.")
        else:
            self.host.post("system", f"Saved peer {peer} was not present.")
        self._refresh_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "contacts_add": self.action_add_contact,
            "contacts_edit": self.action_edit_selected,
            "contacts_use": self.action_use_selected,
            "contacts_info": self.action_show_info,
            "contacts_remove": self.action_remove_selected,
            "contacts_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        self.action_use_selected()


class TuiContactEditorScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("ctrl+s", "save_contact", "Save"),
    ]

    CSS = """
    TuiContactEditorScreen {
        align: center middle;
    }
    #contact_editor_dialog {
        width: 72;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    .contact_editor_label {
        margin-top: 1;
    }
    .contact_editor_input {
        margin-top: 0;
    }
    #contact_editor_help {
        color: $text-muted;
        margin-top: 1;
    }
    #contact_editor_buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def __init__(self, *, peer: Optional[str] = None) -> None:
        super().__init__()
        self._peer = peer

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def compose(self) -> ComposeResult:
        editing = self._peer is not None
        title = "Edit saved peer" if editing else "Add saved peer"
        with Vertical(id="contact_editor_dialog"):
            yield Static(title)
            yield Static("Address", classes="contact_editor_label")
            yield Input(
                self._peer or "",
                placeholder="peer.b32.i2p",
                id="contact_editor_addr",
                classes="contact_editor_input",
                disabled=editing,
            )
            yield Static("Display name", classes="contact_editor_label")
            yield Input("", placeholder="Optional name", id="contact_editor_name", classes="contact_editor_input")
            yield Static("Note", classes="contact_editor_label")
            yield Input("", placeholder="Optional note", id="contact_editor_note", classes="contact_editor_input")
            yield Static("Ctrl+S save · Esc close", id="contact_editor_help")
            with Horizontal(id="contact_editor_buttons"):
                yield Button("Save", id="contact_editor_save", variant="primary")
                yield Button("Close", id="contact_editor_close")

    def on_mount(self) -> None:
        if self._peer is None:
            self.query_one("#contact_editor_addr", Input).focus()
            return
        record = self.host._contact_book.get(self._peer) or ContactRecord(addr=self._peer)
        self.query_one("#contact_editor_name", Input).value = record.display_name
        self.query_one("#contact_editor_note", Input).value = record.note
        self.query_one("#contact_editor_name", Input).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_save_contact(self) -> None:
        addr_input = self.query_one("#contact_editor_addr", Input)
        name_input = self.query_one("#contact_editor_name", Input)
        note_input = self.query_one("#contact_editor_note", Input)
        peer = normalize_peer_address(addr_input.value.strip())
        if not peer:
            self.host.post("error", "Invalid peer address.")
            return
        remember_peer(self.host._contact_book, peer)
        set_peer_profile(
            self.host._contact_book,
            peer,
            display_name=name_input.value.strip(),
            note=note_input.value.strip(),
        )
        set_last_active_peer(self.host._contact_book, peer)
        self.host._save_contacts_book()
        self.host._set_selected_peer(peer, remember=True, announce="Selected peer")
        self.host.post("success", f"Saved peer {peer}.")
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "contact_editor_save":
            self.action_save_contact()
        elif event.button.id == "contact_editor_close":
            self.action_close()


class TuiRouterScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("s", "choose_system", "System"),
        ("b", "choose_bundled", "Bundled"),
        ("enter", "apply_selected", "Apply"),
        ("d", "set_default", "Default"),
        ("r", "restart_router", "Restart"),
    ]

    CSS = """
    TuiRouterScreen {
        align: center middle;
    }
    #router_dialog {
        width: 78;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #router_status {
        margin: 1 0;
    }
    #router_help {
        color: $text-muted;
        margin-bottom: 1;
    }
    .router_label {
        margin-top: 1;
    }
    #router_backend_row {
        height: auto;
        margin: 1 0;
        align-horizontal: center;
    }
    .router_backend_label {
        width: 1fr;
        content-align: center middle;
        min-height: 3;
        text-style: bold;
    }
    #router_backend_caption {
        color: $text-muted;
        margin-bottom: 1;
        content-align: center middle;
    }
    .router_buttons_row {
        height: auto;
        width: 100%;
        margin-top: 1;
    }
    .router_button {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="router_dialog"):
            yield Static("Router backend", id="router_title")
            yield Static("", id="router_status")
            with Horizontal(id="router_backend_row"):
                yield Static("System I2P", classes="router_backend_label")
                yield Switch(False, id="router_backend_toggle")
                yield Static("Bundled i2pd", classes="router_backend_label")
            yield Static("", id="router_backend_caption")
            yield Static("System SAM host", classes="router_label")
            yield Input("", id="router_system_host")
            yield Static("System SAM port", classes="router_label")
            yield Input("", id="router_system_port")
            yield Static("Bundled SAM host", classes="router_label")
            yield Input("", id="router_bundled_host")
            yield Static("Bundled SAM port", classes="router_label")
            yield Input("", id="router_bundled_sam_port")
            yield Static("Bundled HTTP proxy port", classes="router_label")
            yield Input("", id="router_bundled_http_port")
            yield Static("Bundled SOCKS proxy port", classes="router_label")
            yield Input("", id="router_bundled_socks_port")
            yield Static("Bundled control HTTP port", classes="router_label")
            yield Input("", id="router_bundled_control_port")
            yield Static(
                "Switch left = System I2P, right = Bundled i2pd. S/B switch · Enter apply · D default · R restart · Esc close",
                id="router_help",
            )
            with Horizontal(classes="router_buttons_row"):
                yield Button("Apply selected", id="router_apply", variant="primary", classes="router_button")
                yield Button("Default", id="router_default", classes="router_button")
            with Horizontal(classes="router_buttons_row"):
                yield Button("Restart", id="router_restart", variant="warning", classes="router_button")
                yield Button("Close", id="router_close", classes="router_button")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        self._refresh_status()
        settings = self.host._router_settings
        self.query_one("#router_backend_toggle", Switch).value = (
            settings.backend == "bundled"
        )
        self.query_one("#router_system_host", Input).value = settings.system_sam_host
        self.query_one("#router_system_port", Input).value = str(settings.system_sam_port)
        self.query_one("#router_bundled_host", Input).value = settings.bundled_sam_host
        self.query_one("#router_bundled_sam_port", Input).value = str(settings.bundled_sam_port)
        self.query_one("#router_bundled_http_port", Input).value = str(settings.bundled_http_proxy_port)
        self.query_one("#router_bundled_socks_port", Input).value = str(settings.bundled_socks_proxy_port)
        self.query_one("#router_bundled_control_port", Input).value = str(settings.bundled_control_http_port)
        self.set_interval(1.0, self._refresh_status)
        self.query_one("#router_backend_toggle", Switch).focus()

    def _refresh_status(self) -> None:
        status = self.query_one("#router_status", Static)
        backend_toggle = self.query_one("#router_backend_toggle", Switch)
        caption = self.query_one("#router_backend_caption", Static)
        if not bundled_i2pd_allowed():
            backend_toggle.value = False
            backend_toggle.disabled = True
        selected_backend = "Bundled i2pd" if backend_toggle.value else "System I2P"
        if not bundled_i2pd_allowed():
            caption.update("Bundled router is disabled in this build. Selected backend: System I2P")
        else:
            caption.update(f"Selected backend: {selected_backend}")
        status.update(
            self.host._router_status_block()
            + "\n\n"
            + (
                "Bundled router is disabled in this build; external/system SAM only."
                if not bundled_i2pd_allowed()
                else
                "TUI default = external/system SAM until you explicitly save bundled."
                if not self.host._router_settings_explicit
                else "This backend choice is saved in router_prefs.json and reused next start."
            )
        )

    def action_close(self) -> None:
        self.dismiss(None)

    def _settings_from_inputs(self) -> Optional[RouterSettings]:
        try:
            return replace(
                self.host._router_settings,
                system_sam_host=self.query_one("#router_system_host", Input).value.strip() or "127.0.0.1",
                system_sam_port=int(self.query_one("#router_system_port", Input).value.strip()),
                bundled_sam_host=self.query_one("#router_bundled_host", Input).value.strip() or "127.0.0.1",
                bundled_sam_port=int(self.query_one("#router_bundled_sam_port", Input).value.strip()),
                bundled_http_proxy_port=int(self.query_one("#router_bundled_http_port", Input).value.strip()),
                bundled_socks_proxy_port=int(self.query_one("#router_bundled_socks_port", Input).value.strip()),
                bundled_control_http_port=int(self.query_one("#router_bundled_control_port", Input).value.strip()),
            )
        except ValueError:
            self.host.post("error", "Router ports must be integers.")
            return None

    def _selected_backend(self) -> str:
        if not bundled_i2pd_allowed():
            return "system"
        return "bundled" if self.query_one("#router_backend_toggle", Switch).value else "system"

    def _schedule_router_change(self, backend: str) -> None:
        self.dismiss(None)
        asyncio.create_task(self.host._set_router_backend(backend))

    def action_choose_system(self) -> None:
        self.query_one("#router_backend_toggle", Switch).value = False
        self._refresh_status()

    def action_choose_bundled(self) -> None:
        if not bundled_i2pd_allowed():
            return
        self.query_one("#router_backend_toggle", Switch).value = True
        self._refresh_status()

    def action_apply_selected(self) -> None:
        settings = self._settings_from_inputs()
        if settings is None:
            return
        backend = self._selected_backend()
        self.dismiss(None)
        asyncio.create_task(
            self.host._apply_router_settings(
                replace(
                    settings,
                    backend=backend,
                    bundled_auto_start=(backend == "bundled"),
                ),
                explicit=True,
                reason=f"Switching router backend to {backend}…",
            )
        )

    def action_set_default(self) -> None:
        self._schedule_router_change("default")

    def action_restart_router(self) -> None:
        self.dismiss(None)
        asyncio.create_task(
            self.host._restart_router_runtime(
                f"Restarting router backend: {self.host._router_settings.backend}…"
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "router_apply": self.action_apply_selected,
            "router_default": self.action_set_default,
            "router_restart": self.action_restart_router,
            "router_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiBackupsScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("ctrl+s", "export_profile", "Export profile"),
    ]

    CSS = """
    TuiBackupsScreen {
        align: center middle;
    }
    #backups_dialog {
        width: 78;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    .backups_label {
        margin-top: 1;
    }
    #backups_help {
        color: $text-muted;
        margin: 1 0;
    }
    #backups_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="backups_dialog"):
            yield Static("Backups / import / export")
            yield Static("Path", classes="backups_label")
            yield Input("", placeholder="Path to backup file", id="backups_path")
            yield Static("Passphrase", classes="backups_label")
            yield Input("", password=True, placeholder="Passphrase", id="backups_passphrase")
            yield Static("History import mode", classes="backups_label")
            yield Input("skip", placeholder="skip or overwrite", id="backups_conflict")
            yield Static(
                "Buttons run immediately using the current path and passphrase.\n"
                "Ctrl+S exports profile backup. Esc closes.",
                id="backups_help",
            )
            with Horizontal(id="backups_buttons"):
                yield Button("Export profile", id="backups_profile_export", variant="primary")
                yield Button("Import profile", id="backups_profile_import")
                yield Button("Export history", id="backups_history_export")
                yield Button("Import history", id="backups_history_import", variant="warning")
                yield Button("Close", id="backups_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        self.query_one("#backups_path", Input).focus()

    def _values(self) -> tuple[str, str, str]:
        path = self.query_one("#backups_path", Input).value.strip()
        passphrase = self.query_one("#backups_passphrase", Input).value
        conflict = self.query_one("#backups_conflict", Input).value.strip() or "skip"
        return path, passphrase, conflict

    def action_close(self) -> None:
        self.dismiss(None)

    def _run(self, action: str) -> None:
        path, passphrase, conflict = self._values()
        if not path or not passphrase:
            self.host.post("error", "Path and passphrase are required.")
            return
        self.dismiss(None)
        self.host._run_backup_action(action, os.path.expanduser(path), passphrase, conflict)

    def action_export_profile(self) -> None:
        self._run("profile-export")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "backups_profile_export": lambda: self._run("profile-export"),
            "backups_profile_import": lambda: self._run("profile-import"),
            "backups_history_export": lambda: self._run("history-export"),
            "backups_history_import": lambda: self._run("history-import"),
            "backups_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiLauncherScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("a", "open_actions", "Actions"),
        ("c", "open_contacts", "Contacts"),
        ("h", "open_history", "History"),
        ("d", "open_diagnostics", "Diagnostics"),
        ("m", "open_media", "Media"),
        ("r", "open_router", "Router"),
        ("s", "open_settings", "Settings"),
        ("?", "show_help_panel", "Help"),
    ]

    CSS = """
    TuiLauncherScreen {
        align: center middle;
    }
    #launcher_dialog {
        width: 82;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #launcher_summary {
        margin: 1 0;
    }
    #launcher_help {
        color: $text-muted;
        margin: 1 0;
    }
    #launcher_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="launcher_dialog"):
            yield Static("Quick launcher")
            yield Static("", id="launcher_summary")
            yield Static(
                "A actions · C contacts · D diagnostics · H history · M media · S settings · R router · ? help · Esc close",
                id="launcher_help",
            )
            with Horizontal(id="launcher_buttons"):
                yield Button("Actions", id="launcher_actions", variant="primary")
                yield Button("Contacts", id="launcher_contacts")
                yield Button("History", id="launcher_history")
                yield Button("Diagnostics", id="launcher_diagnostics")
                yield Button("Media", id="launcher_media")
                yield Button("Settings", id="launcher_settings")
                yield Button("Router", id="launcher_router")
                yield Button("Help", id="launcher_help_btn")
                yield Button("Close", id="launcher_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        self._refresh_summary()
        self.set_interval(1.0, self._refresh_summary)
        self.query_one("#launcher_actions", Button).focus()

    def _refresh_summary(self) -> None:
        self.query_one("#launcher_summary", Static).update(
            self.host._launcher_summary_text()
        )

    def action_close(self) -> None:
        self.dismiss(None)

    def _open(self, action: str) -> None:
        self.dismiss(None)
        mapping = {
            "actions": self.host._show_actions_screen,
            "contacts": self.host._show_contacts,
            "history": self.host._show_history_screen,
            "diagnostics": self.host._show_diagnostics_screen,
            "media": self.host._show_media_screen,
            "settings": self.host._show_settings_screen,
            "router": self.host._show_router_screen,
            "help": self.host.show_help,
        }
        target = mapping.get(action)
        if target is not None:
            target()

    def action_open_actions(self) -> None:
        self._open("actions")

    def action_open_contacts(self) -> None:
        self._open("contacts")

    def action_open_history(self) -> None:
        self._open("history")

    def action_open_diagnostics(self) -> None:
        self._open("diagnostics")

    def action_open_media(self) -> None:
        self._open("media")

    def action_open_settings(self) -> None:
        self._open("settings")

    def action_open_router(self) -> None:
        self._open("router")

    def action_show_help_panel(self) -> None:
        self._open("help")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "launcher_actions": self.action_open_actions,
            "launcher_contacts": self.action_open_contacts,
            "launcher_history": self.action_open_history,
            "launcher_diagnostics": self.action_open_diagnostics,
            "launcher_media": self.action_open_media,
            "launcher_settings": self.action_open_settings,
            "launcher_router": self.action_open_router,
            "launcher_help_btn": self.action_show_help_panel,
            "launcher_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiActionsScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("c", "connect_peer", "Connect"),
        ("x", "disconnect_peer", "Disconnect"),
        ("l", "lock_peer", "Lock"),
        ("y", "copy_address", "Copy address"),
        ("r", "refresh", "Refresh"),
    ]

    CSS = """
    TuiActionsScreen {
        align: center middle;
    }
    #actions_dialog {
        width: 88;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #actions_summary {
        margin: 1 0;
    }
    .actions_label {
        margin-top: 1;
    }
    #actions_help {
        color: $text-muted;
        margin: 1 0;
    }
    .actions_buttons_row {
        height: auto;
        width: 100%;
        margin-top: 1;
    }
    .actions_button {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="actions_dialog"):
            yield Static("Actions")
            yield Static("", id="actions_summary")
            yield Static("Peer", classes="actions_label")
            yield Input("", placeholder="peer.b32.i2p", id="actions_peer")
            yield Static(
                "C connect · X disconnect · L lock · Y copy my addr · R refresh",
                id="actions_help",
            )
            with Horizontal(classes="actions_buttons_row"):
                yield Button("Connect", id="actions_connect", variant="primary", classes="actions_button")
                yield Button("Disconnect", id="actions_disconnect", variant="warning", classes="actions_button")
                yield Button("Lock peer", id="actions_lock", classes="actions_button")
                yield Button("Copy my addr", id="actions_copy", classes="actions_button")
            with Horizontal(classes="actions_buttons_row"):
                yield Button("Close", id="actions_close", classes="actions_button")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        peer = self.host._current_target_peer() or ""
        self.query_one("#actions_peer", Input).value = peer
        self._refresh_summary()
        self.set_interval(0.75, self._refresh_summary)
        self.query_one("#actions_peer", Input).focus()

    def _refresh_summary(self) -> None:
        summary = self.query_one("#actions_summary", Static)
        summary.update(self.host._actions_summary_text())
        connect_btn = self.query_one("#actions_connect", Button)
        disconnect_btn = self.query_one("#actions_disconnect", Button)
        lock_btn = self.query_one("#actions_lock", Button)
        peer = self._peer_value()
        connect_btn.disabled = (
            peer is None
            or self.host.core.conn is not None
            or self.host.core.is_outbound_connect_busy()
        )
        disconnect_btn.disabled = self.host.core.conn is None
        lock_btn.disabled = not self.host.core.is_current_peer_verified_for_lock()

    def _peer_value(self) -> Optional[str]:
        raw = self.query_one("#actions_peer", Input).value.strip()
        if not raw:
            return self.host._current_target_peer()
        return normalize_peer_address(raw)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh_summary()

    def action_connect_peer(self) -> None:
        peer = self._peer_value()
        if not peer:
            self.host.post("error", "Enter a peer .b32.i2p address first.")
            return
        self.host._set_selected_peer(peer, remember=True, announce="Connecting to")
        self.dismiss(None)
        self.host.run_worker(self.host.core.connect_to_peer(peer))

    def action_disconnect_peer(self) -> None:
        self.dismiss(None)
        self.host.run_worker(self.host.core.disconnect())

    def action_lock_peer(self) -> None:
        self.dismiss(None)
        asyncio.create_task(self.host._execute_command("/save"))

    def action_copy_address(self) -> None:
        self.dismiss(None)
        asyncio.create_task(self.host._execute_command("/copyaddr"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "actions_connect": self.action_connect_peer,
            "actions_disconnect": self.action_disconnect_peer,
            "actions_lock": self.action_lock_peer,
            "actions_copy": self.action_copy_address,
            "actions_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiMediaScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("ctrl+s", "send_file", "Send file"),
        ("ctrl+p", "send_picture", "Send picture"),
        ("b", "send_bw", "BW"),
        ("a", "send_ascii", "ASCII"),
        ("r", "refresh", "Refresh"),
    ]

    CSS = """
    TuiMediaScreen {
        align: center middle;
    }
    #media_dialog {
        width: 86;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #media_summary {
        margin: 1 0;
    }
    .media_label {
        margin-top: 1;
    }
    #media_table {
        height: 10;
        margin: 1 0;
    }
    #media_help {
        color: $text-muted;
        margin: 1 0;
    }
    #media_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="media_dialog"):
            yield Static("Media / transfers")
            yield Static("", id="media_summary")
            yield Static("File or image path", classes="media_label")
            yield Input("", placeholder="/path/to/file.png", id="media_path")
            yield DataTable(id="media_table")
            yield Static(
                "Ctrl+S send file · Ctrl+P send picture · A ASCII/Braille · B BW · Esc close",
                id="media_help",
            )
            with Horizontal(id="media_buttons"):
                yield Button("Send file", id="media_send_file", variant="primary")
                yield Button("Send picture", id="media_send_picture")
                yield Button("Send ASCII", id="media_send_ascii")
                yield Button("Send BW", id="media_send_bw")
                yield Button("Close", id="media_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    @property
    def table(self) -> DataTable:
        return self.query_one("#media_table", DataTable)

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(0.75, self._refresh)
        self.query_one("#media_path", Input).focus()

    def _path(self) -> str:
        return os.path.expanduser(self.query_one("#media_path", Input).value.strip())

    def _refresh(self) -> None:
        self.query_one("#media_summary", Static).update(self.host._media_summary_text())
        table = self.table
        table.clear(columns=True)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("When", "Kind", "File", "Detail")
        if not self.host._recent_transfers:
            table.add_row("—", "", "No recent transfers", "", key="empty")
            table.disabled = True
            return
        table.disabled = False
        for item in list(self.host._recent_transfers)[:20]:
            table.add_row(item.timestamp, item.kind, item.filename, item.detail, key=f"{item.timestamp}:{item.filename}")
        has_peer = self.host._current_target_peer() is not None
        for button_id in (
            "#media_send_file",
            "#media_send_picture",
            "#media_send_ascii",
            "#media_send_bw",
        ):
            self.query_one(button_id, Button).disabled = not has_peer

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh()

    def _require_path(self) -> Optional[str]:
        path = self._path()
        if not path:
            self.host.post("error", "Enter a file path first.")
            return None
        if not os.path.exists(path):
            self.host.post("error", f"File not found: {path}")
            return None
        return path

    def action_send_file(self) -> None:
        path = self._require_path()
        if not path:
            return
        self.host._record_transfer_event("file", path, "queued for sending")
        self.dismiss(None)
        self.host.run_worker(self.host.core.send_file(path))

    def action_send_picture(self) -> None:
        path = self._require_path()
        if not path:
            return
        self.host._record_transfer_event("image", path, "queued for sending")
        self.dismiss(None)
        self.host.run_worker(self.host.core.send_image(path))

    def action_send_ascii(self) -> None:
        path = self._require_path()
        if not path:
            return

        async def _runner() -> None:
            lines = render_braille(path)
            await self.host._send_image_me(lines)
            self.host._record_transfer_event("ascii", path, "queued for sending")
            self.host.run_worker(self.host.core.send_image_lines(lines))

        self.dismiss(None)
        asyncio.create_task(_runner())

    def action_send_bw(self) -> None:
        path = self._require_path()
        if not path:
            return

        async def _runner() -> None:
            lines = render_bw(path)
            await self.host._send_image_me(lines)
            self.host._record_transfer_event("bw", path, "queued for sending")
            self.host.run_worker(self.host.core.send_image_lines(lines))

        self.dismiss(None)
        asyncio.create_task(_runner())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "media_send_file": self.action_send_file,
            "media_send_picture": self.action_send_picture,
            "media_send_ascii": self.action_send_ascii,
            "media_send_bw": self.action_send_bw,
            "media_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiSettingsScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("ctrl+s", "apply_settings", "Apply"),
    ]

    CSS = """
    TuiSettingsScreen {
        align: center middle;
    }
    #settings_dialog {
        width: 84;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    .settings_label {
        margin-top: 1;
    }
    #settings_help {
        color: $text-muted;
        margin: 1 0;
    }
    .settings_buttons_row {
        height: auto;
        width: 100%;
        margin-top: 1;
    }
    .settings_button {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_dialog"):
            yield Static("Settings / history / profile import")
            yield Static("Save local history", classes="settings_label")
            yield Switch(False, id="settings_history_enabled")
            yield Static("History max messages", classes="settings_label")
            yield Input("", id="settings_history_max")
            yield Static("History retention days", classes="settings_label")
            yield Input("", id="settings_history_days")
            yield Static("Import profile .dat path", classes="settings_label")
            yield Input("", placeholder="/path/to/profile.dat", id="settings_profile_path")
            yield Static("Imported profile name", classes="settings_label")
            yield Input("", placeholder="alice", id="settings_profile_name")
            yield Static(
                "Ctrl+S apply · Import loads .dat and switches profile.\n"
                "Router and Backups are opened from here.",
                id="settings_help",
            )
            with Horizontal(classes="settings_buttons_row"):
                yield Button("Apply", id="settings_apply", variant="primary", classes="settings_button")
                yield Button("Import .dat", id="settings_import", classes="settings_button")
                yield Button("Router…", id="settings_router", classes="settings_button")
            with Horizontal(classes="settings_buttons_row"):
                yield Button("Backups…", id="settings_backups", classes="settings_button")
                yield Button("Close", id="settings_close", classes="settings_button")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        self.query_one("#settings_history_enabled", Switch).value = self.host._history_enabled
        self.query_one("#settings_history_max", Input).value = str(
            self.host._load_history_max_messages()
        )
        self.query_one("#settings_history_days", Input).value = str(
            self.host._load_history_retention_days()
        )
        self.query_one("#settings_profile_name", Input).value = self.host.profile
        self.query_one("#settings_history_max", Input).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_apply_settings(self) -> None:
        try:
            max_messages = int(self.query_one("#settings_history_max", Input).value.strip())
            max_days = int(self.query_one("#settings_history_days", Input).value.strip())
        except ValueError:
            self.host.post("error", "History limits must be integers.")
            return
        self.host._apply_settings_ui(
            history_enabled=self.query_one("#settings_history_enabled", Switch).value,
            max_messages=max_messages,
            max_days=max_days,
        )
        self.host.post("success", "Settings applied.")
        self.dismiss(None)

    def _import_profile(self) -> None:
        path = os.path.expanduser(self.query_one("#settings_profile_path", Input).value.strip())
        name = self.query_one("#settings_profile_name", Input).value.strip()
        if not path or not name:
            self.host.post("error", "Profile path and profile name are required.")
            return
        if not os.path.exists(path):
            self.host.post("error", f"Profile file not found: {path}")
            return
        self.dismiss(None)
        self.host._run_profile_import_action(path, name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings_apply":
            self.action_apply_settings()
        elif event.button.id == "settings_import":
            self._import_profile()
        elif event.button.id == "settings_router":
            self.dismiss(None)
            self.host._show_router_screen()
        elif event.button.id == "settings_backups":
            self.dismiss(None)
            self.host._show_backups_screen()
        elif event.button.id == "settings_close":
            self.action_close()


class TuiHistoryScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("r", "refresh", "Refresh"),
        ("c", "clear_history", "Clear"),
    ]

    CSS = """
    TuiHistoryScreen {
        align: center middle;
    }
    #history_dialog {
        width: 90;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #history_summary {
        margin: 1 0;
    }
    #history_table {
        height: 14;
        margin: 1 0;
    }
    #history_help {
        color: $text-muted;
        margin: 1 0;
    }
    #history_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="history_dialog"):
            yield Static("History browser")
            yield Static("", id="history_summary")
            yield DataTable(id="history_table")
            yield Static("R refresh · C clear current peer history · Esc close", id="history_help")
            with Horizontal(id="history_buttons"):
                yield Button("Refresh", id="history_refresh", variant="primary")
                yield Button("Clear current peer", id="history_clear", variant="warning")
                yield Button("Close", id="history_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    @property
    def table(self) -> DataTable:
        return self.query_one("#history_table", DataTable)

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(1.0, self._refresh)
        self.table.focus()

    def _refresh(self) -> None:
        peer = self.host._current_target_peer()
        summary = self.query_one("#history_summary", Static)
        if not peer:
            summary.update("No active peer selected.")
            table = self.table
            table.clear(columns=True)
            table.add_columns("When", "Sender", "State", "Text")
            table.add_row("—", "", "No peer selected", "", key="empty")
            table.disabled = True
            return
        self.host._try_load_history(force=True)
        entries = self.host._history_entries[-50:]
        summary.update(
            f"Peer: {peer}\nSaved entries: {len(self.host._history_entries)}"
        )
        table = self.table
        table.clear(columns=True)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("When", "Sender", "State", "Text")
        if not entries:
            table.add_row("—", "", "No saved history", "", key="empty")
            table.disabled = True
            return
        table.disabled = False
        for entry in entries:
            try:
                ts = datetime.fromisoformat(entry.ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
            except Exception:
                ts = entry.ts[:8]
            sender = "Me" if entry.kind == "me" else self.host._peer_display_name(peer)
            table.add_row(
                ts,
                sender,
                delivery_state_label(normalize_loaded_delivery_state(entry.delivery_state)) or "—",
                entry.text.replace("\n", " ⏎ "),
                key=f"{ts}:{sender}:{entry.text[:12]}",
            )

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh()

    def action_clear_history(self) -> None:
        peer = self.host._current_target_peer()
        if not peer:
            self.host.post("error", "No active peer selected.")
            return
        self.host.push_screen(
            TuiConfirmScreen(
                title="Clear history?",
                message=f"Delete saved history for {peer}?",
                confirm_label="Clear",
            ),
            callback=lambda confirmed: self._handle_clear_confirmed(peer, confirmed),
        )

    def _handle_clear_confirmed(self, peer: str, confirmed: bool | None) -> None:
        if not confirmed:
            return
        deleted = delete_history(
            self.host.core.get_profile_data_dir(),
            self.host.core.profile,
            peer,
            app_data_root=self.host.core.get_profiles_dir(),
        )
        if deleted:
            self.host._history_entries = []
            self.host._history_dirty = False
            self.host.post("success", f"History cleared for {peer}.")
        else:
            self.host.post("system", f"No saved history found for {peer}.")
        self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "history_refresh": self.action_refresh,
            "history_clear": self.action_clear_history,
            "history_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiDiagnosticsScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("r", "refresh", "Refresh"),
        ("b", "show_blindbox_panel", "BlindBox"),
        ("t", "show_trust_panel", "Trust"),
        ("u", "check_updates", "Updates"),
    ]

    CSS = """
    TuiDiagnosticsScreen {
        align: center middle;
    }
    #diagnostics_dialog {
        width: 84;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #diagnostics_summary {
        margin: 1 0;
    }
    #diagnostics_help {
        color: $text-muted;
        margin: 1 0;
    }
    .diagnostics_buttons_row {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="diagnostics_dialog"):
            yield Static("Diagnostics")
            yield Static("", id="diagnostics_summary")
            yield Static(
                "B BlindBox · T trust · U updates · R refresh · Esc close",
                id="diagnostics_help",
            )
            with Horizontal(classes="diagnostics_buttons_row"):
                yield Button("BlindBox", id="diagnostics_blindbox", variant="primary")
                yield Button("Trust", id="diagnostics_trust")
                yield Button("Updates", id="diagnostics_updates")
                yield Button("Close", id="diagnostics_close")

    @property
    def host(self) -> I2PChat:
        app = self.app
        assert isinstance(app, I2PChat)
        return app

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(1.0, self._refresh)
        self.query_one("#diagnostics_blindbox", Button).focus()

    def _refresh(self) -> None:
        snap = self.host._build_status_snapshot()
        peer = self.host._current_target_peer() or "—"
        trust = (
            self.host.core.get_peer_trust_info(peer)
            if peer and peer != "—"
            else None
        )
        self.query_one("#diagnostics_summary", Static).update(
            f"{snap.full}\n\n"
            f"Peer: {peer}\n"
            f"Trust pinned: {'yes' if trust and trust.pinned else 'no'}\n"
            f"App dir: {get_profiles_dir()}\n"
            f"Version: {self.host._current_version()}\n"
            f"{self.host._router_status_block()}\n"
            f"{snap.blindbox_bar}"
        )

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh()

    def action_show_blindbox_panel(self) -> None:
        self.dismiss(None)
        self.host._show_blindbox_diagnostics()

    def action_show_trust_panel(self) -> None:
        peer = self.host._current_target_peer()
        if not peer:
            self.host.post("error", "No active or selected peer.")
            return
        self.dismiss(None)
        self.host._show_contact_info(peer)

    def action_check_updates(self) -> None:
        self.dismiss(None)
        self.host.run_worker(self.host._check_updates())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "diagnostics_blindbox": self.action_show_blindbox_panel,
            "diagnostics_trust": self.action_show_trust_panel,
            "diagnostics_updates": self.action_check_updates,
            "diagnostics_close": self.action_close,
        }
        handler = mapping.get(event.button.id or "")
        if handler is not None:
            handler()


class TuiConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "confirm", "Confirm"),
    ]

    CSS = """
    TuiConfirmScreen {
        align: center middle;
    }
    #confirm_dialog {
        width: 56;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #confirm_message {
        margin: 1 0;
    }
    #confirm_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def __init__(self, *, title: str, message: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_dialog"):
            yield Static(self._title)
            yield Static(self._message, id="confirm_message")
            with Horizontal(id="confirm_buttons"):
                yield Button("Cancel", id="confirm_cancel")
                yield Button(self._confirm_label, id="confirm_ok", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#confirm_ok", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm_ok":
            self.action_confirm()
        else:
            self.action_cancel()


class TuiTrustDecisionScreen(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "reject", "Reject"),
        ("enter", "approve", "Approve"),
    ]

    CSS = """
    TuiTrustDecisionScreen {
        align: center middle;
    }
    #trust_dialog {
        width: 76;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #trust_peer {
        margin: 1 0;
    }
    #trust_body {
        margin: 1 0;
    }
    #trust_buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        peer_addr: str,
        body_lines: list[str],
        confirm_label: str,
    ) -> None:
        super().__init__()
        self._title = title
        self._peer_addr = peer_addr
        self._body_lines = body_lines
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="trust_dialog"):
            yield Static(self._title)
            yield Static(f"Peer: {self._peer_addr}", id="trust_peer")
            yield Static("\n".join(self._body_lines), id="trust_body")
            with Horizontal(id="trust_buttons"):
                yield Button("Reject", id="trust_reject")
                yield Button(self._confirm_label, id="trust_approve", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#trust_approve", Button).focus()

    def action_reject(self) -> None:
        self.dismiss(False)

    def action_approve(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "trust_approve":
            self.action_approve()
        else:
            self.action_reject()


if __name__ == "__main__":
    app = I2PChat()
    app.run()
