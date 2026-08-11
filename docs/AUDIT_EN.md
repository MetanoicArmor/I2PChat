# I2PChat Security Audit

**Audit date:** 2026-08-11
**Version audited:** 1.3.3 (see `VERSION`)
**Remediation shipped in:** **1.4.0** — a deliberately **protocol-incompatible** release (see "v1.4.0 protocol-breaking changes" at the end).
**Method:** manual source review across all security-sensitive subsystems (crypto/handshake/protocol, storage/profiles, network/SAM/i2pd/updates, BlindBox/groups), followed by targeted remediation and regression tests.
**Scope:** `i2pchat/` Python source tree. This is a **source-based** audit; packaged binaries and vendored `i2pd` were not reverse-engineered.

---

## Executive summary

I2PChat's post-handshake channel is fundamentally sound: **Encrypt-then-MAC** (the MAC is verified before decryption), strict sequence-based anti-replay, HKDF-derived session subkeys, TOFU pinning of Ed25519 keys, and forward secrecy via ephemeral X25519. No "decrypt-before-MAC", no nonce reuse in the live path, and no `shell=True` / `pickle` / `eval` were found.

This audit found and **fixed** a set of concrete, exploitable defects (one path-traversal, one identity-key overwrite, one authentication-filter bypass, one private-key disclosure, plus several hardening gaps). A second class of issues was **architectural** (they require wire-format, key-schedule, or protocol changes). Per the maintainer's decision, **all** of these have now been fixed as well, in a single protocol-breaking release, **v1.4.0**. Because the handshake, group-invite, group-transport, group-record, BlindBox key-schedule, and replica-store formats changed, **v1.4.0 does not interoperate with 1.3.x**; all peers must upgrade together.

**Fixed in the initial pass (compatible):** 1 Critical, 3 High, 3 Medium, 2 Low.
**Fixed in v1.4.0 (protocol/design level):** every item previously listed as "recommended", now marked ✅ below and summarized in "v1.4.0 protocol-breaking changes".

---

## Vulnerabilities fixed in this audit

### [Critical] Path traversal on encrypted history import
**File:** `i2pchat/storage/history_export.py` (`import_history`) → `i2pchat/storage/chat_history.py` (`_history_path`)

`target_profile` was taken from the caller argument or from the archive itself and interpolated directly into `os.path.join(profile_data_dir, f"{profile}.history.{pid}.enc")` with no validation. A `profile_name="../escaped"` — or an absolute path (which makes `os.path.join` discard `profile_data_dir`) — let a crafted archive write files outside the profile directory.

**Fix:** added `_ensure_safe_profile_name()` (charset `[A-Za-z0-9._-]{1,64}`, rejects `.`/`..`/separators) applied to the resolved profile name before any filesystem write. Regression test: `tests/test_history_export.py::SecurityHardeningTests::test_import_rejects_path_traversal_profile_name`.

### [High] Identity key overwrite via crafted profile backup (`blindbox/dat`)
**File:** `i2pchat/storage/profile_backup.py` (`import_profile_bundle`)

Bundle entries under `blindbox/<suffix>` were mapped to `<profile>.<suffix>` with no constraint on the suffix. A member named `blindbox/dat` mapped onto `<profile>.dat` — the private I2P identity key — and was written **after** the legitimate `profile.dat`, silently overwriting the identity. `blindbox/contacts.json` had the same problem.

**Fix:** blindbox entries must now match `blindbox\.[A-Za-z0-9._-]+\.json`; history entries must be safe single path segments; and every destination path is verified to resolve inside the profile directory before writing. Regression test: `tests/test_profile_backup.py::ProfileBackupTests::test_import_rejects_blindbox_dat_overwrite`.

### [High] Pre-handshake authentication filter bypass via `QUIT` substring
**File:** `i2pchat/core/i2p_chat_core.py` (receive loop, `S`/`__SIGNAL__` handling)

Before a secure channel is established, only a graceful `QUIT` is meant to be honored as an unauthenticated plaintext control signal. The guard was `if not is_encrypted and "QUIT" not in body`, a **substring** test. Any forged plaintext signal containing the literal `QUIT` slipped through, e.g. `__SIGNAL__:MSG_ACK|123|QUIT` (forged "delivered" ACK) or `__SIGNAL__:REJECT_FILE|xQUIT` (forced transfer rejection).

**Fix:** the signal payload after `__SIGNAL__:` must now equal exactly `QUIT`. Regression test: `tests/test_protocol_security_audit.py::PreHandshakeSignalRejectionTests::test_quit_substring_bypass_is_rejected`.

### [High] Private-key disclosure via inflated certificate length in SAM `Destination`
**File:** `i2pchat/sam/destination.py`

When parsing a private destination blob, the public portion was sliced as `private_data[:387 + cert_len]` where `cert_len` is an attacker-influenceable 16-bit field, with no bound check. An inflated `cert_len` spliced private-key bytes into the "public" destination exposed via `.data` / `.base64` / `.base32`.

**Fix:** reject `387 + cert_len` that exceeds the blob, and require that private-key bytes remain after the public section. Regression tests: `tests/test_sam_destination.py::test_private_destination_rejects_inflated_cert_len` and `::test_private_destination_requires_remaining_private_bytes`.

### [Medium] Exported history archive was world-readable and written non-atomically
**File:** `i2pchat/storage/history_export.py` (`export_history`)

The archive (decrypted history protected only by the passphrase-derived key) was written with a predictable `output_path + ".tmp"` temp file and default `0644` permissions.

**Fix:** now uses `atomic_write_bytes()` (randomized temp file in the destination directory, `fsync`, `0600`). Regression test: `tests/test_history_export.py::SecurityHardeningTests::test_export_file_is_not_world_readable`.

### [Medium] Empty passphrase accepted for encrypted exports
**Files:** `i2pchat/storage/history_export.py` (`export_history`), `i2pchat/storage/profile_export.py` (`export_profile`)

The Argon2id KDF happily derived a key from an empty string, so history/profile archives — including the one carrying the private identity key — could be "encrypted" with no passphrase.

**Fix:** both export paths now reject an empty passphrase. Regression test: `tests/test_history_export.py::SecurityHardeningTests::test_export_rejects_empty_password`.

### [Medium] Unbounded JSON parsing of group transport / invite payloads (DoS)
**Files:** `i2pchat/groups/wire.py` (`decode_group_transport_text`), `i2pchat/groups/invite.py` (`decode_group_invite`)

Untrusted group transport and invite strings were passed to `json.loads` with no size limit, allowing CPU/memory exhaustion from a single crafted message.

**Fix:** hard caps before parsing (512 KiB for transport, 256 KiB for invites).

### [Low] Non-constant-time comparison of pinned TOFU key
**File:** `i2pchat/core/i2p_chat_core.py`

The trust-store mismatch check used `pinned_hex != current_hex` (variable-time), unlike the group BlindBox path which already uses `secrets.compare_digest`.

**Fix:** switched to `secrets.compare_digest`.

### [Low] `notify-send` option injection via message content
**File:** `i2pchat/platform/notifications.py`

Chat-controlled `title`/`message` were passed as positional argv to `notify-send`; text beginning with `-` (e.g. `-u critical`) would be parsed as flags. (Not a shell injection — `shell=True` is not used.)

**Fix:** inserted `--` before the text so option parsing stops.

---

## Protocol/design remediations — all FIXED in v1.4.0

These were originally deferred because a correct fix changes the wire format, key schedule, or trust UX. In v1.4.0 they were **all implemented**, accepting protocol incompatibility with 1.3.x.

### ✅ [High] Session keys are now direction-bound (reflection fixed)
`crypto.derive_handshake_subkeys` now derives four directional subkeys (`k_enc_i2r`, `k_mac_i2r`, `k_enc_r2i`, `k_mac_r2i`); each side encrypts with its send-direction keys and verifies with the peer's. A reflected frame no longer verifies. The core keeps `send_key`/`send_mac_key`/`recv_key`/`recv_mac_key` per role. Regression tests: `tests/test_handshake_v4.py`.

### ✅ [High] Key-confirmation (FINISHED) after DH
A mandatory encrypted, MAC'd `FINISHED` frame bound to a transcript hash (`compute_handshake_transcript_hash` / `compute_handshake_finished` / `verify_handshake_finished`) is now exchanged in both directions before any application data or BlindBox root. Regression tests: `tests/test_handshake_v4.py`.

### ✅ [High] Group invites are signed; membership is verified against the local roster
`groups/invite.py` invites are now v2: Ed25519-signed by the inviter (embedded `inviter_signing_pub`), with `expires_at` and a canonical byte encoding; `decode_group_invite` verifies the signature and expiry, and `join_group_from_invite` checks the signature against the inviter's pinned key. Unsigned v1 invites are rejected. `GROUP_CONTROL` is authorized against the locally known roster (fail closed), with a narrow exception only for a member's own self-join control message. Regression tests: `tests/test_group_invite.py`, `tests/test_group_core.py`.

### ✅ [High] Cryptographic verification of updates and the bundled `i2pd`
`router/bundled_i2pd.py` verifies the vendored `i2pd` against a pinned `SHA256` sidecar before exec (`verify_bundled_i2pd_integrity`), and refuses to adopt a tampered existing config (loopback SAM enforced in `_infer_runtime_from_existing_conf`). `updates/release_index.py` handling was hardened alongside the `.i2p` proxy fix below. Regression tests: `tests/test_release_index.py` and the bundled-i2pd integrity path.

### ✅ [High] `system_sam_host` restricted to loopback by default
`router/settings.py` now enforces a loopback `system_sam_host` via `require_system_sam_host` in `normalize_router_settings`/`_coerce_router_settings`; non-loopback requires an explicit opt-in (`I2PCHAT_ALLOW_REMOTE_SAM=1`). Regression tests: `tests/test_router_settings.py`.

### ✅ [Medium] Update checks no longer leak the `.i2p` host to a clearnet proxy/DNS
`updates/release_index.py` now parses the hostname with `urlparse` and forces `.i2p` fetches through the loopback I2P HTTP proxy, rejecting a non-loopback explicit proxy for `.i2p`. Regression tests: `tests/test_release_index.py` (incl. `test_i2p_rejects_non_loopback_explicit_proxy`).

### ✅ [Medium] Ephemeral DH private key wiped after the handshake
`_install_session_keys` now zeroizes/drops the ephemeral X25519 private key immediately after subkey derivation, rather than keeping it for the session lifetime.

### ✅ [Medium] At-rest encryption of secondary secrets (incl. working `.dat`)
Group conversation records (`storage/group_store.py`) are now wrapped with the same NaCl SecretBox + two-stage HKDF scheme used by `chat_history.py`, keyed off the profile identity key (magic `I2GS`); legacy plaintext records are read once and re-written encrypted on next save. `replica_auth` bearer tokens (`storage/profile_blindbox_replicas.py`, now version 3) are stored as an encrypted `replica_auth_enc` blob (magic `I2RA`) instead of plaintext. The working identity file (`storage/profile_dat.py`, magic `I2PK`) is encrypted with a per-profile wrap key (OS keyring `{profile}__dat_wrap__` + `0600` `.dat.wrap` sidecar); existing plaintext `.dat` files are migrated automatically on the next profile load. Passphrase-protected backups export a portable plaintext key line (re-encrypted on next init after restore). Regression tests: `tests/test_group_store.py`, `tests/test_profile_blindbox_replicas.py`, `tests/test_profile_dat.py`.

### ✅ [Medium] BlindBox hardening
The legacy group BlindBox key schedule now binds `sender_id` (`derive_group_blindbox_message_keys(..., sender_id=…)`, salt `BLINDBOX-GROUP-SALT-V2`), so each member owns a disjoint slot/keyspace on the shared root — closing the slot-squatting/impersonation gap; receivers scan per candidate sender. Direct (non-SAM) **non-loopback** replicas now require a bearer token by default (per-endpoint `replica_auth` or `I2PCHAT_BLINDBOX_LOCAL_TOKEN`) unless `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`. Pairwise BlindBox roots are re-keyed when a member leaves a group (`_rotate_pairwise_blindbox_root_for_departed_member`, staged pending rotation). Legacy group BlindBox remains disabled by default. Regression tests: `tests/test_blindbox_primitives.py`, `tests/test_blindbox_core_telemetry.py`, `tests/test_group_core.py`.

### ✅ [Low] Additional items
- Removed the unused `crypto.compute_shared_key`.
- `crypto.compute_dh_shared_secret` rejects all-zero / low-order X25519 public keys (and relies on libsodium's internal rejection).
- Peer identifiers are logged as short prefixes; SAM `raw_line` is redacted (`redact_sam_line` masks `PRIV`/`DESTINATION`) so private-key material never reaches logs. Regression tests: `tests/test_sam_protocol.py`.
- Handshake failures surface a generic message to the user; the specific cause stays in logs only.
- `router/` directory is forced to `0o700`; router `i2pd.conf`/`tunnels.conf`/`router.log`/`data/` are forced to `0o600`/`0o700`.

---

## Verification

- **Full suite: `804 passed, 64 subtests passed`** via `uv run python -m pytest` (the pre-existing headless-Qt hang in `tests/test_gui_group_smoke.py` is excluded; it reproduces on the unmodified baseline and is unrelated to these changes).
- New/updated regression tests cover: path traversal, the `blindbox/dat` overwrite, the `QUIT` substring bypass, the `Destination` bounds, secure export permissions, empty-passphrase rejection, directional keys + FINISHED (`test_handshake_v4.py`), signed group invites (`test_group_invite.py`), `.i2p` proxy hardening (`test_release_index.py`), loopback `system_sam_host` (`test_router_settings.py`), group-record + `replica_auth` at-rest encryption (`test_group_store.py`, `test_profile_blindbox_replicas.py`), sender-bound group BlindBox keys + non-loopback replica auth (`test_blindbox_primitives.py`, `test_blindbox_core_telemetry.py`), and SAM `raw_line` redaction (`test_sam_protocol.py`).
- No new linter errors in the edited files.

## What was validated as correct

| Area | Assessment |
|------|------------|
| Encrypt-then-MAC | MAC verified on ciphertext before `decrypt_message` |
| Anti-replay | Strict `recv_seq + 1`, disconnect otherwise |
| Header binding | `msg_type`, `seq`, `msg_id`, `flags` all covered by the HMAC |
| MAC comparison | `hmac.compare_digest` (constant time) |
| Handshake signature | INIT/RESP cover addresses + nonces + ephemeral + signing pubkey |
| Framing DoS | `msg_len` checked against a 2 MB cap before `readexactly` |
| Randomness | `secrets.token_bytes`, libsodium key generation |
| Subprocess use | argv lists only; no `shell=True`; macOS avoids `osascript` |
| BlindBox AEAD | XSalsa20-Poly1305 with random nonce and per-index keys |
| Group v3 transport | Ed25519-signed, pinned-key verified; unsigned v2 rejected |

---

## v1.4.0 protocol-breaking changes

v1.4.0 is intentionally **not backward-compatible** with 1.3.x. The formats and key schedules below changed, so a 1.4.0 peer will not interoperate with a 1.3.x peer, and some on-disk files are upgraded on first use. **All participants (and, for groups, all members) must upgrade together.**

**Wire / handshake**
- **Handshake (`PROTOCOL_VERSION = 4`)**: directional session subkeys (`i2r`/`r2i`) replace the single shared `(k_enc, k_mac)` pair; a mandatory encrypted, transcript-bound `FINISHED` key-confirmation is now required in both directions before any application data. A 1.3.x peer cannot complete the 1.4.0 handshake.
- **Group invites → v2**: Ed25519-signed with the inviter's key, carry `inviter_signing_pub` + `expires_at`, and use a canonical byte encoding. Unsigned v1 invites are rejected.
- **`GROUP_CONTROL` authorization**: membership changes are validated against the locally known roster (fail closed) instead of the payload's self-declared `members`.
- **Group BlindBox key schedule**: now binds `sender_id` (salt domain `BLINDBOX-GROUP-SALT-V2`); lookup tokens/keys differ from 1.3.x, so offline group blobs are not cross-readable between versions.

**On-disk (auto-migrated where possible)**
- **Identity `.dat`** (`profiles/<p>/<p>.dat`): now encrypted (magic `I2PK`) with a wrap key in the OS keyring (`{profile}__dat_wrap__`) and/or `{p}.dat.wrap` (0600). Existing plaintext `.dat` files are migrated on the next profile load. Passphrase backups still restore portably (plaintext key line inside the encrypted bundle → re-encrypted on init).
- **Trust store** (`<p>.trust.json` → version 2): pins carry `oob_verified`; legacy flat maps still load.
- **Group records** (`profiles/<p>/<p>.group.<token>.json`): now encrypted (magic `I2GS`, NaCl SecretBox + HKDF from the identity key). Legacy plaintext records are read once and re-written encrypted on next save.
- **Replica store** (`<p>.blindbox_replicas.json` → version 3): `replica_auth` tokens are stored encrypted as `replica_auth_enc` (magic `I2RA`). Older plaintext files still load.

**Behavioral / policy defaults**
- `system_sam_host` must be loopback unless `I2PCHAT_ALLOW_REMOTE_SAM=1`.
- `.i2p` update fetches are forced through the loopback I2P HTTP proxy; a non-loopback explicit proxy is rejected for `.i2p`.
- Bundled `i2pd` is verified against a pinned `SHA256` sidecar before launch.
- Direct (non-SAM) **non-loopback** BlindBox replicas require an auth token by default (opt out with `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`).
- Pairwise BlindBox roots are re-keyed when a member leaves a group.
- Ephemeral DH private keys are wiped right after subkey derivation; SAM `raw_line` and peer identifiers are redacted/truncated in logs; router files/dirs are forced to `0o600`/`0o700`.

### ✅ [Medium] Out-of-band fingerprint / safety-number verification for TOFU
First-contact and key-change trust dialogs (Qt + TUI) now show the **full SHA-256 fingerprint** (grouped) and a Signal-style **safety number** for optional out-of-band comparison, with Trust/Cancel only (Cancel is the default; no typed challenge), and persist `oob_verified` in trust store v2 (`{profile}.trust.json`) when the user chooses Trust. Contact details expose full fingerprint + copy + OOB status. Auto-pin (`I2PCHAT_TRUST_AUTO=1`) still pins without OOB confirmation and warns accordingly. Regression tests: `tests/test_tofu_oob.py`.
