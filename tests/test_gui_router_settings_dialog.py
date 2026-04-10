from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication

from i2pchat.gui.main_qt import _RouterSettingsDialog
from i2pchat.router.settings import RouterSettings


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_router_dialog_preserves_custom_control_http_port(qapp: QApplication) -> None:
    dialog = _RouterSettingsDialog(
        None,
        settings=RouterSettings(
            backend="bundled",
            bundled_control_http_port=20004,
        ),
        bundled_status="Bundled router is running.",
    )

    settings = dialog.settings()

    assert settings.bundled_control_http_port == 20004
