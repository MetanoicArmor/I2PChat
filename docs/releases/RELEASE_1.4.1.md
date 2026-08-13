# I2PChat v1.4.1 — sealed 1:1 metadata, opaque group invites, group sidebar

## EN

### Scope

Maintenance release after **v1.4.0**. It encrypts 1:1 contact metadata and compose drafts at rest, hides group-invite contents in the shareable token, shows encrypted groups in the sidebar as soon as the identity key is available, and ships bundled `i2pd` **2.61.0**.

Handshake remains **v4**. Peers on **1.4.0** still chat and join groups; new opaque invite strings need **1.4.1** to decode.

### Highlights

- **Contacts and compose drafts at rest:** `*.contacts.json` (`I2CB`) and `*.compose_drafts.json` (`I2CD`) are identity-keyed NaCl SecretBox, same model as group records. Last-message previews and unsent drafts are no longer plaintext on disk. Legacy JSON is read and re-encrypted on the next save after identity load; an encrypted file is not overwritten with plaintext if the key is not ready yet.
- **Opaque group invites:** new shareable tokens are unpadded base64url of `wrap_key || SecretBox(signed v2 JSON)` with no `__I2PCHAT_GROUP_INVITE__:` prefix, so title and members are not visible on sight. **1.4.0** prefix+JSON invites still decode.
- **Groups in the sidebar:** encrypted group records (`I2GS`) are listed once `get_identity_key_bytes()` is available, instead of staying empty until a Saved-peer click.
- **Group sender label:** the sender name is highlighted above group message text.
- **Bundled i2pd 2.61.0:** linux-x86_64 (Arch Boost 1.91), darwin-arm64 from gui-i2pd; staged `*.so` keep execute mode.
- **Packaging/docs:** Debian zip/icon staging prefers local release files; Ubuntu/Pages install URLs point at **I2PChat-ng**.

### Compatibility

- **Wire handshake v4** is unchanged. **1.4.0** and **1.4.1** interoperate for 1:1 and group transport.
- **Still not compatible with 1.3.x.**
- New opaque invite tokens are not readable by **1.4.0**; **1.4.1** still accepts the older prefixed form.
- Existing profiles migrate contacts and drafts locally on first save after identity load. Chat history `.enc` was already encrypted.

### Verification

```bash
gpg --keyserver keys.openpgp.org --recv-keys 2BA0C56D8240077F9773248A2C05CFB3F6DFDF99
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS   # or: shasum -a 256 -c SHA256SUMS
```

### Maintainer checklist (for `v1.4.1` tag + GitHub assets)

1. Build/upload platform artifacts for `v1.4.1` (Windows / macOS arm64+x64 / Linux x86_64+aarch64, plus winget zips).
2. Publish signed `SHA256SUMS` + `SHA256SUMS.asc` (and `SHA256SUMS.linux-aarch64` when needed).
3. Refresh packaging manifests: `./packaging/refresh-checksums.sh 1.4.1`.
4. Publish notes: `gh release edit v1.4.1 --notes-file docs/releases/RELEASE_1.4.1.md`.

## RU

### Кратко

Технический релиз после **v1.4.0**. На диске шифруются книга контактов и черновики ввода, инвайты в группы больше не показывают название и участников в открытую, группы появляются в сайдбаре сразу после загрузки ключа личности, в сборки входит bundled `i2pd` **2.61.0**.

Handshake по-прежнему **v4**. С **1.4.0** живой чат и группы совместимы; новые непрозрачные инвайты читает только **1.4.1**.

### Основные изменения

- **Контакты и черновики at rest:** `*.contacts.json` (`I2CB`) и `*.compose_drafts.json` (`I2CD`) шифруются ключом личности. Превью сообщений и неотправленный текст больше не лежат открытым JSON. Старый plaintext мигрирует при следующем сохранении; зашифрованный файл не затирается, пока ключа нет.
- **Непрозрачные инвайты:** без префикса `__I2PCHAT_GROUP_INVITE__:`; title/members не видны в токене. Старый формат 1.4.0 по-прежнему принимается.
- **Группы в сайдбаре** после загрузки identity, а не только после клика по Saved peers.
- **Имя отправителя** в групповых сообщениях выделено над текстом.
- **Bundled i2pd 2.61.0** (linux-x86_64, darwin-arm64); у staged `*.so` сохраняется `+x`.
- **Упаковка:** Debian предпочитает локальный zip/иконку; apt-ссылки на **I2PChat-ng**.

### Совместимость

С **1.4.0** по проводу совместим. С **1.3.x** — нет. Новые инвайты 1.4.0 не декодирует. Контакты и черновики перешифровываются локально.

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
| Windows | `I2PChat-windows-x64-v1.4.1.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.4.1.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.4.1.zip` | Unzip → open I2PChat.app |
