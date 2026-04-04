from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from typing import Any

from i2pchat.core.i2p_chat_core import get_profiles_dir
from i2pchat.storage.blindbox_state import atomic_write_json


_ROUTER_SETTINGS_FILE = "router_prefs.json"


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
            return _coerce_router_settings(raw)
    except Exception:
        pass
    return RouterSettings()


def save_router_settings(settings: RouterSettings) -> None:
    atomic_write_json(router_settings_path(), asdict(settings))
