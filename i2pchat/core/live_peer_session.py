"""
Per-peer live I2P stream state (one SAM stream + crypto + receive loop context).

I2PChatCore keeps identity-global state; each LivePeerSession holds everything that
must not mix between simultaneous peers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from i2pchat.core.i2p_chat_core import FileTransferInfo, PendingAckEntry


@dataclass
class LivePeerSession:
    """State for one live connection to a single peer (.b32.i2p)."""

    peer_id: str
    conn: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
    announce_lifecycle: bool = True
    current_peer_dest_b64: Optional[str] = None
    peer_identity_binding_verified: bool = False
    proven: bool = False

    # ``shared_key`` остаётся индикатором «ключи установлены» (гейтинг), но
    # реальное шифрование/MAC используют направленные ключи ниже (protocol v4).
    shared_key: Optional[bytes] = None
    shared_mac_key: Optional[bytes] = None
    # Направленные ключи: send_* — исходящий трафик, recv_* — входящий.
    send_key: Optional[bytes] = None
    send_mac_key: Optional[bytes] = None
    recv_key: Optional[bytes] = None
    recv_mac_key: Optional[bytes] = None
    my_nonce: Optional[bytes] = None
    peer_nonce: Optional[bytes] = None
    my_ephemeral_private: Optional[bytes] = None
    my_ephemeral_public: Optional[bytes] = None
    peer_ephemeral_public: Optional[bytes] = None
    peer_signing_public: Optional[bytes] = None
    use_encryption: bool = False
    handshake_complete: bool = False
    _handshake_initiated: bool = False
    # Key confirmation (FINISHED) state (protocol v4).
    _hs_role: Optional[str] = None  # "initiator" | "responder"
    _hs_transcript: Optional[bytes] = None
    _hs_finished_sent: bool = False
    _hs_peer_finished: bool = False
    # First-contact TOFU deferred until after FINISHED (avoids stalling RESP).
    _hs_tofu_pending_key: Optional[bytes] = None
    # True while the Trust dialog is open during finalize (Send stays disabled).
    _hs_tofu_awaiting_ui: bool = False
    _send_seq: int = 0
    _recv_seq: int = 0
    _pending_text_acks: Dict[int, Any] = field(default_factory=dict)
    _pending_file_acks: Dict[int, Any] = field(default_factory=dict)
    _pending_image_acks: Dict[int, Any] = field(default_factory=dict)
    _incoming_file_msg_id: Optional[int] = None
    _incoming_image_msg_id: Optional[int] = None
    _ack_session_epoch: int = 0

    incoming_file: Any = None
    incoming_info: Any = None
    image_buffer: list[str] = field(default_factory=list)
    inline_image_buffer: bytearray = field(default_factory=bytearray)
    inline_image_info: Optional[Tuple[str, int]] = None

    _file_transfer_active: bool = False
    _inline_image_last_emit: int = 0
    _soft_signal_ack_since_drain: int = 0
    _cancel_transfer: bool = False
    _transfer_aborted_by_peer: bool = False
    _transfer_rejected_by_peer: bool = False
    _recv_loop_active: bool = False

    receive_task: Optional[asyncio.Task] = None

    def reset_crypto(self) -> None:
        self.shared_key = None
        self.shared_mac_key = None
        self.send_key = None
        self.send_mac_key = None
        self.recv_key = None
        self.recv_mac_key = None
        self.my_nonce = None
        self.peer_nonce = None
        self.my_ephemeral_private = None
        self.my_ephemeral_public = None
        self.peer_ephemeral_public = None
        self.peer_signing_public = None
        self.use_encryption = False
        self.handshake_complete = False
        self._handshake_initiated = False
        self._hs_role = None
        self._hs_transcript = None
        self._hs_finished_sent = False
        self._hs_peer_finished = False
        self._hs_tofu_pending_key = None
        self._hs_tofu_awaiting_ui = False
        self._send_seq = 0
        self._recv_seq = 0
        self._pending_text_acks.clear()
        self._pending_file_acks.clear()
        self._pending_image_acks.clear()
        self._incoming_file_msg_id = None
        self._incoming_image_msg_id = None
        self._ack_session_epoch = 0
        self.peer_identity_binding_verified = False
        self.current_peer_dest_b64 = None


def max_concurrent_live_sessions() -> int:
    import os

    raw = os.environ.get("I2PCHAT_MAX_LIVE_SESSIONS", "8").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 8
    return max(1, min(n, 64))
