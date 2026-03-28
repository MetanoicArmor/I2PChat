# Security Audit Report: I2PChat

Audit date: 2026-03-28  
Repository state: `d4ecd11`  
Mode: full audit (protocol + cryptography + local persistence + UI + CI/release + supply chain)

## Executive Summary

This is a fresh post-remediation audit after the latest hardening cycle.

Confirmed findings:
- Critical: 0
- High: 0
- Medium: 1
- Low: 3

Overall status:
- Core secure-channel controls remain strong (signed handshake, HKDF key separation, HMAC + sequence integrity, anti-downgrade).
- The previously open history and inline-image integrity items are fixed and covered by tests.
- Remaining risks are mostly release/process hardening gaps rather than protocol breakage.

## Scope and Methodology

Reviewed components:
- Protocol/runtime/crypto: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline subsystem: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI/local storage: `main_qt.py`, `chat_history.py`
- CI/release/supply-chain: `.github/workflows/*`, `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, lockfiles

Executed checks:
- `python3 -m unittest tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_asyncio_regression tests.test_chat_history tests.test_history_ui_guards tests.test_audit_remediation -v`
  - Result: `OK (90 tests)`
- Manual code review for trust boundaries, dataflow, local persistence, and release pipeline policy.

## Findings (Current State)

### [MEDIUM] A-01: Release signing is still optional by default in build scripts

Affected:
- `build-linux.sh`
- `build-macos.sh`
- `build-windows.ps1`
- `.github/workflows/security-audit.yml` (`release-integrity-policy`)

Issue:
- Build scripts can still produce unsigned release output if `gpg` is absent or `I2PCHAT_SKIP_GPG_SIGN=1` is used (unless `I2PCHAT_REQUIRE_GPG=1` is explicitly enforced).
- CI policy verifies signing-related tokens in scripts but does not verify that official release artifacts are always signed/notarized.

Impact:
- Runtime protocol security is not directly affected, but release authenticity assurance still depends on operator discipline and user verification behavior.

Recommendation:
1. Enforce `I2PCHAT_REQUIRE_GPG=1` in official release jobs.
2. Fail release jobs when detached signature generation fails.
3. Add artifact-level verification in CI (for example, verify `.asc` for produced release artifacts) and maintain a clear end-user verification procedure.

---

### [LOW] A-02: Inline image end marker branch still relies on truthy buffer check

Affected:
- `i2p_chat_core.py` (`receive_loop`, branch `msg_type == "G"`, `body == "__IMG_END__"`)

Issue:
- The finalization branch currently requires both `self.inline_image_info` and `self.inline_image_buffer` to be truthy.
- In edge cases with active transfer metadata but empty buffer, flow falls into a different error path instead of deterministic finalization handling.

Impact:
- This remains fail-closed in typical attacks, but it is a brittle edge-case behavior and can produce ambiguous diagnostics.

Recommendation:
1. Handle `__IMG_END__` whenever `inline_image_info` is present, independent of buffer truthiness.
2. Apply deterministic size-based finalization logic for both empty and non-empty buffers.

---

### [LOW] A-03: Main test gate still omits some security-relevant test modules

Affected:
- `.github/workflows/test-gate.yml`

Issue:
- Gate coverage has improved and now includes history/audit suites, but some security-relevant modules are still outside the default gate.

Impact:
- Regressions in non-gated suites may pass the main check if no broader test job runs in the same PR path.

Recommendation:
1. Add a second mandatory “full unittest security” job, or expand main gate coverage further.

---

### [LOW] A-04: `pip-audit` currently ignores one known vulnerability ID

Affected:
- `.github/workflows/security-audit.yml`

Issue:
- Workflow currently uses `--ignore-vuln CVE-2026-4539` for Pygments while waiting for an upstream-fixed release.

Impact:
- This is a managed/explicit exception, but it weakens strict “no known vulns” guarantees until dependency update is available.

Recommendation:
1. Remove ignore flag immediately when fixed package version is available.
2. Track this exception with explicit expiry/review cadence.

## Remediation Status of Previous Open Items

Previously reported items closed in current code:
- Inline image strict end-size integrity and no-ACK-on-mismatch.
- History file peer identifier upgraded to full SHA-256 digest.
- Decrypted history now validates embedded `peer` against expected peer.
- GUI history save failures are logged and surfaced to user.
- Main test gate includes `test_chat_history`, `test_history_ui_guards`, `test_audit_remediation`.

## Verified Security Strengths

- Secure handshake flow with signed context-bound INIT/RESP payloads and TOFU pinning for persistent profiles.
- HKDF-based session key separation (`k_enc` / `k_mac`) and strict HMAC integrity checks bound to message metadata.
- Anti-downgrade behavior after handshake and strict sequence monotonicity enforcement.
- Stronger transfer integrity on file and inline image completion paths (with regression coverage).
- Hardened local encrypted history design:
  - per-peer storage
  - SecretBox encryption at rest
  - atomic writes
  - fail-closed on corruption/wrong key/peer mismatch
- Improved CI gate coverage versus previous audit revision.

## Residual Operational Risks (By Design / Explicit Opt-In)

- `default` profile remains transient by design (TOFU continuity is not persisted between restarts).
- Local BlindBox insecure mode is still available only through explicit override and warning paths.
- Release built-in BlindBox replica defaults may be suboptimal for strict privacy deployments; custom replica policy is recommended for hardened setups.

## Conclusion

No Critical or High vulnerabilities were confirmed in the current codebase. The most important remaining issue is release-signing enforcement in official automation. Other active items are low-severity edge/process hardening opportunities.
