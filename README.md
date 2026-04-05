<p align="center">
  <img src="image.png" alt="I2PChat Logo" width="280" />
</p>

<h1 align="center">I2PChat</h1>

<p align="center">
  <a href="https://github.com/MetanoicArmor/I2PChat/releases/latest"><img src="https://img.shields.io/github/v/release/MetanoicArmor/I2PChat?label=release" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/MetanoicArmor/I2PChat" alt="License"></a>
  <a href="requirements.txt"><img src="https://img.shields.io/badge/Python-3.14+-blue.svg" alt="Python"></a>
  <a href="https://i2pd.website"><img src="https://img.shields.io/badge/I2P-SAM%20API-purple.svg" alt="I2P"></a>
</p>

<p align="center">
  <b>Experimental peer‑to‑peer chat client for the <a href="https://i2pd.website">I2P</a> anonymity network.</b><br>
  Cross‑platform GUI (PyQt6) on top of a shared asynchronous core.
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
- [📦 Prebuilt binaries](#-prebuilt-binaries)
- [🛠 Running from source](#-running-from-source)
- [🔧 Cross‑platform builds](#-crossplatform-builds)
- [📄 License](#-license)
- [☕ Buy me a coffee](#-buy-me-a-coffee)

### ✨ Features

- **End‑to‑end communication over I2P SAM** (via `i2plib`)
- **E2E encryption** — handshake, key signing and verification
- **TOFU** — peer key pinning on first contact
- **Lock to peer** — bind a profile to a single peer
- **PyQt6 GUI** with light and dark themes (macOS-style, consistent and predictable on all platforms)
- **File transfer** and **image sending** (Send picture: PNG, JPEG, WebP) between peers
- **Profiles (.dat)** — multiple profiles, load and import; each profile’s data lives under **`profiles/<name>/`** in the app data directory (if older **flat** `*.dat` files still sit in the data root, they are **migrated on startup** into that layout — see **§ profile paths** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md))
- **System notifications** — tray toasts for new messages
- **Sound notifications** for incoming messages
- **BlindBox (default-on for named profiles)** — offline message delivery
- **Optional encrypted chat history** — per-peer local history (toggle **Chat history: ON/OFF** in the **⋯** menu); encrypted at rest with keys derived from your profile identity (see **§4.11** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md))
- **Contact book (Saved peers)** — left sidebar list backed by **`profiles/<name>/<name>.contacts.json`**: quick switch between saved `.b32.i2p` peers, optional display name/note, unread hints, resize/collapse, and a context menu (edit, trust details, remove). See **§3.1** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md).
- Cross‑platform build scripts (Linux, macOS, Windows)

#### 📖 Manuals

- **English manual**: [**docs/MANUAL_EN.md**](docs/MANUAL_EN.md)
- **Русский мануал**: [**docs/MANUAL_RU.md**](docs/MANUAL_RU.md)

### 🧠 Core architecture

The runtime is built around one shared async engine — `I2PChatCore` — with thin UI adapters on top and protocol / crypto / BlindBox services below.

Plain-text map (avoids GitHub’s Mermaid viewer, which can fail to load assets such as `viewscreen.githubusercontent.com/.../2038-*.js` in some browsers or networks):

- **UI / entrypoints** — `i2pchat/run_gui.py`, `python -m i2pchat.gui`, PyQt6 [`i2pchat/gui/main_qt.py`](i2pchat/gui/main_qt.py) (`ChatWindow` + qasync), [`i2pchat/presentation/`](i2pchat/presentation/) (status, drafts, replies, unread, notifications), GUI-side persistence (`chat_history`, `contact_book`, `profile_backup`). The Qt layer calls into **I2PChatCore** and receives status / message / file / delivery callbacks.
- **Shared async core** — [`i2pchat/core/i2p_chat_core.py`](i2pchat/core/i2p_chat_core.py): profile/session bootstrap, accept/connect, secure handshake + TOFU pinning, send/receive loops, ACKs and delivery telemetry, text/file/image, BlindBox root exchange; retry helpers [`send_retry_policy.py`](i2pchat/core/send_retry_policy.py), [`transfer_retry.py`](i2pchat/core/transfer_retry.py).
- **Protocol + security** — framing in [`protocol/protocol_codec.py`](i2pchat/protocol/protocol_codec.py); delivery semantics in [`protocol/message_delivery.py`](i2pchat/protocol/message_delivery.py); [`i2pchat/crypto.py`](i2pchat/crypto.py) (X25519, Ed25519, HKDF, SecretBox, HMAC).
- **BlindBox** — client ([`blindbox_client.py`](i2pchat/blindbox/blindbox_client.py)), key schedule, blobs, [`storage/blindbox_state.py`](i2pchat/storage/blindbox_state.py), optional [`blindbox_local_replica.py`](i2pchat/blindbox/blindbox_local_replica.py); replicas over I2P or loopback.
- **Transport** — vendored **i2plib** (SAM session, streams, DEST LOOKUP) ↔ **I2P router (SAM)** ↔ **remote peer**; BlindBox traffic to **replica endpoints**.
- **Profile / local identity** — `profiles/<name>/` (`.dat`, keyring, peer lock, trust store, signing seed) loaded into **I2PChatCore**.

Runtime in practice:

1. **Startup**: `main_qt.py` runs **profile directory migration** when needed (flat `*.dat` in the data root → `profiles/<name>/`) before the profile picker, then creates `ChatWindow`; `start_core()` calls `I2PChatCore.init_session()`, which loads or creates the profile identity, opens the long-lived SAM session, warms up tunnels, and starts `accept_loop()` / `tunnel_watcher()`.
2. **Live chat path**: `connect_to_peer()` or `accept_loop()` establishes an I2P stream; `I2PChatCore` runs the plaintext handshake boundary, verifies/pins the peer signing key (TOFU), derives session subkeys, then switches to encrypted vNext frames through `ProtocolCodec` + `crypto`.
3. **Delivery tracking**: each outgoing text / file / image gets a `MSG_ID` and ACK context; `message_delivery.py` turns low-level outcomes into UI states (`sending`, `queued`, `delivered`, `failed`).
4. **Offline path (BlindBox)**: when no live secure session is available, `send_text()` can route through BlindBox — derive deterministic lookup/blob keys, encrypt a padded blob, PUT it to one or more BlindBox replicas, and later poll / decrypt GET results back into the chat stream.
5. **UI responsibility split**: `I2PChatCore` stays UI-agnostic and emits callbacks only; the Qt layer renders chat, status and notifications, while GUI-side storage modules persist chat history, contacts, drafts and backup/export data.

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
  <img src="screenshots/10.png" alt="I2PChat – chat and file transfer (sending)" width="900" />
</p>

The gallery above is a short subset. **`screenshots/2.png`** (⋯ menu), **`3.png`** (profile picker), **`5.png`** (emoji picker), **`6.png`** (BlindBox diagnostics), **`8.png`** (I2P router dialog), and **`9.png`** (Blind Box setup examples — `install.sh` / **Copy curl** for a custom replica) are documented inline in [**MANUAL_EN.md**](docs/MANUAL_EN.md) / [**MANUAL_RU.md**](docs/MANUAL_RU.md).

### 📦 Prebuilt binaries

**[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** — prebuilt binaries for Windows, macOS, and Linux (version **v1.2.2** matches [`VERSION`](VERSION) in this repo).

Currently shipped assets use **versioned** zip names, for example:

- **Windows (x64) GUI** — `I2PChat-windows-x64-v1.2.2.zip`
  - Inside: `I2PChat\I2PChat.exe` (Qt GUI) and **`I2PChat\I2PChat-tui.exe`** (Textual TUI, **console** — run from **cmd** / **PowerShell**).
  - Built with **Python 3.14** and PyInstaller, includes the Python runtime and all dependencies.
  - **Python is *not* required on the target system** – unpack the zip and run `I2PChat.exe` or `I2PChat-tui.exe [profile]`.
  - Release bundles can now include a **bundled `i2pd` sidecar**, so the app can work either with a system router or an embedded router backend.

**Linux** (`I2PChat-linux-x86_64-v1.2.2.zip` → AppImage) and **macOS arm64** (`I2PChat-macOS-arm64-v1.2.2.zip` → `.app`) — same release page; **direct download links** are in the **Prebuilt Downloads** table later in this README. Prebuilt AppImage and `.app` also ship a **console TUI** binary (`I2PChat-tui`); see the table below.

#### Package managers (optional)

Upstream-maintained recipes and docs live under [**packaging/**](packaging/README.md):

| Ecosystem | Status | Notes |
|-----------|--------|--------|
| **Homebrew** (macOS arm64 cask) | Files in [`packaging/homebrew/`](packaging/homebrew/README.md) | Publish a tap repo `homebrew-i2pchat` or open a PR to Homebrew cask; then `brew install --cask i2pchat`. |
| **winget** (Windows portable zip) | Manifests in [`packaging/winget/`](packaging/winget/README.md) | Copy versioned YAML into a PR to [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs); then `winget install MetanoicArmor.I2PChat`. |
| **Arch (AUR)** | [`packaging/aur/i2pchat-bin/`](packaging/aur/README.md) | Submit `PKGBUILD` / `.SRCINFO` as package `i2pchat-bin`; users: `yay -S i2pchat-bin` (or any AUR helper). |
| **Debian/Ubuntu** | [`packaging/debian/`](packaging/debian/README.md) | Prefer **`i2pchat_*_amd64.deb`** attached to [GitHub Releases](https://github.com/MetanoicArmor/I2PChat/releases) (built by [`release-deb.yml`](.github/workflows/release-deb.yml) when the Linux zip is on the release), or build locally with [`build-deb-from-appimage.sh`](packaging/debian/build-deb-from-appimage.sh). Optional: your own apt repo / PPA / **Flatpak** (Flathub). |
| **Fedora** | [`packaging/fedora/`](packaging/fedora/README.md) | RPM from the same upstream zip via [`i2pchat.spec`](packaging/fedora/i2pchat.spec); publish on **COPR**, then `dnf copr enable …` and `dnf install i2pchat`. |

After each new **GitHub release** tag, refresh checksums with `./packaging/refresh-checksums.sh` and update version fields in the manifests you maintain (including **`Version:`** in the Fedora spec).

### 🛠 Running from source

Requirements:

- Python **3.14+** (recommended; this is what the vendored local `i2plib` copy and current builds are tested with)
- one of:
  - a **system** [i2pd](https://i2pd.website) router with **SAM** enabled (default port `7656`), or
  - a **bundled** `i2pd` binary shipped with your build/package

Quick run commands (from repo root):

**Linux (Debian/Ubuntu)** — system packages you may need:

```bash
# Python 3.14 (if missing)
sudo apt install python3.14 python3.14-venv

# PyQt6 6.5+ on X11: without this, Qt may fail to load the "xcb" platform plugin
# (error: xcb-cursor0 / libxcb-cursor0 is needed)
sudo apt install libxcb-cursor0
```

**macOS / Linux**

```bash
python3.14 -m venv .venv314
./.venv314/bin/pip install -r requirements.txt
# PyQt6 GUI (package entry; optional profile name as first arg):
./.venv314/bin/python -m i2pchat.gui
# Same GUI, explicit module:
# ./.venv314/bin/python -m i2pchat.gui.main_qt
# Terminal UI (Textual, same venv):
./.venv314/bin/python -m i2pchat.tui
# Example with a saved profile (name at the end of the line):
# ./.venv314/bin/python -m i2pchat.tui alice
```

**Windows (PowerShell)**

```powershell
py -3.14 -m venv .venv314
.\.venv314\Scripts\pip install -r requirements.txt
# PyQt6 GUI (package entry; optional profile as first arg):
.\.venv314\Scripts\python -m i2pchat.gui
# Same GUI, explicit module:
# .\.venv314\Scripts\python -m i2pchat.gui.main_qt
# Terminal UI (Textual, same venv):
.\.venv314\Scripts\python -m i2pchat.tui
# Example with a saved profile (name at the end of the line):
# .\.venv314\Scripts\python -m i2pchat.tui alice
```

If the venv already exists and dependencies are installed, run only the `python -m …` line you need (GUI or terminal).

The same code path is available as `python -m i2pchat.run_gui` (matches [`i2pchat/run_gui.py`](i2pchat/run_gui.py), the PyInstaller analyzed script). Prefer `-m` from the repo root; running the `.py` file directly can break package imports.

PyInstaller builds use [`i2pchat/run_gui.py`](i2pchat/run_gui.py) as the entry script (equivalent
to `python -m i2pchat.gui` / `python -m i2pchat.gui.main_qt`). All modules live under `i2pchat/`.

**Developer note (BlindBox):** [`i2pchat/blindbox/blindbox_server_example.py`](i2pchat/blindbox/blindbox_server_example.py) is the hardened service implementation, while the **production-oriented package entrypoint** is `python -m i2pchat.blindbox.daemon`. The repo now also ships package-local `systemd`, env, install/bundle helper scripts, a one-shot `install.sh`, and fail2ban assets under [`i2pchat/blindbox/daemon/`](i2pchat/blindbox/daemon/) and [`i2pchat/blindbox/fail2ban/`](i2pchat/blindbox/fail2ban/). Public replicas behind an I2P tunnel may keep replica auth empty; raw TCP / loopback exposure should still keep a token. See **§4.9** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md).

### 🔧 Cross‑platform builds

The project is intentionally **cross‑platform** and ships with helper scripts for the main targets.  
Everywhere, the recommended/runtime version is **Python 3.14+** (the repo includes a vendored local `i2plib` copy compatible with modern asyncio; PyPI `i2plib` is not used).

#### 🐧 Linux (GUI AppImage)

```bash
./build-linux.sh
```

This script:

- Uses `python3.14` (or default `python3`) and `.venv314`.
- Builds a self‑contained GUI binary via PyInstaller.
- Packs it into `I2PChat.AppImage` using `appimagetool` (в образе: `usr/bin/I2PChat` и **`usr/bin/I2PChat-tui`**, плюс `.desktop` для TUI с `Terminal=true`).
- Creates release archive `I2PChat-linux-<arch>-v<version>.zip` (contains `I2PChat.AppImage`).

#### 🍎 macOS (GUI .app bundle)

```bash
./build-macos.sh
```

- Uses Python 3.14+ (from PATH or Homebrew).
- Builds `dist/I2PChat.app` via PyInstaller (в бандле: GUI и **`Contents/MacOS/I2PChat-tui`** → `Resources/I2PChat/I2PChat-tui`).

### 🪟 Windows build (GUI)

For reproducible Windows builds there is a PowerShell script:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

For a safer one-off session, prefer:

```powershell
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope Process RemoteSigned; .\build-windows.ps1"
```

This limits policy relaxation to the current process and does not change machine/user policy permanently.

It will:

1. Create a fresh virtual environment `.venv314` using **Python 3.14** via `py -3.14 -m venv`.
2. Install all dependencies from `requirements.txt` and `requirements-build.txt` (both hash-locked).
3. Build PyQt6 GUI + Textual TUI binaries:
   - Output folder: `dist\I2PChat\`
   - `I2PChat.exe` (GUI) and **`I2PChat-tui.exe`** (console TUI)

The resulting executables are self‑contained and can be distributed to machines without Python installed.

### Verify release artifacts

Release build scripts generate:

- `SHA256SUMS` file for produced release archive(s);
- detached armored GPG signature `SHA256SUMS.asc` (best-effort by default).

These files are **not** tracked in git (they differ per OS/build); upload them **with the release assets** on GitHub.

Build-time controls:

- `I2PCHAT_SKIP_GPG_SIGN=1` — always skip detached signature creation;
- `I2PCHAT_REQUIRE_GPG=1` — fail build if GPG signing is unavailable or fails;
- `I2PCHAT_GPG_KEY_ID=<keyid>` — select a specific key for detached signature.

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
I2PCHAT_PADDING_PROFILE=off python -m i2pchat.gui.main_qt
```

Trade-off: stronger padding reduces length correlation but increases bandwidth.

#### ❄️ NixOS

```bash
# Run directly
nix run github:MetanoicArmor/I2PChat

# Development shell
nix develop github:MetanoicArmor/I2PChat
```

### 📄 License

I2PChat is licensed under the **GNU Affero General Public License v3.0** (or any later version — see section 14 of the license). The full text is in [`LICENSE`](LICENSE).

The vendored [`vendor/i2plib/`](vendor/i2plib/) package (alongside [`vendor/i2pd/`](vendor/i2pd/)) remains under the **MIT** license (see [`vendor/i2plib/__version__.py`](vendor/i2plib/__version__.py)).

### ☕ Buy me a coffee

If you like this project and want to support development, you can send a small donation in Bitcoin:

- **BTC address**: `bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd`

<p align="center">
  <img src="btc_donation_qr.png" alt="Bitcoin donation QR" width="220" />
</p>

---

## 🚀 Quick Start

### 📥 Prebuilt Downloads

**[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** — prebuilt bundles match `VERSION` in the repo (currently **v1.2.2**); no Python installation required.

| Platform | Download | Launch |
|----------|----------|--------|
| **Windows** | [I2PChat-windows-x64-v1.2.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-x64-v1.2.2.zip) | Unzip → `I2PChat.exe` (GUI) or **`I2PChat-tui.exe`** in the same folder (cmd/PowerShell) |
| **macOS** | [I2PChat-macOS-arm64-v1.2.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-arm64-v1.2.2.zip) | Unzip → open `I2PChat.app`; **TUI:** `I2PChat.app/Contents/MacOS/I2PChat-tui` [profile] |
| **Linux** | [I2PChat-linux-x86_64-v1.2.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-v1.2.2.zip) | GUI: `chmod +x I2PChat.AppImage` → `./I2PChat.AppImage`. **TUI:** `MNT=$(./I2PChat.AppImage --appimage-mount)` then `"$MNT/usr/bin/I2PChat-tui"` (or use the **I2P Chat (terminal)** desktop entry if your launcher shows it) |

> **Router backend:** On a **fresh profile** (no saved preference), I2PChat defaults to the **bundled** `i2pd` sidecar. You can switch to a system `i2pd` (SAM, typically `127.0.0.1:7656`) via **More actions → I2P router…** (shortcut **Cmd/Ctrl+R**); that choice is persisted. The same dialog opens the router data/log paths and can restart the bundled router.

### ℹ️ About

I2PChat is a cross‑platform chat client for the [I2P](https://i2pd.website) anonymity network, using the SAM interface.  
PyQt6 GUI with light and dark themes.

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
