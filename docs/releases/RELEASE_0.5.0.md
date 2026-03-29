# I2PChat v0.5.0 — UI refresh and lock-to-peer reliability

## RU

### Контекст

`v0.5.0` объединяет UI-обновление и исправления профильного persistence:

- единый macOS-inspired визуальный стиль для Linux/macOS/Windows;
- стабилизация сценария `Lock to peer` без повторных действий пользователя.

### Что реализовано

#### 1) Кроссплатформенный UI в стиле macOS 26

- Добавлены и отбалансированы темы `ligth` и `night`.
- Переключение темы перенесено в рабочее окно (рядом со статусом).
- Унифицированы popup-элементы и карточки передачи файлов/изображений.
- Статус-строка стала информативнее и адаптивнее по ширине.

#### 2) Исправление профилей и `Lock to peer`

- Исправлен сценарий первого lock, при котором `.dat` мог сохраняться в некорректном формате.
- Нормализован формат `.dat`:
  - строка 1 — приватный ключ профиля;
  - строка 2 — закреплённый peer (`stored peer`).
- Улучшена загрузка старых/повреждённых `.dat` без ручного восстановления.
- Убраны дубли строк и зависимость от повторного lock/restart.

#### 3) Иконки и сборка по платформам

- Пересобраны иконки из нового исходника.
- Поддержаны нативные форматы:
  - macOS: `I2PChat.icns`;
  - Windows: `i2pchat.ico`;
  - base PNG: `icon.png`.
- Обновлены build-скрипты и spec-файл для корректного выбора иконок.

### Итог

Релиз `v0.5.0` делает приложение визуально цельным и устраняет практический баг profile lock:

- современный UI и темы, одинаково предсказуемые на всех ОС;
- корректный `Lock to peer` с первого раза и стабильная загрузка профиля.
- security-hardening: проверка profile name по whitelist, path confinement для профильных файлов, TOFU-предупреждение об отсутствии OOB-верификации, и безопасное сохранение входящих файлов без overwrite.

---

## EN

### Context

`v0.5.0` combines a UI refresh with profile persistence hardening:

- a unified macOS-inspired visual language across Linux/macOS/Windows;
- reliable `Lock to peer` behavior without repeated user actions.

### Implemented

#### 1) Cross-platform macOS 26 style UI

- Added and balanced `ligth` and `night` themes.
- Moved theme switching to the main window (next to status text).
- Unified popup behavior and file/image transfer cards.
- Made status text more informative and width-adaptive.

#### 2) Profile and `Lock to peer` fixes

- Fixed first-lock flow that could save malformed `.dat` state.
- Standardized `.dat` format:
  - line 1 — profile private key;
  - line 2 — pinned peer (`stored peer`).
- Improved compatibility with previously malformed `.dat` files.
- Removed duplicate writes and repeated lock/restart dependency.

#### 3) Icons and build pipeline

- Regenerated icons from the new source artwork.
- Added native icon targets:
  - macOS: `I2PChat.icns`;
  - Windows: `i2pchat.ico`;
  - base PNG: `icon.png`.
- Updated build scripts and spec to pick proper icon formats per platform.

### Summary

`v0.5.0` delivers a cleaner cross-platform UX and fixes the practical profile-lock issue:

- modern, consistent UI and themes on all target OSes;
- deterministic `Lock to peer` behavior and reliable profile restore.
- security hardening: profile-name whitelist, profile-path confinement, explicit TOFU warning for non-OOB identities, and collision-safe incoming file naming without overwrite.

