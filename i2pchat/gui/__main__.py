from i2pchat.logging_setup import configure_i2pchat_logging_from_env

configure_i2pchat_logging_from_env()

from i2pchat.gui.main_qt import main


if __name__ == "__main__":
    main()
