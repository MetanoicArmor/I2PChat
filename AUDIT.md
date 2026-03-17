# Security Audit Report: I2PChat

Дата аудита: 2026-03-17  
Область: исходный код репозитория, локальная сборка/CI, без аудита внешней инфраструктуры.

## Executive Summary

Проведен полный статический аудит безопасности `I2PChat` по направлениям:
- протокол и криптография (`i2p_chat_core.py`, `crypto.py`, `protocol_codec.py`);
- локальное хранение профилей/ключей, файловый ввод-вывод (`main_qt.py`, `i2p_chat_core.py`);
- dependency hygiene и supply-chain контур (`requirements*`, GitHub Actions, build-скрипты);
- релевантные security-регрессии в тестах.

Итог по подтвержденным находкам:
- **Critical:** 0
- **High:** 0
- **Medium:** 3
- **Low:** 2

Ключевые риски:
1. целостность `MSG_ID` не защищена MAC (возможна подмена ACK-контекста);
2. импорт `.dat` профиля в GUI может молча перезаписать существующий профиль;
3. Linux build pipeline тянет `appimagetool` по `latest` без проверки checksum/signature.

Важно: ряд ранее типичных проблем уже закрыт в текущем коде (identity binding через SAM lookup, path confinement профилей, безопасная уникализация входящих файлов, strict vNext по умолчанию).

---

## Scope и методика

Проверенные файлы:
- `i2p_chat_core.py`
- `crypto.py`
- `protocol_codec.py`
- `main_qt.py`
- `notifications.py`
- `requirements.in`
- `requirements.txt`
- `.github/workflows/security-audit.yml`
- `.github/workflows/nix-check.yml`
- `build-linux.sh`
- `tests/test_protocol_framing_vnext.py`
- `tests/test_asyncio_regression.py`

Метод:
- threat-model ревизия: активы, границы доверия, модель атакующего;
- статический анализ по классам уязвимостей: spoofing/tampering/replay/downgrade/DoS/file safety/supply chain;
- верификация потенциальных рисков по актуальным инвариантам кода и тестам;
- формирование remediation-плана с приоритизацией.

---

## Threat Model (кратко)

Активы:
- приватный ключ destination (профиль `.dat` / keyring);
- локальный Ed25519 signing seed для handshake;
- привязка peer identity (`.b32.i2p` ↔ destination/signing key);
- целостность входящих файлов и локального профиля;
- доверенность зависимостей и build-инструментов.

Границы доверия:
- все входящие сетевые кадры и signaling считаются недоверенными;
- локальная ФС частично доверена, но может содержать подмененные файлы;
- внешние источники артефактов (PyPI/GitHub releases/actions) недоверены по умолчанию.

Модель атакующего:
- удаленный пир I2P, отправляющий произвольные кадры/порядок кадров;
- атакующий с возможностью модификации трафика между endpoint'ами (tampering);
- локальный пользователь, импортирующий/подменяющий profile-файлы;
- supply-chain атакующий, влияющий на внешние build/runtime зависимости.

---

## Findings

## [MEDIUM] F-01: `MSG_ID` не аутентифицируется MAC (tampering ACK semantics)

**Затронуто:** `i2p_chat_core.py`, `crypto.py`, `protocol_codec.py`  
**Категория:** Integrity / Protocol metadata tampering
**Статус:** FIXED (2026-03-17) — `MSG_ID/FLAGS` включены в MAC-вход, добавлены tamper-тесты.

### Что происходит

В vNext-фрейме `MSG_ID` расположен в заголовке (`MAGIC|VER|TYPE|FLAGS|MSG_ID|LEN`), но MAC считается только по `msg_type + seq + encrypted_body`.  
Следствие: изменение `MSG_ID` в заголовке не ломает криптопроверку payload.

### Почему это важно

`MSG_ID` используется для корреляции ACK (`MSG_ACK`, `FILE_ACK`, `IMG_ACK`) и отображения доставки. Подмена `MSG_ID` может нарушать состояние pending ACK (ложные/потерянные подтверждения, неконсистентность UI/telemetry), даже при валидном MAC.

### PoC (сценарий)

1. Перехватить зашифрованный кадр `U` с валидным payload (`seq|ciphertext|mac`).
2. Изменить только `MSG_ID` в заголовке vNext.
3. Получатель примет кадр (MAC валиден), но ACK-контекст будет искажен.

### Рекомендации

1. Включить `msg_id` (и желательно `flags`) в MAC-вход.
2. Либо перейти на AEAD для всего заголовка как AAD (authenticated associated data).
3. Добавить негативные тесты на tamper заголовка при неизменном payload.

---

## [MEDIUM] F-02: Импорт `.dat` профиля может перезаписать существующий профиль без подтверждения

**Затронуто:** `main_qt.py` (`on_load_profile_clicked`)  
**Категория:** Local integrity / unsafe overwrite
**Статус:** FIXED (2026-03-17) — импорт по коллизии выполняется в новый профиль (`name_1`, `name_2`, ...), без silent overwrite.

### Что происходит

При импорте профиля выполняется `shutil.copy2(path, dest_path)`, где `dest_path` уже может существовать.  
Подтверждение overwrite отсутствует, стратегия безопасного merge/rename не применяется.

### Влияние

- тихая потеря/подмена локальной profile identity;
- риск случайной утраты рабочего профиля пользователем при загрузке одноименного `.dat`.

### Рекомендации

1. Перед копированием проверять `os.path.exists(dest_path)`.
2. В GUI запрашивать явное подтверждение overwrite.
3. Безопасный вариант по умолчанию: импортировать как новый профиль (`name (1).dat`).

---

## [MEDIUM] F-03: Непроверенная загрузка `appimagetool` по `latest` в Linux build script

**Затронуто:** `build-linux.sh`  
**Категория:** Supply-chain / Build integrity

### Что происходит

Скрипт скачивает `appimagetool` с GitHub по URL `releases/latest/...` и сразу делает исполняемым. Проверка digest/signature отсутствует.

### Влияние

- риск выполнения подмененного или компрометированного build-инструмента;
- потенциальная компрометация release-артефактов.

### Рекомендации

1. Пиновать конкретную версию `appimagetool`.
2. Проверять SHA256 (минимум) перед запуском.
3. По возможности использовать подписи релиза или доверенный источник из package manager.

---

## [LOW] F-04: GitHub Actions не пинованы по commit SHA и не заданы минимальные `permissions`

**Затронуто:** `.github/workflows/security-audit.yml`, `.github/workflows/nix-check.yml`  
**Категория:** CI hardening

### Что происходит

Используются floating major tags (`actions/checkout@v4`, `actions/setup-python@v5`, `cachix/install-nix-action@v27`) и не задан явный блок `permissions`.

### Влияние

- расширенная поверхность supply-chain риска через upstream action updates;
- потенциально избыточные права `GITHUB_TOKEN` относительно принципа least privilege.

### Рекомендации

1. Пиновать actions по полному commit SHA.
2. Добавить минимальные `permissions` на workflow/job уровне.

---

## [LOW] F-05: Нет hash pinning для Python-пакетов

**Затронуто:** `requirements.txt`  
**Категория:** Dependency integrity

### Что происходит

Версии пакетов зафиксированы (pip-compile), но отсутствуют хеши артефактов (`--generate-hashes` / `--require-hashes`).

### Влияние

- сниженная защита от подмены wheel/sdist при скачивании;
- зависимость от TLS/индекса без дополнительной integrity-валидации.

### Рекомендации

1. Перейти на `pip-compile --generate-hashes`.
2. В CI/сборке устанавливать зависимости с `pip install --require-hashes -r requirements.txt`.

---

## Что уже реализовано хорошо

- Проверка identity binding через SAM lookup перед фиксацией peer identity (`_set_verified_peer_identity`).
- Профильная path-безопасность: whitelist имени профиля + confinement в profiles dir (`_profile_scoped_path`).
- Входящие файлы: санитизация имени + уникализация + открытие в `"xb"` (без silent overwrite).
- Защита канала: обязательное шифрование после handshake, detection downgrade на plaintext кадрах.
- Целостность/anti-replay: `HMAC + seq` и строгий порядок кадров.
- TOFU mismatch-check для peer signing key; lock-to-peer завязан на verified binding.
- Безопасные subprocess-вызовы нотификаций (`shell=False`).

---

## Пробелы тестирования

Рекомендуемые дополнительные security-тесты:

1. **Header tampering tests**: негативные кейсы изменения `MSG_ID/FLAGS` при валидном payload MAC.
2. **Profile import overwrite tests**: GUI-поведение при коллизии `<name>.dat`.
3. **Supply-chain policy tests/checks**: валидация pinned action SHAs и checksum build-tools.
4. **TOFU policy tests**: сценарии first-contact в headless/без callback (явное policy-решение).

---

## Приоритет remediation

1. **P1 (сразу):** закрыть F-03 (pin + checksum для `appimagetool`) и F-04 (SHA-pinned actions + permissions).
2. **P1/P2:** закрыть F-01 (аутентификация `MSG_ID`/header через MAC/AAD).
3. **P2:** закрыть F-02 (безопасный импорт профиля без тихого overwrite).
4. **P3:** закрыть F-05 (hash-pinned Python dependencies).

---

## Статус ранее типичных рисков

Проверено и **не подтверждено** в текущей версии:
- identity misbinding "по строке peer address" без проверки: mitigated через SAM binding verification;
- path traversal через имя профиля: mitigated через `ensure_valid_profile_name` и `_profile_scoped_path`;
- тихая перезапись входящих файлов от пира: mitigated через `allocate_unique_filename` + `"xb"`.

