# English tooltip strings for menus and context actions (wording aligned with docs/MANUAL_EN.md).

from __future__ import annotations

# --- More actions (⋯) ---
TT_MORE_ACTIONS_BUTTON = (
    "Open the ⋯ menu: load profile, send picture or file, backups, BlindBox, lock, "
    "history, privacy, notifications, and more."
)

TT_LOAD_PROFILE_DAT = (
    "Open a file dialog to load a profile from a .dat file. "
    "The file is copied into your profiles folder and the app switches to that profile."
)

TT_SEND_PICTURE = (
    "Send an image file (PNG, JPEG, or WebP) to the connected peer; "
    "images appear inline in the chat."
)

TT_SEND_FILE = (
    "Send any file to the connected peer via the file picker; "
    "progress and completion appear in the chat."
)

TT_BLINDBOX_DIAGNOSTICS = (
    "Textual summary of BlindBox / offline routing and replica health; "
    "complements the status row."
)

TT_BLINDBOX_REPLICA_EDITOR = (
    "What: TCP addresses of Blind Box replica servers — shared storage where I2PChat places "
    "encrypted blobs for delayed delivery when there is no live chat session.\n"
    "Why: you and your locked peer should normally use the same pool so offline-queued "
    "messages can be fetched later.\n"
    "Here: one endpoint per line (e.g. *.b32.i2p:19444 or 127.0.0.1:19444). "
    "Every replica you use must appear in this list first; optional line-protocol secrets "
    "for specific replicas are set in the field below (not a second server list). "
    "Release builds include a default public pair unless overridden by env, a defaults file, "
    "or this saved list. Lines starting with # are ignored when you save."
)

TT_BLINDBOX_REPLICA_AUTH_EDITOR = (
    "Optional shared secret per replica endpoint for custom Blind Box servers that require "
    "authentication (same wire format as the local replica: token as an extra argument on "
    "PUT/GET). Set the same value as BLINDBOX_AUTH_TOKEN in the server environment.\n"
    "One line per protected replica: type the endpoint exactly as in the list above, press "
    "the Tab key once, then the secret (not spaces instead of Tab). "
    "Replicas without a token need no line here (e.g. public defaults). "
    "Lines starting with # are ignored. Only keys that match the endpoint list above are kept. "
    "This does not replace trust in the I2P destination — it only gates the TCP line protocol."
)

TT_BLINDBOX_REPLICA_EDITOR_ENV_LOCKED = (
    "Same role as above: Blind Box replicas are the shared servers used for offline queueing. "
    "This field is read-only because the list is set outside the app — environment variables, "
    "a global defaults file, or local-auto fallback. See the manual for Blind Box / replica options."
)

TT_BLINDBOX_REPLICA_EDITOR_TRANSIENT_PROFILE = (
    "These addresses are Blind Box replica servers — shared storage for offline-queued messages "
    "when there is no live chat session. The built-in transient (default) profile cannot save "
    "a list here; load or create a named profile to edit. One endpoint per line; "
    "lines starting with # are ignored on save."
)

TT_EXPORT_PROFILE_BACKUP = (
    "Create a password-protected bundle of the current profile (.dat and supported sidecar data)."
)

TT_IMPORT_PROFILE_BACKUP = (
    "Restore a password-protected profile backup; "
    "avoids name collisions by picking a free profile name when needed."
)

TT_EXPORT_HISTORY_BACKUP = "Export encrypted per-peer history files only (no full profile)."

TT_IMPORT_HISTORY_BACKUP = (
    "Restore history from a backup; "
    "you can overwrite matching files or add only missing ones."
)

TT_CHECK_UPDATES = (
    "Compare this app’s version to release files on the I2P project site. "
    "For .i2p URLs the check uses the I2P HTTP proxy at http://127.0.0.1:4444 "
    "when no system http_proxy is set; override with I2PCHAT_UPDATE_HTTP_PROXY if needed."
)

TT_OPEN_APP_DIR = "Open the app data directory in your system file manager."

TT_LOCK_TO_PEER = (
    "Bind this named profile to the connected peer: the address is stored in the profile .dat, "
    "and later sessions reuse it. Not available in TRANSIENT (default) mode. "
    "Requires a verified connection first."
)

TT_FORGET_PINNED_PEER_KEY = (
    "Remove the saved TOFU signing-key pin for the current peer. "
    "Use after a legitimate key rotation or to re-run TOFU on next connect."
)

TT_COPY_MY_ADDRESS = "Copy your I2P destination to the clipboard so you can share it with a peer."

TT_CHAT_HISTORY_TOGGLE = (
    "When ON, new messages from the current secure session are saved to local encrypted history. "
    "When OFF, new messages are not written to disk. "
    "The label shows the current state."
)

TT_CLEAR_HISTORY = "Delete the encrypted history file for the current peer only."

TT_HISTORY_RETENTION = (
    "Set maximum messages per peer and maximum age in days; "
    "0 days means limit by message count only."
)

TT_PRIVACY_MODE_TOGGLE = (
    "When ON: tray toasts omit message body text (title may still name the peer); "
    "while this window is focused, tray toasts and notification sounds are suppressed "
    "(including for other chats). When OFF, those behaviours are disabled."
)

TT_NOTIFICATION_SOUND_TOGGLE = (
    "When OFF, incoming notification sounds are never played; "
    "your custom sound path is kept. Privacy mode can still mute sound while this window is focused."
)

# --- Saved peers context ---
TT_EDIT_NAME_NOTE = (
    "Edit local display name and note for this contact only; "
    "does not change the peer’s keys or address."
)

TT_CONTACT_DETAILS = "Show address, TOFU fingerprint, and optional Remove pin for this peer."

TT_REMOVE_SAVED_PEER = (
    "Remove this peer from Saved peers; "
    "you can also delete encrypted history, TOFU pin, profile lock, and BlindBox state where applicable."
)

# --- Chat message / bubble context ---
TT_OPEN_IMAGE_OR_FILE = "Open this file with the system default application."
TT_COPY_PATH = "Copy the full file path to the clipboard."
TT_OPEN_FOLDER = "Open the folder containing the received file."
TT_COPY_FOLDER_PATH = "Copy the folder path to the clipboard."
TT_COPY_TEXT = "Copy the message text without metadata."
TT_COPY_WITH_TIMESTAMP = "Copy the message text including sender and timestamp."
TT_REPLY = "Quote this message in the compose field for a reply."
TT_RETRY = "Retry sending this failed message when supported."
TT_COPY_FILENAME = "Copy the transfer filename to the clipboard."
TT_DELIVERY_DETAIL = "Delivery or routing detail for this message."

# --- Compose / search field edit menu ---
TT_UNDO = "Undo the last edit."
TT_REDO = "Redo the last undone edit."
TT_CUT = "Cut the selection to the clipboard."
TT_COPY = "Copy the selection to the clipboard."
TT_PASTE = "Paste from the clipboard (text or image where supported)."
TT_DELETE = "Delete the selected text."
TT_SELECT_ALL = "Select all text in the field."
