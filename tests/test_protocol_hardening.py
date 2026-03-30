"""
Protocol framing hardening tests: truncated frames, oversized frames,
malformed headers, corrupted base64, connection drop mid-transfer,
duplicate/out-of-order transfer end markers.
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import sys
import tempfile
import types
import unittest

# Stub out PIL if not installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from protocol_codec import HEADER_STRUCT, HEADER_SIZE, MAGIC, PROTOCOL_VERSION, FLAG_ENCRYPTED, ProtocolCodec
from i2p_chat_core import I2PChatCore


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self._buf = bytearray(payload)

    async def readexactly(self, n: int) -> bytes:
        if len(self._buf) < n:
            partial = bytes(self._buf)
            self._buf.clear()
            raise asyncio.IncompleteReadError(partial=partial, expected=n)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    async def read(self, n: int = -1) -> bytes:
        if not self._buf:
            return b""
        if n < 0 or n >= len(self._buf):
            data = bytes(self._buf)
            self._buf.clear()
            return data
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _patch_crypto(core_module):
    """Patch crypto to identity functions. Returns originals for restore."""
    originals = (
        core_module.crypto.NACL_AVAILABLE,
        core_module.crypto.encrypt_message,
        core_module.crypto.decrypt_message,
        core_module.crypto.compute_mac,
        core_module.crypto.verify_mac,
    )
    core_module.crypto.NACL_AVAILABLE = True
    core_module.crypto.encrypt_message = lambda _k, p: p  # type: ignore[assignment]
    core_module.crypto.decrypt_message = lambda _k, c: c  # type: ignore[assignment]
    core_module.crypto.compute_mac = (
        lambda _k, _t, _b, seq=None, msg_id=None, flags=None: b"x" * 32
    )  # type: ignore[assignment]
    core_module.crypto.verify_mac = (
        lambda _k, _t, _b, _m, seq=None, msg_id=None, flags=None: True
    )  # type: ignore[assignment]
    return originals


def _restore_crypto(core_module, originals):
    (
        core_module.crypto.NACL_AVAILABLE,
        core_module.crypto.encrypt_message,
        core_module.crypto.decrypt_message,
        core_module.crypto.compute_mac,
        core_module.crypto.verify_mac,
    ) = originals


# ---------------------------------------------------------------------------
# Codec-level: truncated / oversized / malformed header tests
# ---------------------------------------------------------------------------

class TestTruncatedFrames(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_header_mid_magic_raises(self) -> None:
        """Stream ends inside the magic bytes — IncompleteReadError."""
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        with self.assertRaises(asyncio.IncompleteReadError):
            await codec.read_frame(_Reader(b"\x89I2"))  # 3 of 4 magic bytes

    async def test_truncated_after_magic_raises(self) -> None:
        """Stream ends after magic but before full header."""
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        with self.assertRaises(asyncio.IncompleteReadError):
            await codec.read_frame(_Reader(MAGIC + b"\x04"))  # only ver byte

    async def test_truncated_payload_raises(self) -> None:
        """Header claims N payload bytes but stream has fewer."""
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        frame = codec.encode("S", b"hello world", msg_id=1)
        # Chop last 3 bytes of payload
        with self.assertRaises(asyncio.IncompleteReadError):
            await codec.read_frame(_Reader(frame[:-3]))

    async def test_empty_stream_raises(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        with self.assertRaises(asyncio.IncompleteReadError):
            await codec.read_frame(_Reader(b""))


class TestOversizedFrames(unittest.IsolatedAsyncioTestCase):
    async def test_encode_oversized_raises(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=4)
        with self.assertRaises(ValueError):
            codec.encode("S", b"12345", msg_id=1)

    async def test_decode_oversized_frame_raises(self) -> None:
        """Remote sends a frame with len > max_frame_body."""
        sender = ProtocolCodec(allowed_types={"S"}, max_frame_body=65536)
        receiver = ProtocolCodec(allowed_types={"S"}, max_frame_body=8)
        frame = sender.encode("S", b"0123456789", msg_id=1)
        with self.assertRaises(ValueError):
            await receiver.read_frame(_Reader(frame))

    async def test_max_boundary_frame_accepted(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=10)
        frame = codec.encode("S", b"1234567890", msg_id=1)
        decoded = await codec.read_frame(_Reader(frame))
        self.assertEqual(decoded.payload, b"1234567890")


class TestMalformedHeaders(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_protocol_version_raises(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        # Build frame with version 99
        bad_header = HEADER_STRUCT.pack(MAGIC, 99, ord("S"), 0, 1, 0)
        with self.assertRaises(ValueError) as ctx:
            await codec.read_frame(_Reader(bad_header))
        self.assertIn("version", str(ctx.exception).lower())

    async def test_unknown_frame_type_raises(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        bad_header = HEADER_STRUCT.pack(MAGIC, PROTOCOL_VERSION, ord("Z"), 0, 1, 0)
        with self.assertRaises(ValueError) as ctx:
            await codec.read_frame(_Reader(bad_header))
        self.assertIn("type", str(ctx.exception).lower())

    async def test_resync_limit_exceeded_raises(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024, resync_limit=10)
        # 20 bytes of garbage — codec can't find MAGIC within limit
        with self.assertRaises(ValueError) as ctx:
            await codec.read_frame(_Reader(b"\xff" * 20))
        self.assertIn("resync", str(ctx.exception).lower())

    async def test_garbage_before_valid_frame_resyncs(self) -> None:
        """Codec should scan past garbage and find the frame."""
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024, resync_limit=256)
        valid_frame = codec.encode("S", b"hi", msg_id=5)
        dirty = b"\x00\xde\xad\xbe\xef" + valid_frame
        decoded = await codec.read_frame(_Reader(dirty))
        self.assertEqual(decoded.payload, b"hi")

    async def test_zero_length_payload_frame_accepted(self) -> None:
        codec = ProtocolCodec(allowed_types={"S"}, max_frame_body=1024)
        frame = codec.encode("S", b"", msg_id=99)
        decoded = await codec.read_frame(_Reader(frame))
        self.assertEqual(decoded.payload, b"")
        self.assertEqual(decoded.msg_id, 99)


# ---------------------------------------------------------------------------
# File transfer: corrupted base64, connection drop, duplicate end markers
# ---------------------------------------------------------------------------

class TestFileTransferHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import i2p_chat_core as core_module
        self._core_module = core_module
        self._originals = _patch_crypto(core_module)
        self._original_downloads = core_module.get_downloads_dir
        self._tmp = tempfile.mkdtemp()
        core_module.get_downloads_dir = lambda: self._tmp  # type: ignore[assignment]

    def tearDown(self):
        _restore_crypto(self._core_module, self._originals)
        self._core_module.get_downloads_dir = self._original_downloads  # type: ignore[assignment]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_core(self, **kwargs):
        core = I2PChatCore(**kwargs)
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]
        return core

    async def test_corrupted_base64_in_file_chunk_aborts(self) -> None:
        errors: list[str] = []
        core = self._make_core(on_error=errors.append, on_file_offer=lambda _n, _s: True)
        payload = (
            core.frame_message("F", "data.bin|10")
            + core.frame_message("D", "!!!not-base64!!!")
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("File chunk error" in e for e in errors), errors)
        self.assertIsNone(core.incoming_file)
        self.assertEqual(os.listdir(self._tmp), [])

    async def test_connection_drop_after_offer_does_not_deliver(self) -> None:
        """Drop after file offer, before any chunks — on_file_delivered must not fire."""
        delivered: list[str] = []
        writer = _Writer()
        core = self._make_core(
            on_file_offer=lambda _n, _s: True,
            on_file_delivered=delivered.append,
        )
        payload = core.frame_message("F", "test.bin|100")
        conn = (_Reader(payload), writer)
        core.conn = conn
        await core.receive_loop(conn)

        self.assertEqual(delivered, [])
        self.assertNotIn(b"FILE_ACK", bytes(writer.buf))

    async def test_connection_drop_mid_chunks_does_not_deliver(self) -> None:
        """Drop mid-chunk stream — on_file_delivered must not fire, FILE_ACK must not be sent."""
        delivered: list[str] = []
        writer = _Writer()
        core = self._make_core(
            on_file_offer=lambda _n, _s: True,
            on_file_delivered=delivered.append,
        )
        partial_chunk = base64.b64encode(b"hell").decode("ascii")
        payload = (
            core.frame_message("F", "partial.bin|20")
            + core.frame_message("D", partial_chunk)
            # No end frame — simulates mid-transfer drop
        )
        conn = (_Reader(payload), writer)
        core.conn = conn
        await core.receive_loop(conn)

        self.assertEqual(delivered, [])
        self.assertNotIn(b"FILE_ACK", bytes(writer.buf))

    async def test_oversized_file_chunk_is_rejected(self) -> None:
        errors: list[str] = []
        core = self._make_core(on_error=errors.append, on_file_offer=lambda _n, _s: True)
        # Offer 1 byte but send 2
        oversize = base64.b64encode(b"\x00\x01").decode("ascii")
        payload = (
            core.frame_message("F", "tiny.bin|1")
            + core.frame_message("D", oversize)
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("File chunk error" in e for e in errors), errors)

    async def test_file_end_marker_without_full_data_rejected(self) -> None:
        errors: list[str] = []
        writer = _Writer()
        core = self._make_core(on_error=errors.append, on_file_offer=lambda _n, _s: True)
        # Offer 10 bytes, send only 3, then end
        chunk = base64.b64encode(b"abc").decode("ascii")
        payload = (
            core.frame_message("F", "short.bin|10")
            + core.frame_message("D", chunk)
            + core.frame_message("E", "done")
        )
        conn = (_Reader(payload), writer)
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("File transfer incomplete" in e for e in errors), errors)
        self.assertIsNone(core.incoming_file)
        self.assertNotIn(b"FILE_ACK", bytes(writer.buf))

    async def test_duplicate_file_end_marker_is_ignored(self) -> None:
        """Second E frame after a completed transfer doesn't crash."""
        errors: list[str] = []
        core = self._make_core(on_error=errors.append, on_file_offer=lambda _n, _s: True)
        chunk = base64.b64encode(b"abc").decode("ascii")
        payload = (
            core.frame_message("F", "dup.bin|3")
            + core.frame_message("D", chunk)
            + core.frame_message("E", "done")
            + core.frame_message("E", "done")  # duplicate
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        # No crash; the duplicate E is a no-op (incoming_file already cleared)
        # The test passes if receive_loop returns without exception
        critical = [e for e in errors if "crash" in e.lower() or "exception" in e.lower()]
        self.assertEqual(critical, [])


# ---------------------------------------------------------------------------
# Image transfer: corrupted base64, connection drop, duplicate end markers
# ---------------------------------------------------------------------------

class TestImageTransferHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import i2p_chat_core as core_module
        self._core_module = core_module
        self._originals = _patch_crypto(core_module)

    def tearDown(self):
        _restore_crypto(self._core_module, self._originals)

    def _make_core(self, **kwargs):
        core = I2PChatCore(**kwargs)
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]
        return core

    async def test_corrupted_base64_in_image_chunk_aborts(self) -> None:
        errors: list[str] = []
        core = self._make_core(on_error=errors.append)
        payload = (
            core.frame_message("G", "photo.png|10")
            + core.frame_message("G", "!!!not-base64!!!")
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("Image data error" in e for e in errors), errors)
        self.assertIsNone(core.inline_image_info)
        self.assertEqual(core.inline_image_buffer, bytearray())

    async def test_connection_drop_mid_image_receive_cleans_up(self) -> None:
        """Drop mid-stream — image must not be delivered."""
        images: list = []
        core = self._make_core(on_error=lambda _: None, on_inline_image_received=lambda *a: images.append(a))
        chunk = base64.b64encode(b"partial").decode("ascii")
        payload = (
            core.frame_message("G", "img.png|20")
            + core.frame_message("G", chunk)
            # No __IMG_END__
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        # on_image must not have been called with incomplete data
        self.assertEqual(images, [])

    async def test_image_end_marker_without_full_data_rejected(self) -> None:
        errors: list[str] = []
        writer = _Writer()
        core = self._make_core(on_error=errors.append)
        chunk = base64.b64encode(b"ab").decode("ascii")
        payload = (
            core.frame_message("G", "img.png|10")
            + core.frame_message("G", chunk)
            + core.frame_message("G", "__IMG_END__")
        )
        conn = (_Reader(payload), writer)
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("Image transfer incomplete" in e for e in errors), errors)
        self.assertIsNone(core.inline_image_info)
        self.assertNotIn(b"IMG_ACK", bytes(writer.buf))

    async def test_duplicate_image_end_marker_is_ignored(self) -> None:
        """Second __IMG_END__ after completed transfer doesn't crash."""
        errors: list[str] = []
        core = self._make_core(on_error=errors.append)
        chunk = base64.b64encode(b"abc").decode("ascii")
        payload = (
            core.frame_message("G", "img.png|3")
            + core.frame_message("G", chunk)
            + core.frame_message("G", "__IMG_END__")
            + core.frame_message("G", "__IMG_END__")  # duplicate
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        critical = [e for e in errors if "crash" in e.lower() or "exception" in e.lower()]
        self.assertEqual(critical, [])

    async def test_oversized_image_chunk_is_rejected(self) -> None:
        errors: list[str] = []
        core = self._make_core(on_error=errors.append)
        oversize = base64.b64encode(b"\x00\x01").decode("ascii")
        payload = (
            core.frame_message("G", "img.png|1")
            + core.frame_message("G", oversize)
        )
        conn = (_Reader(payload), _Writer())
        core.conn = conn
        await core.receive_loop(conn)

        self.assertTrue(any("Image data error" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
