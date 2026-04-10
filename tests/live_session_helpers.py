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
    use_encryption: bool = False,
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
    ls.use_encryption = use_encryption
    if shared_key is not None:
        ls.shared_key = shared_key
    if shared_mac_key is not None:
        ls.shared_mac_key = shared_mac_key
    core._live_sessions[k] = ls
    core.current_peer_addr = k
    return k
