"""
Per-profile Blind Box replica endpoints (GUI-editable when not overridden by env).

File: {profiles_dir}/{profile}.blindbox_replicas.json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from i2pchat.storage.blindbox_state import atomic_write_json

logger = logging.getLogger("i2pchat.storage.profile_blindbox_replicas")

PROFILE_BLINDBOX_REPLICAS_VERSION = 1


def profile_blindbox_replicas_path(profiles_dir: str, profile: str) -> str:
    safe = (profile or "").strip()
    if not safe or safe == "default":
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


def load_profile_blindbox_replicas_list(profiles_dir: str, profile: str) -> list[str]:
    """Returns non-empty list if file exists and valid; otherwise []."""
    if (profile or "").strip() in ("", "default"):
        return []
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("BlindBox profile replicas load failed (%s): %s", path, e)
        return []
    if not isinstance(data, dict):
        return []
    if int(data.get("version", 0)) != PROFILE_BLINDBOX_REPLICAS_VERSION:
        return []
    reps = data.get("replicas")
    if not isinstance(reps, list):
        return []
    strings = [str(x).strip() for x in reps if str(x).strip()]
    return normalize_replica_endpoints(strings)


def save_profile_blindbox_replicas_list(
    profiles_dir: str, profile: str, replicas: list[str]
) -> None:
    normalized = normalize_replica_endpoints(replicas)
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    payload: dict[str, Any] = {
        "version": PROFILE_BLINDBOX_REPLICAS_VERSION,
        "replicas": normalized,
    }
    atomic_write_json(path, payload)


def delete_profile_blindbox_replicas_file(profiles_dir: str, profile: str) -> None:
    if (profile or "").strip() in ("", "default"):
        return
    path = profile_blindbox_replicas_path(profiles_dir, profile)
    try:
        os.unlink(path)
    except OSError:
        pass
