"""
Password-protected backup/export helpers for profiles and encrypted history.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import (
    get_profile_data_dir,
    migrate_legacy_profile_files_if_needed,
)
from i2pchat.storage import chat_history as chat_history_mod
from i2pchat.storage.blindbox_state import atomic_write_bytes


BUNDLE_MAGIC = b"I2PBKP1"
BUNDLE_VERSION = 1
SALT_SIZE = 32


class BackupError(ValueError):
    """Raised when a backup bundle is invalid or cannot be decrypted."""


@dataclass
class ExportSummary:
    bundle_type: str
    profile: str
    file_count: int
    history_files: int
    sidecar_files: int


@dataclass
class ImportSummary:
    bundle_type: str
    source_profile: str
    target_profile: str
    restored_files: int
    history_files: int
    skipped_files: int = 0


def _require_nacl() -> None:
    if not crypto.NACL_AVAILABLE:
        raise BackupError("PyNaCl is required for encrypted backup import/export")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    raw = (passphrase or "").encode("utf-8")
    if not raw:
        raise BackupError("Backup passphrase is required")
    return hashlib.scrypt(raw, salt=salt, n=2**14, r=8, p=1, dklen=32)


def _encrypt_payload(payload: bytes, passphrase: str) -> bytes:
    _require_nacl()
    salt = os.urandom(SALT_SIZE)
    key = _derive_backup_key(passphrase, salt)
    ciphertext = crypto.encrypt_message(key, payload)
    return BUNDLE_MAGIC + bytes([BUNDLE_VERSION]) + salt + ciphertext


def _decrypt_payload(raw: bytes, passphrase: str) -> bytes:
    _require_nacl()
    min_len = len(BUNDLE_MAGIC) + 1 + SALT_SIZE + 1
    if len(raw) < min_len:
        raise BackupError("Backup bundle is too short")
    if raw[: len(BUNDLE_MAGIC)] != BUNDLE_MAGIC:
        raise BackupError("Unsupported backup bundle magic")
    version = raw[len(BUNDLE_MAGIC)]
    if version != BUNDLE_VERSION:
        raise BackupError(f"Unsupported backup bundle version: {version}")
    salt_start = len(BUNDLE_MAGIC) + 1
    salt_end = salt_start + SALT_SIZE
    salt = raw[salt_start:salt_end]
    ciphertext = raw[salt_end:]
    key = _derive_backup_key(passphrase, salt)
    plaintext = crypto.decrypt_message(key, ciphertext)
    if plaintext is None:
        raise BackupError("Failed to decrypt backup bundle (wrong passphrase or corrupted data)")
    return plaintext


def _safe_member_name(name: str) -> str:
    cleaned = name.replace("\\", "/").strip("/")
    if not cleaned or cleaned.startswith("../") or "/../" in cleaned:
        raise BackupError(f"Unsafe bundle path: {name!r}")
    return cleaned


def _build_tar_payload(manifest: dict[str, object], files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        manifest_info.mtime = int(time.time())
        tf.addfile(manifest_info, io.BytesIO(manifest_bytes))

        for logical_name, content in sorted(files.items()):
            member_name = f"payload/{_safe_member_name(logical_name)}"
            info = tarfile.TarInfo(member_name)
            info.size = len(content)
            info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _read_tar_payload(payload: bytes) -> tuple[dict[str, object], dict[str, bytes]]:
    buf = io.BytesIO(payload)
    try:
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            manifest_member = tf.getmember("manifest.json")
            manifest_file = tf.extractfile(manifest_member)
            if manifest_file is None:
                raise BackupError("Backup bundle manifest is unreadable")
            manifest_raw = manifest_file.read()
            manifest = json.loads(manifest_raw.decode("utf-8"))
            if not isinstance(manifest, dict):
                raise BackupError("Backup manifest must be a JSON object")

            files: dict[str, bytes] = {}
            for member in tf.getmembers():
                if not member.isfile() or member.name == "manifest.json":
                    continue
                if not member.name.startswith("payload/"):
                    raise BackupError(f"Unexpected bundle member: {member.name}")
                logical_name = _safe_member_name(member.name[len("payload/") :])
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise BackupError(f"Cannot read bundle member: {member.name}")
                files[logical_name] = extracted.read()
    except (tarfile.TarError, OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Failed to parse backup payload: {exc}") from exc
    return manifest, files


def _validate_manifest_files(
    manifest: dict[str, object],
    files: dict[str, bytes],
) -> tuple[str, str]:
    bundle_type = str(manifest.get("bundle_type", "")).strip().lower()
    source_profile = str(manifest.get("source_profile", "")).strip()
    if bundle_type not in {"profile", "history"}:
        raise BackupError("Unsupported backup bundle type")
    if not source_profile:
        raise BackupError("Backup manifest is missing source profile")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise BackupError("Backup manifest is missing entries list")
    expected_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("Backup manifest entry must be an object")
        name = _safe_member_name(str(entry.get("path", "")))
        expected_names.add(name)
        if name not in files:
            raise BackupError(f"Backup payload is missing file: {name}")
        content = files[name]
        expected_size = int(entry.get("size", -1))
        if expected_size != len(content):
            raise BackupError(f"Backup payload size mismatch for {name}")
        expected_sha = str(entry.get("sha256", "")).lower()
        if expected_sha != _sha256_hex(content):
            raise BackupError(f"Backup payload checksum mismatch for {name}")
    if set(files) != expected_names:
        extra = sorted(set(files) - expected_names)
        raise BackupError(f"Backup payload has unexpected files: {extra}")
    return bundle_type, source_profile


def _profile_dat_path(app_root: str, profile: str) -> str:
    migrate_legacy_profile_files_if_needed(app_root=app_root, profile=profile)
    return os.path.join(
        get_profile_data_dir(profile, create=False, app_root=app_root),
        f"{profile}.dat",
    )


def _collect_optional_file(path: str) -> Optional[bytes]:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def list_history_files(app_root: str, profile: str) -> list[str]:
    migrate_legacy_profile_files_if_needed(app_root=app_root, profile=profile)
    pdir = get_profile_data_dir(profile, create=False, app_root=app_root)
    return chat_history_mod.list_history_file_paths(
        pdir, profile, app_data_root=app_root
    )


def _collect_profile_bundle_files(
    app_root: str,
    profile: str,
    *,
    include_history: bool,
) -> tuple[dict[str, bytes], int, int]:
    files: dict[str, bytes] = {}
    sidecar_count = 0
    history_count = 0

    migrate_legacy_profile_files_if_needed(app_root=app_root, profile=profile)
    pdir = get_profile_data_dir(profile, create=False, app_root=app_root)

    profile_dat = _collect_optional_file(_profile_dat_path(app_root, profile))
    if profile_dat is None:
        raise BackupError(f"Profile data file not found: {profile}.dat")
    files["profile.dat"] = profile_dat

    optional_sidecars = [
        (f"{profile}.contacts.json", "contacts.json"),
        (f"{profile}.compose_drafts.json", "compose_drafts.json"),
    ]
    for file_name, logical_name in optional_sidecars:
        content = _collect_optional_file(os.path.join(pdir, file_name))
        if content is None:
            continue
        files[logical_name] = content
        sidecar_count += 1

    blindbox_prefix = f"{profile}.blindbox."
    try:
        listing = sorted(os.listdir(pdir))
    except FileNotFoundError:
        listing = []
    for name in listing:
        if not (name.startswith(blindbox_prefix) and name.endswith(".json")):
            continue
        with open(os.path.join(pdir, name), "rb") as f:
            files[f"blindbox/{name[len(profile) + 1:]}"] = f.read()
        sidecar_count += 1

    if include_history:
        for path in list_history_files(app_root, profile):
            name = os.path.basename(path)
            suffix = name[len(f"{profile}.history.") :]
            with open(path, "rb") as f:
                files[f"history/{suffix}"] = f.read()
            history_count += 1

    return files, history_count, sidecar_count


def _collect_history_bundle_files(
    app_root: str,
    profile: str,
) -> tuple[dict[str, bytes], int]:
    files: dict[str, bytes] = {}
    count = 0
    for path in list_history_files(app_root, profile):
        name = os.path.basename(path)
        suffix = name[len(f"{profile}.history.") :]
        with open(path, "rb") as f:
            files[f"history/{suffix}"] = f.read()
        count += 1
    if count == 0:
        raise BackupError(f"No saved history files found for profile {profile!r}")
    return files, count


def _build_manifest(
    *,
    bundle_type: str,
    profile: str,
    files: dict[str, bytes],
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for logical_name, content in sorted(files.items()):
        if logical_name == "profile.dat":
            kind = "profile_dat"
        elif logical_name.startswith("history/"):
            kind = "history"
        elif logical_name.startswith("blindbox/"):
            kind = "blindbox"
        else:
            kind = "sidecar"
        entries.append(
            {
                "path": logical_name,
                "kind": kind,
                "size": len(content),
                "sha256": _sha256_hex(content),
            }
        )
    return {
        "bundle_type": bundle_type,
        "format_version": BUNDLE_VERSION,
        "source_profile": profile,
        "created_utc": int(time.time()),
        "entries": entries,
    }


def export_profile_bundle(
    bundle_path: str,
    profiles_dir: str,
    profile: str,
    passphrase: str,
    *,
    include_history: bool = True,
) -> ExportSummary:
    files, history_count, sidecar_count = _collect_profile_bundle_files(
        profiles_dir,
        profile,
        include_history=include_history,
    )
    manifest = _build_manifest(bundle_type="profile", profile=profile, files=files)
    payload = _build_tar_payload(manifest, files)
    atomic_write_bytes(bundle_path, _encrypt_payload(payload, passphrase))
    return ExportSummary(
        bundle_type="profile",
        profile=profile,
        file_count=len(files),
        history_files=history_count,
        sidecar_files=sidecar_count,
    )


def export_history_bundle(
    bundle_path: str,
    profiles_dir: str,
    profile: str,
    passphrase: str,
) -> ExportSummary:
    files, history_count = _collect_history_bundle_files(profiles_dir, profile)
    manifest = _build_manifest(bundle_type="history", profile=profile, files=files)
    payload = _build_tar_payload(manifest, files)
    atomic_write_bytes(bundle_path, _encrypt_payload(payload, passphrase))
    return ExportSummary(
        bundle_type="history",
        profile=profile,
        file_count=len(files),
        history_files=history_count,
        sidecar_files=0,
    )


def _load_bundle(bundle_path: str, passphrase: str) -> tuple[dict[str, object], dict[str, bytes], str, str]:
    try:
        with open(bundle_path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise BackupError(f"Failed to read backup bundle: {exc}") from exc
    payload = _decrypt_payload(raw, passphrase)
    manifest, files = _read_tar_payload(payload)
    bundle_type, source_profile = _validate_manifest_files(manifest, files)
    return manifest, files, bundle_type, source_profile


def import_profile_bundle(
    bundle_path: str,
    profiles_dir: str,
    passphrase: str,
    *,
    requested_profile: Optional[str] = None,
) -> ImportSummary:
    from i2pchat.core.i2p_chat_core import (
        ensure_valid_profile_name,
        import_profile_dat_atomic,
    )

    _manifest, files, bundle_type, source_profile = _load_bundle(bundle_path, passphrase)
    if bundle_type != "profile":
        raise BackupError("This backup bundle does not contain a full profile export")
    base_profile = ensure_valid_profile_name(requested_profile or source_profile)
    profile_dat = files.get("profile.dat")
    if profile_dat is None:
        raise BackupError("Profile bundle is missing profile.dat")

    os.makedirs(profiles_dir, exist_ok=True)
    restored_files = 0
    history_files = 0
    with tempfile.TemporaryDirectory() as td:
        tmp_profile = os.path.join(td, f"{base_profile}.dat")
        atomic_write_bytes(tmp_profile, profile_dat)
        target_profile = import_profile_dat_atomic(tmp_profile, profiles_dir, base_profile)
    restored_files += 1

    pdir = get_profile_data_dir(target_profile, create=True, app_root=profiles_dir)
    for logical_name, content in sorted(files.items()):
        if logical_name == "profile.dat":
            continue
        if logical_name == "contacts.json":
            dest_name = f"{target_profile}.contacts.json"
        elif logical_name == "compose_drafts.json":
            dest_name = f"{target_profile}.compose_drafts.json"
        elif logical_name.startswith("blindbox/"):
            suffix = logical_name[len("blindbox/") :]
            dest_name = f"{target_profile}.{suffix}"
        elif logical_name.startswith("history/"):
            suffix = logical_name[len("history/") :]
            dest_name = f"{target_profile}.history.{suffix}"
            history_files += 1
        else:
            raise BackupError(f"Unexpected file in profile bundle: {logical_name}")
        atomic_write_bytes(os.path.join(pdir, dest_name), content)
        restored_files += 1

    return ImportSummary(
        bundle_type="profile",
        source_profile=source_profile,
        target_profile=target_profile,
        restored_files=restored_files,
        history_files=history_files,
    )


def import_history_bundle(
    bundle_path: str,
    profiles_dir: str,
    target_profile: str,
    passphrase: str,
    *,
    conflict_mode: str = "skip",
) -> ImportSummary:
    from i2pchat.core.i2p_chat_core import ensure_valid_profile_name

    _manifest, files, bundle_type, source_profile = _load_bundle(bundle_path, passphrase)
    if bundle_type != "history":
        raise BackupError("This backup bundle does not contain a history export")
    target_profile = ensure_valid_profile_name(target_profile)
    if conflict_mode not in {"skip", "overwrite"}:
        raise BackupError(f"Unsupported history import conflict mode: {conflict_mode}")

    os.makedirs(profiles_dir, exist_ok=True)
    pdir = get_profile_data_dir(target_profile, create=True, app_root=profiles_dir)
    restored_files = 0
    skipped_files = 0
    history_files = 0
    for logical_name, content in sorted(files.items()):
        if not logical_name.startswith("history/"):
            raise BackupError(f"Unexpected file in history bundle: {logical_name}")
        suffix = logical_name[len("history/") :]
        dest_path = os.path.join(pdir, f"{target_profile}.history.{suffix}")
        history_files += 1
        if os.path.exists(dest_path) and conflict_mode == "skip":
            skipped_files += 1
            continue
        atomic_write_bytes(dest_path, content)
        restored_files += 1

    return ImportSummary(
        bundle_type="history",
        source_profile=source_profile,
        target_profile=target_profile,
        restored_files=restored_files,
        history_files=history_files,
        skipped_files=skipped_files,
    )
