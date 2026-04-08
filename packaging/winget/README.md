# winget

Готовые манифесты для [Windows Package Manager](https://learn.microsoft.com/windows/package-manager/) лежат в каталоге с версией, например [`1.2.5/`](1.2.5/) (**GUI**). Отдельный пакет **TUI**: [`../winget-tui/`](../winget-tui/) (`MetanoicArmor.I2PChat.TUI`) — в репозитории winget-pkgs путь **`manifests/m/MetanoicArmor/I2PChat/TUI/<version>/`** (каждый сегмент идентификатора после издателя = вложенная папка). Старые версии ищите в истории git.

## Публикация в community-репозитории

1. Форкните [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. Скопируйте три YAML-файла в ветку:

   `manifests/m/MetanoicArmor/I2PChat/1.2.5/`

3. Откройте pull request по [инструкции winget-pkgs](https://github.com/microsoft/winget-pkgs/blob/master/README.md).

Проверка локально (при установленном [wingetcreate](https://github.com/microsoft/winget-create) или клиенте winget):

```powershell
winget validate --manifest .\packaging\winget\1.2.5
```

## Обновление на новый релиз

Скопируйте каталог под новую версию, обновите `PackageVersion` во всех трёх файлах, `InstallerUrl` / `InstallerSha256` и при необходимости `ReleaseDate`. Либо используйте [`../refresh-checksums.sh`](../refresh-checksums.sh) и вручную подставьте значения в YAML.

## Microsoft: Installers Scan / `binaryValidation` / ESRP (i2pd)

Пайплайн **winget-pkgs** распаковывает zip и прогоняет бинарники; **встроенный i2pd** даёт детекции вроде **Win64/Riskware.I2PD.A** и падает **Installers Scan**.

**Решение в этом репозитории:** `build-windows.ps1` после обычных архивов делает **второй** проход PyInstaller с `I2PCHAT_OMIT_BUNDLED_I2PD=1` (см. `I2PChat.spec` / `I2PChat-tui.spec`) и упаковывает:

- `I2PChat-windows-x64-winget-v<версия>.zip`
- `I2PChat-windows-tui-x64-winget-v<версия>.zip`

Манифесты winget указывают на **эти** URL. В архивах **нет** вендорного i2pd — для работы нужен **системный** i2pd (SAM) либо полный zip с релиза GitHub.

**Перед merge в winget-pkgs:**

1. Залить оба `*-winget-*.zip` на **тот же** GitHub Release, что и обычные Windows zip.
2. Подставить SHA256: вывод в конце `build-windows.ps1` или `./packaging/refresh-checksums.sh vX.Y.Z` (секции *winget*).
3. В `MetanoicArmor.I2PChat*.installer.yaml` заменить placeholder `0000…0000` на реальные хеши и запушить в ветку PR.

### Блок для PR в winget-pkgs (смена URL на `*-winget-*` и Installers Scan)

Если в PR или в обсуждении спрашивают, **зачем** в манифесте URL на `I2PChat-*-winget-*.zip` вместо обычного zip — вставьте цитату ниже (англ.). Источник для ссылки: этот файл в апстриме I2PChat — [`packaging/winget/README.md`](README.md).

> We switched the manifest to the **`*-winget-*`** release assets built **without** the embedded i2pd binary so the installer passes Microsoft’s **Installers Scan** (`binaryValidation` / ESRP). The standard `I2PChat-windows-x64-v*.zip` (and the full TUI zip) on GitHub Releases still include the bundled i2pd router for users who want it. i2pd upstream: https://github.com/PurpleI2P/i2pd
