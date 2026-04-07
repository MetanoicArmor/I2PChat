# Debian / Ubuntu (.deb)

Кратко: **I2PChat** — оконный клиент (PyQt) и отдельно **тот же чат в терминале** (часто подписан как **TUI**, *terminal user interface* — без окон и без PyQt6).

Попасть в официальные архивы Debian/Ubuntu без мейнтейнера в дистрибутиве нельзя. Варианты:

| Подход | Плюсы | Минусы |
|--------|--------|--------|
| **`.deb` с GitHub Release** | `sudo apt install ./пакет.deb` | Обновлять вручную с каждым релизом |
| **Свой apt (GitHub Pages и т.д.)** | Дальше — обычный `apt install` | GPG и инфраструктура; здесь — [**`packaging/apt/`**](../apt/README.md) |
| **PPA** | Привычно для Ubuntu | Рецепты, очередь сборки |
| **Flatpak** | Один формат на много дистрибутивов | Не `apt`; отдельный манифест |

---

## Установка через apt (подписанное зеркало)

В зеркале сейчас **amd64**. Для **arm64** скачайте `*_arm64.deb` с [релизов](https://github.com/MetanoicArmor/I2PChat/releases) и установите `sudo apt install ./файл.deb`.

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL "https://metanoicarmor.github.io/I2PChat/KEY.gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/i2pchat.gpg
echo "deb [signed-by=/etc/apt/keyrings/i2pchat.gpg] https://metanoicarmor.github.io/I2PChat stable main" | sudo tee /etc/apt/sources.list.d/i2pchat.list
sudo apt update
sudo apt install i2pchat        # GUI
sudo apt install i2pchat-tui    # терминал (TUI)
```

Настройка Pages, секреты CI: [**`packaging/apt/README.md`**](../apt/README.md).

---

## Скачать `.deb` с релиза

В **Assets** на [релизах](https://github.com/MetanoicArmor/I2PChat/releases): **`i2pchat_<версия>_{amd64|arm64}.deb`** (GUI), **`i2pchat-tui_<версия>_{amd64|arm64}.deb`** (терминал). Установка: `sudo apt install ./имя.deb`.

---

## Сборка `.deb` локально (из официальных zip)

Нужны `bash`, `curl`, `unzip`, **`dpkg-deb`**. Запуск из **корня** клона I2PChat (на macOS без Linux — `.deb` не собрать).

| Пакет | Скрипт | Заметки |
|--------|--------|---------|
| GUI | [`build-deb-from-appimage.sh`](build-deb-from-appimage.sh) | По умолчанию amd64 (`I2PChat-linux-x86_64-v*.zip`); arm64: `I2PCHAT_DEB_ARCH=arm64 ./packaging/debian/build-deb-from-appimage.sh [версия]` |
| Терминал (TUI) | [`build-tui-deb-from-release-zip.sh`](build-tui-deb-from-release-zip.sh) | Те же архитектуры |

Версия — аргумент или первая строка **`VERSION`** в корне репо.

**Рантайм:** AppImage может требовать FUSE и т.п. ([документация AppImage](https://docs.appimage.org/)). **glibc** у бинарников — как у хоста сборки zip; релизные zip для Linux собирают на Ubuntu 22.04 в CI ([`build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml)).

---

## Source package в корне `debian/` (не путать с `.deb` из zip выше)

Это отдельная линия: **официальный стиль Debian** (`dpkg-buildpackage`), пакеты **`python3-i2pchat`** + мета **`i2pchat`** / **`i2pchat-tui`**, зависимость от **системного `i2pd`**, без бинарника встроенного роутера в `.deb`.

**Статус:** приближение к **RFS**: native **`debian/changelog`** с версией **`1.2.4`** (без `-1`), **`debian/source/options`** отсекает локальный мусор из tarball, **`debian/copyright`** покрывает emoji/icons. Дальше — **sbuild** у спонсора и **ITP**.

- **CI:** [`.github/workflows/debian-dpkg-buildpackage.yml`](../../.github/workflows/debian-dpkg-buildpackage.yml) — контейнер **`debian:sid`**, чтобы из архива ставился **`i2pd` ≥ 2.59** (как в `Depends` у `python3-i2pchat`). В **Ubuntu 24.04** в main сейчас только i2pd **2.49**, поэтому noble без PPA/backports не подходит под текущие зависимости. Python **≥ 3.12** — как в [`pyproject.toml`](../../pyproject.toml).
- **Локально (Docker):** [`docker-dpkg-buildpackage.sh`](docker-dpkg-buildpackage.sh) — по умолчанию **`debian:sid`**. Скрипт выполняет **`dpkg-buildpackage -us -uc`** (полный native upload), копирует в **`debian-ci-out/`** артефакты `*.deb` / `*.changes` / `*.buildinfo` / `*.dsc` / `*.tar.xz`, затем **`lintian -E`**, **`autopkgtest`**, переустановку `.deb` и проверку маркера **`/usr/share/i2pchat/system-router-only`**.
  - **Перед RFS / для спонсора (amd64):** на **macOS ARM** или другом не-amd64 хосте задайте **`DEBIAN_DOCKER_PLATFORM=linux/amd64`**, чтобы сборка в Docker совпадала с типичным **amd64** chroot/sbuild у ревьюера. На **x86_64 Linux** платформу можно не задавать (уже amd64).
- **Чистый source tree:** в git должны оставаться только «ручные» файлы под `debian/` (`rules`, `control`, `copyright`, `tests/*`, …). Всё, что создаёт `dh`/`dpkg-buildpackage` (`debian/files`, `*.substvars`, `debhelper-build-stamp`, `debian/python3-i2pchat/` и т.д.), перечислено в **`.gitignore`** — после локальной сборки можно смело удалять эти пути.
- **sbuild (следующий рубеж, вручную):** после `dpkg-buildpackage -S` — например `sbuild -d unstable ../i2pchat_*.dsc` в настроенном chroot; в CI пока не гоняется.

Сборка **намеренно пропускает `dh_auto_test`**: pybuild по умолчанию вызывает `unittest discover`, что не совпадает с pytest-сьютом в репозитории; тесты — в [**`test-gate.yml`**](../../.github/workflows/test-gate.yml).

**Проверка раскладки пакетов после сборки** (из родительского каталога клона, где лежат `*.deb`):

```bash
# Маркер «только системный i2pd» ставится в usr/share/i2pchat/system-router-only (не под usr/share/doc —
# иначе dpkg с path-exclude для doc срежет файл при установке).
dpkg-deb -c ../python3-i2pchat_*_all.deb | grep -E 'usr/share/(i2pchat|doc/python3-i2pchat)/'
dpkg-deb -c ../i2pchat_*_all.deb | head -20          # .desktop, pixmaps
```

### Перед отправкой спонсору (checklist)

**Дерево**

- `git status` чистый (кроме осознанных untracked: не коммитить `I2PChat.AppImage`, локальные отчёты).
- В source tree нет случайных build artifacts; в **`debian/`** не должно быть generated-файлов (`files`, `*.substvars`, `*.debhelper.log`, `debhelper-build-stamp`, каталоги `python3-i2pchat/` и т.д.) — они в **`.gitignore`**, после сборки удаляйте вручную или не добавляйте в коммит.

**Версия**

- **`debian/source/format`:** `3.0 (native)`.
- **`debian/changelog`:** версия вида **`1.2.4`**, без `-1`; совпадает с намерением релиза (сверить с **`VERSION`** в корне).

**Сборка**

- **`dpkg-buildpackage -us -uc`** проходит на **sid** — локально: [`docker-dpkg-buildpackage.sh`](docker-dpkg-buildpackage.sh) (при необходимости **`DEBIAN_DOCKER_PLATFORM=linux/amd64`**); в CI: workflow **Debian dpkg-buildpackage** (раннер amd64).
- **`sbuild`** на чистом chroot — у спонсора или у себя вручную (`sbuild -d unstable ../i2pchat_*.dsc`); в репозитории не автоматизировано.

**Качество**

- **`lintian`** на **`*.changes`** (в скрипте — `lintian -E`); цель — без ошибок, предупреждения только осознанные; overrides в **`debian/*lintian-overrides`** и **`debian/source/lintian-overrides`** — минимальные и с комментариями.

**Тесты**

- **`autopkgtest`** (`virt null`): минимум **`import-runtime`**; в docker-скрипте дополнительно переустановка `.deb` и проверка **`/usr/share/i2pchat/system-router-only`** и отсутствия бинарника **`i2pd`** в `python3-i2pchat`.

**Зависимости**

- **`Depends` / `Build-Depends`** соответствуют реальности; **`i2pd (>= 2.59.0~)`** зафиксирован в **`debian/control`**; в Debian-пакет **bundled router не входит** (отдельно — portable/AppImage и **`vendor/`**, в tarball режется **`debian/source/options`**).

**Policy / packaging**

- Split: **`python3-i2pchat`** (Python + entry points), **`i2pchat`** / **`i2pchat-tui`** (desktop); **`.desktop`** и **`.install`** согласованы; override **`desktop-command-not-in-package`** у метапакетов обоснован.
- **`debian/source/options`** отсекает **`.git`**, venv, AppImage, **`vendor/`** и пр. из source tarball.

**Licensing**

- **`debian/copyright`:** исходники (AGPL-3+), **Fluent emoji** (Expat), **GUI icons**, **`icon.png`**, packaging.

**Процесс**

- **ITP / WNPP** — оформить до/вместе с RFS при необходимости.
- Спонсору удобно сразу: ссылка на **репозиторий и commit**; готовые **`.dsc`**, **`.changes`**, **`.buildinfo`**, **source tarball** (после сборки в `..` или **`debian-ci-out/`**); короткий текст **RFS** с перечислением: **native**, **sid**, **lintian**, **autopkgtest**, **system-router-only**, **отделение portable от Debian source**.

**Что подчеркнуть спонсору одной фразой**

- Пакет **native**, собирается на **sid**, прогнаны **lintian** и **autopkgtest**, в **.deb** только **system i2pd** и маркер **`system-router-only`**, portable/bundled сборки **не смешиваются** с Debian source package.

---

## CI

После публикации релиза [**`release-linux-pkgs.yml`**](../../.github/workflows/release-linux-pkgs.yml) собирает `.deb` из zip на релизе (amd64; arm64 — если есть aarch64 zip). Повтор: **Actions → Release Linux packages → Run workflow** с тегом `vX.Y.Z`. Обновление apt-зеркала на Pages — при секрете `APT_REPO_GPG_PRIVATE_KEY`, см. **`packaging/apt/`**.

Отдельно: workflow **Debian dpkg-buildpackage** (см. выше) проверяет **корневой `debian/`**, а не zip-репакеты.

---

## См. также

- [**`packaging/README.md`**](../README.md) — все каналы распространения  
- [**`docs/INSTALL.md`**](../../docs/INSTALL.md) — установка по ОС
