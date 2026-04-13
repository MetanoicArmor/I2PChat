# winget — TUI-only package (`MetanoicArmor.I2PChat.TUI`)

**winget** uses **`I2PChat-windows-tui-x64-winget-vVERSION.zip`** (no embedded i2pd — см. [`../winget/README.md`](../winget/README.md)). Полный zip с роутером: **`I2PChat-windows-tui-x64-vVERSION.zip`**. Отдельный `PackageIdentifier` от GUI — в [winget-pkgs](https://github.com/microsoft/winget-pkgs) это **второе приложение**, поэтому PR на TUI открывается **отдельно** от PR на [`MetanoicArmor.I2PChat`](../winget/); один PR с GUI+TUI бот отклонит.

1. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. После публикации **`I2PChat-windows-tui-x64-winget-vX.Y.Z.zip`** (и остальных артефактов) на GitHub Release, из корня репозитория:

   ```bash
   ./packaging/refresh-checksums.sh vX.Y.Z
   ```

   Copy the printed **`InstallerSha256`** for the TUI zip into `MetanoicArmor.I2PChat.TUI.installer.yaml` (under the versioned folder below).
3. Open a PR to `winget-pkgs` following [their README](https://github.com/microsoft/winget-pkgs/blob/master/README.md).

Local validation (Windows, with winget):

```powershell
winget validate --manifest .\packaging\winget-tui\manifests\m\MetanoicArmor\I2PChat\TUI\1.3.1
```

**Folder layout in [winget-pkgs](https://github.com/microsoft/winget-pkgs)** must mirror each `PackageIdentifier` segment after the publisher: for `MetanoicArmor.I2PChat.TUI` use `manifests/m/MetanoicArmor/I2PChat/TUI/<version>/` (not `I2PChat.TUI`). Canonical copies live under [`manifests/m/MetanoicArmor/I2PChat/TUI/`](manifests/m/MetanoicArmor/I2PChat/TUI/) in this tree.

Update **PackageVersion** and duplicate that path for new releases (mirror [`packaging/winget/`](../winget/) process).
