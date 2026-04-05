# AUR: i2pchat-bin / i2pchat-tui-bin

- [`i2pchat-bin/`](i2pchat-bin/) — GUI: официальный **AppImage** из `I2PChat-linux-x86_64-v*.zip`.
- [`i2pchat-tui-bin/`](i2pchat-tui-bin/) — только TUI: zip **`I2PChat-linux-x86_64-tui-v*.zip`** → `/opt/i2pchat-tui`, команда **`i2pchat-tui`** (без конфликта с `i2pchat` из GUI-пакета).

## Публикация на AUR

1. Зарегистрируйтесь на [aur.archlinux.org](https://aur.archlinux.org/).
2. Добавьте в профиль AUR **SSH public key** (Account → SSH Keys). Без ключа `git push` на `ssh://aur@aur.archlinux.org/...` завершится ошибкой **`Permission denied (publickey)`**.
3. Создайте пакет `i2pchat-bin` через веб-интерфейс (Submit), либо клонируйте пустой репозиторий: `git clone ssh://aur@aur.archlinux.org/i2pchat-bin.git` (отдельно **`i2pchat-tui-bin`** для TUI).
4. Загрузите содержимое каталога из этого репозитория (`PKGBUILD`, `.SRCINFO`) — `git add`, `commit`, `push` на AUR.

Если по SSH с текущей машины зайти нельзя, используйте другой хост с настроенным ключом или веб-форму AUR для первичной отправки, затем правки через `git` с машины, где SSH к AUR работает.

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
