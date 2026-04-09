# I2PChat security audit (full repo)

**Audit date:** 2026-04-09  
**Method:** static code review + focused security tests + dependency audit (`pip-audit` against locked `uv` exports).  
**Scope:** full repository with emphasis on runtime trust boundaries: updates, SAM transport, BlindBox, bundled router process management, local data handling, and CI supply-chain controls.

---

## 1. Executive summary

- **No Critical/High confirmed vulnerabilities** were identified in the current tree.
- Main practical risks are **trust-model and operational**:
  - update metadata is parsed from an HTTP release page (integrity is user-verified, not app-verified);
  - optional insecure/local BlindBox modes depend on operator choices;
  - SAM trust boundary remains the local router.
- Positive controls are strong:
  - internal SAM input validation and regression tests;
  - CI secret scanning and locked dependency audit;
  - release-signing policy checks in CI.

**Severity tally:** Critical `0`, High `0`, Medium `1`, Low `3`, Informational `5`.

---

## 2. Validation performed

### 2.1 Dependency vulnerability scan

Executed during this audit:

- `uv export --frozen --no-dev --no-emit-project -o /tmp/audit-runtime.txt`
- `uvx pip-audit==2.9.0 --require-hashes -r /tmp/audit-runtime.txt`
- `uv export --frozen --only-group build --no-emit-project -o /tmp/audit-build.txt`
- `uvx pip-audit==2.9.0 --require-hashes -r /tmp/audit-build.txt`

Result: **No known vulnerabilities found** in both runtime and build dependency sets.

### 2.2 Security-focused tests

Executed:

- `uv run pytest tests/test_audit_remediation.py tests/test_sam_input_validation.py tests/test_blindbox_client.py -q`

Result: **48 passed**.

### 2.3 Secret pattern scan (manual grep pass)

- No obvious private keys/API tokens found in tracked source/doc files during this audit pass.
- CI also enforces `gitleaks` in `.github/workflows/secret-scan.yml`.

---

## 3. Findings

Severity scale: **Critical / High / Medium / Low / Informational**.

### 3.1 [Medium] Update check does not cryptographically authenticate metadata

**Locations:** `i2pchat/updates/release_index.py`, `i2pchat/gui/main_qt.py`  

**Evidence:** update logic fetches and parses HTML (`fetch_releases_page`, `check_for_updates_sync`), then can open `downloads_page_url()` in browser.

**Impact:** a hostile network/proxy/release-page origin can manipulate visible “latest version” hints and drive users to a malicious download page.  
Important nuance: the app does **not** auto-download/install binaries, and UI explicitly instructs users to verify `SHA256SUMS`/GPG.

**Recommendation:** keep current warnings, and if auto-update is ever added, require a **signed update manifest** with an embedded trusted public key.

---

### 3.2 [Low] Custom release page URL from environment can redirect update UX

**Locations:** `i2pchat/updates/release_index.py` (`I2PCHAT_RELEASES_PAGE_URL`), `i2pchat/gui/main_qt.py` (update override warnings + `openUrl`).

**Impact:** local/environment-level attacker (or misconfiguration) can redirect update checks/download page to an arbitrary URL.

**Current mitigation:** first-run warning dialog explains trust impact; docs mention verification workflow.

**Recommendation:** optional hardening: allowlist schemes/hosts or stronger warning for non-default host.

---

### 3.3 [Low] BlindBox quorum defaults prioritize availability over stronger integrity guarantees

**Location:** `i2pchat/core/i2p_chat_core.py` (`I2PCHAT_BLINDBOX_PUT_QUORUM`, default `1`).

**Impact:** with weak quorum and untrusted replicas, a single replica outage/behavior can influence delivery outcomes more than in stricter quorum settings.

**Recommendation:** document production guidance to use multiple independent replicas and stricter quorum where latency budget permits.

---

### 3.4 [Low] Image decoding can still be used for local resource pressure

**Location:** `i2pchat/core/i2p_chat_core.py` (`validate_image` uses Pillow `Image.open(...).load()`).

**Impact:** local user opening crafted images may trigger elevated CPU/memory use (local DoS profile).

**Current mitigation:** file size and max-dimension limits are enforced before decode.

**Recommendation:** optional defense-in-depth: explicit Pillow decompression bomb guard policy and/or stricter decode-time limits.

---

## 4. Informational observations (not direct vulnerabilities)

1. **SAM trust model is explicit:** app trusts the configured I2P router boundary (`i2pchat.sam` + core integration).
2. **BlindBox insecure-local mode is opt-in:** code requires token unless user explicitly sets `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`.
3. **Process execution hygiene is generally good:** bundled router launch uses `create_subprocess_exec`/argument arrays; no broad `shell=True` runtime pattern in audited paths.
4. **Release and supply-chain governance exists in CI:** pinned `gitleaks`, `pip-audit`, signed-release policy checks in `.github/workflows/security-audit.yml`.
5. **Secret scanning exceptions are narrow:** `.gitleaks.toml` allowlist targets a specific test fixture only.

---

## 5. What is already mitigated well

- SAM command/token validation and dedicated tests (`tests/test_sam_input_validation.py`).
- BlindBox protocol hardening and client/server checks (`tests/test_blindbox_client.py`).
- Path confinement for opening chat images in GUI (`main_qt.py` checks under images directory).
- Locked dependency management (`uv.lock`) with CI audit gate.
- User-facing guidance to verify checksums/signatures for downloaded artifacts.

---

## 6. Residual risks

1. **Network trust for update metadata** remains a social/operational risk until signed metadata is introduced.
2. **Router as TCB:** compromised or malicious router can affect visibility/control at SAM boundary.
3. **Operator-configurable insecure flags** (e.g. BlindBox insecure local mode, weak quorum) can reduce security posture intentionally.

---

## 7. Prioritized next steps

1. Keep update-check warnings and docs strict; avoid silent/automatic installer behavior.
2. Add optional signed metadata validation path for future update UX.
3. Document “secure BlindBox profile” baseline (token required, quorum guidance).
4. Continue running `pip-audit` + gitleaks in CI and keep lockfiles fresh.
5. Consider explicit decode hardening policy for hostile image inputs.

---

## 8. Primary files reviewed

- `i2pchat/updates/release_index.py`
- `i2pchat/gui/main_qt.py`
- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/core/session_manager.py`
- `i2pchat/sam/protocol.py`, `i2pchat/sam/client.py`
- `i2pchat/blindbox/blindbox_client.py`
- `i2pchat/router/bundled_i2pd.py`
- `.github/workflows/security-audit.yml`
- `.github/workflows/secret-scan.yml`
- `.gitleaks.toml`
- `tests/test_audit_remediation.py`
- `tests/test_sam_input_validation.py`
- `tests/test_blindbox_client.py`

---

*This is a point-in-time assessment and does not replace periodic dependency scans, release-signature verification, or dedicated penetration testing.*
