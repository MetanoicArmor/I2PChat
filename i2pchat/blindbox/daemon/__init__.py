"""
Production-oriented BlindBox daemon package.

Canonical entrypoint:
    python -m i2pchat.blindbox.daemon
"""

from .service import main

__all__ = ["main"]
