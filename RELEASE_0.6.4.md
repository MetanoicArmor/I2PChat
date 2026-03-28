# I2PChat v0.6.4 — Encrypted chat history (per-peer) with optional UI toggle

## EN

### Summary

`v0.6.4` introduces encrypted per-peer chat history for the Qt app, with secure local persistence, a user-facing ON/OFF toggle in the actions menu, and clear-history control for the current peer. This release also fixes session-state edge cases so history from different peers is not mixed.

### What changed

- **Encrypted per-peer history storage:** added `chat_history.py` with a dedicated binary format:
  - header: `I2CH` magic + version + 32-byte salt
  - payload: NaCl SecretBox ciphertext (JSON messages)
- **Key derivation from identity key:** history keys are derived via HKDF from the I2P identity private key:
  - profile key (per profile)
  - file key (per peer, salted)
- **Atomic writes for history files:** history writes use `atomic_write_bytes` (temp file + fsync + replace + mode), reducing corruption risk on crash/power loss.
- **UI controls in `main_qt.py`:**
  - `Chat history: ON/OFF` toggle in `ActionsPopup`
  - `Clear history` action for the current peer
- **Persistent history prefs:** added `history_enabled` and `history_max_messages` support in `ui_prefs.json` (default enabled, default max 1000).
- **Runtime integration:**
  - load history after secure channel establishment
  - periodic flush (60s) when dirty
  - save on disconnect and on window close
- **Identity access API:** added `get_identity_key_bytes()` and `get_profiles_dir()` in `i2p_chat_core.py` for history layer integration.
- **Peer isolation and OFF-mode hardening:**
  - fixed potential cross-peer mixing by keeping a per-session peer history buffer
  - OFF mode is strict: no new history capture/saves while disabled
  - disconnect now resets loaded-peer/session history state

### Tests

- Added `tests/test_chat_history.py` covering:
  - encryption/decryption round-trip
  - corrupted/invalid files and wrong key behavior
  - FIFO truncation / max-messages behavior
  - atomic write failure safety
  - per-peer isolation behavior
- Added `tests/test_history_ui_guards.py` covering:
  - ON/OFF capture guard presence
  - OFF-state reset guards
  - disconnect reset guard presence

### Compatibility

Patch release for `v0.6.x`. No protocol/frame changes for normal online chat exchange.

---

## RU

### Кратко

`v0.6.4` добавляет зашифрованную историю чата по каждому peer для Qt-версии, с безопасной локальной записью, переключателем ON/OFF в меню действий и очисткой истории текущего peer. Также исправлены edge-case’ы состояния сессии, чтобы история разных peer не смешивалась.

### Что изменилось

- **Зашифрованная per-peer история:** добавлен модуль `chat_history.py` с отдельным бинарным форматом:
  - заголовок: `I2CH` + версия + 32-байтный salt
  - полезная нагрузка: ciphertext NaCl SecretBox (JSON сообщений)
- **Деривация ключа из identity-ключа:** ключи истории выводятся через HKDF из приватного ключа I2P:
  - profile key (на профиль)
  - file key (на peer, с salt)
- **Атомарная запись файлов истории:** используется `atomic_write_bytes` (temp + fsync + replace + mode), что снижает риск порчи при crash/power loss.
- **Новые элементы UI в `main_qt.py`:**
  - переключатель `Chat history: ON/OFF` в `ActionsPopup`
  - действие `Clear history` для текущего peer
- **Персистентные настройки истории:** в `ui_prefs.json` добавлена поддержка `history_enabled` и `history_max_messages` (по умолчанию включено, лимит 1000).
- **Интеграция в runtime:**
  - загрузка истории после установления secure channel
  - периодический flush (60с) при наличии изменений
  - сохранение при disconnect и закрытии окна
- **API доступа к identity:** в `i2p_chat_core.py` добавлены `get_identity_key_bytes()` и `get_profiles_dir()` для интеграции history-слоя.
- **Hardening изоляции peer и OFF-режима:**
  - устранено возможное смешивание истории разных peer за счёт отдельного session-буфера истории
  - OFF-режим строгий: при выключении новые сообщения не попадают в историю и не сохраняются
  - при disconnect состояние загруженной истории/peer корректно сбрасывается

### Тесты

- Добавлен `tests/test_chat_history.py`:
  - round-trip шифрования/дешифрования
  - поведение при повреждённом/некорректном файле и неверном ключе
  - FIFO-ротация / лимиты сообщений
  - безопасность при ошибке атомарной записи
  - изоляция истории между peer
- Добавлен `tests/test_history_ui_guards.py`:
  - guard записи только при ON
  - guard-сброс состояния при OFF
  - guard-сброс состояния при disconnect

### Совместимость

Patch-релиз для ветки `v0.6.x`, без изменений форматов обычного online chat-протокола.
