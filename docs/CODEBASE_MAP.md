# I2PChat codebase map

This document is a low-risk navigation guide for the current repository layout.
It intentionally describes the package-first structure without removing the
root-level compatibility launchers.

## Canonical source tree

The canonical implementation now lives under `i2pchat/`.

### `i2pchat/core`

Runtime orchestration and session logic.

- `i2p_chat_core.py` - main chat runtime, SAM session handling, handshake,
  delivery orchestration, file/image flows, BlindBox root exchange
- `send_retry_policy.py` - retry policy helpers used by the GUI

### `i2pchat/protocol`

Wire-format and delivery semantics.

- `protocol_codec.py` - vNext framing codec, header parsing, legacy opt-in mode
- `message_delivery.py` - delivery states and related helper logic

### `i2pchat/storage`

Persistent local state.

- `chat_history.py` - encrypted per-peer history
- `profile_backup.py` - password-protected profile/history backup bundles
- `contact_book.py` - saved peers / contact metadata
- `blindbox_state.py` - atomic write helpers and BlindBox state persistence

### `i2pchat/blindbox`

Offline / delayed delivery subsystem.

- `blindbox_blob.py` - encrypted BlindBox blob format
- `blindbox_client.py` - replica client protocol
- `blindbox_key_schedule.py` - key derivation for offline delivery
- `blindbox_local_replica.py` - local BlindBox replica support
- `blindbox_diagnostics.py` - user-facing diagnostics text helpers

### `i2pchat/gui`

User interfaces and UI entrypoints.

- `main_qt.py` - Qt desktop client
- `chat_python.py` - Textual TUI
- `__main__.py` - package-first GUI entrypoint (`python -m i2pchat.gui`)

### `i2pchat/presentation`

UI-independent presentation helpers.

- `compose_drafts.py`
- `notification_prefs.py`
- `reply_format.py`
- `status_presentation.py`
- `unread_counters.py`

### `i2pchat/platform`

OS/platform integration helpers.

- `notifications.py` - system notification helpers

### `i2pchat/crypto.py`

Shared cryptographic primitives used across live protocol, history, and backup
flows.

## Compatibility layer

Root-level Python files are intentionally kept for now as compatibility shims
or launchers:

- to avoid breaking existing imports immediately
- to keep build scripts and packaging transitions low-risk
- to preserve stable patch/import paths in tests while the package layout
  settles

The long-term direction is package-first, but the current repository still
supports the previous flat layout through these wrappers.

## Recommended entrypoints

Preferred developer entrypoints:

```bash
python -m i2pchat.gui
python -m i2pchat.gui.main_qt
python -m i2pchat.gui.chat_python
```

Legacy launchers still exist:

```bash
python main_qt.py
python chat-python.py
```

These root files are wrappers. The canonical implementation lives under the
package paths above.

## Tests and tooling

- `tests/` - unit, regression, and GUI smoke tests
- `I2PChat.spec` - PyInstaller spec
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1` - release packaging
- `flake.nix` - Nix packaging / dev shell
- `docs/PROTOCOL.md` - network protocol reference

## Reading order for new contributors

If you want to understand the system with minimal context switching:

1. `docs/PROTOCOL.md`
2. `i2pchat/core/i2p_chat_core.py`
3. `i2pchat/protocol/protocol_codec.py`
4. `i2pchat/gui/main_qt.py`
5. `i2pchat/storage/chat_history.py`
6. `i2pchat/blindbox/`
