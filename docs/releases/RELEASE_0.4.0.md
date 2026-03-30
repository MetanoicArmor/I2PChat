# I2PChat v0.4.0 — Отчёт по унификации протокола и security hardening

## Контекст

Ветка `protocol-unification` была направлена на перенос сильных инженерных сторон `stan/termchat-i2p-python` в текущий форк без потери защищённой модели соединения.

Цели релиза:

- перейти на более предсказуемый wire framing (`MAGIC` + fixed header + `VERSION` + `MSG_ID`);
- сохранить строгую security-модель текущего форка (signed handshake, TOFU, anti-downgrade, anti-replay);
- усилить устойчивость к spoofing/abuse для ACK-сигналов;
- добавить наблюдаемость по дропам ACK.

---

## Что реализовано

### 1) Новый vNext codec-слой

Добавлен отдельный модуль `i2pchat/protocol/protocol_codec.py`:

- `MAGIC = b"\\x89I2P"`;
- явный `PROTOCOL_VERSION = 4`;
- фиксированный header:
  - `MAGIC(4) | VER(1) | TYPE(1) | FLAGS(1) | MSG_ID(8) | LEN(4)`;
- флаг шифрования `FLAG_ENCRYPTED`;
- resync по `MAGIC` с ограничением сканирования (`resync_limit`).

Эффект:

- более надёжный и детерминированный парсинг потока;
- снижение риска «тихой» рассинхронизации кадра.

### 2) Интеграция codec в `I2PChatCore`

В `i2pchat/core/i2p_chat_core.py`:

- формирование кадров переведено на `frame_message_with_id(...)` + `ProtocolCodec.encode(...)`;
- добавлен `MSG_ID` для прикладного уровня доставки;
- сохранён шифрованный payload формата `SEQ + ENCRYPTED_BODY + HMAC`;
- `receive_loop` переведён на чтение через `self._codec.read_frame(...)`.

### 3) Сохранена строгая security state-machine

- блокировка пользовательских кадров до завершения secure handshake;
- после handshake plaintext-кадры считаются downgrade и приводят к разрыву;
- replay/out-of-order по `SEQ` и HMAC-проверка остались обязательными;
- fallback в legacy не включён в рантайме ядра по умолчанию (strict vNext).

### 4) Исправление критического регресса входящего connect/accept

После первоначального перехода на vNext был восстановлен совместимый preface identity:

- при исходящем connect снова отправляется raw identity line (`base64 + "\\n"`) перед framed `S`.

Эффект:

- `accept_loop(reader.readline())` снова корректно получает identity и не таймаутит.

### 5) Усиление ACK-валидации

ACK-сигналы (`MSG_ACK`, `FILE_ACK`, `IMG_ACK`) теперь подтверждаются только при корректном контексте:

- `msg_id` существует в ожидающей таблице;
- тип ACK соответствует ожидаемому (`msg/file/image`);
- состояние записи `awaiting_ack`;
- для file/image совпадает токен имени файла;
- ACK привязан к контексту peer и сессии.

Добавлена структура `PendingAckEntry` с полями:

- `token`, `ack_kind`, `created_at`, `state`,
- `peer_addr`,
- `ack_session_epoch`.

### 6) Anti-abuse для pending ACK

Добавлены TTL/лимиты и периодическая очистка:

- `ACK_TTL_SECONDS`;
- `ACK_MAX_PENDING`;
- `ACK_PRUNE_INTERVAL`;
- prune устаревших и лишних записей по oldest-first.

### 7) ACK telemetry и краткая индикация в UI

Добавлены счётчики дропа ACK:

- `unknown_id`,
- `context_mismatch`,
- `invalid_format`,
- `expired_or_state`.

Реализовано:

- `get_ack_telemetry()` в core;
- warning-логирование причин дропа;
- краткая индикация в статус-баре GUI/TUI:
  - `ACKdrop:N`, показывается только если `N > 0`.

---

## Изменённые файлы

- `i2pchat/protocol/protocol_codec.py` — новый vNext codec и framing.
- `i2pchat/core/i2p_chat_core.py` — интеграция codec, security hardening, ACK validation, telemetry.
- `tests/test_protocol_framing_vnext.py` — новые unit/negative тесты vNext/ACK/telemetry.
- `tests/test_asyncio_regression.py` — регрессия на connect identity preface.
- `i2pchat/gui/main_qt.py` — краткий `ACKdrop` в статусе.
- `i2pchat/gui/chat_python.py` — краткий `ACKdrop` в status panel.

---

## Тесты

Прогон:

```bash
python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py -v
```

Итог: `OK` (14 tests).

Ключевые проверки:

- downgrade-detection после handshake;
- connect identity preface перед framed identity;
- explicit legacy policy и legacy desync negative;
- ACK spoofing negative;
- encrypted ACK positive path;
- ACK session-context mismatch negative;
- pending ACK TTL/limit pruning;
- telemetry counters на dropped ACK.

---

## Совместимость

- Режим по умолчанию — **strict secure vNext**.
- Backward compatibility не является приоритетом данного релиза.
- Legacy fallback в runtime ядра отключён по умолчанию; plaintext после handshake запрещён.

---

## Итог

`v0.4.0` переводит форк на более зрелый wire-слой с явным `MAGIC/VERSION/MSG_ID`, сохраняет и усиливает текущую security-модель и закрывает класс ACK-spoofing/ACK-context злоупотреблений.

Результат релиза:

- повышена защищённость протокола;
- улучшена наблюдаемость (ACK telemetry + краткий статус);
- подтверждена устойчивость регрессионными и negative тестами.
