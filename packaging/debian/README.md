# Debian / Ubuntu (.deb)

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя; реалистичные варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **Свой .deb + репозиторий** (GitHub Pages, Packagecloud, свой apt repo) | Полный контроль, `apt install` после добавления источника | Нужно подписывать репозиторий и обновлять пакеты |
| **PPA (Launchpad)** | Знакомый путь для Ubuntu | Рецепты сборки, очередь сборки |
| **Flatpak / Flathub** | Один формат для многих дистрибутивов | Не `apt`; отдельный манифест и ревью Flathub |

## Сборка локального .deb из официального AppImage

Скрипт [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) собирает минимальный пакет `i2pchat` (amd64): AppImage в `/opt/i2pchat/`, симлинк `usr/bin/i2pchat`, `.desktop` и иконка.

Требования: `bash`, `curl`, `unzip`, **`dpkg-deb`** (пакет `dpkg` в Debian/Ubuntu; на macOS скрипт не соберёт `.deb` без Debian-окружения). Запуск из корня репозитория I2PChat:

```bash
./packaging/debian/build-deb-from-appimage.sh 1.2.1
```

Версия должна совпадать с опубликованным тегом `vX.Y.Z` на GitHub. Готовый файл: `dist/i2pchat_<version>_amd64.deb`.

**Зависимости в рантайме:** AppImage может требовать FUSE или встроенный runtime в зависимости от типа образа и версии ОС; при проблемах запуска см. [документацию AppImage](https://docs.appimage.org/).

## Публикация apt-репозитория (кратко)

1. Соберите `.deb` для каждого релиза.
2. Создайте структуру `dists/stable/main/binary-amd64/` и индексы (`apt-ftparchive` или `reprepro`).
3. Подпишите `Release` (GPG) и выложите на HTTPS.
4. Пользователи добавляют `deb [signed-by=…] https://… stable main` и ключ.

Подробности выходят за рамки этого репозитория; ориентиры: [Debian Repository Format](https://wiki.debian.org/DebianRepository/Format), `reprepro`.
