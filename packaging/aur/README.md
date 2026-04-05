# AUR: i2pchat-bin / i2pchat-tui-bin

- [`i2pchat-bin/`](i2pchat-bin/) — GUI: официальный **AppImage** из `I2PChat-linux-x86_64-v*.zip`.
- [`i2pchat-tui-bin/`](i2pchat-tui-bin/) — только TUI: zip **`I2PChat-linux-x86_64-tui-v*.zip`** → `/opt/i2pchat-tui`, команда **`i2pchat-tui`** (без конфликта с `i2pchat` из GUI-пакета).

## Публикация на AUR

1. Зарегистрируйтесь на [aur.archlinux.org](https://aur.archlinux.org/).
2. Создайте репозиторий `i2pchat-bin` через веб-интерфейс (Submit).
3. Загрузите содержимое каталога (`PKGBUILD`, `.SRCINFO`) — например `git clone ssh://aur@aur.archlinux.org/i2pchat-bin.git` или отдельный репозиторий **`i2pchat-tui-bin`** для TUI.

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
