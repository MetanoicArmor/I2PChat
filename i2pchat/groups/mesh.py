from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import GroupState, normalize_member_id

logger = logging.getLogger("i2pchat")


@dataclass(slots=True, frozen=True)
class GroupMeshPeerSnapshot:
    peer_id: str
    peer_state: str = "disconnected"
    live_ready: bool = False
    active_session: bool = False
    blindbox_ready: bool = False
    next_retry_mono: float = 0.0


class GroupMeshManager:
    """
    Background planner for group mesh connectivity.

    It does not own sockets or protocol state. Instead, it periodically scans
    known groups, decides which peers still need a quiet live bootstrap, and
    delegates scheduling to the runtime via callbacks.
    """

    def __init__(
        self,
        *,
        list_group_states: Callable[[], list[GroupState]],
        get_local_member_id: Callable[[], str],
        build_peer_snapshot: Callable[[str], GroupMeshPeerSnapshot],
        schedule_peer_intros: Callable[[list[str]], None],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._list_group_states = list_group_states
        self._get_local_member_id = get_local_member_id
        self._build_peer_snapshot = build_peer_snapshot
        self._schedule_peer_intros = schedule_peer_intros
        self._clock = clock or time.monotonic
        self._wakeup = asyncio.Event()
        self._stop_requested = False

    @staticmethod
    def _env_truthy(name: str, default: str = "1") -> bool:
        value = os.environ.get(name, default).strip().lower()
        return value not in {"0", "false", "no", "off"}

    @staticmethod
    def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
        raw = os.environ.get(name, str(default)).strip()
        try:
            value = float(raw)
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
        raw = os.environ.get(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    def enabled(self) -> bool:
        if "I2PCHAT_GROUP_AUTO_MESH" in os.environ:
            return self._env_truthy("I2PCHAT_GROUP_AUTO_MESH", "1")
        return self._env_truthy("I2PCHAT_GROUP_AUTO_INTRO", "1")

    def interval_sec(self) -> float:
        return self._env_float(
            "I2PCHAT_GROUP_AUTO_MESH_INTERVAL_SEC",
            20.0,
            minimum=3.0,
            maximum=600.0,
        )

    def max_scheduled_per_tick(self) -> int:
        return self._env_int(
            "I2PCHAT_GROUP_AUTO_MESH_MAX_PER_TICK",
            3,
            minimum=1,
            maximum=64,
        )

    def connect_offline_ready_peers(self) -> bool:
        return self._env_truthy("I2PCHAT_GROUP_AUTO_MESH_CONNECT_OFFLINE_READY", "0")

    def request_scan(self) -> None:
        self._wakeup.set()

    def stop(self) -> None:
        self._stop_requested = True
        self._wakeup.set()

    def _iter_group_peers(self) -> Iterable[str]:
        try:
            local_member_id = normalize_member_id(self._get_local_member_id())
        except Exception:
            return ()
        if not local_member_id:
            return ()
        seen: set[str] = set()
        peers: list[str] = []
        for state in self._list_group_states():
            for member_id in state.members:
                normalized = normalize_member_id(member_id)
                if not normalized or normalized == local_member_id or normalized in seen:
                    continue
                seen.add(normalized)
                peers.append(normalized)
        return peers

    def collect_due_peer_intros(self, *, now_mono: float | None = None) -> list[str]:
        if not self.enabled():
            return []
        now = self._clock() if now_mono is None else float(now_mono)
        connect_offline_ready = self.connect_offline_ready_peers()
        candidates: list[tuple[int, float, str]] = []
        for peer_id in self._iter_group_peers():
            snapshot = self._build_peer_snapshot(peer_id)
            if snapshot.live_ready or snapshot.active_session:
                continue
            if snapshot.peer_state in {"connecting", "handshaking", "secure"}:
                continue
            if snapshot.next_retry_mono > now:
                continue
            if snapshot.blindbox_ready and not connect_offline_ready:
                continue
            state_priority = {
                "failed": 0,
                "stale": 1,
                "disconnected": 2,
            }.get(snapshot.peer_state, 3)
            candidates.append((state_priority, snapshot.next_retry_mono, snapshot.peer_id))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        limit = self.max_scheduled_per_tick()
        return [peer_id for _, _, peer_id in candidates[:limit]]

    def tick(self, *, now_mono: float | None = None) -> list[str]:
        peers = self.collect_due_peer_intros(now_mono=now_mono)
        if peers:
            self._schedule_peer_intros(peers)
        return peers

    async def run(self) -> None:
        try:
            while not self._stop_requested:
                try:
                    self.tick()
                except Exception:
                    logger.exception("Group mesh tick failed")
                timeout = self.interval_sec()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._wakeup.clear()
        except asyncio.CancelledError:
            raise
