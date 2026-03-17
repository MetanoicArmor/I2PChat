# I2PChat v0.5.2 — security remediation from audit

## RU

### Контекст

`v0.5.2` закрывает рекомендации security-аудита (`AUDIT.md`) по приоритетам F-01..F-04:

- устранение identity misbinding в handshake;
- защита профилей от path traversal;
- отказ от тихой перезаписи входящих файлов;
- фиксация зависимостей и добавление dependency-audit в CI.

### Что реализовано

#### 1) F-01: Identity binding hardening (High)

- В `i2p_chat_core.py` добавлена проверка binding адреса пира к destination через SAM lookup.
- Self-asserted identity из сети больше не используется как самостоятельный источник доверия.
- Для `S`-identity добавлен fail-closed при несоответствии ожидаемого/полученного peer address.
- Добавлен признак verified binding и метод `is_current_peer_verified_for_lock()`.
- В `main_qt.py` `Lock to peer` разрешён только после:
  - завершённого secure handshake;
  - подтверждённого cryptographic identity binding.
- TOFU-диалог дополнен явным предупреждением:
  - без OOB-подтверждения личность не считается подтверждённой.

#### 2) F-02: Path traversal через profile name (Medium)

- В `i2p_chat_core.py` введён whitelist профиля:
  - `^[A-Za-z0-9._-]{1,64}$`.
- Добавлены `is_valid_profile_name()` / `ensure_valid_profile_name()` и fail-closed поведение.
- Добавлен path confinement для профильных путей:
  - `.dat`;
  - `.trust.json`;
  - `.signing`.
- В `main_qt.py` добавлена валидация profile name для:
  - CLI-аргумента;
  - диалога выбора профиля;
  - `Load .dat` / `switch_profile`.

#### 3) F-03: Безопасная обработка коллизий входящих файлов (Medium)

- В `i2p_chat_core.py` добавлен `allocate_unique_filename(...)`.
- При приёме файлов (`msg_type == "F"`) реализована deterministic-уникализация:
  - `name.ext`;
  - `name (1).ext`;
  - `name (2).ext`; и т.д.
- Запись выполняется через `open(..., "xb")` для исключения overwrite.
- Добавлено системное уведомление о коллизии имени и итоговом имени файла.

#### 4) F-04: Dependency hygiene (Low)

- Добавлен `requirements.in`.
- `requirements.txt` переведён в pinned/locked вид (`pip-tools`).
- Добавлен CI workflow:
  - `.github/workflows/security-audit.yml`;
  - запуск `pip-audit` на `push`, `pull_request`, `schedule`, `workflow_dispatch`.

#### 5) Тесты и документация

- Добавлены security/regression тесты:
  - `tests/test_asyncio_regression.py`;
  - `tests/test_protocol_framing_vnext.py`.
- Покрыты сценарии:
  - reject невалидного profile name;
  - path confinement для профильных путей;
  - lock-gating по verified identity binding;
  - отсутствие overwrite при коллизии имён входящих файлов.
- Обновлена документация:
  - `docs/MANUAL_RU.md`;
  - `docs/MANUAL_EN.md`;
  - `RELEASE_0.5.2.md` (дополнен блоками remediation).

#### 6) F-01/F-02 remediation (medium, 2026-03-17)

- **F-01 fixed:** MAC в encrypted vNext-кадрах теперь включает `msg_type + seq + flags + msg_id + encrypted_body`.
- **Breaking change:** старые клиенты с прежней MAC-семантикой несовместимы с обновлёнными узлами.
- Добавлены негативные тесты tamper заголовка (`MSG_ID/FLAGS`) в `tests/test_protocol_framing_vnext.py`.
- **F-02 fixed:** импорт `.dat` больше не делает silent overwrite существующего профиля; при коллизии используется безопасное имя (`name_1`, `name_2`, ...).
- Добавлена уникализация имени профиля и тесты в `tests/test_profile_import_overwrite.py`.

### Дополнительные изменения после последующих аудитов

- Усилен handshake-ключ:
  - введён HKDF поверх DH shared secret и nonce-ов;
  - ключ разделён на `k_enc` (шифрование) и `k_mac` (MAC), MAC больше не использует тот же материал, что и шифрование.
- Добавлен padding-профиль для зашифрованных payload:
  - по умолчанию `balanced` (выравнивание до 128-байтных блоков);
  - опция `off` через `I2PCHAT_PADDING_PROFILE=off` для минимизации накладных расходов;
  - в MANUAL/README задокументированы остаточные утечки метаданных (`TYPE/LEN` и preface) и trade-off padding.
- Формализован governance для vendored `i2plib`:
  - добавлен machine-readable provenance-файл `i2plib/VENDORED_UPSTREAM.json`;
  - добавлен документ `docs/VENDORED_I2PLIB_POLICY.md` с политикой ревизий и источниками security-advisories;
  - в CI добавлен supply-chain policy-check, который валидирует provenance и наличие корректного `flake.lock`.
- Улучшен Nix reproducibility:
  - добавлен `flake.lock` с pinned `nixpkgs`/`flake-utils`;
  - CI-проверки полагаются на lock-файл и не используют channel override `nixos-unstable`.
- Подпись релизных артефактов стала более дружелюбной к локальной сборке:
  - скрипты по-прежнему генерируют `SHA256SUMS` и, при наличии GPG, `SHA256SUMS.asc`;
  - добавлены переключатели `I2PCHAT_SKIP_GPG_SIGN=1` / `I2PCHAT_REQUIRE_GPG=1` и `I2PCHAT_GPG_KEY_ID`, чтобы можно было собирать без обязательной настройки ключей.

### Проверка

- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py` — `OK`
- Линтер-диагностика изменённых файлов — без ошибок.

### Итог

`v0.5.2` закрывает критичные и средние риски из аудита и переводит проект на более строгую security-модель:

- identity binding подтверждается криптографически и через SAM;
- profile storage защищён от traversal/path injection;
- входящие файлы сохраняются без неявной перезаписи;
- зависимости зафиксированы, а security-audit зависимостей автоматизирован в CI.

---

## EN

### Context

`v0.5.2` implements security-audit remediation items (`AUDIT.md`) for F-01..F-04:

- identity misbinding mitigation in handshake;
- profile path traversal protection;
- no silent overwrite for incoming files;
- locked dependencies with CI dependency auditing.

### Implemented

#### 1) F-01: Identity binding hardening (High)

- Added peer-address-to-destination binding verification via SAM lookup in `i2p_chat_core.py`.
- Self-asserted network identity is no longer trusted as a standalone security source.
- Added fail-closed behavior for `S` identity mismatch against expected peer address.
- Added verified-binding state and `is_current_peer_verified_for_lock()`.
- In `main_qt.py`, `Lock to peer` now requires:
  - completed secure handshake;
  - cryptographically verified identity binding.
- TOFU dialog now explicitly warns that identity is not OOB-verified.

#### 2) F-02: Path traversal via profile name (Medium)

- Introduced profile-name whitelist in `i2p_chat_core.py`:
  - `^[A-Za-z0-9._-]{1,64}$`.
- Added `is_valid_profile_name()` / `ensure_valid_profile_name()` with fail-closed behavior.
- Enforced profile path confinement for:
  - `.dat`;
  - `.trust.json`;
  - `.signing`.
- Added profile-name validation in `main_qt.py` for:
  - CLI argument;
  - profile selection dialog;
  - `Load .dat` / `switch_profile`.

#### 3) F-03: Safe incoming filename collision handling (Medium)

- Added `allocate_unique_filename(...)` in `i2p_chat_core.py`.
- Incoming file handling (`msg_type == "F"`) now uses deterministic renaming:
  - `name.ext`;
  - `name (1).ext`;
  - `name (2).ext`; etc.
- Switched file creation to `open(..., "xb")` to prevent overwrite.
- Added system message for collision and final resolved filename.

#### 4) F-04: Dependency hygiene (Low)

- Added `requirements.in`.
- Converted `requirements.txt` to pinned/locked output via `pip-tools`.
- Added CI workflow:
  - `.github/workflows/security-audit.yml`;
  - runs `pip-audit` on `push`, `pull_request`, `schedule`, `workflow_dispatch`.

#### 5) Tests and docs

- Added security/regression tests in:
  - `tests/test_asyncio_regression.py`;
  - `tests/test_protocol_framing_vnext.py`.
- Covered scenarios:
  - invalid profile-name rejection;
  - profile path confinement;
  - lock gating on verified identity binding;
  - collision-safe incoming file saving without overwrite.
- Updated docs:
  - `docs/MANUAL_RU.md`;
  - `docs/MANUAL_EN.md`;
  - `RELEASE_0.5.2.md` (remediation notes).

#### 6) F-01/F-02 remediation (medium, 2026-03-17)

- **F-01 fixed:** MAC for encrypted vNext frames now covers `msg_type + seq + flags + msg_id + encrypted_body`.
- **Breaking change:** clients using the previous MAC semantics are not wire-compatible with updated peers.
- Added negative header-tampering tests (`MSG_ID/FLAGS`) in `tests/test_protocol_framing_vnext.py`.
- **F-02 fixed:** `.dat` import no longer silently overwrites an existing profile; collisions are imported as a new safe name (`name_1`, `name_2`, ...).
- Added unique profile-name allocation and tests in `tests/test_profile_import_overwrite.py`.

#### 7) Post-audit hardening (HKDF, padding, governance)

- Strengthened handshake key derivation:
  - introduced HKDF over the DH shared secret and both nonces;
  - split session material into `k_enc` (encryption) and `k_mac` (MAC), removing the single-key pattern.
- Added an encrypted payload padding profile:
  - default `balanced` (padding to 128-byte buckets);
  - optional `off` via `I2PCHAT_PADDING_PROFILE=off` for reduced overhead;
  - threat model and metadata leakage (TYPE/LEN + identity preface) are explicitly documented in README and manuals.
- Formalised vendored `i2plib` governance:
  - added machine-readable provenance file `i2plib/VENDORED_UPSTREAM.json`;
  - added `docs/VENDORED_I2PLIB_POLICY.md` describing review cadence and advisory sources;
  - wired supply-chain policy checks into `.github/workflows/security-audit.yml` (provenance + `flake.lock` sanity).
- Improved Nix-based reproducibility:
  - added `flake.lock` pinning `nixpkgs` and `flake-utils`;
  - relied on the lockfile instead of a drifting `nixos-unstable` channel override in CI.
- Made release signing friendlier for local builds:
  - build scripts still produce `SHA256SUMS` and, when GPG is available, `SHA256SUMS.asc`;
  - added environment flags `I2PCHAT_SKIP_GPG_SIGN` / `I2PCHAT_REQUIRE_GPG` and `I2PCHAT_GPG_KEY_ID` to control signer and strictness.

### Verification

- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py` — `OK`
- No linter issues in modified files.

### Summary

`v0.5.2` closes high/medium audit risks and strengthens the security baseline:

- identity binding is cryptographically validated with SAM support;
- profile storage is traversal/path-injection safe;
- incoming files are saved without implicit overwrite;
- dependencies are pinned and continuously audited in CI.

