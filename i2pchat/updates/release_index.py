"""
Парсинг страницы релизов I2PChat и сравнение версий с локальной (файл VERSION).

HTTP к *.i2p: если в окружении нет http_proxy/HTTP_PROXY (и системных прокси из getproxies),
по умолчанию используется локальный прокси I2P ``http://127.0.0.1:4444``.
Переопределение: ``I2PCHAT_UPDATE_HTTP_PROXY`` (пусто / 0 / none / off / direct — без прокси).
"""

from __future__ import annotations

import os
import platform
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_RELEASES_PAGE_URL = (
    "http://i2pchatsfjisxgbfpjqg52qfv4unspxgcizvvh7mfirn2uzj2udq.b32.i2p/"
)

# Строго под имена вида I2PChat-linux-x86_64-v1.0.1.zip
RELEASE_ZIP_RE = re.compile(
    r"^I2PChat-(?P<platform>linux|macOS|windows)-(?P<arch>[A-Za-z0-9_]+)-"
    r"v(?P<version>\d+\.\d+\.\d+)\.zip$"
)

# Грубый поиск кандидатов в HTML (href, текст листинга).
ZIP_CANDIDATE_RE = re.compile(r"I2PChat-[^\s\"'<>]+\.zip")

# Типичный HTTP-прокси Java I2P / i2pd (если не задан http_proxy).
_DEFAULT_I2P_HTTP_PROXY = "http://127.0.0.1:4444"


def _url_is_i2p_host(url: str) -> bool:
    return ".i2p" in url.lower()


def _env_http_proxy_explicit() -> bool:
    if urllib.request.getproxies().get("http") or urllib.request.getproxies().get("https"):
        return True
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        if (os.environ.get(key) or "").strip():
            return True
    return False


def _opener_for_update_fetch(
    url: str, *, proxy_url: Optional[str] = None
) -> Callable[..., object]:
    """
    Для *.i2p без явного прокси в окружении — HTTP через 127.0.0.1:4444.
    ``I2PCHAT_UPDATE_HTTP_PROXY`` задаёт URL прокси; пусто/0/none/off/direct — как urllib без доп. прокси.
    """
    raw = proxy_url if proxy_url is not None else (os.environ.get("I2PCHAT_UPDATE_HTTP_PROXY") or "").strip()
    low = raw.lower()
    if low in ("0", "none", "off", "direct", "false"):
        return urllib.request.urlopen
    if raw:
        handler = urllib.request.ProxyHandler({"http": raw, "https": raw})
        return urllib.request.build_opener(handler).open
    if not _url_is_i2p_host(url):
        return urllib.request.urlopen
    if _env_http_proxy_explicit():
        return urllib.request.urlopen
    handler = urllib.request.ProxyHandler(
        {"http": _DEFAULT_I2P_HTTP_PROXY, "https": _DEFAULT_I2P_HTTP_PROXY}
    )
    return urllib.request.build_opener(handler).open


def releases_page_url() -> str:
    raw = (os.environ.get("I2PCHAT_RELEASES_PAGE_URL") or "").strip()
    return raw if raw else DEFAULT_RELEASES_PAGE_URL


def downloads_page_url() -> str:
    """URL для открытия в браузере с якорем #downloads."""
    u = releases_page_url().strip()
    if "#" in u:
        return u
    return u.rstrip("/") + "/#downloads"


def expected_artifact_prefix() -> Optional[str]:
    """
    Префикс имени ZIP без версии, например I2PChat-linux-x86_64.
    None — неподдерживаемая комбинация ОС/архитектуры.
    """
    plat = sys.platform
    machine = platform.machine().lower()
    if plat == "linux":
        if machine in ("x86_64", "amd64"):
            return "I2PChat-linux-x86_64"
        if machine in ("aarch64", "arm64"):
            return "I2PChat-linux-arm64"
        return None
    if plat == "darwin":
        if machine == "arm64":
            return "I2PChat-macOS-arm64"
        if machine == "x86_64":
            return "I2PChat-macOS-x64"
        return None
    if plat == "win32":
        if machine in ("amd64", "x86_64", "arm64"):
            return "I2PChat-windows-x64"
        return None
    return None


def parse_version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"expected major.minor.patch, got {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def compare_version_strings(a: str, b: str) -> int:
    """-1 если a < b, 0 если равны, 1 если a > b."""
    ta, tb = parse_version_tuple(a), parse_version_tuple(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def iter_unique_zip_candidates(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in ZIP_CANDIDATE_RE.finditer(html):
        name = m.group(0)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def parse_valid_release_rows(html: str) -> list[tuple[str, str, tuple[int, int, int]]]:
    """Список (filename, version_str, version_tuple) для валидных имён."""
    rows: list[tuple[str, str, tuple[int, int, int]]] = []
    for name in iter_unique_zip_candidates(html):
        m = RELEASE_ZIP_RE.match(name)
        if not m:
            continue
        vs = m.group("version")
        try:
            vt = parse_version_tuple(vs)
        except ValueError:
            continue
        rows.append((name, vs, vt))
    return rows


def find_latest_for_prefix(
    html: str, artifact_prefix: str
) -> Optional[tuple[str, str]]:
    """
    Максимальная версия среди файлов, чьё имя начинается с ``artifact_prefix-v``.
    Возвращает (version_str, filename) или None.
    """
    best_vt: Optional[tuple[int, int, int]] = None
    best: Optional[tuple[str, str]] = None
    prefix_with_v = f"{artifact_prefix}-v"
    for filename, vs, vt in parse_valid_release_rows(html):
        if not filename.startswith(prefix_with_v):
            continue
        if best_vt is None or vt > best_vt:
            best_vt = vt
            best = (vs, filename)
    return best


def fetch_releases_page(
    url: str,
    *,
    timeout: float = 45.0,
    opener: Optional[Callable[..., object]] = None,
    proxy_url: Optional[str] = None,
) -> str:
    """
    GET страницы. opener — для тестов (например urllib.request.build_opener с моком).
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "I2PChat-update-check/1.0"},
        method="GET",
    )
    op = (
        opener
        if opener is not None
        else _opener_for_update_fetch(url, proxy_url=proxy_url)
    )
    with op(req, timeout=timeout) as resp:  # type: ignore[misc]
        raw = resp.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


@dataclass
class UpdateCheckResult:
    """Результат проверки обновлений (без Qt)."""

    ok: bool
    kind: str
    message: str
    remote_version: Optional[str] = None
    remote_filename: Optional[str] = None


def check_for_updates_sync(
    current_version: str,
    *,
    page_url: Optional[str] = None,
    timeout: float = 45.0,
    opener: Optional[Callable[..., object]] = None,
    proxy_url: Optional[str] = None,
) -> UpdateCheckResult:
    """
    Скачивает страницу, ищет ZIP для текущей платформы, сравнивает с current_version.
    """
    prefix = expected_artifact_prefix()
    if prefix is None:
        return UpdateCheckResult(
            ok=False,
            kind="unsupported",
            message=(
                "Automatic update check is not available for this OS/CPU combination. "
                "Open the downloads page and pick a build manually."
            ),
        )

    url = page_url if page_url is not None else releases_page_url()
    try:
        html = fetch_releases_page(
            url, timeout=timeout, opener=opener, proxy_url=proxy_url
        )
    except urllib.error.HTTPError as e:
        return UpdateCheckResult(
            ok=False,
            kind="network",
            message=(
                f"HTTP error while fetching release page ({e.code}). "
                "Ensure I2P is running; for .i2p the app uses http://127.0.0.1:4444 "
                "when no http_proxy is set. Check the proxy port in your router settings."
            ),
        )
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return UpdateCheckResult(
            ok=False,
            kind="network",
            message=(
                f"Could not reach the release page ({reason}). "
                "Ensure I2P is running. If the HTTP proxy is not on 127.0.0.1:4444, set "
                "http_proxy or I2PCHAT_UPDATE_HTTP_PROXY to your I2P HTTP proxy URL."
            ),
        )
    except TimeoutError:
        return UpdateCheckResult(
            ok=False,
            kind="network",
            message=(
                "Request timed out. Ensure I2P is running and the HTTP proxy is configured."
            ),
        )
    except OSError as e:
        return UpdateCheckResult(
            ok=False,
            kind="network",
            message=f"Network error: {e}",
        )

    latest = find_latest_for_prefix(html, prefix)
    if latest is None:
        return UpdateCheckResult(
            ok=True,
            kind="no_artifact",
            message=(
                f"No release file found for this platform (expected prefix {prefix}). "
                "You can still open the downloads page to look for other builds."
            ),
        )

    remote_ver, remote_name = latest
    try:
        cmp = compare_version_strings(current_version.strip(), remote_ver)
    except ValueError:
        return UpdateCheckResult(
            ok=False,
            kind="bad_local_version",
            message=f"Invalid local version string: {current_version!r}",
        )

    if cmp >= 0:
        return UpdateCheckResult(
            ok=True,
            kind="up_to_date",
            message=f"You are up to date (v{current_version.strip()}).",
            remote_version=remote_ver,
            remote_filename=remote_name,
        )

    return UpdateCheckResult(
        ok=True,
        kind="update_available",
        message=f"A newer release is available: v{remote_ver} (you have v{current_version.strip()}).",
        remote_version=remote_ver,
        remote_filename=remote_name,
    )
