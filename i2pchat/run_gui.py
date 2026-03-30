"""PyInstaller / dev launcher: package-first GUI entry.

Импортируйте код приложения только как ``i2pchat...`` (корневые шимы
``crypto``, ``main_qt``, ``i2p_chat_core`` и т.п. удалены).
"""
from i2pchat.gui.main_qt import main

if __name__ == "__main__":
    main()
