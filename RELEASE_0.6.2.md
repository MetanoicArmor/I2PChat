# I2PChat v0.6.2 — BlindBox client, SAM input, and local persistence hardening

## EN

### Summary

`v0.6.2` is a security-focused patch release on top of `v0.6.1`. It mitigates malicious BlindBox replica responses, tightens local loopback/direct replica policy, adds a strict local-replica probe without token pre-leak, makes `BLINDBOX_ROOT` exchange two-phase with ACK, validates SAM command inputs across all relevant builders, hardens atomic persistence (including profile `.dat`), bounds offline dedup memory, and surfaces insecure local mode in telemetry/UI.

### What changed

- **BlindBox GET size bound:** the client no longer trusts unbounded `OK <size>` from a replica. Response body size is capped (aligned with `BLINDBOX_MAX_FRAME_SIZE`) before buffering, reducing memory DoS risk when a compromised replica lies about size.
- **Local BlindBox replicas — secure by default:** configuring loopback/direct BlindBox endpoints (e.g. `127.0.0.1:19444`) without `I2PCHAT_BLINDBOX_LOCAL_TOKEN` now fails fast at startup. To allow the previous insecure behavior (e.g. dev/legacy), set `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1` explicitly. Telemetry exposes `allow_insecure_local_replicas`, `has_loopback_replicas`, and `insecure_local_mode`; the status bar shows **“BlindBox: insecure local”** with a tooltip warning when that mode is active.
- **Local replica health probe (no token leak):** when the app cannot bind the local BlindBox port and probes an already-listening process, it now uses a strict two-step handshake: `PING` → exact `PONG BLINDBOX_LOCAL_REPLICA_V1`, then (only if a token is configured) `AUTH <token>` → `OK`. Arbitrary services that reply `ERR` (or anything other than the magic line) are **not** treated as a compatible replica; the token is never sent before the magic response. This replaces the old `GET __health__` probe that could leak the token and accept misleading replies.
- **`BLINDBOX_ROOT` two-phase commit:** the initiator keeps a **pending** root until it receives `__SIGNAL__:BLINDBOX_ROOT_ACK|<epoch>` over the secure channel; only then does it promote pending → active and move the previous root into `previous roots`. The peer applies an incoming root, persists, then sends ACK. Duplicate roots for the same epoch are idempotent; stale ACKs are ignored; reconnect re-sends the same pending root instead of rotating again.
- **SAM line-protocol hardening (full entry points):** in addition to `NAMING LOOKUP` / `STREAM CONNECT`, `i2plib/sam.py` now validates `SESSION CREATE`, `STREAM ACCEPT`, `STREAM FORWARD` (`session_id`, `STYLE`, `SILENT`, `PORT`, options), plus `HELLO` version strings and `DEST GENERATE` signature type. This closes injection-style issues on paths that previously interpolated user-controlled fragments unchecked.
- **Safer atomic writes:** shared helpers (`atomic_write_json` / `atomic_write_text` in `blindbox_state.py`) are used for BlindBox state (single-pass JSON with all root fields), trust store, UI prefs, and **profile `.dat`**. Writes use `mkstemp` in the target directory, `flush` + `fsync`, `os.replace`, `chmod 0o600`, and temp cleanup—no fixed `path + ".tmp"` and no read–merge–rewrite for BlindBox state.
- **Bounded dedup cache:** offline BlindBox receive path limits growth of seen blob digests (default cap configurable via `I2PCHAT_BLINDBOX_MAX_SEEN_HASHES`, minimum 1).
- **Built-in BlindBox endpoint fix:** the release-default second BlindBox replica address was corrected to `dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444`. This fixes the previously embedded typo in the fallback/default replica list.
- **Regression coverage:** added/extended unit tests for probe behavior, `BLINDBOX_ROOT` / ACK, oversized GET headers, local-token policy, SAM input validation, atomic writes (including profile fault-injection), and telemetry for insecure local mode.

### Compatibility

This is a patch release for the `v0.6.x` line.

---

#### Read this — compatibility at a glance

| Area | Impact |
|------|--------|
| **Normal in-app chat with a remote peer** | **Unchanged.** Framed messages and signals for everyday messaging are the same; **this release does not break core chat.** |
| **BlindBox — local port probe** | **May require action.** Anything that pretends to be an “already running” local replica must speak the **new** `PING` → `PONG BLINDBOX_LOCAL_REPLICA_V1` (+ `AUTH` when a token is set) handshake. Old replicas that only did `PUT`/`GET` without it may no longer pass the probe. |
| **BlindBox — root exchange** | **Best with both sides on v0.6.2** (or builds with the same `BLINDBOX_ROOT` + `BLINDBOX_ROOT_ACK` logic). Mixed old/new peers can leave a **pending root** uncommitted until the peer supports ACK. |

---

**Details**

- **Local replica probe:** a process listening on the local BlindBox TCP port must implement the new `PING` / `AUTH` handshake to be recognized as “already running”. Old standalone local replicas that only spoke `PUT`/`GET` without this handshake may cause bind failure to surface as an error instead of being accepted as compatible.
- **Root exchange:** both sides should run this release (or equivalent logic) so that `BLINDBOX_ROOT` and `BLINDBOX_ROOT_ACK` stay in sync. Mixed old/new peers may leave a pending root uncommitted until both support ACK.

**Operational note:** if you use direct TCP to a local BlindBox replica and relied on *no* token, set `I2PCHAT_BLINDBOX_LOCAL_TOKEN` to a strong secret, or set `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1` only if you accept the risk.

---

## RU

### Кратко

`v0.6.2` — security patch поверх `v0.6.1`. Снижен риск DoS по памяти из-за ответов BlindBox-реплик, ужесточена политика локальных loopback/direct-реплик, добавлен строгий probe локальной реплики без утечки токена, двухфазный обмен `BLINDBOX_ROOT` с ACK, полная валидация SAM на всех точках входа, усилённая атомарная персистентность (включая `.dat` профиля), ограничение роста кэша дедупликации и явное отображение небезопасного локального режима в телеметрии/UI.

### Что изменилось

- **Лимит размера GET BlindBox:** клиент не принимает произвольный `OK <size>` от реплики; размер тела ограничен (в духе `BLINDBOX_MAX_FRAME_SIZE`) до чтения в буфер, что снижает риск memory DoS при злонамеренной реплике.
- **Локальные BlindBox-реплики — secure-by-default:** конфигурация loopback/direct (например `127.0.0.1:19444`) без `I2PCHAT_BLINDBOX_LOCAL_TOKEN` теперь приводит к ошибке при старте. Для прежнего небезопасного режима (dev/legacy) нужно явно задать `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`. В телеметрии: `allow_insecure_local_replicas`, `has_loopback_replicas`, `insecure_local_mode`; в статус-баре при активном небезопасном режиме показывается **«BlindBox: insecure local»** и предупреждение в toolTip.
- **Health probe локальной реплики (без утечки токена):** если порт занят и проверяется «чужой» процесс, используется строгий двухшаговый обмен: `PING` → точный ответ `PONG BLINDBOX_LOCAL_REPLICA_V1`, затем (при настроенном токене) `AUTH <token>` → `OK`. Произвольный сервис с ответом `ERR` или без magic-строки **не** считается совместимой репликой; токен не отправляется до валидного `PONG`. Старый probe через `GET __health__` убран.
- **Двухфазный коммит `BLINDBOX_ROOT`:** инициатор держит **pending** root до приёма `__SIGNAL__:BLINDBOX_ROOT_ACK|<epoch>` по защищённому каналу; только после ACK переносится pending → active и обновляются previous roots. Получатель применяет root, сохраняет состояние и шлёт ACK. Дубликаты для того же epoch — идемпотентно; устаревшие ACK игнорируются; при reconnect переотправляется тот же pending root.
- **Укрепление SAM (все точки входа):** помимо `NAMING LOOKUP` / `STREAM CONNECT`, в `i2plib/sam.py` валидируются `SESSION CREATE`, `STREAM ACCEPT`, `STREAM FORWARD` (идентификатор сессии, `STYLE`, `SILENT`, `PORT`, options), а также строки версий в `HELLO` и тип подписи в `DEST GENERATE`.
- **Атомарная персистентность:** общие хелперы (`atomic_write_json` / `atomic_write_text` в `blindbox_state.py`) используются для state BlindBox (однопроходный JSON со всеми полями root), trust store, UI prefs и **профиля `.dat`**: `mkstemp` в каталоге назначения, `flush` + `fsync`, `os.replace`, `chmod 0o600`, очистка временного файла; без фиксированного `path + ".tmp"` и без перечитывания/дописывания state в два прохода.
- **Ограничение кэша дедупликации:** рост множества хэшей увиденных blob’ов ограничен (лимит по умолчанию задаётся `I2PCHAT_BLINDBOX_MAX_SEEN_HASHES`, минимум 1).
- **Исправление встроенного BlindBox endpoint:** второй адрес BlindBox-реплики в release-дефолтах исправлен на `dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444`. Это исправляет прежнюю опечатку в зашитом списке fallback/default-реплик.
- **Тесты:** добавлены/расширены кейсы на probe, обмен root/ACK, oversized GET, политику токена, SAM, атомарные записи (в т.ч. fault injection для `.dat`) и телеметрию insecure local.

### Совместимость

Patch-релиз `v0.6.x`.

---

#### Обратите внимание — совместимость кратко

| Область | Влияние |
|---------|---------|
| **Обычный чат с удалённым peer в приложении** | **Без изменений.** Форматы кадров и сигналов для обычных сообщений те же; **основной чат этим релизом не ломается.** |
| **BlindBox — probe локального порта** | **Может потребоваться действие.** «Уже запущенная» локальная реплика должна поддерживать **новый** handshake `PING` → `PONG BLINDBOX_LOCAL_REPLICA_V1` (+ `AUTH` при токене). Старые реплики только с `PUT`/`GET` без него могут **не** проходить probe. |
| **BlindBox — обмен root** | **Желательно обе стороны на v0.6.2** (или сборки с той же логикой `BLINDBOX_ROOT` + `BLINDBOX_ROOT_ACK`). Смешанные старый/новый peer могут оставить **pending root** некоммиченным, пока второй не поддерживает ACK. |

---

**Подробности**

- **Probe локальной реплики:** процесс, слушающий порт локальной BlindBox, должен поддерживать новый обмен `PING` / `AUTH`, чтобы считаться «уже запущенной» совместимой репликой. Старые отдельные реплики только с `PUT`/`GET` без этого handshake могут привести к ошибке bind вместо «совместимого» процесса.
- **Обмен root:** обе стороны желательно с этим релизом (или эквивалентной логикой ACK), иначе pending root может не закоммититься, пока второй peer не поддерживает `BLINDBOX_ROOT_ACK`.

**Эксплуатация:** если вы использовали прямой TCP к локальной BlindBox-реплике *без* токена, задайте `I2PCHAT_BLINDBOX_LOCAL_TOKEN` со стойким секретом, либо только при осознанном риске включите `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`.
