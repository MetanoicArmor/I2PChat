# Cutover: C++ becomes the default client

The Python tree keeps shipping until the C++ client has demonstrated parity in
beta. This is the checklist, not a date.

## Beta (both clients in a release)

- Publish `i2pchat-*-cpp` artifacts next to the Python ones.
- Profiles stay shared: Application Support / `%APPDATA%` / `~/.i2pchat`.
- Interop gate in CI: `tests/test_cpp_interop.py` and
  `tests/test_cpp_storage_interop.py` plus the Catch2 golden vectors.
- Manual: C++ initiator ↔ Python 1.4.x responder through a real i2pd, and the
  other way around; file send; BlindBox collect after a disconnect; group
  invite accept.

## Promotion

When those gates hold for a full minor release:

1. CMake/C++ becomes the default build in README, Homebrew, winget, apt.
2. Python sources move to `legacy/` (or an `archive/python-1.4` branch) and
   receive security fixes only.
3. Debian package `python3-i2pchat` is replaced by `i2pchat`; the transitional
   package depends on the new one for one stable cycle.
4. PyInstaller specs and `uv` lockfiles leave the default CI path.

## What must not change at cutover

- Protocol v4 / HS4
- Sealed file magics and HKDF domains
- SAM option names and destination `.dat` layout
- BlindBox replica line protocol and `audit.log` shape for fail2ban

A user who never opens a terminal should be able to install the C++ build over
the Python one and keep talking to every peer they already have.
