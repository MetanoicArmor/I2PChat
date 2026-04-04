"""PyInstaller / dev launcher: package-first GUI entry.

Импортируйте код приложения только как ``i2pchat...`` (корневые шимы
``crypto``, ``main_qt``, ``i2p_chat_core`` и т.п. удалены).
"""
import os
import sys

# Detached child: graceful bundled i2pd shutdown after GUI exit (macOS / Linux).
if os.environ.get("I2PCHAT_ROUTER_REAPER") == "1":
    from i2pchat.router.bundled_i2pd import unix_reaper_main

    unix_reaper_main()
    raise SystemExit(0)

from i2pchat.gui.main_qt import main

if __name__ == "__main__":
    main()
