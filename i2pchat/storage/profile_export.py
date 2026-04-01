"""
Profile export and import for I2PChat.

File format (.i2pchat-profile):
  4 bytes  — magic b"I2CP"
  2 bytes  — format version (big-endian uint16, currently 1)
  32 bytes — random salt for key derivation
  rest     — NaCl SecretBox ciphertext wrapping a UTF-8 JSON payload

JSON payload fields:
  version       — int, format version (1)
  export_ts     — str, ISO-8601 UTC timestamp
  dat_content   — str, base64-encoded .dat file bytes
  contacts      — any, contacts.json parsed object (v2 format)
  gui_settings  — any | null, parsed gui.json or null if not included

Key derivation:
  key = nacl.pwhash.argon2id.kdf(
      size=32, password=password_bytes, salt=salt,
      opslimit=MODERATE_OPS, memlimit=MODERATE_MEM)
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import struct
import tempfile
from datetime import datetime, timezone
from typing import Any, Literal, Optional

PROFILE_MAGIC = b"I2CP"
PROFILE_VERSION = 1
SALT_SIZE = 16  # argon2id requires exactly 16 bytes
HEADER_SIZE = 4 + 2 + 16  # magic + version + salt

EXPORT_WARNING = (
    "This archive contains your private I2P identity key. "
    "Store it in a secure location. "
    "Anyone with this file and the password can impersonate you on the I2P network."
)


def _derive_key(password: str | bytes, salt: bytes) -> bytes:
    """Derive a 32-byte key from a password using Argon2id via nacl.pwhash."""
    from nacl.pwhash import argon2id

    pw = password.encode("utf-8") if isinstance(password, str) else password
    return argon2id.kdf(
        32,
        pw,
        salt,
        opslimit=argon2id.OPSLIMIT_MODERATE,
        memlimit=argon2id.MEMLIMIT_MODERATE,
    )


def _read_dat(app_root: str, profile_name: str) -> bytes:
    from i2pchat.core.i2p_chat_core import resolve_existing_profile_file

    path = resolve_existing_profile_file(
        app_root, profile_name, f"{profile_name}.dat"
    )
    if not path:
        raise FileNotFoundError(
            f"Profile .dat not found for {profile_name!r} under {app_root!r}"
        )
    with open(path, "rb") as f:
        return f.read()


def _read_contacts(app_root: str, profile_name: str) -> Any:
    from i2pchat.core.i2p_chat_core import resolve_existing_profile_file

    path = resolve_existing_profile_file(
        app_root, profile_name, f"{profile_name}.contacts.json"
    )
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_gui_settings(profiles_dir: str) -> Any:
    """Read gui.json from profiles_dir or the parent ~/.i2pchat directory."""
    for candidate in [
        os.path.join(profiles_dir, "gui.json"),
        os.path.join(os.path.dirname(profiles_dir), "gui.json"),
    ]:
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
    return None


def export_profile(
    profile_name: str,
    password: str | bytes,
    profiles_dir: str,
    *,
    include_gui_settings: bool = True,
    output_path: Optional[str] = None,
) -> tuple[str, str]:
    """
    Export a profile to an encrypted .i2pchat-profile archive.

    Args:
        profile_name: name of the profile (without .dat extension)
        password: encryption password
        profiles_dir: application data root (same as ``get_profiles_dir()``)
        include_gui_settings: whether to include gui.json in the archive
        output_path: destination file path; defaults to profiles_dir/{profile_name}.i2pchat-profile

    Returns:
        (output_path, warning_message)

    Raises:
        FileNotFoundError: if the .dat file is missing
        ImportError: if pynacl is not installed
    """
    from nacl.secret import SecretBox

    dat_bytes = _read_dat(profiles_dir, profile_name)
    contacts = _read_contacts(profiles_dir, profile_name)
    gui_settings = _read_gui_settings(profiles_dir) if include_gui_settings else None

    payload: dict[str, Any] = {
        "version": PROFILE_VERSION,
        "export_ts": datetime.now(timezone.utc).isoformat(),
        "dat_content": base64.b64encode(dat_bytes).decode("ascii"),
        "contacts": contacts,
        "gui_settings": gui_settings,
    }
    plaintext = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")

    salt = secrets.token_bytes(SALT_SIZE)
    key = _derive_key(password, salt)
    box = SecretBox(key)
    ciphertext = bytes(box.encrypt(plaintext))

    header = PROFILE_MAGIC + struct.pack(">H", PROFILE_VERSION) + salt
    archive_bytes = header + ciphertext

    if output_path is None:
        output_path = os.path.join(
            os.path.abspath(profiles_dir), f"{profile_name}.i2pchat-profile"
        )

    _atomic_write_profile(output_path, archive_bytes)
    return output_path, EXPORT_WARNING


def _atomic_write_profile(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".profile_export.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _decrypt_archive(archive_path: str, password: str | bytes) -> dict[str, Any]:
    """
    Read and decrypt a .i2pchat-profile archive.

    Raises:
        ValueError: wrong password, corrupted archive, unsupported version, missing fields
    """
    from nacl.exceptions import CryptoError
    from nacl.secret import SecretBox

    with open(archive_path, "rb") as f:
        raw = f.read()

    if len(raw) < HEADER_SIZE:
        raise ValueError("Archive too short — file is corrupted")

    magic = raw[:4]
    if magic != PROFILE_MAGIC:
        raise ValueError(f"Invalid magic bytes: expected {PROFILE_MAGIC!r}, got {magic!r}")

    (version,) = struct.unpack(">H", raw[4:6])
    if version != PROFILE_VERSION:
        raise ValueError(f"Unsupported archive version: {version} (expected {PROFILE_VERSION})")

    salt = raw[6:22]
    ciphertext = raw[HEADER_SIZE:]

    if not ciphertext:
        raise ValueError("Archive has no ciphertext — file is corrupted")

    try:
        key = _derive_key(password, salt)
    except Exception as exc:
        raise ValueError(f"Key derivation failed: {exc}") from exc

    try:
        box = SecretBox(key)
        plaintext = bytes(box.decrypt(ciphertext))
    except CryptoError as exc:
        raise ValueError("Wrong password or corrupted archive") from exc

    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Corrupted payload — cannot parse JSON: {exc}") from exc

    _validate_payload(payload)
    return payload


def _validate_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Archive payload must be a JSON object")

    version = payload.get("version")
    if version != PROFILE_VERSION:
        raise ValueError(f"Unsupported payload version: {version!r} (expected {PROFILE_VERSION})")

    required = ("dat_content", "export_ts")
    for field in required:
        if field not in payload:
            raise ValueError(f"Archive is missing required field: {field!r}")

    dat_content = payload["dat_content"]
    if not isinstance(dat_content, str) or not dat_content:
        raise ValueError("Field 'dat_content' must be a non-empty string")

    try:
        base64.b64decode(dat_content, validate=True)
    except Exception as exc:
        raise ValueError(f"Field 'dat_content' is not valid base64: {exc}") from exc


ConflictStrategy = Literal["error", "rename", "overwrite"]


def import_profile(
    archive_path: str,
    password: str | bytes,
    profiles_dir: str,
    conflict_strategy: ConflictStrategy = "error",
    *,
    restore_gui_settings: bool = False,
) -> str:
    """
    Decrypt and restore a .i2pchat-profile archive.

    Args:
        archive_path: path to the .i2pchat-profile file
        password: decryption password
        profiles_dir: application data root; files are written under ``profiles/<final_name>/``
        conflict_strategy: what to do if the profile already exists:
            "error"    — raise FileExistsError
            "rename"   — suffix with _1, _2, ... (allocate_unique_profile_name)
            "overwrite" — replace existing files
        restore_gui_settings: if True, also write gui.json from the archive

    Returns:
        The final profile name (without .dat extension)

    Raises:
        ValueError: wrong password, corrupted archive, missing fields, version mismatch
        FileExistsError: if conflict_strategy="error" and the profile already exists
        FileNotFoundError: if archive_path does not exist
    """
    from i2pchat.core.i2p_chat_core import allocate_unique_profile_name

    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    from i2pchat.core.i2p_chat_core import get_profile_data_dir, nested_profile_dat_path

    # Decrypt and validate before touching the filesystem (all-or-nothing)
    payload = _decrypt_archive(archive_path, password)

    dat_bytes = base64.b64decode(payload["dat_content"])
    contacts = payload.get("contacts")
    gui_settings = payload.get("gui_settings") if restore_gui_settings else None

    # Infer profile name from archive filename
    archive_basename = os.path.basename(archive_path)
    if archive_basename.endswith(".i2pchat-profile"):
        base_name = archive_basename[: -len(".i2pchat-profile")]
    else:
        base_name = archive_basename.split(".")[0] or "imported"

    # Sanitise: keep only valid characters
    import re
    base_name = re.sub(r"[^A-Za-z0-9._-]", "_", base_name)[:64] or "imported"

    app_root = os.path.abspath(profiles_dir)
    os.makedirs(app_root, exist_ok=True)
    dat_dest = nested_profile_dat_path(app_root, base_name)

    if conflict_strategy == "error":
        if os.path.exists(dat_dest):
            raise FileExistsError(f"Profile already exists: {dat_dest}")
        final_name = base_name
    elif conflict_strategy == "rename":
        final_name = allocate_unique_profile_name(app_root, base_name)
    elif conflict_strategy == "overwrite":
        final_name = base_name
    else:
        raise ValueError(f"Unknown conflict_strategy: {conflict_strategy!r}")

    # Write files atomically — contacts.json only if present in archive
    pdir = get_profile_data_dir(final_name, create=True, app_root=app_root)
    dat_path = os.path.join(pdir, f"{final_name}.dat")
    _atomic_write_profile(dat_path, dat_bytes)

    if contacts is not None:
        contacts_path = os.path.join(pdir, f"{final_name}.contacts.json")
        contacts_bytes = json.dumps(contacts, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        _atomic_write_profile(contacts_path, contacts_bytes)

    if gui_settings is not None:
        gui_path = os.path.join(profiles_dir, "gui.json")
        gui_bytes = json.dumps(gui_settings, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        _atomic_write_profile(gui_path, gui_bytes)

    return final_name
