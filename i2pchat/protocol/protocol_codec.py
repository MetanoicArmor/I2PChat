import asyncio
import struct
from dataclasses import dataclass
from typing import Set

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


class ProtocolCodec:
    """
    vNext framing codec: binary frames with MAGIC and explicit protocol version.
    """

    def __init__(
        self,
        allowed_types: Set[str],
        max_frame_body: int,
        resync_limit: int = 64 * 1024,
    ) -> None:
        self.allowed_types = allowed_types
        self.max_frame_body = max_frame_body
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
        )
