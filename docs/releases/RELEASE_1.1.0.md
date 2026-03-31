# I2PChat v1.1.0 — Keyboard shortcuts, update check, UX polish

> **Screenshots:** placeholders for maintainers — add UI captures when publishing the GitHub release. **Manuals** (`docs/MANUAL_EN.md`, `docs/MANUAL_RU.md`): refresh in a follow-up pass to mention shortcuts, update check, and compose hints.

## EN

### Summary

`v1.1.0` adds **discoverable keyboard shortcuts** for the **⋯ (More actions)** menu and chrome (theme, contacts sidebar), **platform-accurate** text in the compose field, **Escape** to reset in-chat search, and an **in-app check for updates** against release ZIP names on the project I2P site—with sensible defaults for the I2P HTTP proxy and more reliable reading of the root **`VERSION`** file when running from a source tree.

### User-visible changes

- **⋯ More actions — shortcuts (Ctrl on Windows/Linux, ⌘ on macOS via Qt’s portable `Ctrl+…` sequences)**  
  Tooltips append a **Shortcut:** line in native form where applicable.

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
  | Copy my address | Ctrl/Cmd+Shift+C (does not conflict with chat copy **Ctrl/Cmd+C**) |
  | Check for updates… | Ctrl/Cmd+U |
  | Privacy mode toggle | **macOS:** **Control+H** (⌘H is reserved by the system for *Hide*); **Windows/Linux:** Ctrl+H |

- **Theme & sidebar**  
  - **Switch theme:** Ctrl/Cmd+**T** (tooltip on the theme control).  
  - **Show/hide Saved peers strip:** Ctrl/Cmd+**B** (tooltip on the ◀/▶ control).  
  - Same shortcuts are handled from the **message compose** field so they are not swallowed by the rich-text editor (e.g. Ctrl+B for bold).

- **Compose field placeholder**  
  - Send hint uses **only** **Ctrl** or **⌘** depending on OS (no combined `Ctrl/⌘` string).  
  - Notes that **drag and drop** of **images** and **files** is supported.

- **In-chat search (history on)**  
  - **Escape** closes the hits console and **clears** the search field (when search is active or the search row has focus, or when there is a non-empty query / open console—see implementation). Focus returns to the message list.

- **Check for updates…**  
  - Fetches the configured releases page (default: project **eepsite**), parses HTML for ZIP names matching `I2PChat-{linux\|macOS\|windows}-{arch}-vMAJOR.MINOR.PATCH.zip`, compares to the **current build** from the root **`VERSION`** file.  
  - **HTTP proxy:** if the URL contains `.i2p` and no `http_proxy` / `HTTP_PROXY` / `ALL_PROXY` (or system proxy via `getproxies()`), the client uses **`http://127.0.0.1:4444`** by default. Override with **`I2PCHAT_UPDATE_HTTP_PROXY`** (`off` / `none` / `direct` / `0` disables the default). **`I2PCHAT_RELEASES_PAGE_URL`** overrides the default page URL.  
  - **Open downloads page** in the result dialog opens the same base URL with **`#downloads`**.  
  - **Version discovery:** `_read_version()` walks **up** from `main_qt.py` (and still checks cwd, `_MEIPASS`, and the frozen executable directory) so **trunk / IDE** runs find repo-root **`VERSION`** instead of falling back to `0.0.0`.

### Technical / validation

- New module: `i2pchat/updates/release_index.py` (fetch, parse, compare).  
- Tests: `tests/test_release_index.py` (parser, opener selection, sync check with mocked fetch).  
- Run the project’s **unittest** / **pytest** suites before tagging (same expectations as prior releases).

### Compatibility

Compatible with **`v1.0.x`** on-disk formats and protocol. No intentional wire or storage format breaks.

---

## RU

### Кратко

В **`v1.1.0`** добавлены **горячие клавиши** для меню **⋯** и элементов интерфейса (тема, боковая панель контактов), **корректный по ОС** текст подсказки в поле ввода, **Escape** для сброса поиска по чату и **проверка обновлений** с I2P-страницы релизов — с прокси по умолчанию и **надёжным** чтением **`VERSION`** из корня репозитория при запуске из исходников.

### Изменения для пользователя

- **⋯ — хоткеи** (на Linux/Windows — **Ctrl**, на macOS в Qt строка `Ctrl+…` даёт **⌘**). В подсказках пунктов добавлена строка **Shortcut:** в нативном виде. Таблица совпадает с блоком EN (Load profile **O**, Send picture **P**, Send file **F**, BlindBox **D**, Export/Import profile **E**/**I**, Export/Import history **Shift+E**/**Shift+I**, Lock **L**, Copy my address **Shift+C**, Check for updates **U**).

- **Privacy mode:** на **macOS** для переключения используется **физический Control+H** (в последовательности Qt это `Meta+H`), чтобы не конфликтовать с системным **⌘H** (*Скрыть приложение*). На Windows/Linux — **Ctrl+H**.

- **Тема:** Ctrl/Cmd+**T**; **панель Saved peers:** Ctrl/Cmd+**B**; подсказки на кнопках. Работают и при фокусе в поле сообщения.

- **Плейсхолдер ввода:** только **Ctrl** или **⌘** для отправки; упоминание **drag and drop** изображений и файлов.

- **Поиск по ленте:** **Esc** закрывает панель совпадений и **очищает** строку поиска (по правилам фокуса/состояния в коде).

- **Check for updates…:** разбор имён ZIP на странице, сравнение с **`VERSION`**; по умолчанию для `.i2p` без переменных прокси — **`http://127.0.0.1:4444`**; переопределение **`I2PCHAT_UPDATE_HTTP_PROXY`** и **`I2PCHAT_RELEASES_PAGE_URL`**; кнопка открытия страницы с **`#downloads`**.

### Совместимость

Совместимо с **`v1.0.x`**: профили, история, backup; протокол и форматы намеренно не ломались.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.0.zip` | Unzip → open I2PChat.app |
