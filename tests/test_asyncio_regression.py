import asyncio
import base64
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

# CI/agent environment may not have Pillow installed; for these tests
# image functionality is irrelevant, so a lightweight stub is enough.
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat.blindbox.blindbox_blob import decrypt_blindbox_blob
from i2pchat.blindbox.blindbox_key_schedule import derive_blindbox_message_keys
from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.protocol.protocol_codec import ProtocolCodec

LOCAL_B32 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B32 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
LOCKED_B32 = "cccccccccccccccccccccccccccccccccccccccc.b32.i2p"
OTHER_B32 = "dddddddddddddddddddddddddddddddddddddddd.b32.i2p"
EXAMPLE_HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
EXAMPLE_B32 = EXAMPLE_HOST + ".b32.i2p"


class _FakeReader:
    def __init__(self, payload: bytes) -> None:
        self._buffer = bytearray(payload)

    async def read(self, n: int = -1) -> bytes:
        if not self._buffer:
            return b""
        if n < 0 or n >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data

    async def readexactly(self, n: int) -> bytes:
        if len(self._buffer) < n:
            partial = bytes(self._buffer)
            self._buffer.clear()
            raise asyncio.IncompleteReadError(partial=partial, expected=n)
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _BlindBoxCaptureClient:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, object]] = []

    async def put(self, key: str, blob: bytes, *, queue_caps=None):
        self.puts.append((key, bytes(blob), queue_caps))
        return []


class AsyncioRegressionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _decode_frame(raw: bytes):
        codec = ProtocolCodec(
            allowed_types={"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"},
            max_frame_body=I2PChatCore.MAX_FRAME_BODY,
        )
        frame = await codec.read_frame(_FakeReader(raw))
        return frame

    def _make_blindbox_core(self) -> I2PChatCore:
        core = I2PChatCore(profile="alice")
        core.my_signing_seed = b"D" * 32
        core.stored_peer = PEER_B32
        core.current_peer_addr = PEER_B32
        core.my_dest = SimpleNamespace(base32=LOCAL_B32)
        return core

    def test_invalid_profile_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            I2PChatCore(profile="../../escape")

    def test_profile_paths_stay_within_profiles_dir(self) -> None:
        import i2pchat.core.i2p_chat_core as core_module

        original_get_profiles_dir = core_module.get_profiles_dir
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                core_module.get_profiles_dir = lambda: tmp_dir  # type: ignore[assignment]
                core = I2PChatCore(profile="alice")
                allowed_prefix = os.path.abspath(tmp_dir) + os.sep
                self.assertTrue(core._profile_path().startswith(allowed_prefix))
                self.assertTrue(core._trust_store_path().startswith(allowed_prefix))
                self.assertTrue(core._signing_seed_path().startswith(allowed_prefix))
        finally:
            core_module.get_profiles_dir = original_get_profiles_dir  # type: ignore[assignment]

    def test_profile_paths_reject_symlink_targets(self) -> None:
        import i2pchat.core.i2p_chat_core as core_module

        original_get_profiles_dir = core_module.get_profiles_dir
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                outside_path = os.path.join(tmp_dir, "outside.dat")
                with open(outside_path, "w", encoding="utf-8") as f:
                    f.write("x")
                nest = os.path.join(tmp_dir, "profiles", "alice")
                os.makedirs(nest, exist_ok=True)
                os.symlink(outside_path, os.path.join(nest, "alice.dat"))
                core_module.get_profiles_dir = lambda: tmp_dir  # type: ignore[assignment]
                core = I2PChatCore(profile="alice")
                with self.assertRaises(ValueError):
                    core._profile_path()
        finally:
            core_module.get_profiles_dir = original_get_profiles_dir  # type: ignore[assignment]

    def test_lock_requires_verified_identity_binding(self) -> None:
        core = I2PChatCore(profile="alice")
        core.current_peer_addr = PEER_B32
        core.handshake_complete = True
        core.peer_identity_binding_verified = False
        self.assertFalse(core.is_current_peer_verified_for_lock())
        core.peer_identity_binding_verified = True
        self.assertTrue(core.is_current_peer_verified_for_lock())

    async def test_connect_sends_identity_line_before_framed_identity(self) -> None:
        core = I2PChatCore()
        core.my_dest = SimpleNamespace(base64="DEST_B64")
        core._start_handshake_watchdog = lambda _conn: None  # type: ignore[assignment]
        core.receive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
        core.initiate_secure_handshake = AsyncMock(return_value=True)  # type: ignore[method-assign]
        core._keepalive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

        reader = _FakeReader(b"")
        writer = _FakeWriter()

        import i2pchat.core.i2p_chat_core as core_module

        original_stream_connect = core_module.i2plib.stream_connect
        original_nacl_available = core_module.crypto.NACL_AVAILABLE

        async def _fake_stream_connect(session_id: str, target: str, sam_address=None):
            return reader, writer

        core_module.i2plib.stream_connect = _fake_stream_connect  # type: ignore[assignment]
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core.connect_to_peer(PEER_B32)
        finally:
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        payload = bytes(writer.buffer)
        self.assertTrue(payload.startswith(b"DEST_B64\n"))

    async def test_protocol_downgrade_schedules_disconnect_without_reentrancy(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(on_error=errors.append)
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32

        codec = ProtocolCodec(
            allowed_types={"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"},
            max_frame_body=core.MAX_FRAME_BODY,
        )
        # Plaintext user data after handshake must be treated as downgrade.
        reader = _FakeReader(codec.encode("U", b"x", msg_id=1, flags=0))
        writer = _FakeWriter()
        core.conn = (reader, writer)

        await core.receive_loop(core.conn)
        await asyncio.sleep(0)

        self.assertTrue(any("Protocol downgrade detected" in e for e in errors))
        self.assertIsNone(core.conn)
        self.assertTrue(writer.closed)
        self.assertFalse(core._recv_loop_active)

    async def test_schedule_disconnect_is_idempotent_while_task_running(self) -> None:
        core = I2PChatCore()
        core.conn = (_FakeReader(b""), _FakeWriter())
        disconnect_mock: AsyncMock = AsyncMock()
        core.disconnect = disconnect_mock  # type: ignore[method-assign]

        core._schedule_disconnect()
        core._schedule_disconnect()
        await asyncio.sleep(0)

        self.assertEqual(disconnect_mock.call_count, 1)

    async def test_handshake_role_conflict_on_init_triggers_disconnect(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(on_error=errors.append)
        core._handshake_initiated = True
        writer = _FakeWriter()
        core._disconnect_scheduled_for_test = False  # type: ignore[attr-defined]

        def _mark_disconnect() -> None:
            core._disconnect_scheduled_for_test = True  # type: ignore[attr-defined]

        core._schedule_disconnect = _mark_disconnect  # type: ignore[method-assign]

        import i2pchat.core.i2p_chat_core as core_module

        original_nacl_available = core_module.crypto.NACL_AVAILABLE
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core._handle_handshake_message("INIT:malformed", writer)
        finally:
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        self.assertEqual(writer.buffer, bytearray())
        self.assertTrue(getattr(core, "_disconnect_scheduled_for_test", False))
        self.assertTrue(any("role conflict" in msg.lower() for msg in errors), errors)

    async def test_pin_or_verify_waits_for_trust_decision_future(self) -> None:
        seen: Optional[tuple[str, str, str]] = None

        def trust_cb(peer: str, fp: str, key_hex: str) -> bool:
            nonlocal seen
            seen = (peer, fp, key_hex)
            return True

        core = I2PChatCore(on_trust_decision=trust_cb)
        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_B32,
            b"\x11" * 32,
        )

        self.assertTrue(ok)
        self.assertIsNotNone(seen)
        self.assertIn(EXAMPLE_B32, core.peer_trusted_signing_keys)

    async def test_connect_rejects_target_that_differs_from_locked_peer(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(profile="alice", on_error=errors.append)
        core.stored_peer = LOCKED_B32
        import i2pchat.core.i2p_chat_core as core_module

        original_nacl_available = core_module.crypto.NACL_AVAILABLE
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core.connect_to_peer(OTHER_B32)
        finally:
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        self.assertIsNone(core.conn)
        self.assertTrue(
            any("locked to another peer" in msg.lower() for msg in errors),
            errors,
        )

    async def test_blindbox_root_not_sent_when_connected_peer_differs_from_lock(self) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        try:
            errors: list[str] = []
            core = I2PChatCore(profile="alice", on_error=errors.append)
            core.my_signing_seed = b"D" * 32
            core.stored_peer = LOCKED_B32
            core.current_peer_addr = OTHER_B32
            core.my_dest = SimpleNamespace(base32=LOCAL_B32)
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)

            self.assertEqual(writer.buffer, bytearray())
            self.assertTrue(
                any("does not match locked peer" in msg for msg in errors),
                errors,
            )
        finally:
            if old_enabled is None:
                os.environ.pop("I2PCHAT_BLINDBOX_ENABLED", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_ENABLED"] = old_enabled
            if old_replicas is None:
                os.environ.pop("I2PCHAT_BLINDBOX_REPLICAS", None)
            else:
                os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = old_replicas

    async def test_blindbox_root_sender_waits_for_ack_before_switching_active_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)

            self.assertIsNone(core._blindbox_root_secret)
            self.assertIsNotNone(core._blindbox_pending_root_secret)
            self.assertEqual(core._blindbox_root_epoch, 0)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)

    async def test_blindbox_root_receiver_applies_root_and_sends_ack(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            receiver = self._make_blindbox_core()
            writer = _FakeWriter()
            root_secret = b"\x44" * 32

            await receiver._handle_incoming_blindbox_root_signal(
                f"__SIGNAL__:BLINDBOX_ROOT|1|{root_secret.hex()}",
                writer,
            )

            self.assertEqual(receiver._blindbox_root_secret, root_secret)
            self.assertEqual(receiver._blindbox_root_epoch, 1)
            frame = await self._decode_frame(bytes(writer.buffer))
            self.assertEqual(frame.msg_type, "S")
            self.assertEqual(
                frame.payload.decode("utf-8"),
                "__SIGNAL__:BLINDBOX_ROOT_ACK|1",
            )

    async def test_blindbox_root_receiver_ignores_root_when_peer_differs_from_lock(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            errors: list[str] = []
            receiver = I2PChatCore(profile="alice", on_error=errors.append)
            receiver.my_signing_seed = b"D" * 32
            receiver.stored_peer = LOCKED_B32
            receiver.current_peer_addr = OTHER_B32
            receiver.my_dest = SimpleNamespace(base32=LOCAL_B32)
            writer = _FakeWriter()
            root_secret = b"\x44" * 32

            await receiver._handle_incoming_blindbox_root_signal(
                f"__SIGNAL__:BLINDBOX_ROOT|1|{root_secret.hex()}",
                writer,
            )

            self.assertIsNone(receiver._blindbox_root_secret)
            self.assertEqual(receiver._blindbox_root_epoch, 0)
            self.assertEqual(writer.buffer, bytearray())
            self.assertTrue(
                any("does not match locked peer" in msg for msg in errors),
                errors,
            )

    async def test_blindbox_root_reconnect_resends_same_pending_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            writer_a = _FakeWriter()
            writer_b = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer_a)
            await core._send_blindbox_root_if_needed(writer_b)

            frame_a = await self._decode_frame(bytes(writer_a.buffer))
            frame_b = await self._decode_frame(bytes(writer_b.buffer))
            self.assertEqual(frame_a.payload, frame_b.payload)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)

    async def test_blindbox_root_stale_ack_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)
            pending_secret = core._blindbox_pending_root_secret

            core._handle_blindbox_root_ack_signal("__SIGNAL__:BLINDBOX_ROOT_ACK|999")

            self.assertIsNone(core._blindbox_root_secret)
            self.assertEqual(core._blindbox_pending_root_secret, pending_secret)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)

    async def test_blindbox_root_ack_ignored_when_peer_differs_from_lock(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            errors: list[str] = []
            core = I2PChatCore(profile="alice", on_error=errors.append)
            core.my_signing_seed = b"D" * 32
            core.stored_peer = LOCKED_B32
            core.current_peer_addr = LOCKED_B32
            core.my_dest = SimpleNamespace(base32=LOCAL_B32)
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)
            pending_secret = core._blindbox_pending_root_secret

            core.current_peer_addr = OTHER_B32
            core._handle_blindbox_root_ack_signal("__SIGNAL__:BLINDBOX_ROOT_ACK|1")

            self.assertIsNone(core._blindbox_root_secret)
            self.assertEqual(core._blindbox_pending_root_secret, pending_secret)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)
            self.assertTrue(
                any("does not match locked peer" in msg for msg in errors),
                errors,
            )

    async def test_blindbox_root_rotation_preserves_previous_root_after_ack(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x11" * 32
            core._blindbox_root_epoch = 7
            core._blindbox_root_created_at = 100
            core._blindbox_root_send_index_base = 3
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer, force_rotate=True)
            new_secret = core._blindbox_pending_root_secret
            self.assertIsNotNone(new_secret)

            core._handle_blindbox_root_ack_signal(
                f"__SIGNAL__:BLINDBOX_ROOT_ACK|{core._blindbox_pending_root_epoch}"
            )

            self.assertEqual(core._blindbox_root_secret, new_secret)
            self.assertEqual(core._blindbox_root_epoch, 8)
            self.assertTrue(core._blindbox_prev_roots)
            self.assertEqual(core._blindbox_prev_roots[0]["epoch"], 7)
            self.assertEqual(core._blindbox_prev_roots[0]["secret"], b"\x11" * 32)
            self.assertIsNone(core._blindbox_pending_root_secret)

    async def test_blindbox_queue_receiver_applies_epoch_and_sends_ack(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            receiver = self._make_blindbox_core()
            writer = _FakeWriter()

            await receiver._handle_incoming_blindbox_queue_epoch_signal(
                "__SIGNAL__:BLINDBOX_QUEUE_EPOCH|4",
                writer,
            )

            self.assertEqual(receiver._blindbox_recv_queue_epoch, 4)
            frame = await self._decode_frame(bytes(writer.buffer))
            self.assertEqual(frame.msg_type, "S")
            self.assertEqual(
                frame.payload.decode("utf-8"),
                "__SIGNAL__:BLINDBOX_QUEUE_EPOCH_ACK|4",
            )

    async def test_blindbox_queue_reconnect_resends_same_pending_epoch(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x55" * 32
            core._blindbox_root_epoch = 1
            core._blindbox_send_queue_epoch = 2
            core._blindbox_send_queue_created_at = 100
            core._blindbox_send_queue_send_index_base = 0
            writer_a = _FakeWriter()
            writer_b = _FakeWriter()

            await core._send_blindbox_queue_epoch_if_needed(writer_a, force_rotate=True)
            await core._send_blindbox_queue_epoch_if_needed(writer_b, force_rotate=True)

            frame_a = await self._decode_frame(bytes(writer_a.buffer))
            frame_b = await self._decode_frame(bytes(writer_b.buffer))
            self.assertEqual(frame_a.payload, frame_b.payload)
            self.assertEqual(core._blindbox_pending_send_queue_epoch, 3)

    async def test_blindbox_queue_rotation_commits_after_ack(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x66" * 32
            core._blindbox_root_epoch = 1
            core._blindbox_send_queue_epoch = 5
            core._blindbox_send_queue_created_at = 100
            core._blindbox_send_queue_send_index_base = 3
            writer = _FakeWriter()

            await core._send_blindbox_queue_epoch_if_needed(writer, force_rotate=True)
            self.assertEqual(core._blindbox_pending_send_queue_epoch, 6)

            core._handle_blindbox_queue_epoch_ack_signal(
                "__SIGNAL__:BLINDBOX_QUEUE_EPOCH_ACK|6"
            )

            self.assertEqual(core._blindbox_send_queue_epoch, 6)
            self.assertEqual(core._blindbox_pending_send_queue_epoch, 0)

    async def test_offline_file_send_queues_f_d_e_frames(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x33" * 32
            core._blindbox_root_epoch = 1
            core._blindbox_send_queue_epoch = 2
            client = _BlindBoxCaptureClient()
            core._blindbox_client = client  # noqa: SLF001 - test double
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "note.txt")
                with open(path, "wb") as f:
                    f.write(b"hello offline file")

                await core.send_file(path)

            self.assertGreaterEqual(len(client.puts), 3)
            decoded_types: list[str] = []
            for index, (_key, blob, _queue_caps) in enumerate(client.puts):
                keys = derive_blindbox_message_keys(
                    core._blindbox_root_secret,
                    LOCAL_B32,
                    PEER_B32,
                    "send",
                    index,
                    epoch=core._blindbox_root_epoch,
                )
                frame = decrypt_blindbox_blob(
                    blob,
                    keys.blob_key,
                    expected_direction="send",
                    expected_index=index,
                    expected_state_tag=keys.state_tag,
                )
                parsed = await self._decode_frame(frame)
                decoded_types.append(parsed.msg_type)
            self.assertEqual(decoded_types[0], "F")
            self.assertIn("D", decoded_types)
            self.assertEqual(decoded_types[-1], "E")

    async def test_offline_file_send_rejects_above_200mb_limit(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x30" * 32
            core._blindbox_root_epoch = 1
            core._blindbox_send_queue_epoch = 2
            client = _BlindBoxCaptureClient()
            core._blindbox_client = client  # noqa: SLF001
            errors: list[str] = []
            core.on_error = errors.append
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "huge.bin")
                with open(path, "wb") as f:
                    f.write(b"x")
                with patch(
                    "i2pchat.core.i2p_chat_core.os.path.getsize",
                    return_value=201 * 1024 * 1024,
                ):
                    await core.send_file(path)
            self.assertEqual(client.puts, [])
            self.assertTrue(any("200 MB" in msg for msg in errors), errors)

    async def test_offline_file_receive_writes_download(self) -> None:
        core = I2PChatCore(profile="alice")
        core.stored_peer = PEER_B32
        core.current_peer_addr = PEER_B32
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "i2pchat.core.i2p_chat_core.get_downloads_dir",
                return_value=td,
            ):
                core._request_file_offer_decision = AsyncMock(return_value=True)  # noqa: SLF001
                payload = b"offline file body"
                header = core._codec.encode("F", b"report.txt|17", msg_id=11, flags=0)
                chunk = core._codec.encode(
                    "D", base64.b64encode(payload), msg_id=0, flags=0
                )
                end = core._codec.encode("E", b"", msg_id=0, flags=0)

                self.assertTrue(await core._process_blindbox_frame(header))
                self.assertTrue(await core._process_blindbox_frame(chunk))
                self.assertTrue(await core._process_blindbox_frame(end))

                out_path = os.path.join(td, "report.txt")
                with open(out_path, "rb") as f:
                    self.assertEqual(f.read(), payload)

    async def test_offline_text_roundtrip_emits_receipt_back_to_sender(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            sender = self._make_blindbox_core()
            sender._blindbox_root_secret = b"\x31" * 32
            sender._blindbox_root_epoch = 1
            sender._blindbox_send_queue_epoch = 2
            sender_client = _BlindBoxCaptureClient()
            sender._blindbox_client = sender_client  # noqa: SLF001

            delivered: list[str] = []
            sender.on_text_delivered = delivered.append
            msg_id = await sender._send_text_via_blindbox("hello receipt")
            self.assertIsNotNone(msg_id)

            _key, blob, _queue_caps = sender_client.puts[0]
            keys = derive_blindbox_message_keys(
                sender._blindbox_root_secret,
                LOCAL_B32,
                PEER_B32,
                "send",
                0,
                epoch=sender._blindbox_root_epoch,
            )
            frame = decrypt_blindbox_blob(
                blob,
                keys.blob_key,
                expected_direction="send",
                expected_index=0,
                expected_state_tag=keys.state_tag,
            )

            receiver = self._make_blindbox_core()
            receiver._blindbox_root_secret = sender._blindbox_root_secret
            receiver._blindbox_root_epoch = sender._blindbox_root_epoch
            receiver._blindbox_send_queue_epoch = 2
            receiver.my_dest = SimpleNamespace(base32=PEER_B32)
            receiver.stored_peer = LOCAL_B32
            receiver.current_peer_addr = LOCAL_B32
            receiver_client = _BlindBoxCaptureClient()
            receiver._blindbox_client = receiver_client  # noqa: SLF001

            self.assertTrue(await receiver._process_blindbox_frame(frame))
            self.assertTrue(receiver_client.puts)

            _ack_key, ack_blob, _ack_caps = receiver_client.puts[0]
            ack_keys = derive_blindbox_message_keys(
                sender._blindbox_root_secret,
                LOCAL_B32,
                PEER_B32,
                "recv",
                0,
                epoch=sender._blindbox_root_epoch,
            )
            ack_frame = decrypt_blindbox_blob(
                ack_blob,
                ack_keys.blob_key,
                expected_direction="send",
                expected_index=0,
                expected_state_tag=ack_keys.state_tag,
            )

            self.assertTrue(await sender._process_blindbox_frame(ack_frame))
            self.assertEqual(delivered, [str(msg_id)])

    async def test_offline_file_roundtrip_emits_receipt_back_to_sender(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            sender = self._make_blindbox_core()
            sender._blindbox_root_secret = b"\x32" * 32
            sender._blindbox_root_epoch = 1
            sender._blindbox_send_queue_epoch = 2
            sender_client = _BlindBoxCaptureClient()
            sender._blindbox_client = sender_client  # noqa: SLF001
            delivered_files: list[str] = []
            sender.on_file_delivered = delivered_files.append

            with tempfile.TemporaryDirectory() as td:
                file_path = os.path.join(td, "offline.txt")
                with open(file_path, "wb") as f:
                    f.write(b"offline ack file")
                await sender._send_file_via_blindbox(file_path)

                receiver = self._make_blindbox_core()
                receiver._blindbox_root_secret = sender._blindbox_root_secret
                receiver._blindbox_root_epoch = sender._blindbox_root_epoch
                receiver._blindbox_send_queue_epoch = 2
                receiver.my_dest = SimpleNamespace(base32=PEER_B32)
                receiver.stored_peer = LOCAL_B32
                receiver.current_peer_addr = LOCAL_B32
                receiver_client = _BlindBoxCaptureClient()
                receiver._blindbox_client = receiver_client  # noqa: SLF001

                with patch(
                    "i2pchat.core.i2p_chat_core.get_downloads_dir",
                    return_value=td,
                ):
                    receiver._request_file_offer_decision = AsyncMock(return_value=True)  # noqa: SLF001
                    for index, (_key, blob, _queue_caps) in enumerate(sender_client.puts):
                        keys = derive_blindbox_message_keys(
                            sender._blindbox_root_secret,
                            LOCAL_B32,
                            PEER_B32,
                            "send",
                            index,
                            epoch=sender._blindbox_root_epoch,
                        )
                        frame = decrypt_blindbox_blob(
                            blob,
                            keys.blob_key,
                            expected_direction="send",
                            expected_index=index,
                            expected_state_tag=keys.state_tag,
                        )
                        await receiver._process_blindbox_frame(frame)

                self.assertTrue(receiver_client.puts)
                _ack_key, ack_blob, _ack_caps = receiver_client.puts[-1]
                ack_index = len(receiver_client.puts) - 1
                ack_keys = derive_blindbox_message_keys(
                    sender._blindbox_root_secret,
                    LOCAL_B32,
                    PEER_B32,
                    "recv",
                    ack_index,
                    epoch=sender._blindbox_root_epoch,
                )
                ack_frame = decrypt_blindbox_blob(
                    ack_blob,
                    ack_keys.blob_key,
                    expected_direction="send",
                    expected_index=ack_index,
                    expected_state_tag=ack_keys.state_tag,
                )
                self.assertTrue(await sender._process_blindbox_frame(ack_frame))
                self.assertTrue(delivered_files)

    async def test_offline_image_send_queues_g_frames(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            core = self._make_blindbox_core()
            core._blindbox_root_secret = b"\x44" * 32
            core._blindbox_root_epoch = 1
            core._blindbox_send_queue_epoch = 2
            client = _BlindBoxCaptureClient()
            core._blindbox_client = client  # noqa: SLF001 - test double
            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\x0DIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
                b"\x90wS\xde"
                b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb1"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "tiny.png")
                with open(path, "wb") as f:
                    f.write(png_bytes)
                with patch(
                    "i2pchat.core.i2p_chat_core.validate_image",
                    return_value=(True, "", "png"),
                ), patch(
                    "i2pchat.core.i2p_chat_core.get_images_dir",
                    return_value=td,
                ), patch(
                    "i2pchat.core.i2p_chat_core.cleanup_images_cache",
                    return_value=None,
                ):
                    local_path = await core.send_image(path)

            self.assertIsNotNone(local_path)
            self.assertGreaterEqual(len(client.puts), 3)
            decoded_types: list[str] = []
            decoded_bodies: list[str] = []
            for index, (_key, blob, _queue_caps) in enumerate(client.puts):
                keys = derive_blindbox_message_keys(
                    core._blindbox_root_secret,
                    LOCAL_B32,
                    PEER_B32,
                    "send",
                    index,
                    epoch=core._blindbox_root_epoch,
                )
                frame = decrypt_blindbox_blob(
                    blob,
                    keys.blob_key,
                    expected_direction="send",
                    expected_index=index,
                    expected_state_tag=keys.state_tag,
                )
                parsed = await self._decode_frame(frame)
                decoded_types.append(parsed.msg_type)
                decoded_bodies.append(parsed.payload.decode("utf-8"))
            self.assertEqual(decoded_types[0], "G")
            self.assertEqual(decoded_types[-1], "G")
            self.assertEqual(decoded_bodies[-1], "__IMG_END__")

    async def test_offline_image_receive_reconstructs_image(self) -> None:
        core = I2PChatCore(profile="alice")
        core.stored_peer = PEER_B32
        core.current_peer_addr = PEER_B32
        captured: list[tuple[str, bool, Optional[str]]] = []
        core._emit_inline_image = lambda path, is_from_me=False, sent_filename=None: captured.append((path, is_from_me, sent_filename))  # noqa: SLF001
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0DIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb1"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "offline.png")

            def _fake_finalize(image_bytes: bytes, detected_ext: str, images_dir: str):
                with open(out_path, "wb") as f:
                    f.write(image_bytes)
                return out_path, None

            with patch(
                "i2pchat.core.i2p_chat_core._finalize_inline_image_worker",
                side_effect=_fake_finalize,
            ), patch(
                "i2pchat.core.i2p_chat_core.cleanup_images_cache",
                return_value=None,
            ), patch(
                "i2pchat.core.i2p_chat_core.get_images_dir",
                return_value=td,
            ):
                header = core._codec.encode(
                    "G", f"offline.png|{len(png_bytes)}".encode("utf-8"), msg_id=12, flags=0
                )
                chunk = core._codec.encode(
                    "G", base64.b64encode(png_bytes), msg_id=0, flags=0
                )
                end = core._codec.encode("G", b"__IMG_END__", msg_id=0, flags=0)

                self.assertTrue(await core._process_blindbox_frame(header))
                self.assertTrue(await core._process_blindbox_frame(chunk))
                self.assertTrue(await core._process_blindbox_frame(end))

                with open(out_path, "rb") as f:
                    self.assertEqual(f.read(), png_bytes)
                self.assertTrue(captured)
                self.assertEqual(captured[0][0], out_path)

    async def test_tofu_without_callback_requires_explicit_policy(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(profile="default", on_error=errors.append)

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_B32,
            b"\x22" * 32,
        )

        self.assertFalse(ok)
        self.assertNotIn(EXAMPLE_B32, core.peer_trusted_signing_keys)
        self.assertTrue(any("I2PCHAT_TRUST_AUTO=1" in msg for msg in errors), errors)

    async def test_tofu_auto_pin_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"I2PCHAT_TRUST_AUTO": "1"}, clear=False):
            core = I2PChatCore(profile="default")
            ok = await core._pin_or_verify_peer_signing_key(
                EXAMPLE_B32,
                b"\x33" * 32,
            )

        self.assertTrue(ok)
        self.assertIn(EXAMPLE_B32, core.peer_trusted_signing_keys)

    def test_forget_pinned_peer_key_removes_normalized_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=tmpdir):
                core = I2PChatCore(profile="alice")
                core.peer_trusted_signing_keys[EXAMPLE_B32] = "ab" * 32

                removed = core.forget_pinned_peer_key(EXAMPLE_HOST)

                self.assertTrue(removed)
                self.assertNotIn(EXAMPLE_B32, core.peer_trusted_signing_keys)
                with open(core._trust_store_path(), "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), "{}")

    async def test_signing_key_mismatch_emits_explicit_rejection_message(self) -> None:
        errors: list[str] = []
        systems: list[str] = []
        core = I2PChatCore(profile="alice", on_error=errors.append, on_system=systems.append)
        core.peer_trusted_signing_keys[EXAMPLE_B32] = "11" * 32

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_B32,
            b"\x22" * 32,
        )

        self.assertFalse(ok)
        self.assertTrue(any("Peer signing key mismatch" in msg for msg in errors), errors)
        self.assertTrue(
            any("not approved" in msg for msg in systems),
            systems,
        )

    async def test_signing_key_mismatch_can_replace_pin_when_user_approves(self) -> None:
        systems: list[str] = []
        decisions: list[tuple[str, str, str, str, str]] = []

        def trust_mismatch_cb(
            peer: str,
            old_fp: str,
            new_fp: str,
            old_key: str,
            new_key: str,
        ) -> bool:
            decisions.append((peer, old_fp, new_fp, old_key, new_key))
            return True

        core = I2PChatCore(
            profile="alice",
            on_system=systems.append,
            on_trust_mismatch_decision=trust_mismatch_cb,
        )
        core.peer_trusted_signing_keys[EXAMPLE_B32] = "11" * 32

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_B32,
            b"\x22" * 32,
        )

        self.assertTrue(ok)
        self.assertEqual(core.peer_trusted_signing_keys[EXAMPLE_B32], "22" * 32)
        self.assertEqual(len(decisions), 1)
        self.assertTrue(any("Updated trusted signing key" in msg for msg in systems))


if __name__ == "__main__":
    unittest.main()
