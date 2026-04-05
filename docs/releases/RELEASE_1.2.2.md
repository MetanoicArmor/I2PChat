# I2PChat v1.2.2 — Entrypoints, TUI polish, offline history UX

Patch after **v1.2.1**: **documented package-style launch commands** for Qt and Textual, a **short TUI module path**, **TUI fixes**, and **Qt chat history** visible **before** a live peer session without wiping the startup status lines.

## EN

### Summary

**v1.2.2** improves discoverability and first-run clarity:

- **Qt GUI** can be started as **`python -m i2pchat.gui`** (same code as **`python -m i2pchat.gui.main_qt`** / **`python -m i2pchat.run_gui`**). Optional CLI args: **profile name**, then optional **theme** (`ligth` / `night`), e.g. `./.venv314/bin/python -m i2pchat.gui win`.
- **Terminal UI (Textual)** has a short entrypoint: **`python -m i2pchat.tui`** [profile]. The legacy path **`python -m i2pchat.gui.chat_python`** remains equivalent.
- **Qt:** saved per-peer history loads when you pick a peer or finish editing the address **while disconnected**, not only after “Secure channel with PFS established”. The first history injection **keeps** the early **system** bootstrap lines (profile / keyring / stored contact / session start) instead of clearing the whole feed.
- **TUI:** **`/copyaddr`** copies the full **`… .b32.i2p`** address; **`post` / `post_panel`** tolerate a missing chat widget without crashing; regression tests added in **`tests/test_tui_router_defaults.py`**.
- **Docs:** **`README.md`**, **`docs/MANUAL_EN.md`**, **`docs/MANUAL_RU.md`**, **`docs/CODEBASE_MAP.md`**, and **`flake.nix`** updated for the new commands where relevant.

### User-visible changes

#### 1. Recommended `python -m` commands (from repo root, with venv Python)

| Client | Example |
|--------|---------|
| Qt GUI | `./.venv314/bin/python -m i2pchat.gui` or `… -m i2pchat.gui myprofile` |
| Same Qt (explicit module) | `… -m i2pchat.gui.main_qt` |
| PyInstaller-aligned launcher | `… -m i2pchat.run_gui` |
| Textual TUI | `… -m i2pchat.tui` or `… -m i2pchat.tui myprofile` |
| TUI (legacy path) | `… -m i2pchat.gui.chat_python` |

On **Windows** (PowerShell), replace `./.venv314/bin/python` with `.\.venv314\Scripts\python`.

#### 2. Qt: offline history + smoother startup feed

- History (when enabled) appears for the **selected** peer **without** requiring an active secure session first.
- The **initial system lines** from session setup are **not** removed the first time history is merged into the transcript.

#### 3. TUI quality-of-life

- Clipboard copy of local address includes the **`.b32.i2p`** suffix.
- Safer logging when the Rich log is not ready.

#### 4. Windows prebuilt zip: second console executable

- The **Windows x64** archive includes **`I2PChat-tui.exe`** next to **`I2PChat.exe`** (same `I2PChat\` folder). The TUI build uses **`console=True`** so Textual works in **cmd** / **PowerShell**. Optional profile: `I2PChat-tui.exe myprofile`.

### Technical

- **Qt:** `ChatWindow._refresh_offline_history_display`, `_chat_contains_injected_history_block`, `_try_load_history` peer key normalization; call sites (contacts, address field, connect, `start_core`, profile switch, router restart paths, history import/toggle).
- **TUI:** `i2pchat/gui/chat_python.py` — `post` / `post_panel` guards, `/copyaddr` suffix.
- **Launcher:** `i2pchat/tui.py` — thin delegate to `I2PChat().run()`.
- **PyInstaller (Windows):** `i2pchat/run_tui.py` + shared `COLLECT` with deduplicated `binaries`/`datas` (`normalize_toc`); no `MERGE` (avoids onedir → onefile extraction overhead).

### Compatibility

- **Wire protocol** and on-disk history encryption format unchanged.
- **CLI / module paths** are additive; old invocations still work.

### Validation

- `python3 -m py_compile i2pchat/gui/main_qt.py i2pchat/tui.py i2pchat/run_tui.py i2pchat/gui/chat_python.py`
- `pytest tests/test_tui_router_defaults.py` (with dev deps / Textual installed)

---

## RU

### Кратко

**v1.2.2** после **v1.2.1**:

- **Qt:** запуск пакетом **`python -m i2pchat.gui`** [профиль] [тема] — удобная форма вместо длинного пути к модулю; эквивалентно **`i2pchat.gui.main_qt`** и **`i2pchat.run_gui`**.
- **TUI:** короткая команда **`python -m i2pchat.tui`** [профиль]; старый путь **`python -m i2pchat.gui.chat_python`** по смыслу тот же.
- **Qt:** сохранённая история чата подгружается **до** соединения с пиром (выбор контакта / адрес / Connect); при **первой** вставке истории **не** стираются системные строки старта (профиль, keyring, контакт, «Starting I2P session…»).
- **TUI:** **`/copyaddr`** копирует полный адрес с **`.b32.i2p`**; устойчивость **`post`** при отсутствии виджета лога; тесты в **`tests/test_tui_router_defaults.py`**.
- **Документация** обновлена под новые точки входа.
- **Windows zip:** в папке **`I2PChat\`** — **`I2PChat-tui.exe`** (консольный Textual TUI) рядом с **`I2PChat.exe`**; запуск из cmd/PowerShell, профиль: `I2PChat-tui.exe имя`.

### Примеры из корня репозитория

```bash
./.venv314/bin/python -m i2pchat.gui
./.venv314/bin/python -m i2pchat.gui win
./.venv314/bin/python -m i2pchat.tui
./.venv314/bin/python -m i2pchat.tui win
```

### Совместимость

Протокол и формат зашифрованной истории **без изменений**. Старые команды запуска **сохраняются**.

### Проверка

См. блок **Validation** в английской части.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.2.zip` | Unzip → `I2PChat.exe` (GUI) or `I2PChat-tui.exe` (console TUI) |
| Linux | `I2PChat-linux-x86_64-v1.2.2.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.2.zip` | Unzip → open I2PChat.app |
