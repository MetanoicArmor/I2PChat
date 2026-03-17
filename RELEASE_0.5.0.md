# I2PChat v0.5.0 — UI & Profile Lock Update

## RU

### Контекст

Релиз фокусируется на двух направлениях:

- новый кроссплатформенный интерфейс в стиле macOS 26;
- исправление логики профиля и `Lock to peer`.

### Что вошло в релиз

#### 1) Новый UI в стиле macOS для всех платформ

- Переработан визуальный стиль приложения (Linux/macOS/Windows).
- Добавлены две темы: `ligth` и `night`.
- Переключатель темы перенесён в основное окно рядом со строкой статуса.
- Обновлены ключевые элементы интерфейса: popup-меню, панели действий, статусная строка и карточки передачи файлов/изображений.

#### 2) Исправление `Lock to peer` и формата профиля

- Исправлен сценарий, где при первом `Lock to peer` в `.dat` мог попасть только адрес peer.
- Нормализован формат профиля `.dat`:
  - строка 1 — приватный ключ профиля;
  - строка 2 — закреплённый peer (`stored peer`), если lock включён.
- Исправлена загрузка профилей с ранее некорректным содержимым `.dat`.
- Убрано дублирование строк при повторном lock.

### Итог

`v0.5.0` стабилизирует UX и профильный persistence:

- современный единый UI с двумя темами на всех платформах;
- корректный `Lock to peer` с первого раза и предсказуемое восстановление состояния профиля.

---

## EN

### Context

This release focuses on two major areas:

- a new cross-platform macOS 26 inspired UI;
- profile persistence and `Lock to peer` reliability fixes.

### What’s included

#### 1) New macOS-style UI across all platforms

- Updated visual language for Linux, macOS, and Windows.
- Added two themes: `ligth` and `night`.
- Moved theme switching to the main window (next to the status bar).
- Refined key UI components: popup menus, action toolbars, status bar text, and file/image transfer cards.

#### 2) `Lock to peer` and profile format fixes

- Fixed the issue where first-time `Lock to peer` could write only peer address to `.dat`.
- Standardized `.dat` profile format:
  - line 1 — profile private key;
  - line 2 — pinned peer (`stored peer`) when lock is enabled.
- Improved loading of previously malformed `.dat` files.
- Removed duplicate lines on repeated lock actions.

### Summary

`v0.5.0` improves both UX and profile state consistency:

- one modern UI with two themes across all platforms;
- deterministic `Lock to peer` behavior and reliable profile restore.

