# Отчёт по аудиту безопасности: I2PChat

Дата аудита: 2026-03-30  
Состояние репозитория: `cfc2036d59005994738059971567148ef50f119f`  
Режим: полный аудит (протокол + криптография + локальная персистентность + UI + CI/release + supply chain + secret scan)

## Краткий итог

Полная ревизия аудита после завершения **package-first** раскладки (весь код приложения под `i2pchat/`, точка PyInstaller — [`i2pchat/run_gui.py`](../i2pchat/run_gui.py)), выравнивания шага `compileall` в скриптах сборки Linux/macOS/Windows и обновлений документации на `main`. Сам рефакторинг не менял протокол и криптоповерхность; в этом прогоне повторены аудит зависимостей, регрессионные тесты и обновлён снимок.

Подтверждённые findings (те же ID; перепроверено 2026-03-30):
- Critical: 0
- High: 0
- Medium: 1
- Low: 4

Общая оценка:
- Базовые контроли защищённого канала по-прежнему сильные (signed handshake, HKDF, HMAC + sequence, anti-downgrade).
- Исправления по истории чата и inline-image из прошлых аудитов на месте, с регрессионными тестами.
- **Test gate** в CI гоняет весь набор **`pytest tests/`** поверх фиксированного списка unittest.
- **Gitleaks** на push/PR; **`.gitleaks.toml`** фиксирует узкий path-allowlist для тестовой фикстуры (см. A-05).
- **pip-audit** (те же вызовы, что в CI): в hash-locked графе не зафиксировано незакрытых CVE сверх документированного ignore для Pygments (см. A-04).
- Выборочная проверка: под `i2pchat/` нет `shell=True` / `pickle.loads` / `eval`; нотификации и звук в GUI вызывают `subprocess` только со списками argv.
- Оставшиеся риски в основном **релиз/процесс** и **edge-case** hardening, а не поломка протокола.

## Scope и методология

Проверенные компоненты:
- Протокол/runtime/криптография: `i2pchat/core/i2p_chat_core.py`, `i2pchat/protocol/protocol_codec.py`, `i2pchat/crypto.py`
- Offline: `i2pchat/blindbox/blindbox_client.py`, `i2pchat/blindbox/blindbox_blob.py`, `i2pchat/storage/blindbox_state.py`, `i2pchat/blindbox/blindbox_local_replica.py`
- UI/локальное хранение: `i2pchat/gui/main_qt.py`, `i2pchat/run_gui.py`, `i2pchat/gui/__main__.py`, `i2pchat/storage/chat_history.py`, `i2pchat/storage/contact_book.py`, `i2pchat/presentation/compose_drafts.py`, `i2pchat/presentation/notification_prefs.py`, `i2pchat/presentation/unread_counters.py`, `i2pchat/platform/notifications.py`
- CI/release/supply-chain: `.github/workflows/*` (test-gate, security-audit, secret-scan), `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, `requirements*.txt`, `flake.lock`, `.gitleaks.toml`

Выполненные проверки:
- `pip-audit` (из `requirements-ci-audit.txt`), как в [`.github/workflows/security-audit.yml`](../.github/workflows/security-audit.yml):
  - `pip-audit -r requirements.txt --ignore-vuln CVE-2026-4539` → **OK** («No known vulnerabilities found, 1 ignored»).
  - `pip-audit -r requirements-build.txt --ignore-vuln CVE-2026-4539` → **OK**.
  - `pip-audit -r requirements.in --ignore-vuln CVE-2026-4539` → **OK**.
- `python -m unittest tests.test_blindbox_state_wrap tests.test_asyncio_regression tests.test_blindbox_client tests.test_atomic_writes tests.test_chat_history tests.test_history_ui_guards tests.test_profile_import_overwrite tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_audit_remediation`
  - Результат: **OK (125 tests)** (на машине аудитора).
- `python -m pytest tests/ -q`
  - Результат: **432 passed**, **64 subtests passed** (на машине аудитора).
- Ручной обзор: trust boundaries, семантика BlindBox/lock, валидация contact JSON, паттерны GUI/subprocess, политика secret-scan, package-first точки входа и отсутствие корневых шимов.

## Findings (текущее состояние)

### [MEDIUM] A-01: Подпись релизов всё ещё необязательна по умолчанию в build-скриптах

Затронуто:
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`
- `.github/workflows/security-audit.yml` (`release-integrity-policy`)

Суть:
- Сборка может выдать неподписанный артефакт без `gpg` или при `I2PCHAT_SKIP_GPG_SIGN=1`, если явно не задан `I2PCHAT_REQUIRE_GPG=1`.
- CI проверяет наличие signing-токенов в скриптах, но не гарантирует подпись каждого официального релиза.

Влияние:
- На протокол в рантайме не влияет; аутентичность дистрибутива зависит от дисциплины сборки и проверки пользователем.

Рекомендации:
1. В официальных release jobs принудительно `I2PCHAT_REQUIRE_GPG=1`.
2. Падать при сбое detached-signature.
3. Проверка артефактов в CI (например обязательная `.asc`) и понятная инструкция verify для пользователей.

*Статус (2026-03-30): подтверждено; без изменений после package-first и правок build-скриптов.*

---

### [LOW] A-02: Ветвь `__IMG_END__` для inline-image всё ещё требует truthy буфер

Затронуто:
- `i2pchat/core/i2p_chat_core.py` (`receive_loop`, `msg_type == "G"`, `body == "__IMG_END__"`)

Суть:
- Финализация требует и `inline_image_info`, и **truthy** `inline_image_buffer`.

Влияние:
- Обычно fail-closed; хрупкий edge-case и диагностика.

Рекомендации:
1. Обрабатывать `__IMG_END__` при наличии `inline_image_info` независимо от буфера.
2. Единое size-based правило для пустого/непустого буфера.

*Статус (2026-03-30): подтверждено.*

---

### [LOW] A-03: Покрытие CI и опциональные GUI-зависимости

Затронуто:
- `.github/workflows/test-gate.yml` (unittest gate **+** `pytest tests/ -q`)

Статус (относительно более старых ревизий):
- Gate **действительно** прогоняет всё дерево pytest, включая `contact_book`, `compose_drafts`, `notification_prefs`, `send_retry_policy` и др.

Остаточный риск:
- Тесты, которые **skip** без PyQt6 или при непригодном окружении раннера, не дают гарантий по веткам `main_qt` в CI; smoke для Qt остаётся зависимым от среды.

Рекомендации:
1. Расширять headless/Qt-offscreen smoke или отдельный optional job с виртуальным дисплеем.

*Статус (2026-03-30): подтверждено.*

---

### [LOW] A-04: `pip-audit` игнорирует известный CVE

Затронуто:
- `.github/workflows/security-audit.yml`

Суть:
- `--ignore-vuln CVE-2026-4539` для Pygments до фикс-релиза на PyPI.

Влияние:
- Управляемое исключение; временно ослабляет строгий «no known vulns».

Рекомендации:
1. Убрать ignore после появления исправленной версии.
2. Явный срок пересмотра.

*Статус (2026-03-30): подтверждено; прогоны выше использовали тот же ignore для паритета с CI.*

---

### [LOW] A-05: Path allowlist Gitleaks для одного тестового файла

Затронуто:
- `.gitleaks.toml` (allowlist: `tests/test_clear_locked_peer\.py`)

Суть:
- Убирает ложные срабатывания `generic-api-key` на mock первой строки `.dat`. Путь узкий.

Влияние:
- Чуть меньше автоматического внимания к этому файлу; приемлемо, если там только тестовые не-секреты.

Рекомендации:
1. Периодически пересматривать содержимое на новые high-entropy литералы.
2. По возможности избегать паттерна `*KEY* =` в тестах (частично уже: `MOCK_DAT_LINE1`).

*Статус (2026-03-30): подтверждено.*

## Статус закрытия / улучшений относительно прошлых ревизий

Без изменения диспозиции (см. выше):
- **A-01, A-02, A-04**.

Улучшено в более ранних ревизиях (по-прежнему в силе):
- **Ширина test gate**: полный `pytest tests/` в `test-gate.yml`.
- **Скан секретов**: `secret-scan.yml` + gitleaks + `.gitleaks.toml`.
- **Книга контактов**: строгая нормализация адреса / regex хоста, лимит `MAX_CONTACTS`, атомарная запись JSON, миграция v1→v2, тесты `tests/test_contact_book.py`.
- **Lock в UI**: `clear_locked_peer()` + `tests/test_clear_locked_peer.py`; снимок доверия `get_peer_trust_info` + `tests/test_peer_trust_info.py`.

С момента аудита от 2026-03-29 (обслуживание без изменения модели угроз):
- **Package-first**: только импорты под `i2pchat/`; корневые Python-шимы удалены; канонические запуски `python -m i2pchat.gui`, `python -m i2pchat.run_gui`, скрипт PyInstaller `i2pchat/run_gui.py`.
- **Скрипты сборки**: перед PyInstaller выполняется `compileall i2pchat i2plib scripts make_icon.py` на Linux/macOS/Windows — снижает риск отгрузки синтаксически битого дерева; на криптографию и trust boundaries не влияет.

## Подтверждённые сильные стороны

- Handshake с подписанными INIT/RESP и TOFU для persistent-профилей.
- HKDF-разделение ключей сессии, HMAC и строгая монотонность sequence, anti-downgrade.
- Усиленные пути завершения file/inline-image с тестами.
- Зашифрованная per-peer история: SecretBox, atomic writes, привязка peer digest, fail-closed.
- Тесты SAM/ввода, framing, BlindBox client/state.
- Supply-chain job: `i2plib/VENDORED_UPSTREAM.json`, pin `flake.lock`.

## Остаточные операционные риски (по дизайну / явный opt-in)

- Профиль `default` — transient.
- Небезопасный локальный BlindBox — только через явный override и предупреждения.
- Встроенные реплики BlindBox в релизе могут не подходить для строгих privacy-сценариев.
- Файлы **`*.contacts.json`** и **`*.compose_drafts.json`** хранят **незашифрованные** локальные метаданные и черновики в каталоге профилей — защищайте диск/учётную запись; это отдельно от зашифрованной истории чата.

## Заключение

Подтверждённых Critical/High в проверенном снимке нет. Главный открытый пункт — **принудительная подпись релизов** в официальной автоматизации. Остальное — low-severity edge/process и учёт исключений pip-audit/gitleaks. Обновления package-first и build-скриптов **не выявили** новых подтверждённых регрессий безопасности; на машине аудитора для этой ревизии прошли автоматический аудит зависимостей и полные тестовые ворота.
