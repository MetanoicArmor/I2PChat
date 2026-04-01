# I2PChat v1.1.0 — Custom Blind Box replicas, update check, emoji, shortcuts & tooltips

Full notes: **custom per-profile Blind Box endpoints** (GUI), **in-app update check** against release ZIPs on the project eepsite, **offline emoji** (picker + inline in compose and bubbles), and **keyboard shortcuts** surfaced in **tooltips**—plus **on-disk profile layout** (`profiles/<name>/` with automatic migration from older flat installs), compose/search polish, and a Windows PyQt6 rendering fix.

## EN

### Summary

**v1.1.0** adds **per-profile Blind Box replica lists** you can edit in **⋯ → BlindBox diagnostics** (saved as `profiles/<profile>/<profile>.blindbox_replicas.json` when not overridden by environment), an **in-app “Check for updates…”** flow that parses the releases page and compares to root **`VERSION`**, and **bundled raster emoji** (Noto-style) in the picker, message bubbles, and compose field—with HiDPI-aware sizing and safer HTML handling. **Profile on-disk layout** is **restructured** to **`profiles/<name>/`** per profile, with **automatic migration** from older flat installs. **Shortcuts** for **⋯** actions, theme, and the Saved peers strip appear in **tooltips** and work even when the compose field is focused.

### User-visible changes

#### Custom Blind Box replicas (primary)

- For **named persistent profiles**, you can maintain a **per-profile replica list** in the **BlindBox diagnostics** dialog: edit endpoints (one per line), **Save and restart** to apply.
- Stored as **`profiles/<profile>/<profile>.blindbox_replicas.json`** when the list is **not** locked by **`I2PCHAT_BLINDBOX_REPLICAS`**, deployment defaults, or related env-driven modes (the UI explains when editing is disabled).
- Core validates endpoints and restarts the BlindBox runtime after save; see manuals for env vars and built-in release defaults.

#### Check for updates

- **⋯ → Check for updates…** (shortcut **Ctrl/Cmd+U**): fetches the configured releases page (default: project **eepsite**), parses HTML for ZIP names matching `I2PChat-{linux|macOS|windows}-{arch}-vMAJOR.MINOR.PATCH.zip`, compares to the **running build** from **`VERSION`**.
- **HTTP proxy:** for `.i2p` URLs, if no `http_proxy` / `HTTP_PROXY` / `ALL_PROXY` (and no system proxy), defaults to **`http://127.0.0.1:4444`**. Override with **`I2PCHAT_UPDATE_HTTP_PROXY`** (`off` / `none` / `direct` / `0` disables). **`I2PCHAT_RELEASES_PAGE_URL`** overrides the page URL.
- **Open downloads** uses the same base URL with **`#downloads`**. **`VERSION`** discovery walks up from the GUI module so **source-tree / IDE** runs see the repo root **`VERSION`** instead of falling back to `0.0.0`.

#### Emoji / smiles

- **Offline emoji picker** (bundled PNG set), **inline emoji in message bubbles** and in the **compose** field, with **device pixel ratio** handling for sharper glyphs on HiDPI.
- **Security:** manifest paths are constrained; pasting HTML from the clipboard is treated as **plain text** (no arbitrary HTML images).

#### Tooltips & keyboard shortcuts

- **⋯** menu: tooltips include a **Shortcut:** line where applicable (Ctrl on Windows/Linux; on macOS Qt’s `Ctrl+…` sequences show as **⌘**).

| Action | Shortcut |
|--------|----------|
| Load profile (.dat) | Ctrl/Cmd+O |
| Send picture | Ctrl/Cmd+P |
| Send file | Ctrl/Cmd+F |
| BlindBox diagnostics | Ctrl/Cmd+D |
| Export profile backup… | Ctrl/Cmd+E |
| Import profile backup… | Ctrl/Cmd+I |
| Export history backup… | Ctrl/Cmd+Shift+E |
| Import history backup… | Ctrl/Cmd+Shift+I |
| Lock to peer | Ctrl/Cmd+L |
| Copy my address | Ctrl/Cmd+Shift+C |
| Check for updates… | Ctrl/Cmd+U |
| Privacy mode | **macOS:** **Control+H** (avoids **⌘H** *Hide*); **Windows/Linux:** Ctrl+H |

- **Theme toggle:** Ctrl/Cmd+**T** (tooltip on theme control). **Saved peers strip:** Ctrl/Cmd+**B** (tooltip on ◀/▶). Both work from the **message compose** field (so they are not swallowed by rich-text editing).
- **Compose placeholder:** send hint uses **Ctrl** or **⌘** per OS; mentions **drag and drop** for images and files.
- **In-chat search:** **Escape** closes the hits UI and clears the search field (per focus/state rules in code).

#### Profile data layout (restructured)

- Per-profile files (`.dat`, contacts, history sidecars, compose drafts, BlindBox state, etc.) are stored under **`profiles/<profile_name>/`** inside the application data directory, instead of many prefixed files mixed at the data root.
- **Upgrading from v1.0.x:** if `<name>.dat` and related `<name>.*` files still sit in the root of the data folder, they are **migrated automatically**. On **GUI/TUI startup** the app runs a **full sweep** over every profile that still has a flat `<name>.dat` in the root, so the data folder is tidied even for profiles you have not opened in a long time. Shared items (e.g. `downloads/`, `images/`, `ui_prefs.json`) stay at the root. See **MANUAL_EN** / **MANUAL_RU** for paths.

#### Also in this release

- **Windows:** PyQt6 fix for **QRegion** masks built from **QPolygonF** (`toPolygon()`), avoiding a crash path in rounded chrome.
- **Chat UI:** slightly **smaller inline image previews** in the message list for a denser feed.

### Technical / validation

- New: `i2pchat/updates/release_index.py`; tests: `tests/test_release_index.py`. Per-profile replicas: `i2pchat/storage/profile_blindbox_replicas.py`, `tests/test_profile_blindbox_replicas.py`. Nested profile data dir + legacy migration: `tests/test_profile_layout_migration.py`.
- Run **unittest** / **pytest** before tagging (same expectations as prior releases).

### Compatibility

Compatible with **v1.0.x** wire protocol and existing **file formats** (`.dat`, encrypted history, bundles). **Storage layout** now nests profile files under **`profiles/<name>/`**; legacy root-level profile files are **moved on first use**—no manual step. The optional per-profile **`*.blindbox_replicas.json`** file lives under that folder when saved from the GUI. No intentional wire-protocol breaks.

---

## RU

### Кратко

**v1.1.0:** **свои списки реплик Blind Box на профиль** (редактирование в **BlindBox diagnostics**), **проверка обновлений** по странице релизов, **офлайн-эмодзи** (пикер, баблы, поле ввода) и **горячие клавиши** с отображением в **подсказках**; **перестройка раскладки данных** — файлы каждого профиля в **`profiles/<имя>/`** с **автоматической миграцией** со старой плоской схемы; плюс мелкие правки чата и исправление под **Windows + PyQt6**.

### Изменения для пользователя

#### Кастомные Blind Box (главное)

- Для **именованных persistent-профилей** список реплик можно вести **в диалоге BlindBox diagnostics**: endpoints построчно, **Save and restart**.
- Файл **`profiles/<профиль>/<профиль>.blindbox_replicas.json`**, если список **не** зафиксирован переменными окружения / деплой-дефолтами (интерфейс сообщает, когда правка недоступна).

#### Проверка обновлений

- **⋯ → Check for updates…** (**Ctrl/Cmd+U**): разбор имён ZIP на странице, сравнение с **`VERSION`**; прокси для `.i2p` по умолчанию **`127.0.0.1:4444`** при отсутствии настроенного прокси; **`I2PCHAT_UPDATE_HTTP_PROXY`**, **`I2PCHAT_RELEASES_PAGE_URL`**; открытие загрузок с **`#downloads`**. Чтение **`VERSION`** из корня репозитория при запуске из исходников.

#### Смайлы / эмодзи

- **Пикер без сети**, эмодзи в **сообщениях** и в **поле ввода**, учёт **HiDPI**. Вставка HTML из буфера — как **простой текст**.

#### Подсказки и хоткеи

- В подсказках пунктов **⋯** — строка **Shortcut:** (таблица совпадает с блоком EN). **Тема:** Ctrl/Cmd+**T**; **Saved peers:** Ctrl/Cmd+**B**. **Privacy mode** на macOS — **физический Control+H**. **Esc** в поиске по чату — сброс (по правилам в коде).

#### Раскладка данных профилей (перестройка)

- Файлы профиля (`.dat`, контакты, история, черновики, BlindBox и др.) хранятся в **`profiles/<имя>/`** в каталоге данных приложения, а не одним слоем префиксных файлов в корне.
- **Обновление с v1.0.x:** если в корне данных остались `<имя>.dat` и сопутствующие `<имя>.*`, они **переносятся автоматически**. При **запуске GUI или TUI** выполняется **полный проход** по всем профилям, у которых в корне ещё лежит плоский `<имя>.dat`, так что порядок наводится даже для давно не открытых профилей. Общие каталоги (`downloads/`, `images/`, `ui_prefs.json`) остаются в корне. Подробные пути — в **MANUAL_RU** / **MANUAL_EN**.

#### Ещё

- **Windows:** маска **QRegion** из **QPolygonF** через **`toPolygon()`**. **Превью картинок** в ленте чата чуть компактнее.

### Совместимость

Совместимо с **v1.0.x** по протоколу и форматам файлов (`.dat`, шифрованная история, бэкапы). **На диске** профильные файлы переезжают в **`profiles/<имя>/`**; старая плоская раскладка **мигрирует при первом использовании** профиля. Опциональный **`*.blindbox_replicas.json`** при сохранении из GUI лежит в той же вложенной папке. Намеренных разрывов wire-протокола нет.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.0.zip` | Unzip → open I2PChat.app |
