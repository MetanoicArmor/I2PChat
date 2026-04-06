from __future__ import annotations

import tempfile
import unittest

from i2pchat.sam.destination import Destination, i2p_b64encode


def _sample_public_destination() -> bytes:
    return bytes((index % 256 for index in range(400)))


def _sample_private_destination(cert_len: int = 5) -> bytes:
    data = bytearray((index % 256 for index in range(420)))
    data[385:387] = cert_len.to_bytes(2, "big")
    return bytes(data)


class SamDestinationTests(unittest.TestCase):
    def test_public_destination_exposes_data_base64_and_base32(self) -> None:
        public = _sample_public_destination()
        dest = Destination(public)
        self.assertEqual(dest.data, public)
        self.assertEqual(dest.base64, i2p_b64encode(public))
        self.assertEqual(len(dest.base32), 52)
        self.assertIsNone(dest.private_key)

    def test_private_destination_splits_private_blob_and_public_part(self) -> None:
        private_blob = _sample_private_destination(cert_len=5)
        dest = Destination(private_blob, has_private_key=True)
        self.assertIsNotNone(dest.private_key)
        assert dest.private_key is not None
        self.assertEqual(dest.private_key.data, private_blob)
        self.assertEqual(dest.private_key.base64, i2p_b64encode(private_blob))
        self.assertEqual(dest.data, private_blob[:392])
        self.assertEqual(dest.base64, i2p_b64encode(private_blob[:392]))

    def test_destination_stringifies_to_base64(self) -> None:
        public = _sample_public_destination()
        dest = Destination(public)
        self.assertEqual(str(dest), dest.base64)
        self.assertIn(dest.base32, repr(dest))

    def test_destination_can_load_from_path(self) -> None:
        public = _sample_public_destination()
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(public)
            handle.flush()
            dest = Destination(path=handle.name)
        self.assertEqual(dest.data, public)

    def test_private_destination_rejects_short_blob(self) -> None:
        with self.assertRaises(ValueError):
            Destination(b"too-short", has_private_key=True)


if __name__ == "__main__":
    unittest.main()
