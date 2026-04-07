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
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from i2pchat import sam as i2plib
from i2pchat.blindbox.blindbox_blob import BLINDBOX_MAX_FRAME_SIZE
from i2pchat.sam import protocol as sam_protocol
from i2pchat.sam.errors import LegacySAMException, ProtocolError
from i2pchat.sam.protocol import (
    build_stream_connect,
    expect_ok,
    parse_reply_line,
)

logger = logging.getLogger("i2pchat")


def _expect_sam_line_ok(line: bytes) -> None:
    """Parse a single SAM reply; same success rules as i2pchat.sam (incl. i2pd SESSION/DEST quirks)."""
    try:
        expect_ok(parse_reply_line(line))
    except ProtocolError as exc:
        text = line.decode("utf-8", errors="ignore").strip()
        if not text:
            raise RuntimeError("(no response / disconnected)") from exc
        # Prefer the wire line for errors (router-specific RESULT / MESSAGE).
        raise RuntimeError(text) from exc


def _sam_exc_detail(exc: BaseException) -> str:
    """SAM errors sometimes have empty str(); still show something useful."""
    text = str(exc).strip()
    if text:
        return text
    if isinstance(exc, LegacySAMException):
        doc = (type(exc).__doc__ or "").strip()
        first = doc.split("\n", 1)[0] if doc else ""
        if first:
            return f"{type(exc).__name__} — {first}"
    return type(exc).__name__

StreamPair = tuple[asyncio.StreamReader, asyncio.StreamWriter]
StreamFactory = Callable[[str], Awaitable[StreamPair]]
BlobAcceptor = Callable[[bytes], bool | Awaitable[bool]]


@dataclass(frozen=True)
class BlindBoxPutResult:
    """One successful PUT to a Blind Box endpoint (address is host:port or .b32.i2p…)."""

    address: str
    status: str


class BlindBoxClient:
    def __init__(
        self,
        session_id: str,
        blind_boxes: list[str],
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
        # HELLO + SESSION CREATE can block a long time on a busy Java I2P router.
        sam_session_timeout: float = 120.0,
        # Optional token sent only to loopback host:port replicas when no per-replica map entry.
        local_auth_token: str = "",
        # Optional per-endpoint secrets (exact address string as in blind_boxes).
        replica_auth: Optional[Dict[str, str]] = None,
        # Hard safety cap for GET response body size.
        max_get_blob_size: int = BLINDBOX_MAX_FRAME_SIZE,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if not blind_boxes:
            raise ValueError("blind_boxes list cannot be empty")
        if put_quorum < 1 or put_quorum > len(blind_boxes):
            raise ValueError("put_quorum must be in range 1..len(blind_boxes)")
        if get_quorum < 1 or get_quorum > len(blind_boxes):
            raise ValueError("get_quorum must be in range 1..len(blind_boxes)")
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be >= 1")
        if io_timeout <= 0:
            raise ValueError("io_timeout must be positive")
        if sam_session_timeout <= 0:
            raise ValueError("sam_session_timeout must be positive")
        if int(max_get_blob_size) <= 0:
            raise ValueError("max_get_blob_size must be positive")

        self.session_id = session_id
        # SAM session nickname passed to SESSION CREATE / STREAM CONNECT. Rotated on
        # each successful setup so a stale session left on the router after close()
        # cannot cause RESULT=DUPLICATED_ID on the next start().
        self._active_sam_id = session_id
        self.blind_boxes = list(blind_boxes)
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
        self.sam_session_timeout = sam_session_timeout
        self.local_auth_token = str(local_auth_token or "").strip()
        ra: dict[str, str] = {}
        if replica_auth:
            for k, v in replica_auth.items():
                ks = str(k).strip()
                vs = str(v).strip() if v is not None else ""
                if ks and vs:
                    ra[ks] = vs
        self.replica_auth = ra
        self.max_get_blob_size = int(max_get_blob_size)

        self._ctrl_reader: Optional[asyncio.StreamReader] = None
        self._ctrl_writer: Optional[asyncio.StreamWriter] = None
        self._started = False
        # Serialize start(): poll loop and put()/get() can otherwise race and issue
        # two SESSION CREATE with the same ID → SAM RESULT=DUPLICATED_ID.
        self._start_lock = asyncio.Lock()
        # Throttle identical Blind Box failure logs (poller hammers GET).
        self._box_warn_next: dict[str, float] = {}

    @staticmethod
    def _validate_blindbox_key(key: str) -> str:
        token = str(key or "").strip()
        if not token:
            raise ValueError("key is required")
        if any(ch in token for ch in ("\r", "\n", "\x00", " ", "\t")):
            raise ValueError("key contains forbidden characters")
        return token

    def _log_box_failure(self, op: str, box_addr: str, err: Exception) -> None:
        now = time.monotonic()
        key = f"{op}|{box_addr}|{type(err).__name__}|{err!s}"
        if now < self._box_warn_next.get(key, 0):
            logger.debug(
                "Blind Box %s failed (%s) (suppressed): %s", op, box_addr, err
            )
            return
        self._box_warn_next[key] = now + 90.0
        logger.warning("Blind Box %s failed (%s): %s", op, box_addr, err)

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
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
            t_sess = self.sam_session_timeout

            self._ctrl_reader, self._ctrl_writer = await asyncio.open_connection(
                self.sam_host, self.sam_port
            )
            try:
                await self._sam_hello(
                    self._ctrl_reader, self._ctrl_writer, line_timeout=t_sess
                )
            except asyncio.TimeoutError as exc:
                writer = self._ctrl_writer
                self._ctrl_writer = None
                self._ctrl_reader = None
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"SAM timed out ({t_sess:g}s) waiting for HELLO reply "
                    f"({self.sam_host}:{self.sam_port}). "
                    "Is I2P running? Increase I2PCHAT_BLINDBOX_SAM_SESSION_TIMEOUT if needed."
                ) from exc

            self._active_sam_id = f"{self.session_id}_{secrets.token_hex(4)}"
            cmd = sam_protocol.session_create(
                "STREAM",
                self._active_sam_id,
                "TRANSIENT",
                options=self.sam_options,
                sig_type=7,
            )
            self._ctrl_writer.write(cmd)
            await self._ctrl_writer.drain()
            try:
                response = await asyncio.wait_for(
                    self._ctrl_reader.readline(), timeout=t_sess
                )
            except asyncio.TimeoutError as exc:
                writer = self._ctrl_writer
                self._ctrl_writer = None
                self._ctrl_reader = None
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"SAM timed out ({t_sess:g}s) during Blind Box SESSION CREATE "
                    f"({self.sam_host}:{self.sam_port}). "
                    "I2P may still be building tunnels — wait and retry, or increase "
                    "I2PCHAT_BLINDBOX_SAM_SESSION_TIMEOUT."
                ) from exc
            try:
                _expect_sam_line_ok(response)
            except RuntimeError as exc:
                # Drop half-open ctrl connection so a retry can open a fresh socket.
                writer = self._ctrl_writer
                self._ctrl_writer = None
                self._ctrl_reader = None
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                inner = str(exc)
                if inner == "(no response / disconnected)":
                    detail = (
                        "SAM session create failed: (no response / disconnected — "
                        f"is I2P running and SAM enabled on {self.sam_host}:{self.sam_port}?)"
                    )
                else:
                    detail = f"SAM session create failed: {inner}"
                raise RuntimeError(detail) from exc
            self._started = True

    def is_runtime_ready(self) -> bool:
        """True after start() completes (SAM session or direct mode ready for I/O)."""
        return self._started

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
        key = self._validate_blindbox_key(key)
        if not isinstance(blob, (bytes, bytearray)) or len(blob) == 0:
            raise ValueError("blob must be non-empty bytes")

        ok_results: list[BlindBoxPutResult] = []
        put_failures: list[tuple[str, Exception]] = []
        success_count = 0
        pending: dict[asyncio.Task[BlindBoxPutResult], str] = {
            asyncio.create_task(self._put_to_blind_box(addr, key, bytes(blob))): addr
            for addr in self.blind_boxes
        }
        try:
            while pending:
                task_addrs = dict(pending)
                done, pending_set = await asyncio.wait(
                    pending.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                pending = {task: task_addrs[task] for task in pending_set}
                for task in done:
                    addr = task_addrs.get(task, "")
                    try:
                        result = task.result()
                    except Exception as exc:
                        put_failures.append((addr, exc))
                        continue
                    ok_results.append(result)
                    if result.status == "OK":
                        success_count += 1
                    elif result.status == "EXISTS":
                        try:
                            existing_blob = await self._get_from_blind_box(result.address, key)
                        except Exception as exc:
                            put_failures.append(
                                (
                                    result.address,
                                    RuntimeError(f"PUT EXISTS verification failed: {exc}"),
                                )
                            )
                            continue
                        if existing_blob == bytes(blob):
                            success_count += 1
                        else:
                            put_failures.append(
                                (
                                    result.address,
                                    RuntimeError("PUT EXISTS verification mismatch"),
                                )
                            )
                    if success_count >= self.put_quorum:
                        await self._cancel_pending_tasks(list(pending.keys()))
                        pending.clear()
                        break
        finally:
            await self._cancel_pending_tasks(list(pending.keys()))
        if success_count < self.put_quorum:
            for addr, err in put_failures:
                self._log_box_failure("PUT", addr, err)
            raise RuntimeError(
                f"Blind Box PUT quorum not reached: {success_count}/{self.put_quorum}"
            )
        # Quorum met: another Blind Box carried the message — failures are non-fatal noise.
        for addr, err in put_failures:
            logger.debug(
                "Blind Box PUT: %s failed (quorum already satisfied): %s",
                addr,
                err,
            )
        return ok_results

    async def get(self, key: str, *, require_quorum: bool = True) -> list[bytes]:
        if not self._started:
            await self.start()
        key = self._validate_blindbox_key(key)

        tasks = [
            asyncio.create_task(self._get_from_blind_box(addr, key))
            for addr in self.blind_boxes
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        blobs: list[bytes] = []
        get_failures: list[tuple[str, Exception]] = []
        for addr, result in zip(self.blind_boxes, results):
            if isinstance(result, Exception):
                get_failures.append((addr, result))
                continue
            if result is not None:
                blobs.append(result)

        if require_quorum and len(blobs) < self.get_quorum:
            for addr, err in get_failures:
                self._log_box_failure("GET", addr, err)
            raise RuntimeError(
                f"Blind Box GET quorum not reached: {len(blobs)}/{self.get_quorum}"
            )
        for addr, err in get_failures:
            logger.debug(
                "Blind Box GET: %s failed (quorum already satisfied): %s",
                addr,
                err,
            )
        return self._dedup_blobs(blobs)

    async def get_first_accepted(
        self,
        key: str,
        *,
        accept_blob: BlobAcceptor,
    ) -> Optional[bytes]:
        if not self._started:
            await self.start()
        key = self._validate_blindbox_key(key)

        pending: dict[asyncio.Task[Optional[bytes]], str] = {
            asyncio.create_task(self._get_from_blind_box(addr, key)): addr
            for addr in self.blind_boxes
        }
        try:
            while pending:
                task_addrs = dict(pending)
                done, pending_set = await asyncio.wait(
                    pending.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                pending = {task: task_addrs[task] for task in pending_set}
                for task in done:
                    try:
                        blob = task.result()
                    except Exception:
                        continue
                    if blob is None:
                        continue
                    accepted = accept_blob(blob)
                    if asyncio.iscoroutine(accepted):
                        accepted = await accepted
                    if accepted:
                        await self._cancel_pending_tasks(list(pending.keys()))
                        pending.clear()
                        return blob
            return None
        finally:
            await self._cancel_pending_tasks(list(pending.keys()))

    async def _put_to_blind_box(
        self, box_addr: str, key: str, blob: bytes
    ) -> BlindBoxPutResult:
        async def _op() -> BlindBoxPutResult:
            reader, writer = await self._connect(box_addr)
            try:
                auth_suffix = self._command_auth_suffix(box_addr)
                writer.write(
                    f"PUT {key} {len(blob)}{auth_suffix}\n".encode("utf-8")
                )
                writer.write(blob)
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=self.io_timeout)
                status = line.decode("utf-8", errors="ignore").strip()
                if status not in {"OK", "EXISTS"}:
                    raise RuntimeError(f"Unexpected PUT response: {status!r}")
                return BlindBoxPutResult(address=box_addr, status=status)
            finally:
                await self._safe_close(writer)

        return await self._with_retries(_op, op_name=f"PUT {box_addr}")

    async def _get_from_blind_box(self, box_addr: str, key: str) -> Optional[bytes]:
        async def _op() -> Optional[bytes]:
            reader, writer = await self._connect(box_addr)
            try:
                auth_suffix = self._command_auth_suffix(box_addr)
                writer.write(f"GET {key}{auth_suffix}\n".encode("utf-8"))
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
                if size <= 0:
                    raise RuntimeError("Invalid blob size in GET response")
                if size > self.max_get_blob_size:
                    raise RuntimeError(
                        f"GET blob size {size} exceeds limit {self.max_get_blob_size}"
                    )
                return await asyncio.wait_for(reader.readexactly(size), timeout=self.io_timeout)
            finally:
                await self._safe_close(writer)

        return await self._with_retries(_op, op_name=f"GET {box_addr}")

    def _token_for_endpoint(self, box_addr: str) -> str:
        addr = (box_addr or "").strip()
        mapped = self.replica_auth.get(addr, "").strip()
        if mapped:
            return mapped
        token = self.local_auth_token
        if token and self._is_loopback_endpoint(addr):
            return token
        return ""

    def _command_auth_suffix(self, box_addr: str) -> str:
        t = self._token_for_endpoint(box_addr)
        return f" {t}" if t else ""

    @staticmethod
    def _is_loopback_endpoint(box_addr: str) -> bool:
        try:
            host, _port = box_addr.rsplit(":", 1)
            host = host.strip().lower().strip("[]")
        except Exception:
            return False
        return host in {"127.0.0.1", "localhost", "::1"}

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
        if last_exc is None:
            raise RuntimeError(
                f"{op_name}: retry loop exhausted without recording an exception"
            )
        raise last_exc

    async def _connect(self, box_addr: str) -> StreamPair:
        if self.stream_factory is not None:
            return await self.stream_factory(box_addr)
        if not self.use_sam:
            host, port_raw = box_addr.rsplit(":", 1)
            host = host.strip()
            port = int(port_raw)
            try:
                return await asyncio.open_connection(
                    host, port, family=socket.AF_INET
                )
            except OSError:
                # Fallback for environments where AF selection fails unexpectedly.
                return await asyncio.open_connection(host, port)
        return await self._connect_via_sam(box_addr)

    @staticmethod
    def _sam_destination_from_endpoint(endpoint: str) -> str:
        """
        SAM STREAM CONNECT requires a valid I2P DESTINATION (.b32.i2p, .i2p, or Base64).
        Config lines like ``host.b32.i2p:19444`` use ``:19444`` as a human hint for the
        Blind Box TCP port on the remote side, not as part of the SAM destination string.
        Passing the suffix causes ``RESULT=INVALID_KEY``.
        """
        s = endpoint.strip()
        if ":" not in s:
            return s
        host_part, port_part = s.rsplit(":", 1)
        if port_part.isdigit() and host_part.endswith(".i2p"):
            return host_part
        return s

    async def _open_sam_stream_to(self, dest_for_sam: str) -> StreamPair:
        """New SAM TCP connection: HELLO + STREAM CONNECT (uses BlindBox session ID)."""
        dest_for_sam = self._validate_sam_destination(dest_for_sam)
        reader, writer = await asyncio.open_connection(self.sam_host, self.sam_port)
        try:
            await self._sam_hello(reader, writer)
            writer.write(
                build_stream_connect(
                    self._active_sam_id,
                    dest_for_sam,
                    silent="false",
                )
            )
            await writer.drain()
            response = await asyncio.wait_for(
                reader.readline(), timeout=self.io_timeout
            )
            try:
                _expect_sam_line_ok(response)
            except RuntimeError as exc:
                await self._safe_close(writer)
                inner = str(exc)
                if inner == "(no response / disconnected)":
                    raise RuntimeError(
                        "SAM STREAM CONNECT failed: (no response — "
                        "Blind Box SAM session may not be started)"
                    ) from exc
                raise RuntimeError(f"SAM STREAM CONNECT failed: {inner}") from exc
            return reader, writer
        except Exception:
            await self._safe_close(writer)
            raise

    @staticmethod
    def _validate_sam_destination(dest_for_sam: str) -> str:
        value = str(dest_for_sam or "").strip()
        if not value:
            raise RuntimeError("Empty SAM destination")
        if any(ch in value for ch in ("\r", "\n", "\x00", " ", "\t")):
            raise RuntimeError("Invalid SAM destination")
        return value

    async def _connect_via_sam(self, box_addr: str) -> StreamPair:
        destination = self._sam_destination_from_endpoint(box_addr)
        sam_address = (self.sam_host, self.sam_port)
        connect_order: List[str] = []

        if destination.endswith(".b32.i2p"):
            # Prefer NAMING LOOKUP → Base64 (same order as stream_connect in i2pchat.sam). Some routers
            # have no LeaseSet yet → KEY_NOT_FOUND; then raw .b32.i2p may still work.
            try:
                dest_obj = await i2plib.dest_lookup(
                    destination, sam_address=sam_address
                )
                connect_order.append(dest_obj.base64)
            except Exception as e:
                logger.info(
                    "Blind Box NAMING LOOKUP failed for %s (%s); will try raw .b32.i2p",
                    destination,
                    _sam_exc_detail(e),
                )
            connect_order.append(destination)
        elif destination.endswith(".i2p"):
            try:
                dest_obj = await i2plib.dest_lookup(
                    destination, sam_address=sam_address
                )
                connect_order.append(dest_obj.base64)
            except Exception as e:
                raise RuntimeError(
                    f"I2P dest_lookup failed for Blind Box {destination!r}: "
                    f"{_sam_exc_detail(e)}"
                ) from e
        else:
            connect_order.append(destination)

        errors: list[str] = []
        for dest_str in connect_order:
            try:
                return await self._open_sam_stream_to(dest_str)
            except RuntimeError as e:
                errors.append(str(e))
                continue
        raise RuntimeError(
            "Blind Box SAM STREAM CONNECT failed for all destination forms tried ("
            + "; ".join(errors)
            + ")"
        )

    async def _sam_hello(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        line_timeout: Optional[float] = None,
    ) -> None:
        writer.write(b"HELLO VERSION MIN=3.0 MAX=3.2\n")
        await writer.drain()
        t = self.io_timeout if line_timeout is None else line_timeout
        response = await asyncio.wait_for(reader.readline(), timeout=t)
        try:
            expect_ok(parse_reply_line(response))
        except ProtocolError as exc:
            raw = response.decode("utf-8", errors="ignore").strip()
            if not raw:
                raise RuntimeError(
                    "SAM HELLO failed: (no response / disconnected — "
                    f"check I2P router and SAM on {self.sam_host}:{self.sam_port})"
                ) from exc
            raise RuntimeError(f"SAM HELLO failed: {raw}") from exc

    @staticmethod
    async def _safe_close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    @staticmethod
    async def _cancel_pending_tasks(tasks: list[asyncio.Task[Any]]) -> None:
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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
