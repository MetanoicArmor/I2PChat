"""Tests for optional I2PCHAT_LOG_LEVEL configuration."""

from __future__ import annotations

import importlib
import logging

import pytest


def test_configure_logging_from_env_no_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("I2PCHAT_LOG_LEVEL", raising=False)
    import i2pchat.logging_setup as ls

    importlib.reload(ls)
    ls.configure_i2pchat_logging_from_env()
    pkg = logging.getLogger("i2pchat")
    assert pkg.handlers == [] or ls._CONFIGURED is False


def test_configure_logging_from_env_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("I2PCHAT_LOG_LEVEL", "DEBUG")
    import i2pchat.logging_setup as ls

    importlib.reload(ls)
    ls._CONFIGURED = False
    ls.configure_i2pchat_logging_from_env()
    pkg = logging.getLogger("i2pchat")
    assert pkg.handlers
    assert pkg.level <= logging.DEBUG
