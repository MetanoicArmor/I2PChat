import asyncio
import struct
from dataclasses import dataclass
from typing import Optional, Set

MAGIC = b"\x89I2P"
PROTOCOL_VERSION = 4

# Header: MAGIC(4) | VER(1) | TYPE(1) | FLAGS(1) | MSG_ID(8) | LEN(4)
HEADER_STRUCT = struct.Struct(">4sBBBQI")
HEADER_SIZE = HEADER_STRUCT.size

FLAG_ENCRYPTED = 0x01
ENCRYPTED_TRAILER_SIZE = 8 + 32  # seq + mac


@dataclass
class DecodedFrame:
    msg_type: str
    payload: bytes
    flags: int
    msg_id: int
    is_legacy: bool = False


class ProtocolCodec:
    """
    vNext framing codec.

    Supports:
    - Binary vNext frames with MAGIC and explicit protocol version
    - Optional explicit legacy mode (no auto-fallback detection)
    """

    def __init__(
        self,
        allowed_types: Set[str],
        max_frame_body: int,
        allow_legacy: bool = False,
        resync_limit: int = 64 * 1024,
    ) -> None:
        self.allowed_types = allowed_types
        self.max_frame_body = max_frame_body
        self.allow_legacy = allow_legacy
        self.resync_limit = max(HEADER_SIZE, resync_limit)

    def encode(self, msg_type: str, payload: bytes, msg_id: int, flags: int = 0) -> bytes:
        if msg_type not in self.allowed_types:
            raise ValueError(f"Unknown message type: {msg_type!r}")
        if len(payload) > self.max_frame_body:
            raise ValueError(f"Frame too large: {len(payload)}")
        if len(msg_type) != 1:
            raise ValueError("Message type must be one character")
        header = HEADER_STRUCT.pack(
            MAGIC,
            PROTOCOL_VERSION,
            ord(msg_type),
            flags & 0xFF,
            msg_id & 0xFFFFFFFFFFFFFFFF,
            len(payload),
        )
        return header + payload

    async def read_frame(self, reader: asyncio.StreamReader) -> DecodedFrame:
        first = await reader.readexactly(1)
        if self.allow_legacy:
            # Legacy parsing is opt-in policy, not auto-detection by stream bytes.
            return await self._read_legacy_frame(reader, first)
        return await self._read_vnext_frame(reader, first)

    async def _read_vnext_frame(
        self, reader: asyncio.StreamReader, first_byte: bytes
    ) -> DecodedFrame:
        sync_buf = bytearray(first_byte)
        scanned = 1
        while bytes(sync_buf) != MAGIC:
            nxt = await reader.readexactly(1)
            sync_buf.extend(nxt)
            scanned += 1
            if len(sync_buf) > len(MAGIC):
                del sync_buf[0]
            if scanned > self.resync_limit:
                raise ValueError("Resync limit exceeded while searching for MAGIC")

        rest = await reader.readexactly(HEADER_SIZE - len(MAGIC))
        version, type_byte, flags, msg_id, msg_len = struct.unpack(">BBBQI", rest)

        if version != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported protocol version: {version}")
        msg_type = chr(type_byte)
        if msg_type not in self.allowed_types:
            raise ValueError(f"Unknown frame type: {msg_type!r}")
        if msg_len > self.max_frame_body:
            raise ValueError(f"Frame too large: {msg_len}")

        payload = await reader.readexactly(msg_len)
        return DecodedFrame(
            msg_type=msg_type,
            payload=payload,
            flags=flags,
            msg_id=msg_id,
            is_legacy=False,
        )

    async def _read_legacy_frame(
        self, reader: asyncio.StreamReader, first_type_byte: bytes
    ) -> DecodedFrame:
        msg_type = first_type_byte.decode("utf-8", errors="strict")
        if msg_type not in self.allowed_types:
            raise ValueError(f"Invalid legacy message type: {msg_type!r}")

        first_len_byte = await reader.readexactly(1)
        is_encrypted = first_len_byte == b"E"
        if is_encrypted:
            seq = await reader.readexactly(8)
            length_bytes = await reader.readexactly(6)
        else:
            length_bytes = first_len_byte + await reader.readexactly(3)
            seq = b""

        try:
            msg_len = int(length_bytes.decode("ascii"))
        except ValueError as exc:
            raise ValueError("Invalid legacy length field") from exc
        if msg_len < 0 or msg_len > self.max_frame_body:
            raise ValueError(f"Legacy frame too large: {msg_len}")

        body = await reader.readexactly(msg_len)
        trailer = b""
        if is_encrypted:
            trailer = await reader.readexactly(32)
        delim = await reader.readexactly(1)
        if delim != b"\n":
            raise ValueError("Invalid legacy delimiter")

        payload = body if not is_encrypted else (seq + body + trailer)
        flags = FLAG_ENCRYPTED if is_encrypted else 0
        return DecodedFrame(
            msg_type=msg_type,
            payload=payload,
            flags=flags,
            msg_id=0,
            is_legacy=True,
        )
