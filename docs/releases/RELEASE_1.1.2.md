# I2PChat v1.1.2 — Legacy framing policy, file-send throughput, ACK soft-drain

Patch after **v1.1.1**: **operational security** for legacy framing (only with a **locked peer** matching the live session), **less event-loop blocking** during **file and inline-image** sends (disk reads off the asyncio thread), **batched `drain`** for **MSG_ACK** / **IMG_ACK** while an **outgoing** transfer is active, new env **`I2PCHAT_MSG_ACK_DRAIN_EVERY`**, refreshed **security audits** (EN/RU), and regression tests.

## EN

### Summary

**v1.1.2** tightens **when legacy frame parsing is allowed**: `I2PCHAT_LEGACY_COMPAT=1` still opts in, but `ProtocolCodec.allow_legacy` turns **on** only when there is an **active connection** and the profile’s **stored (locked) peer** equals the session’s **`current_peer_addr`**. Unlocked profiles, transient profiles, and inbound callers when nothing is locked stay **vNext-only** on the wire. Separately, **sending** files and **G** inline images reads disk chunks via **`asyncio.to_thread`**, and **automatic text delivery ACKs** use a **soft drain** policy during outgoing transfers to reduce **`await drain()`** churn. Documentation and **EN/RU audits** describe the new behaviour.

### User-visible changes

#### Legacy framing (`I2PCHAT_LEGACY_COMPAT`)

- **Before:** With the env flag set, legacy parsing applied to **every** connected peer.
- **After:** Legacy applies **only** if the profile has a **saved/locked peer** that **matches** the peer you are connected to. Otherwise framing stays **strict vNext** (including unknown inbound peers when the profile is not locked).

#### File and picture sending (responsiveness)

- **Disk reads** during **outgoing** file and **inline image (`G`)** sends run in a **thread pool** (`asyncio.to_thread`), so the asyncio loop is not blocked on large synchronous `read()` calls.

#### ACK traffic during your upload

- While **you** are sending a file or inline image, **MSG_ACK** / **IMG_ACK** responses no longer **`drain()`** after every single signal frame by default; **`I2PCHAT_MSG_ACK_DRAIN_EVERY`** (default **16**, clamped **1–256**) controls how many such **S**-frames are buffered before a drain.

### Technical / validation

- Core: `_peer_eligible_for_legacy_framing`, `_sync_codec_allow_legacy`, `_write_signal_frame_maybe_soft_drain`, `_msg_ack_soft_drain_every`; env header comment for **`I2PCHAT_MSG_ACK_DRAIN_EVERY`**.
- Tests: `tests/test_asyncio_regression.py` — `test_legacy_compat_controls_codec_legacy_mode` updated for gated legacy.
- Run **pytest** / **unittest** as for prior releases.

### Compatibility

Wire protocol and frame types are **unchanged**. Operators who relied on legacy against **unlocked** sessions must **lock the profile to that peer** (same address as in `.dat` / stored contact) for legacy parsing to activate. No intentional breaking changes for normal vNext-only use.

---

## RU

### Кратко

**v1.1.2** — патч после **v1.1.1**: **политика legacy-фрейминга** (только при **залоченном** пире, совпадающем с текущей сессией), **меньше блокировок цикла** при **отправке** файлов и **inline-картинок** (чтение с диска через **`asyncio.to_thread`**), **мягкий drain** для **MSG_ACK** / **IMG_ACK** на время **исходящей** передачи, переменная **`I2PCHAT_MSG_ACK_DRAIN_EVERY`**, обновлённые **аудиты безопасности** (EN/RU) и тесты.

### Изменения для пользователя

#### Legacy-фрейминг (`I2PCHAT_LEGACY_COMPAT`)

- **Раньше:** при включённом env legacy действовал для **любого** подключённого пира.
- **Теперь:** legacy включается **только** если в профиле задан **сохранённый (lock) пир** и он **совпадает** с пиром текущей сессии. Иначе на линии остаётся **только vNext** (в т.ч. неизвестные входящие, если профиль не залочен).

#### Отправка файла и картинки

- **Чтение с диска** при **исходящей** отправке файла и **inline (`G`)** выполняется в **пуле потоков**, чтобы не блокировать цикл **asyncio** на синхронном `read()`.

#### ACK во время вашей отправки

- Пока **вы** отправляете файл или inline-картинку, после каждого **MSG_ACK** / **IMG_ACK** не обязательно сразу **`drain()`**; **`I2PCHAT_MSG_ACK_DRAIN_EVERY`** (по умолчанию **16**, диапазон **1–256**) задаёт, через сколько таких **S**-кадров сделать принудительный drain.

### Совместимость

Протокол и типы кадров **те же**. Если legacy нужен к **незалоченному** сценарию — нужно **залочить профиль на этого пира** (тот же адрес, что в сессии). Для обычного vNext поведение без намеренных разрывов.

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.2.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.2.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.2.zip` | Unzip → open I2PChat.app |
