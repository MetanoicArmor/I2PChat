# I2PChat v0.6.0 — BlindBox UX and offline delivery update

## RU

### Кратко

`v0.6.0` развивает BlindBox-контур из `v0.5.4` и доводит UX офлайн-доставки до практичного ежедневного режима:

- BlindBox для именованных (`persistent`) профилей работает **по умолчанию**;
- `Send` стал маршрутизатором: live-сессия при наличии, иначе офлайн-очередь BlindBox;
- добавлен опциональный локальный Blind Box (fallback) для dev/same-host тестов;
- уменьшен UI-шум: служебные BlindBox-уведомления о queued/received в чат не выводятся;
- улучшена строка статуса и кнопки действий (динамика, компактность в узком окне, выравнивание).

Релиз остается в фокусе **text-only offline** для BlindBox (вложения через BlindBox не включались).

---

### Что добавлено и изменено относительно v0.5.4

#### 1) Поведение отправки (smart routing)

- `send_text()` в ядре возвращает структурированный результат маршрута/причины блокировки.
- При отсутствии live secure-сессии сообщение отправляется в BlindBox-очередь, если runtime готов.
- При live-сессии используется онлайн-канал как приоритетный путь.

#### 2) BlindBox default-on для persistent профилей

- Для именованных профилей BlindBox включается по умолчанию.
- Для `default` (`TRANSIENT`) режима BlindBox остается отключенным.
- Явное выключение: `I2PCHAT_BLINDBOX_ENABLED=0`.

#### 3) Blind Box-серверы для межхостовой доставки и локальный fallback

- Для межхостовой офлайн-доставки список **Blind Box**-серверов задаётся через `I2PCHAT_BLINDBOX_REPLICAS`.
- Список серверов по умолчанию для деплоя: `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS`.
- Для прод-настроек: файл со списком серверов — `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE`.
- Приоритет чтения: `I2PCHAT_BLINDBOX_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS` → `I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE` → **релизный набор** `DEFAULT_RELEASE_BLINDBOX_ENDPOINTS` в `i2p_chat_core.py` (два адреса: `tcglilyjadosrez5gu3kqvrdpu6ri622jwrzamtpburtnpge7wgq.b32.i2p:19444`, `dzyhukukogujr6r2vwfy667cwm7vg300mhx2sryxhb6mn414wbjq.b32.i2p:19444`). Отключить встроенный набор: `I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1`.
- Локальный fallback (`127.0.0.1:19444`) оставлен как явный opt-in для dev/same-host сценариев (`I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1`).
- Локальный Blind Box переиспользуется, если уже запущен на порту.

#### 4) Соединение и устойчивость

- Для некоторых `CantReachPeer` сценариев добавлен мягкий warm-up retry.
- Уточнены состояния доставки/инициализации (в т.ч. handshake-in-progress), чтобы UI давал корректные подсказки.

#### 5) UI/UX и шум в чате

- Убраны служебные BlindBox-строки из основного чата:
  - `[BlindBox] Received offline message id=...`
  - `[BlindBox] queued offline message index=...`
- Кнопка отправки в offline-ready режиме помечается как `Send offline` (2 строки в кнопке).
- Строка статуса сохраняет динамичное поведение при изменениях сети/безопасности, но не мешает сужению окна.

---

### Крипто- и протокольная база BlindBox (из v0.5.4, актуально)

- отдельные примитивы: `blindbox_key_schedule.py`, `blindbox_blob.py`, `blindbox_state.py`;
- клиент для нескольких Blind Box: `blindbox_client.py`;
- metadata-hardening: padding/jitter/cover GET/random window order;
- root/epoch rotation с bounded-state ограничениями.

---

### Совместимость

- Live-режим совместим с текущей линией клиента.
- BlindBox между очень разными сборками может работать частично (ожидаемо для эволюции offline-контура).

---

### Основные файлы, затронутые релизом

- `i2p_chat_core.py`
- `main_qt.py`
- локальный сервис Blind Box: `blindbox_local_replica.py`
- `blindbox_client.py`
- `I2PChat.spec`
- `README.md`
- `docs/MANUAL_EN.md`
- `docs/MANUAL_RU.md`

---

### Итог

`v0.6.0` переводит BlindBox из «добавленной возможности» в удобный рабочий сценарий:

- меньше ручных шагов для пользователя;
- меньше служебного шума в чате;
- более предсказуемая и понятная доставка (online/offline) с сохранением приватностных свойств BlindBox.

---

### Security hardening update (после v0.6.0)

Дополнительно после релиза `v0.6.0` выполнено усиление безопасности (кроме `M-05`, оставленного отдельно):

- **CI dependency audit (M-04):**
  - основной gate переведён на `pip-audit -r requirements.txt` (lockfile-first);
  - `pip-audit -r requirements.in` оставлен как дополнительный сигнал.

- **Редакция чувствительных SAM-логов (M-01):**
  - debug-лог SAM-ответов теперь проходит redaction чувствительных полей (`PRIV`, `DESTINATION` и др.).

- **BlindBox local hardening (M-02/M-03):**
  - в локальном Blind Box (`blindbox_local_replica.py`) добавлен опциональный auth token для `PUT/GET`;
  - добавлен лимит записей (`max_entries`) с ответом `FULL`;
  - добавлен strict режим `I2PCHAT_BLINDBOX_REQUIRE_SAM=1` (запрет direct `host:port`);
  - добавлено явное предупреждение при активном non-SAM транспорте.

- **Local privacy hardening (L-01/L-02):**
  - в UI/логах убраны абсолютные пути в чувствительных местах (basename-only);
  - Windows stdout fallback уведомлений больше не печатает текст сообщений.

- **Тесты и документация:**
  - добавлены/обновлены тесты: `tests/test_aiosam_redaction.py`, `tests/test_blindbox_client.py`, `tests/test_blindbox_core_telemetry.py`;
  - обновлены отчёты `AUDIT_EN.md`, `AUDIT_RU.md`;
  - добавлен `REMEDIATION_PLAN.md`.
