# Security Remediation Plan

Date: 2026-03-22  
Based on: `AUDIT_EN.md`, `AUDIT_RU.md`

## Priority P1 (Immediate)

### M-01: Redact sensitive SAM debug logs
- Scope: `i2plib/aiosam.py`
- Action:
  - Add centralized SAM reply redaction for sensitive keys (`PRIV`, `DESTINATION`, etc.).
  - Ensure debug logging never writes raw private material.
- Verification:
  - Unit test for redaction behavior (`tests/test_aiosam_redaction.py`).

### M-02: Harden BlindBox local replica trust boundary
- Scope: `blindbox_local_replica.py`, `blindbox_client.py`, `i2p_chat_core.py`
- Action:
  - Add optional local auth token support for `PUT/GET`.
  - Add server-side entry limit (`max_entries`) to reduce unbounded local memory growth.
  - Wire auth token from core runtime into local fallback and client.
- Verification:
  - Add client/server token flow test (`tests/test_blindbox_client.py`).

### M-03: Enforce strict SAM mode and explicit downgrade warning
- Scope: `i2p_chat_core.py`
- Action:
  - Add `I2PCHAT_BLINDBOX_REQUIRE_SAM=1` strict mode to reject direct `host:port` replica configs.
  - Emit explicit warning when non-SAM direct transport is active.
- Verification:
  - Add regression test for strict mode rejection (`tests/test_blindbox_core_telemetry.py`).

## Priority P2 (Near-term)

### M-04: Lockfile-first dependency audit in CI
- Scope: `.github/workflows/security-audit.yml`
- Action:
  - Run `pip-audit -r requirements.txt` as primary CI gate.
  - Keep `requirements.in` audit as optional informational check.
- Verification:
  - CI workflow passes with lockfile audit.

### M-05: Platform-native release trust
- Scope: release/build workflows and signing policy docs
- Action:
  - Add platform-native signing/notarization.
  - Add provenance attestations and user verification guidance.
- Verification:
  - Release artifacts include platform-trust signatures and attestation metadata.

## Priority P3 (Hardening)

### L-01 / L-02 / L-03
- Path redaction in UI/system logs where absolute paths are not required.
- Safer notification fallback on Windows (no plaintext message body to stdout).
- Improve independent trust root validation for security tooling artifacts in CI.
