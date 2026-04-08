# I2PChat v1.2.5 — GUI survives failed I2P session

Patch after **v1.2.4**: if **`init_session`** fails (for example **SAM** unreachable or **connection refused**), the GUI leaves **`self.core = None`** and **`handle_error`** refreshes the UI. Earlier code assumed **`self.core`** was always present and raised **`AttributeError`**, which could **close the window** instead of showing the error state.

## EN

### Summary

- **Status bar:** **`refresh_status_label`** shows a consistent offline/error presentation when **`self.core`** is missing (no **`stored_peer`** / **`conn`** access).
- **Connection and send controls:** **`_refresh_connection_buttons`** and **`_refresh_send_controls`** disable **Connect** / **Disconnect** / **Send** with safe tooltips when the core failed to initialize.
- **Send gating:** **`_peer_target_available`** and **`_send_action_allowed`** return **False** without a live core.
- **Peer lock indicator:** **`_update_peer_lock_indicator`** uses persisted data and compose input only when **`self.core`** is **None**, avoiding core-only calls.

### Compatibility

Wire protocol and encrypted history format **unchanged**. This release is **GUI behavior only** when the router/session is unavailable at startup.

### Validation

```bash
python -m pytest tests/test_history_ui_guards.py -q
```

## RU

### Кратко

- **Строка статуса:** при отсутствии ядра **`refresh_status_label`** показывает офлайн/ошибку без обращения к **`stored_peer`** и **`conn`**.
- **Кнопки и отправка:** при **`self.core is None`** отключаются **Connect** / **Disconnect** / **Send** с безопасными подсказками.
- **Отправка:** **`_peer_target_available`** и **`_send_action_allowed`** запрещают отправку без живого ядра.
- **Индикатор блокировки пира:** при **`core is None`** используются только persist и поле ввода, без вызовов **`self.core.*`**.

### Совместимость

Протокол приложения и формат зашифрованной истории **без изменений**. Изменения касаются только **поведения GUI**, когда при старте недоступен роутер или сессия.

### Проверка

См. блок **Validation** в английской части.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.5.zip` | Unzip → `I2PChat.exe` (GUI) or `I2PChat-tui.exe` (console TUI) |
| Linux | `I2PChat-linux-x86_64-v1.2.5.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.5.zip` | Unzip → open I2PChat.app |
