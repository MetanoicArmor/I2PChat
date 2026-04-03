"""
Local Blind Box server (best-effort convenience fallback).

Protocol:
- PING\n -> PONG BLINDBOX_LOCAL_REPLICA_V1
- CAPA\n -> OK BLINDBOX_QUEUE_CAPS_V1 | ERR
- AUTH <token>\n -> OK | ERR
- QPUT <queue> <key> <size> <put_cap> <get_cap> <delete_cap> [token]\n + <size bytes>
  -> OK | EXISTS | FULL | ERR
- QGET <queue> <key> <get_cap> [token]\n -> MISS | OK <size>\n + <size bytes>
- QDEL <queue> <key> <delete_cap> [token]\n -> OK | MISS | ERR
"""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass, field
from typing import Optional


BLINDBOX_LOCAL_REPLICA_MAGIC = "PONG BLINDBOX_LOCAL_REPLICA_V1"
BLINDBOX_QUEUE_CAPS_MAGIC = "OK BLINDBOX_QUEUE_CAPS_V1"


@dataclass
class _QueueRecord:
    put_cap: str
    get_cap: str
    delete_cap: str
    items: dict[str, bytes] = field(default_factory=dict)


class BlindBoxLocalReplicaServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 19444,
        max_blob_size: int = 1_048_576,
        max_entries: int = 4096,
        auth_token: str = "",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.max_blob_size = int(max_blob_size)
        self.max_entries = int(max_entries)
        self.auth_token = str(auth_token or "").strip()
        self._server: Optional[asyncio.AbstractServer] = None
        self._queues: dict[str, _QueueRecord] = {}

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    async def start(self) -> bool:
        if self._server is not None:
            return True
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
            return True
        except OSError:
            # Port is likely already in use by another process/server.
            self._server = None
            return False

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                parts = line.decode("utf-8", errors="ignore").strip().split()
                if not parts:
                    writer.write(b"ERR\n")
                    await writer.drain()
                    return
                cmd = parts[0]
                if cmd == "PING" and len(parts) == 1:
                    writer.write(f"{BLINDBOX_LOCAL_REPLICA_MAGIC}\n".encode("utf-8"))
                    await writer.drain()
                    continue
                if cmd == "CAPA" and len(parts) <= 2:
                    if not self._is_authorized(parts, token_index=1):
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    writer.write(f"{BLINDBOX_QUEUE_CAPS_MAGIC}\n".encode("utf-8"))
                    await writer.drain()
                    continue
                if cmd == "AUTH" and len(parts) == 2:
                    if self._is_probe_authorized(parts[1]):
                        writer.write(b"OK\n")
                    else:
                        writer.write(b"ERR\n")
                    await writer.drain()
                    continue
                if cmd == "QPUT" and len(parts) >= 7:
                    if not self._is_authorized(parts, token_index=7):
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    queue_id = parts[1]
                    key = parts[2]
                    try:
                        size = int(parts[3])
                    except Exception:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    if size <= 0 or size > self.max_blob_size:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    put_cap = parts[4]
                    get_cap = parts[5]
                    delete_cap = parts[6]
                    body = await reader.readexactly(size)
                    queue = self._queues.get(queue_id)
                    if queue is None:
                        queue = _QueueRecord(
                            put_cap=put_cap,
                            get_cap=get_cap,
                            delete_cap=delete_cap,
                        )
                        self._queues[queue_id] = queue
                    elif (
                        not hmac.compare_digest(queue.put_cap, put_cap)
                        or not hmac.compare_digest(queue.get_cap, get_cap)
                        or not hmac.compare_digest(queue.delete_cap, delete_cap)
                    ):
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    if key in queue.items:
                        writer.write(b"EXISTS\n")
                    else:
                        if self._entry_count() >= self.max_entries:
                            writer.write(b"FULL\n")
                            await writer.drain()
                            return
                        queue.items[key] = body
                        writer.write(b"OK\n")
                    await writer.drain()
                    return
                if cmd == "QGET" and len(parts) >= 4:
                    if not self._is_authorized(parts, token_index=4):
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    queue_id = parts[1]
                    key = parts[2]
                    queue = self._queues.get(queue_id)
                    if queue is None or not hmac.compare_digest(queue.get_cap, parts[3]):
                        writer.write(b"MISS\n")
                        await writer.drain()
                        return
                    data = queue.items.get(key)
                    if data is None:
                        writer.write(b"MISS\n")
                        await writer.drain()
                        return
                    writer.write(f"OK {len(data)}\n".encode("utf-8"))
                    writer.write(data)
                    await writer.drain()
                    return
                if cmd == "QDEL" and len(parts) >= 4:
                    if not self._is_authorized(parts, token_index=4):
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                    queue_id = parts[1]
                    key = parts[2]
                    queue = self._queues.get(queue_id)
                    if queue is None or not hmac.compare_digest(
                        queue.delete_cap, parts[3]
                    ):
                        writer.write(b"MISS\n")
                        await writer.drain()
                        return
                    if key in queue.items:
                        del queue.items[key]
                        writer.write(b"OK\n")
                    else:
                        writer.write(b"MISS\n")
                    await writer.drain()
                    return
                writer.write(b"ERR\n")
                await writer.drain()
                return
        finally:
            writer.close()
            await writer.wait_closed()

    def _is_authorized(self, parts: list[str], *, token_index: int) -> bool:
        if not self.auth_token:
            return True
        if len(parts) <= token_index:
            return False
        return hmac.compare_digest(parts[token_index], self.auth_token)

    def _is_probe_authorized(self, token: str) -> bool:
        if not self.auth_token:
            return True
        return hmac.compare_digest(str(token), self.auth_token)

    def _entry_count(self) -> int:
        return sum(len(queue.items) for queue in self._queues.values())


_LOCAL_REPLICA_SERVER: Optional[BlindBoxLocalReplicaServer] = None


async def _probe_existing_local_replica(host: str, port: int, auth_token: str = "") -> bool:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except Exception:
        return False
    try:
        token = str(auth_token or "").strip()
        writer.write(b"PING\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=1.5)
        status = line.decode("utf-8", errors="ignore").strip()
        if status != BLINDBOX_LOCAL_REPLICA_MAGIC:
            return False
        if not token:
            return True
        writer.write(f"AUTH {token}\n".encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=1.5)
        return line.decode("utf-8", errors="ignore").strip() == "OK"
    except Exception:
        return False
    finally:
        writer.close()
        await writer.wait_closed()


async def ensure_local_blindbox_replica(
    *,
    host: str = "127.0.0.1",
    port: int = 19444,
    auth_token: str = "",
    max_entries: int = 4096,
) -> str:
    global _LOCAL_REPLICA_SERVER
    if _LOCAL_REPLICA_SERVER is None:
        _LOCAL_REPLICA_SERVER = BlindBoxLocalReplicaServer(
            host=host,
            port=port,
            auth_token=auth_token,
            max_entries=max_entries,
        )
    started = await _LOCAL_REPLICA_SERVER.start()
    if not started:
        if not await _probe_existing_local_replica(host, port, auth_token=auth_token):
            raise RuntimeError(
                f"Local Blind Box failed to bind on {host}:{port} (port busy?)"
            )
    return _LOCAL_REPLICA_SERVER.endpoint
