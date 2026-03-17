# Security Audit Report: I2PChat

Дата аудита: 2026-03-17  
Область: исходный код репозитория (application code only), без инфраструктуры/деплоя.

## Executive Summary

Проведен аудит безопасности ключевых компонентов `I2PChat`:
- транспортный протокол и handshake (`i2p_chat_core.py`, `crypto.py`, `protocol_codec.py`);
- хранение идентичности/доверия (profile `.dat`, keyring fallback, TOFU trust store);
- обработка входящих файлов/изображений и управляющих сигналов;
- тестовое покрытие security-инвариантов.

Итог:
- **High**: 1
- **Medium**: 2
- **Low**: 1

Ключевой риск: в текущем протоколе адрес пира (`.b32.i2p`) принимается из данных, присланных самим пиром, и не криптографически привязан к signing key handshake. Это создает окно для spoofing/identity misbinding на первом контакте.

---

## Scope и методика

Проверенные файлы:
- `i2p_chat_core.py`
- `crypto.py`
- `protocol_codec.py`
- `main_qt.py`
- `notifications.py`
- `tests/test_protocol_framing_vnext.py`
- `tests/test_asyncio_regression.py`
- `requirements.txt`

Метод:
- статический анализ по категориям: аутентичность, целостность, конфиденциальность, downgrade/replay, DoS, безопасность файлового ввода;
- верификация кода на соответствие заявленным инвариантам;
- подготовка PoC для подтвержденных рисков.

---

## Threat Model (кратко)

Активы:
- приватные ключи I2P destination (профили);
- signing seed для handshake;
- целостность peer identity binding (`peer_addr <-> signing key`);
- пользовательские файлы/изображения в локальном хранилище.

Границы доверия:
- все входящие сетевые кадры считаются недоверенными;
- локальная файловая система пользователя частично доверена, но может содержать вредоносные/подмененные файлы;
- keyring может быть недоступен (fallback в файл).

Модель атакующего:
- удаленный пир в сети I2P, который может отправлять произвольные протокольные кадры;
- локальный пользователь/процесс с возможностью запускать приложение с произвольным profile name.

---

## Findings

## [HIGH] F-01: Identity misbinding в handshake (spoofing peer address)

**Затронуто:** `i2p_chat_core.py`  
**Категория:** Spoofing / Authentication flaw

### Что происходит

На принимающей стороне адрес пира берется из строки, которую присылает сам удаленный узел:
- `accept_loop()` читает `peer_identity_line` и выставляет `current_peer_addr`.
- позже этот же `current_peer_addr` используется для построения/проверки handshake payload в `_build_init_sig_payload()` / `_build_resp_sig_payload()`.

Подпись проверяется на валидность относительно `peer_sign_pub`, но **не доказывает**, что этот signing key принадлежит именно заявленному `.b32.i2p` адресу.

### Почему это уязвимость

На первом контакте злоумышленник может:
1. заявить любой `peer_addr` (включая адрес «ожидаемого» собеседника);
2. подписать handshake своим ключом;
3. пройти TOFU как «новый ключ для этого адреса», если ключ еще не пинован.

Это classic identity misbinding: криптография валидна, но привязка "кто именно" не гарантирована.

### PoC (сценарий эксплуатации)

1. Жертва запускает новый профиль без ранее закрепленного signing key для адреса `Alice.b32.i2p`.
2. Атакующий устанавливает соединение и в preface/`S`-кадре объявляет себя как `Alice.b32.i2p`.
3. Атакующий отправляет корректно подписанный `INIT` своим signing key.
4. Жертва видит TOFU-диалог для `Alice...` и при подтверждении пинует ключ атакующего.
5. Дальше атаки типа impersonation/MITM становятся возможны в рамках этого trust binding.

### Влияние

- подмена личности собеседника на первом контакте;
- компрометация смысла `Lock to peer` до момента надежного OOB-подтверждения fingerprint;
- риск закрепления неверного ключа в trust store.

### Рекомендации

1. Убрать доверие к self-asserted identity line/`S`-identity для security-binding.
2. Привязать адрес пира к криптографическому доказательству владения I2P destination private key (или эквивалентной challenge-response схеме через SAM).
3. В TOFU UI явно показывать предупреждение, что без OOB verification identity не подтверждена.
4. Для `Lock to peer` требовать уже верифицированный binding, а не только строковое совпадение адреса.

---

## [MEDIUM] F-02: Path traversal через имя профиля (локальная arbitrary file path write/read)

**Затронуто:** `main_qt.py`, `i2p_chat_core.py`  
**Категория:** Local file integrity / path injection

### Что происходит

Имя профиля принимается из:
- CLI (`sys.argv[1]`) в `main_qt.py`;
- editable combo в `ProfileSelectDialog.selected_profile()`.

Далее без нормализации используется в:
- `_profile_path() -> os.path.join(get_profiles_dir(), f"{self.profile}.dat")`;
- `_trust_store_path()`, `.signing` path.

Символы `../` или абсолютные компоненты не блокируются.

### PoC (подтверждено)

Проверка резолва пути:

```bash
python3 - <<'PY'
import os
base=os.path.join(os.path.expanduser('~'),'.i2pchat')
profile='../../tmp/poc_profile'
print(os.path.join(base,f'{profile}.dat'))
print(os.path.abspath(os.path.join(base,f'{profile}.dat')))
PY
```

Результат показал выход за пределы профилей (`/Users/tmp/poc_profile.dat` в текущей среде).

### Влияние

- локальная запись/перезапись файлов вне `profiles` директории от имени пользователя;
- потенциальная порча данных или несанкционированное хранение ключей в непредусмотренном месте.

### Рекомендации

1. Ввести строгий whitelist для profile name (например: `[a-zA-Z0-9._-]{1,64}`).
2. После построения пути проверять, что `abspath(target).startswith(abspath(get_profiles_dir()) + os.sep)`.
3. При невалидном имени профиля - fail closed с явной ошибкой.

---

## [MEDIUM] F-03: Тихая перезапись входящих файлов с одинаковым именем

**Затронуто:** `i2p_chat_core.py` (ветка `msg_type == "F"`)  
**Категория:** File integrity / unsafe overwrite

### Что происходит

При приеме файла:
- имя санитизируется (`sanitize_filename`);
- путь строится как `downloads/<safe_name>`;
- файл открывается через `open(safe_path, "wb")`.

Если файл уже существует, он будет перезаписан без подтверждения.

### PoC (сценарий)

1. У жертвы уже есть `downloads/report.pdf`.
2. Злоумышленник отправляет файл с именем `report.pdf`.
3. После accept запись идет в `wb`, старый файл заменяется новым содержимым.

### Влияние

- потеря локальной целостности данных в sandbox downloads;
- возможная подмена ожидаемого файла.

### Рекомендации

1. Перед записью проверять существование файла.
2. Использовать стратегию уникализации (`name (1).ext` / UUID suffix).
3. Опционально спрашивать подтверждение overwrite в UI.

---

## [LOW] F-04: Непинованные зависимости в `requirements.txt` (supply-chain риск)

**Затронуто:** `requirements.txt`  
**Категория:** Dependency hygiene

### Что происходит

Зависимости указаны без фиксированных версий (`textual`, `rich`, `i2plib`, `PyQt6`, `pynacl`, ...).

### Риск

- непредсказуемый набор версий при новой установке;
- шанс незаметного подтягивания несовместимых/уязвимых релизов.

### Рекомендации

1. Перейти на lock-файл (`pip-tools`, `poetry.lock`, или pinned constraints).
2. Добавить регулярный dependency audit в CI (`pip-audit`/эквивалент).

---

## Что уже реализовано хорошо

- Принудительное шифрование пользовательских данных после handshake (downgrade detection при plaintext кадрах).
- HMAC + sequence number для целостности и anti-replay.
- Ограничения размеров фрейма/изображений, валидация base64 и image magic bytes.
- TOFU pinning signing key с отдельным trust store и диалогом подтверждения.
- ACK-контекст (peer/session/kind) и защита от spoofing ACK сигналов.

---

## Пробелы тестирования

Рекомендуется добавить security-тесты:

1. **Identity binding tests**: негативные кейсы, где peer заявляет один адрес, но использует несвязанный signing key.
2. **Profile name sanitization tests**: отклонение `../`, абсолютных путей, спецсимволов.
3. **File overwrite policy tests**: поведение при конфликте имен в `downloads`.
4. **TOFU UX safety tests**: явный флаг/статус "not OOB-verified yet".

---

## Приоритет remediation

1. **Срочно (High):** исправить identity binding в handshake (F-01).
2. **Далее (Medium):** валидация имени профиля и path confinement (F-02).
3. **Далее (Medium):** безопасная политика именования входящих файлов (F-03).
4. **Планово (Low):** lock/pin dependency versions (F-04).

