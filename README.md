<p align="center">
  <img src="image.png" alt="I2PChat Logo" width="280" />
</p>

<h1 align="center">I2PChat</h1>

<p align="center">
  <a href="https://github.com/MetanoicArmor/I2PChat/releases"><img src="https://img.shields.io/github/v/release/MetanoicArmor/I2PChat?label=release" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/MetanoicArmor/I2PChat" alt="License"></a>
  <a href="requirements.txt"><img src="https://img.shields.io/badge/Python-3.14+-blue.svg" alt="Python"></a>
  <a href="https://geti2p.net"><img src="https://img.shields.io/badge/I2P-SAM%20API-purple.svg" alt="I2P"></a>
</p>

<p align="center">
  <b>Experimental peer‑to‑peer chat client for the <a href="https://geti2p.net">I2P</a> anonymity network.</b><br>
  Terminal UI (TUI) and graphical UI (PyQt6) on top of a shared asynchronous core.
</p>

---

### Language / Язык

[![English manual](https://img.shields.io/badge/📖%20Manual-EN-blue.svg)](docs/MANUAL_EN.md)
[![Русский мануал](https://img.shields.io/badge/📖%20Мануал-RU-red.svg)](docs/MANUAL_RU.md)

**Credits / upstream project:**  
I2PChat is a **separate GUI client** that reuses ideas and parts of the logic from the original terminal client **`termchat-i2p-python`** by **Stanley** from the I2P community.  
Stanley’s original project is available here: `http://git.community.i2p/stan/termchat-i2p-python`.  
The initial TUI concept, I2P protocol integration and a significant portion of the core logic come from his work; this repository extends it with a GUI client, an additional TUI, and cross‑platform build and packaging scripts.

---

### Table of contents

- [Features](#features)
- [Manuals](#manuals)
- [Screenshots](#screenshots)
- [Prebuilt binaries](#prebuilt-binaries)
- [Running from source](#running-from-source)
- [Cross‑platform builds](#crossplatform-builds)
- [License](#license)
- [Buy me a coffee](#buy-me-a-coffee)

### Features

- **End‑to‑end communication over I2P SAM** (via `i2plib`)
- **Two frontends**:
  - Terminal UI (`chat-python.py`)
  - PyQt6 GUI (`main_qt.py`)
- **File transfer** between peers
- **ASCII / braille image rendering** for sending images over text channels
- Cross‑platform build scripts (Linux, macOS, Windows)

#### Manuals

- **English manual**: [**docs/MANUAL_EN.md**](docs/MANUAL_EN.md)
- **Русский мануал**: [**docs/MANUAL_RU.md**](docs/MANUAL_RU.md)

### Screenshots

<img src="screenshots/2.png" alt="I2PChat macOS GUI – profile & notifications" width="900" />

### Prebuilt binaries

Releases are published on GitHub under the **Releases** section.

Currently available:

- **Windows (x64) GUI**
  - Archive: `I2PChat-windows-x64.zip`
  - Inside: `I2PChat\I2PChat.exe`
  - Built with **Python 3.14** and PyInstaller, includes the Python runtime and all dependencies.
  - **Python is *not* required on the target system** – just unpack the zip and run `I2PChat.exe`.

Other platforms (Linux TUI / Linux AppImage / macOS TUI app) are supported by helper scripts in the repo (see below) and can be added to releases as needed.

### Running from source

Requirements:

- Python **3.14+** (recommended; this is what the bundled `i2plib` copy and current builds are tested with)
- An I2P router with **SAM** enabled (default port `7656`)

Create and activate a virtual environment, then install dependencies:

```bash
python3.14 -m venv .venv
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
Everywhere, the recommended/runtime version is **Python 3.14+** (the repo includes an updated copy of `i2plib` compatible with modern asyncio).

#### Linux (TUI binary)

```bash
./build-linux.sh
```

This:

- Uses `python3.14` (or default `python3`) and virtualenv `.venv314`.
- Builds a single‑file TUI binary: `dist/termchat-i2p-python`.

#### Linux (GUI AppImage)

```bash
./build_appimage.sh
```

This script:

- Uses `python3.14` (or default `python3`) and `.venv314`.
- Builds a self‑contained GUI binary via PyInstaller.
- Packs it into `I2PChat-x86_64.AppImage` using `appimagetool`.

#### macOS (TUI app bundle)

```bash
./build-macos-app.sh
```

- Uses Python 3.14+ (from PATH or Homebrew).
- Builds `dist/termchat-i2p-python` via PyInstaller.
- Then you wrap it into a `.app` bundle using **Platypus** (steps are printed by the script).

### Windows build (GUI)

For reproducible Windows builds there is a PowerShell script:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

It will:

1. Create a fresh virtual environment `.venv314` using **Python 3.14** via `py -3.14 -m venv`.
2. Install all dependencies from `requirements.txt` plus `pyinstaller`.
3. Build a GUI‑only PyQt6 binary:
   - Output folder: `dist\I2PChat\`
   - Main executable: `dist\I2PChat\I2PChat.exe`

The resulting `I2PChat.exe` is self‑contained and can be distributed to machines without Python installed.

### License

See `LICENSE` for full license text.  
Please also respect the original `termchat-i2p-python` licensing and attribution to **Stanley (I2P community)**.

### Buy me a coffee

If you like this project and want to support development, you can send a small donation in Bitcoin:

- **BTC address**: `bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd`

<img src="btc_donation_qr.png" alt="Bitcoin donation QR" width="220" />

---

## I2PChat (RU)

Ниже — краткая информация о проекте на русском языке.

### О проекте

I2PChat — это пиринговый чат‑клиент поверх анонимной сети [I2P](https://geti2p.net), работающий через SAM‑интерфейс.  
Внутри есть общее асинхронное ядро (`i2p_chat_core.py`) и два интерфейса:

- терминальный TUI (`chat-python.py`, Textual/Rich);
- графический клиент на PyQt6 (`main_qt.py`).

Проект основан на оригинальном терминальном клиенте `termchat-i2p-python` от Stanley (I2P community):  
`http://git.community.i2p/stan/termchat-i2p-python`.

### Возможности

- обмен сообщениями через I2P SAM (через `i2plib`);
- TUI и GUI‑клиенты;
- передача файлов между участниками;
- отправка изображений в виде ASCII / braille арта;
- скрипты сборки под Linux, macOS и Windows.

### Как запустить

1. Установить Python 3.14+ и запустить I2P‑роутер c включённым SAM (`127.0.0.1:7656`).
2. В корне репозитория:

```bash
python3.14 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Запуск TUI:

```bash
python chat-python.py
```

4. Запуск GUI:

```bash
python main_qt.py
```

### Сборки

Для удобной упаковки есть скрипты:

- `./build-linux.sh` — TUI‑бинарь для Linux;
- `./build_appimage.sh` — GUI AppImage для Linux;
- `./build-macos-app.sh` — TUI‑приложение для macOS;
- `build-windows.ps1` — GUI‑сборка для Windows (PyInstaller + Python 3.14).

### Поддержать проект

Если хотите «купить мне кофе», можно отправить донат в BTC на адрес  
`bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd` (QR‑код см. выше).

