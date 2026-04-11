from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from typing import Any

from i2pchat.core.i2p_chat_core import get_profiles_dir
from i2pchat.storage.blindbox_state import atomic_write_json


_ROUTER_SETTINGS_FILE = "router_prefs.json"
_DISABLE_BUNDLED_ENV = "I2PCHAT_DISABLE_BUNDLED_I2PD"
_DISABLE_BUNDLED_MARKERS = (
    "/usr/share/i2pchat/system-router-only",
    "/usr/local/share/i2pchat/system-router-only",
)


@dataclass
class RouterSettings:
    # Fresh install / no router_prefs.json: system SAM (portable & winget builds often ship without i2pd).
    backend: str = "system"  # "system" | "bundled"

    system_sam_host: str = "127.0.0.1"
    system_sam_port: int = 7656

    bundled_sam_host: str = "127.0.0.1"
    bundled_sam_port: int = 17656

    bundled_http_proxy_port: int = 14444
    bundled_socks_proxy_port: int = 14447
    bundled_control_http_port: int = 17070

    bundled_auto_start: bool = False


def bundled_i2pd_allowed() -> bool:
    raw = os.environ.get(_DISABLE_BUNDLED_ENV, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return False
    return not any(os.path.isfile(path) for path in _DISABLE_BUNDLED_MARKERS)


def normalize_router_settings(settings: RouterSettings) -> RouterSettings:
    if bundled_i2pd_allowed():
        return settings
    if settings.backend != "bundled" and not settings.bundled_auto_start:
        return settings
    return RouterSettings(
        backend="system",
        system_sam_host=settings.system_sam_host,
        system_sam_port=settings.system_sam_port,
        bundled_sam_host=settings.bundled_sam_host,
        bundled_sam_port=settings.bundled_sam_port,
        bundled_http_proxy_port=settings.bundled_http_proxy_port,
        bundled_socks_proxy_port=settings.bundled_socks_proxy_port,
        bundled_control_http_port=settings.bundled_control_http_port,
        bundled_auto_start=False,
    )


def router_settings_path() -> str:
    return os.path.join(get_profiles_dir(), _ROUTER_SETTINGS_FILE)


def router_runtime_dir() -> str:
    path = os.path.join(get_profiles_dir(), "router")
    os.makedirs(path, exist_ok=True)
    return path


def _coerce_string_setting(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _coerce_int_setting(
    value: Any,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 65535,
) -> int:
    try:
        if isinstance(value, str):
            value = value.strip()
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    if coerced < minimum or coerced > maximum:
        return default
    return coerced


def _coerce_bool_setting(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_router_settings(raw: dict[str, Any]) -> RouterSettings:
    defaults = asdict(RouterSettings())
    backend = _coerce_string_setting(raw.get("backend"), defaults["backend"]).lower()
    if backend not in {"system", "bundled"}:
        backend = defaults["backend"]
    return RouterSettings(
        backend=backend,
        system_sam_host=_coerce_string_setting(
            raw.get("system_sam_host"),
            defaults["system_sam_host"],
        ),
        system_sam_port=_coerce_int_setting(
            raw.get("system_sam_port"),
            defaults["system_sam_port"],
        ),
        bundled_sam_host=_coerce_string_setting(
            raw.get("bundled_sam_host"),
            defaults["bundled_sam_host"],
        ),
        bundled_sam_port=_coerce_int_setting(
            raw.get("bundled_sam_port"),
            defaults["bundled_sam_port"],
        ),
        bundled_http_proxy_port=_coerce_int_setting(
            raw.get("bundled_http_proxy_port"),
            defaults["bundled_http_proxy_port"],
        ),
        bundled_socks_proxy_port=_coerce_int_setting(
            raw.get("bundled_socks_proxy_port"),
            defaults["bundled_socks_proxy_port"],
        ),
        bundled_control_http_port=_coerce_int_setting(
            raw.get("bundled_control_http_port"),
            defaults["bundled_control_http_port"],
        ),
        bundled_auto_start=_coerce_bool_setting(
            raw.get("bundled_auto_start"),
            defaults["bundled_auto_start"],
        ),
    )


def load_router_settings() -> RouterSettings:
    path = router_settings_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return normalize_router_settings(_coerce_router_settings(raw))
    except Exception:
        pass
    return normalize_router_settings(RouterSettings())


def save_router_settings(settings: RouterSettings) -> None:
    atomic_write_json(router_settings_path(), asdict(normalize_router_settings(settings)))
