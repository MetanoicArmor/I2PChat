# I2PChat v1.0.0 — Stable niche release

## EN

### Summary

`v1.0.0` closes the pre-1.0 roadmap by combining the earlier conversation,
trust, delivery, and diagnostics work with the remaining portability,
privacy, and reliability layer: **encrypted profile backups**, **history
backup/restore**, **history retention controls**, **privacy mode**, and
**drag-and-drop attachments**.

This release does **not** widen scope into group chat or multi-device sync.
Instead, it marks the point where the existing privacy-focused single-device
conversation model is coherent enough to be treated as a stable niche release.

### User-visible changes

- **Encrypted profile backup / restore (issues 19, 20)**  
  The **⋯** menu now exposes **Export profile backup…** and
  **Import profile backup…**. Backups are password-protected, include the
  current `.dat` profile plus supported sidecar data, and restore into a safe
  profile name if a collision already exists.

- **Encrypted history backup / restore (issue 21)**  
  Users can export only local encrypted history or import it back later. The
  import flow explicitly asks whether matching history files should be
  overwritten or whether only missing files should be restored.

- **History retention controls (issue 22)**  
  **⋯ → History retention…** lets users configure both:
  - maximum saved messages per peer;
  - maximum history age in days (`0` = keep by count only).  
  Retention is applied predictably before encrypted history is persisted.

- **Privacy mode (issue 23)**  
  **⋯ → Privacy mode: ON/OFF** gives a quick local privacy preset. In the first
  stable version it forces hidden notification previews plus focused-window
  quiet mode and keeps those settings persisted across restarts.

- **Drag-and-drop attachments (issue 24)**  
  Files and supported images can now be dropped onto the compose field. Images
  follow the same validation path as existing clipboard / picker sends; other
  files route through the standard secure file transfer path.

- **Transfer UX and reliability hardening (issues 25, 26)**  
  The earlier retry and delivery-state work is now paired with broader test
  coverage for encrypted history retention and backup flows. The pre-1.0
  release criteria are backed by a green local test suite and the existing CI
  gate on GitHub.

### Release scope

`v1.0.0` brings the repository to the minimum state previously described in the
roadmap:

- convenient dialog and contact workflow;
- searchable local history;
- understandable delivery states;
- explicit trust/key-change UX;
- usable offline delivery diagnostics;
- practical backup and restore paths;
- stronger reliability coverage around protocol, history, and transfers.

### Validation

Validated in this repository revision with:

- `python3 -m unittest tests.test_chat_history tests.test_chat_history_v2 tests.test_profile_import_overwrite tests.test_profile_backup`
- `python3 -m pytest tests/ -q --tb=short`

Result during release prep:

- **228 passed**
- **1 skipped** (GUI smoke in the cloud container because a system Qt runtime
  library, `libEGL.so.1`, is absent there)

### Compatibility

`v1.0.0` is the first stable release line for the current architecture.

- Existing local encrypted history remains profile-bound and encrypted at rest.
- Backup bundles are password-protected export artifacts intended for user
  migration / restore flows.
- Protocol scope remains intentionally narrow: no group chat, no multi-device
  sync, no plugin surface in 1.0.

### Repository layout

- Release notes live under **`docs/releases/`**.
- Feature-level notes for the **0.9.0** slice: [RELEASE_0.9.0.md](RELEASE_0.9.0.md).
- **Release integrity:** ship official binaries with `I2PCHAT_REQUIRE_GPG=1` and publish `SHA256SUMS` + `SHA256SUMS.asc` (see root [README.md](../../README.md) *Verify release artifacts*).

---

## RU

### Кратко

`v1.0.0` закрывает pre-1.0 слой roadmap: к уже реализованным диалогам,
доставке, trust-UX и диагностике добавлены недостающие возможности для
переносимости, локальной приватности и эксплуатационной зрелости:
**зашифрованные backup профиля**, **backup/restore истории**,
**настройки retention**, **privacy mode** и **drag-and-drop вложений**.

Это сознательно **не** релиз про расширение продуктового масштаба
(группы/мультидевайс). Это релиз про то, что существующая privacy-focused
модель общения стала достаточно цельной и предсказуемой для стабильной ветки.

### Что видит пользователь

- **Зашифрованный backup профиля / восстановление (пункты 19, 20)**  
  В меню **⋯** появились **Export profile backup…** и
  **Import profile backup…**. Backup защищён паролем, включает текущий `.dat`
  профиль и поддерживаемые sidecar-данные, а при конфликте имени импортируется
  в безопасное свободное имя.

- **Зашифрованный backup / import истории (пункт 21)**  
  Историю можно экспортировать отдельно от профиля. При импорте явно
  запрашивается поведение при конфликте: перезаписать совпадающие history-файлы
  или восстановить только отсутствующие.

- **Настройки retention истории (пункт 22)**  
  Пункт **⋯ → History retention…** позволяет настраивать:
  - максимум сохранённых сообщений на peer;
  - максимальный возраст истории в днях (`0` = ограничение только по числу).  
  Политика применяется предсказуемо перед записью зашифрованной истории.

- **Privacy mode (пункт 23)**  
  Быстрый переключатель **⋯ → Privacy mode: ON/OFF** включает локальный
  privacy-presет. В первой стабильной версии он принудительно скрывает preview
  уведомлений и включает тихий режим при фокусе окна; состояние сохраняется
  между перезапусками.

- **Drag-and-drop вложений (пункт 24)**  
  Файлы и поддерживаемые изображения теперь можно перетаскивать прямо в поле
  ввода. Картинки проходят ту же проверку, что и при вставке/выборе через
  диалог; остальные файлы идут через обычный secure file transfer.

- **Улучшение transfer UX и надёжности (пункты 25, 26)**  
  Более ранние delivery/retry улучшения теперь подкреплены расширенным
  покрытием тестами для retention и backup-сценариев. Формальные критерии
  pre-1.0 подтверждены локальным прогоном тестов и существующим CI.

### Состояние релиза

`v1.0.0` приводит проект к минимальному состоянию, заявленному в roadmap:

- удобная работа с диалогами и контактами;
- поиск по локальной истории;
- понятные статусы доставки;
- явный UX для trust / смены ключей;
- рабочая диагностика офлайн-доставки;
- практичные сценарии backup и восстановления;
- более сильное покрытие по надёжности вокруг протокола, истории и transfer flows.

### Проверки

Во время подготовки релиза проверялись:

- `python3 -m unittest tests.test_chat_history tests.test_chat_history_v2 tests.test_profile_import_overwrite tests.test_profile_backup`
- `python3 -m pytest tests/ -q --tb=short`

Результат:

- **228 passed**
- **1 skipped** (GUI smoke в cloud-контейнере из-за отсутствующей системной Qt
  библиотеки `libEGL.so.1`)

### Совместимость

`v1.0.0` — первая стабильная линия для текущей архитектуры.

- Локальная зашифрованная история по-прежнему привязана к профилю и остаётся
  зашифрованной на диске.
- Backup bundles — это пароль-защищённые экспортные артефакты для переноса и
  восстановления пользовательских данных.
- Область протокола специально остаётся узкой: в 1.0 нет групповых чатов,
  мультидевайс-синхронизации и плагинной поверхности.

### Структура репозитория

- Описания релизов находятся в **`docs/releases/`**.
- Детали вехи **0.9.0**: [RELEASE_0.9.0.md](RELEASE_0.9.0.md).
- **Целостность сборок:** для официальных бинарников задайте `I2PCHAT_REQUIRE_GPG=1` и публикуйте `SHA256SUMS` и `SHA256SUMS.asc` рядом с архивами (см. [README.md](../../README.md), раздел *Verify release artifacts*).

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.0.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.0.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.0.0.zip` | Unzip → open I2PChat.app |
