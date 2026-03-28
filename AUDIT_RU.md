# Отчёт по аудиту безопасности: I2PChat

Дата аудита: 2026-03-28  
Состояние репозитория: `d4ecd11`  
Режим: полный аудит (протокол + криптография + локальная персистентность + UI + CI/release + supply chain)

## Краткий итог

Это новый пост-ремедиационный аудит после последних исправлений.

Подтверждённые findings:
- Critical: 0
- High: 0
- Medium: 1
- Low: 3

Общая оценка:
- Базовые контроли защищённого канала сильные (signed handshake, HKDF-разделение ключей, HMAC+seq целостность, anti-downgrade).
- Ранее открытые проблемы истории и inline-image завершения закрыты и покрыты тестами.
- Оставшиеся риски в основном операционно-процессные, а не криптографический пробой протокола.

## Scope и методология

Проверенные компоненты:
- Протокол/runtime/криптография: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline-подсистема: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI/локальное хранение: `main_qt.py`, `chat_history.py`
- CI/release/supply-chain: `.github/workflows/*`, `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, lockfiles

Выполненные проверки:
- `python3 -m unittest tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_asyncio_regression tests.test_chat_history tests.test_history_ui_guards tests.test_audit_remediation -v`
  - Результат: `OK (90 tests)`
- Ручной code review trust boundaries, dataflow, локальной персистентности и release policy.

## Findings (текущее состояние)

### [MEDIUM] A-01: Подпись релизов всё ещё необязательна по умолчанию в build-скриптах

Затронуто:
- `build-linux.sh`
- `build-macos.sh`
- `build-windows.ps1`
- `.github/workflows/security-audit.yml` (`release-integrity-policy`)

Суть:
- Скрипты сборки всё ещё могут выпускать неподписанный релиз, если нет `gpg` или используется `I2PCHAT_SKIP_GPG_SIGN=1` (если явно не включён `I2PCHAT_REQUIRE_GPG=1`).
- CI-политика проверяет наличие signing-токенов в скриптах, но не проверяет факт обязательной подписи/notarization артефактов официального релиза.

Влияние:
- На безопасность runtime-протокола это напрямую не влияет, но гарантия аутентичности дистрибутива всё ещё зависит от дисциплины сборщика и проверки пользователем.

Рекомендации:
1. В официальных release jobs принудительно включить `I2PCHAT_REQUIRE_GPG=1`.
2. Падать при любой неудаче detached-signature.
3. Добавить artifact-level проверку в CI (например, обязательная валидация `.asc` для produced artifacts) и чёткий user-facing verify процесс.

---

### [LOW] A-02: Ветвь `__IMG_END__` для inline-image всё ещё завязана на truthy буфер

Затронуто:
- `i2p_chat_core.py` (`receive_loop`, `msg_type == "G"`, `body == "__IMG_END__"`)

Суть:
- Финализация сейчас требует одновременно truthy `self.inline_image_info` и truthy `self.inline_image_buffer`.
- В edge-case, когда metadata активна, а буфер пуст, обработка уходит в другой error path, а не в детерминированную финализацию.

Влияние:
- В типичных атакующих сценариях это остаётся fail-closed, но поведение хрупкое и может давать неоднозначную диагностику.

Рекомендации:
1. Обрабатывать `__IMG_END__` при наличии `inline_image_info` независимо от truthy буфера.
2. Применять единое детерминированное size-based правило финализации для пустого и непустого буфера.

---

### [LOW] A-03: Основной test gate всё ещё не покрывает часть security-relevant модулей

Затронуто:
- `.github/workflows/test-gate.yml`

Суть:
- Покрытие gate уже расширено (history/audit добавлены), но часть модулей остаётся вне default gate.

Влияние:
- Регрессии в негейтируемых suite могут пройти основной check, если в том же PR-потоке нет более широкого прогона.

Рекомендации:
1. Добавить второй обязательный job “full unittest security” или дальше расширить основной gate.

---

### [LOW] A-04: `pip-audit` пока игнорирует один известный vulnerability ID

Затронуто:
- `.github/workflows/security-audit.yml`

Суть:
- В workflow используется `--ignore-vuln CVE-2026-4539` для Pygments до выхода upstream-fixed релиза.

Влияние:
- Это управляемое исключение, но оно временно ослабляет строгую гарантию “no known vulns”.

Рекомендации:
1. Снять ignore сразу после доступности фикс-версии.
2. Вести это исключение с явным сроком пересмотра.

## Статус закрытия прошлых пунктов

Ранее открытые проблемы, закрытые в текущем коде:
- Inline image strict end-size integrity и no-ACK-on-mismatch.
- Полный SHA-256 peer identifier в имени history-файла.
- Проверка `peer` после decrypt истории.
- Логирование/показ ошибок сохранения истории в GUI.
- Добавление `test_chat_history`, `test_history_ui_guards`, `test_audit_remediation` в основной test gate.

## Подтверждённые сильные стороны

- Надёжный handshake с подписанными контекстно-связанными INIT/RESP payloads и TOFU-pinning для persistent-профилей.
- HKDF-разделение session keys (`k_enc`/`k_mac`) и строгий HMAC-контроль с привязкой к метаданным сообщения.
- Anti-downgrade после handshake и строгая монотонность sequence numbers.
- Усиленная целостность завершения file и inline-image передач (плюс regression-покрытие).
- Корректно усиленная локальная зашифрованная история:
  - per-peer хранение
  - SecretBox at-rest
  - atomic writes
  - fail-closed при corruption/wrong key/peer mismatch
- Улучшенное CI-покрытие относительно прошлой ревизии аудита.

## Остаточные операционные риски (по дизайну / явный opt-in)

- `default` профиль остаётся transient по дизайну (TOFU continuity не переживает рестарт).
- Insecure local BlindBox режим по-прежнему возможен только как явный override и warning path.
- Built-in BlindBox replicas релиза могут быть не оптимальны для строгих privacy deployments; для hardened-конфигураций лучше задавать свои реплики.

## Заключение

Подтверждённых Critical/High уязвимостей в текущем коде не выявлено. Главный оставшийся пункт — принудительное обеспечение release-signing в официальной automation. Остальные активные пункты — low-severity edge/process hardening.
