# Debian / Ubuntu (.deb)

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя; реалистичные варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` на GitHub Release** | Один файл, `sudo apt install ./i2pchat_*_amd64.deb` | Нужно собирать при каждом релизе (локально или CI) |
| **Свой .deb + apt-репозиторий** (GitHub Pages, Packagecloud) | Полный контроль, `apt install` после добавления источника | Подпись `Release` (GPG), инфраструктура |
| **PPA (Launchpad)** | Знакомый путь для Ubuntu | Рецепты сборки, очередь сборки |
| **Flatpak / Flathub** | Один формат для многих дистрибутивов | Не `apt`; отдельный манифест и ревью Flathub |

**Рекомендуемый путь для пользователей:** скачать **`i2pchat_<версия>_amd64.deb`** с [релизов GitHub](https://github.com/MetanoicArmor/I2PChat/releases) (если приложён) **или** официальный **Linux zip с AppImage** и при необходимости собрать `.deb` локально скриптом ниже.

## Сборка локального .deb из официального AppImage

Скрипт [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) собирает минимальный пакет `i2pchat` (amd64): AppImage в `/opt/i2pchat/`, симлинк `usr/bin/i2pchat`, `.desktop` и иконка.

Требования: `bash`, `curl`, `unzip`, **`dpkg-deb`** (пакет `dpkg` в Debian/Ubuntu; на macOS `.deb` не соберётся без Linux-окружения). Запуск из корня репозитория I2PChat:

```bash
# версия должна совпадать с опубликованным тегом vX.Y.Z и именем zip на GitHub
./packaging/debian/build-deb-from-appimage.sh 1.2.2

# или взять версию из первой строки файла VERSION в корне репо:
./packaging/debian/build-deb-from-appimage.sh
```

Готовый файл: `dist/i2pchat_<version>_amd64.deb`.

**Зависимости в рантайме:** AppImage может требовать FUSE или встроенный runtime в зависимости от типа образа и версии ОС; при проблемах запуска см. [документацию AppImage](https://docs.appimage.org/).

## Автоматическая сборка в CI

При **публикации** GitHub Release (событие `published`) workflow [`.github/workflows/release-deb.yml`](../../.github/workflows/release-deb.yml) скачивает `I2PChat-linux-x86_64-v<версия>.zip` с того же релиза, собирает `.deb` и загружает его обратно как ассет (с `--clobber`, если файл уже есть).

Условие: в момент срабатывания workflow **linux zip уже должен быть на релизе** (опубликуйте релиз после загрузки артефактов или перезапустите workflow вручную).

## Публикация apt-репозитория (кратко)

1. Соберите `.deb` для каждого релиза.
2. Создайте структуру `dists/stable/main/binary-amd64/` и индексы (`apt-ftparchive` или `reprepro`).
3. Подпишите `Release` (GPG) и выложите на HTTPS.
4. Пользователи добавляют `deb [signed-by=…] https://… stable main` и ключ.

Подробности выходят за рамки этого репозитория; ориентиры: [Debian Repository Format](https://wiki.debian.org/DebianRepository/Format), `reprepro`.
