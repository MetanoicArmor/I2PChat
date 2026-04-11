# I2PChat Security Audit

**Audit date:** 2026-04-11  
**Method:** manual source review + automated secret/dependency/static scanning + targeted regression tests  
**Scope:** repository source tree, build/release scripts, CI workflows, and security-sensitive docs/UI flows  
**Important limitation:** this was a **source-based** audit. Generated archives and packaged binaries present in the repository were not reverse-engineered or diffed as binaries.

---

## Executive summary

- No confirmed **Critical** or **High** vulnerabilities were identified in the current source tree.
- The chat/runtime security model is generally strong:
  - SAM command construction is input-validated.
  - Handshake uses X25519 + Ed25519 + HKDF-derived subkeys.
  - Post-handshake traffic enforces MAC validation and replay/order checks.
  - Local sensitive files are usually written atomically with restrictive permissions.
- The main confirmed risks are **integrity and supply-chain/operational** risks rather than direct remote memory-corruption or injection bugs.

**Severity tally:** Critical `0`, High `0`, Medium `3`, Low `3`, Informational `6`

---

## What was validated

### Automated checks

Commands executed during this audit:

```bash
gitleaks detect --no-git --source . --config .gitleaks.toml --report-format json --report-path /tmp/i2pchat-gitleaks.json
uvx --from bandit bandit -q -r i2pchat -f json -o /tmp/i2pchat-bandit.json
uv export --frozen --no-dev --no-emit-project -o /tmp/i2pchat-runtime.txt
uvx pip-audit==2.9.0 --require-hashes -r /tmp/i2pchat-runtime.txt
uv export --frozen --only-group build --no-emit-project -o /tmp/i2pchat-build.txt
uvx pip-audit==2.9.0 --require-hashes -r /tmp/i2pchat-build.txt
uv run pytest tests/test_audit_remediation.py tests/test_sam_input_validation.py tests/test_protocol_hardening.py tests/test_blindbox_server_example.py tests/test_profile_backup.py tests/test_history_export.py -q
```

Observed results:

- `gitleaks`: **no leaks found**
- `pip-audit` runtime lock export: **No known vulnerabilities found**
- `pip-audit` build lock export: **No known vulnerabilities found**
- `pytest`: **102 passed in 57.07s**
- `bandit`: **103 findings**, but manual triage showed they were mostly low-signal patterns (`try/except/pass`, `assert`, generic subprocess heuristics). No Bandit High findings were confirmed as exploitable issues.

### Manual review focus areas

- Update-check trust path and download UX
- Handshake, MAC, replay, and transport framing
- BlindBox local/direct modes and replica example server
- Profile/history export and backup handling
- Bundled-router process management and build-time provenance
- CI security gates and release-integrity controls

---

## Confirmed findings

### Medium

### M1. Update metadata is not cryptographically authenticated

**Locations**

- `i2pchat/updates/release_index.py:20-21`
- `i2pchat/updates/release_index.py:55-80`
- `i2pchat/gui/main_qt.py:10175-10257`

**Evidence**

- The update source is an HTML release page at `http://...b32.i2p/`.
- The client parses ZIP filenames from HTML and compares only version numbers.
- The GUI warns users to verify checksums/signatures manually, but the app itself does not verify signed update metadata.

**Impact**

- A hostile release-page origin, malicious proxy, or compromised eepsite can influence what the app presents as the “latest version”.
- The current implementation does **not** auto-download or auto-install binaries, so this is not direct code execution by itself.
- The risk is primarily **integrity/social-engineering**: redirecting the user toward a malicious build or false update prompt.

**Recommendation**

- If update UX grows beyond “informational check”, require a **signed update manifest** verified with an embedded trusted public key.
- Keep the current manual-verification warning until signed metadata exists.

---

### M2. BlindBox setup UI offers a mutable `curl ... && sudo bash` path from GitHub `main`

**Locations**

- `i2pchat/blindbox/local_server_example.py:23-25`
- `i2pchat/blindbox/local_server_example.py:173-176`
- `i2pchat/gui/main_qt.py:11960-11966`

**Evidence**

- The helper builds a one-liner that downloads `install.sh` from `raw.githubusercontent.com/.../main/...`.
- The GUI exposes a **Copy curl** button that places this command in the clipboard.
- The command runs the downloaded script as root.

**Impact**

- This bypasses the stronger local/bundled path and teaches operators a **mutable remote root installer** workflow.
- If the repository, default branch, or publishing account is compromised, the copied command can execute attacker-controlled code as root on the target host.
- This is a **supply-chain/operational** risk, not an automatic compromise inside I2PChat itself.

**Recommendation**

- Prefer the existing **Get install** flow that saves the bundled local script.
- If a one-liner must remain, pin it to a **release tag or commit digest** and pair it with an integrity check.
- Avoid recommending `curl | sudo bash` style flows from a mutable branch tip.

---

### M3. Portable build path can stage bundled `i2pd` from an unpinned external repository

**Locations**

- `scripts/ensure_bundled_i2pd.sh:8-10`
- `scripts/ensure_bundled_i2pd.sh:45-55`
- `scripts/ensure_bundled_i2pd.sh:64-69`
- `build-windows.ps1:140-159`
- `docs/BUILD.md:20-31`

**Evidence**

- Build helpers can auto-clone `https://github.com/MetanoicArmor/i2pchat-bundled-i2pd.git`.
- The clone is `--depth=1` and there is no commit pin, signed-tag verification, checksum validation, or provenance recording.
- Windows build logic mirrors the same behavior.

**Impact**

- A compromised external repository, branch, or builder environment can poison the bundled router binary that gets embedded into release artifacts.
- This mainly affects **maintainers/builders and release provenance**, not an already-installed client.

**Recommendation**

- Pin the bundled-router source to a **specific commit/tag** and verify provenance.
- Prefer immutable release assets plus checksums/signatures instead of branch-tip clones.
- Record the bundled `i2pd` source revision in release metadata.

---

### Low

### L1. Environment overrides can redirect update UX to arbitrary release/proxy sources

**Locations**

- `i2pchat/updates/release_index.py:62-80`
- `i2pchat/gui/main_qt.py:10175-10195`

**Evidence**

- `I2PCHAT_RELEASES_PAGE_URL` can replace the release-page origin.
- `I2PCHAT_UPDATE_HTTP_PROXY` can redirect update traffic through an arbitrary proxy.
- The GUI warns once before use.

**Impact**

- A local attacker with environment control, or a bad deployment configuration, can influence update results and download-page opening behavior.
- This is mitigated by the one-time warning and by the lack of auto-install.

**Recommendation**

- Consider showing the resolved update origin every time, not only once.
- Optionally restrict non-default schemes/hosts or add a strict allowlist mode.

---

### L2. Optional BlindBox HTTP status endpoint can become unauthenticated if operators expose it incorrectly

**Locations**

- `i2pchat/blindbox/blindbox_server_example.py:92-99`
- `i2pchat/blindbox/blindbox_server_example.py:151-155`
- `i2pchat/blindbox/blindbox_server_example.py:556-563`
- `i2pchat/blindbox/blindbox_server_example.py:597-605`

**Evidence**

- The HTTP status service is optional and disabled by default.
- The default bind is loopback, but `BLINDBOX_HTTP_HOST` is operator-controlled.
- If both admin and replica auth tokens are empty, `_admin_token_ok()` allows access.

**Impact**

- Misconfigured operators can expose `/healthz`, `/status.json`, and `/metrics` beyond loopback without authentication.
- The returned data is not deeply sensitive, but it can leak service state and aid probing.

**Recommendation**

- If `BLINDBOX_HTTP_HOST` is non-loopback, require `BLINDBOX_ADMIN_TOKEN`.
- Alternatively refuse public binds unless explicit auth is configured.

---

### L3. Image validation still fully decodes image payloads, leaving residual local resource-pressure risk

**Location**

- `i2pchat/core/i2p_chat_core.py:1112-1141`

**Evidence**

- Image validation checks file size and dimensions first, then fully loads the image via Pillow (`img.load()`).

**Impact**

- A crafted local image may still trigger elevated CPU/memory usage during decode.
- This is primarily a **local DoS/resource exhaustion** concern, not remote code execution.

**Recommendation**

- Enable an explicit decompression-bomb policy and/or a stricter pixel-budget guard.
- Keep file-size and dimension caps in place.

---

## Informational observations

1. **SAM input validation is solid.** `i2pchat/sam/protocol.py` rejects whitespace/newline/control-character injection in critical SAM tokens and options.
2. **Handshake hardening is present.** `i2pchat/crypto.py` and `i2pchat/core/i2p_chat_core.py` use X25519, Ed25519, HKDF subkey separation, MAC verification, and replay/order checks.
3. **TOFU trust is explicit.** New peer signing keys are pinned and mismatch handling requires approval unless explicit auto-trust is enabled.
4. **Path handling for incoming files is guarded.** Incoming filenames are sanitized and collision-safe allocation uses exclusive creation.
5. **Profile/history backup handling is careful.** Archive imports validate structure/checksums and use atomic writes; backup bundles reject unsafe tar member paths.
6. **CI security hygiene is good.** The repository already runs dependency audit, secret scan, and release-integrity policy checks.

---

## Triaged automated-scan noise

The following automated findings were reviewed and **not** treated as confirmed vulnerabilities:

- `bandit` `B603/B607` around subprocess usage:
  - runtime code uses argument arrays, not `shell=True`
  - Linux notification helpers resolve absolute binaries with `shutil.which`
  - bundled router launch uses `asyncio.create_subprocess_exec`
- `bandit` `B311` in `session_manager.py`:
  - `random.uniform()` is used for reconnect jitter, not cryptographic material
- `bandit` `B103` on `os.chmod(path, 0o755)`:
  - this is applied to a user-saved shell installer and is consistent with executable-script behavior
- `assert` usages in TUI/GUI:
  - they are correctness smells, but no concrete security impact was confirmed in this audit

---

## Strong controls already in place

- No `shell=True` patterns were found in runtime code.
- Secret scanning is configured in CI and local `gitleaks` was clean.
- Dependency locks are present and both runtime/build exports were clean under `pip-audit`.
- Protocol hardening tests cover malformed/truncated frames and transfer edge cases.
- Backup and history persistence favor atomic writes and restrictive file permissions.
- User documentation already tells users to verify `SHA256SUMS` and detached GPG signatures.

---

## Residual risk and recommended next steps

### Highest-value next steps

1. Replace mutable update/install trust paths with signed, immutable metadata/artifacts.
2. Pin and verify external bundled-router sources in build workflows.
3. Enforce auth for any non-loopback BlindBox HTTP status bind.
4. Keep CI dependency/secret scanning mandatory.
5. Add a stricter decompression-bomb policy for hostile image inputs.

### Residual risks that remain by design

- The local I2P router remains part of the trusted computing base.
- Operator-chosen flags can intentionally weaken posture (`I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL`, direct TCP replicas, weak BlindBox quorum).
- Update checks remain advisory rather than cryptographically authoritative.

---

## Primary files reviewed

- `i2pchat/updates/release_index.py`
- `i2pchat/gui/main_qt.py`
- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/core/session_manager.py`
- `i2pchat/crypto.py`
- `i2pchat/protocol/protocol_codec.py`
- `i2pchat/sam/protocol.py`
- `i2pchat/blindbox/blindbox_server_example.py`
- `i2pchat/blindbox/local_server_example.py`
- `i2pchat/router/bundled_i2pd.py`
- `i2pchat/storage/profile_backup.py`
- `i2pchat/storage/history_export.py`
- `i2pchat/storage/profile_export.py`
- `scripts/ensure_bundled_i2pd.sh`
- `.github/workflows/security-audit.yml`
- `.github/workflows/secret-scan.yml`

---

This document is a point-in-time assessment of the current repository state. It does not replace reproducible-build verification, release-signature validation, infrastructure review, or external penetration testing.
