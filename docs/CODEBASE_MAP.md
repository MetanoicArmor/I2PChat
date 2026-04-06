# I2PChat codebase map

This document is a low-risk navigation guide for the current repository layout.
All application code lives under `i2pchat/`; the repo root keeps tooling and
docs; vendored third-party code lives under `vendor/` (primarily **`vendor/i2pd`**;
an optional legacy **`vendor/i2plib`** tree may remain for tooling or audits but is
not required for normal app SAM transport — see **`i2pchat/sam/`**).

## Canonical source tree

The canonical implementation lives under `i2pchat/`.

### `i2pchat/core`

Runtime orchestration and session logic.

- `i2pchat/core/i2p_chat_core.py` — main chat runtime, SAM session handling, handshake,
  delivery orchestration, file/image flows, BlindBox root exchange
- `i2pchat/core/send_retry_policy.py` — retry policy helpers used by the GUI
- `i2pchat/core/transfer_retry.py` — file/media transfer retry policy and UX labels

### `i2pchat/protocol`

Wire-format and delivery semantics.

- `i2pchat/protocol/protocol_codec.py` — vNext framing codec, header parsing, legacy opt-in mode
- `i2pchat/protocol/message_delivery.py` — delivery states and related helper logic

### `i2pchat/sam`

I2P **SAM** transport to the router (separate from vNext chat framing).

- `i2pchat/sam/protocol.py` — SAM line builders and strict reply parsing
- `i2pchat/sam/client.py` — `SAMClient`, stream open helpers, I/O timeouts
- `i2pchat/sam/backend.py` — async façade used by core and BlindBox (`create_session`, `stream_connect`, …)
- `i2pchat/sam/destination.py` — destination / private-key value object
- `i2pchat/sam/errors.py` — typed SAM protocol errors

### `i2pchat/storage`

Persistent local state.

- `i2pchat/storage/chat_history.py` — encrypted per-peer history
- `i2pchat/storage/profile_backup.py` — password-protected profile/history backup bundles
- `i2pchat/storage/profile_export.py` — legacy `.i2pchat-profile` encrypted export/import
- `i2pchat/storage/contact_book.py` — saved peers / contact metadata
- `i2pchat/storage/blindbox_state.py` — atomic write helpers and BlindBox state persistence
- `i2pchat/storage/history_export.py` — encrypted per-peer history export/import (`.i2hx`)
- `i2pchat/storage/history_retention.py` — retention policy enforcement

### `i2pchat/blindbox`

Offline / delayed delivery subsystem.

- `i2pchat/blindbox/blindbox_blob.py` — encrypted BlindBox blob format
- `i2pchat/blindbox/blindbox_client.py` — replica client protocol
- `i2pchat/blindbox/blindbox_key_schedule.py` — key derivation for offline delivery
- `i2pchat/blindbox/blindbox_local_replica.py` — local BlindBox replica support
- `i2pchat/blindbox/blindbox_diagnostics.py` — user-facing diagnostics text helpers

### `i2pchat/gui`

User interfaces and UI entrypoints.

- `i2pchat/gui/main_qt.py` — Qt desktop client
- `i2pchat/gui/chat_python.py` — Textual TUI (реализация; запуск: `python -m i2pchat.tui`)
- `i2pchat/gui/__main__.py` — package-first GUI entrypoint (`python -m i2pchat.gui`)
- `i2pchat/run_gui.py` — Qt launcher, PyInstaller entry on Linux/macOS / primary exe on Windows
- `i2pchat/run_tui.py` — TUI launcher for PyInstaller (второй бинарник `I2PChat-tui` / `I2PChat-tui.exe`, `console=True`)
- `i2pchat/tui.py` — short TUI launcher (`python -m i2pchat.tui`; delegates to `chat_python`)

### `i2pchat/presentation`

UI-independent presentation helpers.

- `i2pchat/presentation/compose_drafts.py`
- `i2pchat/presentation/notification_prefs.py`
- `i2pchat/presentation/reply_format.py`
- `i2pchat/presentation/status_presentation.py`
- `i2pchat/presentation/unread_counters.py`
- `i2pchat/presentation/privacy_mode.py` — privacy toggle / lock-pin logic (no Qt)
- `i2pchat/presentation/drag_drop.py` — drag-drop validation (no Qt)

### `i2pchat/platform`

OS/platform integration helpers.

- `i2pchat/platform/notifications.py` — system notification helpers

### `i2pchat/crypto.py`

Shared cryptographic primitives used across live protocol, history, and backup
flows.

## Recommended entrypoints

```bash
python -m i2pchat.gui
python -m i2pchat.run_gui
python -m i2pchat.gui.main_qt
python -m i2pchat.tui
```

Legacy TUI module path (same app): `python -m i2pchat.gui.chat_python`.

PyInstaller uses [`run_gui.py`](../i2pchat/run_gui.py) as the analyzed script (same as
`python -m i2pchat.gui`).

## Tests and tooling

- `tests/` - unit, regression, and GUI smoke tests
- `I2PChat.spec` - PyInstaller spec (entry: `i2pchat/run_gui.py`)
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1` - release packaging
- `flake.nix` - Nix packaging / dev shell
- `docs/PROTOCOL.md` - network protocol reference
- `docs/AUDIT_EN.md`, `docs/AUDIT_RU.md` - security audit reports (EN / RU)
- `docs/ROADMAP.md`, `docs/ROADMAP_RU.md` - product roadmap (EN / RU)
- `docs/ISSUE_BACKLOG.md`, `docs/ISSUE_BACKLOG_RU.md` - issue backlog (EN / RU)

## Reading order for new contributors

If you want to understand the system with minimal context switching:

1. `docs/PROTOCOL.md`
2. `i2pchat/core/i2p_chat_core.py`
3. `i2pchat/protocol/protocol_codec.py`
4. `i2pchat/gui/main_qt.py`
5. `i2pchat/storage/chat_history.py`
6. `i2pchat/blindbox/`
