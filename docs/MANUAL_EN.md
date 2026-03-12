## I2PChat GUI Buttons Guide

### Profile selection dialog

When you start the GUI **without** passing a profile name, the **Select profile** dialog appears:

<img src="../screenshots/3.png" alt="Select profile dialog" width="420" />

- window title: **Select profile**;
- main text:
  - `Profile name (default = TRANSIENT).`
  - `Pick from the list,`
  - `or type a new name to save keys:`
- below the text there is a combo box / editable field with the current value `default`;
- at the bottom there are two buttons: **Cancel** and **OK**.

How to use it:

- **`default`**:
  - leave this value if you want a temporary (TRANSIENT) profile without locking to a single peer;
- **pick from the list**:
  - open the drop‑down on the right and select an existing profile (a `.dat` file previously created in `~/.i2pchat`);
- **enter a new name**:
  - type your own profile name (for example, `alice`);
  - when you later use the **Lock to peer** function, keys and metadata will be saved to `~/.i2pchat/<name>.dat`.

After choosing or typing a name, press **OK** to continue or **Cancel** to close the dialog and abort starting the chat.

### 4. Actions bar (connection and profiles)

The actions bar is located **at the bottom of the window**, below the message input area, and contains:

- the **`Load .dat`** button;
- the **peer address** input field;
- the following buttons:
  - **`Connect`**;
  - **`Disconnect`**;
  - **`Send File`**;
  - **`Lock to peer`**;
  - **`Copy My Addr`**.

All controls in the bar have the same height and are laid out in a single row.

#### 4.1. Peer address field

The `Peer .b32.i2p address` field is for the full destination of your peer:

```text
<base32>.b32.i2p
```

- You can type or paste the address manually.
- If the current profile is already locked to a peer and the field is empty, the address is filled from the stored value automatically.

#### 4.2. `Connect` button

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

#### 4.3. `Disconnect` button

The **`Disconnect`** button terminates the current connection to the peer.

After pressing it:

- the core initiates a disconnect;
- a system message about the disconnection may appear in the chat;
- the status label is updated accordingly.

#### 4.4. `Copy My Addr` button

The **`Copy My Addr`** button copies your own I2P destination to the clipboard.

<img src="../screenshots/2.png" alt="Getting your own address via Copy My Addr" width="700" />

Logic:

1. If the local destination is not yet initialised:
   - a dialog is shown:

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

#### 4.5. `Send File` button

The **`Send File`** button sends a file to the currently connected peer.

After pressing it:

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

#### 4.6. `Lock to peer` button

The **`Lock to peer`** button permanently associates the current profile with the currently verified peer.  
In practice this means:

- the peer address is stored in the profile `.dat` file;
- on subsequent runs with this profile, the stored peer will be reused automatically.

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
   - the file `~/.i2pchat/<profile>.dat` is created or updated and the peer address is appended there;
   - a system message appears in the chat:

   ```text
   Identity <profile> is now locked to this peer.
   ```

#### 4.7. `Load .dat` button

The **`Load .dat`** button lets you switch to another profile by picking an existing `.dat` file.

After pressing it:

1. The `Select profile (.dat)` dialog opens:
   - by default it points to the profiles directory `~/.i2pchat`;
   - it filters files using the `*.dat` mask.
2. If no file is chosen, the operation is cancelled.
3. If a file is chosen:
   - the base name without extension (`<base>`) is taken from the path;
   - the `.dat` file is copied to `~/.i2pchat/<base>.dat` (if it is not already there);
   - the profile is switched asynchronously:
     - the current core is cleanly shut down (`shutdown`);
     - the window title is updated to `I2PChat • <profile_name>`;
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

#### 5.2. Sound notifications

- If the `QtMultimedia` module is available:
  - a `QSoundEffect` instance is created;
  - on macOS one of the standard system sounds is used (`/System/Library/Sounds/Glass.aiff`);
  - default volume is about 70%.
- For incoming messages when the window is not active:
  - a soft system sound is played;
  - if playback fails, the fallback `QApplication.beep()` is used instead.

### 6. Typical usage scenarios

#### 6.1. First start and sending a message

1. Make sure your I2P router with SAM (`127.0.0.1:7656`) is running.
2. Start I2PChat depending on your platform:

   - **Windows**: unpack the release archive and run `I2PChat.exe`.
   - **Linux**: make the AppImage executable (`chmod +x I2PChat-x86_64.AppImage`) and run `./I2PChat-x86_64.AppImage`.
   - **macOS**: move `I2PChat.app` to `/Applications` (or any convenient folder) and open it as a normal app.

3. In the `Select profile` dialog:
   - keep `default` or type your own profile name (for example, `alice`).
4. In the main window:
   - wait until the status label shows a working state;
   - if needed, copy your address using `Copy My Addr` and send it to your peer via another channel.
5. Once you have the peer address:
   - paste it into `Peer .b32.i2p address`;
   - press `Connect`.
6. After the connection is established:
   - type your message in the bottom input field;
   - press `Enter` or click `Send`.
7. The new message will appear on the right side of the chat area as your outgoing message.

#### 6.2. Sending a file to a peer

1. Ensure you are connected to the peer (you pressed `Connect` and see no errors).
2. Click **`Send File`**.
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
4. Click **`Lock to peer`**:
   - make sure the profile is not `default`;
   - on success, you will see:

   ```text
   Identity myprofile is now locked to this peer.
   ```

5. On subsequent runs with the `myprofile` profile:
   - the status label will show `Stored peer` with the address;
   - if the peer field is empty, the stored address will be auto‑filled.

#### 6.4. Importing an existing `.dat` profile

1. Make sure you have a profile file, for example `friend.dat`.
2. Start the GUI (with any profile or via `default`).
3. Click **`Load .dat`**.
4. In the file dialog, pick `friend.dat`:
   - the file will be copied to `~/.i2pchat/friend.dat` (if it is not already there);
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
- the peer is online and using a compatible client.

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

### 8. Summary

The I2PChat GUI provides:

- a clear chat view with coloured bubbles;
- a convenient bar for managing profiles and connections;
- file and text‑image sending;
- system and sound notifications for incoming messages.

For everyday use you typically only need to:

1. Run the I2PChat application (exe / AppImage / `.app`) with the desired profile.
2. Paste the peer address and press `Connect`.
3. Chat using the input field and `Send` button.
4. When needed, send files/images and use profile locking for a long‑term peer.

