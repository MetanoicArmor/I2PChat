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
  Cross‑platform GUI (PyQt6) on top of a shared asynchronous core.
</p>

---

### Language / Язык

[![English manual](https://img.shields.io/badge/📖%20Manual-EN-blue.svg)](docs/MANUAL_EN.md)
[![Русский мануал](https://img.shields.io/badge/📖%20Мануал-RU-red.svg)](docs/MANUAL_RU.md)

**Credits / upstream project:**  
I2PChat is based on the original **`termchat-i2p-python`** by **Stanley** from the I2P community: `http://git.community.i2p/stan/termchat-i2p-python`.  

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
- **PyQt6 GUI** with dark theme
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

Other platforms are available — see the table below or check [Releases](https://github.com/MetanoicArmor/I2PChat/releases).

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

#### GUI client (PyQt6)

```bash
python main_qt.py
```

### Cross‑platform builds

The project is intentionally **cross‑platform** and ships with helper scripts for the main targets.  
Everywhere, the recommended/runtime version is **Python 3.14+** (the repo includes an updated copy of `i2plib` compatible with modern asyncio).

#### Linux (GUI AppImage)

```bash
./build-linux.sh
```

This script:

- Uses `python3.14` (or default `python3`) and `.venv314`.
- Builds a self‑contained GUI binary via PyInstaller.
- Packs it into `I2PChat-x86_64.AppImage` using `appimagetool`.

#### macOS (GUI .app bundle)

```bash
./build-macos.sh
```

- Uses Python 3.14+ (from PATH or Homebrew).
- Builds `dist/I2PChat.app` via PyInstaller.

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

I2PChat — это кроссплатформенный чат‑клиент поверх анонимной сети [I2P](https://geti2p.net), работающий через SAM‑интерфейс.  
Графический интерфейс на PyQt6 с тёмной темой.

Проект основан на оригинальном `termchat-i2p-python` от Stanley (I2P community):  
`http://git.community.i2p/stan/termchat-i2p-python`.

### Возможности

- обмен сообщениями через I2P SAM (через `i2plib`);
- кроссплатформенный GUI (Windows, macOS, Linux);
- передача файлов между участниками;
- отправка изображений в виде ASCII / braille арта.

### Готовые сборки

**Установка Python не требуется** — всё уже собрано и готово к запуску.

| Платформа | Скачать | Запуск |
|-----------|---------|--------|
| **Windows** | [I2PChat-windows-x64.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-windows-x64.zip) | Распаковать → `I2PChat.exe` |
| **macOS** | [I2PChat-macOS.app.zip](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-macOS.app.zip) | Распаковать → `I2PChat.app` |
| **Linux** | [I2PChat-x86_64.AppImage](https://github.com/MetanoicArmor/I2PChat/releases/latest/download/I2PChat-x86_64.AppImage) | `chmod +x` → запустить |

> **Требование:** I2P‑роутер должен быть запущен с включённым SAM API (порт 7656 по умолчанию).

### Поддержать проект

Если хотите «купить мне кофе», можно отправить донат в BTC на адрес  
`bc1q3sq35ym2a90ndpqe35ujuzktjrjnr9mz55j8hd` (QR‑код см. выше).

