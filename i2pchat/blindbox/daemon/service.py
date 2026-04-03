"""
Stable production daemon entrypoint for BlindBox service deployments.

This module intentionally reuses the hardened BlindBox service implementation from
``blindbox_server_example`` so deployment assets can point to a package-local,
stable module path without duplicating the runtime logic.
"""

from __future__ import annotations

from i2pchat.blindbox.blindbox_server_example import main

__all__ = ["main"]
