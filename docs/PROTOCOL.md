# I2PChat protocol specification

## Scope

This document describes the network-facing protocol used by the current
I2PChat architecture:

- live chat over **I2P SAM**;
- binary **vNext** framing;
- secure handshake and post-handshake encryption;
- delivery acknowledgements;
- file / image transfer signaling;
- BlindBox offline-delivery side protocol.

It is intended as a developer-facing reference. Implementation details live in:

- `i2pchat/protocol/protocol_codec.py`
- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/crypto.py`
- `i2pchat/blindbox/*.py`

## Transport

I2PChat uses a byte stream over the I2P SAM interface. In practice this means a
single TCP-like stream between peers, established through SAM.

There are two layers on top of that stream:

1. **live chat framing** (`vNext`);
2. **BlindBox replica protocol** for delayed/offline text delivery.

These two layers are distinct and should not be confused.

## Versioning and compatibility

- Current framing version: `PROTOCOL_VERSION = 4`
- Canonical framing codec: `i2pchat/protocol/protocol_codec.py`
- Legacy parsing exists only as an explicit compatibility mode (`allow_legacy`)

The modern protocol should be treated as the source of truth. Legacy support is
strictly opt-in and is not auto-detected from arbitrary incoming bytes.

## vNext frame layout

Every live frame uses this binary header:

```text
MAGIC (4) | VER (1) | TYPE (1) | FLAGS (1) | MSG_ID (8) | LEN (4) | PAYLOAD
```

Canonical values:

- `MAGIC = b"\x89I2P"`
- `VER = 4`
- `MSG_ID` is an unsigned 64-bit identifier
- `LEN` is the payload length in bytes

Reference:

- `HEADER_STRUCT = struct.Struct(">4sBBBQI")`
- `HEADER_SIZE = HEADER_STRUCT.size`

### Header fields

| Field | Meaning |
|------|---------|
| `MAGIC` | frame resynchronization marker |
| `VER` | framing version |
| `TYPE` | one-byte application message type |
| `FLAGS` | framing flags (currently encrypted bit) |
| `MSG_ID` | per-message identifier for ACK correlation |
| `LEN` | payload length |

### Flags

Currently defined:

- `FLAG_ENCRYPTED = 0x01`

If the encrypted flag is set, the payload is not raw application body; see the
post-handshake encryption section below.

## Connection preface and handshake boundary

Before secure messaging is established, peers exchange identity-preface data and
perform a plaintext handshake.

Important developer rule:

- plaintext `H` handshake messages are valid only before the secure channel is
  established;
- after handshake completion, plaintext application frames are treated as a
  protocol downgrade / violation.

This behaviour is enforced in `i2pchat/core/i2p_chat_core.py` and regression
tested in `tests/test_protocol_framing_vnext.py`.

## Handshake

Handshake message type:

- `TYPE = "H"`

The handshake payload is plaintext UTF-8 and uses structured text messages such
as:

- `INIT:...`
- `RESP:...`

The secure session derives subkeys from:

- ephemeral Diffie-Hellman shared secret;
- initiator nonce;
- responder nonce.

Key derivation reference:

- `derive_handshake_subkeys(...)`
- HKDF labels:
  - `I2PCHAT-HS3-SALT|`
  - `I2PCHAT-HS3|key|enc`
  - `I2PCHAT-HS3|key|mac`

### Handshake outputs

After a successful handshake, the session has:

- encryption key `k_enc`;
- integrity/MAC key `k_mac`;
- sequence tracking / replay checks.

## Post-handshake encrypted payload format

After the secure channel is open, frames carry encrypted payloads.

Logical encrypted body:

```text
SEQ (8) | SecretBox ciphertext | HMAC-SHA256 (32)
```

The encrypted framing trailer size is therefore:

- `ENCRYPTED_TRAILER_SIZE = 8 + 32`

### Encryption

- cipher: NaCl `SecretBox`
- authenticated encryption payload is produced by `crypto.encrypt_message`

### Integrity

The outer MAC is computed with:

- `msg_type`
- `seq`
- `flags`
- `msg_id`
- encrypted body bytes

Reference:

- `crypto.compute_mac(...)`
- `crypto.verify_mac(...)`

### Replay / ordering

The `SEQ` field is part of encrypted-session state and must advance
monotonically. Sequence mismatches are treated as protocol violations.

## Padding profile

Even with encryption, the following metadata remains observable on the wire:

- frame type;
- frame length;
- pre-handshake identity preface exchange.

To reduce traffic-shape leakage, encrypted payloads can be padded.

Supported profiles:

- `balanced` — pad to 128-byte buckets;
- `off` — disable padding.

Runtime control:

```bash
I2PCHAT_PADDING_PROFILE=off
```

Padding envelope magic:

- `I2PPAD1`

Implementation lives in `i2pchat/core/i2p_chat_core.py`.

## Message types

The protocol intentionally keeps message types compact.

### Core live-channel types

| Type | Purpose |
|------|---------|
| `H` | plaintext handshake |
| `U` / `S` / `P` / `O` | control / identity / session flow |
| `D` | text data or file chunk body, depending on active flow |
| `F` | file offer / file metadata |
| `E` | end-of-file marker |
| `I` | image-line / image-text transport path |
| `G` | inline binary image transport path |

Exact semantics are context-sensitive in `i2pchat/core/i2p_chat_core.py`, so the
code should be consulted for branch-specific handling.

### Signals carried as payload text

Some higher-level acknowledgements and state transitions are represented as
payload strings with a `__SIGNAL__:` prefix rather than separate frame types.

Examples include:

- `MSG_ACK|...`
- `FILE_ACK|...`
- `IMG_ACK|...`
- `ABORT_FILE`
- `REJECT_FILE`
- BlindBox root synchronization signals

These signal payloads are parsed in the live core and tied back to `MSG_ID`,
session epoch, and delivery telemetry.

## Acknowledgements and delivery state

The protocol distinguishes local acceptance from peer-confirmed delivery.

The main ACK families are:

- text message ACK (`MSG_ACK`)
- file ACK (`FILE_ACK`)
- image ACK (`IMG_ACK`)

They are correlated against:

- `MSG_ID`
- filename or attachment token where relevant
- current ACK session epoch

The UI delivery model (`sending`, `queued`, `delivered`, `failed`) is therefore
not just visual chrome: it is derived from explicit protocol events plus
offline-delivery state.

Reference:

- `i2pchat/protocol/message_delivery.py`
- ACK handling branches in `i2pchat/core/i2p_chat_core.py`

## File transfer

High-level flow:

1. sender emits `F` with file metadata;
2. receiver accepts or rejects;
3. sender streams base64 chunks via `D`;
4. sender closes transfer via `E`;
5. delivery may later be confirmed by ACK/signal flow.

Important behavioural properties:

- chunks are streamed in bounded units;
- transfers can be aborted or rejected;
- filename handling is sanitized and sandboxed;
- receiver-side naming collisions are resolved safely instead of overwriting.

Related implementation:

- `send_file(...)`
- incoming file handlers in `i2pchat/core/i2p_chat_core.py`

## Image transfer

There are two image-related modes:

1. **inline binary image transfer**
2. **rendered image lines** (text-rendered modes such as braille / bw)

Supported regular image formats:

- PNG
- JPEG
- WebP

Images follow the same general secure-channel guarantees as other encrypted live
traffic, with additional local validation on size, dimensions, and format.

## BlindBox offline-delivery protocol

BlindBox is a separate delayed-delivery mechanism for text messages.

Important boundary:

- the live chat framing and the BlindBox replica protocol are different;
- BlindBox does not reuse the vNext binary frame layout.

### BlindBox key schedule

Key derivation is documented in:

- `i2pchat/blindbox/blindbox_key_schedule.py`

The schedule derives:

- lookup token
- blob encryption key
- state-related keys

### BlindBox blob format

Canonical magic:

- `BLNDBX01`

Implementation:

- `i2pchat/blindbox/blindbox_blob.py`

The blob layer includes:

- explicit header;
- encrypted body;
- padding to fixed buckets (256-byte profile in current implementation).

### BlindBox replica wire protocol

Replica protocol is line-oriented and intentionally simple.

Core commands:

- `PUT key size [token]\n`
- `GET key [token]\n`

Reference implementations:

- client: `i2pchat/blindbox/blindbox_client.py`
- local replica: `i2pchat/blindbox/blindbox_local_replica.py`

### Root synchronization over live channel

BlindBox root/bootstrap coordination still happens through the live secure
channel using dedicated signals such as:

- `BLINDBOX_ROOT`
- `BLINDBOX_ROOT_ACK`

This is distinct from the replica `PUT`/`GET` traffic.

## Security notes

- Post-handshake plaintext traffic is treated as suspicious / downgrade-like.
- Some metadata necessarily remains visible (type, length, preface).
- Padding reduces but does not eliminate traffic analysis leakage.
- BlindBox is intentionally narrower than the live protocol and is aimed at
  offline text delivery, not arbitrary protocol generalization.

## Code map

| Area | Canonical implementation |
|------|---------------------------|
| Framing | `i2pchat/protocol/protocol_codec.py` |
| Handshake / session state | `i2pchat/core/i2p_chat_core.py` |
| Crypto primitives | `i2pchat/crypto.py` |
| Delivery model | `i2pchat/protocol/message_delivery.py` |
| BlindBox client | `i2pchat/blindbox/blindbox_client.py` |
| BlindBox blob | `i2pchat/blindbox/blindbox_blob.py` |
| BlindBox local replica | `i2pchat/blindbox/blindbox_local_replica.py` |
| Protocol regressions | `tests/test_protocol_framing_vnext.py` |

## Practical reading order

For developers new to the repository, the easiest order is:

1. `docs/PROTOCOL.md`
2. `README.md` protocol overview
3. `i2pchat/protocol/protocol_codec.py`
4. `i2pchat/crypto.py`
5. `i2pchat/core/i2p_chat_core.py`
6. `i2pchat/blindbox/*.py`
7. `tests/test_protocol_framing_vnext.py`
