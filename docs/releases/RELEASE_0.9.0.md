# I2PChat v0.9.0 — Portability, privacy, hardening

## EN

### Summary

`v0.9.0` closes the **0.9.0 roadmap milestone** ([backlog §19–26](../ISSUE_BACKLOG_RU.md)): **encrypted profile backup**, **encrypted history export/import**, **history retention** (policy + apply-all with confirmation), **privacy mode** (notifications suppressed, optional PIN to exit), **drag-and-drop** files/images onto the chat list, **automatic transfer retry** with backoff for retryable failures, clearer **transfer error** strings, and continued **protocol/transfer tests**.

### User-visible changes

- **Profile & history backup (issues 19–21)** — ⋯ menu: export/import profile; export/import chat history (existing flow; settings live in `ui_prefs.json` alongside other GUI prefs).
- **History retention (issue 22)** — ⋯ → **History retention…**: optional max age (days), max messages per peer; **Apply to all chats…** runs pruning after confirmation. Ongoing saves and history loads apply the policy without extra prompts.
- **Privacy mode (issue 23)** — ⋯ → **Privacy mode** toggle; **Privacy lock (PIN)…** to require a PIN when turning privacy off. When active (with default *hide notifications*), tray toasts and sounds for new activity are suppressed and notification body text is hidden via `notification_prefs` integration.
- **Drag-and-drop (issue 24)** — Drop a single file or image onto the chat message list to send (same constraints as Send file / Send picture; 100 MB cap; image types validated).
- **Transfer retry & UX (issue 25)** — `FileTransferInfo.failure_reason` from the core drives user-facing messages and `transfer_retry.should_retry_transfer` schedules bounded automatic resends for retryable outbound failures.
- **Hardening (issue 26)** — Framing/damaged-data tests under `tests/test_protocol_hardening.py` (and related suites).

### Developer / modules

- [`profile_export.py`](../../profile_export.py), [`history_export.py`](../../history_export.py)
- [`history_retention.py`](../../history_retention.py), [`privacy_mode.py`](../../privacy_mode.py)
- [`drag_drop.py`](../../drag_drop.py), [`transfer_retry.py`](../../transfer_retry.py)
- [`main_qt.py`](../../main_qt.py), [`i2p_chat_core.py`](../../i2p_chat_core.py) (`failure_reason` on transfers)

### Compatibility

Minor feature release on the path to **1.0.0**. Encrypted history on disk may be rewritten when retention pruning applies on load or flush. Upgrade peers together for smoothest file/image transfer behavior.

---

## RU

### Кратко

`v0.9.0` закрывает веху **0.9.0** из roadmap: **бэкап профиля** и **истории**, **политика хранения истории** с подтверждением массового применения, **режим приватности** с опциональным PIN, **перетаскивание файлов/картинок** в список чата, **автоповтор отправки** вложений с backoff для части ошибок, более понятные **сообщения об ошибках передачи**, дополнительные **тесты протокола**.

### Что видит пользователь

Пункты ⋯: экспорт/импорт профиля и истории; **History retention…**; переключатель **Privacy mode** и **Privacy lock (PIN)…**; перенос файла или изображения в область списка сообщений.

### Совместимость

Релиз на пути к **1.0.0**; при применении retention файлы истории на диске могут обновляться при загрузке или сохранении.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v0.9.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v0.9.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v0.9.0.zip` | Unzip → open I2PChat.app |
