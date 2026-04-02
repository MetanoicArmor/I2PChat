# I2PChat v1.1.1 — Long messages, compose splitter, file transfer polish, theme auto, docs

Patch release after **v1.1.0**: **split long outgoing text** into multiple chat frames (Telegram-style limit), **draggable chat / compose height** with persisted preference, **smoother file sends** (less UI jank), **system light/dark** following the OS when theme is auto, **⋯ menu** keyboard navigation, **compose and bubble word wrap** fixes, **emoji compose** rebuild tweaks, **clearer SAM “connection refused”** errors, **transient profile** default rename, and **documentation / screenshots** updates.

## EN

### Summary

**v1.1.1** improves day-to-day chat UX: very long messages are **split automatically** (~4096 Unicode characters per part, breaking at newlines or spaces where possible) for both **live** sessions and **BlindBox** offline queue—each part is a normal `U` frame, so the peer sees **several bubbles** like in Telegram. The **horizontal strip** between the message list and the input area (**tooltip:** *Drag to resize the message field*) now drives splitter height with **direction aligned** to intuitive drag. **File uploads** use **throttled progress** and **batched socket drains** to reduce UI stalls. **Theme** can **follow system** appearance (light/dark). Miscellaneous GUI fixes (word wrap, emoji materialization, **⋯** popup keys) and **Linux docs** (PyQt6 xcb cursor dependency, Python 3.14 packages) round out the release.

### User-visible changes

#### Long text messages (primary)

- Outgoing text longer than **4096 characters** is sent as **multiple sequential messages** (same wire type as before).
- Splitting prefers **line breaks**, then **spaces**, then a hard cut at the limit (Unicode code points, not UTF-8 bytes).
- Applies to **online** send and **BlindBox** offline send (each chunk is its own queued envelope).

#### Chat / compose layout

- **Resize** the bottom **compose** area by dragging the **strip** above the input (saved height in UI preferences).
- **Fix:** drag direction on that strip (with the resize tooltip) matches **“pull to enlarge / shrink input”** expectations.

#### File transfer (speed / responsiveness)

- **What was going wrong:** with **qasync**, Qt and the **same thread** run the chat UI and `receive_loop`. Emitting file progress to the list **on every ~4 KiB chunk** could stall reading the next frames; the sender then blocked on **`drain`** (TCP backpressure), which felt like freezes rather than “slow I2P”.
- **Fixes:**
  - **Throttled progress** on **receive** (same cadence as send for large files: ~64 KiB steps, plus start / end / errors), so the model is not updated tens of thousands of times per file.
  - **Batched `drain`:** several `D`/`G` frames per `await writer.drain()` (default batch **4**), tunable via **`I2PCHAT_FILE_SEND_DRAIN_BATCH`**; optional larger disk reads **`I2PCHAT_FILE_CHUNK_BYTES`** (still within frame limits).
  - **Send Picture (`G`):** idle **read timeout** during an incoming picture no longer **tears down** the session (same **restart `receive_loop`** behaviour as for file **F/D** on slow links).
  - **End of picture receive:** **SHA-256 + disk write + PIL validation** run in a **thread pool** (`asyncio.to_thread`) so the UI does not hitch as hard when the last chunk arrives.
- **Optional diagnostics:** **`I2PCHAT_FILE_XFER_DEBUG=1`** — log slow drains and long `handle_file_event`; **`I2PCHAT_QT_FILE_EVENT_NOOP=1`** — disable file progress callbacks for isolation tests (no progress UI).

#### Theme

- **Auto** theme can **follow the OS** light/dark appearance (Qt style hints / platform integration).

#### ⋯ (More) menu

- **Arrow keys** and **Enter** navigate and activate items in the actions popup.

#### Compose & bubbles

- **Word wrap** fixes in the **compose** field and **message bubbles**.
- **Emoji in compose:** avoid **repeated** full-document rebuilds when nothing changed; **skip** raster pass when no bundled glyphs exist.

#### Connection errors

- If the **SAM** TCP port is **connection refused**, the app shows a **short, actionable** hint (router not running / wrong SAM address).

#### Transient profile

- Default ephemeral profile folder name uses **`random_address`** instead of **`default`** (clearer that it is non-persistent).

#### Documentation

- **Debian/Ubuntu:** `libxcb-cursor0` for PyQt6 **xcb**; **Python 3.14** install example.
- **README:** simplified **source-run** commands; **screenshots** refreshed / third image added; main shot width aligned with gallery.

### Technical / validation

- New: `i2pchat/protocol/chat_text_chunking.py`; tests: `tests/test_chat_text_chunking.py`, extended `tests/test_send_text_routing.py`.
- File transfer: `should_emit_file_progress`, `_finalize_inline_image_worker`; tests `tests/test_file_transfer_progress.py`, `tests/test_protocol_hardening.py` (`test_inline_image_read_timeout_preserves_connection`).
- Run **pytest** / **unittest** as for prior releases.

### Compatibility

Wire protocol and frame types are **unchanged**; peers receive **more `U` frames** for long paste instead of one huge body. Profile data layout same as **v1.1.0**. No intentional breaking changes.

---

## RU

### Кратко

**v1.1.1** — патч после **v1.1.0**: **автоматическое разбиение** очень длинных исходящих текстов на несколько сообщений (лимит как у Telegram), **перетаскиваемая высота** зоны чата/ввода с сохранением, **более плавная** отправка файлов, **тема по системе** (светлая/тёмная), навигация в меню **⋯**, правки **переносов** и **эмодзи** в поле ввода, понятнее ошибка **отказа в подключении к SAM**, переименование дефолта **эфемерного профиля**, обновления **документации и скриншотов**.

### Изменения для пользователя

#### Длинные текстовые сообщения (главное)

- Текст длиннее **4096 символов** (кодпоинты Unicode) уходит **несколькими** последовательными сообщениями.
- Разбиение: сначала **переводы строк**, затем **пробелы**, иначе жёсткий срез по лимиту.
- Действует и для **живой** сессии, и для **BlindBox** (каждая часть — отдельная постановка в очередь).

#### Макет чата и ввода

- **Меняется высота** нижней панели: перетаскивание **полоски** над полем ввода (подсказка *Drag to resize the message field*); высота **сохраняется** в настройках UI.
- **Исправление:** направление перетаскивания на этой полосе приведено в соответствие с ожиданием («вниз — больше места под ввод» и т.п.).

#### Передача файлов (скорость и отзывчивость)

- **В чём была проблема:** при **qasync** интерфейс Qt и **`receive_loop`** работают в **одном потоке**. Обновление прогресса в списке **на каждый чанк ~4 KiB** могло задерживать чтение следующих кадров; отправитель тогда долго ждал **`drain`** (обратное давление TCP) — ощущалось как **подвисание**, а не как «медленный I2P».
- **Что сделано:**
  - **Дросселирование прогресса на приёме** (как у отправки для больших файлов: шаг ~64 KiB, плюс начало / конец / ошибки).
  - **Батчинг `drain`:** несколько кадров **D/G** на один `await writer.drain()` (по умолчанию **4**), переменная **`I2PCHAT_FILE_SEND_DRAIN_BATCH`**; при желании больший **`I2PCHAT_FILE_CHUNK_BYTES`** (в пределах лимита кадра).
  - **Картинки (`G`):** при **долгой паузе** чтения во время приёма inline-картинки сессия **не сбрасывается** — как для файла **F/D**, выполняется **перезапуск `receive_loop`**.
  - **Конец приёма картинки:** **SHA-256, запись на диск и PIL** вынесены в **пул потоков** (`asyncio.to_thread`), чтобы меньше фризить UI в момент завершения.
- **Диагностика (по желанию):** **`I2PCHAT_FILE_XFER_DEBUG=1`** — логи медленных drain и долгих обработчиков; **`I2PCHAT_QT_FILE_EVENT_NOOP=1`** — отключить колбэки прогресса для проверки гипотез (без полоски прогресса).

#### Тема

- Режим **авто** может **следовать** светлой/тёмной схеме **системы**.

#### Меню ⋯

- **Стрелки** и **Enter** в всплывающем списке действий.

#### Поле ввода и баблы

- Правки **переноса слов** в **поле ввода** и в **пузырях** сообщений.
- **Эмодзи в compose:** меньше лишних полных пересборок документа; нет лишней работы, если **нет** растровых глифов в комплекте.

#### Ошибки подключения

- При **connection refused** на порт **SAM** — **короткая подсказка**, что проверить (роутер, адрес SAM).

#### Эфемерный профиль

- Имя каталога по умолчанию — **`random_address`** вместо **`default`**.

#### Документация

- **Debian/Ubuntu:** зависимость **`libxcb-cursor0`** для PyQt6 **xcb**; пример установки **Python 3.14**.
- **README:** упрощённые команды **запуска из исходников**; обновлённые **скриншоты**, третье изображение в галерее.

### Совместимость

Протокол и типы кадров **те же**; при длинной вставке пир получает **несколько кадров `U`** вместо одного гигантского. Раскладка данных как в **v1.1.0**. Намеренных разрывов совместимости нет.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.1.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.1.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.1.zip` | Unzip → open I2PChat.app |
