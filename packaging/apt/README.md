# apt-зеркало в этом же репозитории (GitHub Pages)

**Пока никто не настроил секреты и не выкатил сайт, зеркала нет:** пользователям нужны **`.deb` с GitHub Releases** (`sudo apt install ./…`). Ниже — инструкция **для будущего мейнтейнера** и готовые команды на тот случай, когда **`KEY.gpg`** уже отдаётся по URL.

## Что нужно для этого apt-зеркала (кроме мейнтейнера)

Имеется в виду **собственное зеркало на GitHub Pages**, а не включение пакета в **официальный архив Debian** (для Debian нужны отдельно: ITP/RFS, сопровождение в `sid`, загрузчик и т.д. — это другой процесс).

Для **Pages + apt** исходный **tarball не нужен**: репозиторий собирается из готовых **`.deb`**.

| Требование | Зачем |
|------------|--------|
| **GitHub Pages → источник «GitHub Actions»** | Деплой сайта с `dists/` и `pool/` (не ветка `gh-pages` с большими бинарниками в git). |
| **Окружение `github-pages`** в Actions | Первый деплой может запросить подтверждение. |
| **Секреты `APT_REPO_GPG_PRIVATE_KEY`** и при необходимости **`APT_REPO_GPG_PASSPHRASE`** | Подпись `InRelease` / `Release.gpg` и публикация `KEY.gpg`. |
| На **релизе** `vX.Y.Z` файлы **`i2pchat_X.Y.Z_amd64.deb`** и **`i2pchat-tui_X.Y.Z_amd64.deb`** | Их подтягивает workflow **Publish apt mirror** или шаг apt в **Release Linux packages**. Обычно они появляются после job **deb-amd64** (сборка из Linux zip на релизе). |
| Linux **x86_64** GUI + TUI **zip** на том же релизе | Нужны **до** сборки `.deb` в CI (`I2PChat-linux-x86_64-v*.zip`, `…-tui-…`). GUI zip — с **AppImage** внутри (режим по умолчанию `build-linux.sh`) или **portable** onedir — скрипт **`packaging/debian/build-deb-from-appimage.sh`** поддерживает оба варианта. |

Зеркало в **`dists/stable/main/binary-amd64`** сейчас рассчитано на **amd64** только для apt-индекса; **arm64** `.deb` можно класть на Releases отдельно (`deb-arm64` в **Release Linux packages**), без добавления в то же дерево Pages (для multi-arch пришлось бы расширять `build-apt-site.sh`).

После деплоя пользователи подключают apt к **`https://<owner>.github.io/<repo>/`** (для **`MetanoicArmor/I2PChat`**: `https://metanoicarmor.github.io/I2PChat/`). В корне зеркала лежит **`index.html`** — витрина с командами установки и ссылками на `KEY.gpg` / индексы.

Сайт публикуется через **GitHub Actions → Pages** (артефакт): в выдачу попадает полное дерево **`dists/`**, **`pool/main/*.deb`**, **`KEY.gpg`**, подписи. Так обходятся два ограничения:

- **Git** не хранит `.deb` > **100 MB** (push в ветку был бы отклонён).
- Поле **`Filename: https://github.com/...`** в `Packages` **ломает** обычный **apt**: он склеивает базовый URL репозитория с `Filename`, получается неверный адрес. Поэтому в зеркале используется относительный **`pool/main/...`** и реальный файл на Pages.

## Настройка один раз (владелец репозитория)

1. **GitHub Pages → источник «GitHub Actions»**  
   **Settings → Pages → Build and deployment → Source:** выберите **GitHub Actions** (не ветку `main` / `gh-pages`). Иначе workflow деплоя не сможет обновить сайт.

2. **Окружение `github-pages`**  
   При первом деплое GitHub может запросить одобрение environment **github-pages** для workflow — подтвердите в интерфейсе.

3. **GPG-ключ только для apt** (не личный повседневный):
   ```bash
   gpg --full-generate-key
   gpg --armor --export-secret-keys KEY_ID > apt-signing-private.asc
   ```

4. **Settings → Secrets and variables → Actions:**
   - **`APT_REPO_GPG_PRIVATE_KEY`** — содержимое `apt-signing-private.asc`
   - **`APT_REPO_GPG_PASSPHRASE`** — по необходимости

5. Запустите **[Publish apt mirror (GitHub Pages)](../../.github/workflows/apt-github-pages.yml)** с версией `x.y.z` или дождитесь релиза с job **`deb-amd64`** в **[Release Linux packages](../../.github/workflows/release-linux-pkgs.yml)** (если секрет задан; зеркало только amd64).

### Устаревшая схема «только ветка gh-pages»

Workflow **[Init gh-pages branch](../../.github/workflows/init-gh-pages.yml)** нужен только если вы **намеренно** публикуете Pages **с ветки**; для apt с большим `.deb` это не подходит. Текущая схема — **Actions**.

## Когда обновляется зеркало

- **Автоматически:** в **Release Linux packages** после загрузки `.deb` на релиз (если задан **`APT_REPO_GPG_PRIVATE_KEY`**) — job **deb** собирает сайт, затем job **deploy-apt-site** выкладывает его в Pages.
- **Вручную:** **Publish apt mirror** и версия `x.y.z` (на релизе уже должен быть `i2pchat_x.y.z_amd64.deb`).

Без **`APT_REPO_GPG_PRIVATE_KEY`** публикация apt пропускается, `.deb` на релиз по-прежнему попадает.

## Установка у пользователя

Рекомендуется **`signed-by`** и формат **deb822** (файл **`*.sources`**) — так делает современный **apt** на Debian 12+ и актуальных Ubuntu:

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://OWNER.github.io/REPO/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
sudo tee /etc/apt/sources.list.d/i2pchat.sources >/dev/null <<'EOF'
Types: deb
URIs: https://OWNER.github.io/REPO
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/i2pchat.gpg
Architectures: amd64
EOF
sudo apt update
sudo apt install i2pchat        # GUI (AppImage)
sudo apt install i2pchat-tui    # только TUI (тот же источник apt)
```

Для **MetanoicArmor/I2PChat** подставьте `https://metanoicarmor.github.io/I2PChat` в поле **`URIs`** (без завершающего `/`).

Устаревший однострочный **`sources.list`** (эквивалент):

```bash
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://OWNER.github.io/REPO/ stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
```

Suite: **`stable`**, компонент: **`main`**, на зеркале пакеты только **`amd64`** (`Architectures: amd64` в deb822 снижает шум на других архитектурах).

## Локальная проверка (Linux)

Полное зеркало с `pool/` (как на Pages):

```bash
export VERSION=1.2.3
export DEB_PATH="$PWD/dist/i2pchat_${VERSION}_amd64.deb"
export DEB_PATH_2="$PWD/dist/i2pchat-tui_${VERSION}_amd64.deb"
./packaging/apt/scripts/build-apt-site.sh
gpg --import apt-signing-private.asc
export APT_REPO_GPG_PASSPHRASE='...'   # если ключ с паролем
./packaging/apt/scripts/sign-release.sh
# дерево: packaging/apt/site/
```

Опция **`APT_DEB_FILENAME_URL`** в скрипте оставлена только для экспериментов; с обычным **apt** на репозитории с базой `https://...github.io/.../` она **не работает** (см. комментарий в `build-apt-site.sh`).

## Файлы

| Путь | Назначение |
|------|------------|
| [`i2pchat.sources.example`](i2pchat.sources.example) | образец **deb822** для официального зеркала (копия в `/etc/apt/sources.list.d/`) |
| `scripts/build-apt-site.sh` | `site/pool` + `site/dists` + неподписанный `Release` |
| `scripts/sign-release.sh` | `InRelease`, `Release.gpg`, `KEY.gpg`, `.nojekyll`, копия **`index.html`** в корень `site/` |
| `index.html` | Главная GitHub Pages: витрина apt, ссылки на индексы; **без внешних шрифтов**; кнопка «Copy» подставляет URL текущего хоста |
| `config/apt-ftparchive-release.conf` | поля для `apt-ftparchive release` |

Формат репозитория: [DebianRepository/Format](https://wiki.debian.org/DebianRepository/Format).
