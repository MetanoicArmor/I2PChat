# I2PChat v1.3.0 — Text groups, multi-peer live routing, Saved-peers model

## EN

### Scope

Feature release building on **v1.2.6** (`SessionManager`). Delivers **text groups** (multi-member chats with live + pairwise BlindBox offline fan-out), a **multi-peer live session** model (parallel secure streams), and the **Saved peers** inbound policy (legacy **Lock to peer** removed). Includes router/group UI alignment, BlindBox snapshot metadata, and documentation refresh.

### Highlights

- **Text groups:** `GroupManager`, mesh/topology helpers, group history in Qt, canonical I2P ids for transport and errors, BlindBox offline fan-out per member over bilateral channels, live-slot routing for group traffic.
- **Multi-peer / Saved peers:** inbound accepts gated by **Saved peers** / contact book; **Lock to peer** product feature removed with migration from old `.dat` lines; **`activate_peer_context`** unifies UI selection and outbound routing.
- **Parallel live sessions:** multiple concurrent secure peer connections; peer-scoped routing and reset boundaries (`reset_peer_lifecycle`, reduced active-peer fallback in resolution).
- **BlindBox:** per-peer snapshot metadata for diagnostics and routing; optional slow-poll warning off by default.
- **Router / GUI:** router settings coercion; sidebar and search bar alignment with unified group + direct chat navigation.
- **Docs & packaging:** roadmap/codebase map/protocol updates for groups; **winget** / **Homebrew** template directories for **1.3.0** (replace checksums after publishing assets — see below).

### Compatibility

- Wire format remains **vNext** (`PROTOCOL_VERSION` unchanged). Peers on **≥1.2.x** interoperate for 1:1 chat; **groups** require compatible peers running group-capable builds.
- Profile migration: old lock lines merge into Saved peers where applicable (see prior release notes and manuals).

### Tests

Run the full suite before tagging:

```bash
uv run pytest -q
```

### Maintainer checklist (after `v1.3.0` tag and binaries on GitHub)

1. Upload all platform zips (including `*-winget-*` Windows zips).
2. `./packaging/refresh-checksums.sh 1.3.0` — paste SHA256 into **`packaging/homebrew/Casks/*.rb`** (replace `sha256 :no_check`) and **`packaging/winget/1.3.0/*.yaml`** / **`packaging/winget-tui/.../1.3.0/*.yaml`** (replace `0000…` placeholders).
3. `gh release edit v1.3.0 --notes-file docs/releases/RELEASE_1.3.0.md` (optional).

## RU

### Кратко

- **Текстовые группы:** несколько участников, live и офлайн-доставка через BlindBox **по каждому** участнику (парные каналы), маршрутизация group/live, история группы в Qt.
- **Мультипир / Saved peers:** входящие только с адресов из сохранённых контактов; режим **Lock to peer** убран; единый контекст **`activate_peer_context`** для UI и исходящего маршрута.
- **Несколько live-сессий:** параллельные защищённые соединения с разными peer’ами; peer-scoped сброс и политика маршрутизации.
- **BlindBox / роутер:** снимки состояния по peer; настройки роутера; выравнивание боковой панели и поиска.
- **Сборки:** шаблоны **1.3.0** для Homebrew/winget — после публикации zip подставить SHA256 (**`refresh-checksums.sh 1.3.0`**).

### Совместимость

Формат кадров vNext без изменений версии протокола. Группы — только с поддерживающими сборками.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.3.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.3.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.3.0.zip` | Unzip → open I2PChat.app |
