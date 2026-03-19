"""
Client for uploading/downloading payloads to drop.i2p.

Изначально в проекте нет HTTP-клиентов, поэтому используем только стандартную библиотеку
и прокидываем блокирующие запросы в отдельный поток через asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.error
import urllib.request
from typing import Any, Optional


DROP_BASE_URL = "http://drop.i2p"
DROP_UPLOAD_ENDPOINT = "/api/upload"
DEFAULT_HTTP_TIMEOUT_SECONDS = 60


def _build_multipart_payload(
    *,
    boundary: str,
    fields: dict[str, str],
    file_field_name: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> bytes:
    # RFC 7578 style multipart/form-data payload.
    # Собираем целиком в памяти: для оффлайн-сообщений это небольшой объём.
    crlf = "\r\n".encode("ascii")
    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(f"--{boundary}".encode("ascii") + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="{key}"'.encode("utf-8")
            + crlf
        )
        parts.append(crlf)
        parts.append(str(value).encode("utf-8") + crlf)

    parts.append(f"--{boundary}".encode("ascii") + crlf)
    parts.append(
        f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"'.encode(
            "utf-8"
        )
        + crlf
    )
    parts.append(f"Content-Type: {content_type}".encode("utf-8") + crlf)
    parts.append(crlf)
    parts.append(file_bytes + crlf)

    parts.append(f"--{boundary}--".encode("ascii") + crlf)
    return b"".join(parts)


def _json_loads_strict(data: bytes) -> dict[str, Any]:
    txt = data.decode("utf-8", errors="replace").strip()
    parsed = json.loads(txt)
    if not isinstance(parsed, dict):
        raise ValueError("drop.i2p response is not a JSON object")
    return parsed


async def upload_file_bytes(
    file_bytes: bytes,
    *,
    filename: str,
    expiry: str = "24h",
    password: Optional[str] = None,
    max_downloads: Optional[int] = 1,
    keep_metadata: Optional[str] = None,
    http_timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """
    Upload payload via POST /api/upload (multipart/form-data).

    Возвращает JSON с полями вида:
      { "id": "...", "url": "/f/...", "delete_token": "..." }
    """

    boundary = f"----I2PChatDrop{secrets.token_hex(8)}"
    fields: dict[str, str] = {"expiry": str(expiry)}
    if password:
        fields["password"] = str(password)
    if max_downloads is not None:
        fields["max_downloads"] = str(max_downloads)
    if keep_metadata is not None:
        fields["keep_metadata"] = str(keep_metadata)

    payload = _build_multipart_payload(
        boundary=boundary,
        fields=fields,
        file_field_name="file",
        filename=filename,
        file_bytes=file_bytes,
    )

    url = DROP_BASE_URL + DROP_UPLOAD_ENDPOINT
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    def _do_request() -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout_seconds) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            raise RuntimeError(
                f"drop.i2p upload failed HTTP {e.code}: {body[:200]!r}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"drop.i2p upload failed: {e}") from e

        return _json_loads_strict(body)

    return await asyncio.to_thread(_do_request)


async def download_bytes_from_path(
    url_path: str,
    *,
    http_timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> bytes:
    """
    Download bytes from drop.i2p, e.g. url_path="/f/k7x9m2".
    """

    if not url_path:
        raise ValueError("url_path is empty")
    if url_path.startswith("/"):
        url = DROP_BASE_URL + url_path
    else:
        url = DROP_BASE_URL + "/" + url_path

    def _do_request() -> bytes:
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=http_timeout_seconds) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            raise RuntimeError(
                f"drop.i2p download failed HTTP {e.code}: {body[:200]!r}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"drop.i2p download failed: {e}") from e

    return await asyncio.to_thread(_do_request)


def payload_to_json_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")

