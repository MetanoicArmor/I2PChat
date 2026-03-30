# I2PChat v0.8.0 — Trust, delivery, offline clarity

## EN

### Summary

`v0.8.0` closes the **0.8.0 roadmap milestone** ([backlog §13–18](../ISSUE_BACKLOG_RU.md)): clearer **per-message delivery**, explicit **trust / key-change** flows, **lock-to-peer** messaging, **BlindBox diagnostics**, and **retry** for failed sends. Core protocol framing for online chat is unchanged; history encryption format may carry delivery metadata for UI restore.

### User-visible changes

- **Outgoing queue & delivery states (issues 13, 14)**  
  Outgoing bubbles can show lifecycle: **sending**, **queued** (offline / BlindBox path), **delivered**, **failed** — with tooltips and status-line hints tied to `get_delivery_telemetry()` where applicable.

- **Key change / trust (issues 15, 16)**  
  **TOFU** and **signing-key change** paths use explicit dialogs (`handle_trust_decision`, `handle_trust_mismatch_decision`); lock-to-peer constraints (e.g. switching saved peers) use clear English copy where surfaced.

- **BlindBox diagnostics (issue 17)**  
  **⋯ menu → BlindBox diagnostics**: read-only summary from [`blindbox_diagnostics.build_blindbox_diagnostics_text`](../../blindbox_diagnostics.py) (availability, common failure reasons).

- **Retry failed sends (issue 18)**  
  Failed outbound items can expose **Retry** from the bubble context menu when marked `retryable` (`retryRequested` → `_on_retry_requested`).

### Developer / modules

- [`i2pchat/protocol/message_delivery.py`](../../i2pchat/protocol/message_delivery.py) — delivery state labels and helpers.  
- [`i2pchat/core/i2p_chat_core.py`](../../i2pchat/core/i2p_chat_core.py) — send results, ACK / text delivery callbacks, trust callbacks.  
- [`i2pchat/gui/main_qt.py`](../../i2pchat/gui/main_qt.py) — `ChatItem.delivery_state`, delegate rendering, history persistence of delivery fields, telemetry wiring.  
- [`i2pchat/blindbox/blindbox_diagnostics.py`](../../i2pchat/blindbox/blindbox_diagnostics.py), [`i2pchat/core/send_retry_policy.py`](../../i2pchat/core/send_retry_policy.py).

### Backlog coverage (issues 13–18)

| Issue | Theme | Status in this release |
|-------|--------|-------------------------|
| 13 | Outgoing queue UI | Queued vs live distinction in UI + routing copy |
| 14 | Per-message delivery states | `sending` / `queued` / `delivered` / `failed` on bubbles |
| 15 | Key change warning | Explicit mismatch / TOFU decision handlers |
| 16 | Trust & lock-to-peer UX | Tooltips, dialogs, blocked peer switch when locked |
| 17 | BlindBox diagnostics | ⋯ → BlindBox diagnostics screen |
| 18 | Retry failed | Context-menu Retry when `retryable` |

### Compatibility

Minor release on the **0.8.x** line. Prefer upgrading together: **GUI**, **core**, and **encrypted history** expectations assume delivery metadata where saved.

### Repository layout

- Release notes live under **`docs/releases/`**.

---

## RU

### Кратко

`v0.8.0` — веха **0.8.0** из roadmap: **понятная доставка сообщений** (очередь, офлайн), **явные сценарии доверия и смены ключа**, ясность **Lock to peer**, **диагностика BlindBox**, **повтор отправки** при сбоях. Протокол live-чата не менялся; в истории могут сохраняться поля состояния доставки для отображения.

### Что видит пользователь

- Состояния исходящих сообщений: отправка, в очереди, доставлено, ошибка; подсказки и строка статуса.
- Диалоги при первом контакте и при **несовпадении ключа**; привязка профиля к пиру блокирует переключение контакта из списка, пока активен lock.
- Пункт **«BlindBox diagnostics»** в меню **⋯** — текстовая диагностика офлайн-пути.
- **Retry** в контекстном меню бабла для помеченных неудачных отправок.

### Сверка с backlog 13–18

Таблица соответствия — в английском блоке выше (**Backlog coverage**).

### Разработка

См. список модулей в EN-секции.

### Совместимость

Линейка **0.8.x**; желательно обновлять клиент целиком при работе с уже зашифрованной историей с новыми полями.

### Структура репозитория

- Описания релизов в **`docs/releases/`**.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v0.8.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v0.8.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v0.8.0.zip` | Unzip → open I2PChat.app |
