# I2PChat v0.6.0 — BlindBox UX and offline delivery

## EN

### Summary

`v0.6.0` makes BlindBox offline delivery practical for daily use:

- **Smart send:** live secure session when available; otherwise BlindBox queue when runtime is ready.
- **Default-on BlindBox** for named (persistent) profiles; off for `default` / `TRANSIENT`; disable with `I2PCHAT_BLINDBOX_ENABLED=0`.
- **Blind Box servers:** configure shared endpoints via `I2PCHAT_BLINDBOX_REPLICAS` and related env vars; built-in release defaults in `i2p_chat_core.py` (opt-out with `I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1`). Optional local Blind Box for dev/same-host: `I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1`.
- **Quieter UI:** fewer BlindBox status lines in the chat; clearer status bar and `Send offline` when queued; still **text-only** offline (no attachments via BlindBox).

### Security hardening (shipped with this line)

Audit follow-ups (except deferred **M-05**): lockfile-first `pip-audit` in CI; SAM debug log redaction; local Blind Box hardening (optional auth token, `max_entries`/`FULL`, `I2PCHAT_BLINDBOX_REQUIRE_SAM`); basename-only paths in sensitive UI/logs; notification fallback without message body on Windows. See `AUDIT_EN.md`, `AUDIT_RU.md`, `REMEDIATION_PLAN.md`.

### Compatibility

Live chat stays compatible with the current client line; very old BlindBox peers may partially interoperate.

---

## RU

### Кратко

`v0.6.0` доводит BlindBox до удобного офлайн-режима на каждый день:

- **Умная отправка:** при живой secure-сессии — онлайн; иначе очередь BlindBox, если runtime готов.
- **BlindBox по умолчанию** для именованных (persistent) профилей; для `default` / `TRANSIENT` выключен; отключение: `I2PCHAT_BLINDBOX_ENABLED=0`.
- **Серверы Blind Box:** список endpoint’ов через `I2PCHAT_BLINDBOX_REPLICAS` и связанные переменные; в релизе есть встроенный набор в `i2p_chat_core.py` (отключение: `I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1`). Для dev/same-host — опционально локальный Blind Box: `I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1`.
- **Тише UI:** меньше служебных строк BlindBox в чате; понятнее статус-бар и подпись `Send offline` в режиме очереди; офлайн по-прежнему **только текст** (вложения через BlindBox не входят).

### Усиление безопасности (в этой же ветке релиза)

По аудиту (кроме отложенного **M-05**): CI с lockfile-first `pip-audit`; редакция SAM debug-логов; hardening локального Blind Box (`blindbox_local_replica.py`: опциональный auth token, `max_entries`/`FULL`, `I2PCHAT_BLINDBOX_REQUIRE_SAM`); в чувствительных местах в UI/логах только basename путей; Windows fallback уведомлений без текста сообщения. Подробности: `AUDIT_EN.md`, `AUDIT_RU.md`, `REMEDIATION_PLAN.md`.

### Совместимость

Live-чат совместим с текущей линией клиента; очень разные сборки BlindBox могут работать частично.
