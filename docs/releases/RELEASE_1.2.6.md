# I2PChat v1.2.6 — SAM Session Manager (transport lifecycle split)

## Что сделано по Session Manager

В этой итерации появился отдельный слой **`SessionManager`**:

- **Новый модуль:** `i2pchat/core/session_manager.py`
- **Роль:** управляет жизненным циклом транспорта/SAM, а не бизнес-логикой сообщений.

### Перенесено из `I2PChatCore`

- создание/удержание long-lived SAM session socket;
- владение transport task lifecycle:
  - `accept`,
  - `tunnel watcher`,
  - `keepalive`,
  - `handshake watchdog`,
  - `disconnect task`;
- реестр outbound streams;
- reconnect/backoff bookkeeping;
- состояние “live path alive / degraded”.

### Добавленные state machine

- **Transport state:**
  `STOPPED`, `STARTING`, `SAM_CONNECTED`, `WARMING_TUNNELS`, `READY`, `DEGRADED`, `RECONNECTING`, `SHUTTING_DOWN`, `FAILED`
- **Peer state:**
  `DISCONNECTED`, `CONNECTING`, `HANDSHAKING`, `SECURE`, `STALE`, `FAILED`

### Policy для отправки

Вместо разрозненных условий в `send_text` добавлен централизованный outbound policy:

- `LIVE_ONLY`
- `PREFER_LIVE_FALLBACK_BLINDBOX`
- `QUEUE_THEN_RETRY_LIVE`
- `BLINDBOX_ONLY`

## Совместимость и поведение

- Публичный интерфейс `I2PChatCore` сохранён.
- Wire protocol не менялся.
- Для маршрутизации `auto` сохранена совместимая семантика live-пути:
  live считается доступным при `conn + handshake_complete`.

## Связанные стабилизации после внедрения

После выделения Session Manager добавлены правки для устойчивости BlindBox:

- ускорен и упорядочен выбор `recv_index` (приоритет от `recv_base`);
- добавлен лимит scan за цикл;
- добавлен timeout на один `recv_index` и мягкий grace после первого результата;
- добавлена UI-диагностика poller (`[BBDBG]`) и slow-warning для lagging replicas.

## Изменённые файлы (ядро изменений)

- `i2pchat/core/session_manager.py`
- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/blindbox/blindbox_client.py`
- `tests/test_session_manager.py`
- `tests/test_send_text_routing.py`
- `tests/test_blindbox_polling.py`
- `tests/test_blindbox_client.py`

## Проверка

- полный прогон: `632-633 passed` (в зависимости от набора тестов между коммитами),
- ключевые regression tests на Session Manager, routing и BlindBox polling добавлены.
