# Debian / Ubuntu (.deb)

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя; реалистичные варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` на GitHub Release** | `sudo apt install ./i2pchat_*_amd64.deb` или `./i2pchat_*_arm64.deb` на ARM | Нужно собирать при каждом релизе (локально или CI) |
| **Свой .deb + apt-репозиторий** (GitHub Pages, Packagecloud) | Полный контроль, `apt install` после добавления источника | Подпись `Release` (GPG) — в **этом** репо: [**`packaging/apt/`**](../apt/README.md) (ветка `gh-pages` + секреты) |
| **PPA (Launchpad)** | Знакомый путь для Ubuntu | Рецепты сборки, очередь сборки |
| **Flatpak / Flathub** | Один формат для многих дистрибутивов | Не `apt`; отдельный манифест и ревью Flathub |

**Рекомендуемый путь для пользователей:** скачать **`i2pchat_<версия>_amd64.deb`** / **`_arm64.deb`** (GUI) и при необходимости **`i2pchat-tui_*`** той же архитектуры с [релизов GitHub](https://github.com/MetanoicArmor/I2PChat/releases), **или** собрать локально скриптами ниже.

## Сборка локального .deb из официального AppImage

Скрипт [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) собирает минимальный пакет `i2pchat`: AppImage в `/opt/i2pchat/`, симлинк `usr/bin/i2pchat`, `.desktop` и иконка. По умолчанию **amd64** (zip **`I2PChat-linux-x86_64-v*.zip`**). Для **arm64** (zip **`I2PChat-linux-aarch64-v*.zip`**): `I2PCHAT_DEB_ARCH=arm64 ./packaging/debian/build-deb-from-appimage.sh 1.2.3` → `dist/i2pchat_<version>_arm64.deb`.

Требования: `bash`, `curl`, `unzip`, **`dpkg-deb`** (пакет `dpkg` в Debian/Ubuntu; на macOS `.deb` не соберётся без Linux-окружения). Запуск из корня репозитория I2PChat:

```bash
# версия должна совпадать с опубликованным тегом vX.Y.Z и именем zip на GitHub
./packaging/debian/build-deb-from-appimage.sh 1.2.3

# или взять версию из первой строки файла VERSION в корне репо:
./packaging/debian/build-deb-from-appimage.sh
```

Готовый файл: `dist/i2pchat_<version>_<amd64|arm64>.deb`.

**Зависимости в рантайме:** AppImage может требовать FUSE или встроенный runtime в зависимости от типа образа и версии ОС; при проблемах запуска см. [документацию AppImage](https://docs.appimage.org/).

## Сборка .deb для TUI (официальный Linux TUI zip)

Скрипт [`build-tui-deb-from-release-zip.sh`](build-tui-deb-from-release-zip.sh) собирает пакет **`i2pchat-tui`**: содержимое официального TUI zip в `/opt/i2pchat-tui/`, команда **`i2pchat-tui`**, `.desktop` для терминала. **amd64:** **`I2PChat-linux-x86_64-tui-v*.zip`**. **arm64:** `I2PCHAT_DEB_ARCH=arm64` и zip **`I2PChat-linux-aarch64-tui-v*.zip`**. Требования к **glibc** — как у бинарника в zip (см. workflow **Build Linux release artifacts** в [`.github/workflows/build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml)).

```bash
./packaging/debian/build-tui-deb-from-release-zip.sh 1.2.3
# или версия из файла VERSION:
./packaging/debian/build-tui-deb-from-release-zip.sh
```

Готовый файл: `dist/i2pchat-tui_<version>_<amd64|arm64>.deb`.

## Автоматическая сборка в CI

При **публикации** GitHub Release (событие `published`) workflow [`.github/workflows/release-linux-pkgs.yml`](../../.github/workflows/release-linux-pkgs.yml) запускает два job’а: **`deb-amd64`** (ждёт x86_64 zip, выкладывает **`_amd64.deb`**) и **`deb-arm64`** (ждёт **`I2PChat-linux-aarch64-v*.zip`** и **`*-tui-*`**, выкладывает **`_arm64.deb`**). Зеркало **apt** на GitHub Pages по-прежнему строится только из **amd64** `.deb` (см. [`packaging/apt/`](../apt/README.md)).

Условие: нужные zip уже в ассетах релиза; для каждого job’а — **ожидание** по API. Для форка: `GITHUB_REPOSITORY` / `I2PCHAT_RELEASE_REPO`.

**workflow_dispatch** с тегом `vX.Y.Z`: для **arm64** `.deb` на релизе должны быть **`I2PChat-linux-aarch64-v*.zip`** и **`*-tui-*`**; иначе job **`deb-arm64`** завершится ошибкой (**`continue-on-error`**, amd64 и apt не пострадают).

## Публикация apt-репозитория (кратко)

1. Соберите `.deb` для каждого релиза.
2. Создайте структуру `dists/stable/main/binary-amd64/` и индексы (`apt-ftparchive` или `reprepro`).
3. Подпишите `Release` (GPG) и выложите на HTTPS.
4. Пользователи добавляют `deb [signed-by=…] https://… stable main` и ключ.

Пошаговая автоматизация в **этом** репозитории — [**`packaging/apt/README.md`**](../apt/README.md). Ориентиры по формату: [Debian Repository Format](https://wiki.debian.org/DebianRepository/Format), `reprepro`.
