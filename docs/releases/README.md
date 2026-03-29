# Release notes

Versioned release write-ups for I2PChat live in this directory (`docs/releases/`).

| Version | Notes |
|--------|--------|
| **0.6.5** | [RELEASE_0.6.5.md](RELEASE_0.6.5.md) — UX polish milestone (drafts, unread, status, context menu, notifications) |
| 0.6.4 | [RELEASE_0.6.4.md](RELEASE_0.6.4.md) |
| 0.6.3 | [RELEASE_0.6.3.md](RELEASE_0.6.3.md) |
| 0.6.2 | [RELEASE_0.6.2.md](RELEASE_0.6.2.md) |
| 0.6.1 | [RELEASE_0.6.1.md](RELEASE_0.6.1.md) |
| 0.6.0 | [RELEASE_0.6.0.md](RELEASE_0.6.0.md) — BlindBox / offline delivery |
| 0.5.x | [RELEASE_0.5.2.md](RELEASE_0.5.2.md), [RELEASE_0.5.1.md](RELEASE_0.5.1.md), [RELEASE_0.5.0.md](RELEASE_0.5.0.md) |
| 0.4.0 | [RELEASE_0.4.0.md](RELEASE_0.4.0.md) |
| 0.3.x | [RELEASE_0.3.1.md](RELEASE_0.3.1.md), [RELEASE_0.3.0.md](RELEASE_0.3.0.md) |
| 0.2.1 | [RELEASE_0.2.1.md](RELEASE_0.2.1.md) |
| Legacy v2 security | [RELEASE.md](RELEASE.md) |

---

## How to add a release (maintainers)

1. **Create** `docs/releases/RELEASE_X.Y.Z.md` (use the previous file as a template: title, **EN** / **RU** sections, summary, user-visible changes, tests, compatibility).
2. **Register** the version in the table above (new row near the top, after the latest stable line).
3. **Bump** repo version in root [`VERSION`](../../VERSION) when cutting the release.
4. **Update** prebuilt download links in root [`README.md`](../../README.md) if artifact names include the version string.
5. **Tag** `vX.Y.Z` on GitHub when binaries are published (optional but recommended).

---

## Как оформить релиз (сопровождение)

1. **Создать** файл `docs/releases/RELEASE_X.Y.Z.md` (ориентир — предыдущий релиз: заголовок, блоки **EN** / **RU**, кратко, изменения для пользователя, тесты, совместимость).
2. **Добавить** строку в таблицу выше (новая версия сразу под актуальной).
3. **Поднять** [`VERSION`](../../VERSION) в корне репозитория.
4. **Обновить** ссылки на сборки в [`README.md`](../../README.md), если в имени архива фигурирует версия.
5. **Поставить** тег `vX.Y.Z` на GitHub после публикации артефактов (по желанию, но удобно).
