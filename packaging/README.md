# Распространение через менеджеры пакетов

Здесь лежат **шаблоны и инструкции** для Homebrew (cask), winget, AUR, **`.deb` (Debian/Ubuntu)** и **RPM/COPR (Fedora)**. Исходники приложения не обязаны жить в этих форматах — достаточно стабильных **GitHub Releases** с zip/AppImage.

## Статус публикации (v1.2.3, 2026-04)

| Канал | Состояние |
|-------|-----------|
| **winget** | PR в [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs): [MetanoicArmor.I2PChat 1.2.3](https://github.com/microsoft/winget-pkgs/pull/355476), [MetanoicArmor.I2PChat.TUI 1.2.3](https://github.com/microsoft/winget-pkgs/pull/355477) — ждут ревью/merge. |
| **Homebrew tap** | Репозиторий [MetanoicArmor/homebrew-i2pchat](https://github.com/MetanoicArmor/homebrew-i2pchat): `brew tap MetanoicArmor/i2pchat`. |
| **AUR** | **Опубликовано:** [i2pchat-bin](https://aur.archlinux.org/packages/i2pchat-bin), [i2pchat-tui-bin](https://aur.archlinux.org/packages/i2pchat-tui-bin). Установка (например **yay**): `yay -S i2pchat-bin`, `yay -S i2pchat-tui-bin`. Шаблоны в репо: [`aur/i2pchat-bin/`](aur/i2pchat-bin/), [`aur/i2pchat-tui-bin/`](aur/i2pchat-tui-bin/); обновление пакетов на AUR — см. [aur/README.md](aur/README.md). |
| **Flatpak / COPR** | Flatpak — шаблоны в репо ([flatpak/README.md](flatpak/README.md)). COPR — по желанию мейнтейнера ([fedora/i2pchat.spec](fedora/i2pchat.spec)). |
| **`.deb` на GitHub Release** | Workflow [release-linux-pkgs.yml](../.github/workflows/release-linux-pkgs.yml): прикрепляет **`i2pchat_…_amd64.deb`** и **`i2pchat-tui_…_amd64.deb`**. См. [debian/README.md](debian/README.md). **`.rpm`** — локально или COPR, см. [fedora/README.md](fedora/README.md). |
| **apt + GitHub Pages (это же репо)** | [`apt/README.md`](apt/README.md): Pages → **GitHub Actions**, секрет **`APT_REPO_GPG_PRIVATE_KEY`**; **Release Linux packages** (job **deploy-apt-site**) / **Publish apt mirror**. |

| Платформа | Каталог | Действие мейнтейнера |
|-----------|---------|----------------------|
| macOS (arm64) | [`homebrew/`](homebrew/) | Отдельный tap `homebrew-i2pchat` или PR в `homebrew-cask`: cask **`i2pchat`** (GUI `.app`), cask **`i2pchat-tui`** (TUI-only zip) |
| Windows | [`winget/`](winget/) | PR в [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs): **`MetanoicArmor.I2PChat`** (GUI zip), **`MetanoicArmor.I2PChat.TUI`** — шаблоны в [`winget-tui/`](winget-tui/) |
| Arch | [`aur/`](aur/) | **`i2pchat-bin`** (AppImage), **`i2pchat-tui-bin`** (Linux TUI zip → `/opt/i2pchat-tui`) |
| Flatpak | [`flatpak/`](flatpak/) | Шаблоны `net.i2pchat.I2PChat` / `net.i2pchat.I2PChat.TUI` и README для PR в [Flathub](https://github.com/flathub/flathub) |
| Debian/Ubuntu | [`debian/`](debian/) | Скрипт [`build-deb-from-appimage.sh`](debian/build-deb-from-appimage.sh); CI — [`release-linux-pkgs.yml`](../.github/workflows/release-linux-pkgs.yml); **apt + Pages в этом репо** — [`apt/`](apt/README.md); опционально PPA / Flatpak |
| Fedora | [`fedora/`](fedora/) | [`i2pchat.spec`](fedora/i2pchat.spec), скрипт [`build-rpm-from-release.sh`](fedora/build-rpm-from-release.sh); опционально [COPR](https://copr.fedorainfracloud.org/) |

## Версии и checksums

Файлы в этом каталоге привязаны к **последнему опубликованному на GitHub** релизу на момент правки (см. `version` / `pkgver` в соответствующих файлах). Корневой файл [`VERSION`](../VERSION) в репозитории может опережать тег — после публикации `vX.Y.Z` обновите манифесты.

Скрипт:

```bash
./packaging/refresh-checksums.sh          # latest release
./packaging/refresh-checksums.sh v1.2.3   # конкретный тег
```

выводит SHA256 для **шести** релизных zip (GUI + TUI на каждой ОС) и для `icon.png`, плюс строки для cask.

Если на релизе **вручную заменили только Linux zip**, обновите **`SHA256SUMS`** (два ряда, как после `build-linux.sh`):

```bash
./packaging/refresh-linux-sha256sums.sh v1.2.3   # качает с GitHub, пишет dist/SHA256SUMS
gh release upload v1.2.3 dist/SHA256SUMS --clobber
```

При публикации **`SHA256SUMS.asc`** пересоздайте подпись от нового файла.

## См. также

- [**docs/INSTALL.md**](../docs/INSTALL.md) — краткая установка с релизов по платформам (для пользователей).
- Корневой README: **Prebuilt binaries** и **Quick Start** — официальные ссылки на сборки; краткая заметка про сторонние менеджеры **без** мейнтенерских инструкций.
