# I2PChat v0.6.1 — BlindBox and trust-model security fixes

## EN

### Summary

`v0.6.1` is a security-focused patch release on top of `v0.6.0`. It closes three practical issues around BlindBox local secret storage, locked-peer enforcement, and silent TOFU behavior in non-GUI mode.

### What changed

- **BlindBox local state protection:** the local wrap key for `blindbox_root_secret_enc` and previous roots is now derived from the profile’s secret signing seed, not only from predictable profile/peer identifiers. Existing stored BlindBox state is migrated compatibly on load.
- **Locked peer enforcement:** if a profile is locked to `stored_peer`, outbound `connect_to_peer(...)` now fail-closes for other targets, and BlindBox root exchange is blocked unless the connected peer matches the locked peer.
- **Explicit trust policy in CLI/TUI:** without a trust callback, first-contact key pinning is no longer silent by default. Non-GUI auto-pin now requires explicit opt-in via `I2PCHAT_TRUST_AUTO=1`.
- **Regression coverage:** added tests for wrap-key derivation, locked-peer enforcement, BlindBox root suppression on mismatch, and CLI trust policy.

### Compatibility

This is a patch release for the `v0.6.x` line. Live protocol behavior stays compatible with current peers; the main changes are local security policy and BlindBox state handling.

---

## RU

### Кратко

`v0.6.1` — security patch поверх `v0.6.0`. Он закрывает три практических проблемы: локальную защиту BlindBox-секрета на диске, жёсткую привязку locked profile к peer и silent TOFU в non-GUI режиме.

### Что изменилось

- **Защита локального BlindBox state:** локальный wrap key для `blindbox_root_secret_enc` и предыдущих root теперь выводится из секретного signing seed профиля, а не только из предсказуемых profile/peer идентификаторов. Старое BlindBox state мигрируется совместимо при загрузке.
- **Enforce locked peer:** если профиль залочен на `stored_peer`, исходящий `connect_to_peer(...)` теперь fail-closed блокирует другие адреса, а BlindBox root не отправляется, пока connected peer не совпадает с locked peer.
- **Явная trust policy в CLI/TUI:** без trust-callback первый pin ключа больше не происходит молча по умолчанию. Для auto-pin в non-GUI теперь нужен явный opt-in `I2PCHAT_TRUST_AUTO=1`.
- **Регрессии покрыты тестами:** добавлены проверки на derivation wrap-key, enforce locked-peer, запрет отправки BlindBox root при mismatch и explicit trust policy для CLI.

### Совместимость

Это patch-релиз в линии `v0.6.x`. Live-протокол совместим с текущими peer; основные изменения касаются локальной security policy и хранения BlindBox state.
