import asyncio
import sys
import time
import types
import unittest

from protocol_codec import MAGIC, FLAG_ENCRYPTED, ProtocolCodec

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

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
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class ProtocolFramingVnextTests(unittest.IsolatedAsyncioTestCase):
    async def test_vnext_encode_decode_roundtrip(self) -> None:
        codec = ProtocolCodec(
            allowed_types={"S", "U"},
            max_frame_body=1024,
        )
        frame = codec.encode("U", b"hello", msg_id=42, flags=FLAG_ENCRYPTED)
        decoded = await codec.read_frame(_Reader(frame))

        self.assertFalse(decoded.is_legacy)
        self.assertEqual(decoded.msg_type, "U")
        self.assertEqual(decoded.payload, b"hello")
        self.assertEqual(decoded.msg_id, 42)
        self.assertEqual(decoded.flags, FLAG_ENCRYPTED)

    async def test_vnext_resync_after_garbage_prefix(self) -> None:
        codec = ProtocolCodec(
            allowed_types={"S"},
            max_frame_body=1024,
            resync_limit=4096,
        )
        frame = codec.encode("S", b"ok", msg_id=7, flags=0)
        reader = _Reader(b"\x00\x01garbage" + MAGIC[:2] + b"\xff" + frame)
        decoded = await codec.read_frame(reader)

        self.assertEqual(decoded.msg_type, "S")
        self.assertEqual(decoded.payload, b"ok")
        self.assertEqual(decoded.msg_id, 7)

    async def test_reject_oversized_frame(self) -> None:
        codec = ProtocolCodec(
            allowed_types={"S"},
            max_frame_body=8,
        )
        with self.assertRaises(ValueError):
            codec.encode("S", b"0123456789", msg_id=1, flags=0)

    async def test_legacy_mode_is_explicit_policy(self) -> None:
        codec = ProtocolCodec(
            allowed_types={"S"},
            max_frame_body=1024,
            allow_legacy=True,
        )
        vnext_frame = ProtocolCodec(
            allowed_types={"S"},
            max_frame_body=1024,
            allow_legacy=False,
        ).encode("S", b"hello", msg_id=1, flags=0)
        with self.assertRaises(ValueError):
            await codec.read_frame(_Reader(vnext_frame))

    async def test_legacy_desync_rejected_in_legacy_mode(self) -> None:
        codec = ProtocolCodec(
            allowed_types={"S"},
            max_frame_body=1024,
            allow_legacy=True,
        )
        # Legacy frame with wrong delimiter should be rejected.
        bad_legacy = b"S0002ok!"
        with self.assertRaises(ValueError):
            await codec.read_frame(_Reader(bad_legacy))

    async def test_msg_ack_clears_pending_text_ack_encrypted(self) -> None:
        import i2p_chat_core as core_module

        original_crypto = (
            core_module.crypto.NACL_AVAILABLE,
            core_module.crypto.encrypt_message,
            core_module.crypto.decrypt_message,
            core_module.crypto.compute_mac,
            core_module.crypto.verify_mac,
        )

        core_module.crypto.NACL_AVAILABLE = True
        core_module.crypto.encrypt_message = lambda _k, p: p  # type: ignore[assignment]
        core_module.crypto.decrypt_message = lambda _k, c: c  # type: ignore[assignment]
        core_module.crypto.compute_mac = lambda _k, _t, _b, seq=None: b"x" * 32  # type: ignore[assignment]
        core_module.crypto.verify_mac = lambda _k, _t, _b, _m, seq=None: True  # type: ignore[assignment]

        core = I2PChatCore()
        writer = _Writer()
        send_conn = (_Reader(b""), writer)
        core.conn = send_conn
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]

        try:
            await core.send_text("hello peer")
            self.assertEqual(len(core._pending_text_acks), 1)
            ack_id = next(iter(core._pending_text_acks.keys()))

            peer_core = I2PChatCore()
            peer_core.handshake_complete = True
            peer_core.use_encryption = True
            peer_core.shared_key = b"x" * 32
            ack_frame = peer_core.frame_message("S", f"__SIGNAL__:MSG_ACK|{ack_id}")
            recv_writer = _Writer()
            recv_conn = (_Reader(ack_frame), recv_writer)
            core.conn = recv_conn
            await core.receive_loop(recv_conn)

            self.assertNotIn(ack_id, core._pending_text_acks)
        finally:
            (
                core_module.crypto.NACL_AVAILABLE,
                core_module.crypto.encrypt_message,
                core_module.crypto.decrypt_message,
                core_module.crypto.compute_mac,
                core_module.crypto.verify_mac,
            ) = original_crypto

    async def test_ack_spoofing_plaintext_signal_is_rejected(self) -> None:
        delivered: list[str] = []
        errors: list[str] = []
        core = I2PChatCore(on_error=errors.append, on_file_delivered=delivered.append)
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]

        core._register_pending_ack(
            core._pending_file_acks,
            123,
            token="safe.txt",
            ack_kind="file",
        )
        spoof_payload = "__SIGNAL__:FILE_ACK|safe.txt|123".encode("utf-8")
        spoof_frame = core._codec.encode("S", spoof_payload, msg_id=999, flags=0)
        recv_conn = (_Reader(spoof_frame), _Writer())
        core.conn = recv_conn
        await core.receive_loop(recv_conn)

        self.assertTrue(any("Protocol downgrade detected" in e for e in errors))
        self.assertEqual(delivered, [])
        self.assertIn(123, core._pending_file_acks)

    async def test_pending_ack_ttl_and_limit_pruning(self) -> None:
        core = I2PChatCore()
        core.ACK_MAX_PENDING = 2
        core.ACK_PRUNE_INTERVAL = 0.0

        core._register_pending_ack(core._pending_text_acks, 1, token="a", ack_kind="msg")
        core._register_pending_ack(core._pending_text_acks, 2, token="b", ack_kind="msg")
        core._register_pending_ack(core._pending_text_acks, 3, token="c", ack_kind="msg")
        self.assertLessEqual(core._total_pending_acks(), 2)

        for table in (core._pending_text_acks, core._pending_file_acks, core._pending_image_acks):
            for entry in table.values():
                entry.created_at = time.monotonic() - 9999
        core._prune_pending_acks(force=True)
        self.assertEqual(core._total_pending_acks(), 0)

    async def test_msg_ack_with_session_context_mismatch_is_ignored(self) -> None:
        import i2p_chat_core as core_module

        original_crypto = (
            core_module.crypto.NACL_AVAILABLE,
            core_module.crypto.encrypt_message,
            core_module.crypto.decrypt_message,
            core_module.crypto.compute_mac,
            core_module.crypto.verify_mac,
        )
        core_module.crypto.NACL_AVAILABLE = True
        core_module.crypto.encrypt_message = lambda _k, p: p  # type: ignore[assignment]
        core_module.crypto.decrypt_message = lambda _k, c: c  # type: ignore[assignment]
        core_module.crypto.compute_mac = lambda _k, _t, _b, seq=None: b"x" * 32  # type: ignore[assignment]
        core_module.crypto.verify_mac = lambda _k, _t, _b, _m, seq=None: True  # type: ignore[assignment]

        core = I2PChatCore()
        core.current_peer_addr = "peer.b32.i2p"
        core._ack_session_epoch = 1
        core._register_pending_ack(
            core._pending_text_acks,
            55,
            token="hello",
            ack_kind="msg",
        )
        # Simulate new connection/session context.
        core._ack_session_epoch = 2
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]

        peer_core = I2PChatCore()
        peer_core.handshake_complete = True
        peer_core.use_encryption = True
        peer_core.shared_key = b"x" * 32
        ack_frame = peer_core.frame_message("S", "__SIGNAL__:MSG_ACK|55")

        try:
            conn = (_Reader(ack_frame), _Writer())
            core.conn = conn
            await core.receive_loop(conn)
            self.assertIn(55, core._pending_text_acks)
        finally:
            (
                core_module.crypto.NACL_AVAILABLE,
                core_module.crypto.encrypt_message,
                core_module.crypto.decrypt_message,
                core_module.crypto.compute_mac,
                core_module.crypto.verify_mac,
            ) = original_crypto

    async def test_ack_telemetry_counts_context_mismatch(self) -> None:
        import i2p_chat_core as core_module

        original_crypto = (
            core_module.crypto.NACL_AVAILABLE,
            core_module.crypto.encrypt_message,
            core_module.crypto.decrypt_message,
            core_module.crypto.compute_mac,
            core_module.crypto.verify_mac,
        )
        core_module.crypto.NACL_AVAILABLE = True
        core_module.crypto.encrypt_message = lambda _k, p: p  # type: ignore[assignment]
        core_module.crypto.decrypt_message = lambda _k, c: c  # type: ignore[assignment]
        core_module.crypto.compute_mac = lambda _k, _t, _b, seq=None: b"x" * 32  # type: ignore[assignment]
        core_module.crypto.verify_mac = lambda _k, _t, _b, _m, seq=None: True  # type: ignore[assignment]

        core = I2PChatCore()
        core.current_peer_addr = "peer-a.b32.i2p"
        core._ack_session_epoch = 1
        core._register_pending_ack(
            core._pending_file_acks,
            77,
            token="safe.txt",
            ack_kind="file",
        )
        # Switch peer context before ACK arrives.
        core.current_peer_addr = "peer-b.b32.i2p"
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]

        peer_core = I2PChatCore()
        peer_core.handshake_complete = True
        peer_core.use_encryption = True
        peer_core.shared_key = b"x" * 32
        frame = peer_core.frame_message("S", "__SIGNAL__:FILE_ACK|safe.txt|77")
        try:
            conn = (_Reader(frame), _Writer())
            core.conn = conn
            await core.receive_loop(conn)
            telemetry = core.get_ack_telemetry()
            self.assertGreaterEqual(telemetry.get("context_mismatch", 0), 1)
            self.assertIn(77, core._pending_file_acks)
        finally:
            (
                core_module.crypto.NACL_AVAILABLE,
                core_module.crypto.encrypt_message,
                core_module.crypto.decrypt_message,
                core_module.crypto.compute_mac,
                core_module.crypto.verify_mac,
            ) = original_crypto


if __name__ == "__main__":
    unittest.main()
