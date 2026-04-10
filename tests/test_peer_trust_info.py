"""I2PChatCore.get_peer_trust_info (read-only UI API)."""

from __future__ import annotations

import pytest

from i2pchat.core.i2p_chat_core import I2PChatCore

VALID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VALID_LEGACY = f"{VALID}.b32.i2p"


def test_get_peer_trust_info_invalid_returns_none() -> None:
    core = I2PChatCore(on_error=lambda _m: None)
    assert core.get_peer_trust_info("") is None
    assert core.get_peer_trust_info("not-an-address") is None


def test_get_peer_trust_info_unpinned() -> None:
    core = I2PChatCore(on_error=lambda _m: None)
    info = core.get_peer_trust_info(VALID)
    assert info is not None
    assert info.peer_normalized == VALID
    assert info.pinned is False
    assert info.signing_key_hex is None


def test_get_peer_trust_info_pinned() -> None:
    core = I2PChatCore(on_error=lambda _m: None)
    hx = "ab" * 32
    core.peer_trusted_signing_keys[VALID] = hx
    info = core.get_peer_trust_info(VALID_LEGACY)
    assert info is not None
    assert info.peer_normalized == VALID
    assert info.pinned is True
    assert info.signing_key_hex == hx
    assert info.fingerprint_short is not None
