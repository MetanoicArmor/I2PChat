from __future__ import annotations

from .client import SAMClient, open_stream_accept, open_stream_connect
from .destination import Destination

__all__ = [
    "create_session",
    "dest_lookup",
    "naming_lookup",
    "new_destination",
    "stream_accept",
    "stream_connect",
]


async def new_destination(
    *,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
    sig_type: int = 7,
) -> Destination:
    client = SAMClient(sam_address=sam_address)
    await client.open()
    try:
        _public, private = await client.dest_generate(sig_type=sig_type)
        return private
    finally:
        await client.close()


async def naming_lookup(
    name: str,
    *,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
) -> str:
    client = SAMClient(sam_address=sam_address)
    await client.open()
    try:
        return await client.naming_lookup(name)
    finally:
        await client.close()


async def dest_lookup(
    name: str,
    *,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
) -> Destination:
    client = SAMClient(sam_address=sam_address)
    await client.open()
    try:
        return Destination(await client.naming_lookup(name))
    finally:
        await client.close()


async def stream_connect(
    session_id: str,
    destination: str,
    *,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
):
    resolved_destination = destination
    if isinstance(destination, str) and not destination.endswith(".i2p"):
        resolved_destination = Destination(destination).base64
    elif isinstance(destination, str):
        resolved_destination = (await dest_lookup(destination, sam_address=sam_address)).base64
    return await open_stream_connect(
        session_id,
        resolved_destination,
        sam_address=sam_address,
    )


async def stream_accept(
    session_id: str,
    *,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
):
    return await open_stream_accept(session_id, sam_address=sam_address)


async def create_session(
    session_id: str,
    *,
    destination: Destination | str | None = None,
    sam_address: tuple[str, int] = ("127.0.0.1", 7656),
    options: dict[str, str] | None = None,
    session_create_timeout: float = 180.0,
):
    client = SAMClient(
        sam_address=sam_address,
        session_create_timeout=session_create_timeout,
    )
    await client.open()

    if destination is None:
        destination_text = "TRANSIENT"
        sig_type = Destination.default_sig_type
    elif isinstance(destination, Destination):
        if destination.private_key is None:
            raise ValueError("SESSION CREATE requires a private destination")
        destination_text = destination.private_key.base64
        sig_type = None
    else:
        if str(destination).strip() == "TRANSIENT":
            destination_text = "TRANSIENT"
            sig_type = Destination.default_sig_type
        else:
            destination_obj = Destination(destination, has_private_key=True)
            if destination_obj.private_key is None:
                raise ValueError("SESSION CREATE requires a private destination")
            destination_text = destination_obj.private_key.base64
            sig_type = None

    handle = await client.create_stream_session(
        session_id,
        destination_text,
        sig_type=sig_type,
        options=options or {},
    )
    return handle.reader, handle.writer
