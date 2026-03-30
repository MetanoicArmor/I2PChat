"""Unit tests for i2pchat.presentation.privacy_mode (issue #23)."""

from __future__ import annotations

import unittest

from i2pchat.presentation.privacy_mode import (
    PrivacyState,
    activate_privacy_mode,
    deactivate_privacy_mode,
    privacy_state_from_dict,
    privacy_state_to_dict,
    set_lock_pin,
    verify_lock_pin,
)
from i2pchat.presentation.notification_prefs import (
    notification_body_for_display,
    should_show_tray_message,
)


class TestPrivacyStateDefaults(unittest.TestCase):
    def test_defaults(self):
        s = PrivacyState()
        self.assertFalse(s.active)
        self.assertTrue(s.hide_notifications)
        self.assertFalse(s.lock_enabled)
        self.assertIsNone(s.lock_hash)


class TestActivateDeactivateNoLock(unittest.TestCase):
    def test_activate(self):
        s = PrivacyState()
        s2 = activate_privacy_mode(s)
        self.assertTrue(s2.active)
        self.assertEqual(s2.hide_notifications, s.hide_notifications)

    def test_deactivate_no_lock(self):
        s = activate_privacy_mode(PrivacyState())
        new_s, ok = deactivate_privacy_mode(s)
        self.assertTrue(ok)
        self.assertFalse(new_s.active)

    def test_deactivate_when_already_inactive(self):
        s = PrivacyState(active=False)
        new_s, ok = deactivate_privacy_mode(s)
        self.assertTrue(ok)
        self.assertFalse(new_s.active)


class TestPinHashing(unittest.TestCase):
    def test_set_lock_pin_returns_hash_string(self):
        h = set_lock_pin("1234")
        self.assertTrue(h.startswith("pbkdf2$"))
        parts = h.split("$")
        self.assertEqual(len(parts), 3)

    def test_set_lock_pin_empty_raises(self):
        with self.assertRaises(ValueError):
            set_lock_pin("")

    def test_verify_lock_pin_correct(self):
        h = set_lock_pin("secret")
        self.assertTrue(verify_lock_pin("secret", h))

    def test_verify_lock_pin_wrong(self):
        h = set_lock_pin("secret")
        self.assertFalse(verify_lock_pin("wrong", h))

    def test_verify_lock_pin_bad_format(self):
        self.assertFalse(verify_lock_pin("x", "notavalidhash"))

    def test_two_hashes_differ_same_pin(self):
        h1 = set_lock_pin("abc")
        h2 = set_lock_pin("abc")
        self.assertNotEqual(h1, h2)  # different salts


class TestActivateDeactivateWithLock(unittest.TestCase):
    def test_deactivate_locked_without_pin_denied(self):
        pin_hash = set_lock_pin("1234")
        s = PrivacyState(active=True, lock_enabled=True, lock_hash=pin_hash)
        new_s, ok = deactivate_privacy_mode(s, pin_if_locked=None)
        self.assertFalse(ok)
        self.assertTrue(new_s.active)

    def test_deactivate_locked_wrong_pin_denied(self):
        pin_hash = set_lock_pin("1234")
        s = PrivacyState(active=True, lock_enabled=True, lock_hash=pin_hash)
        new_s, ok = deactivate_privacy_mode(s, pin_if_locked="9999")
        self.assertFalse(ok)
        self.assertTrue(new_s.active)

    def test_deactivate_locked_correct_pin_succeeds(self):
        pin_hash = set_lock_pin("1234")
        s = PrivacyState(active=True, lock_enabled=True, lock_hash=pin_hash)
        new_s, ok = deactivate_privacy_mode(s, pin_if_locked="1234")
        self.assertTrue(ok)
        self.assertFalse(new_s.active)


class TestSerialisation(unittest.TestCase):
    def test_round_trip(self):
        pin_hash = set_lock_pin("pin")
        s = PrivacyState(active=True, hide_notifications=True, lock_enabled=True, lock_hash=pin_hash)
        d = privacy_state_to_dict(s)
        s2 = privacy_state_from_dict(d)
        self.assertEqual(s2, s)

    def test_from_dict_defaults(self):
        s = privacy_state_from_dict({})
        self.assertFalse(s.active)
        self.assertIsNone(s.lock_hash)


class TestNotificationPrefsIntegration(unittest.TestCase):
    def test_notification_body_privacy(self):
        cases = [
            ("peer", "hello", False, False, "hello"),
            ("peer", "hello", True, False, "New message"),
            ("peer", "hello", False, True, ""),
            ("peer", "hello", True, True, ""),
            ("connect", "peer connected", False, True, ""),
        ]
        for kind, preview, hide_body, privacy, expected in cases:
            with self.subTest(kind=kind, hide_body=hide_body, privacy=privacy):
                self.assertEqual(
                    notification_body_for_display(
                        kind=kind, preview=preview, hide_body=hide_body, privacy_active=privacy
                    ),
                    expected,
                )

    def test_should_show_tray_message_privacy(self):
        cases = [
            (False, False, False, False, True),
            (True, True, True, False, False),
            (False, False, False, True, False),
            (True, True, True, True, False),
        ]
        for quiet, app, win, privacy, show in cases:
            with self.subTest(quiet=quiet, privacy=privacy):
                self.assertEqual(
                    should_show_tray_message(
                        quiet_mode=quiet,
                        is_app_active=app,
                        is_window_active=win,
                        privacy_active=privacy,
                    ),
                    show,
                )


if __name__ == "__main__":
    unittest.main()
