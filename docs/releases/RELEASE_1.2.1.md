# I2PChat v1.2.1 — Fluent emoji, system line styling, Windows status font

Patch after **v1.2.0**: **bundled raster emoji** switch from Noto to **Microsoft Fluent UI Emoji** (MIT, Teams-style 3D assets), **in-chat system and info lines** rendered as **centered plain text** (no bubbles) with **tighter spacing**, **lifecycle connection messages** reclassified so they use that style, and **clearer typography on Windows** for the **status bar** and those service lines (**Segoe UI**, unified **11px**).

## EN

### Summary

**v1.2.1** is a visual and packaging polish release:

- Offline emoji in the picker, compose field, and message bubbles now come from **[microsoft/fluentui-emoji](https://github.com/microsoft/fluentui-emoji)** (same Fluent family as modern Microsoft apps). The app bundle path is **`i2pchat/gui/fluent_emoji`**; maintainers refresh PNGs with **`scripts/vendor_fluent_emoji.py`** (supports **3D** / **Color** / **Flat** / **High Contrast**).
- **`system`** and **`info`** rows in the chat list are no longer left “peer-like” bubbles: they are **centered**, **muted** (`system_text`), **compact vertically**, and use the **same pixel size as the status label** (and **Segoe UI** on Windows).
- Wire-up messages (**handshake**, **PFS established**, **connection accepted**, **peer / you disconnected**) are emitted as **`info`** so they follow that layout; **chat history reset** on session end still runs (via **`_SESSION_END_INFO_TEXTS`** in the Qt GUI).

### User-visible changes

#### Fluent UI Emoji (replaces Noto)

- Raster pack is **Fluent UI Emoji** under the upstream **MIT** license (**`LICENSE-FLUENT-EMOJI`** + **`NOTICE`** in the bundle folder).
- **Noto**-specific vendoring script removed; use **`python3 scripts/vendor_fluent_emoji.py /path/to/fluentui-emoji --style 3d`** to regenerate **`png/`** and **`manifest.json`**.
- Skin-tone emoji assets are taken from each asset’s **`Default`** style folder when needed.

#### System / info lines in the chat

- **No rounded bubble** for **`system`** and **`info`**; text is **centered** on the row.
- **Tighter vertical spacing** between consecutive service lines (dedicated padding constants; does not change **me** / **peer** bubble spacing).
- **No italic** on these lines (upright text).
- **Font size** matches the **status bar** label (shared **pixel** size helper); on **Windows**, **Segoe UI** is applied for both **QLabel#StatusLabel** / **ContactsSidebarToggle** (via QSS) and **system/info** painting so small text looks less rough.

#### Connection lifecycle copy in the feed

These now appear as **`info`** (same visual treatment as other service lines), not green **success** or red **disconnect** bubbles:

- Handshake / “Establishing secure channel…”
- “Secure channel with PFS established”
- “Connection accepted from …”
- “Peer disconnected.” / “You disconnected.”

**Errors** (e.g. handshake failure) remain **`error`** bubbles.

### Technical

- **Emoji:** `i2pchat/gui/emoji_paths.py` → **`fluent_emoji_root()`**; **`I2PChat.spec`** and **`.gitattributes`** point at **`fluent_emoji`**; **`scripts/vendor_fluent_emoji.py`** (glyph map from **`metadata.json`**, **`Default/<Style>`** fallback).
- **GUI:** `ChatItemDelegate._paint_system_info`, **`_center_qtextdocument_blocks`**, **`_status_label_font_pixel_size`**, **`_status_ui_font_family`**, **`_status_ui_font_family_qss`**; theme **`%`** dict includes **`status_ui_font_family_qss`**.
- **Core:** `i2pchat/core/i2p_chat_core.py` — **`_emit_message("info", …)`** for the lifecycle strings above; **`disconnect`** no longer used for those two disconnect phrases.
- **Tests:** `tests/test_history_ui_guards.py` updated for session-reset condition.

### Compatibility

- **Wire protocol** unchanged.
- **Chat history on disk** still stores **`me`** / **`peer`**; service line **`kind`** values in the UI model are unchanged in meaning except the lifecycle messages now use **`info`** for display consistency.
- **PyInstaller** bundles **`i2pchat/gui/fluent_emoji`** instead of **`noto_emoji`**.

### Validation

- `python3 -m unittest tests.test_history_ui_guards`
- `python3 -m py_compile i2pchat/gui/main_qt.py i2pchat/core/i2p_chat_core.py`
- Manual: connect / disconnect / handshake; emoji picker and bubbles; Windows status bar and service lines.

---

## RU

### Кратко

**v1.2.1** — визуальная и упаковочная доработка после **v1.2.0**:

- **Офлайн-эмодзи** (пикер, поле ввода, баблы) переведены с **Noto** на **Fluent UI Emoji** из репозитория Microsoft (**MIT**, стиль как у современных приложений Microsoft / Teams). Каталог бандла — **`fluent_emoji`**, обновление растров — **`scripts/vendor_fluent_emoji.py`**.
- Строки **`system`** и **`info`** в ленте чата **без бабла**, **по центру**, **плотнее по вертикали**, **без курсива**, с **тем же пиксельным кеглем**, что и **строка статуса**; на **Windows** — **Segoe UI** для статус-бара и этих строк.
- Сообщения **жизненного цикла соединения** (рукопожатие, PFS, принятие входящего, отключения) идут как **`info`**, чтобы выглядеть как прочие служебные строки; **сброс сессии истории** при отключении сохранён.

### Изменения для пользователя

#### Эмодзи Fluent вместо Noto

- Источник ассетов: **[microsoft/fluentui-emoji](https://github.com/microsoft/fluentui-emoji)**; в каталоге бандла — **`LICENSE-FLUENT-EMOJI`** и **`NOTICE`**.
- Скрипт **`vendor_noto_emoji.py`** удалён; для пересборки PNG: **`python3 scripts/vendor_fluent_emoji.py /path/to/fluentui-emoji --style 3d`** (или **`color`** / **`flat`** / **`high_contrast`**).
- Для глифов со скинтонами используется ветка **`Default/<стиль>`** в дереве Fluent.

#### Служебные строки в чате

- У **`system`** и **`info`** нет **цветного скруглённого бабла**; текст **центрирован**.
- **Меньше вертикального зазора** между подряд идущими служебными строками (отдельные константы; **баблы** собеседника и «я» **не** сжимаются).
- **Курсив отключён** для этого режима.
- **Размер шрифта** согласован со **строкой статуса** (общий хелпер в **px**); на **Windows** добавлены **Segoe UI** в **QSS** и при отрисовке **system/info**.

#### Сообщения о соединении в ленте

Переведены на **`info`** (как служебные строки), а не зелёный **success** или красный **disconnect**:

- рукопожатие / «Establishing secure channel…»
- «Secure channel with PFS established»
- «Connection accepted from …»
- «Peer disconnected.» / «You disconnected.»

**Ошибки** по-прежнему в стиле **`error`**.

### Техническое

- См. блок **Technical** выше (те же файлы и механики).

### Совместимость

- **Протокол** без изменений.
- **История** на диске по-прежнему про **`me`** / **`peer`**; смысл полей в UI сохранён, кроме того что перечисленные фразы жизненного цикла теперь приходят как **`info`**.
- **PyInstaller** включает **`fluent_emoji`** вместо **`noto_emoji`**.

### Проверка

- См. **Validation** в английском блоке.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.1.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.2.1.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.1.zip` | Unzip → open I2PChat.app |
