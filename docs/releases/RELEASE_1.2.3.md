# I2PChat v1.2.3 — Status bar, startup chat, identity line styling

Patch after **v1.2.2**: **short local `.b32` in the status bar** (Qt + TUI), **Qt chat no longer wipes** bootstrap lines when there is **no peer** after tunnels come up (so **“Online! My Address”** stays visible for `random_address` / new profiles), and **“Identity saved …”** uses the **system** transcript line instead of a green success bubble.

## EN

### Summary

- **Status (Qt + TUI):** `My:` plus shortened local destination in the main status presentation (`build_status_presentation`).
- **Qt GUI:** `_refresh_offline_history_display` clears the list on empty peer only when an **offline history injection** block is present; avoids wiping **session / Online** lines after `start_core` / profile switch.
- **Core:** identity persistence confirmations go through **`_emit_system`** so the GUI shows them like other **SYSTEM** lines, not **OK** bubbles.

### Compatibility

Protocol and encrypted history format **unchanged**.

### Validation

`python -m pytest tests/test_status_presentation.py -q`

## RU

### Кратко

- **Строка статуса (Qt и TUI):** в презентации статуса добавлен сокращённый **локальный** адрес (`My:`).
- **Qt:** при пустом пире лента **не очищается** целиком после готовности туннелей — сохраняются системные строки и **«Online! My Address»** для транзиентного / нового профиля.
- **Ядро:** «Identity saved …» выводится как **системная** строка, не как зелёный бабл **OK**.

### Совместимость

Протокол и формат истории **без изменений**.

### Проверка

См. **Validation** в английской части.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.3.zip` | Unzip → `I2PChat.exe` (GUI) or `I2PChat-tui.exe` (console TUI) |
| Linux | `I2PChat-linux-x86_64-v1.2.3.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.3.zip` | Unzip → open I2PChat.app |
