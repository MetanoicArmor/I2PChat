# Security Audit Report: I2PChat

Audit date: 2026-03-28  
Repository state: `0ed6586`  
Mode: full audit (protocol + cryptography + local persistence + UI boundaries + CI/release + supply chain)

## Executive Summary

This audit reviewed the current repository end-to-end, including the newly added encrypted chat history functionality.

Confirmed findings:
- Critical: 0
- High: 0
- Medium: 3
- Low: 3

Overall status:
- Core protocol protections are strong (signed handshake, TOFU pinning for named profiles, strict seq/HMAC checks, downgrade handling).
- New local encrypted chat history is implemented correctly at-rest (HKDF-derived keys + NaCl SecretBox + atomic writes).
- Remaining risks are mostly logic-level integrity edge cases and operational hardening gaps (release trust/distribution policies).

## Scope and Methodology

Reviewed components:
- Protocol/crypto/runtime: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline subsystem: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI boundary and local behavior: `main_qt.py`, `chat_history.py`, `notifications.py`
- Build/release/CI and dependency governance: `requirements.txt`, `requirements.in`, `.github/workflows/*`, `build-*.sh`, `build-windows.ps1`

Method:
- static trust-boundary and attack-surface review
- protocol and cryptographic control verification
- targeted runtime security regression checks
- supply-chain and release-integrity review

Executed checks:
- `python3 -m unittest tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_asyncio_regression tests.test_chat_history tests.test_history_ui_guards -v`
  - Result: `OK (69 tests)`
- Additional targeted checks performed during this audit cycle:
  - history + atomic-write suites: `OK`
  - encrypted history smoke check: history file has `I2CH` magic and does not contain plaintext payload text

## Threat Model Summary

Adversaries considered:
- malicious remote peer on I2P
- active transport/path manipulator
- local unprivileged process on same host
- supply-chain/distribution attacker

Well-mitigated classes:
- message tampering and replay attempts
- post-handshake plaintext downgrade attempts
- frame desync abuse beyond bounded resync limits

Residual classes:
- product-model TOFU first-contact risk (especially in transient profile mode)
- local-host trust assumptions around SAM and BlindBox local options
- release authenticity relying on manual verification workflows

## Findings (ordered by severity)

### [MEDIUM] A-01: File transfer completion accepts `E` without strict final size equality

Affected:
- `i2p_chat_core.py` -> `receive_loop`, branch `msg_type == "E"`

Issue:
- On end-of-file marker `E`, current logic finalizes transfer and emits success/ACK without explicit `incoming_info.received == incoming_info.size` verification.
- Chunk-level bounds in `msg_type == "D"` are present, but a strict final integrity gate is still missing.

Impact:
- A malicious authenticated peer (or buggy sender) can terminate early and have a truncated file accepted as complete.

Recommendation:
1. In `E` branch, require strict equality (`received == declared size`) before success/ACK.
2. On mismatch: emit error, delete partial file, and skip success ACK.

---

### [MEDIUM] A-02: Trust persistence gap for transient `default` profile

Affected:
- `i2p_chat_core.py` -> `_ensure_local_signing_key`, `_load_trust_store`, `_save_trust_store`

Issue:
- For `profile == "default"`, signing seed is ephemeral and trust-store pinning is not persisted.
- This is an intentional product behavior, but it weakens cross-session trust continuity.

Impact:
- First-contact TOFU risk effectively reappears on each run for transient profile usage.

Recommendation:
1. Keep behavior for transient mode, but keep the security trade-off explicit in UI/docs.
2. Recommend named profiles for users requiring stable trust continuity.

---

### [MEDIUM] A-03: Release authenticity still lacks enforced platform-native trust chain

Affected:
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`
- release policy checks in `.github/workflows/security-audit.yml`

Issue:
- Checksums and detached signatures are present, but there is no enforced platform-native signing/notarization flow in release automation.

Impact:
- Verification quality depends on manual user discipline; operational risk remains for mass distribution.

Recommendation:
1. Add platform-native signing/notarization workflows.
2. Add provenance attestations for release artifacts.
3. Publish a strict end-user verification policy.

---

### [LOW] A-04: `legacy_compat` semantics can confuse operators

Affected:
- `i2p_chat_core.py` (`ProtocolCodec(..., allow_legacy=False)` path)
- UI/env flag surface in `main_qt.py`

Issue:
- Flag naming/expectation and effective codec behavior can diverge, increasing operator confusion risk.

Recommendation:
1. Either wire behavior end-to-end or remove/rename the flag.

---

### [LOW] A-05: Insecure local BlindBox mode remains available by explicit override

Affected:
- `i2p_chat_core.py` (`I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL`, direct/local policy handling)
- `blindbox_local_replica.py`

Issue:
- Reduced-security local mode can still be intentionally enabled.

Recommendation:
1. Keep warning telemetry/UI explicit.
2. Prefer strict deployment policy (`I2PCHAT_BLINDBOX_REQUIRE_SAM=1` and tokenized local access).

---

### [LOW] A-06: Secret-scan tool archive and checksum share trust source

Affected:
- `.github/workflows/secret-scan.yml`

Issue:
- Tool archive and checksum are fetched from the same upstream source.

Recommendation:
1. Prefer detached signatures or independent provenance trust roots when available.

## Verified Security Strengths

- Signed handshake and peer key continuity model for named profiles:
  - `i2p_chat_core.py` (`_handle_handshake_message`, `_pin_or_verify_peer_signing_key`)
- Strong framing/integrity controls:
  - `protocol_codec.py` vNext framing + resync bounds
  - `crypto.py` context-bound HMAC (`seq`, `flags`, `msg_id`)
- Anti-downgrade and replay/reorder protections:
  - strict encrypted-frame expectations after handshake
  - sequence monotonicity checks
- Path confinement and safer persistence:
  - profile-scoped path checks + atomic writes in `blindbox_state.py`
- Supply-chain hygiene:
  - hash-pinned lockfile installs in CI paths
  - pinned action refs, dedicated test/audit workflows

## Encrypted Chat History Security Review (new feature)

Status: no direct cryptographic or logic break found in current implementation.

Validated properties:
- per-peer file separation (`<profile>.history.<peer_hash>.enc`)
- no plaintext payload leak in stored history file
- clean failure on wrong key/corrupt history file
- ON/OFF capture behavior enforced by guards
- disconnect/close state reset and save paths covered by tests

Residual note:
- like other local state, confidentiality still depends on host compromise model.

## Testing and Coverage Notes

Strong coverage exists for:
- vNext framing integrity and downgrade behavior
- SAM input validation
- async regressions in handshake/BlindBox state handling
- encrypted history storage and UI guard semantics

Remaining useful tests to add:
1. explicit regression test for strict file-size equality at `msg_type == "E"`
2. dedicated integration test for end-of-file mismatch handling (cleanup + no ACK)
3. expanded release pipeline verification tests (artifact signing policy checks)

## Remediation Priority

1. **P1:** implement strict file completion integrity check on `msg_type == "E"` (A-01).
2. **P1:** enforce platform-native release trust chain and provenance (A-03).
3. **P2:** clarify/clean up `legacy_compat` semantics (A-04).
4. **P2:** continue policy hardening docs around transient profile trust and local BlindBox overrides (A-02, A-05).
5. **P3:** improve independent trust for secret-scan tooling verification (A-06).

## Conclusion

No confirmed Critical/High vulnerabilities were found in the current repository state. Protocol and cryptographic controls are generally strong; the main practical work left is one file-transfer integrity edge case and release/distribution trust hardening.
