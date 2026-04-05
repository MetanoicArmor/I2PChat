# AUR: i2pchat-bin

Каталог [`i2pchat-bin/`](i2pchat-bin/) содержит `PKGBUILD` и `.SRCINFO` для пакета, который ставит официальный AppImage из релиза GitHub.

## Публикация на AUR

1. Зарегистрируйтесь на [aur.archlinux.org](https://aur.archlinux.org/).
2. Создайте репозиторий `i2pchat-bin` через веб-интерфейс (Submit).
3. Загрузите содержимое каталога `i2pchat-bin` (`PKGBUILD`, `.SRCINFO`) — например через `git clone ssh://aur@aur.archlinux.org/i2pchat-bin.git`.

После изменения `PKGBUILD` пересоберите метаданные:

```bash
cd i2pchat-bin
makepkg --printsrcinfo > .SRCINFO
```

## Bump версии

1. Обновите `pkgver` (и при необходимости `pkgrel`) в `PKGBUILD`.
2. Обновите `sha256sums` для zip и `icon.png` (иконка должна существовать в теге `v$pkgver`).
3. Выполните `makepkg --printsrcinfo > .SRCINFO`, проверьте `makepkg -f` / `namcap`.

Скрипт [`../refresh-checksums.sh`](../refresh-checksums.sh) печатает контрольные суммы zip-файлов релиза.
