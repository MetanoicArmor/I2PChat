<p align="center">
  <img src="image.png" alt="I2PChat Logo" width="280" />
</p>

<h1 align="center">I2PChat</h1>

<p align="center">
  <a href="https://github.com/MetanoicArmor/I2PChat/releases/latest"><img src="https://img.shields.io/github/v/release/MetanoicArmor/I2PChat?label=release" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/MetanoicArmor/I2PChat" alt="License"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12+-blue.svg" alt="Python"></a>
  <a href="https://i2pd.website"><img src="https://img.shields.io/badge/I2P-SAM%20API-purple.svg" alt="I2P"></a>
</p>

<p align="center">
  <b>Experimental peer‑to‑peer chat client for the <a href="https://i2pd.website">I2P</a> anonymity network.</b><br>
  Cross‑platform <b>PyQt6 GUI</b> and a separate <b>terminal client</b> (often labeled <b>TUI</b> — <i>terminal user interface</i>: the same chat in a console via Textual, no Qt windows) on one shared asynchronous core.<br>
  Prebuilt releases usually ship a <b>bundled <code>i2pd</code></b>; you can switch to a system router in the app (see manuals).
</p>

---

### Language / Язык

[![English manual](https://img.shields.io/badge/📖%20Manual-EN-blue.svg)](docs/MANUAL_EN.md)
[![Русский мануал](https://img.shields.io/badge/📖%20Мануал-RU-red.svg)](docs/MANUAL_RU.md)
[![Roadmap EN](https://img.shields.io/badge/🗺️%20Roadmap-EN-teal.svg)](docs/ROADMAP.md)
[![Roadmap RU](https://img.shields.io/badge/🗺️%20Roadmap-RU-red.svg)](docs/ROADMAP_RU.md)
[![Issue Backlog EN](https://img.shields.io/badge/📝%20Issue%20Backlog-EN-blueviolet.svg)](docs/ISSUE_BACKLOG.md)
[![Issue Backlog RU](https://img.shields.io/badge/📝%20Issue%20Backlog-RU-orange.svg)](docs/ISSUE_BACKLOG_RU.md)

---

### 📑 Table of contents

- [✨ Features](#-features)
- [🧠 Core architecture](#-core-architecture)
- [🔌 Protocol overview](#-protocol-overview)
- [📬 BlindBox in short](#-blindbox-in-short)
- [📸 Screenshots](#-screenshots)
- [🛠 Running from source](#-running-from-source)
- [Cross‑platform builds](#cross-platform-builds)
- [📄 License](#-license)
- [☕ Developer Support](#-developer-support)
- [🚀 Quick Start](#-quick-start) — downloads, package managers, **INSTALL.md**

### ✨ Features

- **End‑to‑end communication over I2P SAM** (internal `i2pchat.sam` layer)
- **E2E encryption** — handshake, key signing and verification
- **TOFU** — peer key pinning on first contact
- **Multi-peer profiles** — switch between **Saved peers** freely; incoming connections are accepted only from addresses present in the contact book (empty book ⇒ no inbound whitelist matches)
- **PyQt6 GUI** with light and dark themes (macOS-style, consistent and predictable on all platforms)
- **File transfer** and **image sending** (Send picture: PNG, JPEG, WebP) between peers
- **Profiles (.dat)** — multiple profiles, load and import; each profile’s data lives under **`profiles/<name>/`** in the app data directory (if older **flat** `*.dat` files still sit in the data root, they are **migrated on startup** into that layout — see **§ profile paths** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md))
- **System notifications** — tray toasts for new messages
- **Sound notifications** for incoming messages
- **BlindBox (default-on for named profiles)** — offline message delivery
- **Optional encrypted chat history** — per-peer local history (toggle **Chat history: ON/OFF** in the **⋯** menu); encrypted at rest with keys derived from your profile identity (see **§4.11** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md))
- **Contact book (Saved peers)** — left sidebar list backed by **`profiles/<name>/<name>.contacts.json`**: quick switch between saved `.b32.i2p` peers, optional display name/note, unread hints, resize/collapse, and a context menu (edit, trust details, remove). See **§3.1** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md).
- **Text groups** — multi-member conversations over the same vNext stream as 1:1 chat; offline delivery fans out per member via **pairwise** BlindBox (see the manuals for prerequisites and **§** on group BlindBox behavior)
- **Terminal client (TUI)** — *terminal user interface*: full chat in a text shell (Textual, `i2pchat/gui/chat_python.py`); shipped as **`*-tui-*`** release zips and **`i2pchat-tui`** packages (Homebrew, apt, AUR), or **`python -m i2pchat.tui`** from source
- Cross‑platform build scripts (Linux, macOS, Windows)

#### 📖 Manuals

- **English manual**: [**docs/MANUAL_EN.md**](docs/MANUAL_EN.md)
- **Русский мануал**: [**docs/MANUAL_RU.md**](docs/MANUAL_RU.md)

### 🧠 Core architecture

The runtime is built around one shared async engine — `I2PChatCore` — plus **`SessionManager`** (per-peer transport lifecycle and outbound policy since v1.2.6) and **parallel live streams** (`LivePeerSession` rows in **`_live_sessions[peer_id]`**), with **`GroupManager`** for text groups. Thin UI adapters sit on top; protocol / crypto / BlindBox below.

**Toolchain:** Python dependencies are managed with **[uv](https://docs.astral.sh/uv/)** ([`pyproject.toml`](pyproject.toml), [`uv.lock`](uv.lock)). **I2P SAM** (router control connection, sessions, streams, naming lookups) is implemented in-tree as **`i2pchat.sam`** — not the PyPI **`i2plib`** package; the old vendored `i2plib` tree was removed.

```mermaid
flowchart TB
    subgraph Entry["UI / entrypoints"]
        run["python -m i2pchat.gui
python -m i2pchat.tui
i2pchat/run_gui.py"]
        qt["PyQt6 GUI
i2pchat/gui/main_qt.py
ChatWindow + qasync event loop"]
        tui["Textual TUI
i2pchat/gui/chat_python.py"]
        present["Presentation helpers
i2pchat/presentation/*
status / drafts / replies / unread / groups / notification policy"]
        guiStore["GUI-side persistence
chat_history.py
contact_book.py
group_store.py
profile_backup.py"]
        run --> qt
        qt --> present
        qt --> guiStore
        tui -->|"commands + callbacks"| core
        qt -->|"commands + callbacks"| core
    end

    subgraph CoreRuntime["Shared async core"]
        core["i2pchat/core/i2p_chat_core.py
I2PChatCore
• profile/session bootstrap
• accept/connect orchestration
• _live_sessions[peer_id] → LivePeerSession
• secure handshake + TOFU pinning
• per-peer send/receive loops
• ACK tracking + delivery telemetry
• text / file / image + text groups
• BlindBox root exchange
• delegates transport lifecycle → SessionManager"]
        sessionMgr["i2pchat/core/session_manager.py
SessionManager
per-peer transport state
outbound policy · streams · reconnect"]
        groupMgr["i2pchat/groups/manager.py
GroupManager
live + BlindBox fan-out"]
        retry["Retry helpers
send_retry_policy.py
transfer_retry.py"]
        core --> sessionMgr
        core --> groupMgr
        core --> retry
    end

    subgraph ProtocolSecurity["Protocol + security"]
        codec["Framing codec
protocol/protocol_codec.py
vNext header / flags / msg_id / len"]
        delivery["Delivery state model
protocol/message_delivery.py
sending / queued / delivered / failed"]
        crypto["i2pchat/crypto.py
X25519 + Ed25519
HKDF
SecretBox + HMAC"]
    end

    subgraph BlindBox["Offline delivery subsystem"]
        bbclient["blindbox_client.py
quorum PUT / GET
SAM or direct TCP"]
        bbkeys["blindbox_key_schedule.py
lookup / blob / state keys"]
        bbblob["blindbox_blob.py
encrypted padded offline blob"]
        bbstate["storage/blindbox_state.py
send_index / recv window / consumed set"]
        bblocal["blindbox_local_replica.py
optional local BlindBox"]
    end

    subgraph Transport["Network / external boundary"]
        samLayer["i2pchat.sam (internal)
SESSION / STREAM / NAMING
no PyPI i2plib"]
        sam["I2P router
SAM API"]
        peer["Remote peers
N concurrent SAM streams
one secure session per peer id"]
        boxes["BlindBox replicas
I2P or loopback endpoints"]
    end

    subgraph ProfileState["Profile / local identity"]
        profile["profiles/<name>/ per profile
<name>.dat + keyring
contacts.json Saved peers
trust store
signing seed"]
    end

    profile -->|"load / save identity,
trust pins, contacts"| core
    core -->|"encode / decode frames"| codec
    core -->|"derive UI delivery semantics"| delivery
    core -->|"handshake, encryption,
MAC, replay checks"| crypto
    core -->|"queue offline text,
root rotation, polling"| bbclient
    core -->|"derive per-message keys"| bbkeys
    bbkeys --> bbblob
    core -->|"persist offline counters
and root metadata"| bbstate
    core -.->|"optional local fallback"| bblocal
    bbclient -->|"stores / fetches blobs"| bbblob
    bbclient <-->|"SAM streams or TCP"| samLayer
    samLayer <--> sam
    sam <--> peer
    bbclient <--> boxes
    core -->|"status / message / file /
delivery callbacks"| qt
```

Runtime in practice:

1. **Startup**: `main_qt.py` runs **profile directory migration** when needed (flat `*.dat` in the data root → `profiles/<name>/`) before the profile picker, then creates `ChatWindow`; `start_core()` calls `I2PChatCore.init_session()`, which loads or creates the profile identity, opens the long-lived SAM session, warms up tunnels, and starts `accept_loop()` / `tunnel_watcher()`.
2. **Transport lifecycle (`SessionManager`, since v1.2.6)**: per-peer transport state (connecting / handshaking / secure / stale / failed), outbound send policy (`LIVE_ONLY`, `PREFER_LIVE_FALLBACK_BLINDBOX`, `QUEUE_THEN_RETRY_LIVE`, `BLINDBOX_ONLY`), stream registry, reconnect metadata, and inflight ACK hooks live in **`SessionManager`**. Parallel **live** traffic is keyed by **`peer_id`** in **`_live_sessions`** (`LivePeerSession`: `conn`, crypto, ACK tables, receive loop). Legacy `self.conn` may still reflect the active UI peer; **routing and ACKs are peer-scoped**, not “single global connection”. Delivery telemetry and UI read this so **Send** vs **Send offline** stay correct after handshake.
3. **Live chat path**: `connect_to_peer()` / `accept_loop()` opens or updates **one** I2P stream per **peer**; `I2PChatCore` runs the handshake, TOFU pinning, and subkeys, then encrypted vNext frames via `ProtocolCodec` + `crypto`. Multiple peers can be **connected at once** (bounded by `max_concurrent_live_sessions`); the UI **selection** (`current_peer_addr`) does not define which peer receives a send — the **target peer** for that operation does.
4. **Text groups**: **`GroupManager`** sends group envelopes over the same vNext stream as 1:1 chat; offline text fans out **per member** via pairwise BlindBox. State: `i2pchat/storage/group_store.py` (see [MANUAL_EN](docs/MANUAL_EN.md) / [PROTOCOL](docs/PROTOCOL.md)).
5. **Delivery tracking**: each outgoing text / file / image gets a `MSG_ID` and ACK context; `message_delivery.py` turns low-level outcomes into UI states (`sending`, `queued`, `delivered`, `failed`).
6. **Offline path (BlindBox)**: when no live secure session is available, `send_text()` can route through BlindBox — derive deterministic lookup/blob keys, encrypt a padded blob, PUT it to one or more BlindBox replicas, and later poll / decrypt GET results back into the chat stream.
7. **UI responsibility split**: `I2PChatCore` stays UI-agnostic and emits callbacks only; the Qt layer renders chat, status and notifications, while GUI-side storage modules persist chat history, contacts, group state, drafts and backup/export data.

### 🔌 Protocol overview

Traffic is a **byte stream** over **I2P SAM** (one TCP session to the router). Application data is split into **vNext binary frames**:

```
┌─────────── vNext frame ────────────────────────────────────────┐
│ MAGIC (4) │ VER (1) │ TYPE (1) │ FLAGS (1) │ MSG_ID (8) │ LEN (4) │ PAYLOAD (LEN bytes) │
└──────────────────────────────────────────────────────────────────┘
```

- **Handshake** uses **plain** frame bodies (UTF‑8 text: identities, `INIT` / replies, signatures).
- After the secure handshake, payloads are **encrypted** (`FLAGS` marks it): each body is **sequence (8 B) + ciphertext + MAC** (NaCl SecretBox + HMAC over metadata).
- **Message IDs** and **sequence numbers** tie frames to ordering and replay protection; see also [padding](#protocol-metadata-and-padding-profile) below.

For a developer-oriented specification with framing, handshake, ACK, transfer,
BlindBox, and code-map sections, see [**docs/PROTOCOL.md**](docs/PROTOCOL.md).

Runtime layout summary: [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md). Release scripts, signing, checksums, NixOS, BlindBox daemon notes: [**docs/BUILD.md**](docs/BUILD.md).

### 📬 BlindBox

BlindBox is your “send now, deliver later” mode for text messages.

Why users like it:

- You can message people even when they are temporarily offline.
- Delivery happens automatically when they come back online.
- The chat stays clean and readable: only real messages, no technical noise.
- Works naturally with normal live chat — no extra routine in daily use.

Simple flow:

1. If the peer is online, the message is delivered live.
2. If the peer is offline, the app keeps it in the offline queue.
3. When the peer returns, the message appears automatically.

Practical notes:

- For named profiles BlindBox is enabled by default.
- For the transient profile `random_address` (CLI alias `default`) BlindBox is off.
- Disable explicitly with `I2PCHAT_BLINDBOX_ENABLED=0`.
- Deployments can set Blind Box endpoints via env (`I2PCHAT_BLINDBOX_REPLICAS`, `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS`, or `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE`). Built-in release defaults and further options → manuals / release notes above.

### 📸 Screenshots

<p align="center">
  <img src="screenshots/1.png" alt="I2PChat – main window" width="900" /><br>
  <img src="screenshots/4.png" alt="I2PChat – chat and file transfer (sending)" width="900" /><br>
  <img src="screenshots/10.png" alt="I2PChat – TUI (terminal UI)" width="900" />
</p>

The gallery above is a short subset. **`screenshots/2.png`** (⋯ menu), **`3.png`** (profile picker), **`5.png`** (emoji picker), **`6.png`** (BlindBox diagnostics), **`8.png`** (I2P router dialog), **`9.png`** (Blind Box setup examples — `install.sh` / **Copy curl** for a custom replica), and **`10.png`** (TUI) are documented inline in [**MANUAL_EN.md**](docs/MANUAL_EN.md) / [**MANUAL_RU.md**](docs/MANUAL_RU.md).

### 🛠 Building and running from source (C++)

The supported client is **C++20** under [`cpp/`](cpp/). It is wire-compatible with Python 1.4.x peers and reads the same profile directory. CMake presets: [`cpp/README.md`](cpp/README.md). Packaging / cutover: [`cpp/packaging/README.md`](cpp/packaging/README.md), [`cpp/docs/CUTOVER.md`](cpp/docs/CUTOVER.md).

Requirements (all platforms):

- **CMake ≥ 3.24** and a C++20 compiler (GCC 12+, Clang 15+, Apple Clang, MSVC 2022)
- **libsodium**, **Boost ≥ 1.81** (Asio), **nlohmann/json**
- **FTXUI** (TUI), **Qt 6 Widgets** (GUI)
- **Catch2 3** only if you build tests
- a **system** [i2pd](https://i2pd.website) with **SAM** on port `7656`, or a **bundled** `i2pd` staged by the release scripts

<img src="docs/icons/icons8-debian-48.png" alt="Debian" width="28" height="28" align="middle" /> <img src="docs/icons/icons8-ubuntu-48.png" alt="Ubuntu" width="28" height="28" align="middle" /> **Linux (Debian/Ubuntu)**

```bash
sudo apt install cmake ninja-build g++ pkg-config \
  libsodium-dev libboost-dev nlohmann-json3-dev \
  qt6-base-dev libxcb-cursor0
# FTXUI: distro package if present, else vcpkg feature `tui`.
# X11: libxcb-cursor0 is required for the Qt xcb platform plugin.

cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DI2PCHAT_BUILD_TUI=ON -DI2PCHAT_BUILD_GUI=ON -DI2PCHAT_BUILD_TESTS=ON
cmake --build cpp/build -j
ctest --test-dir cpp/build --output-on-failure

./cpp/build/apps/tui/i2pchat-tui --help
./cpp/build/apps/gui/i2pchat-gui --profile default
```

<img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS (Homebrew)**

```bash
brew install cmake ninja libsodium boost nlohmann-json catch2 ftxui qt

cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DI2PCHAT_BUILD_TUI=ON -DI2PCHAT_BUILD_GUI=ON
cmake --build cpp/build -j

./cpp/build/apps/tui/i2pchat-tui -p default
./cpp/build/apps/gui/i2pchat-gui -p default
```

Homebrew prefixes are picked up automatically on Apple Silicon. Profiles live in `~/Library/Application Support/I2PChat/` (same as the historical Python client).

<img src="docs/icons/icons8-windows-48.png" alt="Windows" width="28" height="28" align="middle" /> **Windows (MSVC + vcpkg)**

```powershell
# Visual Studio 2022 with “Desktop development with C++”, CMake, and Git.
$env:VCPKG_ROOT = "C:\src\vcpkg"   # after bootstrap-vcpkg.bat
$env:VCPKG_MANIFEST_FEATURES = "tests;tui;gui"

cmake -S cpp -B cpp/build `
  -DCMAKE_TOOLCHAIN_FILE="$env:VCPKG_ROOT\scripts\buildsystems\vcpkg.cmake" `
  -DI2PCHAT_BUILD_TUI=ON -DI2PCHAT_BUILD_GUI=ON -DI2PCHAT_BUILD_TESTS=ON
cmake --build cpp/build --config RelWithDebInfo --parallel
ctest --test-dir cpp/build -C RelWithDebInfo --output-on-failure

.\cpp\build\apps\tui\RelWithDebInfo\i2pchat-tui.exe --help
.\cpp\build\apps\gui\RelWithDebInfo\i2pchat-gui.exe --profile default
```

Without vcpkg, install libsodium/Boost/nlohmann-json/Qt/FTXUI yourself and pass **`CMAKE_PREFIX_PATH`**.

**CLI flags** (TUI and GUI share the parser): `-p` / `--profile`, `--app-root`, `--sam-host`, `--sam-port`, `--bundled-router`, `--connect`, `--replica`, `--replica-direct`, `--poll-seconds`, `--help`, `--version`.

**SAM:** the C++ stack talks SAM v3 itself. You do not need Python or `i2plib`.

**BlindBox daemon:** `i2pchat-blindbox-daemon`. systemd / fail2ban: [`cpp/apps/blindbox-daemon/packaging/`](cpp/apps/blindbox-daemon/packaging/). Public replicas behind I2P may keep replica auth empty; raw TCP should still use a token. See **§4.9** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md).

The Python tree (`i2pchat/`, `uv`, PyInstaller) remains for interop tests and until cutover; it is not required to build or run the C++ clients.

### 🔧  Cross-platform release builds

The project is **cross-platform**. Release scripts compile the **C++** GUI (`i2pchat-gui` → `I2PChat`), TUI (`i2pchat-tui` → `I2PChat-tui`) and BlindBox daemon, then pack the same artifact names as before so apt/AUR/winget/Homebrew consumers only need new checksums.

Shared helper: [`scripts/build_cpp_binaries.sh`](scripts/build_cpp_binaries.sh) (`cmake` + install). Optional bundled router is still staged by [`scripts/ensure_bundled_i2pd.sh`](scripts/ensure_bundled_i2pd.sh) — see [`docs/BUILD.md`](docs/BUILD.md).

#### <img src="docs/icons/icons8-linux-48.png" alt="linux" width="28" height="28" align="middle" /> Linux (GUI AppImage)

```bash
./build-linux.sh
```

This script:

- Requires **cmake** (≥ 3.24), a C++20 compiler, **Qt 6**, **FTXUI**, libsodium, Boost, nlohmann/json. **Ninja** is used when present.
- Builds into `cpp/build-release` and installs to `dist/cpp-install`.
- Stages `I2PChat` / `I2PChat-tui` plus linked libraries and Qt platform plugins, packs **`I2PChat.AppDir`**, then **`appimagetool`** (pinned SHA-256, same as before).
- Writes `dist/I2PChat-linux-<arch>-v<version>.AppImage` and **`I2PChat-linux-<arch>-v<version>.zip`** (by default one AppImage inside). **`arch`** is **`x86_64`** or **`aarch64`**. Set **`I2PCHAT_LINUX_GUI_ZIP_MODE=portable`** for a zip of the onedir (`I2PChat`, `I2PChat-tui`, `lib/`, `vendor/`).
- Also packs **`I2PChat-linux-<arch>-tui-v<version>.zip`** (launcher `i2pchat-tui` + `usr/bin/I2PChat-tui`).
- **Bundled `i2pd`:** [`scripts/ensure_bundled_i2pd.sh`](scripts/ensure_bundled_i2pd.sh) into `vendor/i2pd/` (default clone [i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)). Boost SONAME staging is unchanged (`stage_i2pd_linux_shlibs.sh`).

Need **`zip`** on PATH for the archives. **`wget`** for appimagetool.

#### <img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> macOS (GUI .app bundle)

```bash
./build-macos.sh
```

- Homebrew **cmake**, **qt**, **ftxui**, libsodium, boost, nlohmann-json.
- Builds `dist/I2PChat.app` (`Contents/MacOS/I2PChat` and `I2PChat-tui`).
- Runs **`macdeployqt`** when found (`brew --prefix qt`).
- Always ad-hoc **`codesign`** nested Qt libraries after **`macdeployqt`** (required on recent macOS). Optional **`I2PCHAT_CODESIGN_IDENTITY`** for Developer ID + hardened runtime.
- Zips **`I2PChat-macOS-<arch>-v<version>.zip`** and **`I2PChat-macos-<arch>-tui-v<version>.zip`**.

#### <img src="docs/icons/icons8-windows-48.png" alt="Windows" width="28" height="28" align="middle" /> Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

Safer one-off:

```powershell
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope Process RemoteSigned; .\build-windows.ps1"
```

This:

1. Requires **cmake** and a C++20 toolchain (MSVC 2022). Prefer **vcpkg**: set **`VCPKG_ROOT`** (manifest features `tui;gui`). Otherwise set **`CMAKE_PREFIX_PATH`** / **`QTDIR`**.
2. Installs into `dist\cpp-install`, then copies **`dist\I2PChat\I2PChat.exe`** and **`I2PChat-tui.exe`**.
3. Runs **`windeployqt`** on the GUI when available.
4. Packs **`I2PChat-windows-x64-v<version>.zip`**, **`I2PChat-windows-tui-x64-v<version>.zip`**, and winget zips **without** embedded i2pd (`*-winget-*`) so Microsoft validation does not flag Riskware.I2PD.

The GUI zip is self-contained after `windeployqt` + vcpkg DLLs; machines do not need MSVC or Python.

### Verify release artifacts

Release build scripts generate:

- `SHA256SUMS` file for produced release archive(s) (Linux aarch64 builds may use a separate **`SHA256SUMS.linux-aarch64`** on GitHub Releases so amd64 sums are not overwritten);
- detached armored GPG signature `SHA256SUMS.asc` (best-effort by default).

These files are **not** tracked in git (they differ per OS/build); upload them **with the release assets** on GitHub.

Build-time controls:

- `I2PCHAT_SKIP_GPG_SIGN=1` — always skip detached signature creation;
- `I2PCHAT_REQUIRE_GPG=1` — fail build if GPG signing is unavailable or fails;
- `I2PCHAT_GPG_KEY_ID=<keyid>` — select a specific key for detached signature (avoids “no default secret key” when you have several keys or no `default-key` in `gpg.conf`);
- `I2PCHAT_GPG_BATCH=0|1` — override auto mode: by default the Linux/macOS scripts use **`gpg --batch`** only when **neither** stdin nor stdout is a TTY (typical CI). If either is a TTY (including `build.sh | tee log`), they omit `--batch` so **pinentry** can ask for your passphrase. Force batch with `I2PCHAT_GPG_BATCH=1` (needs **gpg-agent** with a cached passphrase if the key is protected).

**Official release builds** should set `I2PCHAT_REQUIRE_GPG=1` so unsigned archives are not produced silently; publish `SHA256SUMS` and `SHA256SUMS.asc` next to each asset.

Verification example:

```bash
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

### Protocol metadata and padding profile

The transport is encrypted after handshake, but some protocol metadata remains
observable on the wire:

- frame type (`TYPE`);
- frame length (`LEN`);
- pre-handshake peer identity preface exchange.

To reduce traffic-shape leakage, encrypted payloads use a padding profile:

- default: `balanced` (pads encrypted plaintext to 128-byte buckets);
- optional: `off` (disable padding).

You can override the profile with:

```bash
I2PCHAT_PADDING_PROFILE=off python -m i2pchat.gui
```

Trade-off: stronger padding reduces length correlation but increases bandwidth.

#### ❄️ NixOS

```bash
# Run directly
nix run github:MetanoicArmor/I2PChat

# Install into your profile (adds `i2pchat`, `i2pchat-tui`, desktop entries and icon)
nix profile install github:MetanoicArmor/I2PChat

# Development shell
nix develop github:MetanoicArmor/I2PChat
```

The flake now wraps the app with the Qt runtime pieces that are easy to miss on NixOS sessions: Wayland/platform plugins, multimedia/image plugins, desktop metadata, and Linux notification/sound helpers (`notify-send`, `canberra-gtk-play`, `paplay`, `aplay`).

System keyring integration is optional. If no Secret Service backend is available, I2PChat falls back to file storage automatically; for native keyring support on NixOS, enable a provider such as `gnome-keyring` or KeepassXC Secret Service.

### 📄 License

I2PChat is licensed under the **GNU Affero General Public License v3.0** (or any later version — see section 14 of the license). The full text is in [`LICENSE`](LICENSE).

Bundled `i2pd` binaries, when injected for portable release builds, follow their upstream licenses. The application **SAM** stack is **`i2pchat.sam`** (no PyPI or vendored **i2plib**).

## ☕ Developer Support

If you like this project and it brings value, you can support its development by buying a virtual coffee:

<div align="center">

**☕ Buy developer a coffee:**

**₿ Bitcoin:**
<div align="center">
<img src="btc_donation_qr.png" width="200">

Минимальная сумма транзакции / Minimum transaction amount: **0.0001 BTC**
</div>

### 📋 Bitcoin Address:

```
bc1qfenneg8pt7g42f94uww3l3d7gtw6rl9dd3uslg
```

*Thank you for your support! It motivates to continue working on the project* 🙏

</div>

---

## 🚀 Quick Start

### 📥 Prebuilt Downloads

**[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** — bundles match **`v` + [`VERSION`](VERSION)** in this repo (**v1.4.1** in the table below; **update these rows when you tag a new release** so `latest/download/…` filenames stay valid). No Python on the target machine for these zips.

Full zip layouts, **winget**, **`.deb`**, **Flatpak** notes → [**docs/INSTALL.md**](docs/INSTALL.md).

| Variant | Download | Launch |
|---------|----------|--------|
| <img src="docs/icons/icons8-windows-48.png" alt="Windows" width="28" height="28" align="middle" /> **Windows — GUI** | [I2PChat-windows-x64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-x64-v1.4.1.zip) | Unzip → run `I2PChat.exe` |
| <img src="docs/icons/icons8-windows-48.png" alt="Windows" width="28" height="28" align="middle" /> **Windows — TUI only** | [I2PChat-windows-tui-x64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-tui-x64-v1.4.1.zip) | `I2PChat-tui.exe` in the extracted tree |
| <img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS — GUI (arm64)** | [I2PChat-macOS-arm64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-arm64-v1.4.1.zip) | Unzip → open **`I2PChat-macOS-arm64-bundle/I2PChat.app`** (see **INSTALL.md**) |
| <img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS — TUI only (arm64)** | [I2PChat-macOS-arm64-tui-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-arm64-tui-v1.4.1.zip) | Run **`./i2pchat-tui`** from the extracted folder |
| <img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS — GUI (Intel x64)** | [I2PChat-macOS-x64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-x64-v1.4.1.zip) | Unzip → open **`I2PChat-macOS-x64-bundle/I2PChat.app`** (see **INSTALL.md**) |
| <img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS — TUI only (Intel x64)** | [I2PChat-macOS-x64-tui-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-x64-tui-v1.4.1.zip) | Run **`./i2pchat-tui`** from the extracted folder |
| <img src="docs/icons/icons8-linux-48.png" alt="Linux" width="28" height="28" align="middle" /> **Linux — GUI (x86_64)** | [I2PChat-linux-x86_64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-v1.4.1.zip) | Unzip → `chmod +x I2PChat.AppImage` → run |
| <img src="docs/icons/icons8-linux-48.png" alt="Linux" width="28" height="28" align="middle" /> **Linux — GUI (aarch64)** | [I2PChat-linux-aarch64-v1.4.1.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-aarch64-v1.4.1.zip) | Same — AppImage inside the zip |
| <img src="docs/icons/icons8-linux-48.png" alt="Linux" width="28" height="28" align="middle" /> **Linux — TUI** | [x86_64 TUI](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-tui-v1.4.1.zip) · [aarch64 TUI](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-aarch64-tui-v1.4.1.zip) | After unzip: **`./i2pchat-tui`** |

> **Router backend:** On a **fresh install** (no `router_prefs.json` yet), I2PChat defaults to a **system** `i2pd` **SAM** endpoint (typically `127.0.0.1:7656`). Switch to the **bundled** sidecar when your build includes it via **More actions → I2P router…** (shortcut **Cmd/Ctrl+R**); the choice is persisted. The same dialog opens the router data/log paths and can restart the bundled router.

### 📦 Package managers

<img src="docs/icons/icons8-windows-48.png" alt="Windows" width="28" height="28" align="middle" /> **Windows (x64) — [winget](https://learn.microsoft.com/windows/package-manager/winget/)** (community manifests in [winget-pkgs](https://github.com/microsoft/winget-pkgs); ships the **`*-winget-*`** zip **without** embedded `i2pd`. For a bundled router, use the full **`*-windows-x64-v*.zip`** from [Releases](https://github.com/MetanoicArmor/I2PChat/releases).)

```powershell
winget install MetanoicArmor.I2PChat       # GUI
winget install MetanoicArmor.I2PChat.TUI   # TUI only
```

If the catalog lags behind a fresh release merge, **`winget show MetanoicArmor.I2PChat`** lists available versions; use **`--version x.y.z`** only when you need to pin one.

<img src="docs/icons/icons8-macos-48.png" alt="macOS" width="28" height="28" align="middle" /> **macOS (arm64) — [Homebrew](https://brew.sh)** ([tap](https://github.com/MetanoicArmor/homebrew-i2pchat))

```bash
brew install --cask metanoicarmor/i2pchat/i2pchat       # GUI — I2PChat.app
brew install --cask metanoicarmor/i2pchat/i2pchat-tui   # TUI only
```

Отдельный `brew tap` подключать не нужно — при установке через `metanoicarmor/i2pchat/...` tap подтянется автоматически.
(`brew tap MetanoicArmor/i2pchat` then `brew install --cask i2pchat` тоже работает, если нужен классический путь.)

<img src="docs/icons/icons8-arch-linux-48.png" alt="Arch Linux" width="28" height="28" align="middle" /> **Arch Linux — [AUR](https://aur.archlinux.org/)** (x86_64 and aarch64; example [yay](https://github.com/Jguer/yay))

```bash
yay -S i2pchat-bin       # GUI — AppImage from release
yay -S i2pchat-tui-bin   # TUI only
```

> **Not this repo:** [**`i2pchat-git`**](https://aur.archlinux.org/packages/i2pchat-git) (`yay -S i2pchat-git`) builds [**vituperative/i2pchat**](https://github.com/vituperative/i2pchat) — another I2P chat client (**Qt5**). It may still install and run as *that* app, but it is **not** **MetanoicArmor/I2PChat** (Python / PyQt6 / Textual TUI). For this project use **`i2pchat-bin`** / **`i2pchat-tui-bin`**, or clone this repo and run **`python -m i2pchat.gui`** / **`python -m i2pchat.tui`**.

<img src="docs/icons/icons8-debian-48.png" alt="Debian" width="28" height="28" align="middle" /> <img src="docs/icons/icons8-ubuntu-48.png" alt="Ubuntu" width="28" height="28" align="middle" /> **Debian / Ubuntu — `.deb` from [Releases](https://github.com/MetanoicArmor/I2PChat/releases)** (works without any mirror):

```bash
# after downloading e.g. i2pchat_1.4.1_amd64.deb
sudo apt install ./i2pchat_*_amd64.deb
# optional TUI-only: sudo apt install ./i2pchat-tui_*_amd64.deb
```

**Optional apt mirror** (GitHub Pages, **amd64** + **arm64**): [metanoicarmor.github.io/I2PChat-ng](https://metanoicarmor.github.io/I2PChat-ng/) — see [`packaging/apt/README.md`](packaging/apt/README.md). (Repo rename: old `…/I2PChat/` Pages path **404**; use **`I2PChat-ng`**.) Or install `.deb` from Releases above.

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://metanoicarmor.github.io/I2PChat-ng/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
sudo tee /etc/apt/sources.list.d/i2pchat.sources >/dev/null <<'EOF'
Types: deb
URIs: https://metanoicarmor.github.io/I2PChat-ng
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/i2pchat.gpg
Architectures: amd64 arm64
EOF
sudo apt update
sudo apt install i2pchat i2pchat-tui   # pick one or both
```

Legacy one-line:  
`echo 'deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://metanoicarmor.github.io/I2PChat-ng stable main' | sudo tee /etc/apt/sources.list.d/i2pchat.list`

### ℹ️ About

I2PChat is a cross‑platform chat client for the [I2P](https://i2pd.website) anonymity network over **SAM** — **PyQt6 GUI** with light/dark themes **and** an optional **terminal (TUI)** build on the same core.

### Audit / Аудит

[![English audit](https://img.shields.io/badge/🔍%20Audit-EN-green.svg)](docs/AUDIT_EN.md)
[![Русский аудит](https://img.shields.io/badge/🔍%20Аудит-RU-orange.svg)](docs/AUDIT_RU.md)

---

<details>
<summary>📜 <i>Sur le secret</i> — Pierre Janet</summary>

<br>

> *Chez l'homme naïf la croyance est liée à son expression. Avoir une croyance, c'est l'exprimer, l'affirmer; beaucoup de personnes disent: «Si je ne peux pas parler tout haut, je ne peux pas penser. Si je ne parle pas de ce en quoi je crois, je ne peux pas y croire. Et, au contraire, quand je crois quelque chose, il faut que je l'affirme; quand je pense quelque chose, il faut que je le dise.» Si l'on empêche ces personnes de parler, elles penseront à autre chose. Le secret n'est donc pas une fonction psychologique primitive, c'est un phénomène tardif. Il apparaît à l'époque de la réflexion.*
>
> *Il vaut mieux ne pas communiquer ses projets: en les racontant on se met immédiatement dans une position défavorable. Même si l'idée n'est pas prise, elle sera critiquée d'avance. Il ne faut pas montrer les brouillons. Que se passera-t-il si vous commencez à exprimer toutes vos rêveries, toutes ces pensées «pour vous-même» qui vous soutiennent? Les autres se moqueront de vous, diront que c'est ridicule, absurde, et détruiront vos rêves. «Peu importe», direz-vous, «puisque je sais bien moi-même que ce ne sont que des rêves». Mais en détruisant vos rêves, ils emporteront aussi votre courage et l'enthousiasme que vous y puisiez.*
>
> *Il vient une époque où il n'est plus toujours bon d'exprimer au dehors les phénomènes psychologiques, de les rendre publics. Dans la société, dans le groupe auquel nous appartenons, il faut savoir garder certaines choses secrètes et en dire d'autres; avoir quelque chose pour soi et quelque chose pour les autres. C'est une opération difficile qui se rapproche de l'évaluation, car pour produire une impression favorable il vaut mieux ne pas tout dire. Tout le monde devrait savoir faire cela. Mais c'est difficile et les timides y réussissent mal; aussi l'une de leurs difficultés dans la société est-elle un trouble de la fonction du secret.*
>
> *Il existe toute une catégorie de personnes — les primitifs, les enfants, les malades — chez qui la fonction du secret n'existe pas; ils ne savent pas ce que c'est. Le petit enfant n'a pas de secret. Le malade en état de désagrégation mentale parle tout haut et dit toutes sortes de sottises: il ne comprend absolument pas qu'il y ait des choses qu'il faut garder secrètes.*

</details>

---

<p align="center">
  Created with ❤️ by <b>Vade</b> for the privacy and anonymity community
  <br><br>
  © 2026 Vade
</p>
