#!/usr/bin/env python3
"""
Queue-only BlindBox replica for deployment behind an i2pd server tunnel.

Defaults are safe for a same-host setup:
- bind: 127.0.0.1
- port: 19444
- storage: ~/.i2pchat-blindbox/store
- auth: optional (leave BLINDBOX_AUTH_TOKEN empty for a public replica)

Environment variables:
- BLINDBOX_BASE
- BLINDBOX_BIND_HOST
- BLINDBOX_PORT
- BLINDBOX_MAX_BLOB
- BLINDBOX_TTL_SEC
- BLINDBOX_AUTH_TOKEN
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
from typing import Optional


def _load_optional_dotenv() -> None:
    """Load KEY=value from .env if the variable is not already set."""
    base = os.path.expanduser(os.environ.get("BLINDBOX_BASE", "~/.i2pchat-blindbox"))
    here = os.path.dirname(os.path.abspath(__file__))
    paths = (
        os.path.join(here, ".env"),
        os.path.join(base, ".env"),
    )
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if not key or key in os.environ:
                        continue
                    val = val.strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    os.environ[key] = val
        except OSError:
            pass


_load_optional_dotenv()

BASE = os.path.expanduser(os.environ.get("BLINDBOX_BASE", "~/.i2pchat-blindbox"))
STORE = os.path.join(BASE, "store")
os.makedirs(STORE, exist_ok=True)

BIND_HOST = os.environ.get("BLINDBOX_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("BLINDBOX_PORT", "19444"))
MAX_BLOB = int(os.environ.get("BLINDBOX_MAX_BLOB", str(1024 * 1024)))
TTL_SEC = int(os.environ.get("BLINDBOX_TTL_SEC", str(14 * 24 * 3600)))
AUTH_TOKEN = (os.environ.get("BLINDBOX_AUTH_TOKEN") or "").strip()

QUEUE_CAPS_MAGIC = "OK BLINDBOX_QUEUE_CAPS_V1"


def _token_ok(provided: str) -> bool:
    if not AUTH_TOKEN:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, AUTH_TOKEN)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _queue_dir(queue_id: str) -> str:
    qhash = _hash_text(queue_id)
    return os.path.join(STORE, qhash[:2], qhash)


def _queue_meta_path(queue_id: str) -> str:
    return os.path.join(_queue_dir(queue_id), "meta.json")


def _queue_items_dir(queue_id: str) -> str:
    return os.path.join(_queue_dir(queue_id), "items")


def _queue_item_path(queue_id: str, key: str) -> str:
    khash = _hash_text(key)
    return os.path.join(_queue_items_dir(queue_id), khash[:2], f"{khash}.blob")


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".blindbox.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _atomic_write_json(path: str, obj: dict[str, object]) -> None:
    _atomic_write_bytes(
        path,
        json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
    )


def _read_json(path: str) -> Optional[dict[str, object]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_queue_meta(queue_id: str) -> Optional[dict[str, object]]:
    return _read_json(_queue_meta_path(queue_id))


def _ensure_queue_meta(
    queue_id: str,
    *,
    put_cap: str,
    get_cap: str,
    delete_cap: str,
) -> bool:
    meta_path = _queue_meta_path(queue_id)
    meta = _load_queue_meta(queue_id)
    if meta is None:
        os.makedirs(_queue_items_dir(queue_id), exist_ok=True)
        _atomic_write_json(
            meta_path,
            {
                "version": 1,
                "queue_id_hash": _hash_text(queue_id),
                "put_cap": put_cap,
                "get_cap": get_cap,
                "delete_cap": delete_cap,
                "created_at": int(time.time()),
            },
        )
        return True
    return (
        hmac.compare_digest(str(meta.get("put_cap", "")), put_cap)
        and hmac.compare_digest(str(meta.get("get_cap", "")), get_cap)
        and hmac.compare_digest(str(meta.get("delete_cap", "")), delete_cap)
    )


def _item_expired(path: str) -> bool:
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return True
    return age > TTL_SEC


def _delete_if_exists(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _prune_queue_item(queue_id: str, key: str) -> None:
    path = _queue_item_path(queue_id, key)
    if os.path.exists(path) and _item_expired(path):
        _delete_if_exists(path)


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
            writer.write(f"{QUEUE_CAPS_MAGIC}\n".encode("utf-8"))
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
            if not _ensure_queue_meta(
                queue_id,
                put_cap=put_cap,
                get_cap=get_cap,
                delete_cap=delete_cap,
            ):
                writer.write(b"ERR\n")
                await writer.drain()
                return
            item_path = _queue_item_path(queue_id, key)
            _prune_queue_item(queue_id, key)
            if os.path.exists(item_path):
                writer.write(b"EXISTS\n")
            else:
                _atomic_write_bytes(item_path, body)
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
            meta = _load_queue_meta(queue_id)
            if meta is None or not hmac.compare_digest(str(meta.get("get_cap", "")), sent_cap):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            item_path = _queue_item_path(queue_id, key)
            _prune_queue_item(queue_id, key)
            if not os.path.exists(item_path):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            try:
                with open(item_path, "rb") as f:
                    data = f.read()
            except OSError:
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
            meta = _load_queue_meta(queue_id)
            if meta is None or not hmac.compare_digest(
                str(meta.get("delete_cap", "")), sent_cap
            ):
                writer.write(b"MISS\n")
                await writer.drain()
                return
            item_path = _queue_item_path(queue_id, key)
            _prune_queue_item(queue_id, key)
            if os.path.exists(item_path):
                _delete_if_exists(item_path)
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


async def main() -> None:
    os.makedirs(STORE, exist_ok=True)
    server = await asyncio.start_server(handle, BIND_HOST, PORT)
    mode = "auth required" if AUTH_TOKEN else "public / no auth"
    print(
        f"BlindBox listening on {BIND_HOST}:{PORT} "
        f"({mode}, ttl={TTL_SEC}s, max_blob={MAX_BLOB})"
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
