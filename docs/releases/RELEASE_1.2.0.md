# I2PChat v1.2.0 — bundled i2pd router backend

First feature release after **v1.1.4**: I2PChat can now run either with a **system** `i2pd` router or a **bundled** `i2pd` sidecar shipped with the app. This release adds the runtime manager, router backend UI, packaged platform binaries, active-proxy update checks, and a router shortcut in the More actions menu.

## EN

### Summary

**v1.2.0** makes router setup much easier for end users:

- you can keep using a **system** `i2pd` via SAM, or switch to a **bundled** router shipped with the app
- the GUI now exposes a dedicated **I2P router…** dialog
- release bundles can carry platform-specific `i2pd` binaries for macOS, Linux, and Windows
- update checks now use the **active router backend’s HTTP proxy**, so bundled mode works consistently for `.i2p` release pages too

### User-visible changes

#### 1. Router backend selection in the GUI

The **`⋯` → I2P router…** action opens a router dialog where you can:

- choose **system** vs **bundled** router backend
- set the **system SAM host/port**
- set bundled backend ports for:
  - **SAM**
  - **HTTP proxy**
  - **SOCKS proxy**

There is also a new keyboard shortcut:

- **Ctrl+R** on Windows/Linux
- **⌘R** on macOS

Example dialog:

<img src="../../screenshots/8.png" alt="I2P router dialog: choose system or bundled i2pd backend, adjust ports, open router paths, restart bundled router" width="900" />

#### 2. Router operations are available from the same dialog

The router dialog also exposes direct actions:

- **Open data dir**
- **Open log**
- **Restart bundled router**

This means users can inspect router runtime files and restart the embedded router without leaving the app.

#### 3. Bundled router lifecycle support

When the bundled backend is selected, I2PChat now:

- creates an **isolated runtime directory**
- writes dedicated **`i2pd.conf`** / **`tunnels.conf`**
- chooses explicit local ports
- waits for **SAM readiness**
- shuts the sidecar down on app exit

This is intentionally isolated from any separately installed system router.

#### 4. Update check follows the active router backend

The **Check for updates…** action no longer assumes a fixed proxy when a bundled router is active.

For `*.i2p` release pages, the update check now follows the **active router backend’s HTTP proxy** by default, while still allowing override via `I2PCHAT_UPDATE_HTTP_PROXY`.

#### 5. Release bundles can carry bundled `i2pd`

Packaging was updated so release artifacts can include platform-specific bundled router binaries:

- **macOS arm64**
- **Linux x86_64**
- **Windows x64**

The initial oversized debug-like binaries were replaced with much smaller release-style builds so distribution remains practical.

### Technical

- new package: `i2pchat/router/`
  - `settings.py`
  - `runtime.py`
  - `bundled_i2pd.py`
- `i2pchat/gui/main_qt.py`
  - router settings dialog
  - backend switching / apply flow
  - router data/log open actions
  - bundled router restart action
  - **Ctrl/Cmd+R** shortcut wiring
- `i2pchat/updates/release_index.py`
  - explicit proxy override support
  - active-backend proxy integration for update checks
- build integration:
  - `I2PChat.spec`
  - `build-macos.sh`
  - `build-linux.sh`
  - `build-windows.ps1`

### Compatibility

No wire-format break is intended in **v1.2.0**.

- live chat remains **vNext-only** as before
- system-router workflows remain supported
- bundled-router mode is additive and isolated from system `i2pd` state

### Verification

- `python3 -m unittest tests.test_release_index tests.test_router_settings tests.test_bundled_i2pd_config tests.test_bundled_i2pd_binary_resolution`
- `python3 -m py_compile i2pchat/gui/main_qt.py i2pchat/updates/release_index.py i2pchat/router/__init__.py i2pchat/router/settings.py i2pchat/router/runtime.py i2pchat/router/bundled_i2pd.py`
- local bundled-router smoke on macOS:
  - SAM ready on `127.0.0.1:17656`
  - HTTP proxy ready on `127.0.0.1:14444`

---

## RU

### Кратко

**v1.2.0** упрощает настройку роутера для пользователя:

- можно работать либо через **системный** `i2pd` по SAM, либо через **встроенный** `i2pd`, который идёт вместе с приложением
- в GUI появился отдельный диалог **I2P router…**
- релизные сборки теперь умеют включать platform-specific binary встроенного `i2pd`
- проверка обновлений теперь использует **HTTP-прокси активного backend’а**, поэтому bundled-режим корректно работает и для `.i2p` страницы релизов

### Что заметит пользователь

#### 1. Выбор backend’а роутера в GUI

Пункт **`⋯` → I2P router…** открывает диалог, где можно:

- выбрать **system** или **bundled** router backend
- настроить **system SAM host/port**
- задать bundled-порты для:
  - **SAM**
  - **HTTP proxy**
  - **SOCKS proxy**

Добавлена и горячая клавиша:

- **Ctrl+R** на Windows/Linux
- **⌘R** на macOS

Пример диалога:

<img src="../../screenshots/8.png" alt="Диалог I2P router: выбор system или bundled i2pd, настройка портов, открытие путей роутера и перезапуск bundled router" width="900" />

#### 2. Операции с роутером доступны прямо из диалога

В том же окне есть действия:

- **Open data dir**
- **Open log**
- **Restart bundled router**

То есть пользователь может открыть runtime-каталог, посмотреть лог или перезапустить встроенный роутер прямо из приложения.

#### 3. Полный lifecycle для bundled router

Когда выбран встроенный backend, I2PChat теперь:

- создаёт **изолированную runtime-директорию**
- пишет отдельные **`i2pd.conf`** / **`tunnels.conf`**
- использует собственные локальные порты
- ждёт готовности **SAM**
- корректно останавливает sidecar при выходе из приложения

Это сделано так, чтобы не мешать отдельно установленному системному `i2pd`.

#### 4. Проверка обновлений следует за активным backend’ом

Теперь **Check for updates…** не предполагает фиксированный прокси, если используется bundled router.

Для `*.i2p` страниц релизов проверка обновлений по умолчанию идёт через **HTTP-прокси активного backend’а**, при этом `I2PCHAT_UPDATE_HTTP_PROXY` по-прежнему может переопределить поведение.

#### 5. В релизные сборки можно включать bundled `i2pd`

Packaging обновлён так, что релизные артефакты могут нести platform-specific binary встроенного роутера:

- **macOS arm64**
- **Linux x86_64**
- **Windows x64**

Первые слишком большие debug-подобные бинарники были заменены на компактные release-сборки, чтобы дистрибуция оставалась практичной.

### Совместимость

В **v1.2.0** намеренного разрыва wire-format нет.

- live chat остаётся **vNext-only**
- сценарии с системным роутером полностью сохраняются
- bundled-router режим только добавляет новый вариант и изолирован от system state

### Проверка

- `python3 -m unittest tests.test_release_index tests.test_router_settings tests.test_bundled_i2pd_config tests.test_bundled_i2pd_binary_resolution`
- `python3 -m py_compile i2pchat/gui/main_qt.py i2pchat/updates/release_index.py i2pchat/router/__init__.py i2pchat/router/settings.py i2pchat/router/runtime.py i2pchat/router/bundled_i2pd.py`
- локальный smoke test bundled-router на macOS:
  - SAM поднялся на `127.0.0.1:17656`
  - HTTP proxy поднялся на `127.0.0.1:14444`

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64-v1.2.0.zip` | Unzip → run I2PChat.exe |
| Linux | `I2PChat-linux-x86_64-v1.2.0.zip` | Unzip → chmod +x I2PChat.AppImage → run |
| macOS | `I2PChat-macOS-arm64-v1.2.0.zip` | Unzip → open I2PChat.app |
