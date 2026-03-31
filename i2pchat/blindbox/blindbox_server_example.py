#!/usr/bin/env python3
# Minimal local Blind Box replica for development (listens on 127.0.0.1 only).
# No authentication — not for untrusted networks. No API keys or secrets.
import asyncio
import hashlib
import os
import time

BASE = os.path.expanduser("~/.i2pchat-blindbox")
STORE = os.path.join(BASE, "store")
os.makedirs(STORE, exist_ok=True)

MAX_BLOB = 1_048_576
TTL_SEC = 14 * 24 * 3600  # 14 days

def path_for_key(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    d = os.path.join(STORE, h[:2])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h)

async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        line = await reader.readline()
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if not parts:
            writer.write(b"ERR\n"); await writer.drain(); return

        cmd = parts[0]
        if cmd == "PUT" and len(parts) >= 3:
            key = parts[1]
            try:
                size = int(parts[2])
            except Exception:
                writer.write(b"ERR\n"); await writer.drain(); return
            if size <= 0 or size > MAX_BLOB:
                writer.write(b"ERR\n"); await writer.drain(); return

            body = await reader.readexactly(size)
            p = path_for_key(key)
            if os.path.exists(p):
                writer.write(b"EXISTS\n")
            else:
                with open(p, "wb") as f:
                    f.write(body)
                writer.write(b"OK\n")
            await writer.drain()
            return

        if cmd == "GET" and len(parts) >= 2:
            key = parts[1]
            p = path_for_key(key)
            if not os.path.exists(p):
                writer.write(b"MISS\n"); await writer.drain(); return
            age = time.time() - os.path.getmtime(p)
            if age > TTL_SEC:
                try: os.remove(p)
                except Exception: pass
                writer.write(b"MISS\n"); await writer.drain(); return

            with open(p, "rb") as f:
                data = f.read()
            writer.write(f"OK {len(data)}\n".encode("utf-8"))
            writer.write(data)
            await writer.drain()
            return

        writer.write(b"ERR\n")
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", 19444)
    print("BlindBox listening on 127.0.0.1:19444")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
