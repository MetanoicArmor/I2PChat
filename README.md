## I2PChat

I2PChat is an experimental peer‑to‑peer chat client for the [I2P](https://geti2p.net) anonymity network.  
It provides both a terminal UI (TUI) and a graphical UI (PyQt6) on top of a shared asynchronous core.

**Credits / upstream project:**  
I2PChat is a **separate GUI client** that reuses ideas and parts of the logic from the original terminal client **`termchat-i2p-python`** by **Stanley** from the I2P community.  
Stanley’s original project is available here: [`http://git.community.i2p/stan/termchat-i2p-python`](http://git.community.i2p/stan/termchat-i2p-python).  
The initial TUI concept, I2P protocol integration and a significant portion of the core logic come from his work; this repository extends it with a GUI client, an additional TUI, and cross‑platform build and packaging scripts.

### Features

- **End‑to‑end communication over I2P SAM** (via `i2plib`)
- **Two frontends**:
  - Terminal UI (`chat-python.py`)
  - PyQt6 GUI (`main_qt.py`)
- **File transfer** between peers
- **ASCII / braille image rendering** for sending images over text channels
- Cross‑platform build scripts (Linux, macOS, Windows)

### GUI screenshot

![I2PChat macOS GUI](screenshots/1.png)

### Prebuilt binaries

Releases are published on GitHub under the **Releases** section.

Currently available:

- **Windows (x64) GUI**
  - Archive: `I2PChat-windows-x64.zip`
  - Inside: `I2PChat\I2PChat.exe`
  - Built with **Python 3.9** and PyInstaller, includes the Python runtime and all dependencies.
  - **Python is *not* required on the target system** – just unpack the zip and run `I2PChat.exe`.

Other platforms (Linux TUI / Linux AppImage / macOS TUI app) are supported by helper scripts in the repo (see below) and can be added to releases as needed.

### Running from source

Requirements:

- Python **3.9** (recommended; this is what `i2plib` and the original client were developed and tested with)
- An I2P router with **SAM** enabled (default port `7656`)

Create and activate a virtual environment, then install dependencies:

```bash
python3.9 -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### TUI client

```bash
python chat-python.py
```

#### GUI client (PyQt6)

```bash
python main_qt.py
```

### Cross‑platform builds

The project is intentionally **cross‑platform** and ships with helper scripts for the main targets.  
Everywhere, the recommended/runtime version is **Python 3.9** (because of `i2plib` compatibility).

#### Linux (TUI binary)

```bash
./build-linux.sh
```

This:

- Uses `python3.9` and virtualenv `.venv39`.
- Builds a single‑file TUI binary: `dist/termchat-i2p-python`.

#### Linux (GUI AppImage)

```bash
./build_appimage.sh
```

This script:

- Uses `python3.9` and `.venv39`.
- Builds a self‑contained GUI binary via PyInstaller.
- Packs it into `I2PChat-x86_64.AppImage` using `appimagetool`.

#### macOS (TUI app bundle)

```bash
./build-macos-app.sh
```

This script:

- Uses `python3.9` (from PATH or Homebrew).
- Builds `dist/termchat-i2p-python` via PyInstaller.
- Then you wrap it into a `.app` bundle using **Platypus** (steps are printed by the script).

### Windows build (GUI)

For reproducible Windows builds there is a PowerShell script:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

It will:

1. Create a fresh virtual environment `.venv39` using **Python 3.9** via `py -3.9 -m venv`.
2. Install all dependencies from `requirements.txt` plus `pyinstaller`.
3. Build a GUI‑only PyQt6 binary:
   - Output folder: `dist\I2PChat\`
   - Main executable: `dist\I2PChat\I2PChat.exe`

The resulting `I2PChat.exe` is self‑contained and can be distributed to machines without Python installed.

### License

See `LICENSE` for full license text.  
Please also respect the original `termchat-i2p-python` licensing and attribution to **Stanley (I2P community)**.

