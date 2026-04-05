# winget

Готовые манифесты для [Windows Package Manager](https://learn.microsoft.com/windows/package-manager/) лежат в каталоге с версией, например [`1.2.2/`](1.2.2/).

## Публикация в community-репозитории

1. Форкните [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. Скопируйте три YAML-файла в ветку:

   `manifests/m/MetanoicArmor/I2PChat/1.2.2/`

3. Откройте pull request по [инструкции winget-pkgs](https://github.com/microsoft/winget-pkgs/blob/master/README.md).

Проверка локально (при установленном [wingetcreate](https://github.com/microsoft/winget-create) или клиенте winget):

```powershell
winget validate --manifest .\packaging\winget\1.2.2
```

## Обновление на новый релиз

Скопируйте каталог под новую версию, обновите `PackageVersion` во всех трёх файлах, `InstallerUrl` / `InstallerSha256` и при необходимости `ReleaseDate`. Либо используйте [`../refresh-checksums.sh`](../refresh-checksums.sh) и вручную подставьте значения в YAML.
