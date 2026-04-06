"""I2PChat package bootstrap helpers."""

from __future__ import annotations

import errno
import sys


def _patch_asyncio_set_nodelay_macos() -> None:
    """macOS + asyncio: TCP_NODELAY may raise OSError EINVAL (22) on some sockets (e.g. Python 3.14 + SAM)."""
    if sys.platform != "darwin":
        return
    try:
        import asyncio.base_events as _be
    except Exception:
        return
    if getattr(_be, "_i2pchat_nodelay_patched", False):
        return
    _orig = _be._set_nodelay

    def _safe_set_nodelay(sock):
        try:
            _orig(sock)
        except OSError as e:
            if e.errno == errno.EINVAL:
                return
            raise

    _be._set_nodelay = _safe_set_nodelay  # type: ignore[assignment]
    setattr(_be, "_i2pchat_nodelay_patched", True)


_patch_asyncio_set_nodelay_macos()
