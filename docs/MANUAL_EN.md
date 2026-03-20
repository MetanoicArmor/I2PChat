## I2PChat GUI Buttons Guide

### Profile selection dialog

When you start the GUI **without** passing a profile name, the profile chooser appears:

<img src="../screenshots/3.png" alt="Choose profile dialog" width="420" />

- window title: **I2PChat**;
- subtitle: **Choose profile**;
- hint: `Use default for a one-time session, or enter a name to save your identity.`
- **Profile:** field with a combo box (list + editable), current value `default`;
- helper text: `Click the list on the right to pick an existing profile, or type a new name above.`
- **Profiles folder: <path>** line (clickable, opens the folder);
- two buttons: **Cancel** and **OK**.

How to use it:

- **`default`**:
  - leave this value if you want a one‑time (TRANSIENT) profile without locking to a single peer;
- **pick from the list**:
  - open the drop‑down on the right and select an existing profile (a `.dat` file from the profiles directory);
- **enter a new name**:
  - type your own profile name (for example, `alice`);
  - allowed characters are `a-z`, `A-Z`, `0-9`, `.`, `_`, `-` (length 1..64);
  - the profile `.dat` is created immediately: keys are stored in `<name>.dat` (or the keyring), and **Lock to peer** appends the peer address and makes the profile one‑to‑one.

**Profiles directory** is OS-dependent: on **macOS** — `~/Library/Application Support/I2PChat`, on **Windows** — `%APPDATA%\I2PChat`, on **Linux** and others — `~/.i2pchat`. On Unix, the directory is restricted to the owner (0700).

#### Where the profile (.dat) folder is on your system

All profile files (e.g. `alice.dat`) are stored in a single folder that the app creates automatically:

| OS      | Path (folder with .dat files) |
|---------|-------------------------------|
| Windows | `%APPDATA%\I2PChat` — usually **`C:\Users\<your_username>\AppData\Roaming\I2PChat`** |
| macOS   | `~/Library/Application Support/I2PChat` |
| Linux   | `~/.i2pchat` |

You can open the folder directly from the profile chooser dialog — the **Profiles folder:** line is clickable on all OSes.

Current `.dat` format:

- line 1 — profile private key (if not stored in the system keyring);
- line 2 — pinned peer (`stored peer`) when you use `Lock to peer`.

If identity is stored in the keyring, the `.dat` file may contain only the pinned peer address.

After choosing or typing a name, press **OK** to continue or **Cancel** to close the dialog and abort starting the chat.

### 3. Main window (chat interface)

After you choose a profile, the main chat window opens:

<img src="../screenshots/1.png" alt="I2PChat main window: chat area, input, actions bar" width="900" />

- **Window title** — `I2PChat @ <profile_name>` (e.g. `I2PChat @ alice`).
- **Status row** — at the top, above the chat: `Net`, `Profile`, `Link`, `Peer`, `Stored`, `Secure`, `ACKdrop`.  
  On narrow windows it collapses to `Net`, `Link`, `Peer`, `Secure`, `ACKdrop`; hover for the full text.
- **Theme switch** — to the right of the status row (sun/moon icon). Toggles `ligth` and `night`.
- **Chat area** — shows your and peer messages, system notices, and file transfer progress. You can select and copy message text (right‑click or context menu).
- **Message input** — below the chat:
  - **Enter** inserts a new line;
  - **Shift+Enter** or **Ctrl+Enter** sends the message (or click `Send`).
- **Actions bar** — at the bottom: peer address, connection buttons, and the **`⋯`** menu (see section 4).

Connect to a peer first (enter address in the actions bar → **Connect**), then type in the input field to send messages.

### 4. Actions bar (connection and profiles)

The actions bar is located **at the bottom of the window**, below the message input area, and contains:

- the **peer address** input field;
- **`Connect`** and **`Disconnect`** buttons;
- a **`⋯` (More actions)** button that opens a menu with:
  - **Load profile (.dat)**;
  - **Send picture**;
  - **Send file**;
  - **Lock to peer**;
  - **Copy my address**.

All controls in the bar have the same height and are laid out in a single row.

#### 4.1. `⋯` (More actions) menu

Clicking the **`⋯`** button opens a popup menu with profile and connection actions:

<img src="../screenshots/2.png" alt="More actions menu (⋯): Load profile, Send picture/file, Lock to peer, Copy my address" width="320" />

- **Load profile (.dat)** — open a file dialog to load a profile from a `.dat` file.
- **Send picture** — send an image file to the connected peer.
- **Send file** — send any file to the connected peer.
- **Lock to peer** — bind the current profile to the connected peer (see section 4.7).
- **Copy my address** — copy your I2P destination to the clipboard.

#### 4.2. Peer address field

The `Peer .b32.i2p address` field is for the full destination of your peer:

```text
<base32>.b32.i2p
```

- You can type or paste the address manually.
- If the current profile is already locked to a peer and the field is empty, the address is filled from the stored value automatically.

#### 4.3. `Connect` button

The **`Connect`** button starts a connection to the address currently present in the peer field.

Logic:

1. If the field is **not empty**:
   - the GUI asks the core to connect to that peer (`connect_to_peer`).
2. If the field is **empty**:
   - if there is a stored peer (`stored_peer`), it is copied into the field and used;
   - otherwise a warning is shown:

   ```text
   Please enter peer address
   ```

After a successful connection:

- the status label is updated;
- incoming messages appear in the chat area;
- other events (file transfers, system/info messages) may start flowing over the network.

On first contact with a new peer signing key, a **Trust on First Use (TOFU)** dialog appears:

- it shows the peer address, a short fingerprint, and a public key prefix;
- the dialog explicitly warns that TOFU without OOB verification does not confirm identity;
- choose **Yes** to trust and pin the key, or **No** to abort the connection;
- for higher security, verify the fingerprint with your peer out‑of‑band.

#### 4.4. `Disconnect` button

The **`Disconnect`** button terminates the current connection to the peer.

After pressing it:

- the core initiates a disconnect;
- a system message about the disconnection may appear in the chat;
- the status label is updated accordingly.

#### 4.5. `Copy my address` action (`⋯` menu)

The **`Copy my address`** item in the **`⋯`** menu copies your own I2P destination to the clipboard.

<img src="../screenshots/4.png" alt="Getting your own address via Copy my address" width="900" />

Logic:

1. If the local destination is not yet initialised:
   - a dialog is shown (title **Copy My Addr**):

   ```text
   Local destination is not initialized yet.
   ```

2. If the destination is available:
   - a string of the form `<base32>.b32.i2p` is placed into the system clipboard;
   - a system message appears in the chat:

   ```text
   My address copied to clipboard.
   ```

This is convenient when you need to quickly share your address with the other side using an external channel.

#### 4.6. `Send file` action (`⋯` menu)

The **`Send file`** item in the **`⋯`** menu sends a file to the currently connected peer.

After selecting it:

1. A file chooser dialog opens (`Select file to send`).
2. If no path is selected, sending is cancelled.
3. If a file is selected:
   - the core starts the transfer (`send_file(path)`).

Transfer progress is displayed in the chat area as messages like:

```text
<filename>: <received>/<size> bytes
```

On the receiving side:

- an **`Incoming file`** dialog is shown first:
  - with the question `Accept incoming file?`;
  - plus filename and size information;
- if the user chooses **`No`**:
  - the temporary file is removed;
  - a message appears in the chat:

  ```text
  Incoming file rejected: <filename>
  ```
- if a file with the same name already exists in `downloads`, the new file is saved as `<name> (1).<ext>`, `<name> (2).<ext>`, etc. without overwriting.

The **`Send picture`** item works the same way but is intended for images (PNG/JPEG) and is shown inline in the chat.

#### 4.7. `Lock to peer` button

The **`Lock to peer`** button is **optional** – you can safely use I2PChat without it.  
By default, if you never lock, the profile works like an **email address**:

- **anyone** who knows your destination can write to this profile;
- you are free to connect to different peers over time.

When you do press **`Lock to peer`**, the profile becomes **bound to a single peer**:

- the peer address is stored in the profile `.dat` file in canonical form (line 1 — key, line 2 — peer; keyring setups may store only the peer);
- on subsequent runs with this profile, the stored peer will be reused automatically;
- connections from other addresses can be rejected by the core as “unauthorised”.

Rules and behaviour:

1. If the current profile is `default` (mode `TRANSIENT`):
   - a warning is shown:

   ```text
   Cannot lock in TRANSIENT mode. Restart with a profile name.
   ```

2. If the profile is already locked (`stored_peer` is not empty):
   - an information dialog is shown with the stored address.

3. If there is no verified peer address yet (`current_peer_addr` is empty):
   - a warning is shown:

   ```text
   Peer address not yet verified.
   Establish a connection first.
   ```

4. In all other cases:
   - `Lock to peer` is allowed only after cryptographic peer-address binding verification;
   - the file `<profile>.dat` in the profiles directory is created or updated (canonical format, no duplicate lines);
   - a system message appears in the chat:

   ```text
   Identity <profile> is now locked to this peer.
   ```

#### 4.8. `Load .dat` button

The **`Load .dat`** button lets you switch to another profile by picking an existing `.dat` file.

After pressing it:

1. The `Select profile (.dat)` dialog opens:
   - by default it points to the profiles directory (on Windows: `%APPDATA%\I2PChat`, on Linux: `~/.i2pchat`, on macOS: `~/Library/Application Support/I2PChat`);
   - it filters files using the `*.dat` mask.
2. If no file is chosen, the operation is cancelled.
3. If a file is chosen:
   - the base name without extension (`<base>`) is taken from the path;
   - the `.dat` file is copied into the profiles directory as `<base>.dat` (if not already there);
   - the profile is switched asynchronously:
     - the current core is cleanly shut down (`shutdown`);
     - the window title is updated to `I2PChat @ <profile_name>`;
     - a new core is created for this profile;
     - a new I2P session is initialised.

Using this button you can:

- quickly import an existing profile;
- switch between several profiles without restarting the application.

### 5. System notifications and sound

The GUI uses a system tray icon (`QSystemTrayIcon`) and, where supported,  
sound notifications (`QSoundEffect`) for incoming messages.

#### 5.1. System notifications

- When an incoming message from a peer (kind `peer`) is received, the `handle_notify` callback is invoked.
- If the window / application is **not active** (minimised or in the background):
  - a short title is built:
    - base text is `New message`;
    - if the peer address is known, it becomes `New message from <peer>`.
  - a native system notification (toast) is shown via `QSystemTrayIcon` for about 5 seconds.
- If the window is active, the GUI relies on the visual chat updates without extra pop‑ups.
- For incoming connections, a notification **Incoming connection** is shown with the peer address (when available).

#### 5.2. Sound notifications

- If the `QtMultimedia` module is available:
  - a `QSoundEffect` instance is created;
  - if `I2PCHAT_NOTIFY_SOUND` is set, the specified local audio file is used;
  - default volume is about 70%.
- For incoming messages when the window is not active:
  - a custom sound is played (if configured and available);
  - if playback fails, the fallback `QApplication.beep()` is used instead.

### 6. Typical usage scenarios

#### 6.1. First start and sending a message

1. Make sure your I2P router with SAM (`127.0.0.1:7656`) is running.
2. Start I2PChat depending on your platform:

   - **Windows**: unpack the release archive and run `I2PChat.exe`.
   - **Linux**: make the AppImage executable (`chmod +x I2PChat-x86_64.AppImage`) and run `./I2PChat-x86_64.AppImage`.
   - **macOS**: move `I2PChat.app` to `/Applications` (or any convenient folder) and open it as a normal app.

3. In the `Choose profile` dialog:
   - keep `default` or type your own profile name (for example, `alice`).
4. In the main window:
   - wait until the status row shows a working state;
   - if needed, copy your address via `⋯` → `Copy my address` and send it to your peer via another channel.
5. Once you have the peer address:
   - paste it into `Peer .b32.i2p address`;
   - press `Connect`.
6. After the connection is established:
   - type your message in the bottom input field;
   - press `Shift+Enter`, `Ctrl+Enter`, or click `Send` to send.
7. The new message will appear on the right side of the chat area as your outgoing message.

#### 6.2. Sending a file to a peer

1. Ensure you are connected to the peer (you pressed `Connect` and see no errors).
2. Open the **`⋯`** menu and choose **`Send file`**.
3. Pick the desired file in the dialog.
4. Watch progress messages in the chat:

   ```text
   <filename>: <received>/<size> bytes
   ```

On the receiving side:

- a confirmation dialog is shown;
- if the user rejects the file, it is deleted and a rejection message appears in the chat.

#### 6.3. Switching to a persistent profile and locking to a peer

1. Start I2PChat with a profile name (optionally via command‑line argument):

   - **Windows**: `I2PChat.exe myprofile`.
   - **Linux**: `./I2PChat-x86_64.AppImage myprofile`.
   - **macOS**: `open -a I2PChat --args myprofile`.

2. Connect to the desired peer using the address field and `Connect`.
3. Make sure the connection is active and messages are exchanged.
4. If you want this profile to behave like a **one‑to‑one channel** (only this peer may reach it), click **`Lock to peer`**:
   - make sure the profile is not `default`;
   - on success, you will see:

   ```text
   Identity myprofile is now locked to this peer.
   ```

5. On subsequent runs with the `myprofile` profile:
   - the status row will show `Stored: <address>`;
   - if the peer field is empty, the stored address will be auto‑filled;
   - connections from other peers will no longer be accepted for this profile.

#### 6.4. Importing an existing `.dat` profile

1. Make sure you have a profile file, for example `friend.dat`.
2. Start the GUI (with any profile or via `default`).
3. Click **`Load .dat`**.
4. In the file dialog, pick `friend.dat`:
   - the file will be copied into the profiles directory as `friend.dat` (if not already there);
   - the profile will automatically switch to `friend`;
   - the core will be restarted under the new profile.

### 7. Common GUI‑level issues

#### 7.1. No messages appear in the chat

Check the following:

- the status label:
  - make sure there are no errors related to SAM / I2P;
  - confirm that the state is not stuck at `initializing`;
- the peer address field:
  - the address must end with `.b32.i2p`;
  - there must be no extra spaces or characters;
- that you actually pressed `Connect` and there are no error messages (`ERROR`, `disconnect`) in the chat.

If everything looks correct but there is still no traffic, the problem is most likely in the **I2P/network layer**, not in the GUI.

#### 7.2. Unable to connect to a peer

Make sure that:

- your I2P router is running and the SAM port is reachable;
- the peer address is complete (including `.b32.i2p`);
- the peer is online and using a compatible client (legacy clients below `0.3.x`/`0.4.x` are not supported).

In this case the GUI will show the relevant system/error messages in the chat area.

#### 7.3. No incoming file prompts

When a file is incoming, the GUI should show an `Incoming file` dialog with the question `Accept incoming file?`.

If you do not see it:

- check whether another modal dialog is blocking it (it might be behind the main window);
- ensure the application is not stuck due to network issues.

#### 7.4. Copying message text does not work

Check:

- whether a message bubble is selected (click the bubble first);
- whether you use the standard copy shortcut:
  - `Ctrl+C` on Windows / Linux;
  - `Cmd+C` on macOS;
- you can also use the context menu (`Copy text` / `Copy with timestamp`).

### 8. Protocol metadata and padding

Even with post-handshake encryption, some transport metadata remains observable:

- frame type (`TYPE`);
- frame length (`LEN`);
- pre-handshake identity preface exchange.

To reduce length-based traffic analysis, encrypted mode uses a padding profile:

- default: `balanced` (pads to 128-byte buckets);
- optional: `off` (no padding).

Override via environment variable:

```bash
I2PCHAT_PADDING_PROFILE=off python main_qt.py
```

Trade-off: more padding lowers metadata correlation but increases bandwidth use.

### 9. Summary

The I2PChat GUI provides:

- a clear chat view with coloured bubbles;
- `ligth`/`night` themes and a unified cross‑platform look;
- an informative status row (Net/Link/Peer/Secure/ACKdrop);
- a convenient bar for managing profiles and connections;
- file and image sending;
- system and sound notifications for incoming messages.

For everyday use you typically only need to:

1. Run the I2PChat application (exe / AppImage / `.app`) with the desired profile.
2. Paste the peer address and press `Connect`.
3. Chat using the input field and `Send` button.
4. When needed, send files/images and use profile locking for a long‑term peer.
