"""I2PChat package: ensure vendored ``i2plib`` is importable (``vendor/i2plib`` layout)."""

from __future__ import annotations

import errno
import os
import sys

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_pkg_dir)
_vendor_i2plib = os.path.join(_root, "vendor", "i2plib", "__init__.py")
_flat_i2plib = os.path.join(_root, "i2plib", "__init__.py")

if os.path.isfile(_vendor_i2plib):
    _vendor_parent = os.path.join(_root, "vendor")
    if _vendor_parent not in sys.path:
        sys.path.insert(0, _vendor_parent)
elif os.path.isfile(_flat_i2plib) and _root not in sys.path:
    sys.path.insert(0, _root)


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
