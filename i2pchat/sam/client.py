from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .destination import Destination
from .errors import ProtocolError, SessionClosed
from .protocol import (
    build_dest_generate,
    build_hello,
    build_naming_lookup,
    build_session_create,
    build_stream_accept,
    build_stream_connect,
    expect_ok,
    parse_reply_line,
)


@dataclass(slots=True)
class SessionHandle:
    session_id: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


class SAMClient:
    """Async SAM control connection: HELLO, DEST GENERATE, NAMING LOOKUP, SESSION CREATE."""

    def __init__(
        self,
        sam_address: tuple[str, int] = ("127.0.0.1", 7656),
        *,
        hello_timeout: float = 30.0,
        io_timeout: float = 30.0,
        session_create_timeout: float = 180.0,
    ) -> None:
        self.sam_address = sam_address
        self.hello_timeout = hello_timeout
        self.io_timeout = io_timeout
        self.session_create_timeout = session_create_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        if self._writer is not None:
            return
        reader, writer = await asyncio.open_connection(*self.sam_address)
        self._reader = reader
        self._writer = writer
        try:
            await self._write_and_expect(build_hello(), timeout=self.hello_timeout)
        except Exception:
            writer.close()
            await writer.wait_closed()
            self._reader = None
            self._writer = None
            raise

    async def close(self) -> None:
        if self._writer is None:
            return
        self._writer.close()
        await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def dest_generate(self, sig_type: int = 7) -> tuple[Destination, Destination]:
        reply = await self._write_and_expect(build_dest_generate(sig_type))
        pub = reply.fields.get("PUB")
        priv = reply.fields.get("PRIV")
        if not pub or not priv:
            raise ValueError("DEST GENERATE reply missing PUB/PRIV")
        return Destination(pub), Destination(priv, has_private_key=True)

    async def naming_lookup(self, name: str) -> str:
        reply = await self._write_and_expect(build_naming_lookup(name))
        value = reply.fields.get("VALUE")
        if not value:
            raise ValueError("NAMING LOOKUP reply missing VALUE")
        return value

    async def create_stream_session(
        self,
        session_id: str,
        destination: str,
        *,
        sig_type: int | None = None,
        options: dict[str, str] | None = None,
    ) -> SessionHandle:
        payload = build_session_create(
            "STREAM",
            session_id,
            destination,
            sig_type=sig_type,
            options=options,
        )
        for attempt in range(2):
            try:
                await self._write_and_expect(
                    payload,
                    timeout=self.session_create_timeout,
                )
                break
            except asyncio.TimeoutError as exc:
                raise ProtocolError(
                    message=(
                        f"Timed out waiting for SESSION STATUS after {self.session_create_timeout:.0f}s. "
                        "Routers often reply only after tunnel build (can exceed 1–2 min). "
                        "Increase I2PCHAT_SAM_SESSION_CREATE_TIMEOUT if needed."
                    ),
                    raw_line="",
                ) from exc
            except ProtocolError as exc:
                # i2pd sometimes closes the control socket or sends EOF right after SESSION CREATE
                # under load (see router.log "Read error: End of file"). One fresh TCP+HELLO retry.
                if (
                    attempt == 0
                    and (exc.message or "").strip() == "Empty SAM reply"
                ):
                    await self.close()
                    await asyncio.sleep(0.2)
                    await self.open()
                    continue
                raise
        if self._reader is None or self._writer is None:
            raise SessionClosed(message="SAM client is not open")
        return SessionHandle(session_id=session_id, reader=self._reader, writer=self._writer)

    async def _write_and_expect(self, payload: bytes, *, timeout: float | None = None):
        if self._reader is None or self._writer is None:
            raise SessionClosed(message="SAM client is not open")
        self._writer.write(payload)
        await self._writer.drain()
        line = await asyncio.wait_for(
            self._reader.readline(),
            timeout=self.io_timeout if timeout is None else timeout,
        )
        return expect_ok(parse_reply_line(line))


async def open_stream_connect(
    session_id: str,
    destination: str,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
    *,
    hello_timeout: float = 30.0,
    io_timeout: float = 30.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(*sam_address)
    try:
        writer.write(build_hello())
        await writer.drain()
        hello_line = await asyncio.wait_for(reader.readline(), timeout=hello_timeout)
        expect_ok(parse_reply_line(hello_line))

        writer.write(build_stream_connect(session_id, destination, silent="false"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=io_timeout)
        expect_ok(parse_reply_line(line))
        return reader, writer
    except Exception:
        writer.close()
        await writer.wait_closed()
        raise


async def open_stream_accept(
    session_id: str,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
    *,
    hello_timeout: float = 30.0,
    io_timeout: float = 30.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(*sam_address)
    try:
        writer.write(build_hello())
        await writer.drain()
        hello_line = await asyncio.wait_for(reader.readline(), timeout=hello_timeout)
        expect_ok(parse_reply_line(hello_line))

        writer.write(build_stream_accept(session_id))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=io_timeout)
        expect_ok(parse_reply_line(line))
        return reader, writer
    except Exception:
        writer.close()
        await writer.wait_closed()
        raise
