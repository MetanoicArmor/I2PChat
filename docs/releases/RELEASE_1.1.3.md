# I2PChat v1.1.3 — vNext-only live framing

Patch after **v1.1.2**: **removes** obsolete **pre-vNext** live wire framing from `ProtocolCodec` and the application. **`I2PCHAT_LEGACY_COMPAT`** and constructor **`legacy_compat`** are **gone**; peers must speak **vNext** (`MAGIC` + `PROTOCOL_VERSION`). **Profile-on-disk migration** helpers (`migrate_legacy_*` for flat `.dat` layout) are **unchanged** — they are unrelated to wire format.

Security audits (**EN/RU**) and **`docs/PROTOCOL.md`** are updated accordingly.

## EN

### Summary

All live chat bytes use the **vNext** binary codec only. ASCII line-style frames are **not** parsed. This **narrows** attack surface and matches the supported product line (no maintained interop target for ancient clients).

### Technical

- `i2pchat/protocol/protocol_codec.py`: drop `allow_legacy`, `_read_legacy_frame`, `DecodedFrame.is_legacy`.
- `i2pchat/core/i2p_chat_core.py`, **PyQt** / **Textual** entrypoints: remove env and `legacy_compat`.
- Tests: `tests/test_protocol_framing_vnext.py` — `test_ascii_style_stream_hits_resync_limit`; legacy-specific tests removed.

### Compatibility

**Breaking** for any hypothetical peer that only emitted pre-vNext frames. Normal **v1.x** peers are unaffected.

---

## RU

### Кратко

**v1.1.3** убирает устаревший **построчный** формат с живого канала; остаётся только **vNext**. Переменная **`I2PCHAT_LEGACY_COMPAT`** и флаг **`legacy_compat`** удалены. **Миграция файлов профиля** на диске (`migrate_legacy_*`) **без изменений** — это не про wire.

### Совместимость

**Разрыв** только для гипотетического пира без vNext; обычные пиры **v1.x** не затронуты.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.3.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.3.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.3.zip` | Unzip → open I2PChat.app |
