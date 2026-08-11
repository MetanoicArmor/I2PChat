"""Helpers for unit tests: attach a mock ``LivePeerSession`` to ``I2PChatCore``."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from i2pchat.core.live_peer_session import LivePeerSession


def attach_mock_live_session(
    core: Any,
    peer_addr: str,
    conn: Tuple[Any, Any],
    *,
    handshake_complete: bool = True,
    use_encryption: Optional[bool] = None,
    shared_key: Optional[bytes] = None,
    shared_mac_key: Optional[bytes] = None,
) -> str:
    """
    Register a fake live session and set ``current_peer_addr`` to the same peer
    so ``send_text`` / routing see an active connection.
    """
    k = core._normalize_peer_addr(peer_addr)
    ls = LivePeerSession(peer_id=k)
    ls.conn = conn
    ls.handshake_complete = handshake_complete
    # Wire-secure checks require use_encryption; default it on with handshake.
    ls.use_encryption = (
        bool(handshake_complete) if use_encryption is None else bool(use_encryption)
    )
    if shared_key is not None:
        ls.shared_key = shared_key
        # Production derives directional keys (send_*/recv_*); tests simulate a
        # peer by self-looping the same session, so mirror the key into both
        # directions to keep a symmetric round-trip working.
        mac = shared_mac_key if shared_mac_key is not None else shared_key
        ls.send_key = shared_key
        ls.recv_key = shared_key
        ls.send_mac_key = mac
        ls.recv_mac_key = mac
    elif ls.use_encryption and ls.shared_key is None:
        # Minimal placeholder so frame_message can encrypt in routing tests.
        import secrets

        key = secrets.token_bytes(32)
        ls.shared_key = key
        ls.send_key = key
        ls.recv_key = key
        ls.send_mac_key = key
        ls.recv_mac_key = key
        ls.shared_mac_key = key
    if shared_mac_key is not None:
        ls.shared_mac_key = shared_mac_key
    core._live_sessions[k] = ls
    core.current_peer_addr = k
    return k
