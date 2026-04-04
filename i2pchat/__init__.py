"""I2PChat package: ensure vendored ``i2plib`` is importable (``vendor/i2plib`` layout)."""

from __future__ import annotations

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
