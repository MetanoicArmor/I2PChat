# Installing I2PChat

**Supported binaries** are published on **[GitHub Releases](https://github.com/MetanoicArmor/I2PChat/releases)**. File names include the version (for example `v1.2.3`); use **Latest** on that page for the current build.

## Windows (x64)

1. Download **`I2PChat-windows-x64-v<version>.zip`**.
2. Extract the archive.
3. Run **`I2PChat.exe`** (GUI) or **`I2PChat-tui.exe`** from a terminal (cmd / PowerShell) for the Textual TUI. Optional profile name as the first argument.

Python is **not** required on the target machine (PyInstaller bundle).

**TUI-only zip:** **`I2PChat-windows-tui-x64-v<version>.zip`** contains the `I2PChat` folder with **`I2PChat-tui.exe`**, `_internal`, and `vendor` only (no GUI exe). Use this with **`MetanoicArmor.I2PChat.TUI`** on winget and similar `-tui` packages.

## macOS (Apple Silicon / arm64)

1. Download **`I2PChat-macOS-arm64-v<version>.zip`**.
2. Unzip and open **`I2PChat.app`**.
3. **TUI (optional):** `I2PChat.app/Contents/MacOS/I2PChat-tui` with an optional profile argument.

**TUI-only zip:** **`I2PChat-macos-arm64-tui-v<version>.zip`** unpacks to **`i2pchat-tui`** (launcher) plus **`I2PChat/`** (PyInstaller onedir). Run **`./i2pchat-tui`** from the extracted folder. Homebrew: **`brew install --cask i2pchat-tui`**.

## Linux (x86_64)

1. Download **`I2PChat-linux-x86_64-v<version>.zip`** (contains one **AppImage**).
2. `chmod +x` the `.AppImage` file if needed, then run it for the GUI.
3. **TUI (inside AppImage):** mount or extract the image and run **`usr/bin/I2PChat-tui`**, or use the **I2P Chat (terminal)** desktop entry if present.

**TUI-only zip:** **`I2PChat-linux-x86_64-tui-v<version>.zip`** — unpack and run **`./i2pchat-tui`** (wrapper sets `LD_LIBRARY_PATH` for `usr/bin/I2PChat-tui` and `_internal`).

**glibc / `GLIBC_X.XX not found`:** PyInstaller bundles use the **C standard library from the machine where they were built**. If you see `version 'GLIBC_2.xx' not found` in `libc.so.6`, your distro’s glibc is **older** than the build host’s (for example a zip built on Fedora 42 may require very new symbols). **Workarounds:** install **`I2PChat` from source** on your machine (see [Build from source](#build-from-source)), use **Flatpak** if available, or install **fresh** Linux zips / `.deb` built by **[Build Linux release artifacts](../.github/workflows/build-linux-release-artifacts.yml)** (Ubuntu 22.04; CI also smoke-tests the TUI zip in Docker). If you already used apt: **`sudo apt install --reinstall i2pchat-tui`** only helps after the mirror or local `.deb` was rebuilt — otherwise remove and install a newly downloaded `i2pchat-tui_*_amd64.deb` from Releases. To see what the bundle asks for: `strings /opt/i2pchat-tui/usr/bin/_internal/python3.*/lib-dynload/termios*.so 2>/dev/null | grep '^GLIBC_' | sort -u`.

**Arch Linux (AUR):** with an AUR helper such as **yay** or **paru**:

```bash
yay -S i2pchat-bin      # GUI: official AppImage → /opt/i2pchat, command i2pchat
yay -S i2pchat-tui-bin  # TUI-only: slim Linux zip → /opt/i2pchat-tui, command i2pchat-tui
```

Package pages: [i2pchat-bin](https://aur.archlinux.org/packages/i2pchat-bin), [i2pchat-tui-bin](https://aur.archlinux.org/packages/i2pchat-tui-bin). Maintainer sources: [`packaging/aur/`](../packaging/aur/).

**Optional `.deb` (Debian/Ubuntu):** some releases include **`i2pchat_<version>_amd64.deb`** (GUI) and **`i2pchat-tui_<version>_amd64.deb`** (TUI only). Install with `sudo apt install ./i2pchat_*_amd64.deb` / `./i2pchat-tui_*_amd64.deb`. If missing, use the Linux zips or build locally — [`packaging/debian/README.md`](../packaging/debian/README.md).

**Optional apt source (same repo, GitHub Pages):** if maintainers enabled signing and **Pages → GitHub Actions** deploy, you can add the published apt mirror and run `sudo apt install i2pchat` — see [`packaging/apt/README.md`](../packaging/apt/README.md).

**Optional `.rpm` (Fedora / RHEL-compatible):** соберите локально или через COPR — см. [`packaging/fedora/README.md`](../packaging/fedora/README.md). На странице релиза готовый `.rpm` не обязателен; можно поставить из **Linux AppImage zip** выше.

## Router (I2P)

You need a working I2P router with **SAM** enabled (typical embedded or system **i2pd**). Fresh installs often default to a **bundled** router; you can switch to a system router in the app. Details: [**MANUAL_EN.md**](MANUAL_EN.md) / [**MANUAL_RU.md**](MANUAL_RU.md) and the **Router backend** note in the [README](../README.md) Quick Start section.

## Third-party package managers

Unofficial packages may exist (Homebrew, winget, AUR, Fedora COPR, etc.). The **authoritative** artifacts remain the GitHub release files above. Maintainer-facing recipes live under **[`packaging/`](../packaging/README.md)** only.

## Build from source

See **Running from source** and **Cross‑platform builds** in the repository root [`README.md`](../README.md) (Python 3.14+, venv, `pip install -r requirements.txt`, `python -m i2pchat.gui` or `python -m i2pchat.tui`, plus `build-linux.sh` / `build-macos.sh` / `build-windows.ps1`).

## More documentation

- [MANUAL_EN.md](MANUAL_EN.md) / [MANUAL_RU.md](MANUAL_RU.md) — full user manuals  
- [PROTOCOL.md](PROTOCOL.md) — protocol reference for developers  
