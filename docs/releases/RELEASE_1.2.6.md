# I2PChat v1.2.6 — SessionManager transport lifecycle (embedded)

## EN

### Scope

Incremental hardening/refactor of SAM session lifecycle ownership without introducing daemon/server/IPC architecture. Transport lifecycle stays embedded in the client process.

### Goals

- Make `SessionManager` the stronger source of truth for transport/session lifecycle.
- Move per-peer lifecycle ownership out of `I2PChatCore` where practical.
- Preserve direct chat behavior.
- Preserve BlindBox offline fallback path.
- Keep changes reviewable and test-backed.

### Audit Summary

**Session logic trapped in `I2PChatCore`**

- Live path gating and route decisions relied heavily on global `self.conn` + `self.handshake_complete` checks.
- Disconnect and receive-loop cleanup performed peer-state transitions directly from core.
- Handshake success/failure paths applied duplicate/overlapping peer transitions.

**Global state that needed per-peer ownership**

- Reconnect metadata (`attempt`, `next_retry_mono`, failure reason).
- Peer transport state (connecting/handshaking/secure/stale/failed/disconnected).
- Stream registry and activity updates.
- Inflight message tracking hooks.

**Coupling / dead-path risks identified**

- Duplicate secure/disconnect transitions across core and manager.
- Reconnect metadata mostly diagnostic (telemetry) rather than owned per-peer lifecycle state.
- Core remained the practical source of truth for route choice until this refactor step.

### Delivered Changes

#### A) SessionManager per-peer lifecycle ownership

Updated `i2pchat/core/session_manager.py`:

- Added `PeerTransportState` with peer-scoped fields:
  - `peer_state`, `connected`, `handshake_complete`
  - `secure_since_mono`, `stale_since_mono`, health timestamps
  - per-peer `ReconnectMetadata`
  - per-peer stream registry
  - per-peer inflight message IDs
- Added peer-aware APIs:
  - `set_active_peer`, `ensure_peer_transport`, `get_peer_transport`
  - `set_peer_connected`, `set_peer_handshake_complete`, `set_peer_disconnected`, `mark_peer_failed`
  - `register_stream`/`update_stream_state`/`unregister_stream` with `peer_id`
  - `register_inflight_message`, `acknowledge_inflight_message`, `clear_inflight_messages`
- Added secure session TTL / stale detection:
  - `refresh_peer_health`, `is_peer_secure_channel_ready`
- Kept backward compatibility:
  - `is_live_path_alive` and `select_outbound_policy` still accept legacy booleans, but now prefer peer-aware state when available.

#### B) Core thinning and lifecycle delegation

Updated `i2pchat/core/i2p_chat_core.py`:

- `I2PChatCore` now reports peer transitions via SessionManager APIs in key paths:
  - outbound connect start/fail/success preparation
  - handshake success/failure/role conflict/timeout
  - disconnect and receive-loop cleanup
  - keepalive failure
- Route selection and live-availability checks now query SessionManager with peer context.
- Delivery telemetry reconnect fields are read from active peer reconnect metadata.
- Added inflight hooks in ACK lifecycle (`register` on pending ACK, `acknowledge` on ACK receive, clear on ACK session roll).
- Removed duplicate `current_peer_addr` assignment in outbound connect path.
- UI notifications after a successful secure handshake are emitted **after** `session_manager` marks the peer secure, so the Send button label updates immediately (e.g. `Send offline` → `Send`).

#### C) Route policy centralization behavior (preserved)

`SessionManager.select_outbound_policy` remains the policy decision point with these modes:

- `LIVE_ONLY`
- `PREFER_LIVE_FALLBACK_BLINDBOX`
- `QUEUE_THEN_RETRY_LIVE`
- `BLINDBOX_ONLY`

Behavior remains backward-compatible:

- live-only still blocks if secure live path is not ready;
- auto still uses offline queue when live secure channel is not ready and BlindBox path is available.

#### D) Reliability improvements

- Secure session TTL/stale tracking added in SessionManager.
- Inflight registry hooks added in SessionManager and integrated into core ACK flow.
- Graceful shutdown cleanup coverage improved via dedicated tests.
- Session transitions now logged through clearer, peer-aware manager transitions.

### Tests and Validation

**Added/updated tests**

- `tests/test_session_manager.py` — per-peer reconnect backoff, stream isolation, active-peer recompute, stale TTL, inflight registry
- `tests/test_send_text_routing.py` — live vs BlindBox during handshake, auto routing
- `tests/test_shutdown_cleanup.py` — shutdown ordering, BlindBox runtime cleanup

**Verification run**

- Focused: `uv run pytest tests/test_session_manager.py tests/test_send_text_routing.py tests/test_shutdown_cleanup.py -q`
- Full suite: `uv run pytest -q`
- Result: `643 passed, 64 subtests passed`

### Commits (small/reviewable)

1. `534cc88` — Make SessionManager the per-peer transport state owner
2. `6b35ae0` — Route core lifecycle decisions through SessionManager peer state

### Post-`88a7707` polish (small scope)

- `active_peer` was narrowed to explicit compatibility/view-only behavior:
  - no implicit active-peer fallback in generic peer resolution paths;
  - route/liveness truth remains peer-scoped (`peer_id`) and transport-state driven.
- Internal reset lifecycle usage remains centered on `reset_peer_lifecycle()`:
  - compatibility aliases `reset_peer_session()` / `reset_peer_transport()` stay as wrappers only.
- Peer-reset isolation tests were strengthened:
  - reset of one peer does not imply full manager shutdown;
  - reset preserves other peer state (streams/inflight/secure state) until explicit full shutdown.

### What still remains in core (intentional for this step)

- Protocol framing, crypto, handshake message semantics, UI callbacks.
- Raw live stream handle (`self.conn`) ownership.
- Peer identity-binding storage fields (`current_peer_addr` / `current_peer_dest_b64`) are still core-owned.

### Risks / Follow-ups

- Reconnect metadata is now peer-scoped but still mostly consumed for telemetry; a scheduler that actively consumes `next_retry_mono` is the next hardening step.
- Full migration of connection handle ownership (`self.conn`) into SessionManager is still pending.

### Suggested next step toward group-ready transport

Introduce a SessionManager-managed peer connection slot abstraction (per-peer connection records + reconnect scheduler + health snapshots) while keeping protocol engines in `I2PChatCore`.

## RU

### Кратко

- Выделен слой **`SessionManager`** (`i2pchat/core/session_manager.py`): жизненный цикл SAM/транспорта отделён от бизнес-логики чата.
- Из **`I2PChatCore`** перенесены сессия SAM, задачи accept/tunnel/keepalive/watchdog/disconnect, реестр outbound streams, reconnect/backoff, признак «live path».
- Добавлены машины состояний транспорта и пира; политика исходящей отправки централизована (`LIVE_ONLY`, `PREFER_LIVE_FALLBACK_BLINDBOX`, и т.д.).
- Поведение **`auto`** и wire-протокол сохранены; после стабилизации — правки BlindBox (polling, таймауты, диагностика).
- После успешного secure handshake UI-уведомления идут **после** фиксации состояния в менеджере, чтобы подпись кнопки **Send** обновлялась сразу.

### Совместимость

Публичный API `I2PChatCore` и формат протокола приложения не менялись.

### Проверка

```bash
uv run pytest tests/test_session_manager.py tests/test_send_text_routing.py tests/test_shutdown_cleanup.py -q
```

### Полировка после `88a7707` (узкий scope)

- `active_peer` дополнительно зафиксирован как compatibility/view-only указатель:
  - убран неявный fallback через active-peer в общих путях резолва peer.
- Внутренний reset-поток закреплён за `reset_peer_lifecycle()`:
  - `reset_peer_session()` и `reset_peer_transport()` оставлены как совместимые алиасы-обёртки.
- Усилены тесты изоляции peer reset:
  - reset одного пира не означает полный shutdown менеджера;
  - состояние других пиров (secure/streams/inflight) не должно затрагиваться до явного полного shutdown.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.6.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.2.6.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.6.zip` | Unzip → open I2PChat.app |
