# Compatibility specification (C++ port)

This document is the byte-level contract the C++ client must satisfy to
interoperate with the Python implementation 1.4.x. Where this document and
`docs/PROTOCOL.md` disagree, **the Python source code wins** — the older
document has drifted in at least one place (see [Handshake](#handshake)).

Every claim here is backed by a fixture under [`../testdata/vectors/`](../testdata/vectors/),
regenerated with:

```bash
.venv/bin/python cpp/testdata/generate_vectors.py
```

The generator replaces the CSPRNG with a deterministic keystream, so the
fixtures are byte-stable across runs. A diff in `vectors/` therefore always
means the Python behaviour changed.

## Wire framing (vNext v4)

Source: `i2pchat/protocol/protocol_codec.py`. Fixture: `protocol_frames.json`.

```
MAGIC(4) | VER(1) | TYPE(1) | FLAGS(1) | MSG_ID(8) | LEN(4) | PAYLOAD
```

- `MAGIC = 89 49 32 50` (`\x89I2P`), `VER = 4`.
- `MSG_ID` and `LEN` are unsigned big-endian. Header is 19 bytes.
- Frame types: `U S P O H F D E I G`. Anything else is a protocol violation.
- `FLAG_ENCRYPTED = 0x01` is the only defined flag.
- `MAX_FRAME_BODY = 2 MiB`; a larger declared length must be rejected.
- The reader scans forward byte by byte for `MAGIC`, giving up after 64 KiB
  (`resync_limit`). Buffering is an implementation detail, but the resync
  semantics and the limit must match.

### Encrypted payload

Body of a frame with `FLAG_ENCRYPTED`:

```
SEQ(8 BE) | SecretBox(nonce24 || ciphertext || tag16) | HMAC-SHA256(32)
```

The MAC covers, in order: UTF-8 `msg_type`, `seq` as 8 bytes BE, `flags & 0xFF`
as 1 byte, `msg_id` as 8 bytes BE, then the SecretBox output. See
`crypto.compute_mac`. `SEQ` must increase monotonically; a mismatch is a
protocol violation, not a recoverable error.

### Padding profile

Profile `balanced` (the default) wraps the plaintext before encryption:

```
"I2PPAD1" | original_len(4 BE) | body | random padding
```

padded up to a multiple of 128 bytes. Profile `off` disables this. On receive,
a payload starting with `I2PPAD1` is unwrapped; anything else is passed through
unchanged, which is what keeps the two profiles mutually compatible.

## Handshake

Source: `i2pchat/crypto.py`, `_build_init_sig_payload` / `_build_resp_sig_payload`
in `i2pchat/core/i2p_chat_core.py`. Fixture: `crypto_handshake.json`.

**The domain labels are inconsistent and must be reproduced verbatim.** Key
derivation uses `HS4`; the signed transcript strings use `HS3`. This is not a
typo to fix — changing either one breaks interoperability. `docs/PROTOCOL.md`
incorrectly documents the KDF labels as `HS3`.

Key schedule, yielding four independent 32-byte directional keys:

```
salt = SHA256("I2PCHAT-HS4-SALT|" || nonce_init || nonce_resp)
prk  = HMAC-SHA256(key = salt, msg = dh_shared)
k    = HKDF-Expand(prk, "I2PCHAT-HS4|key|" + {enc,mac} + "|" + {i2r,r2i}, 32)
```

Directional keys defeat frame reflection: a frame echoed back to its sender is
checked against the opposite direction's key and fails the MAC.

Sequence of events:

1. TCP-like stream established through SAM `STREAM CONNECT` / `STREAM ACCEPT`.
2. Identity preface: the peer sends `destination_base64` followed by `\n`.
3. `S` frame carrying the local destination's base64.
4. Initiator sends `H` frame:
   `INIT:<nonce_hex>:<eph_pub_hex>:<sign_pub_hex>:<sig_hex>`, where the Ed25519
   signature covers
   `I2PCHAT-HS3|INIT|<signer_addr>|<remote_addr>|<nonce>|<eph>|<sign_pub>`.
5. Responder replies `RESP:...`, signing
   `I2PCHAT-HS3|RESP|<signer>|<remote>|<init_nonce>|<init_eph>|<init_sign_pub>|<resp_nonce>|<resp_eph>|<resp_sign_pub>`.
6. X25519 DH, then the key schedule above.
7. Both sides send `H` frame `FINISHED:<hmac_hex>` where
   `hmac = HMAC-SHA256(mac_key, "I2PCHAT-HS4|FINISHED|" || transcript_hash)`
   and `transcript_hash = SHA256("I2PCHAT-HS4-TRANSCRIPT|" || resp_sig_payload)`.
8. The Ed25519 signing key is TOFU-pinned. The channel is secure only after
   both `FINISHED` messages.

Addresses in signed payloads are normalized first: lowercase base32 host with
the `.b32.i2p` suffix stripped.

Timeout is 90 s; `P`/`O` keepalive runs every 15 s on the encrypted channel.

X25519 validation: reject a peer public key that is not 32 bytes, is all-zero,
or produces an all-zero shared secret.

### Connection setup

The caller writes its own destination twice: once as a bare
`<i2p-base64>\n` line and once as the body of a plaintext `S` frame. The line
exists because the accepting side reads the caller's identity with a plain
readline before it parses any frames, and dropping it would break older peers.

The accepting side stays silent until that line arrives, then answers with a
single `S` frame carrying its own destination. It must not send an `S` frame
before the line: the caller is not expecting one, and the extra frame desyncs
the exchange.

The base32 address is the hash of the destination, so the two always agree by
construction. What authenticates the peer is the handshake signature plus the
TOFU pin, not the preface; the SAM naming lookup on the claimed address is a
sanity check that it is really published, and is optional.

### Security rules (must be enforced, not merely supported)

- A plaintext application frame after the handshake completes is a downgrade
  attempt and must be rejected.
- An `H` frame after the handshake completes is likewise rejected:
  renegotiation is not part of the protocol, so it can only be an attempt to
  reset the keys on an authenticated channel.
- Before the handshake completes, only `S`, `H`, `P` and `O` frames are
  accepted. Anything else is application data smuggled past authentication.
- An encrypted frame before keys exist is rejected rather than buffered.
- Sequence numbers must be exactly `previous + 1`. The I2P stream is reliable
  and ordered, so a gap means frames were dropped or injected. A rejected frame
  must not advance the counter, or a single bad frame would poison the channel.
- The MAC is verified before the ciphertext is decrypted, and it covers the
  type, flags and `msg_id`, so a captured frame cannot be relabelled.
- An `S` frame may not change the peer identity mid-session.
- The identity preface line is bounded (8 KiB); an unauthenticated peer must not
  be able to make the receiver buffer without limit.
- A `__SIGNAL__` control frame arriving before the handshake completes is
  ignored, with the single exception of a graceful `QUIT`.
- For `recipient`-scope group transport, the envelope's self-declared
  `sender_id` must denote the same destination as the authenticated transport
  peer, otherwise the message is rejected.

### TOFU pins

Plaintext JSON in `{profile}.trust.json`; public keys only, and readable so a
user can inspect and edit their own pins.

```json
{"version": 2,
 "pins": {"<peer-base32>": {"signing_key_hex": "...", "oob_verified": false}}}
```

Version 1 was a flat `{peer: hex}` map and is still read; saving upgrades the
file in place. Hex is lowercased on load, so an uppercase stored pin does not
read as a key change. A key that contradicts an existing pin is never accepted
without an explicit user decision. A corrupt file degrades to no pins rather
than preventing startup: losing pins is visible and recoverable, refusing to
launch is not.

## Canonical JSON (signature-critical)

Fixture: `canonical_json.json`.

Payloads that get signed are serialized with Python's
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`. A C++
serializer must match this exactly:

- Object keys sorted ascending by Unicode code point. Uppercase ASCII sorts
  before lowercase, `_` (0x5F) sorts between them.
- No whitespace anywhere.
- All non-ASCII escaped as `\uXXXX`; characters outside the BMP become UTF-16
  **surrogate pairs**, e.g. `🔐` is `\ud83d\udd10`.
- Control characters use the short forms `\n \t \r \b \f` where they exist and
  `\uXXXX` otherwise; `"` and `\` are escaped, `/` is not.

Sealed at-rest payloads use the same separators but **do not** sort keys, so
their key order follows insertion. That is harmless: those blobs only need to
round-trip, never to be signed.

## At-rest formats

Fixture: `sealed_files.json`. Common layout:

```
magic(4) | version(2 BE) | salt(32) | SecretBox(nonce24 || ciphertext || tag16)
```

The inner plaintext is JSON with `ensure_ascii=True, separators=(",",":")`.
Legacy plaintext JSON without the magic is still accepted on read and
re-encrypted on the next write.

| Magic | File | Key source | HKDF domain |
|---|---|---|---|
| `I2PK` | `{profile}.dat` | wrap key (keyring or sidecar) | `I2PCHAT-PROFILE-DAT` |
| `I2CB` | `{profile}.contacts.json` | identity key | `I2PCHAT-CONTACTS` |
| `I2CD` | `{profile}.compose_drafts.json` | identity key | `I2PCHAT-COMPOSE-DRAFTS` |
| `I2CH` | `{profile}.history.<sha256(peer)>.enc` | identity key | `I2PCHAT-HISTORY` |
| `I2GS` | `{profile}.group.<sha256(group_id)>.json` | identity key | `I2PCHAT-GROUPSTORE` |
| `I2RA` | inside `{profile}.blindbox_replicas.json` | identity key | `I2PCHAT-REPLICA-AUTH` |

The generic two-stage derivation (`storage/sealed_json.py`) is:

```
prk         = HKDF-Extract(salt = domain, ikm = identity_key)
profile_key = HKDF-Expand(prk, domain || "|profile-key", 32)
file_key    = HKDF-Expand(HKDF-Extract(salt = file_salt, ikm = profile_key),
                          domain || "|file-key", 32)
```

Chat history and the group store use the same shape but bind an extra
identifier into the final `info`: `"I2PCHAT-HISTORY|file-key|" + lower(peer)`
and `"I2PCHAT-GROUPSTORE|file-key|" + sha256_hex(group_id)` respectively. Note
history's first stage uses the bare domain `I2PCHAT-HISTORY` as both extract
salt and `info` prefix.

A file's salt is generated once and reused on every later write, so the file key
stays stable for the life of the file. Writers must read the existing header
rather than rolling a fresh salt.

### Two different address normalisations

The peer address that names and keys a history file is normalised by **trim and
lowercase only** (`chat_history.normalize_peer_addr`). It is *not* the canonical
form used elsewhere: a `.b32.i2p` suffix survives, so `peer` and
`peer.b32.i2p` legitimately name two different history files. Canonicalising
harder in the C++ port would make it look for files Python never wrote, so
`storage::history_peer_key` deliberately reproduces the loose version, while
`storage::normalize_contact_address` reproduces the strict one used by the
contact book (full-string base32 match, suffix stripped, 40–80 characters).

### Sealed payload key order

Sealed payloads are written by Python without `sort_keys`, so the key order of a
re-serialised payload differs from `nlohmann::json`'s sorted output. That is
harmless: only the *signed* canonical JSON above depends on key order. Tests
should compare parsed objects, not bytes, for sealed files.

### One reference quirk not reproduced

Python's history reader coerces a JSON `null` delivery field with
`str(m.get(...))`, turning it into the literal string `"None"`. The C++ reader
treats it as unset. Nothing on disk depends on the difference, and reproducing it
would put the word "None" in the UI.

`{profile}.trust.json` (TOFU pins) is **plaintext** JSON:
`{"version":2,"pins":{peer:{"signing_key_hex":...,"oob_verified":bool}}}`.
Version 1 was a flat `{peer: hex}` map. Pins are not persisted for the
transient profile.

### Profile wrap key

The `.dat` wrap key is 32 random bytes stored as base64 in the OS keyring under
service `i2pchat`, account `{profile}__dat_wrap__`, with a 0600 sidecar
`{profile}.dat.wrap` always kept alongside so a copied profile directory stays
openable. The C++ client must use the identical service and account names.

On macOS the Keychain ACL binds an entry to the requesting binary's signature,
so a differently signed C++ binary will prompt the user on first access even
though the entry exists. Plan the first-run UX around this and fall back to the
sidecar. This is not theoretical: reading an entry the C++ test binary had
written blocked the `security` CLI on an approval dialog during development.

Because of that, `storage::keyring::set_enabled(false)` turns the credential
store off for a process and leaves only the sidecar. Tests use it so they never
touch a developer's real keychain, and it is the right switch for a headless
machine where the store would prompt or hang.

## BlindBox

Fixture: `blindbox.json`.

Pairwise key schedule, where `low`/`high` are the two normalized peer ids
sorted lexicographically:

```
salt    = SHA256("BLINDBOX-SALT-V1|" || low || "|" || high)
prk     = HKDF-Extract(salt, root_secret)
context = low | high | direction_label | "epoch=<n>" | hex(index as 8 BE)
lookup  = HKDF-Expand(prk, "BLINDBOX_LOOKUP_V1|" || context, 32)
blob    = HKDF-Expand(prk, "BLINDBOX_BLOB_V1|"   || context, 32)
state   = HKDF-Expand(prk, "BLINDBOX_STATE_V1|"  || context, 16)
token   = hex(SHA256(lookup))
```

`direction_label` is `LOW_TO_HIGH` or `HIGH_TO_LOW` depending on whether the
local peer sorts low, so both sides derive the same keys for the same message.
The `context` separator is `|` and `index` appears as the hex of its 8-byte
big-endian encoding.

The group schedule (`BLINDBOX-GROUP-SALT-V2`) additionally binds the normalized
sender id into both salt and context, giving each member a disjoint keyspace on
the shared group root.

Blob plaintext, padded to 256-byte buckets and then SecretBox'd under
`blob_key`:

```
"BLNDBX01" | ver(1)=1 | dir(1) | index(8 BE) | state_tag(16) | frame_len(4 BE) | frame
```

`dir` is 1 for `send`, 2 for `recv`. Struct format is `>8sBBQ16sI`.

Replica protocol is line-oriented over a plain TCP stream (reached either
through SAM `STREAM CONNECT` to a `.b32.i2p` destination or directly on
loopback, default port 19444):

```
PUT <lookup_token> <size> [auth_token]\n   then <size> raw bytes
GET <lookup_token> [auth_token]\n          -> "MISS" or "OK <size>\n" + bytes
```

Responses: `OK`, `EXISTS`, `FULL`, `RATE`, `ERR`, `MISS`. Admin verbs `PING`,
`STATUS`, `STATUS_JSON`, `METRICS`.

## Groups

Fixtures: `groups.json`, `group_wire.json`.

Invite tokens are opaque: `base64url_nopad(wrap_key(32) || SecretBox(JSON))`
with no prefix, where the SecretBox key is
`HKDF-Expand(HKDF-Extract("I2PCHAT-GROUP-INVITE-SEAL", wrap_key), "I2PCHAT-GROUP-INVITE-SEAL|v3", 32)`.
Anyone holding the token can decrypt it; the Ed25519 signature over
`I2PCHAT-GROUP-INVITE-v2|<canonical JSON>` is what binds the roster to the
inviter's TOFU-pinned signing key. Legacy `__I2PCHAT_GROUP_INVITE__:` +
plaintext JSON tokens are still accepted on redeem. Version 1 unsigned invites
are rejected.

When verifying, canonicalization must use the **raw payload strings** for
`created_at` / `expires_at` rather than re-serializing parsed datetimes.

Group transport messages ride the same encrypted stream as 1:1 chat, prefixed
with `__I2PCHAT_GROUP__:` followed by canonical JSON. v1 is per-recipient
(`recipient_id`, `delivery_id`); v3 is a signed broadcast for group-wide
BlindBox delivery, carrying `signer_key` and `signature`. Cap is 512 KiB.

## I2P destinations and SAM

Fixture: `sam.json`.

- I2P base64 uses the alphabet variant with `-` and `~` replacing `+` and `/`.
  File chunks on the wire use **standard** base64; invite tokens use
  **base64url without padding**. Three different alphabets — a classic source
  of subtle bugs.
- A destination's base32 address is
  `base32(SHA256(dest_data))[:52].lower()`, without the `.b32.i2p` suffix.
- In a private destination blob, the certificate length is a big-endian uint16
  at offset 385; the public destination is the first `387 + cert_len` bytes and
  the remainder is private key material. Reject a `cert_len` that would run
  past the blob or leave no private bytes.
- SAM v3 is a plain-text line protocol: `HELLO VERSION MIN=3.0 MAX=3.2`,
  `DEST GENERATE SIGNATURE_TYPE=7` (Ed25519), `NAMING LOOKUP`,
  `SESSION CREATE STYLE=STREAM`, `STREAM CONNECT`, `STREAM ACCEPT`. Commands
  and replies are `\n`-terminated `KEY=VALUE` tokens.
- The session control socket must stay open for the lifetime of the SAM
  session; each `STREAM CONNECT` / `STREAM ACCEPT` uses its own TCP connection.

## Text chunking

Fixture: `text_chunking.json`.

Long messages are split at 4096 **Unicode code points** — not bytes, not
UTF-16 units. Break preference within the window: the last newline, else the
last space, else a hard cut at the limit. A break is only taken at or beyond
`max_chars / 4` to avoid a tiny first chunk. Empty input yields an empty list.
