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
_QUEUES: dict[str, dict[str, object]] = {}


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


def _queue_record(queue_id: str) -> dict[str, object] | None:
    rec = _QUEUES.get(queue_id)
    if rec is None:
        return None
    items = rec.get("items")
    if not isinstance(items, dict):
        return None
    return rec


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        line = await reader.readline()
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if not parts:
            writer.write(b"ERR\n")
            await writer.drain()
            return

        cmd = parts[0]
        if cmd == "CAPA" and len(parts) <= 2:
            sent_tok = parts[1] if len(parts) >= 2 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            writer.write(b"OK BLINDBOX_QUEUE_CAPS_V1\n")
            await writer.drain()
            return
        if cmd == "QPUT" and len(parts) >= 7:
            queue_id = parts[1]
            key = parts[2]
            try:
                size = int(parts[3])
            except Exception:
                writer.write(b"ERR\n")
                await writer.drain()
                return
            put_cap = parts[4]
            get_cap = parts[5]
            delete_cap = parts[6]
            sent_tok = parts[7] if len(parts) >= 8 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            if size <= 0 or size > MAX_BLOB:
                writer.write(b"ERR\n")
                await writer.drain()
                return
            body = await reader.readexactly(size)
            rec = _queue_record(queue_id)
            if rec is None:
                rec = {
                    "put_cap": put_cap,
                    "get_cap": get_cap,
                    "delete_cap": delete_cap,
                    "items": {},
                }
                _QUEUES[queue_id] = rec
            elif (
                not hmac.compare_digest(str(rec["put_cap"]), put_cap)
                or not hmac.compare_digest(str(rec["get_cap"]), get_cap)
                or not hmac.compare_digest(str(rec["delete_cap"]), delete_cap)
            ):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            items = rec["items"]
            assert isinstance(items, dict)
            if key in items:
                writer.write(b"EXISTS\n")
            else:
                items[key] = body
                writer.write(b"OK\n")
            await writer.drain()
            return

        if cmd == "QGET" and len(parts) >= 4:
            queue_id = parts[1]
            key = parts[2]
            sent_cap = parts[3]
            sent_tok = parts[4] if len(parts) >= 5 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            rec = _queue_record(queue_id)
            if rec is None or not hmac.compare_digest(str(rec["get_cap"]), sent_cap):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            items = rec["items"]
            assert isinstance(items, dict)
            data = items.get(key)
            if data is None:
                writer.write(b"MISS\n")
                await writer.drain()
                return
            writer.write(f"OK {len(data)}\n".encode("utf-8"))
            writer.write(data)
            await writer.drain()
            return

        if cmd == "QDEL" and len(parts) >= 4:
            queue_id = parts[1]
            key = parts[2]
            sent_cap = parts[3]
            sent_tok = parts[4] if len(parts) >= 5 else ""
            if not _token_ok(sent_tok):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            rec = _queue_record(queue_id)
            if rec is None or not hmac.compare_digest(str(rec["delete_cap"]), sent_cap):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            items = rec["items"]
            assert isinstance(items, dict)
            if key in items:
                del items[key]
                writer.write(b"OK\n")
            else:
                writer.write(b"MISS\n")
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
