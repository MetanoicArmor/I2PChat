# Building and releasing I2PChat

The **release clients are C++20** (`cpp/`). Python remains for golden vectors and interop tests until cutover.

Developer builds: [`../cpp/README.md`](../cpp/README.md) and the **Building and running from source** section of [`../README.md`](../README.md).

## Release build scripts

| Target | Command | Output (typical) |
|--------|---------|------------------|
| Linux (AppImage + zip) | `./build-linux.sh` | `I2PChat.AppImage`, **`I2PChat-linux-<arch>-v<version>.zip`** (GUI, AppImage inside by default), **`I2PChat-linux-<arch>-tui-v<version>.zip`** — in the **repo root**; **`dist/`** holds the AppImage and the C++ onedir; `<arch>` is **`x86_64`** or **`aarch64`** |
| Linux aarch64 via Docker | `./packaging/docker/build-linux-aarch64.sh` | Same names with **`aarch64`**; image still needs CMake/Qt/FTXUI instead of uv/PyInstaller — see [`packaging/docker/README.md`](../packaging/docker/README.md) |
| macOS (.app + zip) | `./build-macos.sh` | `dist/I2PChat.app`, `I2PChat-macOS-<arch>-v<version>.zip`, **`I2PChat-macos-<arch>-tui-v<version>.zip`** |
| Windows | `.\build-windows.ps1` | `dist\I2PChat\I2PChat.exe`, **`I2PChat-windows-x64-v<version>.zip`**, **`I2PChat-windows-tui-x64-v<version>.zip`**, plus **`*-winget-*`** zips **without** embedded i2pd |

All three scripts call [`scripts/build_cpp_binaries.sh`](../scripts/build_cpp_binaries.sh) (or equivalent `cmake` on Windows): **`-DI2PCHAT_BUILD_GUI=ON -DI2PCHAT_BUILD_TUI=ON -DI2PCHAT_BUILD_TESTS=OFF`**.

**Linux glibc baseline:** prefer **Ubuntu 22.04** (or CI) so the AppImage/onedir runs on common LTS distros. Workflow **[`build-linux-release-artifacts.yml`](../.github/workflows/build-linux-release-artifacts.yml)** should install CMake, Qt 6, Boost, libsodium, nlohmann-json, FTXUI (or vcpkg) rather than uv.

**Optional Docker:** update images to compile `cpp/` with the same `./build-linux.sh` entrypoint. **`I2PCHAT_LINUX_GUI_ZIP_MODE=appimage`** (default) vs **`portable`**.

**Linux script** no longer uses uv/PyInstaller. It stages ELF deps with `ldd`, copies Qt `platforms` plugins when `qtpaths6` is available, then **`appimagetool`** (pinned SHA-256). TUI zip is `i2pchat-tui` + `usr/bin/I2PChat-tui` + `usr/lib`.

**Bundled router version:** portable payloads still track i2pd **2.61.0** via [i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd) (Linux from Ubuntu noble → Boost **1.83** SONAME).

**Optional bundled router staging:** portable builds can embed `i2pd` if local files are staged under `vendor/i2pd/`. Build scripts now auto-try [`scripts/ensure_bundled_i2pd.sh`](../scripts/ensure_bundled_i2pd.sh), which resolves in this order:

1. already staged `vendor/i2pd/`
2. `I2PCHAT_BUNDLED_I2PD_SOURCE_DIR`
3. sibling repo `../i2pchat-bundled-i2pd`
4. **Git clone** into `.cache/bundled-i2pd-source/`: default **`https://github.com/MetanoicArmor/i2pchat-bundled-i2pd.git`** (`I2PCHAT_BUNDLED_I2PD_GIT_URL` overrides; empty URL or **`I2PCHAT_SKIP_BUNDLED_I2PD_GIT=1`** skips this step). The URL must be **cloneable without a prompt** in your environment (public repo, or SSH URL with keys, or cached credentials); otherwise `ensure_bundled_i2pd.sh` logs `NOT FOUND` and portable builds ship **without** embedded `i2pd`. **`build-linux.sh`** prints an extra **WARN** when the expected `vendor/i2pd/…/i2pd` file is still missing.

**Linux dynamic `i2pd`:** if the router binary is linked against Boost/OpenSSL (not fully static), ship the matching **`*.so*`** files in the same `vendor/i2pd/linux-*/` directory. **`build-linux.sh`** copies that tree next to the C++ binaries (`usr/bin/vendor/...` in the AppImage). Run **[`scripts/stage_i2pd_linux_shlibs.sh`](../scripts/stage_i2pd_linux_shlibs.sh)** to copy the exact DT_NEEDED libs from `/usr/lib`; if staging still fails, **`build-linux.sh`** falls back to the system `i2pd` from `PATH`.

For predictable builds, prefer setting **`I2PCHAT_BUNDLED_I2PD_SOURCE_DIR`** explicitly. The sibling-repo path is only a local convenience fallback. For manual staging or URL-based fetching, use [`scripts/fetch_bundled_i2pd.sh`](../scripts/fetch_bundled_i2pd.sh). The staged files are untracked and are not required for Debian/Ubuntu packaging.

**Raw binary URLs:** `I2PCHAT_I2PD_*_URL` variables in `fetch_bundled_i2pd.sh` must point to a **single** `i2pd` / `i2pd.exe` file. Upstream [PurpleI2P/i2pd releases](https://github.com/PurpleI2P/i2pd/releases) ship `.deb`/`.rpm`/archives — extract the `i2pd` executable into a directory and run `fetch_bundled_i2pd.sh --from` that directory (or clone **[i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)** / rely on `ensure_bundled_i2pd.sh`).

**macOS** builds `dist/I2PChat.app` from the CMake **`i2pchat-gui`** / **`i2pchat-tui`** binaries, then **`macdeployqt`**. The bundle is always **ad-hoc codesigned** (nested Qt dylibs after `macdeployqt` otherwise fail with `CODESIGNING / Invalid Page` on modern macOS). Set **`I2PCHAT_CODESIGN_IDENTITY`** for Developer ID + hardened runtime.

**Windows** CMake-builds once, copies **`I2PChat.exe`** / **`I2PChat-tui.exe`**, runs **`windeployqt`**, packs full zips **with** bundled i2pd, then **`*-winget-*`** zips with `vendor\i2pd` removed (AV “riskware” scans on `winget-pkgs`). Safer one-off PowerShell:

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

## Release tag helper

Use [`../scripts/release-tag.sh`](../scripts/release-tag.sh) to cut a signed annotated release tag from the current `HEAD`:

```bash
./scripts/release-tag.sh v1.3.2
./scripts/release-tag.sh v1.3.2 --push
```

The helper refuses to tag an unsigned `HEAD`, refuses to overwrite existing local or remote tags, and only pushes when `--push` is passed explicitly. This is the preferred path for new releases because moving a published tag changes GitHub source archives and can desynchronize packaging metadata from the original source snapshot.
