# I2PChat v0.3 — Security Hardening Report

## Обзор

Этот релиз фиксирует уязвимости, выявленные в протоколе обмена, и добавляет дополнительные механизмы защиты от:

- MITM на handshake;
- downgrade-атак;
- replay/out-of-order атак;
- отправки пользовательских данных до установления защищённого канала;
- неявного доверия к новому ключу пира без подтверждения пользователем.

Изменения выполнялись поэтапно и уже включены в ветку разработки:

- `ec51ce1` — **Harden protocol: enforce secure handshake and anti-replay**
- `31aecb4` — **Add signed handshake with TOFU key pinning**
- `ff1ff0e` — **Add GUI TOFU confirmation dialog for new peer keys**

---

## Ключевые улучшения безопасности

### 1) Принудительный защищённый режим (без crypto-fallback)

- Убран небезопасный режим без NaCl в защищённом канале.
- Если `PyNaCl` недоступен, secure-протокол не активируется.

### 2) Жёсткая state-machine handshake

- До завершения secure-handshake блокируются пользовательские типы сообщений.
- После завершения handshake запрещены повторные `H`-кадры.
- Добавлен watchdog handshake по таймауту.

### 3) Anti-downgrade

- После установления защищённого канала plaintext-фреймы считаются нарушением протокола.
- При обнаружении downgrade соединение разрывается.

### 4) Anti-replay / anti-reorder

- В зашифрованные фреймы добавлен sequence number.
- HMAC теперь вычисляется с учётом sequence number.
- Нарушение ожидаемой последовательности (повтор/перестановка) приводит к разрыву соединения.

### 5) Signed Handshake (защита от MITM)

Для `INIT/RESP` добавлены:

- nonce;
- ephemeral X25519 public key;
- long-term Ed25519 signing public key (для handshake);
- подпись transcript-а.

Подпись проверяется на обеих сторонах.

### 6) TOFU pinning

- Добавлено закрепление (pinning) ключа подписи пира по адресу.
- Для persistent-профилей trust store хранится в:
  - `~/.i2pchat/<profile>.trust.json` (Linux/Unix),
  - соответствующих platform-specific каталогах профилей.
- При несовпадении ранее закреплённого ключа соединение отклоняется.

### 7) Явное подтверждение TOFU в GUI

- При первом контакте с новым signing key показывается диалог:
  - peer address,
  - короткий fingerprint,
  - префикс публичного ключа.
- Пользователь выбирает:
  - **Yes** — доверить и закрепить ключ;
  - **No** — отклонить ключ, прервать установку защищённого канала.

---

## Изменения по файлам

### `i2pchat/core/i2p_chat_core.py`

- Принудительный secure-mode handshake.
- Handshake watchdog (`HANDSHAKE_TIMEOUT`).
- Протокольные проверки до/после handshake.
- Anti-replay счётчики (`_send_seq`, `_recv_seq`) и валидация последовательности.
- Контроль размера фрейма (`MAX_FRAME_BODY`).
- Signed-handshake логика (INIT/RESP с подписью).
- TOFU pinning + загрузка/сохранение trust store.
- Callback `on_trust_decision` для UI-подтверждения.

### `i2pchat/crypto.py`

- `compute_mac(...)` / `verify_mac(...)` расширены поддержкой `seq`.
- Добавлена генерация пары ключей подписи handshake:
  - `generate_signing_keypair()`.

### `i2pchat/gui/main_qt.py`

- Интегрирован callback `on_trust_decision`.
- Добавлен GUI-обработчик `handle_trust_decision(...)` с `QMessageBox` для явного подтверждения TOFU.

---

## Совместимость

В связи с изменением формата handshake и поведения протокола, для корректной работы необходимо обновление обеих сторон общения до версии с этими изменениями.

---

## Валидация

Проверка синтаксической корректности после изменений:

```bash
python3 -m compileall i2pchat/core/i2p_chat_core.py i2pchat/crypto.py i2pchat/gui/main_qt.py
```

Результат: успешно.

---

## Остаточные риски и рекомендации

1. TOFU не устраняет риск MITM на самом первом контакте без внешней верификации.  
   **Рекомендация:** сверять fingerprint через независимый канал.

2. Для production-режима желательно добавить отдельный UX для управления trust store:
   - просмотр закреплённых ключей;
   - ручной сброс/ротация pin.

3. Рекомендуется в будущем формально зафиксировать версию wire-протокола (например, явный `PROTOCOL_VERSION=3` в negotiation-фреймах), чтобы упростить миграции.

---

## Итог

Релиз `0.3` переводит протокол в значительно более строгий и безопасный режим:

- защищённый handshake обязателен;
- ключевые атаки (downgrade/replay/out-of-order) закрыты на уровне протокольной логики;
- проверка доверия к новому ключу пира стала явной и контролируемой пользователем через GUI.
