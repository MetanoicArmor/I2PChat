from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("i2pchat")


class TransportState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    SAM_CONNECTED = "sam_connected"
    WARMING_TUNNELS = "warming_tunnels"
    READY = "ready"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    SHUTTING_DOWN = "shutting_down"
    FAILED = "failed"


class PeerState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    SECURE = "secure"
    STALE = "stale"
    FAILED = "failed"


class OutboundPolicy(StrEnum):
    LIVE_ONLY = "LIVE_ONLY"
    PREFER_LIVE_FALLBACK_BLINDBOX = "PREFER_LIVE_FALLBACK_BLINDBOX"
    QUEUE_THEN_RETRY_LIVE = "QUEUE_THEN_RETRY_LIVE"
    BLINDBOX_ONLY = "BLINDBOX_ONLY"


@dataclass(slots=True)
class ReconnectMetadata:
    attempt: int = 0
    next_retry_mono: float = 0.0
    last_failure_mono: float = 0.0
    last_failure_reason: str = ""


@dataclass(slots=True)
class OutboundStreamInfo:
    destination: str
    opened_at_mono: float = field(default_factory=time.monotonic)
    last_activity_mono: float = field(default_factory=time.monotonic)
    state: PeerState = PeerState.CONNECTING
    inflight_msg_ids: set[int] = field(default_factory=set)


@dataclass(slots=True)
class PeerTransportState:
    peer_id: str
    peer_state: PeerState = PeerState.DISCONNECTED
    connected: bool = False
    handshake_complete: bool = False
    secure_since_mono: float = 0.0
    stale_since_mono: float = 0.0
    last_activity_mono: float = field(default_factory=time.monotonic)
    last_live_ok_mono: float = 0.0
    last_live_failure_mono: float = 0.0
    last_failure_reason: str = ""
    reconnect: ReconnectMetadata = field(default_factory=ReconnectMetadata)
    outbound_streams: dict[str, OutboundStreamInfo] = field(default_factory=dict)
    inflight_msg_ids: set[int] = field(default_factory=set)


class SessionManager:
    """
    Owns transport/session lifecycle state for I2P live delivery path.

    It intentionally does not know about protocol framing or UI callbacks logic.
    """

    def __init__(
        self,
        *,
        secure_session_ttl_sec: float = 300.0,
        treat_stale_as_offline: bool = False,
        on_transport_state_change: Optional[
            Callable[[TransportState, TransportState, str], None]
        ] = None,
        on_peer_state_change: Optional[Callable[[PeerState, PeerState, str], None]] = None,
    ) -> None:
        self.transport_state: TransportState = TransportState.STOPPED
        self.peer_state: PeerState = PeerState.DISCONNECTED
        self.active_peer_id: str = ""
        self.peer_transport: dict[str, PeerTransportState] = {}
        self.secure_session_ttl_sec: float = max(0.0, float(secure_session_ttl_sec))
        self.treat_stale_as_offline: bool = bool(treat_stale_as_offline)

        self.session_socket: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
        self.accept_task: Optional[asyncio.Task[Any]] = None
        self.tunnel_task: Optional[asyncio.Task[Any]] = None
        self.keepalive_task: Optional[asyncio.Task[Any]] = None
        self.handshake_watchdog_task: Optional[asyncio.Task[Any]] = None
        self.handshake_watchdog_generation: int = 0
        self.disconnect_task: Optional[asyncio.Task[Any]] = None
        self.disconnecting: bool = False
        self.outbound_connect_busy: bool = False

        # Legacy compatibility mirrors. Per-peer state is authoritative.
        self.outbound_streams: dict[str, OutboundStreamInfo] = {}
        self.reconnect = ReconnectMetadata()
        self.last_live_ok_mono: float = 0.0
        self.last_live_failure_mono: float = 0.0

        self._on_transport_state_change = on_transport_state_change
        self._on_peer_state_change = on_peer_state_change

    def transition_transport(self, new_state: TransportState, *, reason: str = "") -> None:
        old_state = self.transport_state
        if old_state == new_state:
            return
        self.transport_state = new_state
        logger.debug("Transport state: %s -> %s (%s)", old_state, new_state, reason or "n/a")
        if self._on_transport_state_change is not None:
            self._on_transport_state_change(old_state, new_state, reason)

    def transition_peer(self, new_state: PeerState, *, reason: str = "") -> None:
        old_state = self.peer_state
        if old_state == new_state:
            return
        self.peer_state = new_state
        logger.debug("Peer state: %s -> %s (%s)", old_state, new_state, reason or "n/a")
        if self._on_peer_state_change is not None:
            self._on_peer_state_change(old_state, new_state, reason)

    @staticmethod
    def _normalize_peer_id(peer_id: str) -> str:
        return (peer_id or "").strip().lower()

    def _resolve_peer_id(self, peer_id: Optional[str] = None) -> str:
        normalized = self._normalize_peer_id(peer_id or "")
        if normalized:
            return normalized
        return self.active_peer_id

    def _set_active_peer(self, peer_id: str) -> None:
        normalized = self._normalize_peer_id(peer_id)
        if normalized:
            self.active_peer_id = normalized

    def set_active_peer(self, peer_id: str) -> None:
        self._set_active_peer(peer_id)
        self._sync_legacy_views()

    def get_active_peer(self) -> str:
        return self.active_peer_id

    def ensure_peer_transport(self, peer_id: str) -> PeerTransportState:
        normalized = self._normalize_peer_id(peer_id)
        if not normalized:
            raise ValueError("peer_id is required")
        peer = self.peer_transport.get(normalized)
        if peer is None:
            peer = PeerTransportState(peer_id=normalized)
            self.peer_transport[normalized] = peer
        if not self.active_peer_id:
            self._set_active_peer(normalized)
        return peer

    def get_peer_transport(self, peer_id: Optional[str] = None) -> Optional[PeerTransportState]:
        normalized = self._resolve_peer_id(peer_id)
        if not normalized:
            return None
        return self.peer_transport.get(normalized)

    def _sync_legacy_views(self) -> None:
        self._rebuild_legacy_outbound_streams()
        self._refresh_active_peer()
        active = self.get_peer_transport()
        if active is None:
            self.transition_peer(PeerState.DISCONNECTED, reason="sync-empty")
            return
        self.transition_peer(active.peer_state, reason=f"sync:{active.peer_id[:24]}")

    def _refresh_active_peer(self) -> None:
        if self.active_peer_id:
            return
        if not self.peer_transport:
            return
        best_peer: Optional[PeerTransportState] = None
        for peer in self.peer_transport.values():
            if best_peer is None:
                best_peer = peer
                continue
            if self._peer_priority(peer) > self._peer_priority(best_peer):
                best_peer = peer
        if best_peer is not None:
            self.active_peer_id = best_peer.peer_id

    @staticmethod
    def _peer_priority(peer: PeerTransportState) -> int:
        priority = 0
        if peer.peer_state == PeerState.SECURE:
            priority += 8
        elif peer.peer_state == PeerState.HANDSHAKING:
            priority += 4
        elif peer.peer_state == PeerState.CONNECTING:
            priority += 2
        if peer.connected:
            priority += 2
        if peer.handshake_complete:
            priority += 2
        if peer.outbound_streams:
            priority += 1
        return priority

    def _rebuild_legacy_outbound_streams(self) -> None:
        merged: dict[str, OutboundStreamInfo] = {}
        for peer in self.peer_transport.values():
            merged.update(peer.outbound_streams)
        self.outbound_streams = merged

    def _touch_peer(self, peer: PeerTransportState) -> None:
        peer.last_activity_mono = time.monotonic()

    def set_peer_connected(
        self,
        peer_id: str,
        *,
        state: PeerState = PeerState.CONNECTING,
        reason: str = "connected",
    ) -> None:
        peer = self.ensure_peer_transport(peer_id)
        peer.connected = True
        peer.peer_state = state
        self._touch_peer(peer)
        self._sync_legacy_views()
        logger.debug("Peer connected: %s (%s)", peer.peer_id[:24], reason)

    def set_peer_handshake_complete(self, peer_id: str, *, reason: str = "handshake-ok") -> None:
        now = time.monotonic()
        peer = self.ensure_peer_transport(peer_id)
        peer.connected = True
        peer.handshake_complete = True
        peer.peer_state = PeerState.SECURE
        peer.secure_since_mono = now
        peer.stale_since_mono = 0.0
        peer.last_live_ok_mono = now
        self._touch_peer(peer)
        self._sync_legacy_views()
        logger.debug("Peer secure: %s (%s)", peer.peer_id[:24], reason)

    def set_peer_disconnected(
        self,
        peer_id: str,
        *,
        reason: str = "disconnected",
        keep_reconnect_metadata: bool = True,
    ) -> None:
        peer = self.ensure_peer_transport(peer_id)
        peer.connected = False
        peer.handshake_complete = False
        peer.peer_state = PeerState.DISCONNECTED
        peer.secure_since_mono = 0.0
        peer.stale_since_mono = 0.0
        peer.outbound_streams.clear()
        peer.inflight_msg_ids.clear()
        if not keep_reconnect_metadata:
            peer.reconnect = ReconnectMetadata()
        self._touch_peer(peer)
        self._sync_legacy_views()
        logger.debug("Peer disconnected: %s (%s)", peer.peer_id[:24], reason)

    def mark_peer_failed(self, peer_id: str, *, reason: str) -> None:
        now = time.monotonic()
        peer = self.ensure_peer_transport(peer_id)
        peer.connected = False
        peer.handshake_complete = False
        peer.peer_state = PeerState.FAILED
        peer.stale_since_mono = 0.0
        peer.last_failure_reason = reason
        peer.last_live_failure_mono = now
        self._touch_peer(peer)
        self._sync_legacy_views()
        logger.debug("Peer failed: %s (%s)", peer.peer_id[:24], reason)

    def set_outbound_connect_busy(self, busy: bool, *, peer_id: Optional[str] = None) -> None:
        self.outbound_connect_busy = bool(busy)
        if busy:
            return
        peer = self.get_peer_transport(peer_id)
        if peer is not None and peer.peer_state == PeerState.CONNECTING and not peer.connected:
            peer.peer_state = PeerState.DISCONNECTED
            self._sync_legacy_views()

    def register_stream(
        self,
        destination: str,
        *,
        state: PeerState = PeerState.CONNECTING,
        peer_id: Optional[str] = None,
    ) -> None:
        info = OutboundStreamInfo(destination=destination, state=state)
        key = self._normalize_peer_id(peer_id or destination)
        if not key:
            self.transition_peer(state, reason=f"stream-open:{destination[:24]}")
            return
        peer = self.ensure_peer_transport(key)
        peer.connected = True
        peer.outbound_streams[destination] = info
        peer.peer_state = state
        self._touch_peer(peer)
        self._sync_legacy_views()

    def unregister_stream(self, destination: str, *, peer_id: Optional[str] = None) -> None:
        key = self._normalize_peer_id(peer_id or destination)
        if not key:
            for candidate_id, candidate in self.peer_transport.items():
                if destination in candidate.outbound_streams:
                    key = candidate_id
                    break
        peer = self.peer_transport.get(key)
        if peer is not None:
            peer.outbound_streams.pop(destination, None)
            if not peer.outbound_streams:
                peer.connected = False
                peer.handshake_complete = False
                peer.peer_state = PeerState.DISCONNECTED
                peer.secure_since_mono = 0.0
                peer.stale_since_mono = 0.0
                peer.inflight_msg_ids.clear()
            self._touch_peer(peer)
        self._sync_legacy_views()

    def update_stream_state(
        self,
        destination: str,
        state: PeerState,
        *,
        peer_id: Optional[str] = None,
    ) -> None:
        key = self._normalize_peer_id(peer_id or destination)
        if key:
            peer = self.ensure_peer_transport(key)
            info = peer.outbound_streams.get(destination)
            if info is None:
                info = OutboundStreamInfo(destination=destination, state=state)
                peer.outbound_streams[destination] = info
            info.state = state
            info.last_activity_mono = time.monotonic()
            peer.connected = True
            peer.outbound_streams[destination] = info
            peer.peer_state = state
            if state == PeerState.SECURE:
                peer.handshake_complete = True
                peer.stale_since_mono = 0.0
                if peer.secure_since_mono <= 0.0:
                    peer.secure_since_mono = info.last_activity_mono
            self._touch_peer(peer)
            self._sync_legacy_views()
        else:
            self.transition_peer(state, reason=f"stream-state:{destination[:24]}")

    def touch_stream(self, destination: str, *, peer_id: Optional[str] = None) -> None:
        key = self._normalize_peer_id(peer_id or destination)
        peer = self.peer_transport.get(key)
        if peer is None and not key:
            for candidate in self.peer_transport.values():
                if destination in candidate.outbound_streams:
                    peer = candidate
                    break
        if peer is not None:
            info = peer.outbound_streams.get(destination)
            if info is not None:
                info.last_activity_mono = time.monotonic()
            self._touch_peer(peer)

    def register_inflight_message(self, msg_id: int, *, peer_id: Optional[str] = None) -> None:
        key = self._resolve_peer_id(peer_id)
        if not key:
            return
        peer = self.ensure_peer_transport(key)
        peer.inflight_msg_ids.add(int(msg_id))
        self._touch_peer(peer)

    def acknowledge_inflight_message(self, msg_id: int, *, peer_id: Optional[str] = None) -> bool:
        key = self._resolve_peer_id(peer_id)
        if not key:
            return False
        peer = self.peer_transport.get(key)
        if peer is None:
            return False
        removed = int(msg_id) in peer.inflight_msg_ids
        peer.inflight_msg_ids.discard(int(msg_id))
        if removed:
            self._touch_peer(peer)
        return removed

    def clear_inflight_messages(self, *, peer_id: Optional[str] = None) -> None:
        key = self._resolve_peer_id(peer_id)
        if not key:
            return
        peer = self.peer_transport.get(key)
        if peer is None:
            return
        peer.inflight_msg_ids.clear()
        self._touch_peer(peer)

    def refresh_peer_health(self, *, peer_id: Optional[str] = None) -> None:
        key = self._resolve_peer_id(peer_id)
        if not key:
            return
        peer = self.peer_transport.get(key)
        if peer is None:
            return
        now = time.monotonic()
        if (
            self.secure_session_ttl_sec > 0.0
            and peer.connected
            and peer.handshake_complete
            and peer.secure_since_mono > 0.0
        ):
            anchor = max(peer.last_activity_mono, peer.last_live_ok_mono, peer.secure_since_mono)
            if now - anchor >= self.secure_session_ttl_sec:
                if peer.stale_since_mono <= 0.0:
                    peer.stale_since_mono = now
                if peer.peer_state == PeerState.SECURE:
                    peer.peer_state = PeerState.STALE
                    self._sync_legacy_views()

    def is_peer_secure_channel_ready(self, *, peer_id: Optional[str] = None) -> bool:
        key = self._resolve_peer_id(peer_id)
        if not key:
            return False
        peer = self.peer_transport.get(key)
        if peer is None:
            return False
        self.refresh_peer_health(peer_id=key)
        if not (peer.connected and peer.handshake_complete):
            return False
        if peer.peer_state == PeerState.STALE and self.treat_stale_as_offline:
            return False
        return True

    def invalidate_handshake_watchdog(self) -> int:
        self.handshake_watchdog_generation += 1
        self.handshake_watchdog_task = None
        return self.handshake_watchdog_generation

    def mark_live_healthy(self, *, peer_id: Optional[str] = None) -> None:
        now = time.monotonic()
        key = self._resolve_peer_id(peer_id)
        if key:
            peer = self.ensure_peer_transport(key)
            peer.connected = True
            peer.handshake_complete = True
            peer.peer_state = PeerState.SECURE
            peer.last_live_ok_mono = now
            peer.stale_since_mono = 0.0
            if peer.secure_since_mono <= 0.0:
                peer.secure_since_mono = now
            peer.reconnect = ReconnectMetadata()
            self._touch_peer(peer)
            self._sync_legacy_views()
        else:
            self.last_live_ok_mono = now
            self.reconnect = ReconnectMetadata()
        if self.transport_state in {
            TransportState.RECONNECTING,
            TransportState.DEGRADED,
            TransportState.SAM_CONNECTED,
            TransportState.WARMING_TUNNELS,
        }:
            self.transition_transport(TransportState.READY, reason="live-ok")

    def mark_live_failure(
        self,
        *,
        reason: str,
        mark_peer_stale: bool = True,
        peer_id: Optional[str] = None,
    ) -> None:
        now = time.monotonic()
        key = self._resolve_peer_id(peer_id)
        if key:
            peer = self.ensure_peer_transport(key)
            peer.last_live_failure_mono = now
            peer.last_failure_reason = reason
            if mark_peer_stale:
                peer.peer_state = PeerState.STALE
                if peer.stale_since_mono <= 0.0:
                    peer.stale_since_mono = now
            self._touch_peer(peer)
            self._sync_legacy_views()
        else:
            self.last_live_failure_mono = now
        if not self._has_ready_peer():
            self.transition_transport(TransportState.DEGRADED, reason=reason)

    def schedule_reconnect_backoff(
        self,
        *,
        reason: str,
        base_delay_sec: float = 1.0,
        max_delay_sec: float = 30.0,
        peer_id: Optional[str] = None,
    ) -> float:
        key = self._resolve_peer_id(peer_id)
        if key:
            peer = self.ensure_peer_transport(key)
            reconnect = peer.reconnect
        else:
            reconnect = self.reconnect
        attempt = max(1, reconnect.attempt + 1)
        delay = min(max_delay_sec, base_delay_sec * (2 ** (attempt - 1)))
        jitter = random.uniform(0.0, min(0.75, delay * 0.25))
        effective_delay = delay + jitter
        now_mono = time.monotonic()
        metadata = ReconnectMetadata(
            attempt=attempt,
            next_retry_mono=now_mono + effective_delay,
            last_failure_mono=now_mono,
            last_failure_reason=reason,
        )
        if key:
            peer.reconnect = metadata
            peer.last_failure_reason = reason
            peer.last_live_failure_mono = now_mono
            self._touch_peer(peer)
            self._sync_legacy_views()
        else:
            self.reconnect = metadata
        if not self._has_ready_peer():
            self.transition_transport(TransportState.RECONNECTING, reason=reason)
        return effective_delay

    def is_live_path_alive(
        self,
        *,
        connected: Optional[bool] = None,
        handshake_complete: Optional[bool] = None,
        peer_id: Optional[str] = None,
    ) -> bool:
        explicit_peer = self._normalize_peer_id(peer_id or "")
        key = self._resolve_peer_id(peer_id)
        if key and key in self.peer_transport:
            return self.is_peer_secure_channel_ready(peer_id=key)
        if explicit_peer:
            return False
        if connected is not None or handshake_complete is not None:
            return bool(connected and handshake_complete)
        return self.is_peer_secure_channel_ready(peer_id=peer_id)

    def select_outbound_policy(
        self,
        *,
        requested_route: str,
        connected: Optional[bool] = None,
        handshake_complete: Optional[bool] = None,
        peer_id: Optional[str] = None,
    ) -> OutboundPolicy:
        route = (requested_route or "auto").strip().lower()
        live_alive = self.is_live_path_alive(
            connected=connected,
            handshake_complete=handshake_complete,
            peer_id=peer_id,
        )
        if route == "live":
            return OutboundPolicy.LIVE_ONLY
        if route == "offline":
            return OutboundPolicy.BLINDBOX_ONLY
        if live_alive:
            return OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX
        return OutboundPolicy.QUEUE_THEN_RETRY_LIVE

    async def cancel_tasks_and_close_session(self) -> None:
        tasks_to_cancel: list[asyncio.Task[Any]] = []
        for name in (
            "accept_task",
            "tunnel_task",
            "keepalive_task",
            "handshake_watchdog_task",
            "disconnect_task",
        ):
            task = getattr(self, name)
            if task is not None and not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
            setattr(self, name, None)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        if self.session_socket is not None:
            try:
                _, writer = self.session_socket
                writer.close()
            except Exception:
                pass
            self.session_socket = None

        self.outbound_connect_busy = False
        self.disconnecting = False
        for peer in self.peer_transport.values():
            peer.outbound_streams.clear()
            peer.inflight_msg_ids.clear()
            peer.connected = False
            peer.handshake_complete = False
            peer.peer_state = PeerState.DISCONNECTED
            peer.stale_since_mono = 0.0
            peer.secure_since_mono = 0.0
        self.peer_transport.clear()
        self.active_peer_id = ""
        self.outbound_streams.clear()
        self.reconnect = ReconnectMetadata()
        self.invalidate_handshake_watchdog()
        self.transition_peer(PeerState.DISCONNECTED, reason="shutdown")

    def get_reconnect_metadata(self, *, peer_id: Optional[str] = None) -> ReconnectMetadata:
        key = self._normalize_peer_id(peer_id or "")
        if key:
            peer = self.peer_transport.get(key)
            if peer is not None:
                return peer.reconnect
        return self.reconnect

    def _has_ready_peer(self) -> bool:
        for peer_id in self.peer_transport:
            if self.is_peer_secure_channel_ready(peer_id=peer_id):
                return True
        return False
