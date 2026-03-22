"""
Local BlindBox replica server (best-effort convenience fallback).

Protocol:
- PUT <key> <size>\n + <size bytes> -> OK | EXISTS | ERR
- GET <key>\n -> MISS | OK <size>\n + <size bytes>
"""

from __future__ import annotations

import asyncio
from typing import Optional


class BlindBoxLocalReplicaServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 19444,
        max_blob_size: int = 1_048_576,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.max_blob_size = int(max_blob_size)
        self._server: Optional[asyncio.AbstractServer] = None
        self._storage: dict[str, bytes] = {}

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
            line = await reader.readline()
            parts = line.decode("utf-8", errors="ignore").strip().split()
            if not parts:
                writer.write(b"ERR\n")
                await writer.drain()
                return
            cmd = parts[0]
            if cmd == "PUT" and len(parts) >= 3:
                key = parts[1]
                try:
                    size = int(parts[2])
                except Exception:
                    writer.write(b"ERR\n")
                    await writer.drain()
                    return
                if size <= 0 or size > self.max_blob_size:
                    writer.write(b"ERR\n")
                    await writer.drain()
                    return
                body = await reader.readexactly(size)
                if key in self._storage:
                    writer.write(b"EXISTS\n")
                else:
                    self._storage[key] = body
                    writer.write(b"OK\n")
                await writer.drain()
                return
            if cmd == "GET" and len(parts) >= 2:
                key = parts[1]
                data = self._storage.get(key)
                if data is None:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                writer.write(f"OK {len(data)}\n".encode("utf-8"))
                writer.write(data)
                await writer.drain()
                return
            writer.write(b"ERR\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


_LOCAL_REPLICA_SERVER: Optional[BlindBoxLocalReplicaServer] = None


async def _probe_existing_local_replica(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except Exception:
        return False
    try:
        writer.write(b"GET __health__\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=1.5)
        status = line.decode("utf-8", errors="ignore").strip()
        return status == "MISS" or status.startswith("OK ") or status == "ERR"
    except Exception:
        return False
    finally:
        writer.close()
        await writer.wait_closed()


async def ensure_local_blindbox_replica(
    *,
    host: str = "127.0.0.1",
    port: int = 19444,
) -> str:
    global _LOCAL_REPLICA_SERVER
    if _LOCAL_REPLICA_SERVER is None:
        _LOCAL_REPLICA_SERVER = BlindBoxLocalReplicaServer(host=host, port=port)
    started = await _LOCAL_REPLICA_SERVER.start()
    if not started:
        if not await _probe_existing_local_replica(host, port):
            raise RuntimeError(
                f"Local BlindBox replica failed to bind on {host}:{port} (port busy?)"
            )
    return _LOCAL_REPLICA_SERVER.endpoint
