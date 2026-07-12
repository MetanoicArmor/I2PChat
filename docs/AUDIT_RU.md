# Аудит безопасности I2PChat

**Дата аудита:** 2026-04-11; повторная проверка и исправления: 2026-07-12
**Метод:** ручной обзор исходников + автоматические secret/dependency/static scans + целевые регрессионные тесты  
**Объём:** исходный код репозитория, build/release-скрипты, CI workflow и security-чувствительные UI/документационные потоки  
**Важное ограничение:** это аудит **по исходникам**. Сгенерированные архивы и собранные бинарники, лежащие в репозитории, не реверсировались и не сравнивались как бинарные артефакты.

---

## Резюме

- В текущем дереве исходников не осталось открытых подтверждённых уязвимостей уровня **Critical** или **High**.
- Повторная проверка в июле выявила **High-impact подмену отправителя группы** в опциональном общем Group BlindBox. До обновления отчёта проблема была устранена: добавлены подписанные Ed25519-конверты v3, проверка закреплённого ключа и отказ от неподписанного v2.
- Модель безопасности chat/runtime в целом сильная:
  - SAM-команды строятся с валидацией входных токенов.
  - Handshake использует X25519 + Ed25519 + HKDF-разделение ключей.
  - После handshake трафик защищён MAC-проверкой и anti-replay / anti-reorder логикой.
  - Отправитель группового сообщения привязан к аутентифицированному каналу или проверенной Ed25519-подписи.
  - SAM встроенного роутера ограничен loopback-адресами.
  - Чувствительные локальные файлы обычно пишутся атомарно и с ограниченными правами.
- Главные подтверждённые риски сейчас — это **integrity / supply-chain / operational** риски, а не прямые remote memory corruption или injection.

**Открытые/остаточные находки по severity:** Critical `0`, High `0`, Medium `3`, Low `3`, Informational `6`

---

## Что было проверено

### Автоматические проверки

Команды, выполненные в рамках исходного аудита и июльской повторной проверки:

```bash
gitleaks detect --no-git --source . --config .gitleaks.toml --report-format json --report-path /tmp/i2pchat-gitleaks.json
uvx --from bandit bandit -q -r i2pchat -f json -o /tmp/i2pchat-bandit.json
uv export --frozen --no-dev --no-emit-project -o /tmp/i2pchat-runtime.txt
uvx pip-audit==2.9.0 --require-hashes -r /tmp/i2pchat-runtime.txt
uv export --frozen --only-group build --no-emit-project -o /tmp/i2pchat-build.txt
uvx pip-audit==2.9.0 --require-hashes -r /tmp/i2pchat-build.txt
uv run pytest tests/test_audit_remediation.py tests/test_sam_input_validation.py tests/test_protocol_hardening.py tests/test_blindbox_server_example.py tests/test_profile_backup.py tests/test_history_export.py -q
uv run pytest -q
```

Фактические результаты:

- Повторный `gitleaks`: **проверено 678 коммитов, утечек не найдено**
- `pip-audit` по runtime lock export: **No known vulnerabilities found**
- `pip-audit` по build lock export: **No known vulnerabilities found**
- Полный повторный `pytest`: **744 passed, 5 skipped, 64 subtests passed**
- Зафиксированный Linux-пакет `cryptography` обновлён до **49.0.0**; прежняя затронутая версия была заменена из-за **GHSA-537c-gmf6-5ccf**.
- `bandit`: **103 findings**, но ручной triage показал, что это в основном низкосигнальные паттерны (`try/except/pass`, `assert`, общие эвристики по subprocess). Подтверждённых exploitable High-находок нет.

### Области ручного обзора

- trust path проверки обновлений и UX загрузок
- handshake, MAC, replay, framing транспорта
- локальные/direct режимы BlindBox и example server
- export/import профиля, истории и backup-потоки
- управление встроенным роутером и provenance build-time артефактов
- CI security gates и release-integrity controls

---

## Находки, исправленные при повторной проверке 2026-07-12

### R1. Подмена отправителя группы при доставке по аутентифицированному каналу 1:1

- **Проблема:** участник группы мог отправить групповой конверт, указав в `sender_id` другого участника.
- **Исправление:** для recipient-scoped транспорта `sender_id` теперь обязан совпадать с криптографически аутентифицированным пиром транспортного канала.
- **Регрессионное покрытие:** поддельный отправитель отклоняется, совпадающий принимается, формы адреса с `.b32.i2p` нормализуются одинаково.

### R2. Подмена отправителя через общий секрет legacy Group BlindBox

- **Проблема:** все участники опционального общего Group BlindBox знали общий материал шифрования, поэтому само шифрование не подтверждало автора конверта.
- **Исправление:** протокол Group BlindBox **v3** подписывает канонический конверт Ed25519-ключом отправителя. Импорт требует корректную подпись и точное совпадение с закреплённым ключом отправителя.
- **Защита от downgrade:** неподписанные конверты Group BlindBox **v2** отклоняются. Старые клиенты отклоняют v3 как неизвестную версию, а не принимают его без проверки подписи.
- **Совместимость:** затронутый общий канал по-прежнему включается только через `I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`; использующие его участники должны обновиться одновременно.

### R3. Неаутентифицированные управляющие сигналы до handshake

- **Проблема:** открытые управляющие кадры до установки защищённого канала могли попасть в обработчик управляющих команд.
- **Исправление:** управляющие сигналы до handshake игнорируются, кроме узко ограниченного корректного `QUIT`. Обработка зашифрованных сигналов после handshake не изменилась.

### R4. Небезопасный bind SAM встроенного роутера и инъекция конфигурации

- **Проблема:** специально сформированный `bundled_sam_host` мог открыть SAM за пределами локальной машины или добавить строки в генерируемую конфигурацию `i2pd`.
- **Исправление:** встроенный SAM принимает только loopback IP-адреса или `localhost`; проверка выполняется при загрузке/сохранении настроек и повторно при генерации конфигурации роутера.
- **Совместимость:** удалённая настройка SAM **системного роутера** остаётся отдельной; ограничение относится к управляемому приложением встроенному роутеру.

### R5. Уязвимая криптографическая зависимость

- **Проблема:** предыдущая зафиксированная версия `cryptography` была затронута **GHSA-537c-gmf6-5ccf**.
- **Исправление:** lock-файл теперь разрешает Linux `cryptography` в версию **49.0.0**.
- **Проверка:** текущие runtime/build exports не содержат известных уязвимостей по результатам `pip-audit`.

---

## Подтверждённые находки

Перечисленные ниже находки остаются открытыми после июльских исправлений. Итог по severity относится только к этим **остаточным** находкам.

### Средний уровень

### M1. Проверка обновлений не аутентифицирует метаданные криптографически

**Где**

- `i2pchat/updates/release_index.py:20-21`
- `i2pchat/updates/release_index.py:55-80`
- `i2pchat/gui/main_qt.py:10175-10257`

**Доказательство**

- Источник обновлений — HTML-страница релизов по `http://...b32.i2p/`.
- Клиент извлекает имена ZIP из HTML и сравнивает только версии.
- GUI предупреждает пользователя о необходимости ручной проверки checksum/signature, но приложение само не проверяет подписанные update-метаданные.

**Влияние**

- Враждебный origin страницы релизов, злонамеренный прокси или скомпрометированный eepsite могут влиять на то, что приложение показывает как “latest version”.
- Текущая реализация **не** скачивает и **не** устанавливает бинарники автоматически, поэтому это не прямой RCE само по себе.
- Риск в первую очередь **integrity/social-engineering**: подвести пользователя к вредоносной сборке или ложному “обновлению”.

**Рекомендация**

- Если update UX станет чем-то большим, чем “информационная проверка”, нужна **подписанная update-manifest схема** с проверкой встроенного доверенного публичного ключа.
- До появления такой схемы сохранять текущий warning про ручную верификацию.

---

### M2. BlindBox setup UI предлагает mutable `curl ... && sudo bash` путь из GitHub `main`

**Где**

- `i2pchat/blindbox/local_server_example.py:23-25`
- `i2pchat/blindbox/local_server_example.py:173-176`
- `i2pchat/gui/main_qt.py:11960-11966`

**Доказательство**

- Helper собирает one-liner, который скачивает `install.sh` с `raw.githubusercontent.com/.../main/...`.
- GUI даёт кнопку **Copy curl**, которая кладёт эту команду в clipboard.
- Команда запускает скачанный скрипт от root.

**Влияние**

- Это обходит более безопасный локальный/bundled путь и фактически рекомендует оператору **mutable remote root installer**.
- Если репозиторий, ветка `main` или publishing account скомпрометированы, скопированная команда может выполнить код атакующего с root-правами на сервере.
- Это **supply-chain/operational** риск, а не автоматическая компрометация внутри I2PChat.

**Рекомендация**

- Предпочесть уже существующий путь **Get install**, который сохраняет bundled local script.
- Если one-liner всё же нужен, пиновать его на **release tag или commit digest** и дополнять проверкой целостности.
- Не рекомендовать `curl | sudo bash`-подобные потоки от mutable branch tip.

---

### M3. Portable build path может подтягивать bundled `i2pd` из непинованного внешнего репозитория

**Где**

- `scripts/ensure_bundled_i2pd.sh:8-10`
- `scripts/ensure_bundled_i2pd.sh:45-55`
- `scripts/ensure_bundled_i2pd.sh:64-69`
- `build-windows.ps1:140-159`
- `docs/BUILD.md:20-31`

**Доказательство**

- Build helper умеет автоматически клонировать `https://github.com/MetanoicArmor/i2pchat-bundled-i2pd.git`.
- Клон делается как `--depth=1`; нет commit pin, signed-tag verification, checksum validation или фиксации provenance.
- Windows build logic зеркалит то же поведение.

**Влияние**

- Скомпрометированный внешний репозиторий, branch tip или build environment могут подменить bundled router binary, который затем попадёт в release artifacts.
- Этот риск в первую очередь затрагивает **maintainers/builders и release provenance**, а не уже установленный клиент.

**Рекомендация**

- Пиновать bundled-router source на **конкретный commit/tag** и проверять provenance.
- Предпочитать immutable release assets + checksums/signatures вместо branch-tip clone.
- Записывать revision bundled `i2pd` в release metadata.

---

### Низкий уровень

### L1. Переменные окружения могут перенаправить update UX на произвольный release/proxy source

**Где**

- `i2pchat/updates/release_index.py:62-80`
- `i2pchat/gui/main_qt.py:10175-10195`

**Доказательство**

- `I2PCHAT_RELEASES_PAGE_URL` может заменить origin release-page.
- `I2PCHAT_UPDATE_HTTP_PROXY` может направить update traffic через произвольный proxy.
- GUI один раз показывает warning перед использованием.

**Влияние**

- Локальный атакующий с контролем окружения или неверная deployment-конфигурация могут влиять на результат проверки обновлений и на открываемую страницу загрузок.
- Риск смягчается one-time warning и отсутствием auto-install.

**Рекомендация**

- Рассмотреть показ resolved update origin каждый раз, а не только один раз.
- Опционально добавить allowlist для non-default scheme/host или strict mode.

---

### L2. Опциональный BlindBox HTTP status endpoint может стать неаутентифицированным при ошибочной эксплуатации

**Где**

- `i2pchat/blindbox/blindbox_server_example.py:92-99`
- `i2pchat/blindbox/blindbox_server_example.py:151-155`
- `i2pchat/blindbox/blindbox_server_example.py:556-563`
- `i2pchat/blindbox/blindbox_server_example.py:597-605`

**Доказательство**

- HTTP status service опционален и по умолчанию выключен.
- По умолчанию bind идёт только на loopback, но `BLINDBOX_HTTP_HOST` управляется оператором.
- Если admin token и replica auth token пустые, `_admin_token_ok()` пропускает запрос.

**Влияние**

- При неудачной конфигурации оператор может открыть `/healthz`, `/status.json` и `/metrics` наружу без аутентификации.
- Возвращаемые данные не выглядят глубоко чувствительными, но помогают probing/enum.

**Рекомендация**

- Если `BLINDBOX_HTTP_HOST` не loopback, требовать `BLINDBOX_ADMIN_TOKEN`.
- Либо запрещать public bind без явной auth-конфигурации.

---

### L3. Валидация изображений всё ещё полностью декодирует payload, оставляя остаточный риск resource pressure

**Где**

- `i2pchat/core/i2p_chat_core.py:1112-1141`

**Доказательство**

- Валидация сначала проверяет file size и dimensions, затем полностью загружает изображение через Pillow (`img.load()`).

**Влияние**

- Специально подготовленное локальное изображение всё ещё может вызвать повышенную нагрузку по CPU/памяти во время decode.
- Это в первую очередь **local DoS/resource exhaustion**, а не remote code execution.

**Рекомендация**

- Добавить явную decompression-bomb policy и/или более строгий pixel-budget guard.
- Сохранить текущие лимиты на размер файла и dimensions.

---

## Информационные наблюдения

1. **SAM input validation сделана хорошо.** `i2pchat/sam/protocol.py` отбрасывает whitespace/newline/control-char injection в критичных SAM token/option полях.
2. **Handshake заметно усилен.** `i2pchat/crypto.py` и `i2pchat/core/i2p_chat_core.py` используют X25519, Ed25519, HKDF key separation, MAC verification и replay/order checks.
3. **TOFU trust явный.** Новые peer signing keys pin’ятся, а mismatch требует подтверждения, если не включён явный auto-trust.
4. **Path handling для входящих файлов защищён.** Filename sanitization и collision-safe allocation используют exclusive creation.
5. **Backup/history import/export реализованы аккуратно.** Архивы валидируют структуру/checksum, запись идёт атомарно; backup bundle отклоняет unsafe tar member paths.
6. **CI security hygiene хорошая.** В репозитории уже есть dependency audit, secret scan и release-integrity policy checks.

---

## Разбор шума автоматических сканов

Следующие findings были просмотрены и **не** были признаны подтверждёнными уязвимостями:

- `bandit` `B603/B607` вокруг subprocess:
  - runtime код использует списки аргументов, а не `shell=True`
  - Linux notification helpers резолвят абсолютные бинарники через `shutil.which`
  - bundled router стартует через `asyncio.create_subprocess_exec`
- `bandit` `B311` в `session_manager.py`:
  - `random.uniform()` используется только для reconnect jitter, а не для crypto material
- `bandit` `B103` на `os.chmod(path, 0o755)`:
  - это chmod для user-saved shell installer и он соответствует назначению исполняемого скрипта
- `assert` в TUI/GUI:
  - это скорее smell корректности, но конкретного security-impact в рамках аудита не подтверждено

---

## Что уже хорошо закрыто

- В runtime-коде не найдено паттернов `shell=True`.
- Secret scan настроен в CI, локальный `gitleaks` чистый.
- Lock-файлы присутствуют, runtime/build exports чистые под `pip-audit`.
- Protocol hardening tests покрывают malformed/truncated frames и transfer edge cases.
- Backup/history persistence предпочитает atomic writes и ограниченные права на файлы.
- В пользовательской документации уже есть инструкция проверять `SHA256SUMS` и detached GPG signature.

---

## Остаточные риски и следующие шаги

### Самые полезные следующие шаги

1. Заменить mutable update/install trust paths на signed immutable metadata/artifacts.
2. Пиновать и верифицировать внешний bundled-router source в build workflows.
3. Жёстко требовать auth для любого non-loopback BlindBox HTTP status bind.
4. Сохранять dependency/secret scanning обязательным в CI.
5. Добавить более строгую decompression-bomb policy для враждебных изображений.

### Риски, которые остаются по дизайну

- Локальный I2P router остаётся частью trusted computing base.
- Операторские флаги могут сознательно ослаблять posture (`I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL`, direct TCP replicas, weak BlindBox quorum).
- Проверка обновлений остаётся advisory, а не cryptographically authoritative.

---

## Основные просмотренные файлы

- `i2pchat/updates/release_index.py`
- `i2pchat/gui/main_qt.py`
- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/core/session_manager.py`
- `i2pchat/crypto.py`
- `i2pchat/protocol/protocol_codec.py`
- `i2pchat/sam/protocol.py`
- `i2pchat/blindbox/blindbox_server_example.py`
- `i2pchat/blindbox/local_server_example.py`
- `i2pchat/router/bundled_i2pd.py`
- `i2pchat/storage/profile_backup.py`
- `i2pchat/storage/history_export.py`
- `i2pchat/storage/profile_export.py`
- `scripts/ensure_bundled_i2pd.sh`
- `.github/workflows/security-audit.yml`
- `.github/workflows/secret-scan.yml`

---

Этот документ фиксирует состояние репозитория на момент проверки. Он не заменяет reproducible-build verification, проверку release signatures, инфраструктурный review и внешний pentest.
