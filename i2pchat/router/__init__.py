"""Router backend helpers for system vs bundled i2pd runtimes."""

from .bundled_i2pd import BundledI2pdManager
from .settings import (
    RouterSettings,
    bundled_i2pd_allowed,
    load_router_settings,
    normalize_router_settings,
    save_router_settings,
)

__all__ = [
    "BundledI2pdManager",
    "RouterSettings",
    "bundled_i2pd_allowed",
    "load_router_settings",
    "normalize_router_settings",
    "save_router_settings",
]
