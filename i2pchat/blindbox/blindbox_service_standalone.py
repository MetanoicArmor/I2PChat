#!/usr/bin/env python3
"""
Standalone launcher for the hardened BlindBox example service.

Usage:
  python3 blindbox_service_standalone.py

The launcher first tries to load a sibling ``blindbox_server_example.py`` from the
same directory so it still works when exported as a small standalone bundle.
If that file is not present, it falls back to the installed package module.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from types import ModuleType


def _load_sibling_module() -> ModuleType | None:
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "blindbox_server_example.py")
    if not os.path.isfile(target):
        return None
    spec = importlib.util.spec_from_file_location("blindbox_server_example", target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_entrypoint():
    sibling = _load_sibling_module()
    if sibling is not None and hasattr(sibling, "main"):
        return sibling.main
    from i2pchat.blindbox.blindbox_server_example import main

    return main


if __name__ == "__main__":
    asyncio.run(_resolve_entrypoint()())
