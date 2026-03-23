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


if __name__ == "__main__":
    unittest.main()
