"""
BlindBox persistent state helpers.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

BLINDBOX_STATE_V1 = "BLINDBOX_STATE_V1"


@dataclass
class BlindBoxState:
    send_index: int = 0
    recv_base: int = 0
    recv_window: int = 16
    consumed_recv: set[int] = field(default_factory=set)
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def mark_consumed(self, recv_index: int) -> None:
        if recv_index < 0:
            raise ValueError("recv_index must be non-negative")
        self.consumed_recv.add(int(recv_index))
        self.advance_recv_base()
        self.updated_at = int(time.time())

    def advance_recv_base(self) -> None:
        while self.recv_base in self.consumed_recv:
            self.recv_base += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": BLINDBOX_STATE_V1,
            "send_index": int(self.send_index),
            "recv_base": int(self.recv_base),
            "recv_window": int(self.recv_window),
            "consumed_recv": sorted(int(x) for x in self.consumed_recv),
            "updated_at": int(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlindBoxState":
        if data.get("version") != BLINDBOX_STATE_V1:
            raise ValueError("Unsupported BlindBox state version")
        send_index = int(data.get("send_index", 0))
        recv_base = int(data.get("recv_base", 0))
        recv_window = int(data.get("recv_window", 16))
        consumed_raw = data.get("consumed_recv", [])
        if send_index < 0 or recv_base < 0:
            raise ValueError("send_index/recv_base must be non-negative")
        if recv_window < 1 or recv_window > 4096:
            raise ValueError("recv_window must be in range 1..4096")
        consumed_recv = {int(x) for x in consumed_raw}
        if any(x < 0 for x in consumed_recv):
            raise ValueError("consumed_recv contains negative indexes")
        state = cls(
            send_index=send_index,
            recv_base=recv_base,
            recv_window=recv_window,
            consumed_recv=consumed_recv,
            updated_at=int(data.get("updated_at", int(time.time()))),
        )
        state.advance_recv_base()
        return state


def save_blindbox_state(path: str, state: BlindBoxState) -> None:
    atomic_write_json(path, state.to_dict())


def atomic_write_bytes(
    path: str,
    data: bytes,
    *,
    mode: int = 0o600,
) -> None:
    if not path:
        raise ValueError("path is required")
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        raise ValueError("Invalid state path")
    os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".blindbox_state.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def atomic_write_text(
    path: str,
    text: str,
    *,
    mode: int = 0o600,
) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def atomic_write_json(
    path: str,
    obj: dict[str, Any],
    *,
    mode: int = 0o600,
) -> None:
    atomic_write_bytes(
        path,
        json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
        mode=mode,
    )


def load_blindbox_state(path: str) -> BlindBoxState:
    if not os.path.exists(path):
        return BlindBoxState()
    with open(path, "rb") as f:
        raw = f.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("BlindBox state must be a JSON object")
    return BlindBoxState.from_dict(data)
