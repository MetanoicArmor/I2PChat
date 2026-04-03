"""
Геометрия встроенного popup и маска скругления: проверки без реальной Windows
(ветка win32 задаётся через monkeypatch перед созданием ProfileComboPopup).
"""

from __future__ import annotations

import os
import unittest
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pytest
except ImportError as exc:  # pragma: no cover - environment-dependent test bootstrap
    raise unittest.SkipTest("pytest is not installed") from exc

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from i2pchat.gui.popup_geometry import (
    apply_rounded_rect_mask,
    embedded_popup_top_left_in_window,
)


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_embedded_popup_center_under_anchor(qapp: QApplication) -> None:
    dlg = QDialog()
    dlg.resize(400, 300)
    anchor = QWidget(dlg)
    anchor.setGeometry(50, 40, 200, 32)
    popup_w, popup_h = 160, 48
    left = embedded_popup_top_left_in_window(
        anchor, dlg, popup_w, popup_h, margin=8, center_under_anchor=False
    )
    assert left.x() == 50
    centered = embedded_popup_top_left_in_window(
        anchor, dlg, popup_w, popup_h, margin=8, center_under_anchor=True
    )
    assert centered.x() == 50 + (200 - popup_w) // 2


def test_apply_rounded_rect_mask_no_crash(qapp: QApplication) -> None:
    w = QWidget()
    w.resize(120, 64)
    apply_rounded_rect_mask(w, 12.0)
    assert not w.mask().isEmpty()


def test_profile_combo_popup_win32_embedded_show_below(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from i2pchat.gui.styled_combo_widgets import ProfileComboPopup

    p = ProfileComboPopup(None, as_embedded_child=True, minimum_popup_width=100)
    assert p._win_menu_chrome is True
    p.apply_theme("night")
    p.set_items(["win"], "win")
    dlg = QDialog()
    dlg.resize(500, 400)
    row = QWidget(dlg)
    row.setGeometry(10, 10, 320, 36)
    dlg.show()
    qapp.processEvents()
    p.show_below(row)
    qapp.processEvents()
    assert p.isVisible()
    assert p.parent() is dlg
    assert p.width() == max(320, p.minimumWidth())


def test_profile_combo_popup_win32_frameless_popup(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from i2pchat.gui.styled_combo_widgets import ProfileComboPopup

    p = ProfileComboPopup(None, as_embedded_child=False, minimum_popup_width=100)
    assert p._win_menu_chrome is True
    p.apply_theme("ligth")
    p.set_items(["a"], "a")
    anchor = QWidget()
    anchor.resize(280, 32)
    anchor.show()
    p.show_below(anchor)
    assert p.isVisible()
    p._apply_win_popup_mask()
    assert not p.mask().isEmpty()
