# I2PChat v0.7.0 — Contacts, conversations, search, trust card

## EN

### Summary

`v0.7.0` completes the **0.7.0 roadmap milestone**: a conversation-oriented workflow on top of the existing single-session I2P chat model. Contacts are stored in **`{profile}.contacts.json` v2** (with automatic migration from v1). There is no protocol change for online chat framing.

### User-visible changes

- **Saved peers sidebar**
  - Two-line rows: title (display name or short address) with unread hint, plus **last message preview** and **last activity** time when known.
  - **Context menu**: *Edit name & note…* (local-only labels), *Contact details…* (trust, copy address, remove pin), *Remove from saved peers…* (optional: encrypted history, TOFU pin, profile lock, BlindBox state file).
  - **More (⋯) menu**: *Forget pinned peer key* for the peer implied by the address field / session (same trust store as contact details).

- **Contact book (v2 file)**
  - Fields per peer: `display_name`, `note`, `last_preview`, `last_activity_ts`, plus MRU ordering.
  - **`last_active_peer`**: restored on startup and when switching profiles (if that peer is still in the saved list).

- **Search in current chat**
  - Field above the message list with **◀ / ▶** to jump between matches in loaded chat rows (text, sender, timestamp).

- **Contact details / trust MVP**
  - Shows full address, **pinned** state (TOFU signing key), short **fingerprint**, truncated hex key; **Remove pin…** with confirmation.

- **Polish shipped with 0.7.0**
  - **Profile switch** reloads the contact book and merges a **locked** peer into Saved peers when needed (sidebar no longer stale across profiles).
  - **Saved peers** context menu uses the same **rounded** popup style as the **⋯** menu; **Edit name / Contact details** dialogs use a dedicated theme sheet (readable text on macOS light window chrome).
  - **Default** open width for the Saved peers strip: narrower (about **¼** of the splitter, capped) until you resize it.

### Developer / modules

- **`contact_book.py`**: load/save, v1→v2 migration, `touch_peer_message_meta`, `remove_peer`, tests `tests/test_contact_book.py`.
- **`i2p_chat_core.I2PChatCore`**: `get_peer_trust_info`, `clear_locked_peer` (UI remove-with-lock); tests `tests/test_peer_trust_info.py`, `tests/test_clear_locked_peer.py`.

### Compatibility

Minor release on the **0.7.x** line. Existing **v1** `*.contacts.json` is upgraded on first save. Chat history encryption and protocol are unchanged.

### Repository layout

- Release notes live under **`docs/releases/`**.

---

## RU

### Кратко

`v0.7.0` закрывает **веху 0.7.0 roadmap**: работа с **сохранёнными пирами** в рамках профиля, превью и время в списке, **последний активный диалог** при старте, **поиск по текущему чату**, **карточка контакта** (TOFU pin / fingerprint). Файл контактов **`{profile}.contacts.json` версии 2** с миграцией с v1. Протокол online-чата не менялся.

### Что видит пользователь

- Боковая панель **Saved peers**: две строки на контакт, превью и время, непрочитанные; ПКМ — правка имени/заметки, карточка контакта (адрес, TOFU, снятие pin), удаление из списка с опциями (история, pin, lock, BlindBox).
- Восстановление **last_active_peer** при запуске и смене профиля.
- Поле **поиска** над лентой чата с переходом по совпадениям.
- Диалог **Contact details** из меню «⋯» или контекста списка: pin, отпечаток, снятие pin.

- **Дополнительно в составе 0.7.0**
  - При **смене профиля** перезагружается книга контактов и при необходимости в список подмешивается **залоченный** пир (нет «пустого» Saved peers после switch).
  - ПКМ по Saved peers — тот же **скруглённый** popup, что у меню **⋯**; диалоги правки имени и деталей контакта с отдельной темой (читаемый текст на macOS).
  - **Дефолтная** ширина открытой панели Saved peers — уже (≈¼ сплиттера, с верхним пределом), пока не меняли вручную.

### Разработка

- **`contact_book.py`**, тесты **`tests/test_contact_book.py`**; в ядре **`get_peer_trust_info`**, **`clear_locked_peer`**, тесты **`tests/test_peer_trust_info.py`**, **`tests/test_clear_locked_peer.py`**.

### Совместимость

Линейка **0.7.x**; старый формат контактов автоматически поднимается до v2 при сохранении.

### Структура репозитория

- Описания релизов в **`docs/releases/`**.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v0.7.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v0.7.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v0.7.0.zip` | Unzip → open I2PChat.app |
