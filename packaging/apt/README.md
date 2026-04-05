# apt-зеркало в этом же репозитории (GitHub Pages)

Пользователи могут подключать **один** источник apt, который публикуется на **GitHub Pages** этого репозитория (ветка **`gh-pages`**), без отдельного репо.

- Берётся официальный **`i2pchat_<версия>_amd64.deb`** с [GitHub Releases](https://github.com/MetanoicArmor/I2PChat/releases).
- На ветку **`gh-pages` попадают только** `dists/`, подписи и **`KEY.gpg`** (и при необходимости заглушка). Сам **`.deb` в git не кладётся**: в `Packages` поле **`Filename`** указывает **прямую ссылку на ассет релиза** — иначе GitHub отклонит push (лимит **100 MB** на файл, пакет больше).
- URL сайта: **`https://<owner>.github.io/<repo>/`**  
  Для **`MetanoicArmor/I2PChat`**: `https://metanoicarmor.github.io/I2PChat/`

## Настройка один раз (владелец репозитория)

1. **Создать ветку `gh-pages`:** в Actions запустите workflow **[Init gh-pages branch](../../.github/workflows/init-gh-pages.yml)** (кнопка *Run workflow*) — появится ветка с `.nojekyll` и короткой `index.html`. Без этой ветки в **Settings → Pages** нельзя выбрать источник `gh-pages`.

2. **GitHub Pages:** **Settings → Pages → Deploy from a branch** → ветка **`gh-pages`**, папка **`/(root)`**.

   **Важно:** у репозитория только **один** источник Pages. Если раньше он указывал на **`main`**, после переключения на **`gh-pages`** по адресу `https://OWNER.github.io/REPO/` будет **только** apt-зеркало (`KEY.gpg`, `dists/`, `pool/`). Старый вариант сайта с README по этому URL показываться не будет.

3. **GPG-ключ только для apt** (не личный повседневный):
   ```bash
   gpg --full-generate-key
   gpg --armor --export-secret-keys KEY_ID > apt-signing-private.asc
   ```
4. В **Settings → Secrets and variables → Actions** добавьте:
   - **`APT_REPO_GPG_PRIVATE_KEY`** — содержимое `apt-signing-private.asc`
   - **`APT_REPO_GPG_PASSPHRASE`** — по необходимости (если ключа без пароля — секрет можно не создавать)

## Когда обновляется зеркало

- **Автоматически** (если задан `APT_REPO_GPG_PRIVATE_KEY`): job **deb** в [`.github/workflows/release-linux-pkgs.yml`](../../.github/workflows/release-linux-pkgs.yml) после сборки и загрузки `.deb` на релиз собирает `packaging/apt/site`, подписывает и пушит в **`gh-pages`**.
- **Вручную**: workflow [**Publish apt mirror (GitHub Pages)**](../../.github/workflows/apt-github-pages.yml) — укажите версию `x.y.z`, на релизе должен уже лежать `i2pchat_x.y.z_amd64.deb`.

После шагов выше запустите **Publish apt mirror** с нужной версией или дождитесь релиза с job **deb** в **Release Linux packages**.

Если секрет **`APT_REPO_GPG_PRIVATE_KEY` не задан**, шаг публикации apt **пропускается**, релизный `.deb` по-прежнему собирается и загружается.

## Установка у пользователя (пример)

Подставьте `OWNER` и `REPO` (для апстрима: `MetanoicArmor` и `I2PChat`):

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://OWNER.github.io/REPO/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://OWNER.github.io/REPO/ stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
sudo apt update
sudo apt install i2pchat
```

Suite: **`stable`**, компонент: **`main`**, архитектура: **`amd64`**.

## Локальная проверка (Linux)

Из корня репозитория I2PChat.

**Как в CI** (без копии `.deb` в `site/`, только метаданные + URL на Releases):

```bash
export VERSION=1.2.3
export DEB_PATH="$PWD/dist/i2pchat_${VERSION}_amd64.deb"
export APT_DEB_FILENAME_URL="https://github.com/MetanoicArmor/I2PChat/releases/download/v${VERSION}/i2pchat_${VERSION}_amd64.deb"
./packaging/apt/scripts/build-apt-site.sh
```

**Полное зеркало с `pool/`** (для теста на локальном веб-сервере) — не задавайте `APT_DEB_FILENAME_URL`:

```bash
export VERSION=1.2.3
export DEB_PATH="$PWD/dist/i2pchat_${VERSION}_amd64.deb"
./packaging/apt/scripts/build-apt-site.sh
gpg --import apt-signing-private.asc
export APT_REPO_GPG_PASSPHRASE='...'
./packaging/apt/scripts/sign-release.sh
# дерево: packaging/apt/site/
```

## Файлы

| Путь | Назначение |
|------|------------|
| `scripts/build-apt-site.sh` | `site/pool` + `site/dists` + неподписанный `Release` |
| `scripts/sign-release.sh` | `InRelease`, `Release.gpg`, `KEY.gpg`, `.nojekyll` |
| `config/apt-ftparchive-release.conf` | поля для `apt-ftparchive release` |

Формат репозитория: [DebianRepository/Format](https://wiki.debian.org/DebianRepository/Format).
