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
- **Profiles (.dat)** — multiple profiles, load and import; each profile’s data lives under **`profiles/<name>/`** in the app data directory (legacy flat files migrate automatically on startup — see **§ profile paths** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md))
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

The runtime is built around one shared async engine — `I2PChatCore` — with thin UI adapters on top and protocol / crypto / BlindBox services below:

```mermaid
flowchart TB
    subgraph Entry["UI / entrypoints"]
        run["python -m i2pchat.gui
i2pchat/run_gui.py"]
        qt["PyQt6 GUI
i2pchat/gui/main_qt.py
ChatWindow + qasync event loop"]
        tui["Textual TUI
i2pchat/gui/chat_python.py"]
        present["Presentation helpers
i2pchat/presentation/*
status / drafts / replies / unread / notification policy"]
        guiStore["GUI-side persistence
chat_history.py
contact_book.py
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
• secure handshake + TOFU pinning
• send/receive loops
• ACK tracking + delivery telemetry
• text / file / image flows
• BlindBox root exchange"]
        retry["Retry helpers
send_retry_policy.py
transfer_retry.py"]
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
        i2plib["Vendored i2plib
SESSION CREATE
STREAM CONNECT / ACCEPT
DEST LOOKUP"]
        sam["I2P router
SAM API"]
        peer["Remote peer
live secure chat stream"]
        boxes["BlindBox replicas
I2P or loopback endpoints"]
    end

    subgraph ProfileState["Profile / local identity"]
        profile["profiles/<name>/ per profile
<name>.dat + keyring
stored peer lock
trust store
signing seed"]
    end

    profile -->|"load / save identity,
trust pins, peer lock"| core
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
    bbclient <-->|"SAM streams or TCP"| i2plib
    i2plib <--> sam
    sam <--> peer
    bbclient <--> boxes
    core -->|"status / message / file /
delivery callbacks"| qt
```

Runtime in practice:

1. **Startup**: `main_qt.py` runs legacy **profile layout migration** (flat `*.dat` in the data root → `profiles/<name>/`) before the profile picker, then creates `ChatWindow`; `start_core()` calls `I2PChatCore.init_session()`, which loads or creates the profile identity, opens the long-lived SAM session, warms up tunnels, and starts `accept_loop()` / `tunnel_watcher()`.
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
  <img src="screenshots/7.png" alt="I2PChat – chat and image preview" width="900" />
</p>

The gallery above is a short subset. **`screenshots/2.png`** (⋯ menu), **`3.png`** (profile picker), **`5.png`** (emoji picker), and **`6.png`** (BlindBox diagnostics) are documented inline in [**MANUAL_EN.md**](docs/MANUAL_EN.md) / [**MANUAL_RU.md**](docs/MANUAL_RU.md).

### 📦 Prebuilt binaries

**[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** — prebuilt binaries for Windows, macOS, and Linux.

Currently available:

- **Windows (x64) GUI**
  - Archive: `I2PChat-windows-x64.zip`
  - Inside: `I2PChat\I2PChat.exe`
  - Built with **Python 3.14** and PyInstaller, includes the Python runtime and all dependencies.
  - **Python is *not* required on the target system** – just unpack the zip and run `I2PChat.exe`.

Other platforms are available — see the table below or check [Releases](https://github.com/MetanoicArmor/I2PChat/releases/latest).

### 🛠 Running from source

Requirements:

- Python **3.14+** (recommended; this is what the vendored local `i2plib` copy and current builds are tested with)
- [i2pd](https://i2pd.website) router with **SAM** enabled (default port `7656`)

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
./.venv314/bin/python -m i2pchat.gui.main_qt
```

**Windows (PowerShell)**

```powershell
py -3.14 -m venv .venv314
.\.venv314\Scripts\pip install -r requirements.txt
.\.venv314\Scripts\python -m i2pchat.gui.main_qt
```

If the venv already exists and dependencies are installed, only the last command is needed to launch.

The same code path is available as `python -m i2pchat.run_gui` (matches [`i2pchat/run_gui.py`](i2pchat/run_gui.py), the PyInstaller analyzed script). Prefer `-m` from the repo root; running the `.py` file directly can break package imports.

PyInstaller builds use [`i2pchat/run_gui.py`](i2pchat/run_gui.py) as the entry script (equivalent
to `python -m i2pchat.gui` / `python -m i2pchat.gui.main_qt`). All modules live under `i2pchat/`.

**Developer note (BlindBox):** [`i2pchat/blindbox/blindbox_server_example.py`](i2pchat/blindbox/blindbox_server_example.py) is a minimal **loopback-only** (`127.0.0.1`) sample **without authentication**. Do not bind it to all interfaces or expose it on untrusted networks; production-style replicas need the full Blind Box deployment model (see **§4.9** in [MANUAL_EN](docs/MANUAL_EN.md) / [MANUAL_RU](docs/MANUAL_RU.md)).

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
- Packs it into `I2PChat.AppImage` using `appimagetool`.
- Creates release archive `I2PChat-linux-<arch>-v<version>.zip` (contains `I2PChat.AppImage`).

#### 🍎 macOS (GUI .app bundle)

```bash
./build-macos.sh
```

- Uses Python 3.14+ (from PATH or Homebrew).
- Builds `dist/I2PChat.app` via PyInstaller.

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
3. Build a GUI‑only PyQt6 binary:
   - Output folder: `dist\I2PChat\`
   - Main executable: `dist\I2PChat\I2PChat.exe`

The resulting `I2PChat.exe` is self‑contained and can be distributed to machines without Python installed.

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

The vendored [`i2plib/`](i2plib/) package remains under the **MIT** license (see [`i2plib/__version__.py`](i2plib/__version__.py)).

### ☕ Buy me a coffee

If you like this project and want to support development, you can send a small donation in Bitcoin:

- **BTC address**: `bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd`

<p align="center">
  <img src="btc_donation_qr.png" alt="Bitcoin donation QR" width="220" />
</p>

---

## 🚀 Quick Start

### 📥 Prebuilt Downloads

**[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** — prebuilt bundles match `VERSION` in the repo (currently **v1.1.2**); no Python installation required.

| Platform | Download | Launch |
|----------|----------|--------|
| **Windows** | [I2PChat-windows-x64-v1.1.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-x64-v1.1.2.zip) | Unzip → run `I2PChat.exe` |
| **macOS** | [I2PChat-macOS-arm64-v1.1.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-arm64-v1.1.2.zip) | Unzip → open `I2PChat.app` |
| **Linux** | [I2PChat-linux-x86_64-v1.1.2.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-v1.1.2.zip) | Unzip → `chmod +x I2PChat.AppImage` → run |

> **Requirement:** [i2pd](https://i2pd.website) router must be running with SAM API enabled (default port 7656).

### ℹ️ About

I2PChat is a cross‑platform chat client for the [I2P](https://i2pd.website) anonymity network, using the SAM interface.  
PyQt6 GUI with light and dark themes.

Originally derived from [`termchat-i2p-python`](http://git.community.i2p/stan/termchat-i2p-python) by Stanley (I2P community), substantially rewritten.

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
