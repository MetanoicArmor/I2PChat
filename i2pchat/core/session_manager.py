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


class SessionManager:
    """
    Owns transport/session lifecycle state for I2P live delivery path.

    It intentionally does not know about protocol framing or UI callbacks logic.
    """

    def __init__(
        self,
        *,
        on_transport_state_change: Optional[
            Callable[[TransportState, TransportState, str], None]
        ] = None,
        on_peer_state_change: Optional[Callable[[PeerState, PeerState, str], None]] = None,
    ) -> None:
        self.transport_state: TransportState = TransportState.STOPPED
        self.peer_state: PeerState = PeerState.DISCONNECTED

        self.session_socket: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
        self.accept_task: Optional[asyncio.Task[Any]] = None
        self.tunnel_task: Optional[asyncio.Task[Any]] = None
        self.keepalive_task: Optional[asyncio.Task[Any]] = None
        self.handshake_watchdog_task: Optional[asyncio.Task[Any]] = None
        self.handshake_watchdog_generation: int = 0
        self.disconnect_task: Optional[asyncio.Task[Any]] = None
        self.disconnecting: bool = False
        self.outbound_connect_busy: bool = False

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

    def set_outbound_connect_busy(self, busy: bool) -> None:
        self.outbound_connect_busy = bool(busy)
        if not busy and self.peer_state == PeerState.CONNECTING:
            self.transition_peer(PeerState.DISCONNECTED, reason="connect-idle")

    def register_stream(
        self,
        destination: str,
        *,
        state: PeerState = PeerState.CONNECTING,
    ) -> None:
        info = OutboundStreamInfo(destination=destination, state=state)
        self.outbound_streams[destination] = info
        self.transition_peer(state, reason=f"stream-open:{destination[:24]}")

    def unregister_stream(self, destination: str) -> None:
        self.outbound_streams.pop(destination, None)
        if not self.outbound_streams:
            self.transition_peer(PeerState.DISCONNECTED, reason="stream-close")

    def update_stream_state(self, destination: str, state: PeerState) -> None:
        info = self.outbound_streams.get(destination)
        if info is None:
            info = OutboundStreamInfo(destination=destination, state=state)
            self.outbound_streams[destination] = info
        info.state = state
        info.last_activity_mono = time.monotonic()
        self.transition_peer(state, reason=f"stream-state:{destination[:24]}")

    def touch_stream(self, destination: str) -> None:
        info = self.outbound_streams.get(destination)
        if info is not None:
            info.last_activity_mono = time.monotonic()

    def invalidate_handshake_watchdog(self) -> int:
        self.handshake_watchdog_generation += 1
        self.handshake_watchdog_task = None
        return self.handshake_watchdog_generation

    def mark_live_healthy(self) -> None:
        self.last_live_ok_mono = time.monotonic()
        self.reconnect = ReconnectMetadata()
        if self.transport_state in {
            TransportState.RECONNECTING,
            TransportState.DEGRADED,
            TransportState.SAM_CONNECTED,
            TransportState.WARMING_TUNNELS,
        }:
            self.transition_transport(TransportState.READY, reason="live-ok")

    def mark_live_failure(self, *, reason: str, mark_peer_stale: bool = True) -> None:
        self.last_live_failure_mono = time.monotonic()
        self.transition_transport(TransportState.DEGRADED, reason=reason)
        if mark_peer_stale:
            self.transition_peer(PeerState.STALE, reason=reason)

    def schedule_reconnect_backoff(
        self,
        *,
        reason: str,
        base_delay_sec: float = 1.0,
        max_delay_sec: float = 30.0,
    ) -> float:
        attempt = max(1, self.reconnect.attempt + 1)
        delay = min(max_delay_sec, base_delay_sec * (2 ** (attempt - 1)))
        jitter = random.uniform(0.0, min(0.75, delay * 0.25))
        effective_delay = delay + jitter
        now_mono = time.monotonic()
        self.reconnect = ReconnectMetadata(
            attempt=attempt,
            next_retry_mono=now_mono + effective_delay,
            last_failure_mono=now_mono,
            last_failure_reason=reason,
        )
        self.transition_transport(TransportState.RECONNECTING, reason=reason)
        return effective_delay

    def is_live_path_alive(
        self,
        *,
        connected: bool,
        handshake_complete: bool,
    ) -> bool:
        # Keep routing compatibility with the legacy Core behavior:
        # live path is considered available whenever the transport socket exists
        # and secure handshake has completed. PeerState is still tracked for
        # telemetry/reconnect diagnostics, but should not force offline fallback
        # by itself because that can stall normal delivery.
        return bool(connected and handshake_complete)

    def select_outbound_policy(
        self,
        *,
        requested_route: str,
        connected: bool,
        handshake_complete: bool,
    ) -> OutboundPolicy:
        route = (requested_route or "auto").strip().lower()
        live_alive = self.is_live_path_alive(
            connected=connected,
            handshake_complete=handshake_complete,
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
        self.outbound_streams.clear()
        self.invalidate_handshake_watchdog()
        self.transition_peer(PeerState.DISCONNECTED, reason="shutdown")
