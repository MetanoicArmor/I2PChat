import unittest

from i2plib import sam


class SamInputValidationTests(unittest.TestCase):
    def test_naming_lookup_rejects_injection_chars(self) -> None:
        with self.assertRaises(ValueError):
            sam.naming_lookup("peer\nX=1")
        with self.assertRaises(ValueError):
            sam.naming_lookup("peer=bad")

    def test_stream_connect_rejects_whitespace_and_newline(self) -> None:
        with self.assertRaises(ValueError):
            sam.stream_connect("sess", "dest with space")
        with self.assertRaises(ValueError):
            sam.stream_connect("sess", "dest\nNEXT")

    def test_session_create_rejects_invalid_session_id_chars(self) -> None:
        for bad_session_id in ("bad id", "bad\nid", "bad=id"):
            with self.assertRaises(ValueError):
                sam.session_create("STREAM", bad_session_id, "TRANSIENT")

    def test_stream_accept_rejects_invalid_session_id_chars(self) -> None:
        for bad_session_id in ("bad id", "bad\nid", "bad=id"):
            with self.assertRaises(ValueError):
                sam.stream_accept(bad_session_id)

    def test_stream_forward_rejects_invalid_port(self) -> None:
        for bad_port in (0, 65536, "80"):
            with self.assertRaises(ValueError):
                sam.stream_forward("sess", bad_port)

    def test_stream_connect_rejects_invalid_silent_flag(self) -> None:
        with self.assertRaises(ValueError):
            sam.stream_connect("sess", "dest", silent="maybe")


if __name__ == "__main__":
    unittest.main()
