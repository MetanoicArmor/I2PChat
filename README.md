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

**I2PChat** is an **experimental** desktop chat client for the [I2P](https://i2pd.website) network: encrypted, peer-to-peer style sessions over **SAM**, with a **PyQt6** GUI (and an optional **Textual** terminal UI). Prebuilt releases usually ship a **bundled `i2pd`** so you can start without a separate router install; you can switch to a system router in the app.

**Goal:** download a release, run the GUI (or TUI), create or pick a **profile**, connect to a peer’s `.b32.i2p` destination. Full behavior, menus, and troubleshooting → [**docs/MANUAL_EN.md**](docs/MANUAL_EN.md) / [**docs/MANUAL_RU.md**](docs/MANUAL_RU.md).

---

## Quick start

1. Open **[Latest release](https://github.com/MetanoicArmor/I2PChat/releases/latest)** (version matches [`VERSION`](VERSION) in this repo).
2. Step-by-step for every OS → [**docs/INSTALL.md**](docs/INSTALL.md).

**Prebuilt downloads** (file names include the version, e.g. **v1.2.3**):

| Platform | Download | Launch |
|----------|----------|--------|
| **Windows** | [I2PChat-windows-x64-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-x64-v1.2.3.zip) | Unzip → `I2PChat.exe` (GUI) or `I2PChat-tui.exe` (cmd/PowerShell) |
| **macOS (arm64)** | [I2PChat-macOS-arm64-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS-arm64-v1.2.3.zip) | Unzip → `I2PChat.app`; TUI: `I2PChat.app/Contents/MacOS/I2PChat-tui` |
| **Linux (x86_64)** | [I2PChat-linux-x86_64-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-v1.2.3.zip) | `chmod +x` the AppImage → run it; TUI: see INSTALL.md |
| **Debian / Ubuntu (.deb)** | [i2pchat_1.2.3_amd64.deb](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/i2pchat_1.2.3_amd64.deb) (GUI), [i2pchat-tui_1.2.3_amd64.deb](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/i2pchat-tui_1.2.3_amd64.deb) (TUI) | `sudo apt install ./i2pchat_*_amd64.deb` / `sudo apt install ./i2pchat-tui_*_amd64.deb` — optional apt: [`packaging/apt/README.md`](packaging/apt/README.md) |

**TUI-only archives** (separate slim PyInstaller bundle via **`I2PChat-tui.spec`**: Textual + core, **no PyQt6**; for winget / Homebrew **`i2pchat-tui`** / AUR **`i2pchat-tui-bin`**):

| Platform | Download | Launch |
|----------|----------|--------|
| **Windows** | [I2PChat-windows-tui-x64-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-tui-x64-v1.2.3.zip) | Unzip → `I2PChat\I2PChat-tui.exe` |
| **macOS (arm64)** | [I2PChat-macos-arm64-tui-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macos-arm64-tui-v1.2.3.zip) | Unzip → run `./i2pchat-tui` (uses `I2PChat/` onedir next to it) |
| **Linux (x86_64)** | [I2PChat-linux-x86_64-tui-v1.2.3.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-linux-x86_64-tui-v1.2.3.zip) | Unzip → run `./i2pchat-tui` |

No Python needed for these bundles.

**Package managers** (same artifacts as GitHub Releases; third-party install paths):

**macOS (Apple Silicon / arm64)** — [Homebrew](https://brew.sh) [tap](https://github.com/MetanoicArmor/homebrew-i2pchat):

```bash
brew tap MetanoicArmor/i2pchat
brew install --cask i2pchat      # GUI — I2PChat.app from release zip
brew install --cask i2pchat-tui  # TUI only — slim zip
```

**Arch Linux (x86_64)** — [AUR](https://aur.archlinux.org/) (example: [yay](https://github.com/Jguer/yay)):

```bash
yay -S i2pchat-bin      # GUI — official AppImage from release
yay -S i2pchat-tui-bin  # TUI only
```

More platforms and detail → [**docs/INSTALL.md**](docs/INSTALL.md).

> **Router:** On a fresh profile, the app often defaults to the **bundled** `i2pd`. Switch to a system **i2pd** (SAM, usually `127.0.0.1:7656`) via **More actions → I2P router…** (**Cmd/Ctrl+R**). The choice is saved.

Unofficial packages (Homebrew, winget, AUR, `.deb`, COPR) may exist; **canonical binaries** are always on **GitHub Releases**. Maintainer recipes → [**packaging/**](packaging/README.md).

**Contents:** [Features](#features) · [Screenshots](#screenshots) · [Technical docs](#technical-docs) · [For developers](#for-developers) · [License](#license) · [Buy me a coffee](#buy-me-a-coffee)

---

### Language / manuals / planning

[![English manual](https://img.shields.io/badge/📖%20Manual-EN-blue.svg)](docs/MANUAL_EN.md)
[![Русский мануал](https://img.shields.io/badge/📖%20Мануал-RU-red.svg)](docs/MANUAL_RU.md)
[![Roadmap EN](https://img.shields.io/badge/🗺️%20Roadmap-EN-teal.svg)](docs/ROADMAP.md)
[![Roadmap RU](https://img.shields.io/badge/🗺️%20Roadmap-RU-red.svg)](docs/ROADMAP_RU.md)
[![Issue Backlog EN](https://img.shields.io/badge/📝%20Issue%20Backlog-EN-blueviolet.svg)](docs/ISSUE_BACKLOG.md)
[![Issue Backlog RU](https://img.shields.io/badge/📝%20Issue%20Backlog-RU-orange.svg)](docs/ISSUE_BACKLOG_RU.md)
[![English audit](https://img.shields.io/badge/🔍%20Audit-EN-green.svg)](docs/AUDIT_EN.md)
[![Русский аудит](https://img.shields.io/badge/🔍%20Аудит-RU-orange.svg)](docs/AUDIT_RU.md)

---

## Features

- Chat over **I2P SAM** with **E2E encryption**, **TOFU** peer pinning, optional **lock to one peer**
- **PyQt6** GUI (light/dark), **file and image** transfer, notifications (tray + sound)
- **Profiles** (`.dat`) and **saved peers** / contact list; optional **encrypted local chat history**
- **BlindBox** — offline text delivery when the peer is away (details in the manuals)
- Optional **terminal UI**: `python -m i2pchat.tui` from source, or `I2PChat-tui` / `I2PChat-tui.exe` in releases

Full feature list, shortcuts, BlindBox setup, and data paths → **MANUAL_EN** / **MANUAL_RU** above.

---

## Screenshots

<p align="center">
  <img src="screenshots/1.png" alt="I2PChat – main window" width="900" /><br>
  <img src="screenshots/4.png" alt="I2PChat – chat and file transfer" width="900" /><br>
  <img src="screenshots/10.png" alt="I2PChat – TUI (terminal UI) with messaging" width="900" />
</p>

More UI shots and captions → [**MANUAL_EN.md**](docs/MANUAL_EN.md) / [**MANUAL_RU.md**](docs/MANUAL_RU.md).

---

## Technical docs

| Doc | Purpose |
|-----|---------|
| [**INSTALL.md**](docs/INSTALL.md) | Install from releases by platform |
| [**PROTOCOL.md**](docs/PROTOCOL.md) | Framing, handshake, ACK, BlindBox (developers) |
| [**ARCHITECTURE.md**](docs/ARCHITECTURE.md) | Runtime layout and wire-format summary |
| [**BUILD.md**](docs/BUILD.md) | Release scripts, GPG/checksums, padding env, NixOS, BlindBox daemon notes |

---

## For developers

**Requirements:** Python **3.14+**; [i2pd](https://i2pd.website) with **SAM** (e.g. port `7656`) or a **bundled** router from your build.

From repo root (Linux/macOS example):

```bash
python3.14 -m venv .venv314
./.venv314/bin/pip install -r requirements.txt
./.venv314/bin/python -m i2pchat.gui    # GUI; optional profile name as first arg
./.venv314/bin/python -m i2pchat.tui   # terminal UI
```

**Windows:** `py -3.14 -m venv .venv314`, then `.\.venv314\Scripts\pip` / `python -m i2pchat.gui` / `i2pchat.tui`.

On **Debian/Ubuntu** you may need `libxcb-cursor0` for PyQt6 on X11. Prefer `python -m` from the repo root (see [**MANUAL_EN**](docs/MANUAL_EN.md) for detail).

**Release builds, signing, padding, NixOS, BlindBox service layout** → [**docs/BUILD.md**](docs/BUILD.md). **Protocol metadata and padding profile:** override with `I2PCHAT_PADDING_PROFILE=off` (details in BUILD.md).

---

## License

I2PChat is licensed under the **GNU Affero General Public License v3.0** (or later — see section 14 of the license). Full text: [`LICENSE`](LICENSE).

Vendored [`vendor/i2plib/`](vendor/i2plib/) and [`vendor/i2pd/`](vendor/i2pd/) remain under **MIT** (see [`vendor/i2plib/__version__.py`](vendor/i2plib/__version__.py)).

---

## Buy me a coffee

If you want to support development, you can donate **Bitcoin**:

- **BTC address:** `bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd`

<p align="center">
  <img src="btc_donation_qr.png" alt="Bitcoin donation QR" width="220" />
</p>

---

<p align="center">
  Created with ❤️ by <b>Vade</b> for the privacy and anonymity community
  <br><br>
  © 2026 Vade
</p>
