# I2PChat v0.6.3 — BlindBox and audit-driven security hardening

## EN

### What changed

- **Inbound BlindBox root hardening:** added locked-peer match checks in inbound `BLINDBOX_ROOT` and `BLINDBOX_ROOT_ACK` handlers in `i2pchat/core/i2p_chat_core.py`.
- **Safe behavior on peer mismatch:** when connected peer does not match locked peer, inbound root/root-ack no longer mutates persistent root state; root apply/commit is skipped and an explicit error is emitted.
- **SAM SESSION CREATE path aligned with validators:** `BlindBoxClient.start()` now builds `SESSION CREATE` via `i2plib.sam.session_create(...)` instead of manual string interpolation.
- **Validation now applies to this path too:** invalid SAM options/tokens on BlindBox start are rejected early by shared `i2plib/sam.py` validators.
- **Regression coverage added:** tests now cover inbound root/root-ack peer-lock mismatch behavior and SAM start path validation (including a negative case with unsafe options).
- **Profile path symlink hardening:** profile-scoped paths now reject symlink targets and enforce real-path confinement to the profiles directory.
- **Atomic signing-seed persistence:** fallback write for `*.signing` now uses atomic write helpers, reducing identity continuity risk on crash/power-loss.
- **Handshake role-conflict guard:** receiving `INIT` while a local `INIT` is still pending now triggers a safe disconnect to avoid double-initiator state confusion.
- **BlindBox PUT `EXISTS` verification:** `EXISTS` is counted toward quorum only after data verification (GET comparison), reducing byzantine false-success risk.
- **BlindBox state load consistency:** `_load_blindbox_state()` now uses a single JSON snapshot (single read) for state and root metadata to remove the double-read TOCTOU window.
- **Stronger downgrade diagnostics:** malformed framing errors after handshake are consistently handled as downgrade/protocol violations with explicit disconnect.

### Compatibility

Patch release for `v0.6.x` with no protocol format changes for normal chat flow.

### Security Audit Closure (Round II)

- **Audit status:** full-scope deep audit completed (code, protocol, architecture, persistence/local, supply-chain).
- **Risk snapshot after remediation:** no Critical/High confirmed issues in runtime protocol paths; residual focus is CI/release assurance and operational trust-model items.
- **Targeted verification executed:** updated regression suites for BlindBox root/ACK, SAM create validation path, profile path hardening, atomic signing persistence, handshake role-conflict handling, and malformed-frame downgrade behavior.
- **Backlog update:** CI P0 items are now closed in this cycle:
  - mandatory CI security regression test gate added (`.github/workflows/test-gate.yml`),
  - dependency audit gate now includes `requirements-build.txt` (`security-audit.yml`).
- **Remaining notable backlog (next release):**
  - implement platform-native release signing/notarization and provenance attestations.

---

## RU

### Что исправлено

- **Hardening входящего BlindBox root:** в `i2pchat/core/i2p_chat_core.py` добавлены проверки совпадения с locked peer для входящих `BLINDBOX_ROOT` и `BLINDBOX_ROOT_ACK`.
- **Безопасное поведение при mismatch:** если текущий peer не совпадает с locked peer, входящий root/root-ack больше не меняет persistent-state; применение/коммит root пропускаются с явной ошибкой.
- **Выравнивание SAM SESSION CREATE с валидаторами:** в `BlindBoxClient.start()` ручная сборка `SESSION CREATE` заменена на `i2plib.sam.session_create(...)`.
- **Валидация теперь покрывает и этот путь:** невалидные SAM options/tokens при старте BlindBox отклоняются заранее общими валидаторами из `i2plib/sam.py`.
- **Добавлены регрессионные тесты:** покрыты кейсы peer-lock mismatch для входящих root/root-ack и проверка SAM-валидации в `start()` (включая негативный сценарий с небезопасными options).
- **Hardening profile-path от symlink:** профильные пути теперь отклоняют symlink-цели и дополнительно проверяются через real-path confinement внутри каталога profiles.
- **Атомарная запись signing-seed:** fallback-путь записи `*.signing` переведен на атомарные хелперы, что снижает риск потери continuity identity при crash/power-loss.
- **Guard конфликта ролей handshake:** если входящий `INIT` приходит при ещё ожидаемом локальном `INIT`, сессия безопасно разрывается для исключения state confusion двух инициаторов.
- **Верификация `EXISTS` в BlindBox PUT:** `EXISTS` теперь считается успехом кворума только после проверки содержимого через GET, что снижает риск byzantine ложного успеха.
- **Консистентная загрузка BlindBox state:** `_load_blindbox_state()` читает JSON одним снимком (single read) для state/root metadata, убирая TOCTOU-окно двойного чтения.
- **Усилена диагностика downgrade:** ошибки malformed framing после handshake стабильно трактуются как протокольное нарушение с явным disconnect.

### Совместимость

Patch-релиз для ветки `v0.6.x`, без изменения форматов обычного chat-протокола.

### Закрытие security-аудита (раунд II)

- **Статус аудита:** выполнен повторный full-scope deep-аудит (код, протокол, архитектура, persistence/local, supply-chain).
- **Срез рисков после remediation:** подтверждённых Critical/High уязвимостей в runtime-протокольных путях не выявлено; остаточный фокус смещён в CI/release assurance и операционные элементы trust-model.
- **Выполненная верификация:** расширены и прогнаны регрессионные тесты для BlindBox root/ACK, SAM create validation path, hardening профильных путей, атомарной persistence signing seed, обработки handshake role-conflict и downgrade-поведения при malformed frame.
- **Обновление backlog:** P0-пункты по CI закрыты в этом цикле:
  - добавлен обязательный CI security regression test gate (`.github/workflows/test-gate.yml`),
  - в `security-audit.yml` аудит зависимостей расширен на `requirements-build.txt`.
- **Оставшийся приоритетный backlog (на следующий релиз):**
  - platform-native signing/notarization релизов и provenance attestations.
