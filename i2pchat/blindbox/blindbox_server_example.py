#!/usr/bin/env python3
# Loopback replica for i2pd; optional BLINDBOX_AUTH_TOKEN in ~/.i2pchat-blindbox/.env (see I2PChat examples).
import asyncio
import hashlib
import hmac
import os
import time

BASE = os.path.expanduser("~/.i2pchat-blindbox")
STORE = os.path.join(BASE, "store")
os.makedirs(STORE, exist_ok=True)


def _load_optional_dotenv() -> None:
    """Load KEY=value from .env if the variable is not already set in the environment."""
    here = os.path.dirname(os.path.abspath(__file__))
    paths = (
        os.path.join(here, ".env"),
        os.path.join(BASE, ".env"),
    )
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if not key:
                        continue
                    val = val.strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    if key not in os.environ:
                        os.environ[key] = val
        except OSError:
            pass


_load_optional_dotenv()

MAX_BLOB = 1_048_576
TTL_SEC = 14 * 24 * 3600  # 14 days
_AUTH_TOKEN = (os.environ.get("BLINDBOX_AUTH_TOKEN") or "").strip()


def path_for_key(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    d = os.path.join(STORE, h[:2])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h)


def _token_ok(provided: str) -> bool:
    if not _AUTH_TOKEN:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, _AUTH_TOKEN)


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
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
            sent_tok = parts[3] if len(parts) >= 4 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            if size <= 0 or size > MAX_BLOB:
                writer.write(b"ERR\n")
                await writer.drain()
                return

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
            sent_tok = parts[2] if len(parts) >= 3 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            p = path_for_key(key)
            if not os.path.exists(p):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            age = time.time() - os.path.getmtime(p)
            if age > TTL_SEC:
                try:
                    os.remove(p)
                except Exception:
                    pass
                writer.write(b"MISS\n")
                await writer.drain()
                return

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
    if _AUTH_TOKEN:
        print("BlindBox listening on 127.0.0.1:19444 (auth required)")
    else:
        print("BlindBox listening on 127.0.0.1:19444 (no auth)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
