# I2PChat security audit — internal SAM layer (`i2pchat.sam`)

**Context:** Transition from PyPI / vendored **`i2plib`** to an in-repository implementation under **`i2pchat/sam/`** (see `docs/SAM_INTERNAL_BACKEND_PLAN.md`, `docs/releases/RELEASE_1.2.4.md`).

**Audit date:** 2026-04-07 (static review of current tree; no penetration test).

**Remediation (2026-04-07):** `SESSION CREATE` **options** dict entries are validated per key/value (including raw `\r`/`\n`/`\x00` before strip). BlindBox **`STREAM CONNECT`** uses **`build_stream_connect`**. BlindBox **PUT/GET** keys reject whitespace and line breaks. Covered by `tests/test_sam_input_validation.py` and `tests/test_blindbox_client.py`.

**Scope:** SAM control-plane code (`protocol.py`, `client.py`, `backend.py`, `destination.py`, `errors.py`), integration in `i2pchat/core/i2p_chat_core.py` and `i2pchat/blindbox/blindbox_client.py`, and adjacent trust boundaries (BlindBox wire commands, bundled router). Application-layer crypto (NaCl, HKDF, vNext framing) is referenced only where it touches SAM assumptions.

---

## 1. Executive summary

The internal SAM stack **replicates the previous hardening pattern** from the old `i2plib` path: SAM command tokens are validated for newline/whitespace/control characters, and the main chat path uses **`build_*` helpers** from `i2pchat.sam.protocol`. Removing the external dependency **shrinks third-party supply-chain risk** but **concentrates protocol correctness in this repository**; any regression in parsing or validation is now fully owned here.

**No critical remote code execution issues** were identified in the reviewed SAM code. Residual risks are dominated by **the inherent SAM trust model** (cleartext TCP to the router, typically loopback) and **operational configuration** (pointing SAM at an untrusted host).

---

## 2. Threat model (SAM-specific)

| Actor | Capability | Relevant mitigations / gaps |
|--------|------------|------------------------------|
| Local process on same machine | Connect to SAM port, race user’s router | OS user isolation; firewall; binding SAM to loopback |
| Malicious / compromised I2P router | Forge SAM replies, observe identities, drop traffic | User chooses router; TLS is not part of standard SAM |
| Network peer over I2P | Chat protocol attacks (out of SAM scope) | vNext framing + NaCl in `i2pchat.core` / `i2pchat.crypto` |
| Malicious Blind Box replica | Return bad blobs; DoS | Quorum, size caps, crypto over blobs |

---

## 3. What improved with the migration

- **Supply chain:** `i2plib` is not declared in `pyproject.toml`; policy tests enforce this (`tests/test_audit_remediation.py`). SAM behavior is auditable in-tree.
- **Injection-style SAM commands:** `i2pchat/sam/protocol.py` validates session IDs, styles, ports, HELLO versions, naming names, stream destinations, and boolean flags before formatting lines.
- **Logging hygiene:** `_redact_sam_reply` masks sensitive SAM keys (`PRIV`, `PRIVATE`, `DESTINATION`, `SIGNING_PRIVATE_KEY`) in `protocol.py`.
- **CI:** `.github/workflows/security-audit.yml` runs **`uv export` + `pip-audit`** on locked runtime and build dependency sets.

---

## 4. Findings

Severity uses a practical scale: **Critical / High / Medium / Low / Informational**.

### 4.1 [Medium] `SESSION CREATE` option map — keys and values not token-validated

**Location:** `i2pchat/sam/protocol.py` — `build_session_create(..., options=dict)`.

**Issue:** Unlike scalar SAM fields (session ID, style, etc.), each **option key and value** is interpolated as `key=value` without the same `_validate_sam_token` rules. The **joined string** is passed through `_validate_sam_options`, which blocks `\r`, `\n`, and `\x00` but **does not** prevent spaces inside a value, embedded `=`, or unusual keys.

**Current risk:** Call sites in-tree (`i2p_chat_core.py`, default `BlindBoxClient.sam_options`) use **fixed** keys/values, so exploitation is **not exposed in default flows**. Risk rises if **future or plugin code** passes partially user-controlled entries into `create_session(..., options=...)`.

**Recommendation:** Validate each option key/value with the same rules as other SAM tokens (or a documented subset), or accept only a whitelist of I2CP option names.

---

### 4.2 [Medium] BlindBox `STREAM CONNECT` built with a manual f-string

**Location:** `i2pchat/blindbox/blindbox_client.py` — `_open_sam_stream_to`.

**Issue:** The line is assembled manually instead of `sam_protocol.build_stream_connect(...)`. `_validate_sam_destination` and `_active_sam_id` generation mitigate injection **today**, but **two code paths** for the same SAM verb increase the chance of **drift** (one path updated, the other not).

**Recommendation:** Route through `build_stream_connect(session_id, destination, silent="false")` so all validation stays centralized.

---

### 4.3 [Low] `DEST GENERATE` — `SIGNATURE_TYPE` not whitelisted

**Location:** `i2pchat/sam/protocol.py` — `build_dest_generate`.

**Issue:** Any `int` is formatted; invalid types are rejected only by the router.

**Recommendation:** Optional allowlist (e.g. Ed25519 = 7 for defaults) to fail fast and match app policy.

---

### 4.4 [Low] Unbounded `readline()` on SAM and BlindBox TCP

**Location:** `i2pchat/sam/client.py`, `open_stream_*`, BlindBox helpers.

**Issue:** A peer that never sends `\n` can cause memory growth on the reader buffer (DoS / resource exhaustion) unless the transport is trusted.

**Recommendation:** Document as accepted for **loopback SAM**; for non-loopback or hardened builds, consider a max-bytes-per-line read wrapper.

---

### 4.5 [Low / defense in depth] BlindBox `PUT`/`GET` keys

**Location:** `i2pchat/blindbox/blindbox_client.py` — `_put_to_blind_box`, `_get_from_blind_box`.

**Issue:** Keys are interpolated into text commands. **Core** uses **hex** `lookup_token` from `derive_blindbox_message_keys()` (safe charset). Direct use of `BlindBoxClient` with arbitrary strings could break the line protocol.

**Recommendation:** Reject keys containing whitespace, `\r`, `\n`, `\x00`, or other delimiters if the API should remain safe for arbitrary callers.

---

### 4.6 [Informational] Relaxed `expect_ok` for i2pd quirks

**Location:** `i2pchat/sam/protocol.py` — `expect_ok`.

**Issue:** Success without `RESULT=OK` is inferred for certain `DEST REPLY` and `SESSION STATUS` shapes (documented for i2pd). A **malicious router** could theoretically influence error/success classification; in practice the app already **fully trusts** the router for SAM.

**Recommendation:** Keep as compatibility behavior; no change required unless moving to an authenticated control channel.

---

### 4.7 [Informational] `Destination(..., path=...)`

**Location:** `i2pchat/sam/destination.py`.

**Issue:** Loading identity material from disk is powerful; callers must not pass **user-controlled** paths.

**Recommendation:** Grep/audit call sites when adding features; prefer explicit bytes/str keys from keyring.

---

### 4.8 [Informational] Bundled `i2pd` subprocess

**Location:** `i2pchat/router/bundled_i2pd.py`.

**Observation:** Launch uses `asyncio.create_subprocess_exec` with argument lists (no `shell=True` in reviewed path). Integrity of the **binary** depends on packaging and user environment.

**Recommendation:** Continue pinning/checksumming release artifacts (already reflected in build scripts and CI policy tests).

---

## 5. Residual risks (not bugs per se)

1. **Cleartext SAM on TCP** — expected by I2P SAM; secure deployment assumes **loopback** or controlled network.
2. **Router as TCB** — any I2P client gives the router visibility into tunnels and identities.
3. **Regression ownership** — protocol edge cases once handled upstream in `i2plib` must be covered by **`i2pchat.sam` tests** (`tests/test_sam_*.py`, `tests/test_blindbox_client.py`).

---

## 6. Recommended next steps

1. Add **unit tests** for malformed multi-line SAM replies and partial reads (**invalid `options` dict** tests are in place).
2. ~~**Unify** BlindBox `STREAM CONNECT` on `sam_protocol.build_stream_connect`.~~ **Done.**
3. ~~Tighten **`options`** validation and **BlindBox key** character set.~~ **Done** (see Remediation above).
4. Keep **scheduled `pip-audit`** and **secret scanning** workflows green on `main`.

---

## 7. Files reviewed (primary)

- `i2pchat/sam/protocol.py`, `client.py`, `backend.py`, `destination.py`, `errors.py`
- `i2pchat/core/i2p_chat_core.py` (SAM session bootstrap and stream helpers)
- `i2pchat/blindbox/blindbox_client.py`
- `i2pchat/blindbox/blindbox_key_schedule.py`
- `tests/test_sam_input_validation.py`, `tests/test_sam_protocol.py`, `tests/test_audit_remediation.py`
- `.github/workflows/security-audit.yml`

---

*This document is a point-in-time assessment and does not replace periodic dependency scanning, release signing verification, or targeted penetration testing.*
