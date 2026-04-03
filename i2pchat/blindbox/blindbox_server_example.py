#!/usr/bin/env python3
# Loopback replica for i2pd; optional BLINDBOX_AUTH_TOKEN in ~/.i2pchat-blindbox/.env (see I2PChat examples).
import asyncio
import collections
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

BASE = os.path.expanduser("~/.i2pchat-blindbox")
STORE = os.path.join(BASE, "store")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        return int(default)
    return max(int(minimum), value)


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

MAX_BLOB = _env_int("BLINDBOX_MAX_BLOB", 1_048_576)
TTL_SEC = _env_int("BLINDBOX_TTL_SEC", 14 * 24 * 3600)  # 14 days
MAX_FILES = _env_int("BLINDBOX_MAX_FILES", 4096)
MAX_TOTAL_BYTES = _env_int("BLINDBOX_MAX_TOTAL_BYTES", 512 * 1024 * 1024)
GC_INTERVAL_SEC = _env_int("BLINDBOX_GC_INTERVAL_SEC", 300)
RATE_LIMIT_PUTS_PER_MINUTE = _env_int(
    "BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE", 240, minimum=0
)
RATE_LIMIT_BYTES_PER_MINUTE = _env_int(
    "BLINDBOX_RATE_LIMIT_BYTES_PER_MINUTE", 64 * 1024 * 1024, minimum=0
)
MAX_PREFIX_FILES = _env_int("BLINDBOX_MAX_PREFIX_FILES", 256, minimum=0)
MAX_PREFIX_BYTES = _env_int("BLINDBOX_MAX_PREFIX_BYTES", 32 * 1024 * 1024, minimum=0)
AUDIT_LOG_MAX_BYTES = _env_int("BLINDBOX_AUDIT_LOG_MAX_BYTES", 1_048_576, minimum=0)
AUDIT_LOG_BACKUPS = _env_int("BLINDBOX_AUDIT_LOG_BACKUPS", 3, minimum=0)
_AUTH_TOKEN = (os.environ.get("BLINDBOX_AUTH_TOKEN") or "").strip()
ADMIN_TOKEN = (os.environ.get("BLINDBOX_ADMIN_TOKEN") or "").strip()
SERVER_MAGIC = "PONG BLINDBOX_SERVER_EXAMPLE_V1"
_RATE_LIMIT_WINDOW_SEC = 60.0
_PUT_RATE_LOCK: asyncio.Lock | None = None
_PUT_TIMESTAMPS: collections.deque[float] = collections.deque()
_PUT_SIZES: collections.deque[tuple[float, int]] = collections.deque()
LOG_JSON = (os.environ.get("BLINDBOX_LOG_JSON") or "").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
AUDIT_LOG_PATH = os.path.join(BASE, "audit.log")
METRICS_JSON_PATH = (os.environ.get("BLINDBOX_METRICS_JSON_PATH") or "").strip()
METRICS_PROM_PATH = (os.environ.get("BLINDBOX_METRICS_PROM_PATH") or "").strip()
HTTP_STATUS_ENABLED = (os.environ.get("BLINDBOX_HTTP_STATUS") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
HTTP_STATUS_HOST = (os.environ.get("BLINDBOX_HTTP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
HTTP_STATUS_PORT = _env_int("BLINDBOX_HTTP_PORT", 19445, minimum=1)
_METRICS_LOCK: asyncio.Lock | None = None
_METRICS: collections.Counter[str] = collections.Counter()


def _ensure_store_layout() -> None:
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    os.makedirs(STORE, mode=0o700, exist_ok=True)
    try:
        os.chmod(BASE, 0o700)
    except OSError:
        pass
    try:
        os.chmod(STORE, 0o700)
    except OSError:
        pass
    if AUDIT_LOG_PATH:
        audit_parent = os.path.dirname(AUDIT_LOG_PATH)
        if audit_parent:
            os.makedirs(audit_parent, mode=0o700, exist_ok=True)
            try:
                os.chmod(audit_parent, 0o700)
            except OSError:
                pass


def path_for_key(key: str) -> str:
    _ensure_store_layout()
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    d = os.path.join(STORE, h[:2])
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return os.path.join(d, h)


def _token_ok(provided: str) -> bool:
    if not _AUTH_TOKEN:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, _AUTH_TOKEN)


def _token_ok_optional(provided: str) -> bool:
    if not _AUTH_TOKEN:
        return True
    return _token_ok(provided)


def _admin_token_ok(provided: str) -> bool:
    token = str(provided or "").strip()
    if ADMIN_TOKEN:
        return bool(token) and hmac.compare_digest(token, ADMIN_TOKEN)
    return _token_ok_optional(token)


def _safe_text(value: object) -> str:
    text = str(value)
    return "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\"} else "_" for ch in text)


def _peer_fields(writer: asyncio.StreamWriter) -> dict[str, object]:
    peer = writer.get_extra_info("peername")
    if isinstance(peer, tuple) and len(peer) >= 2:
        return {"remote_host": str(peer[0]), "remote_port": int(peer[1])}
    if peer is None:
        return {"remote_host": "unknown", "remote_port": 0}
    return {"remote_host": str(peer), "remote_port": 0}


def _rotate_audit_log() -> None:
    if not AUDIT_LOG_PATH or AUDIT_LOG_MAX_BYTES <= 0:
        return
    try:
        size = os.path.getsize(AUDIT_LOG_PATH)
    except OSError:
        return
    if size < AUDIT_LOG_MAX_BYTES:
        return
    for idx in range(AUDIT_LOG_BACKUPS, 0, -1):
        src = AUDIT_LOG_PATH if idx == 1 else f"{AUDIT_LOG_PATH}.{idx - 1}"
        dst = f"{AUDIT_LOG_PATH}.{idx}"
        if not os.path.exists(src):
            continue
        try:
            if idx == AUDIT_LOG_BACKUPS and os.path.exists(dst):
                os.unlink(dst)
            os.replace(src, dst)
        except OSError:
            continue


def _append_audit_line(line: str) -> None:
    if not AUDIT_LOG_PATH:
        return
    _ensure_store_layout()
    _rotate_audit_log()
    fd, tmp_path = tempfile.mkstemp(prefix=".audit.", suffix=".tmp", dir=os.path.dirname(AUDIT_LOG_PATH))
    try:
        existing = b""
        try:
            with open(AUDIT_LOG_PATH, "rb") as src:
                existing = src.read()
        except OSError:
            existing = b""
        with os.fdopen(fd, "wb") as f:
            if existing:
                f.write(existing)
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, AUDIT_LOG_PATH)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(AUDIT_LOG_PATH, 0o600)
    except OSError:
        pass


def _render_event_line(payload: dict[str, object]) -> str:
    if LOG_JSON:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    parts = []
    for key, value in payload.items():
        parts.append(f"{key}={_safe_text(value)}")
    return " ".join(parts)


def _emit_event(event: str, **fields: object) -> None:
    payload: dict[str, object] = {"ts": int(time.time()), "event": event}
    payload.update(fields)
    line = _render_event_line(payload)
    print(line, file=sys.stderr, flush=True)
    _append_audit_line(line + "\n")


def _emit_fail2ban(reason: str, **fields: object) -> None:
    parts = ["FAIL2BAN", f"reason={_safe_text(reason)}"]
    for key, value in fields.items():
        parts.append(f"{key}={_safe_text(value)}")
    line = " ".join(parts)
    print(line, file=sys.stderr, flush=True)
    _append_audit_line(line + "\n")


def _store_entries() -> list[tuple[str, int, float]]:
    _ensure_store_layout()
    entries: list[tuple[str, int, float]] = []
    for root, _dirs, files in os.walk(STORE):
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            entries.append((path, int(st.st_size), float(st.st_mtime)))
    return entries


def _prefix_for_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:2]


def _remove_if_expired(path: str, now_ts: float | None = None) -> bool:
    now = time.time() if now_ts is None else float(now_ts)
    try:
        age = now - os.path.getmtime(path)
    except OSError:
        return False
    if age <= TTL_SEC:
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def _prune_store(*, incoming_bytes: int = 0, now_ts: float | None = None) -> bool:
    now = time.time() if now_ts is None else float(now_ts)
    entries = _store_entries()
    total_bytes = 0
    live_entries: list[tuple[str, int, float]] = []
    for path, size, mtime in entries:
        if now - mtime > TTL_SEC:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        live_entries.append((path, size, mtime))
        total_bytes += size
    if incoming_bytes > MAX_TOTAL_BYTES:
        return False
    live_entries.sort(key=lambda item: item[2])
    while live_entries and (
        len(live_entries) >= MAX_FILES
        or total_bytes + incoming_bytes > MAX_TOTAL_BYTES
    ):
        path, size, _mtime = live_entries.pop(0)
        try:
            os.remove(path)
        except OSError:
            continue
        total_bytes -= size
    return len(live_entries) < MAX_FILES and total_bytes + incoming_bytes <= MAX_TOTAL_BYTES


def _atomic_write_blob(path: str, body: bytes) -> None:
    parent = os.path.dirname(path)
    _ensure_store_layout()
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".blindbox.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


async def _gc_loop() -> None:
    while True:
        await asyncio.sleep(GC_INTERVAL_SEC)
        try:
            _prune_store()
            _write_metrics_exports()
        except Exception:
            pass


def _current_store_stats(*, now_ts: float | None = None) -> tuple[int, int]:
    now = time.time() if now_ts is None else float(now_ts)
    files = 0
    total_bytes = 0
    for _path, size, mtime in _store_entries():
        if now - mtime > TTL_SEC:
            continue
        files += 1
        total_bytes += size
    return files, total_bytes


def _current_prefix_stats(prefix: str, *, now_ts: float | None = None) -> tuple[int, int]:
    now = time.time() if now_ts is None else float(now_ts)
    files = 0
    total_bytes = 0
    needle = os.path.join(STORE, str(prefix))
    for path, size, mtime in _store_entries():
        if now - mtime > TTL_SEC:
            continue
        if os.path.dirname(path) != needle:
            continue
        files += 1
        total_bytes += size
    return files, total_bytes


def _purge_rate_limit_history(now_ts: float) -> None:
    cutoff = float(now_ts) - _RATE_LIMIT_WINDOW_SEC
    while _PUT_TIMESTAMPS and _PUT_TIMESTAMPS[0] <= cutoff:
        _PUT_TIMESTAMPS.popleft()
    while _PUT_SIZES and _PUT_SIZES[0][0] <= cutoff:
        _PUT_SIZES.popleft()


async def _admit_put(size: int, *, now_ts: float | None = None) -> bool:
    global _PUT_RATE_LOCK
    if _PUT_RATE_LOCK is None:
        _PUT_RATE_LOCK = asyncio.Lock()
    now = time.time() if now_ts is None else float(now_ts)
    async with _PUT_RATE_LOCK:
        _purge_rate_limit_history(now)
        if RATE_LIMIT_PUTS_PER_MINUTE > 0 and len(_PUT_TIMESTAMPS) >= RATE_LIMIT_PUTS_PER_MINUTE:
            return False
        if RATE_LIMIT_BYTES_PER_MINUTE > 0:
            used_bytes = sum(item[1] for item in _PUT_SIZES)
            if used_bytes + int(size) > RATE_LIMIT_BYTES_PER_MINUTE:
                return False
        _PUT_TIMESTAMPS.append(now)
        _PUT_SIZES.append((now, int(size)))
        return True


def _admit_prefix_put(key: str, size: int, *, now_ts: float | None = None) -> bool:
    prefix = _prefix_for_key(key)
    files, total_bytes = _current_prefix_stats(prefix, now_ts=now_ts)
    if MAX_PREFIX_FILES > 0 and files >= MAX_PREFIX_FILES:
        return False
    if MAX_PREFIX_BYTES > 0 and total_bytes + int(size) > MAX_PREFIX_BYTES:
        return False
    return True


def _status_payload() -> dict[str, int | str]:
    files, total_bytes = _current_store_stats()
    return {
        "files": files,
        "bytes": total_bytes,
        "auth": 1 if _AUTH_TOKEN else 0,
        "admin_auth": 1 if ADMIN_TOKEN else 0,
        "ttl": TTL_SEC,
        "max_files": MAX_FILES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "puts_per_min": RATE_LIMIT_PUTS_PER_MINUTE,
        "bytes_per_min": RATE_LIMIT_BYTES_PER_MINUTE,
        "max_prefix_files": MAX_PREFIX_FILES,
        "max_prefix_bytes": MAX_PREFIX_BYTES,
    }


def _prometheus_metrics_text() -> str:
    payload = _status_payload()
    lines = [
        "# TYPE blindbox_files gauge",
        f"blindbox_files {int(payload['files'])}",
        "# TYPE blindbox_bytes gauge",
        f"blindbox_bytes {int(payload['bytes'])}",
        "# TYPE blindbox_auth_enabled gauge",
        f"blindbox_auth_enabled {int(payload['auth'])}",
        "# TYPE blindbox_ttl_seconds gauge",
        f"blindbox_ttl_seconds {int(payload['ttl'])}",
        "# TYPE blindbox_limit_files gauge",
        f"blindbox_limit_files {int(payload['max_files'])}",
        "# TYPE blindbox_limit_total_bytes gauge",
        f"blindbox_limit_total_bytes {int(payload['max_total_bytes'])}",
        "# TYPE blindbox_limit_prefix_files gauge",
        f"blindbox_limit_prefix_files {int(payload['max_prefix_files'])}",
        "# TYPE blindbox_limit_prefix_bytes gauge",
        f"blindbox_limit_prefix_bytes {int(payload['max_prefix_bytes'])}",
    ]
    for key in sorted(_METRICS):
        value = int(_METRICS[key])
        lines.append(f'blindbox_events_total{{event="{key}"}} {value}')
    return "\n".join(lines) + "\n"


async def _metrics_increment(name: str, amount: int = 1) -> None:
    global _METRICS_LOCK
    if _METRICS_LOCK is None:
        _METRICS_LOCK = asyncio.Lock()
    async with _METRICS_LOCK:
        _METRICS[name] += int(amount)
    _write_metrics_exports()


def _write_text_atomic(path: str, text: str) -> None:
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".metrics.", suffix=".tmp", dir=parent or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_metrics_exports() -> None:
    if METRICS_JSON_PATH:
        _write_text_atomic(
            METRICS_JSON_PATH,
            json.dumps(
                {"status": _status_payload(), "events": dict(_METRICS)},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
    if METRICS_PROM_PATH:
        _write_text_atomic(METRICS_PROM_PATH, _prometheus_metrics_text())


def _status_line() -> str:
    payload = _status_payload()
    return (
        "OK "
        f"files={payload['files']} "
        f"bytes={payload['bytes']} "
        f"auth={payload['auth']} "
        f"admin_auth={payload['admin_auth']} "
        f"ttl={payload['ttl']} "
        f"max_files={payload['max_files']} "
        f"max_total_bytes={payload['max_total_bytes']} "
        f"puts_per_min={payload['puts_per_min']} "
        f"bytes_per_min={payload['bytes_per_min']} "
        f"max_prefix_files={payload['max_prefix_files']} "
        f"max_prefix_bytes={payload['max_prefix_bytes']}"
    )


def _status_json_line() -> str:
    return json.dumps(_status_payload(), sort_keys=True, separators=(",", ":"))


def _http_reason(status_code: int) -> str:
    return {
        200: "OK",
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
    }.get(int(status_code), "Error")


def _http_response(status_code: int, content_type: str, body: bytes) -> bytes:
    status_text = _http_reason(status_code)
    headers = [
        f"HTTP/1.1 {int(status_code)} {status_text}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("utf-8") + body


def _extract_http_bearer_token(headers: dict[str, str]) -> str:
    auth = str(headers.get("authorization", "")).strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _http_route_response(path: str) -> tuple[int, str, bytes]:
    if path == "/healthz":
        return 200, "text/plain; charset=utf-8", b"ok\n"
    if path == "/status.json":
        return 200, "application/json; charset=utf-8", (_status_json_line() + "\n").encode("utf-8")
    if path == "/metrics":
        return 200, "text/plain; version=0.0.4; charset=utf-8", _prometheus_metrics_text().encode("utf-8")
    return 404, "text/plain; charset=utf-8", b"not found\n"


async def _http_handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = _peer_fields(writer)
    try:
        line = await reader.readline()
        request_line = line.decode("utf-8", errors="ignore").strip()
        if not request_line:
            return
        parts = request_line.split()
        if len(parts) != 3:
            writer.write(_http_response(405, "text/plain; charset=utf-8", b"bad request\n"))
            await writer.drain()
            return
        method, path, _version = parts
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if not text:
                break
            if ":" not in text:
                continue
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        if method != "GET":
            _emit_event("http_method_reject", method=method, path=path, **peer)
            await _metrics_increment("http_method_reject")
            writer.write(_http_response(405, "text/plain; charset=utf-8", b"method not allowed\n"))
            await writer.drain()
            return
        bearer = _extract_http_bearer_token(headers)
        if not _admin_token_ok(bearer):
            _emit_event("http_auth_fail", path=path, **peer)
            _emit_fail2ban("BLINDBOX_HTTP_AUTH_FAIL", path=path, **peer)
            await _metrics_increment("http_auth_fail")
            writer.write(_http_response(401, "text/plain; charset=utf-8", b"unauthorized\n"))
            await writer.drain()
            return
        status_code, content_type, body = _http_route_response(path)
        _emit_event("http_request", path=path, status=status_code, **peer)
        await _metrics_increment("http_request")
        writer.write(_http_response(status_code, content_type, body))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = _peer_fields(writer)
    try:
        line = await reader.readline()
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if not parts:
            _emit_event("request_empty", **peer)
            await _metrics_increment("request_empty")
            writer.write(b"ERR\n")
            await writer.drain()
            return

        cmd = parts[0]
        if cmd == "PING" and len(parts) == 1:
            _emit_event("ping", **peer)
            await _metrics_increment("ping")
            writer.write(f"{SERVER_MAGIC}\n".encode("utf-8"))
            await writer.drain()
            return
        if cmd == "STATUS" and len(parts) in {1, 2}:
            sent_tok = parts[1] if len(parts) == 2 else ""
            if not _admin_token_ok(sent_tok):
                _emit_event("auth_fail", command="STATUS", **peer)
                _emit_fail2ban("BLINDBOX_AUTH_FAIL", command="STATUS", **peer)
                await _metrics_increment("auth_fail")
                writer.write(b"ERR\n")
            else:
                _emit_event("status", format="text", **peer)
                await _metrics_increment("status")
                writer.write(f"{_status_line()}\n".encode("utf-8"))
            await writer.drain()
            return
        if cmd == "STATUS_JSON" and len(parts) in {1, 2}:
            sent_tok = parts[1] if len(parts) == 2 else ""
            if not _admin_token_ok(sent_tok):
                _emit_event("auth_fail", command="STATUS_JSON", **peer)
                _emit_fail2ban("BLINDBOX_AUTH_FAIL", command="STATUS_JSON", **peer)
                await _metrics_increment("auth_fail")
                writer.write(b"ERR\n")
            else:
                _emit_event("status", format="json", **peer)
                await _metrics_increment("status_json")
                writer.write(f"{_status_json_line()}\n".encode("utf-8"))
            await writer.drain()
            return
        if cmd == "METRICS" and len(parts) in {1, 2}:
            sent_tok = parts[1] if len(parts) == 2 else ""
            if not _admin_token_ok(sent_tok):
                _emit_event("auth_fail", command="METRICS", **peer)
                _emit_fail2ban("BLINDBOX_AUTH_FAIL", command="METRICS", **peer)
                await _metrics_increment("auth_fail")
                writer.write(b"ERR\n")
            else:
                _emit_event("metrics", format="prometheus", **peer)
                await _metrics_increment("metrics")
                writer.write(_prometheus_metrics_text().encode("utf-8"))
            await writer.drain()
            return
        if cmd == "PUT" and len(parts) >= 3:
            key = parts[1]
            try:
                size = int(parts[2])
            except Exception:
                _emit_event("put_invalid_size", key=key, **peer)
                await _metrics_increment("put_invalid_size")
                writer.write(b"ERR\n")
                await writer.drain()
                return
            sent_tok = parts[3] if len(parts) >= 4 else ""
            if not _token_ok(sent_tok):
                _emit_event("auth_fail", command="PUT", key=key, size=size, **peer)
                _emit_fail2ban(
                    "BLINDBOX_AUTH_FAIL", command="PUT", key=key, size=size, **peer
                )
                await _metrics_increment("auth_fail")
                writer.write(b"ERR\n")
                await writer.drain()
                return
            if size <= 0 or size > MAX_BLOB:
                _emit_event("put_rejected_size", key=key, size=size, **peer)
                await _metrics_increment("put_rejected_size")
                writer.write(b"ERR\n")
                await writer.drain()
                return
            if not await _admit_put(size):
                _emit_event("rate_limit", command="PUT", key=key, size=size, **peer)
                _emit_fail2ban(
                    "BLINDBOX_RATE_LIMIT", command="PUT", key=key, size=size, **peer
                )
                await _metrics_increment("rate_limit")
                writer.write(b"RATE\n")
                await writer.drain()
                return
            if not _admit_prefix_put(key, size):
                _emit_event("prefix_quota", command="PUT", key=key, size=size, **peer)
                await _metrics_increment("prefix_quota")
                writer.write(b"FULL\n")
                await writer.drain()
                return

            body = await reader.readexactly(size)
            p = path_for_key(key)
            if os.path.exists(p) and not _remove_if_expired(p):
                _emit_event("put_exists", key=key, size=size, **peer)
                await _metrics_increment("put_exists")
                writer.write(b"EXISTS\n")
            else:
                if not _prune_store(incoming_bytes=size):
                    _emit_event("store_quota", command="PUT", key=key, size=size, **peer)
                    await _metrics_increment("store_quota")
                    writer.write(b"FULL\n")
                else:
                    _atomic_write_blob(p, body)
                    _emit_event("put_ok", key=key, size=size, **peer)
                    await _metrics_increment("put_ok")
                    writer.write(b"OK\n")
            await writer.drain()
            return

        if cmd == "GET" and len(parts) >= 2:
            key = parts[1]
            sent_tok = parts[2] if len(parts) >= 3 else ""
            if not _token_ok(sent_tok):
                _emit_event("auth_fail", command="GET", key=key, **peer)
                _emit_fail2ban("BLINDBOX_AUTH_FAIL", command="GET", key=key, **peer)
                await _metrics_increment("auth_fail")
                writer.write(b"ERR\n")
                await writer.drain()
                return
            p = path_for_key(key)
            if not os.path.exists(p):
                _emit_event("get_miss", key=key, **peer)
                await _metrics_increment("get_miss")
                writer.write(b"MISS\n")
                await writer.drain()
                return
            if _remove_if_expired(p):
                _emit_event("get_expired", key=key, **peer)
                await _metrics_increment("get_expired")
                writer.write(b"MISS\n")
                await writer.drain()
                return

            with open(p, "rb") as f:
                data = f.read()
            _emit_event("get_ok", key=key, size=len(data), **peer)
            await _metrics_increment("get_ok")
            writer.write(f"OK {len(data)}\n".encode("utf-8"))
            writer.write(data)
            await writer.drain()
            return

        _emit_event("request_invalid", command=cmd, **peer)
        await _metrics_increment("request_invalid")
        writer.write(b"ERR\n")
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    _ensure_store_layout()
    _prune_store()
    server = await asyncio.start_server(handle, "127.0.0.1", 19444)
    http_server = None
    if HTTP_STATUS_ENABLED:
        http_server = await asyncio.start_server(_http_handle, HTTP_STATUS_HOST, HTTP_STATUS_PORT)
    gc_task = asyncio.create_task(_gc_loop())
    if _AUTH_TOKEN:
        print(
            "BlindBox listening on 127.0.0.1:19444 "
            "(auth optional but configured; "
            f"limits: {MAX_FILES} files / {MAX_TOTAL_BYTES} bytes; "
            f"prefix: {MAX_PREFIX_FILES} files / {MAX_PREFIX_BYTES} bytes; "
            f"rate: {RATE_LIMIT_PUTS_PER_MINUTE} puts/min / {RATE_LIMIT_BYTES_PER_MINUTE} bytes/min)"
        )
    else:
        print(
            "BlindBox listening on 127.0.0.1:19444 "
            "(public/no-auth mode; "
            f"limits: {MAX_FILES} files / {MAX_TOTAL_BYTES} bytes; "
            f"prefix: {MAX_PREFIX_FILES} files / {MAX_PREFIX_BYTES} bytes; "
            f"rate: {RATE_LIMIT_PUTS_PER_MINUTE} puts/min / {RATE_LIMIT_BYTES_PER_MINUTE} bytes/min)"
        )
    if HTTP_STATUS_ENABLED:
        print(
            f"BlindBox HTTP status listening on {HTTP_STATUS_HOST}:{HTTP_STATUS_PORT} "
            f"(admin_auth={'on' if ADMIN_TOKEN else 'off'})"
        )
    try:
        async with server:
            if http_server is None:
                await server.serve_forever()
            else:
                async with http_server:
                    await asyncio.gather(server.serve_forever(), http_server.serve_forever())
    finally:
        gc_task.cancel()
        try:
            await gc_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
