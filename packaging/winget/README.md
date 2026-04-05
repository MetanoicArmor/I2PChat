# winget

Готовые манифесты для [Windows Package Manager](https://learn.microsoft.com/windows/package-manager/) лежат в каталоге с версией, например [`1.2.3/`](1.2.3/) (**GUI**). Отдельный пакет **TUI**: [`../winget-tui/`](../winget-tui/) (`MetanoicArmor.I2PChat.TUI`) — в репозитории winget-pkgs путь **`manifests/m/MetanoicArmor/I2PChat/TUI/<version>/`** (каждый сегмент идентификатора после издателя = вложенная папка). Старые версии ищите в истории git.

## Публикация в community-репозитории

1. Форкните [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. Скопируйте три YAML-файла в ветку:

   `manifests/m/MetanoicArmor/I2PChat/1.2.3/`

3. Откройте pull request по [инструкции winget-pkgs](https://github.com/microsoft/winget-pkgs/blob/master/README.md).

Проверка локально (при установленном [wingetcreate](https://github.com/microsoft/winget-create) или клиенте winget):

```powershell
winget validate --manifest .\packaging\winget\1.2.3
```

## Обновление на новый релиз

Скопируйте каталог под новую версию, обновите `PackageVersion` во всех трёх файлах, `InstallerUrl` / `InstallerSha256` и при необходимости `ReleaseDate`. Либо используйте [`../refresh-checksums.sh`](../refresh-checksums.sh) и вручную подставьте значения в YAML.

## ESRP / wingetbot: Riskware.I2PD.A (ESET)

В zip лежит **вендорный [`i2pd`](https://github.com/PurpleI2P/i2pd)** (маршрутизатор I2P для встроенного режима). Сканеры вроде ESET помечают такие exe как **Win64/Riskware.I2PD.A** — это **ожидаемый** класс детекций для I2P/Tor-подобного ПО, а не признак вредоноса.

**Что сделать в PR в winget-pkgs:** оставьте комментарий для модераторов (на английском), например:

> The ESRP-flagged file is the **bundled upstream i2pd** router (`vendor/.../i2pd.exe`) shipped for the optional embedded I2P router / SAM connectivity. It matches our published GitHub Release; same category of binary as official [PurpleI2P/i2pd](https://github.com/PurpleI2P/i2pd) releases. We request manual review — this is a known AV “riskware” label for legitimate I2P infrastructure, not malware.

При необходимости обновите манифест из этого репозитория (поля `Description` / `ReleaseNotes` в `*.locale.en-US.yaml` уже поясняют наличие i2pd) и запушьте коммит в ветку PR.
