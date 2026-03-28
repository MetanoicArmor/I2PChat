# Отчёт по аудиту безопасности: I2PChat

Дата аудита: 2026-03-28  
Состояние репозитория: `0ed6586`  
Режим: полный аудит (протокол + криптография + локальная персистентность + UI-границы + CI/release + supply chain)

## Краткий итог

Проведён полный пересмотр текущего состояния репозитория, включая новый функционал зашифрованной истории чата.

Подтверждённые findings:
- Critical: 0
- High: 0
- Medium: 3
- Low: 3

Общая оценка:
- Протокольные контроли сильные (signed handshake, TOFU-pinning для именованных профилей, строгие seq/HMAC-проверки, downgrade-обработка).
- Новый механизм локальной зашифрованной истории реализован корректно на уровне at-rest защиты (HKDF-derived key + NaCl SecretBox + atomic write).
- Основные остаточные риски: логический edge-case целостности файлов и операционные пробелы в release trust.

## Scope и методология

Проверенные компоненты:
- Протокол/крипто/runtime: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline-подсистема: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI-границы и локальное поведение: `main_qt.py`, `chat_history.py`, `notifications.py`
- Build/release/CI и управление зависимостями: `requirements.txt`, `requirements.in`, `.github/workflows/*`, `build-*.sh`, `build-windows.ps1`

Подход:
- статический анализ trust boundaries и attack surface
- проверка протокольных и криптографических контролей
- таргетные runtime-regression проверки
- анализ supply-chain и release-integrity

Выполненные проверки:
- `python3 -m unittest tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_asyncio_regression tests.test_chat_history tests.test_history_ui_guards -v`
  - Результат: `OK (69 tests)`
- Дополнительные проверки в текущем цикле:
  - history + atomic-write тесты: `OK`
  - smoke-check истории: файл истории содержит `I2CH` magic и не содержит plaintext текста сообщения

## Сводка модели угроз

Рассмотренные нарушители:
- злонамеренный удалённый peer в I2P
- активный манипулятор транспортного пути
- локальный непривилегированный процесс на том же хосте
- supply-chain/distribution атакующий

Хорошо закрытые классы:
- tampering/replay сообщений
- plaintext downgrade после handshake
- desync-атаки сверх ограниченного `resync_limit`

Остаточные классы:
- риск TOFU первого контакта (особенно в transient `default` профиле)
- локальные допущения доверия вокруг SAM и local BlindBox режимов
- аутентичность релиза, завязанная на ручной verify workflow

## Findings (по убыванию критичности)

### [MEDIUM] A-01: Завершение передачи файла (`E`) без строгой финальной проверки размера

Затронуто:
- `i2p_chat_core.py` -> `receive_loop`, ветка `msg_type == "E"`

Суть:
- На маркере конца файла `E` текущая логика завершает приём и шлёт success/ACK без явной проверки `incoming_info.received == incoming_info.size`.
- Ветвь `msg_type == "D"` имеет chunk-bound checks, но строгого финального gate пока нет.

Влияние:
- Злонамеренный аутентифицированный peer (или buggy sender) может завершить поток раньше и добиться принятия усечённого файла как полного.

Рекомендации:
1. В ветке `E` требовать строгого равенства `received == declared size` до success/ACK.
2. При mismatch: emit error, удалить partial file, не отправлять success ACK.

---

### [MEDIUM] A-02: Разрыв доверия между сессиями для transient `default` профиля

Затронуто:
- `i2p_chat_core.py` -> `_ensure_local_signing_key`, `_load_trust_store`, `_save_trust_store`

Суть:
- Для `profile == "default"` signing seed эфемерный, trust-store pinning не персистится.
- Это ожидаемое продуктовое поведение, но оно ослабляет межсессионную continuity доверия.

Влияние:
- Риск TOFU first-contact фактически повторяется на каждом запуске transient-профиля.

Рекомендации:
1. Оставить поведение transient-режима, но явно подсвечивать trade-off в UI/документации.
2. Для пользователей с требованиями к устойчивому доверию рекомендовать именованные профили.

---

### [MEDIUM] A-03: В release-цепочке нет принудительной platform-native trust-интеграции

Затронуто:
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`
- release-policy проверки в `.github/workflows/security-audit.yml`

Суть:
- Checksums и detached signatures есть, но нет enforced platform-native signing/notarization в automation.

Влияние:
- Качество верификации релизов зависит от ручной дисциплины пользователя; остаётся операционный риск для массовой дистрибуции.

Рекомендации:
1. Добавить platform-native signing/notarization workflows.
2. Добавить provenance attestations для release-артефактов.
3. Опубликовать строгую user-facing политику верификации.

---

### [LOW] A-04: Семантика `legacy_compat` может путать операторов

Затронуто:
- `i2p_chat_core.py` (`ProtocolCodec(..., allow_legacy=False)` путь)
- env/UI-поверхность в `main_qt.py`

Суть:
- Ожидание от флага и фактическое поведение кодека могут расходиться, что создаёт риск operator confusion.

Рекомендация:
1. Либо корректно связать поведение end-to-end, либо удалить/переименовать флаг.

---

### [LOW] A-05: Insecure local BlindBox режим всё ещё доступен по явному override

Затронуто:
- `i2p_chat_core.py` (`I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL`, direct/local policy)
- `blindbox_local_replica.py`

Суть:
- Ослабленный локальный режим может быть намеренно включён.

Рекомендации:
1. Сохранять явные warning-сигналы в telemetry/UI.
2. Для hardened deployment использовать строгую политику (`I2PCHAT_BLINDBOX_REQUIRE_SAM=1` и tokenized local access).

---

### [LOW] A-06: Артефакт secret-scan и checksum берутся из одного trust-источника

Затронуто:
- `.github/workflows/secret-scan.yml`

Суть:
- Архив инструмента и его checksum скачиваются из одного upstream.

Рекомендация:
1. Где возможно, использовать detached signatures или независимый provenance trust root.

## Подтверждённые сильные стороны

- Signed handshake и continuity-модель peer-ключа для именованных профилей:
  - `i2p_chat_core.py` (`_handle_handshake_message`, `_pin_or_verify_peer_signing_key`)
- Сильные framing/integrity контроли:
  - `protocol_codec.py` (vNext framing + bounded resync)
  - `crypto.py` (context-bound HMAC: `seq`, `flags`, `msg_id`)
- Anti-downgrade и replay/reorder защита:
  - strict encrypted-frame expectations после handshake
  - sequence monotonicity checks
- Path confinement и безопасная персистентность:
  - profile-scoped path checks + atomic writes в `blindbox_state.py`
- Supply-chain hygiene:
  - hash-pinned install paths в CI
  - pinned action refs, выделенные test/audit workflow

## Оценка безопасности новой истории чата

Статус: прямых криптографических или логических пробоев не подтверждено.

Проверенные свойства:
- изоляция файлов истории по peer (`<profile>.history.<peer_hash>.enc`)
- отсутствие plaintext payload в сохранённом history-файле
- корректный fail на wrong key/corrupt file
- строгие guard-условия ON/OFF в capture-пути
- корректный save/reset lifecycle при disconnect/close (покрыто тестами)

Остаточное замечание:
- как и другие локальные данные, защита зависит от модели компрометации хоста.

## Пробелы и рекомендации по тестам

Сильное текущее покрытие есть для:
- vNext framing integrity и downgrade-поведения
- SAM input validation
- async-regression сценариев handshake/BlindBox
- encrypted history и UI-guard семантики

Рекомендуемые дополнительные тесты:
1. явный regression-тест строгой проверки размера в `msg_type == "E"`
2. интеграционный тест на end-of-file mismatch (cleanup + no ACK)
3. расширенные проверки release policy в CI (artifact signing/notarization requirements)

## Приоритет remediation

1. **P1:** внедрить строгую финальную проверку целостности файла в `msg_type == "E"` (A-01).
2. **P1:** усилить release trust chain (platform-native signing/notarization + provenance) (A-03).
3. **P2:** привести `legacy_compat` к однозначной семантике (A-04).
4. **P2:** продолжить hardening-документацию по transient trust и local BlindBox override (A-02, A-05).
5. **P3:** усилить независимость trust-источника для secret-scan tooling verify (A-06).

## Заключение

В текущем состоянии репозитория подтверждённых Critical/High уязвимостей не выявлено. Протокольные и криптографические контроли в целом сильные; основной практический фокус — один edge-case целостности file-transfer и hardening release/distribution trust.
