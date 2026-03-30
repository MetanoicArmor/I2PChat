# I2PChat v0.5.2 — security remediation from audit

## EN

### Summary

`v0.5.2` is a security-focused release that implements and extends the audit recommendations (F‑01..F‑04):

- hardens identity binding in the handshake;
- protects profile storage from path traversal;
- prevents silent overwrite of incoming files;
- pins dependencies and adds CI-based dependency auditing;
- adds post-audit protocol and supply-chain hardening.

### Key changes

#### 1) Identity binding hardening (F‑01, High)

- Verifies peer address ↔ destination binding via SAM lookup in `i2pchat/core/i2p_chat_core.py`.
- Stops trusting self-asserted network identity as a standalone security source.
- Adds fail-closed behavior on `S` identity mismatch against the expected peer address.
- Tracks a verified-binding state via `is_current_peer_verified_for_lock()`.
- In `i2pchat/gui/main_qt.py`, `Lock to peer` is allowed only after:
  - a completed secure handshake, and
  - cryptographically verified identity binding.
- TOFU dialog explicitly warns that identity is not out-of-band verified.

#### 2) Profile path hardening (F‑02, Medium)

- Introduces a strict profile-name whitelist in `i2pchat/core/i2p_chat_core.py`:
  - `^[A-Za-z0-9._-]{1,64}$`.
- Adds `is_valid_profile_name()` / `ensure_valid_profile_name()` with fail-closed behavior.
- Enforces confined profile paths for:
  - `.dat`;
  - `.trust.json`;
  - `.signing`.
- Adds profile-name validation in `i2pchat/gui/main_qt.py` for:
  - CLI arguments;
  - profile selection dialog;
  - `Load .dat` / `switch_profile`.

#### 3) Safe handling of incoming file name collisions (F‑03, Medium)

- Adds `allocate_unique_filename(...)` in `i2pchat/core/i2p_chat_core.py`.
- Incoming file handling (`msg_type == "F"`) now uses deterministic renaming:
  - `name.ext`;
  - `name (1).ext`;
  - `name (2).ext`; etc.
- Uses `open(..., "xb")` to avoid accidental overwrite.
- Emits a system message describing the collision and final file name.

#### 4) Dependency hygiene and CI audit (F‑04, Low)

- Adds `requirements.in`.
- Converts `requirements.txt` to pinned/locked output via `pip-tools`.
- Adds a dedicated security workflow:
  - `.github/workflows/security-audit.yml`;
  - runs `pip-audit` on `push`, `pull_request`, `schedule`, and `workflow_dispatch`.

#### 5) Tests and documentation

- Adds security/regression tests:
  - `tests/test_asyncio_regression.py`;
  - `tests/test_protocol_framing_vnext.py`;
  - `tests/test_profile_import_overwrite.py`.
- Covers scenarios:
  - invalid profile-name rejection;
  - profile path confinement;
  - lock gating on verified identity binding;
  - collision-safe saving of incoming files.
- Updates documentation:
  - `docs/MANUAL_EN.md`;
  - `docs/MANUAL_RU.md`;
  - this `RELEASE_0.5.2.md`.

#### 6) F‑01/F‑02 protocol hardening (medium, 2026‑03‑17)

- **F‑01 fixed:** MAC for encrypted vNext frames now covers  
  `msg_type + seq + flags + msg_id + encrypted_body`.
- **Breaking change:** clients using the old MAC semantics are not wire-compatible with updated peers.
- Adds negative header-tampering tests (`MSG_ID/FLAGS`) in `tests/test_protocol_framing_vnext.py`.
- **F‑02 fixed:** `.dat` import no longer silently overwrites an existing profile;  
  collisions are imported under a safe, unique name (`name_1`, `name_2`, ...).
- Adds unique profile-name allocation and tests in `tests/test_profile_import_overwrite.py`.

#### 7) Post-audit protocol and supply-chain hardening

- Strengthens handshake key derivation:
  - introduces HKDF over the DH shared secret and both nonces;
  - splits session material into `k_enc` (encryption) and `k_mac` (MAC), removing the single-key pattern.
- Adds an encrypted payload padding profile:
  - default `balanced` (padding to 128-byte buckets);
  - optional `off` via `I2PCHAT_PADDING_PROFILE=off` for reduced overhead;
  - threat model and metadata leakage (TYPE/LEN + identity preface) are explicitly documented in `README` and manuals.
- Formalises vendored `i2plib` governance:
  - adds machine-readable provenance file `i2plib/VENDORED_UPSTREAM.json`;
  - adds `docs/VENDORED_I2PLIB_POLICY.md` describing review cadence and advisory sources;
  - wires supply-chain policy checks into `.github/workflows/security-audit.yml` (provenance + `flake.lock` sanity).
- Improves Nix-based reproducibility:
  - adds `flake.lock` pinning `nixpkgs` and `flake-utils`;
  - relies on the lockfile instead of any `nixos-unstable` channel override in CI.
- Makes release signing friendlier for local builds:
  - build scripts still produce `SHA256SUMS` and, when GPG is available, `SHA256SUMS.asc`;
  - adds environment flags `I2PCHAT_SKIP_GPG_SIGN`, `I2PCHAT_REQUIRE_GPG`, and `I2PCHAT_GPG_KEY_ID`.

### Verification

- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py tests/test_profile_import_overwrite.py` — `OK`
- No linter issues in modified files.

---

## RU

### Кратко

`v0.5.2` — релиз с упором на безопасность, который реализует и расширяет рекомендации аудита (F‑01..F‑04):

- усиливает привязку личности (identity binding) в handshake;
- защищает профильное хранилище от path traversal;
- исключает тихую перезапись входящих файлов;
- фиксирует зависимости и добавляет автоматический dependency‑audit в CI;
- добавляет дополнительные протокольные и supply‑chain‑усиления после аудита.

### Основные изменения

#### 1) Усиление identity binding (F‑01, High)

- В `i2pchat/core/i2p_chat_core.py` добавлена проверка соответствия адреса пира и destination через SAM‑lookup.
- Self-asserted identity из сети больше не используется как самостоятельный источник доверия.
- Для `S`‑identity введён fail‑closed при несоответствии ожидаемого и полученного peer address.
- Добавлен признак зафиксированного binding и метод `is_current_peer_verified_for_lock()`.
- В `i2pchat/gui/main_qt.py` `Lock to peer` теперь доступен только после:
  - завершённого secure handshake;
  - криптографически подтверждённого identity binding.
- TOFU‑диалог явно предупреждает, что личность не подтверждена out‑of‑band.

#### 2) Защита профилей от path traversal (F‑02, Medium)

- В `i2pchat/core/i2p_chat_core.py` введён whitelist имён профилей:
  - `^[A-Za-z0-9._-]{1,64}$`.
- Добавлены `is_valid_profile_name()` / `ensure_valid_profile_name()` с fail‑closed‑поведением.
- Добавлен строгий path confinement для профильных файлов:
  - `.dat`;
  - `.trust.json`;
  - `.signing`.
- В `i2pchat/gui/main_qt.py` добавлена валидация имени профиля для:
  - CLI‑аргумента;
  - диалога выбора профиля;
  - действий `Load .dat` / `switch_profile`.

#### 3) Безопасные коллизии имён входящих файлов (F‑03, Medium)

- В `i2pchat/core/i2p_chat_core.py` реализован `allocate_unique_filename(...)`.
- При приёме файлов (`msg_type == "F"`) используется детерминированная уникализация:
  - `name.ext`;
  - `name (1).ext`;
  - `name (2).ext`; и т.д.
- Запись ведётся через `open(..., "xb")`, что исключает случайное overwrite.
- В лог/GUI выводится системное сообщение о коллизии и итоговом имени файла.

#### 4) Гигиена зависимостей и CI‑аудит (F‑04, Low)

- Добавлен `requirements.in`.
- `requirements.txt` переведён в pinned/locked‑формат с помощью `pip-tools`.
- Добавлен отдельный security‑workflow:
  - `.github/workflows/security-audit.yml`;
  - запуск `pip-audit` на `push`, `pull_request`, `schedule` и `workflow_dispatch`.

#### 5) Тесты и документация

- Добавлены security/regression‑тесты:
  - `tests/test_asyncio_regression.py`;
  - `tests/test_protocol_framing_vnext.py`;
  - `tests/test_profile_import_overwrite.py`.
- Покрыты сценарии:
  - отклонение невалидных имён профилей;
  - path confinement для профильных путей;
  - блокирование `Lock to peer` без подтверждённого identity binding;
  - отсутствие overwrite при коллизии имён входящих файлов.
- Обновлена документация:
  - `docs/MANUAL_EN.md`;
  - `docs/MANUAL_RU.md`;
  - этот файл `RELEASE_0.5.2.md`.

#### 6) Усиление протокола по пунктам F‑01/F‑02 (medium, 2026‑03‑17)

- **F‑01 исправлен:** MAC для зашифрованных vNext‑кадров теперь покрывает  
  `msg_type + seq + flags + msg_id + encrypted_body`.
- **Breaking change:** клиенты со старой семантикой MAC протокольно несовместимы с обновлёнными узлами.
- Добавлены негативные тесты tamper заголовка (`MSG_ID/FLAGS`) в `tests/test_protocol_framing_vnext.py`.
- **F‑02 исправлен:** импорт `.dat` больше не делает silent overwrite существующего профиля;  
  при коллизии создаётся новое безопасное имя (`name_1`, `name_2`, ...).
- Добавлена уникализация имён профилей и тесты в `tests/test_profile_import_overwrite.py`.

#### 7) Дополнительный hardening протокола и цепочки поставки

- Усилен вывод ключей handshake:
  - введён HKDF поверх DH‑секрета и пары nonce‑ов;
  - материал сессии разделён на `k_enc` (шифрование) и `k_mac` (MAC), убран паттерн «один ключ на всё».
- Добавлен padding‑профиль для зашифрованных payload:
  - по умолчанию `balanced` (выравнивание до 128‑байтных блоков);
  - опция `off` через `I2PCHAT_PADDING_PROFILE=off` для уменьшения накладных расходов;
  - в `README` и мануалах явно задокументированы остаточные утечки метаданных (`TYPE/LEN` + identity preface).
- Формализован governance для vendored `i2plib`:
  - добавлен machine‑readable provenance‑файл `i2plib/VENDORED_UPSTREAM.json`;
  - добавлен документ `docs/VENDORED_I2PLIB_POLICY.md` с описанием ревизий и источников security‑advisories;
  - в `.github/workflows/security-audit.yml` добавлены supply‑chain‑проверки (provenance + `flake.lock`).
- Улучшена reproducibility для Nix:
  - добавлен `flake.lock` с pinned `nixpkgs` и `flake-utils`;
  - CI опирается на lock‑файл и не использует `nixos-unstable` channel override.
- Подпись релизных артефактов стала дружественнее к локальным сборкам:
  - build‑скрипты по-прежнему создают `SHA256SUMS` и, при наличии GPG, `SHA256SUMS.asc`;
  - добавлены переменные окружения `I2PCHAT_SKIP_GPG_SIGN`, `I2PCHAT_REQUIRE_GPG`, `I2PCHAT_GPG_KEY_ID`.

### Проверка

- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py tests/test_profile_import_overwrite.py` — `OK`
- Линтер‑диагностика изменённых файлов — без ошибок.


