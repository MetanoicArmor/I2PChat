# I2PChat v1.3.2 — UI polish for router dialog, emoji picker hover, and group map visuals

## EN

### Scope

Small UX-focused release after **v1.3.1**. Improves visual consistency in the router dialog, adds hover-driven behavior for the emoji picker, and refreshes the group topology map style to a cleaner desktop/network look.

### Highlights

- **Router dialog alignment:** action buttons in **I2P router** settings are centered consistently. The row with **Open data dir / Open log / Restart bundled router** now aligns with the **Cancel / Save and apply** row.
- **Emoji picker hover UX:** moving the cursor onto the emoji button can open the panel automatically; leaving the button/panel area closes it automatically (with small debounce delays to avoid flicker). Click toggle and keyboard shortcut behavior remain supported.
- **Group map restyle:** removed the central “device-like” vertical band and switched to a neutral network hub background (concentric guide rings + subtle axis guides) to better match desktop visual language.
- **No protocol/runtime change:** this release is UI/UX only and does not change wire framing or storage format.

### Compatibility

- Protocol behavior is unchanged (vNext framing as in previous release line).
- Existing profiles, chats, and BlindBox state are unaffected.

### Tests

```bash
uv run pytest -q
```

### Maintainer checklist (for `v1.3.2` tag + GitHub assets)

1. Build/upload platform artifacts for `v1.3.2`.
2. Refresh checksums/manifests if needed: `./packaging/refresh-checksums.sh 1.3.2`.
3. Publish notes: `gh release edit v1.3.2 --notes-file docs/releases/RELEASE_1.3.2.md` (optional if prefilled in release flow).

## RU

### Кратко

- **Выравнивание в роутере:** в окне **I2P router** нижние кнопки теперь выровнены единообразно: ряд **Open data dir / Open log / Restart bundled router** центрирован так же, как **Cancel / Save and apply**.
- **Ховер для эмодзи:** панель эмодзи может открываться по наведению на кнопку и автоматически закрываться при уходе курсора из зоны кнопка+панель (с небольшими задержками против «дребезга»). Клик и хоткей продолжают работать.
- **Новый стиль Group map:** убран центральный «похожий на смартфон» вертикальный блок; фон заменён на нейтральный сетевой хаб (концентрические орбиты + тонкие направляющие).
- **Без изменений протокола:** релиз затрагивает только UI/UX.

### Совместимость

Протокол и формат данных не менялись; текущие профили/история/BlindBox-состояние совместимы без миграций.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.3.2.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.3.2.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.3.2.zip` | Unzip → open I2PChat.app |
