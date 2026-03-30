# Security Audit Report: I2PChat

Audit date: 2026-03-30  
Repository state: `cfc2036d59005994738059971567148ef50f119f`  
Mode: full audit (protocol + cryptography + local persistence + UI + CI/release + supply chain + secret scan)

## Executive Summary

Full audit revision after **package-first** layout completion (all application modules under `i2pchat/`, PyInstaller entry [`i2pchat/run_gui.py`](../i2pchat/run_gui.py)), aligned `compileall` steps in Linux/macOS/Windows build scripts, and documentation updates on `main`. No protocol or crypto surface changes were introduced by that refactor; this pass re-runs dependency audit and regression tests and refreshes the snapshot.

Confirmed findings (unchanged IDs; revalidated 2026-03-30):
- Critical: 0
- High: 0
- Medium: 1
- Low: 4

Overall status:
- Core secure-channel controls remain strong (signed handshake, HKDF key separation, HMAC + sequence integrity, anti-downgrade).
- History and inline-image integrity remediations from prior audits remain in place with regression tests.
- **Test gate** runs the full **`pytest tests/`** suite in CI in addition to the fixed unittest list.
- **Gitleaks** runs on every push/PR; a repo-local **`.gitleaks.toml`** documents one path allowlist for a unit-test fixture (see A-05).
- **pip-audit** (same invocations as CI) reported no unfixed issues in locked requirements beyond the documented Pygments ignore (see A-04).
- Spot check: no `shell=True` / `pickle.loads` / `eval` usage under `i2pchat/`; GUI notification and sound paths use `subprocess` with argv lists only.
- Remaining risks are still mostly **release/process** and **edge-case** hardening rather than protocol breakage.

## Scope and Methodology

Reviewed components:
- Protocol/runtime/crypto: `i2pchat/core/i2p_chat_core.py`, `i2pchat/protocol/protocol_codec.py`, `i2pchat/crypto.py`
- Offline subsystem: `i2pchat/blindbox/blindbox_client.py`, `i2pchat/blindbox/blindbox_blob.py`, `i2pchat/storage/blindbox_state.py`, `i2pchat/blindbox/blindbox_local_replica.py`
- UI/local storage: `i2pchat/gui/main_qt.py`, `i2pchat/run_gui.py`, `i2pchat/gui/__main__.py`, `i2pchat/storage/chat_history.py`, `i2pchat/storage/contact_book.py`, `i2pchat/presentation/compose_drafts.py`, `i2pchat/presentation/notification_prefs.py`, `i2pchat/presentation/unread_counters.py`, `i2pchat/platform/notifications.py`
- CI/release/supply-chain: `.github/workflows/*` (test-gate, security-audit, secret-scan), `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, `requirements*.txt`, `flake.lock`, `.gitleaks.toml`

Executed checks:
- `pip-audit` (tooling from `requirements-ci-audit.txt`), matching [`.github/workflows/security-audit.yml`](../.github/workflows/security-audit.yml):
  - `pip-audit -r requirements.txt --ignore-vuln CVE-2026-4539` → **OK** (“No known vulnerabilities found, 1 ignored”).
  - `pip-audit -r requirements-build.txt --ignore-vuln CVE-2026-4539` → **OK**.
  - `pip-audit -r requirements.in --ignore-vuln CVE-2026-4539` → **OK**.
- `python -m unittest tests.test_blindbox_state_wrap tests.test_asyncio_regression tests.test_blindbox_client tests.test_atomic_writes tests.test_chat_history tests.test_history_ui_guards tests.test_profile_import_overwrite tests.test_protocol_framing_vnext tests.test_sam_input_validation tests.test_audit_remediation`
  - Result: **OK (125 tests)** (auditor host).
- `python -m pytest tests/ -q`
  - Result: **432 passed**, **64 subtests passed** (auditor host).
- Manual code review: trust boundaries, BlindBox/lock semantics, contact JSON validation, GUI/subprocess patterns, secret-scan policy, package-first entrypoints vs removed root shims.

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

*Disposition (2026-03-30): confirmed; unchanged after package-first / build-script hygiene work.*

---

### [LOW] A-02: Inline image end marker branch still requires a truthy buffer

Affected:
- `i2pchat/core/i2p_chat_core.py` (`receive_loop`, branch `msg_type == "G"`, `body == "__IMG_END__"`)

Issue:
- Finalization still requires both `self.inline_image_info` and **truthy** `self.inline_image_buffer`.
- If metadata is active but the buffer is empty, behavior may follow a less deterministic error path.

Impact:
- Typically fail-closed; brittle edge-case diagnostics.

Recommendation:
1. Handle `__IMG_END__` whenever `inline_image_info` is set, regardless of buffer truthiness.
2. Unify size-based finalization for empty and non-empty buffers.

*Disposition (2026-03-30): confirmed.*

---

### [LOW] A-03: CI coverage vs optional GUI-only dependencies

Affected:
- `.github/workflows/test-gate.yml` (unittest gate **+** `pytest tests/ -q`)

Status (improvement vs older audits):
- The gate **does** run the full pytest tree, covering `contact_book`, `compose_drafts`, `notification_prefs`, `send_retry_policy`, etc.

Residual:
- Any test that **skips** when PyQt6 (or other GUI deps) is missing or unusable on the runner will not assert on that path in CI; smoke coverage for `main_qt` remains environment-dependent.

Recommendation:
1. Keep expanding headless/Qt-offscreen smoke where practical, or a dedicated optional job with a virtual display.

*Disposition (2026-03-30): confirmed.*

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

*Disposition (2026-03-30): confirmed; audits above used the same ignore for parity with CI.*

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

*Disposition (2026-03-30): confirmed.*

## Remediation Status of Previous Open Items

Still relevant (unchanged disposition):
- **A-01, A-02, A-04** — see above.

Improved in earlier revisions (still in force):
- **Test gate breadth**: full `pytest tests/` in `test-gate.yml`.
- **Secret scanning**: `secret-scan.yml` + gitleaks + `.gitleaks.toml` for documented exceptions.
- **Contact book**: strict `normalize_peer_address` / host regex, `MAX_CONTACTS`, atomic JSON writes (`atomic_write_json`), v1→v2 migration tests (`tests/test_contact_book.py`).
- **Lock UX**: `I2PChatCore.clear_locked_peer()` with tests (`tests/test_clear_locked_peer.py`); trust snapshot `get_peer_trust_info` tested (`tests/test_peer_trust_info.py`).

Since audit dated 2026-03-29 (security-neutral maintenance):
- **Package-first** imports only; root Python shims removed; canonical launchers `python -m i2pchat.gui`, `python -m i2pchat.run_gui`, PyInstaller script `i2pchat/run_gui.py`.
- **Build scripts**: `compileall i2pchat i2plib scripts make_icon.py` on Linux/macOS/Windows before PyInstaller — reduces risk of shipping syntax-broken trees; does not change crypto or trust boundaries.

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

No Critical or High vulnerabilities were confirmed in the audited snapshot. The primary remaining issue is **enforcing release signing** in official automation. Other items are low-severity edge cases, dependency-exception tracking, and gitleaks allowlist hygiene. Package-first and build-script updates did not introduce new confirmed security regressions; automated dependency audit and full test gates passed on the auditor host for this revision.
