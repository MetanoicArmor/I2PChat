# apt-зеркало в этом же репозитории (GitHub Pages)

Пользователи подключают apt к **`https://<owner>.github.io/<repo>/`** (для **`MetanoicArmor/I2PChat`**: `https://metanoicarmor.github.io/I2PChat/`).

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

5. Запустите **[Publish apt mirror (GitHub Pages)](../../.github/workflows/apt-github-pages.yml)** с версией `x.y.z` или дождитесь релиза с job **deb** в **[Release Linux packages](../../.github/workflows/release-linux-pkgs.yml)** (если секрет задан).

### Устаревшая схема «только ветка gh-pages»

Workflow **[Init gh-pages branch](../../.github/workflows/init-gh-pages.yml)** нужен только если вы **намеренно** публикуете Pages **с ветки**; для apt с большим `.deb` это не подходит. Текущая схема — **Actions**.

## Когда обновляется зеркало

- **Автоматически:** в **Release Linux packages** после загрузки `.deb` на релиз (если задан **`APT_REPO_GPG_PRIVATE_KEY`**) — job **deb** собирает сайт, затем job **deploy-apt-site** выкладывает его в Pages.
- **Вручную:** **Publish apt mirror** и версия `x.y.z` (на релизе уже должен быть `i2pchat_x.y.z_amd64.deb`).

Без **`APT_REPO_GPG_PRIVATE_KEY`** публикация apt пропускается, `.deb` на релиз по-прежнему попадает.

## Установка у пользователя

Рекомендуется **`signed-by`**, а не глобальный `trusted.gpg.d`:

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://OWNER.github.io/REPO/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://OWNER.github.io/REPO/ stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
sudo apt update
sudo apt install i2pchat        # GUI (AppImage)
sudo apt install i2pchat-tui    # только TUI (тот же источник apt)
```

Suite: **`stable`**, компонент: **`main`**, архитектура: **`amd64`**.

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
| `scripts/build-apt-site.sh` | `site/pool` + `site/dists` + неподписанный `Release` |
| `scripts/sign-release.sh` | `InRelease`, `Release.gpg`, `KEY.gpg`, `.nojekyll` |
| `config/apt-ftparchive-release.conf` | поля для `apt-ftparchive release` |

Формат репозитория: [DebianRepository/Format](https://wiki.debian.org/DebianRepository/Format).
