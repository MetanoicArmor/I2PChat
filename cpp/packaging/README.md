# Packaging the C++ client

Python packaging (`packaging/` at the repo root) stays as-is until cutover.
This directory describes how the C++ binaries reuse that machinery.

## What is reused unchanged

- `scripts/ensure_bundled_i2pd.sh` and the staged i2pd trees
- AppDir layout and pinned `appimagetool` SHA
- `SHA256SUMS` + GPG detached signatures, same artifact names with a `-cpp`
  suffix during the beta period (`i2pchat-1.4.1-linux-x64-cpp.AppImage`)
- Downstream consumers (apt, AUR, winget, Homebrew, Flatpak, Fedora): only
  checksums change

## What is replaced

| Python | C++ |
|---|---|
| PyInstaller `.spec`, uv | CMake install of `i2pchat-gui`, `i2pchat-tui`, `i2pchat-blindbox-daemon` |
| Debian `python3-i2pchat` | `i2pchat` depending on `libqt6widgets6`, `libsodium23`, `libboost-system` |
| `flake.nix` pythonEnv | a derivation that builds `cpp/` with nixpkgs boost/qt/libsodium |

## macOS signing

The Python app was unsigned. The C++ `.app` is signed with hardened runtime
and notarized:

```bash
codesign --force --options runtime --deep --sign "Developer ID Application: …" \
  I2PChat.app
xcrun notarytool submit I2PChat.dmg --wait --keychain-profile i2pchat
xcrun stapler staple I2PChat.dmg
```

The Keychain wrap-key ACL is bound to this signature. A first launch after
replacing the Python binary will prompt; the `.dat.wrap` sidecar remains the
fallback so a user is never locked out.

## Debian source sketch

`control`:

```
Package: i2pchat
Depends: libqt6widgets6, libsodium23, libboost-system1.83.0
Recommends: i2pd
```

`i2pchat-tui` is a separate binary package without Qt, so a headless host can
install just the terminal client and the BlindBox daemon.

## Local release build

```bash
cmake --preset vcpkg-release
cmake --build --preset vcpkg-release
cmake --install build/vcpkg-release --prefix dist/
```

Then feed `dist/` to the existing AppImage / dmg / zip scripts, swapping the
PyInstaller payload for these three binaries.
