# I2PChat architecture

The runtime is built around one shared async engine — `I2PChatCore` — with **`SessionManager`** (v1.2.6+) owning transport/session lifecycle state per peer, thin UI adapters on top, and protocol / crypto / BlindBox services below.

This page is a plain-text map (GitHub’s Mermaid renderer can fail to load third-party script chunks in some browsers).

## Component map

- **UI / entrypoints** — `i2pchat/run_gui.py`, `python -m i2pchat.gui`, PyQt6 [`../i2pchat/gui/main_qt.py`](../i2pchat/gui/main_qt.py) (`ChatWindow` + qasync), [`../i2pchat/presentation/`](../i2pchat/presentation/) (status, drafts, replies, unread, notifications), GUI-side persistence (`chat_history`, `contact_book`, `profile_backup`). The Qt layer calls into **I2PChatCore** and receives status / message / file / delivery callbacks.
- **Session / transport lifecycle (`SessionManager`, v1.2.6+)** — [`../i2pchat/core/session_manager.py`](../i2pchat/core/session_manager.py): per-peer transport state (connecting / handshaking / secure / stale / failed / disconnected), outbound policy (`LIVE_ONLY`, `PREFER_LIVE_FALLBACK_BLINDBOX`, `QUEUE_THEN_RETRY_LIVE`, `BLINDBOX_ONLY`), per-peer reconnect metadata, outbound stream registry, inflight message IDs for ACK correlation, secure-session TTL / stale health. **`I2PChatCore`** updates this layer on connect, handshake completion/failure, disconnect, and keepalive loss; live availability for routing and `get_delivery_telemetry()` is derived here (with legacy fallbacks where no peer record exists yet).
- **Text groups (`GroupManager`)** — [`../i2pchat/groups/manager.py`](../i2pchat/groups/manager.py): multi-member conversations use the same vNext stream as 1:1 chat; group payloads are encoded via [`../i2pchat/groups/wire.py`](../i2pchat/groups/wire.py). Offline delivery fans out per member over **pairwise** BlindBox channels (not a separate group-wide replica key). Persisted state: [`../i2pchat/storage/group_store.py`](../i2pchat/storage/group_store.py); Qt presentation: [`../i2pchat/presentation/group_conversations.py`](../i2pchat/presentation/group_conversations.py).
- **Router backend (bundled `i2pd`)** — [`../i2pchat/router/bundled_i2pd.py`](../i2pchat/router/bundled_i2pd.py) + [`../i2pchat/router/settings.py`](../i2pchat/router/settings.py): optional sidecar process and **`router_prefs.json`** alignment with the **I2P router…** dialog.
- **Per-peer live SAM streams (`LivePeerSession`)** — each active I2P stream is a **[`LivePeerSession`](../i2pchat/core/live_peer_session.py)** in **`I2PChatCore._live_sessions[peer_id]`** (normalized bare id): `conn` (reader/writer), crypto state, pending ACK tables, transfer flags, and receive-loop context. **`current_peer_addr`** is **UI/chat selection only** (which dialog is “active”); it does **not** define transport routing or ACK correlation. Outbound sends and ACK registration use the **peer chosen for that operation** (e.g. `peer_for_route` / explicit `peer_address`), and incoming `accept_loop` does **not** auto-switch `current_peer_addr`—the user picks the peer in the UI (notifications, contact list). **Framing** (`frame_message` / `frame_message_with_id`) resolves crypto state from the live session when present, otherwise from core fields before a session row exists (tests / pre-connect).
- **Shared async core** — [`../i2pchat/core/i2p_chat_core.py`](../i2pchat/core/i2p_chat_core.py): profile/session bootstrap, accept/connect, secure handshake + TOFU pinning, send/receive loops, ACKs and delivery telemetry, text/file/image, BlindBox root exchange; retry helpers [`../i2pchat/core/send_retry_policy.py`](../i2pchat/core/send_retry_policy.py), [`../i2pchat/core/transfer_retry.py`](../i2pchat/core/transfer_retry.py).
- **Protocol + security** — framing in [`../i2pchat/protocol/protocol_codec.py`](../i2pchat/protocol/protocol_codec.py); delivery semantics in [`../i2pchat/protocol/message_delivery.py`](../i2pchat/protocol/message_delivery.py); [`../i2pchat/crypto.py`](../i2pchat/crypto.py) (X25519, Ed25519, HKDF, SecretBox, HMAC).
- **BlindBox** — client ([`../i2pchat/blindbox/blindbox_client.py`](../i2pchat/blindbox/blindbox_client.py)), key schedule, blobs, [`../i2pchat/storage/blindbox_state.py`](../i2pchat/storage/blindbox_state.py), optional [`../i2pchat/blindbox/blindbox_local_replica.py`](../i2pchat/blindbox/blindbox_local_replica.py); replicas over I2P or loopback.
- **Transport** — internal **`i2pchat.sam`** (SAM session, streams, naming/dest lookup) ↔ **I2P router (SAM)** ↔ **remote peer**; BlindBox traffic to **replica endpoints**.
- **Profile / local identity** — `profiles/<name>/` (`.dat`, keyring, peer lock, trust store, signing seed) loaded into **I2PChatCore**.

## Toolchain

- **uv** — Python dependencies are declared in **`pyproject.toml`** and pinned in **`uv.lock`**. Contributors typically run **`uv sync`** then **`uv run python -m i2pchat.gui`** / **`i2pchat.tui`** (see root **README**).
- **`i2pchat.sam`** — in-tree SAM client and protocol helpers (HELLO, SESSION, STREAM, NAMING, reply parsing). I2PChat does **not** depend on PyPI **`i2plib`**; a former vendored tree was removed in favor of this package.

## Runtime in practice

1. **Startup**: `main_qt.py` runs **profile directory migration** when needed (flat `*.dat` in the data root → `profiles/<name>/`) before the profile picker, then creates `ChatWindow`; `start_core()` calls `I2PChatCore.init_session()`, which loads or creates the profile identity, opens the long-lived SAM session, warms up tunnels, and starts `accept_loop()` / `tunnel_watcher()`.
2. **Transport lifecycle**: connect/disconnect, handshake success/failure, stream registration, and live-health signals update **`SessionManager`** so **`get_delivery_telemetry()`** and outbound policy agree with UI (e.g. **Send** vs **Send offline**). System/UI notifications after handshake are emitted only after the manager records a secure peer, so the Qt send button label updates immediately when the live path becomes ready.
3. **Live chat path**: `connect_to_peer()` or `accept_loop()` establishes an I2P stream; `I2PChatCore` runs the plaintext handshake boundary, verifies/pins the peer signing key (TOFU), derives session subkeys, then switches to encrypted vNext frames through `ProtocolCodec` + `crypto`.
4. **Delivery tracking**: each outgoing text / file / image gets a `MSG_ID` and ACK context; `message_delivery.py` turns low-level outcomes into UI states (`sending`, `queued`, `delivered`, `failed`).
5. **Offline path (BlindBox)**: when no live secure session is available, `send_text()` can route through BlindBox — derive deterministic lookup/blob keys, encrypt a padded blob, PUT it to one or more BlindBox replicas, and later poll / decrypt GET results back into the chat stream.
6. **UI responsibility split**: `I2PChatCore` stays UI-agnostic and emits callbacks only; the Qt layer renders chat, status and notifications, while GUI-side storage modules persist chat history, contacts, drafts and backup/export data.

## Wire format (summary)

Traffic is a **byte stream** over **I2P SAM** (one TCP session to the router). Application data uses **vNext binary frames**:

```
┌─────────── vNext frame ────────────────────────────────────────┐
│ MAGIC (4) │ VER (1) │ TYPE (1) │ FLAGS (1) │ MSG_ID (8) │ LEN (4) │ PAYLOAD (LEN bytes) │
└──────────────────────────────────────────────────────────────────┘
```

- **Handshake** uses **plain** frame bodies (UTF‑8 text: identities, `INIT` / replies, signatures).
- After the secure handshake, payloads are **encrypted** (`FLAGS` marks it): each body is **sequence (8 B) + ciphertext + MAC** (NaCl SecretBox + HMAC over metadata).
- **Message IDs** and **sequence numbers** tie frames to ordering and replay protection.

Full specification: [**PROTOCOL.md**](PROTOCOL.md). Optional payload padding for traffic-shape mitigation: [**BUILD.md**](BUILD.md#protocol-padding-profile).

## See also

- [MANUAL_EN.md](MANUAL_EN.md) / [MANUAL_RU.md](MANUAL_RU.md) — user-facing behavior
- [releases/RELEASE_1.3.0.md](releases/RELEASE_1.3.0.md) — text groups, multi-peer live routing, Saved peers (v1.3.0)
- [releases/RELEASE_1.2.6.md](releases/RELEASE_1.2.6.md) — SessionManager transport refactor and test notes (v1.2.6)
- [BUILD.md](BUILD.md) — release builds and operational env vars
