import os
import unittest


class HistoryUiGuardsTests(unittest.TestCase):
    def _main_qt_source(self) -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "main_qt.py")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_handle_message_respects_history_enabled(self) -> None:
        src = self._main_qt_source()
        self.assertIn(
            'if kind in ("me", "peer") and self._history_enabled:',
            src,
        )

    def test_toggle_off_resets_history_session_state(self) -> None:
        src = self._main_qt_source()
        # Guard against delayed save after OFF.
        self.assertIn("self._history_entries = []", src)
        self.assertIn("self._history_dirty = False", src)
        self.assertIn("self._history_loaded_for_peer = None", src)

    def test_disconnect_resets_loaded_peer(self) -> None:
        src = self._main_qt_source()
        self.assertIn("if kind == \"disconnect\":", src)
        self.assertIn("self._history_loaded_for_peer = None", src)
        self.assertIn("self._history_flush_timer.stop()", src)

    def test_save_history_failure_is_reported(self) -> None:
        src = self._main_qt_source()
        self.assertIn("Warning: failed to save chat history:", src)
        self.assertIn("self._history_save_error_reported", src)


if __name__ == "__main__":
    unittest.main()
