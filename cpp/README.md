# I2PChat — C++ client

A C++20 rewrite of I2PChat, developed alongside the Python implementation until
it reaches parity. It is wire-compatible with released Python 1.4.x clients and
reads the same on-disk profiles, contacts, history and BlindBox state.

The compatibility contract is written down in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) and enforced by golden vectors
generated from the Python implementation.

## Layout

| Path | Contents |
|---|---|
| `core/` | `libi2pchat_core` — protocol, crypto, SAM, storage, sessions. No UI dependency. |
| `apps/tui/` | Terminal client (FTXUI). |
| `apps/gui/` | Desktop client (Qt 6 Widgets). |
| `tests/` | Catch2 suite, including golden-vector replays and a fake SAM router. |
| `tools/` | `interop_peer` and `interop_storage`, driven by the Python-side interop tests. |
| `testdata/` | Vector generator and the generated fixtures. |
| `docs/` | Compatibility specification. |

## Dependencies

- CMake ≥ 3.24 and a C++20 compiler
- libsodium
- Boost ≥ 1.81 (Asio, Process)
- nlohmann/json ≥ 3.10
- Catch2 3 (tests), FTXUI (TUI), Qt 6 (GUI)

Install them through the system package manager or let vcpkg resolve
[`vcpkg.json`](vcpkg.json).

```bash
# macOS
brew install cmake libsodium boost nlohmann-json catch2 ftxui qt

# Debian / Ubuntu
sudo apt install cmake g++ libsodium-dev libboost-all-dev \
                 nlohmann-json3-dev catch2 qt6-base-dev
```

## Building

```bash
cmake --preset debug
cmake --build --preset debug
ctest --preset debug
```

Presets are defined in [CMakePresets.json](CMakePresets.json): `debug`,
`release`, `asan` (AddressSanitizer + UBSan) and `vcpkg-release`. The GUI is off
by default; enable it with `-DI2PCHAT_BUILD_GUI=ON` or use the `release` preset.
The TUI is on by default when FTXUI is installed.

```bash
# Terminal client, same profiles as the Python app
./build/apps/tui/i2pchat-tui --profile default --help

# Desktop client (needs -DI2PCHAT_BUILD_GUI=ON)
./build/apps/gui/i2pchat-gui --profile default
```

Inside the TUI: slash commands (`/help`, `/connect`, `/contacts`, …), F1–F8 for
screens, and a modal TOFU prompt on first sighting or a key change.

Packaging notes for AppImage / Debian / notarization live in
[packaging/README.md](packaging/README.md). The switch from Python as the
default client is the checklist in [docs/CUTOVER.md](docs/CUTOVER.md).

## Golden vectors

The fixtures in `testdata/vectors/` are the compatibility contract. Regenerate
them from the Python implementation with:

```bash
../.venv/bin/python testdata/generate_vectors.py
```

Generation is deterministic — the CSPRNG is replaced by a fixed keystream — so a
diff in `vectors/` always means the Python behaviour changed rather than that
the generator produced fresh randomness. `tests/test_cpp_golden_vectors.py` in
the Python suite guards against accidental drift.

## Cross-implementation tests

`tools/interop_peer` speaks the wire protocol over plain TCP so that the Python
suite can drive it with the production `i2pchat.crypto` and
`i2pchat.protocol.protocol_codec` modules:

```bash
cmake --build cpp/build            # builds tools/interop_peer
.venv/bin/python -m pytest tests/test_cpp_interop.py
```

These tests cover both roles — Python dialling C++ and C++ dialling Python —
and every byte-level contract a live run would exercise: the identity preface,
the HS3-labelled signed transcript, HS4 key derivation, directional keys,
FINISHED confirmation, framing, the sequence counter, the MAC over the header
fields, and the `I2PPAD1` padding envelope. They also assert that the C++ peer
refuses replays, tampered MACs, plaintext after the handshake, data before it,
and forged signatures.

`tools/interop_storage` does the same for the profile on disk. Each subcommand
reads or writes one sealed format, so the Python suite can write a file with the
production storage modules and have the C++ side open it, and the other way
round:

```bash
.venv/bin/python -m pytest tests/test_cpp_storage_interop.py
```

That covers the contact book, compose drafts, chat history, group records and the
identity `.dat` with its wrap-key sidecar — including the file names, which are
digests of the peer address or group id and so fail silently rather than loudly
if the two implementations disagree.

What they deliberately do not cover is SAM and the router itself, so a manual
interop run against a real i2pd stays on the release checklist.

## Testing philosophy

Tests that consume golden vectors are interoperability tests wearing unit-test
clothing. When one fails, the C++ client would not be able to talk to a released
peer or open an existing profile; treat it as a release blocker rather than a
flaky assertion.
