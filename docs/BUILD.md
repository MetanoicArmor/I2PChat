# Building and releasing I2PChat

Python **3.12+** is supported (**3.14+** recommended for release-style builds). Dependencies are managed with **[uv](https://docs.astral.sh/uv/)** (`pyproject.toml` + **`uv.lock`**). **I2P SAM** is implemented in **`i2pchat.sam`**; PyPI **`i2plib`** is not used.

## Release build scripts

| Target | Command | Output (typical) |
|--------|---------|------------------|
| Linux (AppImage + zip) | `./build-linux.sh` | `I2PChat.AppImage`, **`I2PChat-linux-<arch>-v<version>.zip`** (GUI, AppImage inside), **`I2PChat-linux-<arch>-tui-v<version>.zip`** (только TUI) — **в корне репо**; в **`dist/`** — AppImage и onedir; `<arch>` — **`x86_64`** или **`aarch64`** |
| Linux aarch64 via Docker | `./packaging/docker/build-linux-aarch64.sh` | Same as above with **`aarch64`** in names; Ubuntu **24.04** arm64 image — see [`packaging/docker/README.md`](../packaging/docker/README.md); bundled `i2pd` is **optional** for portable builds |
| macOS (.app + zip) | `./build-macos.sh` | `dist/I2PChat.app`, `I2PChat-macOS-<arch>-v<version>.zip`, **`I2PChat-macos-<arch>-tui-v<version>.zip`** |
| Windows | `.\build-windows.ps1` | `dist\I2PChat\I2PChat.exe`, **`I2PChat-windows-x64-v<version>.zip`**, **`I2PChat-windows-tui-x64-v<version>.zip`**, plus **`I2PChat-windows-x64-winget-v<version>.zip`** / **`I2PChat-windows-tui-x64-winget-v<version>.zip`** (same trees **without** embedded i2pd — for winget / Microsoft validation) |

**Linux glibc baseline:** for release zips that run on common LTS distros, prefer building on **Ubuntu 22.04** (or use CI). Workflow **[`build-linux-release-artifacts.yml`](../.github/workflows/build-linux-release-artifacts.yml)** (`workflow_dispatch`) runs two jobs in parallel: **`build`** on **ubuntu-22.04** (x86_64) uploads `I2PChat-linux-x86_64-*.zip` + `SHA256SUMS`; **`build-aarch64`** on **ubuntu-22.04-arm** uploads `I2PChat-linux-aarch64-*.zip` + **`SHA256SUMS.linux-aarch64`** (separate file so it does not overwrite the amd64 checksums). Bundled `i2pd` may be injected locally for portable artifacts, but it is not required to live in the git tree. Inputs: **`tag`**, optional **`source_ref`**. If the checked-out ref has **no `I2PChat-tui.spec`**, the workflow **shallow-fetches `origin/main`** when **`VERSION` on `main` matches** the release. Local builds on bleeding-edge distros can embed a **newer minimum glibc** than users on Debian/Ubuntu LTS have.

**Optional Docker:** **`./packaging/docker/run-linux-build.sh`** — Ubuntu **24.04** on **amd64** (`Dockerfile.linux-noble-glibc239`, glibc **2.39**); newer baseline than 22.04. **`./packaging/docker/build-linux-aarch64.sh`** — Ubuntu **24.04** on **linux/arm64** for **`I2PChat-linux-aarch64-*`** zips in the **repo root**; by default the **GUI zip** is **portable** (binaries + `_internal` at archive root). Set **`I2PCHAT_LINUX_GUI_ZIP_MODE=appimage`** for the single-AppImage zip used on GitHub Releases. Details: [`packaging/docker/README.md`](../packaging/docker/README.md).

**Linux script** uses **uv** (`.venv`, `uv sync --frozen --group build`) and PyInstaller **`I2PChat.spec`** (GUI + TUI exe sharing one Qt onedir), `appimagetool`; the AppImage includes `usr/bin/I2PChat` and **`usr/bin/I2PChat-tui`**, plus a TUI `.desktop` with `Terminal=true`. After that it runs **`I2PChat-tui.spec`** → `dist/I2PChat-tui/` (no Qt) and packs **`I2PChat-linux-*-tui-*.zip`** from that tree.

**Optional bundled router staging:** portable builds can embed `i2pd` if local files are staged under `vendor/i2pd/`. Build scripts now auto-try [`scripts/ensure_bundled_i2pd.sh`](../scripts/ensure_bundled_i2pd.sh), which resolves in this order:

1. already staged `vendor/i2pd/`
2. `I2PCHAT_BUNDLED_I2PD_SOURCE_DIR`
3. sibling repo `../i2pchat-bundled-i2pd`
4. **Git clone** into `.cache/bundled-i2pd-source/`: default **`https://github.com/MetanoicArmor/i2pchat-bundled-i2pd.git`** (`I2PCHAT_BUNDLED_I2PD_GIT_URL` overrides; empty URL or **`I2PCHAT_SKIP_BUNDLED_I2PD_GIT=1`** skips this step). The URL must be **cloneable without a prompt** in your environment (public repo, or SSH URL with keys, or cached credentials); otherwise `ensure_bundled_i2pd.sh` logs `NOT FOUND` and portable builds ship **without** embedded `i2pd`. **`build-linux.sh`** prints an extra **WARN** when the expected `vendor/i2pd/…/i2pd` file is still missing.

**Linux dynamic `i2pd`:** if the router binary is linked against Boost/OpenSSL (not fully static), ship the matching **`*.so*`** files in the same `vendor/i2pd/linux-*/` directory; PyInstaller packs them like Windows `*.dll`, and the app prepends that directory to **`LD_LIBRARY_PATH`** when starting i2pd (so AppImage `_internal` does not shadow Boost). On **Arch/CachyOS** (and similar), upstream i2pd may ask for **`libboost_program_options.so.1.89.0`** while the system only ships **1.90** — after placing `i2pd`, run **[`scripts/stage_i2pd_linux_shlibs.sh`](../scripts/stage_i2pd_linux_shlibs.sh)** to copy Boost from `/usr/lib` and add version symlinks so **`uv run`** and AppImage builds work.

For predictable builds, prefer setting **`I2PCHAT_BUNDLED_I2PD_SOURCE_DIR`** explicitly. The sibling-repo path is only a local convenience fallback. For manual staging or URL-based fetching, use [`scripts/fetch_bundled_i2pd.sh`](../scripts/fetch_bundled_i2pd.sh). The staged files are untracked and are not required for Debian/Ubuntu packaging.

**Raw binary URLs:** `I2PCHAT_I2PD_*_URL` variables in `fetch_bundled_i2pd.sh` must point to a **single** `i2pd` / `i2pd.exe` file. Upstream [PurpleI2P/i2pd releases](https://github.com/PurpleI2P/i2pd/releases) ship `.deb`/`.rpm`/archives — extract the `i2pd` executable into a directory and run `fetch_bundled_i2pd.sh --from` that directory (or clone **[i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)** / rely on `ensure_bundled_i2pd.sh`).

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
- `I2PCHAT_GPG_KEY_ID=<keyid>` — select signing key;
- `I2PCHAT_GPG_BATCH=0|1` — override `gpg --batch`: omitted when stdin or stdout is a TTY (so pinentry can prompt; works with `| tee`); forced when neither is a TTY (CI). Use `I2PCHAT_GPG_BATCH=1` with gpg-agent if you need batch in a terminal.

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
nix profile install github:MetanoicArmor/I2PChat   # installs i2pchat + i2pchat-tui wrappers and desktop files
nix develop github:MetanoicArmor/I2PChat   # dev shell
```

The flake packages the source tree directly and wraps the GUI with Qt plugins that commonly matter on NixOS desktops: Wayland/platform plugins, Qt Multimedia, SVG/image format support, plus `notify-send` / Linux sound helpers on `PATH`.

The dev shell now includes `uv` and the same Qt runtime pieces as the package, so `python -m i2pchat.gui` and `python -m i2pchat.tui` behave closer to `nix run`.

`keyring` is included in the Nix Python environment, but native Secret Service storage still depends on a running provider (for example `gnome-keyring` or KeepassXC Secret Service). If none is available, I2PChat falls back to file-backed storage automatically.

## BlindBox (daemon / ops)

[`i2pchat/blindbox/blindbox_server_example.py`](../i2pchat/blindbox/blindbox_server_example.py) is the hardened **example** service; the production-oriented entrypoint is `python -m i2pchat.blindbox.daemon`.

The repo ships `systemd` units, env templates, `install.sh`, and fail2ban assets under [`i2pchat/blindbox/daemon/`](../i2pchat/blindbox/daemon/) and [`i2pchat/blindbox/fail2ban/`](../i2pchat/blindbox/fail2ban/).

Public replicas behind an I2P tunnel may keep replica auth empty; raw TCP / loopback exposure should still use a token. See **§4.9** in [MANUAL_EN.md](MANUAL_EN.md) / [MANUAL_RU.md](MANUAL_RU.md).

## Debian `.deb` from release zip

See [`../packaging/debian/README.md`](../packaging/debian/README.md) and [`../packaging/fedora/README.md`](../packaging/fedora/README.md). GitHub Actions workflow `.github/workflows/release-linux-pkgs.yml` attaches **`.deb`** (GUI + TUI) when a release is published (or run manually with a tag).

## Maintainer packaging (brew, winget, AUR, Fedora)

Templates and checksum workflow: [`../packaging/README.md`](../packaging/README.md). TUI-only packages use [`../packaging/winget-tui/`](../packaging/winget-tui/), [`../packaging/homebrew/Casks/i2pchat-tui.rb`](../packaging/homebrew/Casks/i2pchat-tui.rb), [`../packaging/aur/i2pchat-tui-bin/`](../packaging/aur/i2pchat-tui-bin/), and [`../packaging/flatpak/`](../packaging/flatpak/).
