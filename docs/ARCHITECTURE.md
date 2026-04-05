# I2PChat architecture

The runtime is built around one shared async engine — `I2PChatCore` — with thin UI adapters on top and protocol / crypto / BlindBox services below.

This page is a plain-text map (GitHub’s Mermaid renderer can fail to load third-party script chunks in some browsers).

## Component map

- **UI / entrypoints** — `i2pchat/run_gui.py`, `python -m i2pchat.gui`, PyQt6 [`../i2pchat/gui/main_qt.py`](../i2pchat/gui/main_qt.py) (`ChatWindow` + qasync), [`../i2pchat/presentation/`](../i2pchat/presentation/) (status, drafts, replies, unread, notifications), GUI-side persistence (`chat_history`, `contact_book`, `profile_backup`). The Qt layer calls into **I2PChatCore** and receives status / message / file / delivery callbacks.
- **Shared async core** — [`../i2pchat/core/i2p_chat_core.py`](../i2pchat/core/i2p_chat_core.py): profile/session bootstrap, accept/connect, secure handshake + TOFU pinning, send/receive loops, ACKs and delivery telemetry, text/file/image, BlindBox root exchange; retry helpers [`../i2pchat/core/send_retry_policy.py`](../i2pchat/core/send_retry_policy.py), [`../i2pchat/core/transfer_retry.py`](../i2pchat/core/transfer_retry.py).
- **Protocol + security** — framing in [`../i2pchat/protocol/protocol_codec.py`](../i2pchat/protocol/protocol_codec.py); delivery semantics in [`../i2pchat/protocol/message_delivery.py`](../i2pchat/protocol/message_delivery.py); [`../i2pchat/crypto.py`](../i2pchat/crypto.py) (X25519, Ed25519, HKDF, SecretBox, HMAC).
- **BlindBox** — client ([`../i2pchat/blindbox/blindbox_client.py`](../i2pchat/blindbox/blindbox_client.py)), key schedule, blobs, [`../i2pchat/storage/blindbox_state.py`](../i2pchat/storage/blindbox_state.py), optional [`../i2pchat/blindbox/blindbox_local_replica.py`](../i2pchat/blindbox/blindbox_local_replica.py); replicas over I2P or loopback.
- **Transport** — vendored **i2plib** (SAM session, streams, DEST LOOKUP) ↔ **I2P router (SAM)** ↔ **remote peer**; BlindBox traffic to **replica endpoints**.
- **Profile / local identity** — `profiles/<name>/` (`.dat`, keyring, peer lock, trust store, signing seed) loaded into **I2PChatCore**.

## Runtime in practice

1. **Startup**: `main_qt.py` runs **profile directory migration** when needed (flat `*.dat` in the data root → `profiles/<name>/`) before the profile picker, then creates `ChatWindow`; `start_core()` calls `I2PChatCore.init_session()`, which loads or creates the profile identity, opens the long-lived SAM session, warms up tunnels, and starts `accept_loop()` / `tunnel_watcher()`.
2. **Live chat path**: `connect_to_peer()` or `accept_loop()` establishes an I2P stream; `I2PChatCore` runs the plaintext handshake boundary, verifies/pins the peer signing key (TOFU), derives session subkeys, then switches to encrypted vNext frames through `ProtocolCodec` + `crypto`.
3. **Delivery tracking**: each outgoing text / file / image gets a `MSG_ID` and ACK context; `message_delivery.py` turns low-level outcomes into UI states (`sending`, `queued`, `delivered`, `failed`).
4. **Offline path (BlindBox)**: when no live secure session is available, `send_text()` can route through BlindBox — derive deterministic lookup/blob keys, encrypt a padded blob, PUT it to one or more BlindBox replicas, and later poll / decrypt GET results back into the chat stream.
5. **UI responsibility split**: `I2PChatCore` stays UI-agnostic and emits callbacks only; the Qt layer renders chat, status and notifications, while GUI-side storage modules persist chat history, contacts, drafts and backup/export data.

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
- [BUILD.md](BUILD.md) — release builds and operational env vars
