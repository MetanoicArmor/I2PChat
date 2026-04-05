# Building and releasing I2PChat

Python **3.14+** is recommended everywhere. The repo vendors a local **i2plib** compatible with modern asyncio; PyPI `i2plib` is not used.

## Release build scripts

| Target | Command | Output (typical) |
|--------|---------|------------------|
| Linux (AppImage + zip) | `./build-linux.sh` | `I2PChat.AppImage`, `I2PChat-linux-<arch>-v<version>.zip` (AppImage inside), **`I2PChat-linux-<arch>-tui-v<version>.zip`** (TUI-only tree + `i2pchat-tui` launcher) |
| macOS (.app + zip) | `./build-macos.sh` | `dist/I2PChat.app`, `I2PChat-macOS-<arch>-v<version>.zip`, **`I2PChat-macos-<arch>-tui-v<version>.zip`** |
| Windows | `.\build-windows.ps1` | `dist\I2PChat\I2PChat.exe`, `I2PChat-tui.exe`, **`I2PChat-windows-tui-x64-v<version>.zip`** |

**Linux script** uses `.venv314`, PyInstaller **`I2PChat.spec`** (GUI + TUI exe sharing one Qt onedir), `appimagetool`; the AppImage includes `usr/bin/I2PChat` and **`usr/bin/I2PChat-tui`**, plus a TUI `.desktop` with `Terminal=true`. After that it runs **`I2PChat-tui.spec`** → `dist/I2PChat-tui/` (no Qt) and packs **`I2PChat-linux-*-tui-*.zip`** from that tree.

**macOS** builds `dist/I2PChat.app` from **`I2PChat.spec`** (GUI + in-bundle TUI entrypoint sharing Qt), then **`I2PChat-tui.spec`** for the standalone **`I2PChat-macos-*-tui-*.zip`**.

**Windows** runs both specs; the **`-tui`** zip is built from **`dist\I2PChat-tui\`**. Safer one-off PowerShell:

```powershell
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope Process RemoteSigned; .\build-windows.ps1"
```

## Release signing and checksums

Release build scripts generate:

- `SHA256SUMS` for **both** the main release zip(s) and the **TUI-only** zip produced on that platform (two lines per OS build);
- detached armored GPG signature `SHA256SUMS.asc` (best-effort by default).

These files are **not** tracked in git; upload them **with the release assets** on GitHub.

Environment:

- `I2PCHAT_SKIP_GPG_SIGN=1` — skip detached signature;
- `I2PCHAT_REQUIRE_GPG=1` — fail if GPG signing is unavailable or fails;
- `I2PCHAT_GPG_KEY_ID=<keyid>` — select signing key.

**Official release builds** should use `I2PCHAT_REQUIRE_GPG=1` so unsigned archives are not produced silently.

Verification:

```bash
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

## Protocol padding profile

The transport is encrypted after handshake, but some metadata (frame type, length, pre-handshake identity preface) remains observable.

Encrypted payloads use a padding profile:

- default: `balanced` (pads encrypted plaintext to 128-byte buckets);
- optional: `off` (disable padding).

Override:

```bash
I2PCHAT_PADDING_PROFILE=off python -m i2pchat.gui.main_qt
```

Stronger padding reduces length correlation but increases bandwidth.

## NixOS

```bash
nix run github:MetanoicArmor/I2PChat
nix develop github:MetanoicArmor/I2PChat   # dev shell
```

## BlindBox (daemon / ops)

[`i2pchat/blindbox/blindbox_server_example.py`](../i2pchat/blindbox/blindbox_server_example.py) is the hardened **example** service; the production-oriented entrypoint is `python -m i2pchat.blindbox.daemon`.

The repo ships `systemd` units, env templates, `install.sh`, and fail2ban assets under [`i2pchat/blindbox/daemon/`](../i2pchat/blindbox/daemon/) and [`i2pchat/blindbox/fail2ban/`](../i2pchat/blindbox/fail2ban/).

Public replicas behind an I2P tunnel may keep replica auth empty; raw TCP / loopback exposure should still use a token. See **§4.9** in [MANUAL_EN.md](MANUAL_EN.md) / [MANUAL_RU.md](MANUAL_RU.md).

## Debian `.deb` from release zip

See [`../packaging/debian/README.md`](../packaging/debian/README.md). GitHub Actions workflow `.github/workflows/release-deb.yml` can attach a `.deb` when a release is published (or run manually with a tag).

## Maintainer packaging (brew, winget, AUR, Fedora)

Templates and checksum workflow: [`../packaging/README.md`](../packaging/README.md). TUI-only packages use [`../packaging/winget-tui/`](../packaging/winget-tui/), [`../packaging/homebrew/Casks/i2pchat-tui.rb`](../packaging/homebrew/Casks/i2pchat-tui.rb), [`../packaging/aur/i2pchat-tui-bin/`](../packaging/aur/i2pchat-tui-bin/), and [`../packaging/flatpak/`](../packaging/flatpak/).
