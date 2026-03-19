# I2PChat v0.5.3 — offline delivery via drop.i2p

## EN

### Summary

`v0.5.3` adds a semi-offline delivery path on top of the existing E2E encrypted SAM transport:

- when the peer is offline, outgoing text messages are uploaded to `drop.i2p`;
- when the peer comes back online, the sender automatically transmits a short “pointer” over the normal I2P channel;
- the receiver downloads+decrypts the payload from `drop.i2p` and ACKs only after successful processing.

This is deferred delivery: payloads are kept temporarily on `drop.i2p`, and are fetched later after the pointer is delivered over I2P.

It also improves GUI startup stability (qasync/SAM) and hardens operational behaviour around `drop.i2p` limits.

### Key changes

#### 1) Drop-backed semi-offline delivery (High)

- New client `drop_i2p_client.py` implements:
  - `POST /api/upload` (multipart/form-data) for payload upload
  - `GET /f/<id>` for payload download
- `i2p_chat_core.py` adds:
  - a persistent per-peer outbox (`*.drop_outbox.json`);
  - a persistent cache of derived session keys (`*.drop_keys.json`) created after a successful live handshake;
  - automatic outbox “flush” after reconnect/handshake using `__DROP_PTR__` pointer messages over `msg_type="U"`.

#### 2) Receiver-side deduplication for `max_downloads=1` (High)

- Receiver deduplicates repeated pointers using `outbox_id`.
- If the same outbox entry was already processed, it ACKs without re-downloading.

#### 3) Safer peer selection (Medium)

- Removed the “fallback” behavior that could pick a peer from cache when no peer is explicitly set.
- Offline delivery now requires:
  - an active `Connect` (sets `current_peer_addr`), or
  - `Lock to peer` (sets `stored_peer`).

#### 4) Better reconnect UX on SAM transient failures (Medium)

- `connect_to_peer()` retries briefly on `CantReachPeer` / `PeerNotFound` / `Timeout` (often right after peer restart / LeaseSet propagation delays).
- GUI error messages no longer end up empty when SAM exception stringification is blank.

#### 5) GUI/qasync stability fix (Low)

- `i2plib/aiosam.py` now ignores `errno=22` (`TCP_NODELAY` failure) during socket setup in some macOS/Python/qasync combinations, preventing a hard crash on startup.

### Defaults for drop.i2p payloads

- Default `expiry`: `720h` (30 days)
- Default `max_downloads`: `1`
- These can still be overridden via:
  - `I2PCHAT_DROP_EXPIRY`
  - `I2PCHAT_DROP_MAX_DOWNLOADS`

### Verification

- `python3 -m unittest discover -s tests -p "test_*.py"` — `OK`

---

## RU

### Кратко

`v0.5.3` добавляет semi-offline доставку поверх текущего E2E защищённого SAM транспорта:

- если peer оффлайн — исходящие текстовые сообщения **загружаются** на `drop.i2p`;
- когда peer снова онлайн — sender автоматически отправляет по I2P короткий “pointer”;
- receiver скачивает+расшифровывает payload с `drop.i2p` и **ACK-ает только после успешной обработки**.

То есть доставка отсроченная: данные временно хранятся на `drop.i2p`, а получатель забирает их позже после доставки pointer по I2P.

В релиз также включены фиксы стабильности GUI с qasync и усиления вокруг лимитов drop.i2p.

### Основные изменения

#### 1) Semi-offline доставка через drop.i2p (High)

- Новый модуль `drop_i2p_client.py`:
  - `POST /api/upload` (multipart/form-data) для загрузки payload;
  - `GET /f/<id>` для скачивания payload.
- В `i2p_chat_core.py` добавлено:
  - персистентный per-peer outbox (`*.drop_outbox.json`);
  - персистентный кэш derived session keys (`*.drop_keys.json`), создаваемый после успешного live-handshake;
  - автоматический flush outbox после reconnect/handshake через pointer сообщения `__DROP_PTR__` по `msg_type="U"`.

#### 2) Дедуп на стороне receiver для `max_downloads=1` (High)

- Receiver дедуплицирует повторяющиеся pointer’ы по `outbox_id`.
- Если тот же outbox-entry уже обработан — ACK отправляется без повторного скачивания.

#### 3) Безопаснее выбор peer (Medium)

- Убрано небезопасное поведение “fallback”, когда peer мог выбираться из кэша при отсутствии явного указания peer.
- Offline-доставка теперь требует:
  - `Connect` (устанавливает `current_peer_addr`) или
  - `Lock to peer` (устанавливает `stored_peer`).

#### 4) Улучшенный reconnect UX при транзиентных ошибках SAM (Medium)

- `connect_to_peer()` делает краткие ретраи при `CantReachPeer` / `PeerNotFound` / `Timeout` (часто из‑за задержки распространения LeaseSet сразу после рестарта peer).
- Сообщения GUI больше не становятся пустыми, если строка SAM-исключения пуста.

#### 5) Стабильность GUI/qasync (Low)

- `i2plib/aiosam.py` игнорирует `errno=22` (`TCP_NODELAY` failure) при настройке сокета на некоторых macOS/Python/qasync комбинациях, чтобы не было hard-crash при старте.

### Значения по умолчанию для drop.i2p

- `expiry`: `720h` (30 дней)
- `max_downloads`: `1`
- Можно переопределить через:
  - `I2PCHAT_DROP_EXPIRY`
  - `I2PCHAT_DROP_MAX_DOWNLOADS`

### Проверка

- `python3 -m unittest discover -s tests -p "test_*.py"` — `OK`

