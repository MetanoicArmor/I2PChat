# I2PChat v1.4.0 — HS4 handshake, TOFU UX, live Send and SAM fallback

## EN

### Scope

Protocol-breaking security and transport release after **v1.3.3**. It finishes mutual **FINISHED** key confirmation (handshake v4), improves first-contact **TOFU** UX with out-of-band fingerprints, routes acceptor **Send** from a wire-secure **LivePeerSession**, and prefers a healthy **system SAM** when bundled `i2pd` is down so BlindBox / live session can start.

**v1.4.0 does not interoperate with 1.3.x.** All peers (and all group members) must upgrade together. Full audit write-up: [`docs/AUDIT_EN.md`](../AUDIT_EN.md) / [`docs/AUDIT_RU.md`](../AUDIT_RU.md).

### Highlights

- **Handshake v4 (`PROTOCOL_VERSION = 4`):** directional session subkeys (`i2r` / `r2i`) replace the shared `(k_enc, k_mac)` pair; a mandatory encrypted, transcript-bound **`FINISHED`** confirmation runs in both directions before any application data or BlindBox root.
- **Deferred first-contact TOFU:** Trust dialogs (Qt + TUI) show the full SHA-256 fingerprint and a Signal-style safety number for optional OOB comparison; Cancel is the default; `oob_verified` is persisted in trust store v2.
- **Live acceptor Send:** outbound Send on the accepting side is routed from a wire-secure **LivePeerSession** after the channel is confirmed.
- **System SAM fallback:** when bundled `i2pd` is unavailable, a healthy system SAM is preferred so BlindBox / live session can still start; `system_sam_host` stays loopback unless `I2PCHAT_ALLOW_REMOTE_SAM=1`.
- **Security hardening (protocol + at rest):** signed group invites v2, stricter `GROUP_CONTROL` roster checks, sender-bound group BlindBox key schedule, encrypted identity `.dat` / group records / replica auth tokens, pinned bundled-`i2pd` checksum, and related remediations from the v1.4.0 audit.

### Compatibility

- **Not compatible** with **1.3.x** wire handshake, group invites, or group BlindBox key schedule.
- Existing profiles migrate on load where possible (encrypted `.dat`, trust v2, encrypted group/replica stores).
- Fresh installs still default to **system SAM**; switch to bundled router in **More actions → I2P router…** when the build includes it.

### Verification

```bash
gpg --keyserver keys.openpgp.org --recv-keys 2BA0C56D8240077F9773248A2C05CFB3F6DFDF99
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS   # or: shasum -a 256 -c SHA256SUMS
```

### Maintainer checklist (for `v1.4.0` tag + GitHub assets)

1. Build/upload platform artifacts for `v1.4.0` (Windows / macOS arm64+x64 / Linux x86_64+aarch64, plus winget zips).
2. Publish signed `SHA256SUMS` + `SHA256SUMS.asc` (and `SHA256SUMS.linux-aarch64` when needed).
3. Refresh packaging manifests if needed: `./packaging/refresh-checksums.sh 1.4.0`.
4. Publish notes: `gh release edit v1.4.0 --notes-file docs/releases/RELEASE_1.4.0.md`.

## RU

### Кратко

Релиз безопасности и транспорта после **v1.3.3** с **несовместимым** протоколом. Завершено взаимное подтверждение ключей **FINISHED** (handshake v4), улучшен UX **TOFU** при первом контакте, исходящий **Send** на стороне acceptor идёт из wire-secure **LivePeerSession**, при недоступном bundled `i2pd` предпочитается здоровый **system SAM**.

**v1.4.0 не совместим с 1.3.x** — все пиры и участники групп должны обновиться вместе. Подробности аудита: [`docs/AUDIT_RU.md`](../AUDIT_RU.md).

### Основные изменения

- **Handshake v4:** направленные session-ключи и обязательный **`FINISHED`** до прикладных данных.
- **TOFU:** полный fingerprint + safety number, отложенное подтверждение, `oob_verified` в trust v2.
- **Live Send / SAM:** Send acceptor из secure session; fallback на system SAM при падении bundled router.
- **Жёстче security-модель:** подписанные инвайты v2, roster checks, новый group BlindBox schedule, шифрование at-rest для `.dat` / групп / replica auth.

### Совместимость

С **1.3.x** по проводу не совместим. Профили по возможности мигрируют при загрузке.

### Проверка

```bash
gpg --keyserver keys.openpgp.org --recv-keys 2BA0C56D8240077F9773248A2C05CFB3F6DFDF99
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.4.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.4.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.4.0.zip` | Unzip → open I2PChat.app |
