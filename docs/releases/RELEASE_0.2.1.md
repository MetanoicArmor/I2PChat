## 📦 Stable Update

Stable release with file and image transfer improvements, delivery confirmation, and UI updates. **Compatible with v0.2.0** (protocol v2).

---

### ✨ New Features

#### File and image transfer
- **Image support**: send and receive images (PNG, JPEG), “Send Pic” button, inline preview in chat
- **Delivery confirmation**: receiver sends FILE_ACK; sender sees checkmarks (✓/✓✓) for files and images, same as for messages
- **File rejection**: when the receiver declines a file, the sender gets a REJECT_FILE notification
- **Progress**: progress bar when sending and receiving files/images; progress every 4 KB for small transfers; checkmark shadow on white images for readability
- **Files**: removed duplicate “File sent/received” messages; “File received” includes a clickable “Open downloads folder” link; profile dialog has a clickable profiles folder (like “Open downloads” in chat)
- **Transfer cancel**: cancel sync (ABORT_FILE) between peers; PNG/JPEG only for images in the UI

#### Reliability and protocol
- **HMAC**: explicit UTF-8 encoding for cross‑platform compatibility; disconnect on integrity failure; logging of msg_type/body_len
- **Timeouts and disconnects**: fixed idle disconnect; disconnect on connection loss during transfer; keepalive to avoid timeout; no timeout during file transfer
- **Connection**: immediate “Online!” and non‑blocking accept_loop start
- **Core**: parse first message as frame (S+len+body), not readline; single active receive_loop; preserve Unicode filenames (Cyrillic, etc.)

#### Interface
- **Window**: centered profile buttons, **Cmd+Q** and **Quit** menu, title without version, @ separator
- **Theme**: dark theme for QMessageBox dialogs
- **Status**: clear Wait / Ready messages when connecting; animated progress bar in chat; “Starting I2P session” instead of “Building”

#### Build and environment
- **.gitignore**: added build/, dist/, __pycache__/, .DS_Store, .cursor
- Build with **Python 3.14+**, PyInstaller; cross‑platform scripts (macOS, Linux, Windows)

---

### 📸 Screenshots

**File transfer (sending)**

<img src="https://github.com/MetanoicArmor/I2PChat/raw/main/screenshots/4.png" width="700" alt="File transfer" />

**Received image in chat**

<img src="https://github.com/MetanoicArmor/I2PChat/raw/main/screenshots/5.png" width="700" alt="Received image" />

---

### 🌐 Cross-platform I2P Chat Client

**One app. Three platforms. No Python required.**

| Platform | Download | Launch |
|----------|----------|--------|
| Windows | `I2PChat-windows-x64.zip` | Unzip → run I2PChat.exe |
| macOS | `I2PChat-macOS-arm64.zip` | Unzip → open I2PChat.app |
| Linux | `I2PChat-x86_64.AppImage` | chmod +x → run |
