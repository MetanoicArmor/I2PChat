# I2PChat v1.1.4 — BlindBox ops, install, and responsiveness

Patch after **v1.1.3**: focused on **BlindBox usability and operations** rather than wire-format changes. This release improves four areas:

1. **Human-readable BlindBox diagnostics** in the GUI
2. **Production-oriented BlindBox daemon package** and bundled deployment assets
3. **One-shot server installer flow** (`install.sh`) with a strict `public` / `token` choice
4. **Lower latency** when some BlindBox replicas are slow

No live wire or storage format break is intended in this patch line.

## EN

### Summary

**v1.1.4** makes BlindBox much easier to operate in practice.

- The diagnostics window now answers the user-facing questions first: **is offline delivery ready, what can I do now, and what should I fix next?**
- The repo now ships a **production-oriented daemon module** (`python -m i2pchat.blindbox.daemon`) with bundled `systemd`, env, fail2ban, and packaging assets.
- The server deployment path is simplified to a **single install script** that asks for only one decision: **`public`** or **`token`**.
- BlindBox send/receive paths no longer wait unnecessarily on slow replicas once a useful validated result is already available.

### User-visible changes

#### 1. BlindBox diagnostics window is clearer

The diagnostics text is no longer a raw telemetry dump. It is now structured as:

- **Profile**
- **Peer**
- **Status**
- **What you can do now**
- **Security**
- **Replicas**
- **Advanced**
- **State guide**

This means common situations such as **`offline-ready`** now read more like:

- *Offline queue is ready*
- *You can send text now without a live secure session*
- *Live connect is optional right now*

instead of exposing only low-level `yes/no` fields.

#### 2. Setup dialog is focused on the real server path

The BlindBox example dialog in the GUI is trimmed to the tabs that matter for server deployment:

- **`install.sh`**
- **`I2pd`**

It also exposes direct actions:

- **Get install** — save the bundled `install.sh`
- **Copy curl** — copy a one-line server install command

#### 3. Production daemon package and deployment assets

The repo now includes a package-local deployment path:

```bash
python3 -m i2pchat.blindbox.daemon
```

Bundled assets now include:

- `i2pchat/blindbox/daemon/systemd/i2pchat-blindbox.service`
- `i2pchat/blindbox/daemon/env/daemon.env.example`
- `i2pchat/blindbox/daemon/fail2ban/*`
- install helpers under `i2pchat/blindbox/daemon/install/`

This gives operators a stable module entrypoint instead of depending only on ad-hoc example files.

#### 4. One-script server install flow

`install.sh` is now intended as the single operator-facing install path.

It:

- installs the daemon on the server
- provisions a dedicated service user
- writes config and systemd service files
- installs fail2ban examples
- asks for only one mode:
  - **`public`** — no replica token
  - **`token`** — generate replica token automatically

The script now prints a clearer install plan, step-by-step progress, and a clean final summary with health-check commands.

#### 5. `install.sh` is included in release archives

Packaging is updated so `install.sh` is included not only in bundled app resources but also in platform release archives:

- Linux zip
- macOS zip
- Windows zip

#### 6. Better responsiveness with multiple BlindBox replicas

BlindBox now behaves better when one replica is much slower than the others:

- **PUT** returns as soon as quorum is satisfied and cancels slower in-flight replicas
- receive polling now asks for the **first blob that actually passes validation**, instead of waiting for every replica before trying candidates

This keeps validation semantics intact while reducing the drag from slow endpoints.

### Technical

- `i2pchat/blindbox/blindbox_diagnostics.py`: rewrite diagnostics formatter to a human-first structure
- `i2pchat/blindbox/blindbox_client.py`: early return/cancellation once quorum or first validated result is reached
- `i2pchat/core/i2p_chat_core.py`: poll loop updated to use first accepted BlindBox blob
- `i2pchat/blindbox/daemon/`: new production daemon package (`__main__`, `service.py`, assets)
- `i2pchat/blindbox/daemon/install/install.sh`: one-shot installer with strict `public` / `token` flow
- `i2pchat/gui/main_qt.py`: simpler setup dialog + `Get install` / `Copy curl`
- build scripts: include `install.sh` in platform release zips

### Compatibility

No intentional protocol break in **v1.1.4**.

- Live framing remains **vNext-only** as in **v1.1.3**
- Existing BlindBox state and profile data remain compatible
- Deployment assets are expanded, not replaced

### Verification

Targeted verification used during the patch line included:

- `tests.test_blindbox_diagnostics`
- `tests.test_blindbox_client`
- `tests.test_blindbox_local_replica`
- `tests.test_profile_blindbox_replicas`
- `tests.test_blindbox_state_wrap`
- `tests.test_blindbox_core_telemetry`
- syntax / compile checks for updated modules and install/build scripts

---

## RU

### Кратко

**v1.1.4** — это патч про **эксплуатацию BlindBox**, а не про новый wire-format.

Основные улучшения:

- окно диагностики BlindBox стало **понятнее человеку**
- появился **production-oriented daemon package**
- серверный путь установки свели к **одному `install.sh`**
- при нескольких BlindBox приложение стало **меньше ждать медленные реплики**

### Что заметит пользователь / оператор

#### 1. Диагностика BlindBox стала понятнее

Текст в окне диагностики больше не выглядит как просто набор сырых полей telemetry.

Теперь он разбит на понятные блоки:

- **Profile**
- **Peer**
- **Status**
- **What you can do now**
- **Security**
- **Replicas**
- **Advanced**
- **State guide**

Например, для состояния **`offline-ready`** интерфейс теперь прямо говорит, что:

- офлайн-очередь готова
- текст можно отправлять уже сейчас
- live Connect сейчас необязателен

#### 2. Диалог setup в GUI стал проще

В окне примеров BlindBox оставлены только действительно нужные вкладки:

- **`install.sh`**
- **`I2pd`**

Добавлены быстрые действия:

- **Get install** — сохранить готовый `install.sh`
- **Copy curl** — скопировать однострочную команду установки

#### 3. Отдельный daemon package для production-пути

Теперь есть стабильная package-точка входа:

```bash
python3 -m i2pchat.blindbox.daemon
```

В репозитории также лежат готовые assets:

- `systemd`
- env example
- fail2ban filter/jail
- install/bundle helper scripts

То есть деплой больше не завязан только на “примерный python-файл”.

#### 4. Установка на сервер — одним скриптом

`install.sh` теперь задуман как основной операторский путь.

Он:

- ставит daemon на сервер
- создаёт service user
- пишет config и systemd unit
- кладёт примеры fail2ban
- спрашивает только одно:
  - **`public`**
  - **`token`**

При этом скрипт стал заметно дружелюбнее: показывает план, шаги установки и финальную сводку с health-check командами.

#### 5. `install.sh` добавлен в release-архивы

Теперь `install.sh` лежит не только внутри bundled resources, но и добавляется в platform release zip:

- Linux
- macOS
- Windows

#### 6. Меньше задержек из-за медленных BlindBox

Если одна реплика заметно медленнее других:

- **PUT** теперь завершается сразу после достижения quorum
- receive path теперь берёт **первый реально валидный blob**, а не ждёт завершения всех реплик перед обработкой

Это уменьшает задержку получения офлайн-писем без ослабления проверок.

### Совместимость

В **v1.1.4** намеренного разрыва протокола нет.

- live framing остаётся **vNext-only**, как в **v1.1.3**
- BlindBox state и profile data совместимы
- расширены именно deployment assets и UX

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.1.4.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.1.4.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.1.4.zip` | Unzip → open I2PChat.app |
