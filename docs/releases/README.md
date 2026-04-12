# Release notes

Versioned release write-ups for I2PChat live in this directory (`docs/releases/`).

| Version | Notes |
|--------|--------|
| **1.3.1** | [RELEASE_1.3.1.md](RELEASE_1.3.1.md) — **Intel macOS** bundled `i2pd` (`darwin-x64`); BlindBox runtime retry / group polish; **Nix** Qt + profile install; **icons** (`image2.png`); GUI checkbox / group editor fixes |
| **1.3.0** | [RELEASE_1.3.0.md](RELEASE_1.3.0.md) — **Text groups** (live + BlindBox fan-out); **Saved peers** inbound; parallel **live sessions**; `activate_peer_context`; router/UI polish; docs |
| **1.2.6** | [RELEASE_1.2.6.md](RELEASE_1.2.6.md) — **SessionManager** per-peer transport lifecycle; core delegation; outbound policy; shutdown/ACK hooks; Qt Send label after handshake |
| **1.2.5** | [RELEASE_1.2.5.md](RELEASE_1.2.5.md) — GUI does not crash when **I2P session fails** (**`self.core is None`**); status/connect/send/lock UI guarded |
| **1.2.4** | [RELEASE_1.2.4.md](RELEASE_1.2.4.md) — internal **`i2pchat.sam`** (no PyPI i2plib); **uv** + **`uv.lock`**; BlindBox SESSION parsing vs i2pd |
| **1.2.3** | [RELEASE_1.2.3.md](RELEASE_1.2.3.md) — status `My:` local address (Qt+TUI); Qt keeps startup/Online lines with no peer; identity saved as system line |
| **1.2.2** | [RELEASE_1.2.2.md](RELEASE_1.2.2.md) — `python -m i2pchat.gui` / `i2pchat.tui`, prebuilt **`I2PChat-tui`** (Win/Linux/macOS), TUI fixes, Qt offline history without wiping startup lines |
| **1.2.1** | [RELEASE_1.2.1.md](RELEASE_1.2.1.md) — Fluent UI Emoji (replacing Noto), system/info chat styling, lifecycle lines as info, Windows Segoe UI status font |
| **1.2.0** | [RELEASE_1.2.0.md](RELEASE_1.2.0.md) — bundled/system i2pd backend, router dialog + shortcuts, bundled packaging, update-check proxy alignment |
| **1.1.4** | [RELEASE_1.1.4.md](RELEASE_1.1.4.md) — BlindBox diagnostics rewrite, production daemon package, one-shot install flow, lower slow-replica latency |
| **1.1.3** | [RELEASE_1.1.3.md](RELEASE_1.1.3.md) — vNext-only wire codec; remove `I2PCHAT_LEGACY_COMPAT` / legacy parser |
| **1.1.2** | [RELEASE_1.1.2.md](RELEASE_1.1.2.md) — legacy framing gated to locked peer; file/G send `to_thread`; ACK soft-drain; audits |
| **1.1.1** | [RELEASE_1.1.1.md](RELEASE_1.1.1.md) — long message chunking, compose splitter, file transfer polish, theme auto, docs |
| **1.1.0** | [RELEASE_1.1.0.md](RELEASE_1.1.0.md) — keyboard shortcuts, I2P update check, compose/search UX, VERSION discovery |
| **1.0.1** | [RELEASE_1.0.1.md](RELEASE_1.0.1.md) — profile switch race fixes; notification menu (Privacy + sound only) |
| **1.0.0** | [RELEASE_1.0.0.md](RELEASE_1.0.0.md) — stable niche milestone; backup/restore, retention, privacy mode, drag-and-drop, reliability |
| **0.9.0** | [RELEASE_0.9.0.md](RELEASE_0.9.0.md) — profile/history backup, retention UI, privacy/PIN, DnD, transfer auto-retry (feature slice before 1.0.0 tag) |
| **0.8.0** | [RELEASE_0.8.0.md](RELEASE_0.8.0.md) — delivery states, trust/key-change flows, BlindBox diagnostics, retry failed sends |
| **0.7.0** | [RELEASE_0.7.0.md](RELEASE_0.7.0.md) — contacts v2, previews, last active peer, in-chat search, trust card MVP |
| 0.6.5 | [RELEASE_0.6.5.md](RELEASE_0.6.5.md) — UX polish milestone (drafts, unread, status, context menu, notifications) |
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
6. **Footer (required):** after the last section (including RU), append a horizontal rule `---` and the **Cross-platform** block below. Replace every `vX.Y.Z` with the real tag segment (e.g. `v0.6.5`) so filenames match [`README.md`](../../README.md) and published GitHub assets.

---

## Как оформить релиз (сопровождение)

1. **Создать** файл `docs/releases/RELEASE_X.Y.Z.md` (ориентир — предыдущий релиз: заголовок, блоки **EN** / **RU**, кратко, изменения для пользователя, тесты, совместимость).
2. **Добавить** строку в таблицу выше (новая версия сразу под актуальной).
3. **Поднять** [`VERSION`](../../VERSION) в корне репозитория.
4. **Обновить** ссылки на сборки в [`README.md`](../../README.md), если в имени архива фигурирует версия.
5. **Поставить** тег `vX.Y.Z` на GitHub после публикации артефактов (по желанию, но удобно).
6. **Футер (обязательно):** после последнего блока (включая RU) добавить `---` и блок **Cross-platform** ниже. Во всех именах архивов подставить фактический суффикс `vX.Y.Z` (например `v0.6.5`), как в [`README.md`](../../README.md) и на странице релиза GitHub.

---

## Cross-platform footer (copy-paste)

Use at the **end** of `RELEASE_X.Y.Z.md` (English block). Replace the placeholder `vX.Y.Z` in each zip name with the real suffix, e.g. `v0.6.5` for release **0.6.5** (must match [`README.md`](../../README.md) and uploaded assets).

```markdown
---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-vX.Y.Z.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-vX.Y.Z.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-vX.Y.Z.zip` | Unzip → open I2PChat.app |
```

Row order: **Windows → Linux → macOS**. When publishing the GitHub release, `gh release edit vX.Y.Z --notes-file docs/releases/RELEASE_X.Y.Z.md` keeps the page in sync with the repo.
