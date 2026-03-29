# I2PChat v0.6.5 — UX polish milestone (drafts, unread, status, notifications, message actions)

## EN

### Summary

`v0.6.5` delivers the **0.6.5 roadmap milestone**: everyday Qt UX improvements without protocol changes—per-contact compose drafts, unread indicators, clearer connection and delivery language, a richer message context menu, and configurable notifications. It also fixes BlindBox offline text sending, bumps the shipped version to **0.6.5**, and hardens CI so tests do not import the full Qt stack where unnecessary.

### User-visible changes

- **Per-contact compose drafts**
  - Separate draft text per peer/address; restored when switching conversations.
  - Persisted under the profiles directory (`*.compose_drafts.json`) with debounced saves.
  - Pure switch logic in `compose_drafts.py` with unit tests.

- **Unread message indicators**
  - Per-peer unread counts when a message arrives for a non-active conversation.
  - Cleared when that peer’s dialog is opened (history load path).
  - Global hint: window title suffix `(N)` and tray tooltip when supported.
  - Logic in `unread_counters.py` with tests.

- **Simplified connection and delivery status**
  - Main status line uses short, user-oriented phrases (e.g. **Online**, **Disconnected**, **Sending…**, **Will deliver later**) via `status_presentation.py`.
  - Technical detail remains in tooltips / extended lines where applicable.

- **Message context actions**
  - **Reply** inserts a markdown-style quoted block at the end of the input (`reply_format.py`).
  - **Copy text** / **Copy with timestamp** unchanged in spirit; extended for attachment-like rows.
  - **Open** / **Copy path** for inline images and successful file receives; `ChatItem.saved_file_path` stores the absolute path when the received file exists on disk.

- **Notification preferences** (`ui_prefs.json`)
  - Toggle **notification sound** on/off (independent of custom sound path / env).
  - **Hide message body** in tray toasts (generic body; title may still name the peer).
  - **Quiet mode** while the app window is focused: no tray toast and no sound (all chats).
  - Pure policy helpers in `notification_prefs.py` with unit tests.

- **Packaging / docs**
  - `VERSION` set to **0.6.5**; `README` prebuilt download filenames updated; roadmap headers aligned.

### Core fix

- **BlindBox offline text:** serialize the offline text send path correctly (`fix(core): serialize BlindBox offline text send`).

### Developer / CI

- **`send_retry_policy.py`:** `should_start_auto_connect_retry` moved out of `main_qt.py` so `tests/test_send_text_routing.py` does not import PyQt6 on headless Linux (fixes missing `libEGL.so.1` in GitHub Actions pytest).

### Tests (high level)

- `tests/test_compose_drafts.py`, `tests/test_unread_counters.py`, `tests/test_reply_format.py`, `tests/test_notification_prefs.py`, `tests/test_gui_unread_smoke.py`, updated `tests/test_send_text_routing.py`, plus existing suites for core/history/protocol.

### Compatibility

Patch release on the **0.6.x** line. No intentional breaking change to the normal online chat framing/handshake for compatible peers.

### Repository layout

- Release notes are consolidated under **`docs/releases/`** (this file and siblings). Older `RELEASE_*.md` paths at repo root were moved here; update bookmarks accordingly.

---

## RU

### Кратко

`v0.6.5` закрывает **веху 0.6.5 из roadmap**: повседневный UX в Qt без смены протокола — черновики по контактам, непрочитанные, понятные статусы соединения и доставки, контекстное меню сообщений и настройки уведомлений. Исправлена сериализация офлайн-текста через BlindBox, версия продукта **0.6.5**, CI не тянет PyQt там, где это не нужно.

### Что видит пользователь

- **Черновики по контактам** — отдельный текст на peer, восстановление при переключении, запись на диск (`*.compose_drafts.json`), логика в `compose_drafts.py`.

- **Непрочитанные** — счётчик по peer, сброс при открытии диалога, отображение в заголовке окна и tooltip трея (`unread_counters.py`).

- **Упрощённые статусы** — короткие формулировки в строке статуса (**Online**, **Disconnected**, **Sending…**, **Will deliver later** и т.д.) через `status_presentation.py`; детали в подсказках.

- **Контекстное меню сообщений** — **Reply** с цитатой в поле ввода (`reply_format.py`); для картинок и успешного приёма файла — **Open** / **Copy path**; у `ChatItem` поле `saved_file_path` для принятого файла.

- **Настройки уведомлений** — звук вкл/выкл, скрытие текста в тосте, тихий режим при фокусе окна; prefs в `ui_prefs.json`, логика в `notification_prefs.py`.

- **Версия и сборки** — `VERSION` **0.6.5**, обновлены ссылки на архивы в `README`, строки версии в roadmap.

### Исправление в ядре

- **Офлайн-текст BlindBox** — корректная сериализация пути отправки.

### Разработка / CI

- **`send_retry_policy.py`** — политика авто-retry Connect вынесена из `main_qt.py`, чтобы pytest на Linux в CI не импортировал PyQt6 (ошибка `libEGL.so.1`).

### Тесты

- Новые и обновлённые тесты для черновиков, непрочитанных, reply, notification prefs, smoke GUI (где есть Qt), маршрутизации send/offline.

### Совместимость

Patch в линейке **0.6.x**; протокол обычного online-чата для совместимых пиров намеренно не ломался.

### Структура репозитория

- Заметки к релизам собраны в **`docs/releases/`**; старые `RELEASE_*.md` из корня перенесены сюда.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v0.6.5.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v0.6.5.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v0.6.5.zip` | Unzip → open I2PChat.app |
