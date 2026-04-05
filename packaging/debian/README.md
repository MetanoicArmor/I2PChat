# Debian / Ubuntu (.deb)

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя; реалистичные варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` на GitHub Release** | Один файл, `sudo apt install ./i2pchat_*_amd64.deb` | Нужно собирать при каждом релизе (локально или CI) |
| **Свой .deb + apt-репозиторий** (GitHub Pages, Packagecloud) | Полный контроль, `apt install` после добавления источника | Подпись `Release` (GPG) — в **этом** репо: [**`packaging/apt/`**](../apt/README.md) (ветка `gh-pages` + секреты) |
| **PPA (Launchpad)** | Знакомый путь для Ubuntu | Рецепты сборки, очередь сборки |
| **Flatpak / Flathub** | Один формат для многих дистрибутивов | Не `apt`; отдельный манифест и ревью Flathub |

**Рекомендуемый путь для пользователей:** скачать **`i2pchat_<версия>_amd64.deb`** (GUI) и при необходимости **`i2pchat-tui_<версия>_amd64.deb`** (только TUI) с [релизов GitHub](https://github.com/MetanoicArmor/I2PChat/releases), **или** собрать локально скриптами ниже.

## Сборка локального .deb из официального AppImage

Скрипт [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) собирает минимальный пакет `i2pchat` (amd64): AppImage в `/opt/i2pchat/`, симлинк `usr/bin/i2pchat`, `.desktop` и иконка.

Требования: `bash`, `curl`, `unzip`, **`dpkg-deb`** (пакет `dpkg` в Debian/Ubuntu; на macOS `.deb` не соберётся без Linux-окружения). Запуск из корня репозитория I2PChat:

```bash
# версия должна совпадать с опубликованным тегом vX.Y.Z и именем zip на GitHub
./packaging/debian/build-deb-from-appimage.sh 1.2.3

# или взять версию из первой строки файла VERSION в корне репо:
./packaging/debian/build-deb-from-appimage.sh
```

Готовый файл: `dist/i2pchat_<version>_amd64.deb`.

**Зависимости в рантайме:** AppImage может требовать FUSE или встроенный runtime в зависимости от типа образа и версии ОС; при проблемах запуска см. [документацию AppImage](https://docs.appimage.org/).

## Сборка .deb для TUI (официальный Linux TUI zip)

Скрипт [`build-tui-deb-from-release-zip.sh`](build-tui-deb-from-release-zip.sh) собирает пакет **`i2pchat-tui`**: содержимое **`I2PChat-linux-x86_64-tui-v<версия>.zip`** в `/opt/i2pchat-tui/`, команда **`i2pchat-tui`**, `.desktop` для терминала (как в AUR **`i2pchat-tui-bin`**). Требования к **glibc** такие же, как у бинарника в zip (если zip собран на очень новой системе, на старой LTS возможна ошибка `GLIBC_* not found` — пересоберите zip на Ubuntu 22.04, см. workflow **Build Linux release artifacts** в [`.github/workflows/build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml)).

```bash
./packaging/debian/build-tui-deb-from-release-zip.sh 1.2.3
# или версия из файла VERSION:
./packaging/debian/build-tui-deb-from-release-zip.sh
```

Готовый файл: `dist/i2pchat-tui_<version>_amd64.deb`.

## Автоматическая сборка в CI

При **публикации** GitHub Release (событие `published`) job **deb** в workflow [`.github/workflows/release-linux-pkgs.yml`](../../.github/workflows/release-linux-pkgs.yml) ждёт **`I2PChat-linux-x86_64-v<версия>.zip`** и **`I2PChat-linux-x86_64-tui-v<версия>.zip`**, собирает **`i2pchat_…_amd64.deb`** и **`i2pchat-tui_…_amd64.deb`**, загружает их на релиз.

Условие: в момент срабатывания workflow **оба** linux zip уже должны быть в списке ассетов релиза. Событие `release: published` иногда приходит **раньше**, чем GitHub успевает отдать большие ассеты; в CI добавлено **ожидание** по API. Для форка используется `GITHUB_REPOSITORY` (или `I2PCHAT_RELEASE_REPO=owner/name` при локальном запуске).

Если релиз уже опубликован **без** `.deb`, в GitHub Actions запустите workflow **Release Linux packages** вручную (**workflow_dispatch**) и укажите тег `vX.Y.Z` — на релизе должны быть оба zip: **`I2PChat-linux-x86_64-vX.Y.Z.zip`** и **`I2PChat-linux-x86_64-tui-vX.Y.Z.zip`**.

## Публикация apt-репозитория (кратко)

1. Соберите `.deb` для каждого релиза.
2. Создайте структуру `dists/stable/main/binary-amd64/` и индексы (`apt-ftparchive` или `reprepro`).
3. Подпишите `Release` (GPG) и выложите на HTTPS.
4. Пользователи добавляют `deb [signed-by=…] https://… stable main` и ключ.

Пошаговая автоматизация в **этом** репозитории — [**`packaging/apt/README.md`**](../apt/README.md). Ориентиры по формату: [Debian Repository Format](https://wiki.debian.org/DebianRepository/Format), `reprepro`.
