# AUR: i2pchat-bin / i2pchat-tui-bin

- [`i2pchat-bin/`](i2pchat-bin/) — GUI: официальный **AppImage** из `I2PChat-linux-x86_64-v*.zip`.
- [`i2pchat-tui-bin/`](i2pchat-tui-bin/) — только TUI: zip **`I2PChat-linux-x86_64-tui-v*.zip`** → `/opt/i2pchat-tui`, команда **`i2pchat-tui`** (без конфликта с `i2pchat` из GUI-пакета).

## Публикация на AUR

1. Зарегистрируйтесь на [aur.archlinux.org](https://aur.archlinux.org/).
2. Добавьте в профиль AUR **SSH public key** (Account → SSH Keys). Проверка: `ssh -T aur@aur.archlinux.org` → приветствие **Welcome to AUR, …**
3. **Первый раз — зарегистрировать имена пакетов на сайте.** Пока пакета с таким именем нет в AUR, `git clone ssh://aur@aur.archlinux.org/i2pchat-bin.git` **не сработает**. Откройте [Submit (добавить пакет)](https://aur.archlinux.org/submit/) и создайте **`i2pchat-bin`**, затем снова **`i2pchat-tui-bin`**, подставив `PKGBUILD` и `.SRCINFO` из каталогов [`i2pchat-bin/`](i2pchat-bin/) и [`i2pchat-tui-bin/`](i2pchat-tui-bin/) этого репозитория (или минимальный черновик — см. [AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines)). После этого появятся пустые Git-репозитории на сервере AUR.
4. **Один раз задайте автора git-коммитов** (в корне I2PChat или глобально), иначе `publish-to-aur.sh` остановится на `git commit`:
   ```bash
   git config user.email "you@example.com"
   git config user.name "Your Name"
   ```
   Либо только для скрипта: `export AUR_GIT_EMAIL=… AUR_GIT_NAME=…`.

5. **Дальнейшие обновления** — из корня клона I2PChat. Удобно поднять агент, чтобы не вводить passphrase дважды:
   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/aur_ed25519
   ./packaging/aur/publish-to-aur.sh
   ```
   Скрипт клонирует оба репозитория во временный каталог, копирует актуальные `PKGBUILD` и `.SRCINFO`, делает `commit` при изменениях и `git push` в ветку **`master`**.

Если по SSH с текущей машины зайти нельзя, используйте другой хост с настроенным ключом или только веб-интерфейс Submit, затем правки через `git` там, где `ssh aur@aur.archlinux.org` работает.

После изменения `PKGBUILD` пересоберите метаданные:

```bash
cd i2pchat-bin
makepkg --printsrcinfo > .SRCINFO
```

## Bump версии

0. Актуальные хеши с GitHub Releases: **`./packaging/refresh-checksums.sh vX.Y.Z`** — подставьте две строки для Linux zip в соответствующие `PKGBUILD` (и `pkgrel+1`, если `pkgver` не менялся, а zip перезалили).

1. Обновите `pkgver` (и при необходимости `pkgrel`) в `PKGBUILD`.
2. Обновите `sha256sums` для zip и `icon.png` (иконка должна существовать в теге `v$pkgver`).
3. Выполните `makepkg --printsrcinfo > .SRCINFO`, проверьте `makepkg -f` / `namcap`.

Скрипт [`../refresh-checksums.sh`](../refresh-checksums.sh) печатает контрольные суммы zip-файлов релиза.
