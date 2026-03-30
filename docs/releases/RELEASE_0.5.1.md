# I2PChat v0.5.1 — hashing and input validation hardening

## RU

### Контекст

`v0.5.1` фокусируется на усилении безопасной обработки файловых и image-чанков:

- удалено использование `MD5` для коротких hash-суффиксов имён файлов;
- ужесточена валидация входящих `base64`-чанков в ветках `D/G`.

### Что реализовано

#### 1) Замена MD5 на SHA-256 для suffix имён

- В `i2pchat/core/i2p_chat_core.py` заменены обе точки генерации суффикса:
  - при локальном сохранении отправляемого изображения;
  - при сохранении входящего inline-изображения.
- Вместо `hashlib.md5(...).hexdigest()[:8]` используется:
  - `hashlib.sha256(...).hexdigest()[:8]`.
- Формат имён сохранён без изменений: `img_<timestamp>_<suffix>.<ext>`.

#### 2) Строгая base64-валидация и pre/post size checks (D/G)

- Для `msg_type == "G"` и `msg_type == "D"` включено строгое декодирование:
  - `base64.b64decode(body, validate=True)`.
- Добавлена предвалидация до декодирования:
  - расчёт `remaining` (сколько бинарных байт ещё допустимо принять);
  - отклонение чанка, если его base64-строка заведомо превышает допустимый размер.
- Добавлена поствалидация после декодирования:
  - отклонение чанка, если `len(decoded_chunk) > remaining`.

#### 3) Усиленная обработка ошибок при входящих file-chunks

- В ветке `D` при ошибке чанка теперь:
  - закрывается дескриптор входящего файла;
  - отправляется fail-событие в UI (`received=-1`);
  - удаляется частично записанный файл;
  - очищается состояние входящего трансфера.

#### 4) Тесты на негативные сценарии

- Добавлены регрессионные async-тесты:
  - invalid base64 для `G`;
  - oversize chunk для `G`;
  - invalid base64 для `D`;
  - oversize chunk для `D`.
- Проверено, что ошибки корректно приводят к сбросу состояния и без аварий.

### Проверка

- `python3 -m unittest tests.test_protocol_framing_vnext` — `OK`
- `python3 -m unittest tests.test_asyncio_regression` — `OK`
- Линтер-диагностика изменённых файлов — без ошибок.

### Итог

Релиз `v0.5.1` повышает устойчивость к некорректному входу и уменьшает даже теоретические риски коллизий суффиксов:

- отказ от `MD5` в пользу `SHA-256` для коротких hash-идентификаторов;
- строгий контроль формата и размера входящих base64-чанков в `D/G`;
- предсказуемое безопасное поведение при ошибках и повреждённых чанках.

---

## EN

### Context

`v0.5.1` focuses on hardening file/image chunk handling:

- removed `MD5` usage for short filename hash suffixes;
- enforced stricter `base64` validation in `D/G` receive paths.

### Implemented

#### 1) MD5 replaced with SHA-256 suffixes

- Updated both suffix generation points in `i2pchat/core/i2p_chat_core.py`:
  - local copy name for outgoing images;
  - saved name for incoming inline images.
- Replaced `hashlib.md5(...).hexdigest()[:8]` with:
  - `hashlib.sha256(...).hexdigest()[:8]`.
- Kept filename format unchanged: `img_<timestamp>_<suffix>.<ext>`.

#### 2) Strict base64 decode with pre/post size checks (D/G)

- For `msg_type == "G"` and `msg_type == "D"`:
  - switched to `base64.b64decode(body, validate=True)`.
- Added pre-decode validation:
  - compute `remaining` bytes allowed for the transfer;
  - reject chunks whose base64 text is too large for the remaining budget.
- Added post-decode validation:
  - reject chunks when `len(decoded_chunk) > remaining`.

#### 3) Stronger error handling for incoming file chunks

- In `D` branch, chunk errors now trigger:
  - input file close;
  - fail event to UI (`received=-1`);
  - deletion of partially written file;
  - full incoming-transfer state reset.

#### 4) Negative-path regression tests

- Added async regression tests for:
  - invalid base64 in `G`;
  - oversize chunk in `G`;
  - invalid base64 in `D`;
  - oversize chunk in `D`.
- Verified deterministic reset behavior without crashes.

### Verification

- `python3 -m unittest tests.test_protocol_framing_vnext` — `OK`
- `python3 -m unittest tests.test_asyncio_regression` — `OK`
- No linter issues in modified files.

### Summary

`v0.5.1` hardens input handling and reduces theoretical suffix-collision concerns:

- `MD5` removed in favor of `SHA-256` for short hash IDs;
- strict format/size validation for incoming `D/G` base64 chunks;
- predictable, safe failure behavior for malformed chunk input.

