"""Per-profile compose drafts at rest (plaintext JSON or identity-sealed)."""

from __future__ import annotations

import json
import logging
from typing import Any

from i2pchat.storage.sealed_json import read_sealed_json, write_sealed_json

logger = logging.getLogger("i2pchat.storage.compose_drafts_store")

COMPOSE_DRAFTS_MAGIC = b"I2CD"
COMPOSE_DRAFTS_DOMAIN = b"I2PCHAT-COMPOSE-DRAFTS"
COMPOSE_DRAFTS_VERSION = 1


def load_compose_drafts(
    path: str,
    *,
    identity_key: bytes | None = None,
) -> dict[str, str]:
    try:
        data = read_sealed_json(
            path,
            identity_key=identity_key,
            magic=COMPOSE_DRAFTS_MAGIC,
            domain=COMPOSE_DRAFTS_DOMAIN,
        )
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    raw = data.get("drafts")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def save_compose_drafts(
    path: str,
    drafts: dict[str, str],
    *,
    identity_key: bytes | None = None,
) -> None:
    payload: dict[str, Any] = {
        "version": COMPOSE_DRAFTS_VERSION,
        "drafts": dict(drafts),
    }
    try:
        write_sealed_json(
            path,
            payload,
            identity_key=identity_key,
            magic=COMPOSE_DRAFTS_MAGIC,
            domain=COMPOSE_DRAFTS_DOMAIN,
        )
    except Exception:
        logger.debug("failed to save compose drafts", exc_info=True)
