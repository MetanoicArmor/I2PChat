"""
BlindBox client for multiple Blind Box server endpoints (.b32.i2p over SAM or host:port).

Supports:
- quorum PUT/GET
- retry with exponential backoff
- deduplication of blobs returned from multiple boxes (content hash)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("i2pchat")

StreamPair = tuple[asyncio.StreamReader, asyncio.StreamWriter]
StreamFactory = Callable[[str], Awaitable[StreamPair]]


@dataclass(frozen=True)
class BlindBoxPutResult:
    replica: str
    status: str


class BlindBoxClient:
    def __init__(
        self,
        session_id: str,
        replicas: list[str],
        *,
        sam_host: str = "127.0.0.1",
        sam_port: int = 7656,
        sam_options: Optional[dict[str, str]] = None,
        use_sam: bool = True,
        stream_factory: Optional[StreamFactory] = None,
        put_quorum: int = 1,
        get_quorum: int = 1,
        retry_attempts: int = 3,
        retry_backoff_base: float = 0.25,
        io_timeout: float = 15.0,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if not replicas:
            raise ValueError("replicas list cannot be empty")
        if put_quorum < 1 or put_quorum > len(replicas):
            raise ValueError("put_quorum must be in range 1..len(replicas)")
        if get_quorum < 1 or get_quorum > len(replicas):
            raise ValueError("get_quorum must be in range 1..len(replicas)")
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be >= 1")
        if io_timeout <= 0:
            raise ValueError("io_timeout must be positive")

        self.session_id = session_id
        self.replicas = list(replicas)
        self.sam_host = sam_host
        self.sam_port = sam_port
        self.sam_options = sam_options or {
            "inbound.length": "2",
            "outbound.length": "2",
            "inbound.quantity": "2",
            "outbound.quantity": "2",
        }
        self.use_sam = bool(use_sam)
        self.stream_factory = stream_factory
        self.put_quorum = put_quorum
        self.get_quorum = get_quorum
        self.retry_attempts = retry_attempts
        self.retry_backoff_base = retry_backoff_base
        self.io_timeout = io_timeout

        self._ctrl_reader: Optional[asyncio.StreamReader] = None
        self._ctrl_writer: Optional[asyncio.StreamWriter] = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if not self.use_sam and self.stream_factory is None:
            # direct mode does not need SAM startup.
            self._started = True
            return
        if self.stream_factory is not None:
            # custom stream mode handles setup externally.
            self._started = True
            return
        self._ctrl_reader, self._ctrl_writer = await asyncio.open_connection(
            self.sam_host, self.sam_port
        )
        await self._sam_hello(self._ctrl_reader, self._ctrl_writer)
        options_str = " ".join(f"{k}={v}" for k, v in self.sam_options.items())
        cmd = (
            "SESSION CREATE STYLE=STREAM "
            f"ID={self.session_id} DESTINATION=TRANSIENT SIGNATURE_TYPE=7 OPTION {options_str}\n"
        )
        self._ctrl_writer.write(cmd.encode("utf-8"))
        await self._ctrl_writer.drain()
        response = await asyncio.wait_for(
            self._ctrl_reader.readline(), timeout=self.io_timeout
        )
        response_text = response.decode("utf-8", errors="ignore").strip()
        if "RESULT=OK" not in response_text:
            raise RuntimeError(f"SAM session create failed: {response_text}")
        self._started = True

    async def close(self) -> None:
        writer = self._ctrl_writer
        self._ctrl_writer = None
        self._ctrl_reader = None
        self._started = False
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def put(self, key: str, blob: bytes) -> list[BlindBoxPutResult]:
        if not self._started:
            await self.start()
        if not key:
            raise ValueError("key is required")
        if not isinstance(blob, (bytes, bytearray)) or len(blob) == 0:
            raise ValueError("blob must be non-empty bytes")

        tasks = [asyncio.create_task(self._put_to_replica(replica, key, bytes(blob))) for replica in self.replicas]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ok_results: list[BlindBoxPutResult] = []
        for replica, result in zip(self.replicas, results):
            if isinstance(result, Exception):
                logger.warning("BlindBox PUT failed for %s: %s", replica, result)
                continue
            ok_results.append(result)
        success_count = sum(1 for r in ok_results if r.status in {"OK", "EXISTS"})
        if success_count < self.put_quorum:
            raise RuntimeError(
                f"BlindBox PUT quorum not reached: {success_count}/{self.put_quorum}"
            )
        return ok_results

    async def get(self, key: str, *, require_quorum: bool = True) -> list[bytes]:
        if not self._started:
            await self.start()
        if not key:
            raise ValueError("key is required")

        tasks = [asyncio.create_task(self._get_from_replica(replica, key)) for replica in self.replicas]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        blobs: list[bytes] = []
        for replica, result in zip(self.replicas, results):
            if isinstance(result, Exception):
                logger.warning("BlindBox GET failed for %s: %s", replica, result)
                continue
            if result is not None:
                blobs.append(result)

        if require_quorum and len(blobs) < self.get_quorum:
            raise RuntimeError(
                f"BlindBox GET quorum not reached: {len(blobs)}/{self.get_quorum}"
            )
        return self._dedup_blobs(blobs)

    async def _put_to_replica(self, replica: str, key: str, blob: bytes) -> BlindBoxPutResult:
        async def _op() -> BlindBoxPutResult:
            reader, writer = await self._connect(replica)
            try:
                writer.write(f"PUT {key} {len(blob)}\n".encode("utf-8"))
                writer.write(blob)
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=self.io_timeout)
                status = line.decode("utf-8", errors="ignore").strip()
                if status not in {"OK", "EXISTS"}:
                    raise RuntimeError(f"Unexpected PUT response: {status!r}")
                return BlindBoxPutResult(replica=replica, status=status)
            finally:
                await self._safe_close(writer)

        return await self._with_retries(_op, op_name=f"PUT {replica}")

    async def _get_from_replica(self, replica: str, key: str) -> Optional[bytes]:
        async def _op() -> Optional[bytes]:
            reader, writer = await self._connect(replica)
            try:
                writer.write(f"GET {key}\n".encode("utf-8"))
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=self.io_timeout)
                header = line.decode("utf-8", errors="ignore").strip()
                if header == "MISS":
                    return None
                if not header.startswith("OK "):
                    raise RuntimeError(f"Unexpected GET response: {header!r}")
                try:
                    size = int(header.split(" ", 1)[1])
                except Exception as exc:
                    raise RuntimeError(f"Malformed GET header: {header!r}") from exc
                if size < 0:
                    raise RuntimeError("Negative blob size in GET response")
                return await asyncio.wait_for(reader.readexactly(size), timeout=self.io_timeout)
            finally:
                await self._safe_close(writer)

        return await self._with_retries(_op, op_name=f"GET {replica}")

    async def _with_retries(self, op, *, op_name: str):
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return await op()
            except Exception as exc:
                last_exc = exc
                if attempt >= self.retry_attempts:
                    break
                delay = self.retry_backoff_base * (2 ** (attempt - 1))
                logger.debug("%s attempt %d failed, retrying in %.3fs", op_name, attempt, delay)
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _connect(self, replica: str) -> StreamPair:
        if self.stream_factory is not None:
            return await self.stream_factory(replica)
        if not self.use_sam:
            host, port_raw = replica.rsplit(":", 1)
            host = host.strip()
            port = int(port_raw)
            try:
                return await asyncio.open_connection(
                    host, port, family=socket.AF_INET
                )
            except OSError:
                # Fallback for environments where AF selection fails unexpectedly.
                return await asyncio.open_connection(host, port)
        return await self._connect_via_sam(replica)

    async def _connect_via_sam(self, destination: str) -> StreamPair:
        reader, writer = await asyncio.open_connection(self.sam_host, self.sam_port)
        await self._sam_hello(reader, writer)
        cmd = f"STREAM CONNECT ID={self.session_id} DESTINATION={destination}\n"
        writer.write(cmd.encode("utf-8"))
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=self.io_timeout)
        response_text = response.decode("utf-8", errors="ignore").strip()
        if "RESULT=OK" not in response_text:
            await self._safe_close(writer)
            raise RuntimeError(f"SAM STREAM CONNECT failed: {response_text}")
        return reader, writer

    async def _sam_hello(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(b"HELLO VERSION MIN=3.0 MAX=3.2\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=self.io_timeout)
        response_text = response.decode("utf-8", errors="ignore").strip()
        if "RESULT=OK" not in response_text:
            raise RuntimeError(f"SAM HELLO failed: {response_text}")

    @staticmethod
    async def _safe_close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    @staticmethod
    def _dedup_blobs(blobs: list[bytes]) -> list[bytes]:
        seen: set[str] = set()
        unique: list[bytes] = []
        for blob in blobs:
            digest = hashlib.sha256(blob).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(blob)
        return unique
