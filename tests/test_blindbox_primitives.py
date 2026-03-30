import os
import tempfile
import unittest

from i2pchat import crypto
from i2pchat.blindbox.blindbox_blob import decrypt_blindbox_blob, encrypt_blindbox_blob
from i2pchat.blindbox.blindbox_key_schedule import derive_blindbox_message_keys
from i2pchat.storage.blindbox_state import BlindBoxState, load_blindbox_state, save_blindbox_state


class BlindBoxKeyScheduleTests(unittest.TestCase):
    def test_message_keys_are_deterministic(self) -> None:
        root_secret = b"r" * 32
        first = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 7
        )
        second = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 7
        )
        self.assertEqual(first.lookup_token, second.lookup_token)
        self.assertEqual(first.lookup_key, second.lookup_key)
        self.assertEqual(first.blob_key, second.blob_key)
        self.assertEqual(first.state_tag, second.state_tag)

    def test_direction_and_index_change_material(self) -> None:
        root_secret = b"r" * 32
        send_keys = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 1
        )
        recv_keys = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "recv", 1
        )
        next_keys = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 2
        )
        self.assertNotEqual(send_keys.lookup_token, recv_keys.lookup_token)
        self.assertNotEqual(send_keys.blob_key, recv_keys.blob_key)
        self.assertNotEqual(send_keys.lookup_token, next_keys.lookup_token)
        self.assertNotEqual(send_keys.state_tag, next_keys.state_tag)

    def test_epoch_changes_derived_material(self) -> None:
        root_secret = b"r" * 32
        epoch0 = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 1, epoch=0
        )
        epoch1 = derive_blindbox_message_keys(
            root_secret, "alice.b32.i2p", "bob.b32.i2p", "send", 1, epoch=1
        )
        self.assertNotEqual(epoch0.lookup_token, epoch1.lookup_token)
        self.assertNotEqual(epoch0.blob_key, epoch1.blob_key)
        self.assertNotEqual(epoch0.state_tag, epoch1.state_tag)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl is required")
class BlindBoxBlobTests(unittest.TestCase):
    def test_blob_roundtrip_and_validation(self) -> None:
        keys = derive_blindbox_message_keys(
            b"s" * 32, "alice.b32.i2p", "bob.b32.i2p", "send", 11
        )
        frame = b"\x89I2P\x04U\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x05hello"
        blob = encrypt_blindbox_blob(
            frame,
            keys.blob_key,
            "send",
            11,
            keys.state_tag,
            padding_bucket=128,
        )
        parsed = decrypt_blindbox_blob(
            blob,
            keys.blob_key,
            expected_direction="send",
            expected_index=11,
            expected_state_tag=keys.state_tag,
        )
        self.assertEqual(parsed, frame)

    def test_blob_rejects_wrong_state_tag(self) -> None:
        keys = derive_blindbox_message_keys(
            b"s" * 32, "alice.b32.i2p", "bob.b32.i2p", "send", 5
        )
        blob = encrypt_blindbox_blob(
            b"payload",
            keys.blob_key,
            "send",
            5,
            keys.state_tag,
        )
        with self.assertRaises(ValueError):
            decrypt_blindbox_blob(
                blob,
                keys.blob_key,
                expected_direction="send",
                expected_index=5,
                expected_state_tag=b"\x00" * 16,
            )


class BlindBoxStateTests(unittest.TestCase):
    def test_state_roundtrip_and_recv_base_advance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "blindbox.state.json")
            state = BlindBoxState(send_index=3, recv_base=0, recv_window=8)
            state.mark_consumed(0)
            state.mark_consumed(1)
            self.assertEqual(state.recv_base, 2)
            save_blindbox_state(path, state)
            loaded = load_blindbox_state(path)
            self.assertEqual(loaded.send_index, 3)
            self.assertEqual(loaded.recv_base, 2)
            self.assertEqual(loaded.recv_window, 8)
            self.assertEqual(loaded.consumed_recv, {0, 1})

    def test_missing_state_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "missing.state.json")
            loaded = load_blindbox_state(path)
            self.assertEqual(loaded.send_index, 0)
            self.assertEqual(loaded.recv_base, 0)
            self.assertGreaterEqual(loaded.recv_window, 1)


if __name__ == "__main__":
    unittest.main()
