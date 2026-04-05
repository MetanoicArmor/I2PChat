# Распространение через менеджеры пакетов

Здесь лежат **шаблоны и инструкции** для Homebrew (cask), winget, AUR, **`.deb` (Debian/Ubuntu)** и **RPM/COPR (Fedora)**. Исходники приложения не обязаны жить в этих форматах — достаточно стабильных **GitHub Releases** с zip/AppImage.

| Платформа | Каталог | Действие мейнтейнера |
|-----------|---------|----------------------|
| macOS (arm64) | [`homebrew/`](homebrew/) | Отдельный tap `homebrew-i2pchat` или PR в `homebrew-cask`: cask **`i2pchat`** (GUI `.app`), cask **`i2pchat-tui`** (TUI-only zip) |
| Windows | [`winget/`](winget/) | PR в [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs): **`MetanoicArmor.I2PChat`** (GUI zip), **`MetanoicArmor.I2PChat.TUI`** — шаблоны в [`winget-tui/`](winget-tui/) |
| Arch | [`aur/`](aur/) | **`i2pchat-bin`** (AppImage), **`i2pchat-tui-bin`** (Linux TUI zip → `/opt/i2pchat-tui`) |
| Flatpak | [`flatpak/`](flatpak/) | Шаблоны `net.i2pchat.I2PChat` / `net.i2pchat.I2PChat.TUI` и README для PR в [Flathub](https://github.com/flathub/flathub) |
| Debian/Ubuntu | [`debian/`](debian/) | Скрипт [`build-deb-from-appimage.sh`](debian/build-deb-from-appimage.sh); при публикации Release CI прикрепляет `.deb` ([`release-deb.yml`](../.github/workflows/release-deb.yml)); опционально свой apt-repo / PPA / Flatpak |
| Fedora | [`fedora/`](fedora/) | RPM из релизного zip — [`i2pchat.spec`](fedora/i2pchat.spec), публикация в [COPR](https://copr.fedorainfracloud.org/) |

## Версии и checksums

Файлы в этом каталоге привязаны к **последнему опубликованному на GitHub** релизу на момент правки (см. `version` / `pkgver` в соответствующих файлах). Корневой файл [`VERSION`](../VERSION) в репозитории может опережать тег — после публикации `vX.Y.Z` обновите манифесты.

Скрипт:

```bash
./packaging/refresh-checksums.sh          # latest release
./packaging/refresh-checksums.sh v1.2.3   # конкретный тег
```

выводит SHA256 для **шести** релизных zip (GUI + TUI на каждой ОС) и для `icon.png`, плюс строки для cask.

## См. также

- [**docs/INSTALL.md**](../docs/INSTALL.md) — краткая установка с релизов по платформам (для пользователей).
- Корневой README: **Prebuilt binaries** и **Quick Start** — официальные ссылки на сборки; краткая заметка про сторонние менеджеры **без** мейнтенерских инструкций.
