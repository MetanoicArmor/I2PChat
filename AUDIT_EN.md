# Security Audit Report: I2PChat

Audit date: 2026-03-17  
Mode: full audit (architecture + protocol + crypto + CI/build + supply chain)  
Scope: current local repository state (`I2PChat`)

## Executive Summary

This audit focuses on architectural trust boundaries and protocol behavior under adversarial conditions, with additional review of build and CI integrity controls.

Confirmed findings:
- Critical: 0
- High: 0
- Medium: 1
- Low: 4

Overall conclusion: runtime protocol hardening is strong (signed handshake, TOFU pinning, replay/downgrade protections, context-bound MAC), while residual risk is concentrated in release authenticity and long-term supply-chain governance.

## Scope and Methodology

Reviewed components:
- Protocol and core runtime: `i2p_chat_core.py`, `protocol_codec.py`, `crypto.py`
- GUI and local boundary handling: `main_qt.py`, `notifications.py`
- Build and packaging: `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`, `I2PChat.spec`
- Dependency and lock governance: `requirements.in`, `requirements.txt`, `requirements-build.txt`, `requirements-ci-audit.txt`
- CI controls: `.github/workflows/security-audit.yml`, `.github/workflows/secret-scan.yml`, `.github/workflows/nix-check.yml`
- Security regression tests: `tests/test_protocol_framing_vnext.py`, `tests/test_profile_import_overwrite.py`, `tests/test_audit_remediation.py`, `tests/test_asyncio_regression.py`

Method:
- Static trust-boundary and attack-surface review
- Protocol and cryptographic control verification
- Supply-chain and release integrity review
- Regression test execution

Test verification:
- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py tests/test_profile_import_overwrite.py tests/test_audit_remediation.py` -> OK (39 tests)

## Architecture and Trust Boundaries

```mermaid
flowchart LR
  guiEntry[main_qt.py_chat-python.py] --> coreEngine[i2p_chat_core.py]
  coreEngine --> protocolCodec[protocol_codec.py]
  coreEngine --> cryptoLayer[crypto.py]
  coreEngine --> samLayer[i2plib_sam]
  coreEngine --> localStorage[profile_storage_and_files]
  guiEntry --> notifyLayer[notifications.py]
```

Primary boundaries:
- Network peer -> protocol parser (`ProtocolCodec.read_frame`) -> message dispatcher
- Core runtime -> local SAM router (`i2plib.dest_lookup`)
- Core/GUI -> profile storage and file paths
- GUI/runtime -> local subprocess helpers (notifications/audio)
- Build/CI -> release artifacts and published binaries

Security-significant architectural facts:
- Strict vNext framing (`MAGIC`, explicit `PROTOCOL_VERSION=4`, bounded frame length)
- Legacy parsing is explicit (`allow_legacy=False` in core codec setup)
- Profile and image operations use path confinement (`realpath` + directory checks)
- ACK tracking uses bounded state and TTL pruning (`ACK_MAX_PENDING`, `ACK_TTL_SECONDS`)

## Protocol and Cryptography Deep-Dive

Verified controls:
- Signed handshake (`INIT`/`RESP`) using Ed25519 signatures
- Peer key TOFU pinning (`_pin_or_verify_peer_signing_key`)
- PFS with ephemeral X25519 keys and DH shared secret
- Final shared secret derivation from DH + both nonces
- Context-bound HMAC (`seq`, `flags`, `msg_id`) with constant-time compare
- Anti-replay via strict sequence validation
- Anti-downgrade detection for post-handshake plaintext frames
- ACK context validation (`peer_addr`, `ack_kind`, `ack_session_epoch`)

Protocol framing facts:
- Header: `MAGIC(4) | VER(1) | TYPE(1) | FLAGS(1) | MSG_ID(8) | LEN(4)`
- Resynchronization hard limit is enforced (`resync_limit`, default 64 KiB)

## Threat Model Summary

Adversaries considered:
- Remote malicious peer on I2P
- Active MITM-like manipulator at transport boundary
- Local unprivileged attacker in hostile workstation environment
- Supply-chain attacker (dependency/build/release channel)

Mitigated classes:
- Message tampering (HMAC)
- Replay and reorder attempts (sequence checks)
- Protocol downgrade attempts (plaintext rejection after handshake)
- Handshake impersonation without trust break (signed handshake + TOFU + SAM identity checks)

Residual classes (design/operational):
- Metadata leakage from visible framing fields and pre-handshake identity exchange
- Release-channel authenticity gaps without platform-native code signing/notarization
- Long-term maintenance risk of vendored transport library updates

## Findings

## [MEDIUM] A-01: Release artifacts are checksummed/signed but not platform-trust signed

Affected:
- `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`
- `.github/workflows/security-audit.yml`

Category: release authenticity / supply chain

Observation:
- Build scripts generate `SHA256SUMS` and detached `SHA256SUMS.asc` signatures.
- There is no platform-native trust chain (for example Authenticode for Windows, Apple signing/notarization for macOS, provenance attestations in release pipeline).

Impact:
- Users must rely on manual checksum/signature workflows and out-of-band key trust.
- Compromised distribution channels remain a realistic risk amplification point.

Exploitability:
- Medium. Requires release-channel compromise or user verification failure.

Recommendations:
1. Add platform-native signing/notarization for distributed binaries.
2. Add release provenance attestations in CI.
3. Publish and rotate signing policy documentation with key fingerprint pinning.

---

## [LOW] P-01: Handshake key derivation lacks explicit key separation (no HKDF)

Affected:
- `i2p_chat_core.py` (`_compute_final_shared_key`)
- `crypto.py`

Category: cryptographic robustness

Observation:
- Final session key is derived as `SHA256(DH_shared || nonce_init || nonce_resp)` and reused for encryption and MAC contexts.

Impact:
- No known practical break in current construction, but weaker cryptographic hygiene versus explicit KDF key separation.

Exploitability:
- Low. Primarily defense-in-depth.

Recommendations:
1. Introduce HKDF over DH shared secret + transcript salt.
2. Derive separate subkeys (`k_enc`, `k_mac`) with context labels.

---

## [LOW] P-02: Protocol metadata remains observable

Affected:
- `protocol_codec.py`
- `i2p_chat_core.py` (identity preface exchange path)

Category: metadata privacy / traffic analysis

Observation:
- Frame header keeps `TYPE` and `LEN` visible.
- A peer identity line is exchanged before encrypted handshake framing.

Impact:
- Observers may infer message patterns (kind/size) and linkage hints.

Exploitability:
- Low in protocol-integrity terms, relevant for privacy posture.

Recommendations:
1. Document metadata exposure explicitly in threat model/user docs.
2. Evaluate optional padding or obfuscation profiles for high-risk users.

---

## [LOW] S-01: Vendored `i2plib` requires explicit security update governance

Affected:
- `i2plib/` (vendored copy)
- `requirements.in` / `requirements.txt` (no PyPI `i2plib`)

Category: supply-chain lifecycle

Observation:
- The project now intentionally relies on a vendored `i2plib` implementation.
- This removes dependency ambiguity, but shifts patch responsibility entirely to project maintainers.

Impact:
- Security fixes from upstream may lag without formal sync policy.

Exploitability:
- Low direct exploitability; medium operational risk over time.

Recommendations:
1. Establish periodic upstream diff and security advisory review cadence.
2. Add CI policy check for declared vendored-version provenance.

---

## [LOW] S-02: Nix build input tracks moving `nixos-unstable` branch

Affected:
- `flake.nix`

Category: build reproducibility / supply-chain determinism

Observation:
- `nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"` tracks a moving upstream channel.

Impact:
- Security posture and dependency graph can drift between builds.

Exploitability:
- Low direct exploitability; impacts reproducibility and auditability.

Recommendations:
1. Pin nixpkgs by immutable revision for release builds.
2. Add periodic controlled update process and changelog for flake input bumps.

## Verified Strengths

- Hash-pinned lockfiles and `--require-hashes` usage in build/audit dependency installs.
- Pinned GitHub Actions by commit SHA and least-privilege workflow permissions (`contents: read`).
- Dedicated secret scanning workflow with checksum-verified tool download (`gitleaks`).
- Protocol framing and downgrade protections are regression-tested.
- ACK state management includes TTL and bounded pending queue controls.
- GUI image/profile handling includes confinement and atomic write patterns.
- Linux helper execution uses resolved absolute paths (`shutil.which`) before subprocess launch.

## Residual Risks and Testing Gaps

Residual risks:
- Privacy metadata leakage is a known trade-off in current framing.
- Release signing trust UX still depends on user verification discipline.

Recommended additional tests:
1. Negative tests for malformed handshake transcript fields and mixed-role replay attempts.
2. Protocol-level tests for optional padding profiles (if introduced).
3. CI policy tests validating platform signing/notarization requirements once implemented.

## Remediation Priority

1. P1: A-01 (platform-native release trust + provenance)
2. P2: P-01 (HKDF key separation hardening)
3. P3: P-02, S-01, S-02 (privacy documentation/controls and governance hardening)

## Conclusion

I2PChat currently demonstrates strong protocol integrity controls and disciplined defensive checks in runtime paths. The most meaningful remaining work is not breaking protocol security but improving release authenticity guarantees and long-horizon supply-chain governance.
