# Debian / Ubuntu (.deb)

Кратко: **I2PChat** — оконный клиент (PyQt) и отдельно **тот же чат в терминале** (часто подписан как **TUI**, *terminal user interface* — без окон и без PyQt6).

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя. Варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` с GitHub Release** | `sudo apt install ./пакет.deb` | Обновлять вручную с каждым релизом |
| **Свой apt (GitHub Pages и т.д.)** | Дальше — обычный `apt install` | GPG и инфраструктура; здесь — [**`packaging/apt/`**](../apt/README.md) |
| **PPA** | Привычно для Ubuntu | Рецепты, очередь сборки |
| **Flatpak** | Один формат на много дистрибутивов | Не `apt`; отдельный манифест |

---

## Установка через apt (подписанное зеркало)

В зеркале сейчас **amd64**. Для **arm64** скачайте `*_arm64.deb` с [релизов](https://github.com/MetanoicArmor/I2PChat/releases) и установите `sudo apt install ./файл.deb`.

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://metanoicarmor.github.io/I2PChat/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://metanoicarmor.github.io/I2PChat stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
sudo apt update
sudo apt install i2pchat        # GUI
sudo apt install i2pchat-tui    # терминал (TUI)
```

Настройка Pages, секреты CI: [**`packaging/apt/README.md`**](../apt/README.md).

---

## Скачать `.deb` с релиза

В **Assets** на [релизах](https://github.com/MetanoicArmor/I2PChat/releases): **`i2pchat_<версия>_{amd64|arm64}.deb`** (GUI), **`i2pchat-tui_<версия>_{amd64|arm64}.deb`** (терминал). Установка: `sudo apt install ./имя.deb`.

---

## Сборка `.deb` локально (из официальных zip)

Нужны `bash`, `curl`, `unzip`, **`dpkg-deb`**. Запуск из **корня** клона I2PChat (на macOS без Linux — `.deb` не собрать).

| Пакет | Скрипт | Заметки |
|--------|--------|---------|
| GUI | [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) | По умолчанию amd64 (`I2PChat-linux-x86_64-v*.zip`); arm64: `I2PCHAT_DEB_ARCH=arm64 ./packaging/debian/build-deb-from-appimage.sh [версия]` |
| Терминал (TUI) | [`build-tui-deb-from-release-zip.sh`](build-tui-deb-from-release-zip.sh) | Те же архитектуры |

Версия — аргумент или первая строка **`VERSION`** в корне репо.

**Рантайм:** AppImage может требовать FUSE и т.п. ([документация AppImage](https://docs.appimage.org/)). **glibc** у бинарников — как у хоста сборки zip; релизные zip для Linux собирают на Ubuntu 22.04 в CI ([`build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml)).

---

## CI

После публикации релиза [**`release-linux-pkgs.yml`**](../../.github/workflows/release-linux-pkgs.yml) собирает `.deb` из zip на релизе (amd64; arm64 — если есть aarch64 zip). Повтор: **Actions → Release Linux packages → Run workflow** с тегом `vX.Y.Z`. Обновление apt-зеркала на Pages — при секрете `APT_REPO_GPG_PRIVATE_KEY`, см. **`packaging/apt/`**.

---

## См. также

- [**`packaging/README.md`**](../README.md) — все каналы распространения  
- [**`docs/INSTALL.md`**](../../docs/INSTALL.md) — установка по ОС
