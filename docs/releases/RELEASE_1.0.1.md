# I2PChat v1.0.1 — Profile switch race fixes & notification menu simplification

## EN

### Summary

`v1.0.1` is a **patch** release on the stable **1.0.x** line. It fixes GUI races when switching profiles (**⋯ → Load profile (.dat)**) so **Saved peers**, the **locked peer** field, and related UI stay consistent with the loaded `.dat`. It also **simplifies the notification section** of the **⋯** menu: **Privacy mode** and **Notification sound** only (hide-body and quiet-while-focused behaviour are controlled solely via Privacy mode).

### User-visible changes

- **Load profile (.dat) / `switch_profile`**
  - Contact book for the new profile is loaded **before** `init_session()` starts `accept_loop`, so inbound callbacks during long initialization no longer save the **previous** profile’s contacts into the **new** profile’s `{name}.contacts.json`.
  - After switching, the **peer address field** is explicitly synced from the new profile (`.dat` lock / contact book), fixing stale lock indicators and list selection.
  - Chat history state is flushed and the message list cleared when switching profiles to avoid mixing sessions.
  - The Saved peers list is rebuilt more reliably (`takeItem` cleanup, viewport refresh, deferred refresh on the next event-loop tick).
  - Failures during profile switch surface a **critical dialog** instead of failing silently in a background task.
  - **Note:** If an older build already wrote a wrong `{profile}.contacts.json`, delete or restore that file once; this release prevents new corruption.

- **⋯ notifications**
  - **Hide message in notifications** and **Quiet mode (focused)** are removed as separate menu entries; both map to **Privacy mode: ON/OFF**. **Notification sound** stays a separate toggle.
  - If `gui.json` had hide or quiet enabled without `privacy_mode_enabled`, the first launch with `v1.0.1` **enables Privacy mode** so behaviour is preserved.

### Validation

Same expectations as `v1.0.0`: run the project’s **unittest** and **pytest** suites on a developer host before tagging (see [RELEASE_1.0.0.md](RELEASE_1.0.0.md) for the usual commands and counts).

### Compatibility

Fully compatible with **`v1.0.0`** profiles, history, and backup bundles. No protocol or on-disk format changes beyond normal contact/history files.

---

## RU

### Кратко

`v1.0.1` — **патч** на стабильной линии **1.0.x**. Исправлены гонки GUI при смене профиля (**⋯ → Load profile (.dat)**): **Saved peers**, поле **залоченного пира** и связанный UI соответствуют загруженному `.dat`. Упрощено меню уведомлений в **⋯**: остаются только **Privacy mode** и **Notification sound** (скрытие текста в тостах и «тихий» режим при фокусе окна — только через Privacy mode).

### Изменения для пользователя

- **Load profile (.dat) / `switch_profile`**
  - Книга контактов нового профиля загружается **до** `init_session()`, пока не запущен `accept_loop`, чтобы колбэки во время долгой инициализации не записали **чужие** контакты в `{имя}.contacts.json` нового профиля.
  - После смены **поле адреса пира** явно синхронизируется с новым профилем (lock в `.dat` / contact book).
  - При смене профиля сбрасывается состояние истории и очищается лента сообщений.
  - Список Saved peers пересобирается надёжнее (`takeItem`, отложенное обновление).
  - Ошибки смены профиля показываются **диалогом**, а не теряются в фоновой задаче.
  - **Замечание:** если старая сборка уже испортила `{профиль}.contacts.json`, один раз удалите или восстановите файл; новая логика не создаёт такую порчу заново.

- **⋯ уведомления**
  - Пункты **Hide message** и **Quiet mode** убраны; поведение задаётся **Privacy mode**. **Звук** — отдельный переключатель.
  - Миграция: если в `gui.json` были hide/quiet без `privacy_mode_enabled`, при первом запуске **включается Privacy mode**.

### Совместимость

Совместимо с **`v1.0.0`**: профили, история и backup без изменений формата протокола.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.0.1.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.0.1.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.0.1.zip` | Unzip → open I2PChat.app |
