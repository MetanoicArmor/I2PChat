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

**TUI-only zip:** **`I2PChat-macos-arm64-tui-v<version>.zip`** unpacks to **`i2pchat-tui`** (launcher) plus **`I2PChat/`** (PyInstaller onedir). Run **`./i2pchat-tui`** from the extracted folder. Homebrew (tap added automatically): **`brew install --cask metanoicarmor/i2pchat/i2pchat-tui`** (GUI: **`…/i2pchat`**).

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

**Optional `.deb` (Debian/Ubuntu):** some releases include **`i2pchat_<version>_amd64.deb`** / **`i2pchat-tui_<version>_amd64.deb`** and **`i2pchat_<version>_arm64.deb`** / **`i2pchat-tui_<version>_arm64.deb`**. Install with `sudo apt install ./i2pchat_*_<arch>.deb` (or download from the browser). If missing, use the Linux zips or build locally — [`packaging/debian/README.md`](../packaging/debian/README.md).

**apt mirror (Debian/Ubuntu, GitHub Pages)** is **not** guaranteed: it appears only after a maintainer configures **`APT_REPO_GPG_PRIVATE_KEY`** and deploys Pages via Actions — [`packaging/apt/README.md`](../packaging/apt/README.md). **Until then** use **`sudo apt install ./i2pchat_*_*.deb`** from Releases (see above).

**If** the mirror is published, add it with **deb822** (Debian 12+ / recent Ubuntu). Use **`Architectures: amd64 arm64`** when the mirror lists both architectures (see **`…/binary-arm64/Packages.gz`** on the site); if the mirror was built **amd64-only**, use **`Architectures: amd64`** or omit the line — [`packaging/apt/README.md`](../packaging/apt/README.md).

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://metanoicarmor.github.io/I2PChat/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
sudo tee /etc/apt/sources.list.d/i2pchat.sources >/dev/null <<'EOF'
Types: deb
URIs: https://metanoicarmor.github.io/I2PChat
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/i2pchat.gpg
Architectures: amd64 arm64
EOF
sudo apt update
sudo apt install i2pchat        # GUI
# or: sudo apt install i2pchat-tui   # TUI only
```

Legacy `sources.list` line:  
`echo 'deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://metanoicarmor.github.io/I2PChat/ stable main' | sudo tee /etc/apt/sources.list.d/i2pchat.list`

**glibc:** packages from the mirror are the same PyInstaller bundles as the `.deb` on Releases. If they were linked against **GLIBC_2.42**, distros with **older** glibc (e.g. **Ubuntu 24.04 ≈ 2.39**) can still report `GLIBC_2.42 not found` — install a build produced on an older baseline (see [Build Linux release artifacts](../.github/workflows/build-linux-release-artifacts.yml)) or use [Build from source](#build-from-source).

**Optional `.rpm` (Fedora / RHEL-compatible):** соберите локально или через COPR — см. [`packaging/fedora/README.md`](../packaging/fedora/README.md). На странице релиза готовый `.rpm` не обязателен; можно поставить из **Linux AppImage zip** выше.

## Router (I2P)

You need a working I2P router with **SAM** enabled (typical system **i2pd**). Fresh installs default to **system** SAM; switch to **bundled** in the app if your build ships embedded `i2pd`. Details: [**MANUAL_EN.md**](MANUAL_EN.md) / [**MANUAL_RU.md**](MANUAL_RU.md) and the **Router backend** note in the [README](../README.md) Quick Start section.

## Third-party package managers

Unofficial packages may exist (Homebrew, winget, AUR, Fedora COPR, etc.). The **authoritative** artifacts remain the GitHub release files above. Maintainer-facing recipes live under **[`packaging/`](../packaging/README.md)** only.

## Build from source

See **Running from source** and **Cross‑platform builds** in the repository root [`README.md`](../README.md) (Python 3.12+, **uv**, `uv sync`, `uv run python -m i2pchat.gui` or `… tui`, plus `build-linux.sh` / `build-macos.sh` / `build-windows.ps1`).

## More documentation

- [MANUAL_EN.md](MANUAL_EN.md) / [MANUAL_RU.md](MANUAL_RU.md) — full user manuals  
- [PROTOCOL.md](PROTOCOL.md) — protocol reference for developers  
