# I2PChat v1.2.4 — Internal SAM layer, uv toolchain

Patch after **v1.2.3**: the project now uses an **in-repository SAM implementation** (**`i2pchat.sam`**) instead of PyPI **`i2plib`** / vendored copies, standardizes **developer installs on [uv](https://docs.astral.sh/uv/)** (`pyproject.toml` + **`uv.lock`**), and tightens **BlindBox** compatibility with **i2pd** variants that omit **`RESULT=OK`** on some **SESSION** replies.

## EN

### Summary

- **SAM:** I2P control traffic (HELLO, SESSION, STREAM, NAMING, dest lookup) is implemented in **`i2pchat.sam`**. **PyPI `i2plib`** is not a dependency; the old **`vendor/i2plib`** tree was removed.
- **Developers:** use **uv** to sync and run (`uv sync`, `uv run python -m i2pchat.gui` / `i2pchat.tui`). Lockfile **`uv.lock`** tracks exact dependency versions.
- **BlindBox:** protocol parsing and **`blindbox_client`** tolerate i2pd-style **SESSION** lines without **`RESULT=OK`** where appropriate.

### Compatibility

Wire protocol and encrypted history format **unchanged** (SAM is the path to the I2P router, not the app-to-app framing).

### Validation

```bash
python -m pytest tests/test_sam_protocol.py tests/test_sam_backend.py tests/test_sam_input_validation.py tests/test_sam_destination.py tests/test_blindbox_client.py -q
```

## RU

### Кратко

- **SAM:** управление I2P (HELLO, SESSION, STREAM, NAMING, lookup) — в пакете **`i2pchat.sam`**. **PyPI `i2plib`** не используется, вендорный **`vendor/i2plib`** удалён.
- **Разработка:** установка и запуск через **uv** (`uv sync`, `uv run python -m i2pchat.gui` / `i2pchat.tui`), версии зафиксированы в **`uv.lock`**.
- **BlindBox:** разбор ответов и клиент учитывают варианты **i2pd**, где в **SESSION** нет строки **`RESULT=OK`**.

### Совместимость

Протокол приложения и формат зашифрованной истории **без изменений**.

### Проверка

См. блок **Validation** в английской части.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.4.zip` | Unzip → `I2PChat.exe` (GUI) or `I2PChat-tui.exe` (console TUI) |
| Linux | `I2PChat-linux-x86_64-v1.2.4.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.4.zip` | Unzip → open I2PChat.app |
