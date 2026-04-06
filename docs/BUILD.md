# Building and releasing I2PChat

Python **3.12+** is supported (**3.14+** recommended for release-style builds). Dependencies are managed with **[uv](https://docs.astral.sh/uv/)** (`pyproject.toml` + **`uv.lock`**). **I2P SAM** lives in **`i2pchat.sam`**; PyPI **`i2plib`** is not a runtime dependency (optional **`vendor/i2plib`** may remain for audits or tooling).

## Release build scripts

| Target | Command | Output (typical) |
|--------|---------|------------------|
| Linux (AppImage + zip) | `./build-linux.sh` | `I2PChat.AppImage`, **`I2PChat-linux-<arch>-v<version>.zip`** (GUI, AppImage inside), **`I2PChat-linux-<arch>-tui-v<version>.zip`** (только TUI) — **в корне репо**; в **`dist/`** — AppImage и onedir; `<arch>` — **`x86_64`** или **`aarch64`** |
| Linux aarch64 via Docker | `./packaging/docker/build-linux-aarch64.sh` | Same as above with **`aarch64`** in names; Ubuntu **24.04** arm64 image — see [`packaging/docker/README.md`](../packaging/docker/README.md); requires **`vendor/i2pd/linux-aarch64/i2pd`** |
| macOS (.app + zip) | `./build-macos.sh` | `dist/I2PChat.app`, `I2PChat-macOS-<arch>-v<version>.zip`, **`I2PChat-macos-<arch>-tui-v<version>.zip`** |
| Windows | `.\build-windows.ps1` | `dist\I2PChat\I2PChat.exe`, **`I2PChat-windows-x64-v<version>.zip`**, **`I2PChat-windows-tui-x64-v<version>.zip`**, plus **`I2PChat-windows-x64-winget-v<version>.zip`** / **`I2PChat-windows-tui-x64-winget-v<version>.zip`** (same trees **without** embedded i2pd — for winget / Microsoft validation) |

**Linux glibc baseline:** for release zips that run on common LTS distros, prefer building on **Ubuntu 22.04** (or use CI). Workflow **[`build-linux-release-artifacts.yml`](../.github/workflows/build-linux-release-artifacts.yml)** (`workflow_dispatch`) runs two jobs in parallel: **`build`** on **ubuntu-22.04** (x86_64) uploads `I2PChat-linux-x86_64-*.zip` + `SHA256SUMS`; **`build-aarch64`** on **ubuntu-22.04-arm** uploads `I2PChat-linux-aarch64-*.zip` + **`SHA256SUMS.linux-aarch64`** (separate file so it does not overwrite the amd64 checksums). The repo must include **`vendor/i2pd/linux-aarch64/i2pd`** or the aarch64 job fails. Inputs: **`tag`**, optional **`source_ref`**. If the checked-out ref has **no `I2PChat-tui.spec`**, the workflow **shallow-fetches `origin/main`** when **`VERSION` on `main` matches** the release. Local builds on bleeding-edge distros can embed a **newer minimum glibc** than users on Debian/Ubuntu LTS have.

**Optional Docker:** **`./packaging/docker/run-linux-build.sh`** — Ubuntu **24.04** on **amd64** (`Dockerfile.linux-noble-glibc239`, glibc **2.39**); newer baseline than 22.04. **`./packaging/docker/build-linux-aarch64.sh`** — Ubuntu **24.04** on **linux/arm64** for **`I2PChat-linux-aarch64-*`** artifacts. Details: [`packaging/docker/README.md`](../packaging/docker/README.md).

**Linux script** uses **uv** (`.venv`, `uv sync --frozen --group build`) and PyInstaller **`I2PChat.spec`** (GUI + TUI exe sharing one Qt onedir), `appimagetool`; the AppImage includes `usr/bin/I2PChat` and **`usr/bin/I2PChat-tui`**, plus a TUI `.desktop` with `Terminal=true`. After that it runs **`I2PChat-tui.spec`** → `dist/I2PChat-tui/` (no Qt) and packs **`I2PChat-linux-*-tui-*.zip`** from that tree.

**macOS** builds `dist/I2PChat.app` from **`I2PChat.spec`** (GUI + in-bundle TUI entrypoint sharing Qt), then **`I2PChat-tui.spec`** for the standalone **`I2PChat-macos-*-tui-*.zip`**.

**Windows** runs both specs twice: first **with** bundled i2pd (default zips), then with **`I2PCHAT_OMIT_BUNDLED_I2PD=1`** for the **`*-winget-*`** zips (PyInstaller omits i2pd binaries — AV “riskware” scans on `winget-pkgs`). The **`-tui`** zip is built from **`dist\I2PChat-tui\`**. Safer one-off PowerShell:

```powershell
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope Process RemoteSigned; .\build-windows.ps1"
```

## Release signing and checksums

Release build scripts generate:

- `SHA256SUMS` for the main GUI zip, TUI zip, and on Windows the two **`*-winget-*`** zips (four lines on Windows);
- detached armored GPG signature `SHA256SUMS.asc` (best-effort by default).

These files are **not** tracked in git; upload them **with the release assets** on GitHub.

Environment:

- `I2PCHAT_SKIP_GPG_SIGN=1` — skip detached signature;
- `I2PCHAT_REQUIRE_GPG=1` — fail if GPG signing is unavailable or fails;
- `I2PCHAT_GPG_KEY_ID=<keyid>` — select signing key.

**Official release builds** should use `I2PCHAT_REQUIRE_GPG=1` so unsigned archives are not produced silently.

**Linux-only checksum refresh** (after replacing `I2PChat-linux-*-v*.zip` on a release): [`../packaging/refresh-linux-sha256sums.sh`](../packaging/refresh-linux-sha256sums.sh) writes `dist/SHA256SUMS` in the same format as `build-linux.sh`, then upload with `gh release upload`.

Verification:

```bash
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

**Release signing key** (detached signatures on `SHA256SUMS`):

| | |
|---|---|
| **Fingerprint** | `2BA0C56D8240077F9773248A2C05CFB3F6DFDF99` |
| **UID** | `Vade <metanoicarmor@gmail.com>` |
| **Key directory** | [keys.openpgp.org](https://keys.openpgp.org/search?q=metanoicarmor%40gmail.com) |

Fetch before first verify:

```bash
gpg --keyserver keys.openpgp.org --recv-keys 2BA0C56D8240077F9773248A2C05CFB3F6DFDF99
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

See [`../packaging/debian/README.md`](../packaging/debian/README.md) and [`../packaging/fedora/README.md`](../packaging/fedora/README.md). GitHub Actions workflow `.github/workflows/release-linux-pkgs.yml` attaches **`.deb`** (GUI + TUI) when a release is published (or run manually with a tag).

## Maintainer packaging (brew, winget, AUR, Fedora)

Templates and checksum workflow: [`../packaging/README.md`](../packaging/README.md). TUI-only packages use [`../packaging/winget-tui/`](../packaging/winget-tui/), [`../packaging/homebrew/Casks/i2pchat-tui.rb`](../packaging/homebrew/Casks/i2pchat-tui.rb), [`../packaging/aur/i2pchat-tui-bin/`](../packaging/aur/i2pchat-tui-bin/), and [`../packaging/flatpak/`](../packaging/flatpak/).
