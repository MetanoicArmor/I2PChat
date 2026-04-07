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
    backend: str = "bundled"  # "system" | "bundled"; default until router_prefs.json is saved from the UI

    system_sam_host: str = "127.0.0.1"
    system_sam_port: int = 7656

    bundled_sam_host: str = "127.0.0.1"
    bundled_sam_port: int = 17656

    bundled_http_proxy_port: int = 14444
    bundled_socks_proxy_port: int = 14447
    bundled_control_http_port: int = 17070

    bundled_auto_start: bool = True


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


def _coerce_router_settings(raw: dict[str, Any]) -> RouterSettings:
    defaults = asdict(RouterSettings())
    merged: dict[str, Any] = dict(defaults)
    for key in defaults:
        if key in raw:
            merged[key] = raw[key]
    try:
        return RouterSettings(**merged)
    except Exception:
        return RouterSettings()


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
