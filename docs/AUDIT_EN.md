# I2PChat — security audit (technical)

**Document version:** 1.2  
**Date:** 2026-04-02  
**Scope revision:** Aligned with **v1.1.3** codebase — live framing is **vNext-only** (obsolete pre-vNext wire parsing removed); outbound file/inline-image disk I/O and ACK **drain batching** unchanged; scope otherwise (`i2pchat/`, GUI entrypoints, storage, protocol, BlindBox, representative tests). **Point-in-time** review, not a formal penetration test or certification.

**Scope:** Source tree under `i2pchat/`, GUI entrypoints, storage, protocol and BlindBox modules, and representative tests.

**Methodology:** Static review of architecture, crypto usage, network boundaries, file handling, secrets handling, and common vulnerability classes (injection, path traversal, downgrade, deserialization, command execution). No dynamic exploitation was performed.

---

## 1. Product summary

I2PChat is a desktop chat client using **I2P SAM** for streaming connections to peers. It implements a **binary vNext framed protocol**, a **NaCl-based handshake** with ephemeral keys, **authenticated encryption** for payloads, **TOFU-style** signing-key pinning for peers, optional **encrypted local chat history**, and **BlindBox** offline message storage via third-party replicas.

Primary codebase: Python 3, **PyQt6** GUI, **PyNaCl**, internal **`i2pchat.sam`** (SAM), **Pillow** for images.

---

## 2. Threat model and assumptions

**In scope threats**

- Malicious or buggy peer on the wire (protocol abuse, oversized frames, replays, downgrade attempts).
- Malicious BlindBox replica or network observer on I2P paths to replicas (confidentiality/integrity of blobs within stated protocol limits).
- Local attacker with user privileges (reading profile data, tampering with files, clipboard).
- Supply-chain compromise of dependencies or build pipeline.

**Out of scope / explicit limits**

- Compromise of the I2P router or OS kernel, or physical theft of an unlocked machine with no disk encryption.
- Usability attacks (spam, social engineering) except where the client amplifies them.
- Formal verification of cryptographic protocols.

**Trust anchors**

- User trusts the **I2P router** and **SAM** endpoint they configure.
- User trusts **BlindBox replica** operators when using offline delivery.
- **TOFU** on first connect: identity binding is only as strong as the first successful verification the user accepts.

---

## 3. Executive summary

| Area | Assessment |
|------|------------|
| Live transport crypto | **Strong:** modern NaCl primitives, separate MAC key, sequence checks, downgrade blocked after handshake. |
| Framing / DoS limits | **Good:** `max_frame_body`, resync limits; large transfers chunked. **vNext-only** live codec — no alternate on-wire framing parser. |
| File receive path | **Good:** filename sanitization, unique paths, size caps; progress/UI throttling reduces jank (availability). |
| Local history | **Good:** per-file salt, HKDF layering, SecretBox; export uses Argon2id. |
| BlindBox | **Depends on deployment:** line protocol + optional replica tokens; not TLS in the usual sense—trust is layered on I2P/TCP to replicas. |
| GUI content injection | **Low risk for chat bubbles:** painted as plain text; rich text mainly in compose with paste sanitization paths documented in code. |
| Secrets in repo | **No hardcoded API keys** observed; env vars documented for operators. |
| Subprocess use | **Limited:** notification helpers with fixed command lists; no `shell=True` in application code reviewed. |

**Residual risks:** operator misconfiguration (weak BlindBox tokens), plaintext `.dat` when keyring unused, update check fetches remote HTML (trust in URL), and **peer trust UX** (user must confirm TOFU changes deliberately).

---

## 4. Detailed findings

### 4.1 I2P SAM and addressing

- **`i2pchat.sam.protocol`** builders reject dangerous characters in destinations and session IDs (`tests/test_sam_input_validation.py`), reducing command-injection style issues in SAM string building.
- User-supplied peer strings still rely on **correct router behavior**; the app does not escape I2P’s threat model.

**Severity:** informational (defense in depth).

---

### 4.2 Live protocol (vNext) and handshake

- **Framing:** `ProtocolCodec` enforces `MAGIC`, version, allowed types, and maximum body size (`i2pchat/protocol/protocol_codec.py`). The live stream is **vNext only**; there is **no** second parser for obsolete line-oriented frames. Non-`MAGIC` streams fail resync within `resync_limit` or protocol checks.

- **Post-handshake:** Plaintext application frames after encryption are treated as **protocol violation** and lead to disconnect (`receive_loop`).

- **Crypto:** Handshake uses ephemeral X25519, HKDF-derived `k_enc` / `k_mac`, XSalsa20-Poly1305 via SecretBox for payloads, and HMAC over ciphertext/metadata with **monotonic sequence** checks (`i2pchat/crypto.py`, `i2pchat/core/i2p_chat_core.py`). This aligns with common modern practice for channel security.

- **Replay / ordering:** Strict `expected_seq` enforcement; failures emit errors and disconnect.

**Recommendations:** Monitor logs for downgrade and MAC failure spikes.

---

### 4.3 Delivery acknowledgements (ACK)

- Pending ACK tables have **TTL and max size** to limit memory growth (`ACK_TTL_SECONDS`, `ACK_MAX_PENDING`, pruning). Spoofed or duplicate ACKs are filtered with counters (`_record_ack_drop`).

**Severity:** low—mainly availability / state hygiene.

---

### 4.4 BlindBox offline delivery

- Separate **line-oriented replica protocol** from live vNext framing (`docs/PROTOCOL.md`).
- **Root secret** and pending secrets wrapped for storage with profile-derived keys (`_blindbox_encrypt_root_secret` / decrypt paths).
- **Replica auth:** optional per-endpoint tokens and local loopback token (`I2PCHAT_BLINDBOX_LOCAL_TOKEN`, `replica_auth` in JSON). Misconfiguration (empty token on shared network paths) is an **operational** risk, not a silent crypto bypass—the code warns and can refuse unsafe combinations in strict paths.
- **GET size cap:** `max_get_blob_size` defaults tied to blob limits (`BlindBoxClient`).

**Risks:** Replicas see **ciphertext blobs** and metadata dictated by the protocol; traffic is over I2P or TCP depending on endpoint—operators must treat replica hosts as **semi-trusted** infrastructure.

---

### 4.5 Local storage and profiles

- **Chat history:** Encrypted at rest with profile identity-derived keys and per-peer file keys (`i2pchat/storage/chat_history.py`). Wrong key fails closed (empty history after decrypt failure).
- **History export:** Password-based encryption with Argon2id + SecretBox (`i2pchat/storage/history_export.py`). Password strength is **user responsibility**.
- **Profile export/import:** Password-protected archives (`i2pchat/storage/profile_export.py`).
- **Keyring:** Optional storage of private material via `keyring` service `i2pchat` (`KEYRING_SERVICE`). Fallback is **plaintext-sensitive** `.dat`—expected for portability, users should understand OS access controls.
- **Atomic writes:** Used in several paths to reduce torn writes (`atomic_write_*` patterns).

**Risk:** Backup files and `.dat` on disk are **high-value assets**; filesystem permissions (Unix 0700 on data dir) help but do not stop malware running as the user.

---

### 4.6 File and image transfer

- **Filenames:** `sanitize_filename` strips path components and unsafe characters; `allocate_unique_filename` avoids overwrite (`i2pchat/core/i2p_chat_core.py`).
- **Sizes:** `MAX_FILE_SIZE`, `MAX_IMAGE_SIZE`, per-chunk validation against declared remainder, base64 decode with bounds checks.
- **Inline images:** Magic-byte format detection; PIL validation after receive (worker thread in recent versions to keep UI responsive). Malformed images fail closed with user-visible errors.
- **Outbound disk I/O (v1.1.2):** synchronous `read()` for **file** and **inline (`G`)** send chunks runs in a **thread pool** (`asyncio.to_thread`) so the asyncio event loop is not blocked on large reads—**availability** / responsiveness on the same thread as Qt+qasync.
- **ACK signalling during outbound transfer (v1.1.2):** automatic **MSG_ACK** / **IMG_ACK** **`S`** frames use **`_write_signal_frame_maybe_soft_drain`**: while **`_file_transfer_active`**, **`drain()`** runs every **`I2PCHAT_MSG_ACK_DRAIN_EVERY`** frames (default **16**, clamped), reducing syscall/`drain` churn. Wrong tuning could delay ACK visibility to the peer slightly; defaults are conservative.

**Risk:** Quadratic hash/read on very large local sends is a **performance** issue more than security; network still bounded by caps.

---

### 4.7 GUI and desktop integration

- **Chat rendering:** Message bubbles in the main list are drawn with **QPainter** using string text for standard message kinds—not arbitrary HTML from peers—reducing **XSS-in-chat** risk compared to a full HTML view.
- **Compose field:** Rich text enabled for emoji rendering; paste path strips tags to plain text before protocol send (`MessageInputEdit` / related helpers)—reduces accidental HTML/XML paste issues; peer still receives what user sends as text after that pipeline.
- **Open URL / folder:** `QDesktopServices.openUrl` with `fromLocalFile` for controlled paths (downloads, app dirs). User-triggered actions.
- **Subprocess:** Linux notification sound uses `subprocess.Popen` with **argv lists** from `shutil.which` paths—no shell interpolation (`i2pchat/gui/main_qt.py`).
- **Clipboard:** Standard copy of message text; sensitive content exposure is a **user workflow** concern.

---

### 4.8 Updates and external HTTP

- **Check for updates** fetches a **releases HTML page**, parses version strings, does **not** auto-download binaries (`docs/MANUAL_EN.md`).  
- **Risk:** If `I2PCHAT_RELEASES_PAGE_URL` points to a malicious origin, HTML parsing could theoretically have parser bugs—treat as **trusted configuration**. No raw `eval` on fetched content identified in core paths.

---

### 4.9 Environment variables and debug flags

Several flags affect security or privacy posture:

| Variable | Effect |
|----------|--------|
| `I2PCHAT_MSG_ACK_DRAIN_EVERY` | During **outgoing** file/image send: drain after every **N** automatic **MSG_ACK**/**IMG_ACK** `S` frames (default **16**, **1–256**). Affects **responsiveness vs backpressure**, not crypto. |
| `I2PCHAT_BLINDBOX_*` | Replica lists, tokens, quorum—**misconfiguration** leaks or weakens offline security. |
| `I2PCHAT_FILE_XFER_DEBUG` | Logs timing—**may leak transfer patterns** in logs. |
| `I2PCHAT_QT_FILE_EVENT_NOOP` | Disables file progress UI—diagnostic only. |

Operators should avoid debug flags in production unless logs are protected.

---

### 4.10 Dependencies and build

- Core crypto in **PyNaCl** (well-audited library).  
- **PyQt6**, **Pillow**—follow upstream CVE advisories.  
- No `pickle` on untrusted network data identified in reviewed chat paths.  
- PyInstaller / build scripts: follow **reproducible build** and **signature** practices (`I2PCHAT_REQUIRE_GPG` mentioned in README for releases).

---

## 5. Prioritized recommendations

1. **High (operational):** Set **BlindBox tokens** when using TCP/loopback replicas; use **keyring** where OS supports it.
2. **Medium:** Periodically run `pip audit` / OS package audits on dependency pins used in releases.
3. **Medium:** Ensure **release binaries** are signed and hashes published (project already describes this).
4. **Low:** Consider centralizing a **security.txt** or “Reporting vulnerabilities” section in README linking to this audit and contact.
5. **Low:** For update-check HTML parsing, keep dependencies minimal and add regression tests if the parser grows.

---

## 6. Conclusion

I2PChat implements a **credibly designed** stack for encrypted peer chat over I2P: strong **vNext-only** framing rules, modern AEAD and MAC, explicit downgrade handling, and encrypted local history. Remaining risk is dominated by **deployment and trust choices** (TOFU confirmations, BlindBox operators, physical access to profile files), not by an obvious single critical remote code execution flaw in the reviewed paths.

This document should be **updated** after major protocol, crypto, BlindBox, or framing changes.

---

## 7. Key references (code)

| Topic | Location |
|-------|----------|
| Core session / receive loop | `i2pchat/core/i2p_chat_core.py` |
| Crypto primitives | `i2pchat/crypto.py` |
| Framing codec | `i2pchat/protocol/protocol_codec.py` |
| Chat history encryption | `i2pchat/storage/chat_history.py` |
| History export crypto | `i2pchat/storage/history_export.py` |
| Profile backup | `i2pchat/storage/profile_export.py` |
| BlindBox client | `i2pchat/blindbox/blindbox_client.py` |
| Protocol specification | `docs/PROTOCOL.md` |
| SAM input validation tests | `tests/test_sam_input_validation.py` |
| Protocol hardening tests | `tests/test_protocol_hardening.py` |
| vNext-only framing tests | `tests/test_protocol_framing_vnext.py` |
