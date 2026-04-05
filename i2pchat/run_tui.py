"""PyInstaller / dev launcher: Textual TUI (консольный вход, в т.ч. Windows).

Тот же пакетный импорт, что и у run_gui; Qt не подгружается до tui.main.
"""
import os
import sys

# Detached child: graceful bundled i2pd shutdown after TUI exit (macOS / Linux).
if os.environ.get("I2PCHAT_ROUTER_REAPER") == "1":
    from i2pchat.router.bundled_i2pd import unix_reaper_main

    unix_reaper_main()
    raise SystemExit(0)

from i2pchat.tui import main

if __name__ == "__main__":
    main()
