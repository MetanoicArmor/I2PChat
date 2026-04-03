from __future__ import annotations

import asyncio
import socket


def pick_free_tcp_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        return int(s.getsockname()[1])


def is_tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def wait_for_tcp(host: str, port: int, timeout: float = 30.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if is_tcp_open(host, port):
            return
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for TCP {host}:{port}")


async def probe_sam_hello(host: str, port: int, timeout: float = 5.0) -> None:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=timeout,
    )
    try:
        writer.write(b"HELLO VERSION MIN=3.1 MAX=3.1\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        text = line.decode("utf-8", errors="replace").strip()
        if "HELLO REPLY" not in text or "RESULT=OK" not in text:
            raise RuntimeError(f"Unexpected SAM HELLO reply: {text!r}")
    finally:
        writer.close()
        await writer.wait_closed()


async def wait_for_sam_ready(host: str, port: int, timeout: float = 45.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error: Exception | None = None
    while loop.time() < deadline:
        try:
            await probe_sam_hello(host, port, timeout=3.0)
            return
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.5)
    raise TimeoutError(f"SAM did not become ready on {host}:{port}: {last_error}")
