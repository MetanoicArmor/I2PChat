"""Короткая точка входа для Textual TUI: ``python -m i2pchat.tui`` [profile]."""

from __future__ import annotations


def main() -> None:
    from i2pchat.gui.chat_python import I2PChat

    I2PChat().run()


if __name__ == "__main__":
    main()
