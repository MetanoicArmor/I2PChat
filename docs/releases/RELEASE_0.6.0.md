# I2PChat v0.6.0 — BlindBox UX and offline delivery

## EN

### What this release is about

**BlindBox** is I2PChat’s offline path: when the peer is not reachable over a live tunnel, encrypted blobs can still be placed on shared **Blind Box** servers and picked up later. Version **0.6.0** focuses on making that path understandable in the UI and safe to turn on for normal use, without changing the live chat protocol.

### Sending and profiles

- **`send_text()`** returns a structured result (accepted / queued / blocked and why). The GUI routes **Send** intelligently: if a **live secure session** exists, traffic uses it; if not but BlindBox is ready, text goes to the **offline queue**.
- For **named (persistent) profiles**, BlindBox is **enabled by default**. The ephemeral **`random_address`** profile (**TRANSIENT** mode; CLI alias **`default`**) keeps BlindBox **off** so casual runs do not touch shared boxes. To force-disable everywhere: **`I2PCHAT_BLINDBOX_ENABLED=0`**.
- Offline delivery in this release remains **text-only**; file/image transfer through BlindBox is not part of this scope.

### Blind Box servers (configuration)

- Shared **Blind Box** endpoints (for delivery between different hosts) are configured with environment variables. The main list is **`I2PCHAT_BLINDBOX_REPLICAS`**. Defaults for a whole deployment can be set with **`I2PCHAT_BLINDBOX_DEFAULT_REPLICAS`** or, for production, **`I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE`**.
- **Resolution order:** `I2PCHAT_BLINDBOX_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE` → built-in **`DEFAULT_RELEASE_BLINDBOX_ENDPOINTS`** in `i2pchat/core/i2p_chat_core.py`. To disable built-in defaults: **`I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1`**.
- For **local development** or same-machine tests, optional **`I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1`** uses `127.0.0.1:19444` when a local Blind Box is already listening.

### Interface and behaviour

- Chat no longer fills with low-level BlindBox lines such as “queued” / “received” for every blob; status is reflected in the **status bar**, **Send** button label (**Send offline** when queuing), and related tooltips.
- Connection telemetry and delivery states were tightened (including handshake-in-progress) so prompts match what the core is doing.
- Some **`CantReachPeer`** paths use a short **warm-up retry** before giving up.

### Security hardening (same release line)

Work tracked from the security audit (**M-05** intentionally deferred):

- **CI:** dependency audit gate prefers **`pip-audit -r requirements.txt`** (lockfile-first).
- **SAM:** debug logging **redacts** sensitive fields (`PRIV`, `DESTINATION`, etc.).
- **Local Blind Box** (`blindbox_local_replica.py`): optional **auth token** for PUT/GET, **`max_entries`** with **FULL** response, optional **`I2PCHAT_BLINDBOX_REQUIRE_SAM=1`** to avoid naive `host:port` use, and a clear warning when non-SAM transport is active.
- **Privacy:** sensitive UI/log paths use **basename only** where applicable.
- **Windows:** if a desktop toast is unavailable, the **console fallback** prints only a generic notify line—**not** the incoming message body—so chat text does not end up in terminal logs (`notifications.py`).

Full detail: **`docs/AUDIT_EN.md`**, **`docs/AUDIT_RU.md`**, **`REMEDIATION_PLAN.md`**.

### Compatibility

Live (in-tunnel) chat remains compatible with the current client generation. BlindBox interoperability across **very** different builds may be partial while the offline stack evolves.

---

## RU

### О чём релиз

**BlindBox** в I2PChat — это офлайн-доставка: если пир недоступен по «живому» туннелю, зашифрованные данные можно положить на общие **Blind Box**‑серверы и забрать позже. В **v0.6.0** упор на то, чтобы этот сценарий был **понятен в интерфейсе** и **безопасен для обычного включения**, без смены протокола live-чата.

### Отправка и профили

- **`send_text()`** возвращает структурированный результат (принято / в очередь / отклонено и почему). В GUI кнопка **Send** ведёт себя предсказуемо: при активной **live secure**‑сессии идёт онлайн; иначе, если BlindBox готов, текст уходит в **офлайн-очередь**.
- Для **именованных (persistent) профилей** BlindBox **включён по умолчанию**. Профиль **`random_address`** (**TRANSIENT**; в CLI алиас **`default`**) оставляет BlindBox **выключенным**, чтобы случайный запуск не трогал общие серверы. Полное отключение: **`I2PCHAT_BLINDBOX_ENABLED=0`**.
- В этом релизе офлайн по BlindBox по-прежнему **только текст**; вложения через BlindBox сюда не входят.

### Серверы Blind Box (настройка)

- Список общих **Blind Box** (между разными машинами) задаётся переменными окружения. Основной список — **`I2PCHAT_BLINDBOX_REPLICAS`**. Для деплоя по умолчанию: **`I2PCHAT_BLINDBOX_DEFAULT_REPLICAS`** или файл **`I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE`**.
- **Порядок:** `I2PCHAT_BLINDBOX_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE` → встроенный набор **`DEFAULT_RELEASE_BLINDBOX_ENDPOINTS`** в `i2pchat/core/i2p_chat_core.py`. Отключить встроенный набор: **`I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1`**.
- Для **локальной разработки** и тестов на одной машине: **`I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1`** — `127.0.0.1:19444`, если локальный Blind Box уже слушает порт.

### Интерфейс и поведение

- В ленте чата убраны частые служебные строки BlindBox («в очереди» / «получено» на каждый blob); состояние видно в **строке статуса**, на кнопке **Send** (подпись **Send offline** при очереди) и в подсказках.
- Уточнены состояния доставки и инициализации (в т.ч. пока идёт handshake), чтобы подсказки совпадали с ядром.
- Для части сценариев **`CantReachPeer`** добавлен короткий **warm-up retry**.

### Усиление безопасности (та же линия релиза)

По результатам аудита (**M-05** сознательно отложен):

- **CI:** основной gate — **`pip-audit -r requirements.txt`** (lockfile-first).
- **SAM:** в debug-логах — **редакция** чувствительных полей (`PRIV`, `DESTINATION` и др.).
- **Локальный Blind Box** (`blindbox_local_replica.py`): опциональный **токен** для PUT/GET, лимит **`max_entries`** с ответом **FULL**, режим **`I2PCHAT_BLINDBOX_REQUIRE_SAM=1`**, предупреждение при non-SAM транспорте.
- **Приватность:** в чувствительных местах в UI/логах — **только basename** путей.
- **Windows:** если toast недоступен, **фоллбэк в консоль** пишет только нейтральную строку, **без текста входящего сообщения** (`notifications.py`), чтобы переписка не светилась в логе терминала.

Подробно: **`docs/AUDIT_EN.md`**, **`docs/AUDIT_RU.md`**, **`REMEDIATION_PLAN.md`**.

### Совместимость

Live-чат совместим с текущей линией клиентов. BlindBox между сильно разными сборками может работать **частично**, пока офлайн-контур развивается.
