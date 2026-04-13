# I2PChat v1.3.1 — Intel macOS bundled i2pd, BlindBox hardening, Nix & icons

## EN

### Scope

Maintenance and polish release after **v1.3.0**. Improves **portable macOS Intel (x86_64)** builds with a correctly embedded **bundled `i2pd`**, strengthens **BlindBox** runtime behavior for groups and mocks, refreshes the **Nix** packaging story, ships **new application icons**, and includes small **GUI** fixes plus documentation updates.

### Highlights

- **macOS Intel (`x86_64`):** build scripts and runtime resolve **`vendor/i2pd/darwin-x64/i2pd`** (PyInstaller, `.app` bundle, `bundled_i2pd` lookup). The companion repo **[i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)** now ships a **`darwin-x64`** tree so `ensure_bundled_i2pd` / `fetch_bundled_i2pd` can stage the router for Intel portable builds. **Auto-update** artifact prefix for Intel macOS aligns with **`I2PChat-macOS-x64`** zip names.
- **BlindBox / groups:** runtime **retry/backoff** on transport-style failures; **legacy group BlindBox outbound** gated behind **`I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`**; safe **`is_runtime_ready`** probing via **`_blindbox_client_runtime_ready`** (tests and lightweight doubles); **parallel offline fan-out** for group envelopes (`asyncio.gather`) while send serialization stays lock-guarded.
- **Nix (`flake.nix`):** Qt6 **multimedia**, **SVG**, **Wayland** plugin paths; Linux **notify** / sound helpers on `PATH`; **desktop** metadata and **`nix profile install`** documented in **README** / **BUILD**; **`keyring`** in the Python env (Secret Service still optional).
- **Icons:** refreshed **`icon.png`**, **`i2pchat.ico`**, **`I2PChat.icns`** from **`image.png`**; **`make_icon.py`** defaults to **`image.png`** (optional override path as `argv[1]`).
- **GUI:** dialog **checkbox** SVG path/styling; **group editor** member rows use real **QCheckBox** widgets on the light theme.

### Compatibility

- Wire format remains **vNext** (`PROTOCOL_VERSION` unchanged). Peers on **≥1.3.0** behave as before for 1:1 and groups.
- **BlindBox:** optional legacy group outbound is off unless **`I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`** is set (see core behavior in v1.3.0 notes).

### Tests

```bash
uv run pytest -q
```

### Maintainer checklist (after `v1.3.1` tag and binaries on GitHub)

1. Upload all platform zips (including **`*-winget-*`** Windows zips if you ship them).
2. `./packaging/refresh-checksums.sh 1.3.1` — update **Homebrew** / **winget** manifests if you replace `sha256 :no_check` / placeholders. **winget-pkgs:** two separate PRs — `MetanoicArmor.I2PChat` (paths under `packaging/winget/manifests/m/MetanoicArmor/I2PChat/1.3.1/`) and `MetanoicArmor.I2PChat.TUI` (`packaging/winget-tui/.../TUI/1.3.1/`); do not combine in one PR.
3. `gh release edit v1.3.1 --notes-file docs/releases/RELEASE_1.3.1.md` (optional).

## RU

### Кратко

- **macOS Intel:** встроенный **`i2pd`** для портативных сборок из **`darwin-x64`**; бинарь также в **[i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)**; префикс zip для автообновления на Intel — **`I2PChat-macOS-x64`**.
- **BlindBox / группы:** повтор при сбоях транспорта; наследие group BlindBox — за флагом **`I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`**; безопасная проверка готовности рантайма; параллельная офлайн-рассылка по участникам группы.
- **Nix:** Qt-плагины, уведомления/звук, desktop, **`nix profile install`**, документация.
- **Иконки:** набор из **`image.png`** через **`make_icon.py`**.
- **GUI:** SVG чекбокса в диалогах; чекбоксы в редакторе группы на светлой теме.

### Совместимость

Протокол без изменений версии кадров. Поведение групп совместимо с **1.3.0**, опциональные флаги — см. выше.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.3.1.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.3.1.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.3.1.zip` | Unzip → open I2PChat.app |
