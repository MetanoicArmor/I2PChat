"""
Per-profile Blind Box replica endpoints (GUI-editable when not overridden by env).

File: {profile_data_dir}/{profile}.blindbox_replicas.json (``profile_data_dir`` = ``profiles/<profile>/``).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from i2pchat.core.transient_profile import is_transient_profile_name
from i2pchat.storage.blindbox_state import atomic_write_json

logger = logging.getLogger("i2pchat.storage.profile_blindbox_replicas")

PROFILE_BLINDBOX_REPLICAS_VERSION = 2
_SUPPORTED_LOAD_VERSIONS = frozenset({1, 2})


def profile_blindbox_replicas_path(profiles_dir: str, profile: str) -> str:
    safe = (profile or "").strip()
    if not safe or is_transient_profile_name(safe):
        raise ValueError("profile must be a named persistent profile")
    return os.path.join(profiles_dir, f"{safe}.blindbox_replicas.json")


def normalize_replica_endpoints(raw: list[str]) -> list[str]:
    """Strip, drop empties, preserve first-seen order (like _parse_replicas_list)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        candidate = (item or "").strip()
        if not candidate or candidate.startswith("#") or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _replica_auth_subset(replicas: list[str], raw: Any) -> dict[str, str]:
    """Keep only non-empty tokens for keys that appear exactly in ``replicas``."""
    rep_set = set(replicas)
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        val = str(v).strip() if v is not None else ""
        if not key or not val:
            continue
        if key not in rep_set:
            logger.warning(
                "BlindBox replica_auth key not in replicas list, ignored: %s", key
            )
            continue
        out[key] = val
    return out


def load_profile_blindbox_replicas_bundle(
    profiles_dir: str, profile: str
) -> tuple[list[str], dict[str, str]]:
    """Load normalized replicas and per-endpoint auth map. Returns ([], {}) if missing/invalid."""
    if is_transient_profile_name(profile):
        return [], {}
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    if not os.path.isfile(path):
        return [], {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("BlindBox profile replicas load failed (%s): %s", path, e)
        return [], {}
    if not isinstance(data, dict):
        return [], {}
    ver = int(data.get("version", 0))
    if ver not in _SUPPORTED_LOAD_VERSIONS:
        return [], {}
    reps = data.get("replicas")
    if not isinstance(reps, list):
        return [], {}
    strings = [str(x).strip() for x in reps if str(x).strip()]
    replicas = normalize_replica_endpoints(strings)
    if ver == 1:
        return replicas, {}
    return replicas, _replica_auth_subset(replicas, data.get("replica_auth"))


def load_profile_blindbox_replicas_list(profiles_dir: str, profile: str) -> list[str]:
    """Returns non-empty list if file exists and valid; otherwise []."""
    reps, _ = load_profile_blindbox_replicas_bundle(profiles_dir, profile)
    return reps


def save_profile_blindbox_replicas_bundle(
    profiles_dir: str,
    profile: str,
    replicas: list[str],
    replica_auth: dict[str, str],
) -> None:
    normalized = normalize_replica_endpoints(replicas)
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    auth_clean = _replica_auth_subset(normalized, replica_auth)
    payload: dict[str, Any] = {
        "version": PROFILE_BLINDBOX_REPLICAS_VERSION,
        "replicas": normalized,
        "replica_auth": auth_clean,
    }
    atomic_write_json(path, payload)


def save_profile_blindbox_replicas_list(
    profiles_dir: str, profile: str, replicas: list[str]
) -> None:
    save_profile_blindbox_replicas_bundle(profiles_dir, profile, replicas, {})


def delete_profile_blindbox_replicas_file(profiles_dir: str, profile: str) -> None:
    if is_transient_profile_name(profile):
        return
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    try:
        os.unlink(path)
    except OSError:
        pass
