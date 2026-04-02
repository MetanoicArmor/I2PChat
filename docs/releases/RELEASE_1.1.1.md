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

#### File transfer

- **Smoother progress:** UI updates are **throttled**; protocol writes use **batched** `drain` to avoid flooding the event loop.

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

#### Передача файлов

- **Реже подвисает интерфейс:** прогресс **дросселируется**, запись в сокет — с **батчингом** `drain`.

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
