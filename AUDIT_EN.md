# Security Audit Report: I2PChat

Audit date: 2026-03-28  
Repository state: `02bbbd9`  
Mode: full audit (protocol + cryptography + local persistence + UI + CI/release + supply chain)

## Executive Summary

This audit reviewed the current repository state after the recent history-encryption and security-hardening changes.

Confirmed findings:
- Critical: 0
- High: 0
- Medium: 2
- Low: 4

Overall assessment:
- The protocol layer is in good shape: signed handshake, TOFU pinning for persistent profiles, strict sequence/HMAC validation, downgrade handling, and the recent strict file-transfer end check are all present.
- The new encrypted chat history feature is implemented soundly at rest: HKDF-derived keys, per-peer separation, NaCl SecretBox, atomic writes, and correct ON/OFF guards.
- Remaining issues are mostly edge-case integrity gaps and operational hardening gaps, not a fundamental break of the secure-channel design.

## Scope and Methodology

Reviewed components:
- Protocol/runtime/crypto: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline subsystem: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI/local state: `main_qt.py`, `chat_history.py`, `notifications.py`
- Build/release/CI: `.github/workflows/*`, `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, dependency lockfiles

Method:
- static trust-boundary review
- protocol and cryptographic control verification
- targeted runtime regression checks
- release/supply-chain policy review

Executed checks:
- `python3 -m unittest tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_asyncio_regression tests.test_chat_history tests.test_history_ui_guards tests.test_audit_remediation -v`
  - Result: `OK (86 tests)`
- Encrypted-history smoke check:
  - saved history file uses `I2CH` magic
  - plaintext chat message was not present in the stored ciphertext file

## Threat Model Summary

Adversaries considered:
- malicious remote I2P peer
- active network/path manipulator
- local unprivileged process on the same host
- supply-chain / release-distribution attacker

Well-mitigated classes:
- message tampering after handshake
- replay/reorder within the framed channel
- plaintext downgrade after secure channel establishment
- out-of-directory profile-path abuse

Residual classes:
- TOFU first-contact risk by design
- local-host trust assumptions for local BlindBox modes
- release authenticity depending on operator/user verification discipline

## Findings

### [MEDIUM] A-01: Inline image transfer still lacks strict final size equality check

Affected:
- `i2p_chat_core.py` -> `receive_loop`, branch `msg_type == "G"`

Issue:
- Regular file transfers now reject completion unless `received_size == expected_size` before `FILE_ACK`.
- Inline image transfers do not apply the same final integrity rule. On `__IMG_END__`, the code validates size only against `MAX_IMAGE_SIZE`, validates image format, and then emits success with `received=expected_size` and sends `IMG_ACK`.
- A sender can therefore stop early and still get a success path if the truncated payload remains a decodable image.

Impact:
- A malicious authenticated peer can cause truncated inline images to be accepted as complete, producing an integrity mismatch between declared and actual content.

Recommendation:
1. Before saving or ACKing, require `len(self.inline_image_buffer) == expected_size`.
2. On mismatch, discard the image, emit an error/failure event, and do not send `IMG_ACK`.
3. Add a regression test mirroring `test_file_end_without_full_payload_is_rejected`.

---

### [MEDIUM] A-02: Release signing exists, but signed artifacts are not enforced by default

Affected:
- `build-linux.sh`
- `build-macos.sh`
- `build-windows.ps1`
- `.github/workflows/security-audit.yml`

Issue:
- Release scripts can still produce unsigned output when `gpg` is missing or when `I2PCHAT_SKIP_GPG_SIGN=1` is set.
- The CI release-policy job checks that signing-related tokens exist in scripts, but it does not verify that official release artifacts are always detached-signed, platform-signed, or notarized.

Impact:
- Runtime protocol security remains intact, but distribution authenticity still depends on manual operator discipline and user-side verification.

Recommendation:
1. Make detached signing mandatory in official release automation.
2. Add platform-native signing/notarization where applicable.
3. Publish a strict verification procedure for end users and release maintainers.

---

### [LOW] A-03: History filenames use a truncated 64-bit peer hash

Affected:
- `chat_history.py` -> `_safe_peer_id`, `_history_path`

Issue:
- History files are keyed by the first 16 hex characters of SHA-256 of the normalized peer address.
- This is usually fine, but it is still a truncated 64-bit identifier.

Impact:
- A collision is unlikely in normal use, but in theory two different peers could map to the same history filename and cause mixing or overwrite of local history.

Recommendation:
1. Use a longer identifier, preferably full SHA-256 hex or a longer prefix.

---

### [LOW] A-04: Decrypted history does not verify embedded `peer` metadata against requested peer

Affected:
- `chat_history.py` -> `load_history`, `_json_to_entries`

Issue:
- The encryption key is derived from the requested peer address, which is good.
- After decryption, the stored JSON field `peer` is parsed but ignored.

Impact:
- This is only relevant to a local attacker who can already tamper with profile files and operate within the same key context, but it weakens defense in depth for local history integrity.

Recommendation:
1. After decrypting, compare stored `peer` with the normalized requested peer.
2. Reject the file on mismatch.

---

### [LOW] A-05: GUI silently suppresses history-save failures

Affected:
- `main_qt.py` -> `_save_history_if_needed`

Issue:
- `save_history(...)` is wrapped in `try/except Exception: pass`.

Impact:
- On disk, permission, or unexpected write errors, the user may believe history is being saved while persistence is actually failing.
- This is primarily an integrity/operability issue, but it affects trust in the local security feature.

Recommendation:
1. Log failures and surface a system message in the UI.
2. Consider disabling auto-save after repeated failures until the user reconnects or re-enables history.

---

### [LOW] A-06: Main CI test gate does not include all security-relevant test modules

Affected:
- `.github/workflows/test-gate.yml`

Issue:
- The main gate runs a focused subset of tests, but it does not include `tests.test_chat_history`, `tests.test_history_ui_guards`, or `tests.test_audit_remediation`.

Impact:
- Security regressions in encrypted history behavior, UI guard logic, or audit-policy checks could pass the default gate unless another workflow catches them.

Recommendation:
1. Add those suites to `test-gate.yml`, or introduce a broader mandatory security regression job.

## Verified Security Strengths

- Handshake authentication and channel key separation are strong:
  - signed INIT/RESP payloads bind peer addresses and ephemeral keys
  - session subkeys are derived via HKDF with distinct contexts
- Framing and integrity checks are robust:
  - strict sequence monotonicity
  - HMAC verification binds `seq`, `flags`, and `msg_id`
  - plaintext after handshake is treated as downgrade and disconnects the session
- The previously identified file-transfer completion bug is fixed:
  - regular file transfers now reject `E` if received bytes do not exactly match the declared size
  - covered by `test_file_end_without_full_payload_is_rejected`
- Profile path handling is hardened:
  - scoped profile paths stay inside the profiles directory and reject symlink escapes
- Encrypted chat history is well-designed at rest:
  - per-peer files
  - HKDF-derived key hierarchy from the profile identity
  - NaCl SecretBox encryption
  - atomic replacement writes
- Recent security-hardening changes are present:
  - transient-profile trust warning
  - explicit legacy-compat wiring
  - insecure local BlindBox warning path
  - pinned Gitleaks archive checksum in `secret-scan.yml`

## Encrypted Chat History Review

Status: no direct cryptographic break found.

Validated properties:
- history files are not stored in plaintext
- corrupted or wrongly keyed history files fail closed
- per-peer history is separated on disk
- history capture obeys the UI toggle
- disconnect/close handling resets or flushes state correctly

Residual note:
- Like any host-local encrypted state derived from the profile identity, confidentiality still depends on the local-machine compromise model.

## Operational Residual Risks

These are important assumptions, but not rated as new findings in this audit:
- `default` remains a transient profile by design, so TOFU trust continuity does not survive restarts; the current UI/runtime warnings make this explicit.
- `legacy_compat` is now properly wired and remains explicit opt-in; using it broadens the interoperability surface and should stay off by default.
- BlindBox local/direct insecure mode still exists only as an explicit override and is clearly warned about; this is a risky mode, but it is no longer silent.
- Persistent profiles may rely on release-built-in BlindBox replicas unless operators override that policy; privacy-sensitive deployments may prefer their own replicas.

## Remediation Priority

1. **P1:** add strict final-size verification for inline image completion and a matching regression test.
2. **P1:** enforce signed release artifacts in official release automation.
3. **P2:** strengthen local history integrity checks (`peer` verification and longer peer identifier).
4. **P2:** make history-save failures visible to the user.
5. **P3:** widen the default CI security regression gate.

## Conclusion

No confirmed Critical or High software vulnerabilities were found in the current codebase. The secure-channel design, handshake authentication, and encrypted local history are in solid shape. The most important remaining code issue is the inline-image completion integrity gap; the rest are mainly release-policy and defense-in-depth improvements.
