"""
Pure logic for privacy mode toggle and optional local lock/unlock flow.

Qt layer in main_qt.py applies results; settings persisted in gui.json
under the `privacy_mode` key.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PrivacyState:
    active: bool = False
    hide_notifications: bool = True
    lock_enabled: bool = False
    lock_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# PIN hashing (PBKDF2-HMAC-SHA256, 300 000 iterations)
# Format: "pbkdf2$<hex-salt>$<hex-dk>"
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 300_000
_PBKDF2_DKLEN = 32


def _hash_pin(pin: str, salt_hex: Optional[str] = None) -> str:
    """Return a storable hash string for *pin*."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DKLEN,
    )
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def set_lock_pin(pin: str) -> str:
    """Return a hash string for *pin* suitable for storage in gui.json."""
    if not pin:
        raise ValueError("PIN must not be empty")
    return _hash_pin(pin)


def verify_lock_pin(pin: str, stored_hash: str) -> bool:
    """Return True if *pin* matches *stored_hash*."""
    try:
        prefix, salt_hex, dk_hex = stored_hash.split("$")
    except ValueError:
        return False
    if prefix != "pbkdf2":
        return False
    candidate = _hash_pin(pin, salt_hex=salt_hex)
    # constant-time comparison
    return hmac_compare(candidate, stored_hash)


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time string equality."""
    return hmac_compare_digest(a.encode(), b.encode())


def hmac_compare_digest(a: bytes, b: bytes) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Activation helpers
# ---------------------------------------------------------------------------

def activate_privacy_mode(state: PrivacyState) -> PrivacyState:
    """Return a new PrivacyState with privacy active."""
    return PrivacyState(
        active=True,
        hide_notifications=state.hide_notifications,
        lock_enabled=state.lock_enabled,
        lock_hash=state.lock_hash,
    )


def deactivate_privacy_mode(
    state: PrivacyState,
    pin_if_locked: Optional[str] = None,
) -> tuple[PrivacyState, bool]:
    """Attempt to deactivate privacy mode.

    Returns (new_state, success).  If lock is enabled the caller must supply
    the correct PIN; otherwise deactivation is denied and the original state
    is returned unchanged.
    """
    if not state.active:
        return state, True

    if state.lock_enabled and state.lock_hash:
        if not pin_if_locked:
            return state, False
        if not verify_lock_pin(pin_if_locked, state.lock_hash):
            return state, False

    return (
        PrivacyState(
            active=False,
            hide_notifications=state.hide_notifications,
            lock_enabled=state.lock_enabled,
            lock_hash=state.lock_hash,
        ),
        True,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers (gui.json "privacy_mode" key)
# ---------------------------------------------------------------------------

def privacy_state_to_dict(state: PrivacyState) -> dict:
    return {
        "active": state.active,
        "hide_notifications": state.hide_notifications,
        "lock_enabled": state.lock_enabled,
        "lock_hash": state.lock_hash,
    }


def privacy_state_from_dict(data: dict) -> PrivacyState:
    return PrivacyState(
        active=bool(data.get("active", False)),
        hide_notifications=bool(data.get("hide_notifications", True)),
        lock_enabled=bool(data.get("lock_enabled", False)),
        lock_hash=data.get("lock_hash") or None,
    )
