"""Optional stderr logging for I2PChat (enabled via ``I2PCHAT_LOG_LEVEL``)."""

from __future__ import annotations

import logging
import os
from typing import Final

_CONFIGURED: bool = False

_LOG_ROOT_NAME: Final = "i2pchat"


def configure_i2pchat_logging_from_env() -> None:
    """
    If ``I2PCHAT_LOG_LEVEL`` is set to a valid logging level name (e.g. DEBUG, INFO),
    attach a StreamHandler to the ``i2pchat`` logger so protocol and transport lines
    appear on stderr. Safe to call multiple times (no-op after first successful config).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    raw = (os.environ.get("I2PCHAT_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        return
    pkg = logging.getLogger(_LOG_ROOT_NAME)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    pkg.addHandler(handler)
    pkg.setLevel(level)
    pkg.propagate = False
    _CONFIGURED = True
