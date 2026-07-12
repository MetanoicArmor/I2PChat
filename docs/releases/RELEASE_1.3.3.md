# I2PChat v1.3.3 — authenticated group delivery and security hardening

## EN

### Scope

Security-focused maintenance release after **v1.3.2**. It authenticates group-message senders on both direct and legacy group-wide BlindBox delivery paths, hardens pre-handshake frame handling, prevents unsafe bundled-router SAM exposure, and updates the locked cryptography stack.

### Highlights

- **Authenticated group senders:** group messages delivered over an authenticated 1:1 channel must identify the channel peer as their sender. A group member can no longer use its own connection to inject a message attributed to another member.
- **Signed group BlindBox protocol v3:** legacy group-wide BlindBox envelopes are signed with the sender's Ed25519 identity. Import requires a valid signature and a match with the sender's pinned signing key.
- **Downgrade protection:** unsigned group BlindBox protocol v2 envelopes are rejected. This prevents participants who know the shared group secret from forging another member's identity.
- **Pre-handshake hardening:** unauthenticated plaintext control signals are ignored before the secure channel is established, except for the limited graceful `QUIT` signal.
- **Bundled SAM stays local:** bundled `i2pd` accepts only loopback SAM bind addresses. Unsafe external binds and configuration-injection strings are rejected.
- **Dependency security:** the locked Linux `cryptography` dependency is updated to **49.0.0**, addressing **GHSA-537c-gmf6-5ccf**.
- **Security CI maintenance:** GitHub Actions used by the test gate and secret scanner were refreshed; the gitleaks download now retries transient failures and remains checksum-verified.

### Compatibility

- Normal 1:1 messaging, pairwise BlindBox delivery, profiles, chat history, and stored contacts require no migration.
- Legacy group-wide BlindBox is still opt-in through `I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`, but its wire envelope is now **version 3** and requires pinned Ed25519 identities.
- Unsigned group BlindBox v2 messages are intentionally incompatible and rejected. All participants using the optional group-wide BlindBox path should upgrade to **v1.3.3**.
- Existing bundled-router settings using `127.0.0.1`, another loopback address, `::1`, or `localhost` continue to work. Non-loopback bundled SAM binds are no longer accepted.

### Verification

- Full test suite: **744 passed, 5 skipped, 64 subtests passed**.
- `pip-audit` on locked runtime and build dependencies: **no known vulnerabilities found**.
- `gitleaks`: **678 commits scanned, no leaks found**.

### Maintainer checklist (for `v1.3.3` tag + GitHub assets)

1. Bump the root `VERSION` file to `1.3.3` in the release commit.
2. Build and upload platform artifacts for `v1.3.3`.
3. Refresh checksums/manifests: `./packaging/refresh-checksums.sh 1.3.3`.
4. Publish these notes: `gh release edit v1.3.3 --notes-file docs/releases/RELEASE_1.3.3.md`.

## RU

### Кратко

Это технический релиз безопасности после **v1.3.2**. Он закрывает подмену отправителя в групповых сообщениях, усиливает обработку кадров до завершения handshake, запрещает небезопасное открытие SAM встроенного роутера и обновляет криптографическую зависимость.

### Основные изменения

- **Аутентификация отправителя группы:** `sender_id` группового сообщения, доставленного по защищённому каналу 1:1, теперь обязан совпадать с аутентифицированным пиром этого канала.
- **Подписанный Group BlindBox v3:** конверты опционального общего Group BlindBox подписываются Ed25519-ключом отправителя. При импорте проверяются подпись и совпадение ключа с закреплённым ключом отправителя.
- **Защита от downgrade:** неподписанные конверты Group BlindBox v2 отклоняются. Участник, знающий общий секрет группы, больше не может выдать сообщение за сообщение другого участника.
- **Защита до handshake:** открытые управляющие сигналы до установки защищённого канала игнорируются; исключение оставлено только для ограниченного корректного `QUIT`.
- **SAM встроенного роутера доступен только локально:** встроенный `i2pd` принимает лишь loopback-адреса. Внешняя публикация SAM и строки с инъекцией конфигурации отклоняются.
- **Безопасность зависимостей:** зафиксированная Linux-зависимость `cryptography` обновлена до **49.0.0** для устранения **GHSA-537c-gmf6-5ccf**.
- **CI безопасности:** обновлены GitHub Actions тестового контура и сканера секретов; загрузка gitleaks получила повторные попытки и по-прежнему проверяется по SHA-256.

### Совместимость

- Обычные чаты 1:1, попарная BlindBox-доставка, профили, история и контакты не требуют миграции.
- Старый общий Group BlindBox остаётся опциональным через `I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX`, но теперь использует подписанный протокол **v3** и закреплённые Ed25519-ключи.
- Неподписанные сообщения Group BlindBox v2 намеренно не принимаются. Пользователям этого опционального режима необходимо обновить всех участников до **v1.3.3**.
- Настройки встроенного роутера с loopback-адресами продолжают работать; привязка встроенного SAM к внешнему интерфейсу запрещена.

### Проверка

- Полный набор тестов: **744 успешно, 5 пропущено, 64 подтеста успешно**.
- `pip-audit` runtime/build-зависимостей: **известных уязвимостей не найдено**.
- `gitleaks`: **проверено 678 коммитов, утечек не найдено**.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.3.3.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.3.3.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.3.3.zip` | Unzip → open I2PChat.app |
