from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual.app", exc_type=ImportError)


def test_tui_defaults_to_system_router_when_router_prefs_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    monkeypatch.setattr(
        "i2pchat.gui.chat_python.load_router_settings",
        lambda: RouterSettings(backend="bundled", bundled_auto_start=True),
    )
    monkeypatch.setattr(
        "i2pchat.gui.chat_python.router_settings_path",
        lambda: "/tmp/missing-router-prefs.json",
    )
    monkeypatch.setattr("i2pchat.gui.chat_python.os.path.isfile", lambda _path: False)

    settings = I2PChat._load_tui_router_settings()

    assert settings.backend == "system"
    assert settings.bundled_auto_start is False


def test_tui_respects_explicit_saved_router_backend_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    monkeypatch.setattr(
        "i2pchat.gui.chat_python.load_router_settings",
        lambda: RouterSettings(backend="bundled", bundled_auto_start=True),
    )
    monkeypatch.setattr(
        "i2pchat.gui.chat_python.router_settings_path",
        lambda: "/tmp/router-prefs.json",
    )
    monkeypatch.setattr("i2pchat.gui.chat_python.os.path.isfile", lambda _path: True)

    settings = I2PChat._load_tui_router_settings()

    assert settings.backend == "bundled"
    assert settings.bundled_auto_start is True


def test_tui_forces_system_when_bundled_router_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    monkeypatch.setattr(
        "i2pchat.gui.chat_python.load_router_settings",
        lambda: RouterSettings(backend="bundled", bundled_auto_start=True),
    )
    monkeypatch.setattr(
        "i2pchat.gui.chat_python.router_settings_path",
        lambda: "/tmp/router-prefs.json",
    )
    monkeypatch.setattr("i2pchat.gui.chat_python.os.path.isfile", lambda _path: True)
    monkeypatch.setattr("i2pchat.gui.chat_python.bundled_i2pd_allowed", lambda: False)

    settings = I2PChat._load_tui_router_settings()

    assert settings.backend == "system"
    assert settings.bundled_auto_start is False


def test_tui_fallback_system_router_does_not_persist_shared_router_prefs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    app = object.__new__(I2PChat)
    app._router_settings = RouterSettings(backend="system", bundled_auto_start=False)
    app._router_settings_explicit = False
    app._active_http_proxy_address = None

    saved: list[RouterSettings] = []
    monkeypatch.setattr("i2pchat.gui.chat_python.save_router_settings", lambda settings: saved.append(settings))

    sam = asyncio.run(I2PChat._ensure_router_backend_ready(app))

    assert sam == ("127.0.0.1", 7656)
    assert saved == []


def test_tui_command_aliases_cover_gui_style_names() -> None:
    from i2pchat.gui.chat_python import I2PChat

    assert I2PChat._normalize_command_alias("check-updates") == "updates"
    assert I2PChat._normalize_command_alias("copy-my-address") == "copyaddr"
    assert I2PChat._normalize_command_alias("open-app-dir") == "appdir"
    assert I2PChat._normalize_command_alias("lock-peer") == "save"
    assert I2PChat._normalize_command_alias("saved-peers") == "contacts"
    assert I2PChat._normalize_command_alias("send-picture") == "sendpic"
    assert I2PChat._normalize_command_alias("send-file") == "sendfile"
    assert I2PChat._normalize_command_alias("media") == "transfers"
    assert I2PChat._normalize_command_alias("transfer") == "transfers"
    assert I2PChat._normalize_command_alias("load-profile") == "profile-import-dat"
    assert I2PChat._normalize_command_alias("export-profile-backup") == "backup-profile-export"
    assert I2PChat._normalize_command_alias("import-profile-backup") == "backup-profile-import"
    assert I2PChat._normalize_command_alias("export-history-backup") == "backup-history-export"
    assert I2PChat._normalize_command_alias("import-history-backup") == "backup-history-import"
    assert I2PChat._normalize_command_alias("backup-ui") == "backups"
    assert I2PChat._normalize_command_alias("history-browser") == "history-screen"
    assert I2PChat._normalize_command_alias("diagnostics") == "diagnostics-screen"
    assert I2PChat._normalize_command_alias("diag") == "diagnostics-screen"
    assert I2PChat._normalize_command_alias("clear-history") == "history-clear"
    assert I2PChat._normalize_command_alias("preferences") == "settings"
    assert I2PChat._normalize_command_alias("history-retention") == "history-retention"


def test_tui_actions_command_alias_passthrough() -> None:
    from i2pchat.gui.chat_python import I2PChat

    assert I2PChat._normalize_command_alias("actions") == "actions"
    assert I2PChat._normalize_command_alias("menu") == "launcher"


def test_tui_primary_function_key_order_is_sequential() -> None:
    from i2pchat.gui.chat_python import I2PChat

    bindings = {key: action for key, action, _label in I2PChat.BINDINGS}
    labels = [label for _key, _action, label in I2PChat.BINDINGS[:4]]

    assert labels[:3] == ["Quit", "Quit", "Send"]
    assert labels[3] == "Send"

    assert "ctrl+r" not in bindings
    assert "ctrl+shift+c" not in bindings
    assert "ctrl+l" not in bindings
    assert bindings["f1"] == "show_actions"
    assert bindings["f2"] == "show_contacts"
    assert bindings["f3"] == "show_media"
    assert bindings["f4"] == "show_settings"
    assert bindings["f5"] == "show_router"
    assert bindings["f6"] == "show_history_screen"
    assert bindings["f7"] == "show_diagnostics_screen"
    assert "f8" not in bindings
    assert "ctrl+shift+e" not in bindings


def test_tui_defaults_to_tokyo_night_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat

    monkeypatch.setattr("sys.argv", ["chat_python.py", "default"])
    app = I2PChat()

    assert app.theme == "tokyo-night"


def test_tui_router_status_line_marks_default_mode() -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    app = object.__new__(I2PChat)
    app._router_settings = RouterSettings(backend="system")
    app._router_settings_explicit = False
    app._active_sam_address = None
    app._bundled_router_manager = None

    line = I2PChat._router_status_line(app)

    assert "Configured router: system (tui-default)" in line
    assert "Active runtime: external/system SAM" in line


def test_tui_set_router_backend_persists_and_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat
    from i2pchat.router.settings import RouterSettings

    app = object.__new__(I2PChat)
    app._router_settings = RouterSettings(backend="system", bundled_auto_start=False)
    app._router_settings_explicit = False
    restarts: list[str] = []
    monkeypatch.setattr("i2pchat.gui.chat_python.save_router_settings", lambda settings: restarts.append(settings.backend))
    monkeypatch.setattr(app, "_restart_router_runtime", lambda reason: restarts.append(reason) or asyncio.sleep(0))
    monkeypatch.setattr(app, "post", lambda *_args, **_kwargs: None)

    asyncio.run(I2PChat._set_router_backend(app, "bundled"))

    assert app._router_settings.backend == "bundled"
    assert app._router_settings.bundled_auto_start is True
    assert app._router_settings_explicit is True
    assert restarts[0] == "bundled"
    assert "Switching router backend to bundled" in restarts[1]


def test_tui_show_contacts_pushes_contacts_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiContactsScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_contacts(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiContactsScreen)



def test_tui_show_actions_pushes_actions_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiActionsScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_actions_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiActionsScreen)


def test_tui_show_media_pushes_media_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiMediaScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_media_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiMediaScreen)


def test_tui_show_launcher_pushes_launcher_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiLauncherScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_launcher_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiLauncherScreen)


def test_tui_show_history_pushes_history_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiHistoryScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_history_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiHistoryScreen)


def test_tui_show_diagnostics_pushes_diagnostics_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiDiagnosticsScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_diagnostics_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiDiagnosticsScreen)


def test_tui_confirm_screen_exists() -> None:
    from i2pchat.gui.chat_python import TuiConfirmScreen

    screen = TuiConfirmScreen(title="Confirm?", message="Proceed?", confirm_label="Yes")

    assert screen is not None


def test_tui_trust_decision_screen_exists() -> None:
    from i2pchat.gui.chat_python import TuiTrustDecisionScreen

    screen = TuiTrustDecisionScreen(
        title="Trust?",
        peer_addr="peer.b32.i2p",
        body_lines=["Fingerprint: abc"],
        confirm_label="Trust",
    )

    assert screen is not None


def test_tui_post_can_preserve_markup_when_requested() -> None:
    from i2pchat.gui.chat_python import I2PChat

    app = object.__new__(I2PChat)
    written: list[str] = []
    log = type("Log", (), {"write": lambda self, content: written.append(content)})()
    app.query_one = lambda *_args, **_kwargs: log  # type: ignore[method-assign]

    I2PChat.post(app, "system", "Initializing [bold yellow]demo[/]", allow_markup=True)

    assert written
    assert "[bold yellow]demo[/]" in written[0]


def test_tui_post_ignores_missing_chat_widget() -> None:
    from i2pchat.gui.chat_python import I2PChat

    app = object.__new__(I2PChat)
    app.query_one = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gone"))  # type: ignore[method-assign]

    I2PChat.post(app, "system", "bye")
    I2PChat.post_panel(app, "Title", "Body")


def test_tui_copyaddr_adds_b32_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    from i2pchat.gui.chat_python import I2PChat

    class _Dest:
        base32 = "exampleaddress"

    class _Core:
        my_dest = _Dest()

    copied: list[str] = []
    messages: list[tuple[str, str]] = []
    app = object.__new__(I2PChat)
    app.core = _Core()
    app._compose_draft_active_key = None
    app._set_compose_text = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app._flush_compose_drafts_to_disk = lambda: None  # type: ignore[method-assign]
    app._refresh_status_bar = lambda: None  # type: ignore[method-assign]
    app.post = lambda level, msg, **_kwargs: messages.append((level, msg))  # type: ignore[method-assign]
    monkeypatch.setattr("i2pchat.gui.chat_python.pyperclip", type("Clip", (), {"copy": staticmethod(lambda value: copied.append(value))}))

    import asyncio
    asyncio.run(I2PChat._execute_command(app, "/copyaddr"))

    assert copied == ["exampleaddress.b32.i2p"]
    assert messages[-1] == ("success", "Copied local address to clipboard: exampleaddress.b32.i2p")


def test_tui_on_mount_schedules_background_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat

    async def _run() -> None:
        monkeypatch.setattr("sys.argv", ["chat_python.py", "default"])
        app = I2PChat()
        app.selected_peer = None
        app._contact_book = type("Book", (), {"last_active_peer": None})()
        app._load_contacts_book = lambda: None  # type: ignore[method-assign]
        app._load_compose_drafts_from_disk = lambda: None  # type: ignore[method-assign]
        app._sync_compose_draft_to_peer_key = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        app.post = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        app._start_core_session_init_background = lambda: setattr(app, "_started_init", True)  # type: ignore[method-assign]
        app.profile = "default"

        await I2PChat.on_mount(app)

        assert getattr(app, "_started_init", False) is True

    asyncio.run(_run())


def test_tui_start_core_session_init_background_avoids_duplicate_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat

    async def _run() -> None:
        app = object.__new__(I2PChat)
        gate: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        app._core_init_task = None

        async def _init() -> bool:
            return await gate

        app._initialize_core_session = _init  # type: ignore[method-assign]

        I2PChat._start_core_session_init_background(app)
        first_task = app._core_init_task
        I2PChat._start_core_session_init_background(app)

        assert app._core_init_task is first_task
        gate.set_result(True)
        await first_task
        assert app._core_init_task is None

    asyncio.run(_run())


def test_tui_show_contact_editor_pushes_editor_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiContactEditorScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_contact_editor(app, peer="alice.b32.i2p")

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiContactEditorScreen)


def test_tui_show_backups_pushes_backups_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiBackupsScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_backups_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiBackupsScreen)


def test_tui_show_router_pushes_router_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiRouterScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_router_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiRouterScreen)


def test_tui_create_core_wires_trust_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from i2pchat.gui.chat_python import I2PChat

    captured: dict[str, object] = {}

    class _Core:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("i2pchat.gui.chat_python.I2PChatCore", _Core)
    app = object.__new__(I2PChat)
    app.handle_status = lambda *_a, **_k: None
    app.handle_message = lambda *_a, **_k: None
    app.handle_peer_changed = lambda *_a, **_k: None
    app.handle_system = lambda *_a, **_k: None
    app.handle_error = lambda *_a, **_k: None
    app.handle_file_event = lambda *_a, **_k: None
    app.handle_file_offer = lambda *_a, **_k: True
    app.handle_image_received = lambda *_a, **_k: None
    app.handle_inline_image_received = lambda *_a, **_k: None
    app.handle_text_delivered = lambda *_a, **_k: None
    app.handle_image_delivered = lambda *_a, **_k: None
    app.handle_file_delivered = lambda *_a, **_k: None
    app.handle_trust_decision = lambda *_a, **_k: True
    app.handle_trust_mismatch_decision = lambda *_a, **_k: True

    I2PChat._create_core(app, "default", ("127.0.0.1", 7656))

    assert captured["on_trust_decision"] is app.handle_trust_decision
    assert captured["on_trust_mismatch_decision"] is app.handle_trust_mismatch_decision


def test_tui_show_settings_pushes_settings_screen() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiSettingsScreen

    app = object.__new__(I2PChat)
    pushed: list[object] = []
    app.push_screen = lambda screen: pushed.append(screen)  # type: ignore[method-assign]

    I2PChat._show_settings_screen(app)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TuiSettingsScreen)


def test_tui_apply_settings_ui_updates_flags_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from i2pchat.gui.chat_python import I2PChat

    app = object.__new__(I2PChat)
    app._history_entries = []
    app._history_dirty = False
    app._history_enabled = False
    calls: list[tuple[str, object]] = []
    app._save_history_enabled = lambda value: calls.append(("history_enabled", value))  # type: ignore[method-assign]
    app._save_history_max_messages = lambda value: calls.append(("history_max", value))  # type: ignore[method-assign]
    app._save_history_retention_days = lambda value: calls.append(("history_days", value))  # type: ignore[method-assign]
    app._save_history_if_needed = lambda: calls.append(("saved", True))  # type: ignore[method-assign]
    app._try_load_history = lambda force=False: calls.append(("load", force))  # type: ignore[method-assign]

    I2PChat._apply_settings_ui(
        app,
        history_enabled=True,
        max_messages=123,
        max_days=7,
    )

    assert app._history_enabled is True
    assert ("history_enabled", True) in calls
    assert ("history_max", 123) in calls
    assert ("history_days", 7) in calls
    assert ("load", True) in calls


def test_tui_record_transfer_event_keeps_recent_entries() -> None:
    from collections import deque
    from i2pchat.gui.chat_python import I2PChat

    app = object.__new__(I2PChat)
    app._recent_transfers = deque(maxlen=2)

    I2PChat._record_transfer_event(app, "file", "/tmp/a.txt", "queued")
    I2PChat._record_transfer_event(app, "image", "/tmp/b.png", "delivered")

    assert len(app._recent_transfers) == 2
    assert app._recent_transfers[0].filename == "b.png"
    assert app._recent_transfers[1].filename == "a.txt"


def test_tui_summary_helpers_include_core_status_lines() -> None:
    from i2pchat.gui.chat_python import I2PChat, TuiStatusSnapshot

    class _Core:
        conn = None
        proven = False
        stored_peer = None

    app = object.__new__(I2PChat)
    app.core = _Core()
    app._router_status_block = lambda: "Configured backend: system\nSelection source: TUI default\nActive runtime: external/system SAM\nActive SAM address: ('127.0.0.1', 7656)"  # type: ignore[method-assign]
    app._build_status_snapshot = lambda: TuiStatusSnapshot(  # type: ignore[method-assign]
        short="short",
        full="full status",
        technical="technical detail",
        blindbox_bar="BlindBox: on",
        ack_total=0,
    )
    app._current_target_peer = lambda: "peer.b32.i2p"  # type: ignore[method-assign]

    launcher = I2PChat._launcher_summary_text(app)
    actions = I2PChat._actions_summary_text(app)
    media = I2PChat._media_summary_text(app)

    assert "full status" in launcher
    assert "Configured backend:" in launcher
    assert "Current peer: peer.b32.i2p" in actions
    assert "Connected: no" in actions
    assert "BlindBox: on" in media
