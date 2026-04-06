# Debian / Ubuntu (.deb)

**I2PChat** — клиент для сети I2P: есть **оконный** (графический) вариант и отдельно **текстовый** — работа **в терминале**, без окон. В пакетах и документации его часто обозначают как **TUI** (*terminal user interface*); это не отдельная «магия», а тот же чат, но в консоли (без PyQt6).

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя; реалистичные варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` на GitHub Release** | Один файл, `sudo apt install ./i2pchat_*.deb` | Нужно обновлять при каждом релизе |
| **Свой .deb + apt-репозиторий** (GitHub Pages и т.п.) | После настройки — обычный `apt install` | Подпись репозитория (GPG); в этом репо — [**`packaging/apt/`**](../apt/README.md) |
| **PPA (Launchpad)** | Знакомый путь для Ubuntu | Рецепты, очередь сборки |
| **Flatpak / Flathub** | Один формат для многих дистрибутивов | Не `apt`; отдельный манифест и ревью Flathub |

## Установка через apt (подписанное зеркало)

Сейчас в зеркале пакеты **amd64**. На **ARM64** ставьте `*_arm64.deb` с релизов (см. ниже).

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://metanoicarmor.github.io/I2PChat/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://metanoicarmor.github.io/I2PChat stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
sudo apt update
sudo apt install i2pchat       # графический клиент
sudo apt install i2pchat-tui   # версия для терминала (TUI)
```

Настройка зеркала и секреты CI — в [**`packaging/apt/README.md`**](../apt/README.md).

## Скачать `.deb` с релиза

Готовые пакеты лежат в **Assets** на [странице релизов](https://github.com/MetanoicArmor/I2PChat/releases): **GUI** — `i2pchat_<версия>_{amd64|arm64}.deb`, **терминал** — `i2pchat-tui_<версия>_{amd64|arm64}.deb`. Установка: `sudo apt install ./имя_файла.deb`.

## Сборка локального `.deb` из официальных zip

Нужны `bash`, `curl`, `unzip`, **`dpkg-deb`** (на macOS без Linux-окружения `.deb` не собрать). Запуск из **корня** репозитория I2PChat.

**GUI** (AppImage из zip) — [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh): по умолчанию **amd64** (`I2PChat-linux-x86_64-v*.zip`), для **arm64**:  
`I2PCHAT_DEB_ARCH=arm64 ./packaging/debian/build-deb-from-appimage.sh 1.2.3`  
(или без аргумента — версия из файла `VERSION`).

**Терминал (TUI)** — [`build-tui-deb-from-release-zip.sh`](build-tui-deb-from-release-zip.sh): те же правила, для arm64 — `I2PCHAT_DEB_ARCH=arm64`.

**Рантайм:** у AppImage возможны требования к FUSE / окружению; см. [документацию AppImage](https://docs.appimage.org/). У бинарников в zip — тот же **glibc**, что и у сборочной системы (для предсказуемости релизы собирают на Ubuntu 22.04 в CI, см. [`.github/workflows/build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml)).

## Автоматическая сборка в CI

При публикации релиза workflow [**`release-linux-pkgs.yml`**](../../.github/workflows/release-linux-pkgs.yml) собирает `.deb` из zip на релизе (**amd64** и при наличии файлов — **arm64**). Повторная выгрузка: **Actions → Release Linux packages → Run workflow** с тегом `vX.Y.Z`. Зеркало apt на Pages обновляется из **amd64**-пакетов при заданном секрете `APT_REPO_GPG_PRIVATE_KEY` — подробности в **`packaging/apt/`**.

## См. также

- [**`packaging/README.md`**](../README.md) — обзор каналов распространения  
- [**`docs/INSTALL.md`**](../../docs/INSTALL.md) — установка с релизов по платформам
