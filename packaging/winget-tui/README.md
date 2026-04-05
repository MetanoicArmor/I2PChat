# winget — TUI-only package (`MetanoicArmor.I2PChat.TUI`)

Portable zip: **`I2PChat-windows-tui-x64-vVERSION.zip`** (see root [`README.md`](../../README.md)). Separate package id from GUI [`MetanoicArmor.I2PChat`](../winget/).

1. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
2. After **`I2PChat-windows-tui-x64-vX.Y.Z.zip`** is on the GitHub release, run from repo root:

   ```bash
   ./packaging/refresh-checksums.sh vX.Y.Z
   ```

   Copy the printed **`InstallerSha256`** for the TUI zip into `MetanoicArmor.I2PChat.TUI.installer.yaml` (under the versioned folder below).
3. Open a PR to `winget-pkgs` following [their README](https://github.com/microsoft/winget-pkgs/blob/master/README.md).

Local validation (Windows, with winget):

```powershell
winget validate --manifest .\packaging\winget-tui\manifests\m\MetanoicArmor\I2PChat\TUI\1.2.3
```

**Folder layout in [winget-pkgs](https://github.com/microsoft/winget-pkgs)** must mirror each `PackageIdentifier` segment after the publisher: for `MetanoicArmor.I2PChat.TUI` use `manifests/m/MetanoicArmor/I2PChat/TUI/<version>/` (not `I2PChat.TUI`). Canonical copies live under [`manifests/m/MetanoicArmor/I2PChat/TUI/`](manifests/m/MetanoicArmor/I2PChat/TUI/) in this tree.

Update **PackageVersion** and duplicate that path for new releases (mirror [`packaging/winget/`](../winget/) process).
