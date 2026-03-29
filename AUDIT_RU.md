# Отчёт по аудиту безопасности: I2PChat

Дата аудита: 2026-03-29  
Состояние репозитория: `fdb0211`  
Режим: полный аудит (протокол + криптография + локальная персистентность + UI + CI/release + supply chain + secret scan)

## Краткий итог

Полная пересмотренная ревизия аудита после доработок **contact book / Saved peers**, корректировок release-процесса и усилений CI на ветке `main`.

Подтверждённые findings:
- Critical: 0
- High: 0
- Medium: 1
- Low: 4

Общая оценка:
- Базовые контроли защищённого канала по-прежнему сильные (signed handshake, HKDF, HMAC + sequence, anti-downgrade).
- Исправления по истории чата и inline-image из прошлых аудитов на месте, с регрессионными тестами.
- **Test gate** в CI теперь дополнительно гоняет весь набор **`pytest tests/`** поверх фиксированного списка unittest — покрытие хелперов (контакты, черновики, уведомления, маршрутизация send) выше.
- **Gitleaks** выполняется на push/PR; в корне репозитория **`.gitleaks.toml`** фиксирует один path-allowlist для тестовой фикстуры (см. A-05).
- Оставшиеся риски в основном **релиз/процесс** и **edge-case** hardening, а не поломка протокола.

## Scope и методология

Проверенные компоненты:
- Протокол/runtime/криптография: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI/локальное хранение: `main_qt.py`, `chat_history.py`, `contact_book.py`, `compose_drafts.py`, `notification_prefs.py`, `unread_counters.py`
- CI/release/supply-chain: `.github/workflows/*` (test-gate, security-audit, secret-scan), `build-*.sh` / `build-windows.ps1`, `requirements*.txt`, `flake.lock`, `.gitleaks.toml`

Выполненные проверки:
- `python -m unittest tests.test_blindbox_state_wrap tests.test_asyncio_regression tests.test_blindbox_client tests.test_atomic_writes tests.test_chat_history tests.test_history_ui_guards tests.test_profile_import_overwrite tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_audit_remediation`
  - Результат: **OK (120 tests)** (на машине аудитора)
- Ручной обзор: trust boundaries, семантика BlindBox/lock, валидация contact JSON, новые GUI-пути (Saved peers, диалоги), политика secret-scan.

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

---

### [LOW] A-02: Ветвь `__IMG_END__` для inline-image всё ещё требует truthy буфер

Затронуто:
- `i2p_chat_core.py` (`receive_loop`, `msg_type == "G"`, `body == "__IMG_END__"`)

Суть:
- Финализация требует и `inline_image_info`, и **truthy** `inline_image_buffer`.

Влияние:
- Обычно fail-closed; хрупкий edge-case и диагностика.

Рекомендации:
1. Обрабатывать `__IMG_END__` при наличии `inline_image_info` независимо от буфера.
2. Единое size-based правило для пустого/непустого буфера.

---

### [LOW] A-03: Покрытие CI и опциональные GUI-зависимости

Затронуто:
- `.github/workflows/test-gate.yml` (сейчас: unittest gate **+** `pytest tests/ -q`)

Статус (улучшение относительно прошлой ревизии):
- Gate **действительно** прогоняет всё дерево pytest, включая `contact_book`, `compose_drafts`, `notification_prefs`, `send_retry_policy` и др.

Остаточный риск:
- Тесты, которые **skip** без PyQt6 или при непригодном окружении раннера, не дают гарантий по веткам `main_qt` в CI; smoke для Qt остаётся зависимым от среды.

Рекомендации:
1. Расширять headless/Qt-offscreen smoke или отдельный optional job с виртуальным дисплеем.

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

## Статус закрытия / улучшений относительно прошлой ревизии

Без изменения статуса (см. выше):
- **A-01, A-02, A-04**.

Улучшено:
- **Ширина test gate**: полный `pytest tests/` в `test-gate.yml`.
- **Скан секретов**: `secret-scan.yml` + gitleaks + `.gitleaks.toml`.
- **Книга контактов**: строгая нормализация адреса / regex хоста, лимит `MAX_CONTACTS`, атомарная запись JSON, миграция v1→v2, тесты `tests/test_contact_book.py`.
- **Lock в UI**: `clear_locked_peer()` + `tests/test_clear_locked_peer.py`; снимок доверия `get_peer_trust_info` + `tests/test_peer_trust_info.py`.

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

Подтверждённых Critical/High в проверенном снимке нет. Главный открытый пункт — **принудительная подпись релизов** в официальной автоматизации. Остальное — low-severity edge/process и учёт исключений pip-audit/gitleaks. Позиция CI по тестам и secret-scan **лучше**, чем в предыдущей ревизии отчёта.
