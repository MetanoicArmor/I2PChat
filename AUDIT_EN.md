# Security Audit Report: I2PChat

Audit date: 2026-03-29  
Repository state: `fdb0211`  
Mode: full audit (protocol + cryptography + local persistence + UI + CI/release + supply chain + secret scan)

## Executive Summary

Full audit revision after contact-book / Saved peers UX work, release-process adjustments, and CI hardening visible on `main`.

Confirmed findings:
- Critical: 0
- High: 0
- Medium: 1
- Low: 4

Overall status:
- Core secure-channel controls remain strong (signed handshake, HKDF key separation, HMAC + sequence integrity, anti-downgrade).
- History and inline-image integrity remediations from prior audits remain in place with regression tests.
- **Test gate** now runs the full **`pytest tests/`** suite in CI in addition to the fixed unittest list, improving coverage of helpers (contacts, drafts, notifications, routing).
- **Gitleaks** runs on every push/PR; a repo-local **`.gitleaks.toml`** documents one path allowlist for a unit-test fixture (see A-05).
- Remaining risks are still mostly **release/process** and **edge-case** hardening rather than protocol breakage.

## Scope and Methodology

Reviewed components:
- Protocol/runtime/crypto: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- Offline subsystem: `blindbox_client.py`, `blindbox_blob.py`, `blindbox_state.py`, `blindbox_local_replica.py`
- UI/local storage: `main_qt.py`, `chat_history.py`, `contact_book.py`, `compose_drafts.py`, `notification_prefs.py`, `unread_counters.py`
- CI/release/supply-chain: `.github/workflows/*` (test-gate, security-audit, secret-scan), `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, `requirements*.txt`, `flake.lock`, `.gitleaks.toml`

Executed checks:
- `python -m unittest tests.test_blindbox_state_wrap tests.test_asyncio_regression tests.test_blindbox_client tests.test_atomic_writes tests.test_chat_history tests.test_history_ui_guards tests.test_profile_import_overwrite tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_audit_remediation`
  - Result: **OK (120 tests)** (on auditor host)
- Manual code review: trust boundaries, BlindBox/lock semantics, contact JSON validation, new GUI paths (Saved peers, dialogs), secret-scan policy.

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
- Runtime protocol security is not directly affected, but release authenticity assurance still depends on operator discipline and user verification.

Recommendation:
1. Enforce `I2PCHAT_REQUIRE_GPG=1` in official release jobs.
2. Fail release jobs when detached signature generation fails.
3. Add artifact-level verification in CI (e.g. verify `.asc` for produced artifacts) and maintain a clear end-user verification procedure.

---

### [LOW] A-02: Inline image end marker branch still requires a truthy buffer

Affected:
- `i2p_chat_core.py` (`receive_loop`, branch `msg_type == "G"`, `body == "__IMG_END__"`)

Issue:
- Finalization still requires both `self.inline_image_info` and **truthy** `self.inline_image_buffer`.
- If metadata is active but the buffer is empty, behavior may follow a less deterministic error path.

Impact:
- Typically fail-closed; brittle edge-case diagnostics.

Recommendation:
1. Handle `__IMG_END__` whenever `inline_image_info` is set, regardless of buffer truthiness.
2. Unify size-based finalization for empty and non-empty buffers.

---

### [LOW] A-03: CI coverage vs optional GUI-only dependencies

Affected:
- `.github/workflows/test-gate.yml` (now: unittest gate **+** `pytest tests/ -q`)

Status (improvement vs prior audit):
- The gate **does** run the full pytest tree, covering `contact_book`, `compose_drafts`, `notification_prefs`, `send_retry_policy`, etc.

Residual:
- Any test that **skips** when PyQt6 (or other GUI deps) is missing or unusable on the runner will not assert on that path in CI; smoke coverage for `main_qt` remains environment-dependent.

Recommendation:
1. Keep expanding headless/Qt-offscreen smoke where practical, or a dedicated optional job with a virtual display.

---

### [LOW] A-04: `pip-audit` ignores one known vulnerability ID

Affected:
- `.github/workflows/security-audit.yml`

Issue:
- Workflow uses `--ignore-vuln CVE-2026-4539` for Pygments while waiting for an upstream-fixed PyPI release.

Impact:
- Managed exception; weakens strict “no known vulns” until removed.

Recommendation:
1. Remove the ignore when a fixed package version is available.
2. Track with explicit review/expiry cadence.

---

### [LOW] A-05: Gitleaks path allowlist for a unit test file

Affected:
- `.gitleaks.toml` (allowlist: `tests/test_clear_locked_peer\.py`)

Issue:
- Prevents false positives on mock `.dat` first-line fixtures (`generic-api-key` rule). The allowlisted path is narrow.

Impact:
- Slightly lower automatic scrutiny for that file; acceptable if content remains non-secret test data only.

Recommendation:
1. Periodically review the file for new high-entropy literals.
2. Prefer naming/structure that avoids `*KEY*` assignment patterns in tests when possible (already partially applied: `MOCK_DAT_LINE1`).

## Remediation Status of Previous Open Items

Previously reported items still relevant:
- **A-01, A-02, A-04** — unchanged disposition (see above).

Improved since prior audit revision:
- **Test gate breadth**: full `pytest tests/` in `test-gate.yml`.
- **Secret scanning**: `secret-scan.yml` + gitleaks + `.gitleaks.toml` for documented exceptions.
- **Contact book**: strict `normalize_peer_address` / host regex, `MAX_CONTACTS`, atomic JSON writes (`atomic_write_json`), v1→v2 migration tests (`tests/test_contact_book.py`).
- **Lock UX**: `I2PChatCore.clear_locked_peer()` with tests (`tests/test_clear_locked_peer.py`); trust snapshot `get_peer_trust_info` tested (`tests/test_peer_trust_info.py`).

## Verified Security Strengths

- Secure handshake with signed INIT/RESP and TOFU pinning for persistent profiles.
- HKDF session key separation (`k_enc` / `k_mac`), HMAC + strict sequencing, anti-downgrade after handshake.
- Strong file/inline-image completion integrity paths (with regression tests).
- Encrypted per-peer chat history: SecretBox, atomic writes, peer digest binding, fail-closed on mismatch/corruption.
- SAM/input validation tests; protocol framing tests; BlindBox client/state tests.
- Supply-chain governance job checks `i2plib/VENDORED_UPSTREAM.json` and `flake.lock` pin.

## Residual Operational Risks (By Design / Explicit Opt-In)

- `default` profile remains transient (TOFU not persisted across restarts).
- BlindBox insecure local mode only via explicit override and warnings.
- Built-in BlindBox replica defaults may be inappropriate for strict privacy deployments; configure custom replicas for hardened setups.
- **`*.contacts.json`** and **`*.compose_drafts.json`** store **unencrypted** local metadata (names, notes, message previews, draft text) under the profiles directory — protect the host disk/account; distinct from encrypted history blobs.

## Conclusion

No Critical or High vulnerabilities were confirmed in the audited snapshot. The primary remaining issue is **enforcing release signing** in official automation. Other items are low-severity edge cases, dependency-exception tracking, and gitleaks allowlist hygiene. CI test and secret-scan posture improved versus the previous audit revision.
