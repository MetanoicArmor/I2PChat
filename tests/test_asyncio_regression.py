import asyncio
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from typing import Callable, Optional
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

from i2pchat.core.i2p_chat_core import I2PChatCore, _BlindBoxPeerSnapshot
from i2pchat.core.live_peer_session import LivePeerSession
from i2pchat.protocol.protocol_codec import ProtocolCodec
from i2pchat.groups import GroupState
from i2pchat.storage.blindbox_state import BlindBoxState
from i2pchat.storage.group_store import GroupBlindBoxChannel

from tests.live_session_helpers import attach_mock_live_session

LOCAL_BARE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PEER_BARE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
LOCKED_BARE = "cccccccccccccccccccccccccccccccccccccccc"
OTHER_BARE = "dddddddddddddddddddddddddddddddddddddddd"
EXAMPLE_HOST = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
EXAMPLE_BARE = EXAMPLE_HOST


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


class AsyncioRegressionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _decode_frame(raw: bytes):
        codec = ProtocolCodec(
            allowed_types={"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"},
            max_frame_body=I2PChatCore.MAX_FRAME_BODY,
        )
        frame = await codec.read_frame(_FakeReader(raw))
        return frame

    def _make_blindbox_core(
        self, on_error: Optional[Callable[[str], None]] = None
    ) -> I2PChatCore:
        core = I2PChatCore(profile="alice", on_error=on_error)
        core.my_signing_seed = b"D" * 32
        core.current_peer_addr = PEER_BARE
        core.my_dest = SimpleNamespace(base32=LOCAL_BARE)
        core.handshake_complete = True
        core.peer_identity_binding_verified = True
        # Multi-peer: root exchange requires Saved peer; tests use a synthetic session.
        core.peer_in_saved_contacts = lambda _addr: True  # type: ignore[method-assign]
        attach_mock_live_session(
            core,
            PEER_BARE,
            (_FakeReader(b""), _FakeWriter()),
            handshake_complete=True,
            use_encryption=False,
        )
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
        k = attach_mock_live_session(
            core, PEER_BARE, (_FakeReader(b""), _FakeWriter()), handshake_complete=True
        )
        ls = core._live_sessions[k]
        ls.peer_identity_binding_verified = False
        self.assertFalse(core.is_current_peer_verified_for_lock())
        ls.peer_identity_binding_verified = True
        self.assertTrue(core.is_current_peer_verified_for_lock())

    def test_peer_in_saved_contacts_allows_current_selected_peer(self) -> None:
        core = I2PChatCore(profile="alice")
        core.current_peer_addr = PEER_BARE

        self.assertTrue(core.peer_in_saved_contacts(PEER_BARE))

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
            await core.connect_to_peer(PEER_BARE)
        finally:
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        payload = bytes(writer.buffer)
        self.assertTrue(payload.startswith(b"DEST_B64\n"))

    async def test_connect_to_peer_marks_target_as_saved_before_network_attempt(self) -> None:
        core = I2PChatCore(profile="alice")
        remembered: list[str] = []
        core.ensure_peer_in_saved_contacts = lambda peer: remembered.append(peer) or True  # type: ignore[method-assign]

        import i2pchat.core.i2p_chat_core as core_module

        original_nacl_available = core_module.crypto.NACL_AVAILABLE
        original_stream_connect = core_module.i2plib.stream_connect

        async def _fail_stream_connect(*_args, **_kwargs):
            raise RuntimeError("boom")

        core_module.crypto.NACL_AVAILABLE = True
        core_module.i2plib.stream_connect = _fail_stream_connect  # type: ignore[assignment]
        try:
            await core.connect_to_peer(PEER_BARE)
        finally:
            core_module.crypto.NACL_AVAILABLE = original_nacl_available
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]

        self.assertEqual(remembered, [PEER_BARE])

    async def test_accept_loop_allows_first_contact_and_adds_peer_to_saved(self) -> None:
        core = I2PChatCore(profile="alice")
        core.my_dest = SimpleNamespace(base64="DEST_B64")
        remembered: list[str] = []
        systems: list[str] = []
        core.on_system = systems.append
        core.ensure_peer_in_saved_contacts = lambda peer: remembered.append(peer) or True  # type: ignore[method-assign]
        core._set_verified_peer_identity = AsyncMock(return_value=True)  # type: ignore[method-assign]
        core._start_receive_loop_task = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        core._start_handshake_watchdog = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        core._keepalive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

        class _AcceptReader(_FakeReader):
            async def readline(self) -> bytes:
                if not self._buffer:
                    return b""
                newline_idx = self._buffer.find(b"\n")
                if newline_idx < 0:
                    data = bytes(self._buffer)
                    self._buffer.clear()
                    return data
                data = bytes(self._buffer[: newline_idx + 1])
                del self._buffer[: newline_idx + 1]
                return data

        reader = _AcceptReader(b"dest-b64\n")
        writer = _FakeWriter()

        import i2pchat.core.i2p_chat_core as core_module

        original_stream_accept = core_module.i2plib.stream_accept
        original_destination = core_module.i2plib.Destination

        calls = 0

        async def _fake_stream_accept(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return reader, writer
            raise asyncio.CancelledError()

        class _FakeDest:
            def __init__(self, raw: str) -> None:
                self.base32 = PEER_BARE

        core_module.i2plib.stream_accept = _fake_stream_accept  # type: ignore[assignment]
        core_module.i2plib.Destination = _FakeDest  # type: ignore[assignment]
        try:
            with self.assertRaises(asyncio.CancelledError):
                await core.accept_loop()
        finally:
            core_module.i2plib.stream_accept = original_stream_accept  # type: ignore[assignment]
            core_module.i2plib.Destination = original_destination  # type: ignore[assignment]

        self.assertEqual(remembered, [PEER_BARE])
        self.assertTrue(any("Accepted first contact" in msg for msg in systems), systems)

    async def test_accept_loop_prefers_incoming_during_simultaneous_connect_when_local_sorts_after_peer(self) -> None:
        core = I2PChatCore(profile="alice")
        core.my_dest = SimpleNamespace(base64="DEST_B64", base32=OTHER_BARE)
        core.ensure_peer_in_saved_contacts = lambda _peer: False  # type: ignore[method-assign]
        core._set_verified_peer_identity = AsyncMock(return_value=True)  # type: ignore[method-assign]
        core._start_receive_loop_task = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        core._start_handshake_watchdog = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        core._keepalive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
        existing = LivePeerSession(peer_id=PEER_BARE)
        core._live_sessions[PEER_BARE] = existing

        class _AcceptReader(_FakeReader):
            async def readline(self) -> bytes:
                if not self._buffer:
                    return b""
                newline_idx = self._buffer.find(b"\n")
                if newline_idx < 0:
                    data = bytes(self._buffer)
                    self._buffer.clear()
                    return data
                data = bytes(self._buffer[: newline_idx + 1])
                del self._buffer[: newline_idx + 1]
                return data

        reader = _AcceptReader(b"dest-b64\n")
        writer = _FakeWriter()

        import i2pchat.core.i2p_chat_core as core_module

        original_stream_accept = core_module.i2plib.stream_accept
        original_destination = core_module.i2plib.Destination

        calls = 0

        async def _fake_stream_accept(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return reader, writer
            raise asyncio.CancelledError()

        class _FakeDest:
            def __init__(self, raw: str) -> None:
                self.base32 = PEER_BARE

        core_module.i2plib.stream_accept = _fake_stream_accept  # type: ignore[assignment]
        core_module.i2plib.Destination = _FakeDest  # type: ignore[assignment]
        try:
            with self.assertRaises(asyncio.CancelledError):
                await core.accept_loop()
        finally:
            core_module.i2plib.stream_accept = original_stream_accept  # type: ignore[assignment]
            core_module.i2plib.Destination = original_destination  # type: ignore[assignment]

        self.assertIn(PEER_BARE, core._live_sessions)
        self.assertIsNot(core._live_sessions[PEER_BARE], existing)
        self.assertIsNotNone(core._live_sessions[PEER_BARE].conn)

    async def test_connect_to_peer_abandons_outbound_when_session_slot_is_replaced(self) -> None:
        core = I2PChatCore(profile="alice")
        core.my_dest = SimpleNamespace(base64="DEST_B64", base32=LOCAL_BARE)
        core._start_handshake_watchdog = lambda *_args, **_kwargs: None  # type: ignore[assignment]
        core.receive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
        core.initiate_secure_handshake = AsyncMock(return_value=True)  # type: ignore[method-assign]
        core._keepalive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

        reader = _FakeReader(b"")
        writer = _FakeWriter()

        import i2pchat.core.i2p_chat_core as core_module

        original_stream_connect = core_module.i2plib.stream_connect
        original_nacl_available = core_module.crypto.NACL_AVAILABLE

        async def _fake_stream_connect(session_id: str, target: str, sam_address=None):
            norm = core._normalize_peer_addr(PEER_BARE)
            core._live_sessions[norm] = LivePeerSession(peer_id=norm)
            return reader, writer

        core_module.i2plib.stream_connect = _fake_stream_connect  # type: ignore[assignment]
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core.connect_to_peer(PEER_BARE)
        finally:
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        self.assertTrue(writer.closed)
        core.receive_loop.assert_not_called()  # type: ignore[attr-defined]
        core.initiate_secure_handshake.assert_not_called()  # type: ignore[attr-defined]

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
        conn = (reader, writer)
        k = attach_mock_live_session(
            core,
            PEER_BARE,
            conn,
            handshake_complete=True,
            use_encryption=True,
            shared_key=b"x" * 32,
        )

        await core.receive_loop(conn, peer_id=k)
        await asyncio.sleep(0)

        self.assertTrue(any("Protocol downgrade detected" in e for e in errors))
        self.assertNotIn(k, core._live_sessions)
        self.assertTrue(writer.closed)

    async def test_schedule_disconnect_is_idempotent_while_task_running(self) -> None:
        core = I2PChatCore()
        attach_mock_live_session(core, PEER_BARE, (_FakeReader(b""), _FakeWriter()))
        disconnect_mock: AsyncMock = AsyncMock()
        core.disconnect_peer = disconnect_mock  # type: ignore[method-assign]

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

        def _mark_disconnect(peer_id: Optional[str] = None) -> None:
            del peer_id
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
            EXAMPLE_BARE,
            b"\x11" * 32,
        )

        self.assertTrue(ok)
        self.assertIsNotNone(seen)
        self.assertIn(EXAMPLE_BARE, core.peer_trusted_signing_keys)

    async def test_connect_not_blocked_by_legacy_stored_peer_field(self) -> None:
        """Lock-to-peer removed; legacy stored_peer must not block outbound connect."""
        errors: list[str] = []
        core = I2PChatCore(profile="alice", on_error=errors.append)
        core.stored_peer = LOCKED_BARE
        import i2pchat.core.i2p_chat_core as core_module

        original_nacl_available = core_module.crypto.NACL_AVAILABLE
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core.connect_to_peer(OTHER_BARE)
        finally:
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        self.assertFalse(core._has_active_session_for_peer(OTHER_BARE))
        self.assertFalse(
            any("locked to another peer" in msg.lower() for msg in errors),
            errors,
        )

    async def test_connect_rejects_duplicate_pending_session_slot(self) -> None:
        systems: list[str] = []
        core = I2PChatCore(profile="alice", on_system=systems.append)
        normalized_peer = core._normalize_peer_addr(PEER_BARE)
        core._live_sessions[normalized_peer] = LivePeerSession(peer_id=normalized_peer)

        import i2pchat.core.i2p_chat_core as core_module

        original_nacl_available = core_module.crypto.NACL_AVAILABLE
        original_stream_connect = core_module.i2plib.stream_connect
        called = False

        async def _unexpected_stream_connect(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("stream_connect should not run for duplicate pending session")

        core_module.crypto.NACL_AVAILABLE = True
        core_module.i2plib.stream_connect = _unexpected_stream_connect  # type: ignore[assignment]
        try:
            await core.connect_to_peer(PEER_BARE)
        finally:
            core_module.crypto.NACL_AVAILABLE = original_nacl_available
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]

        self.assertFalse(called)
        self.assertIn("Already connected to this peer.", systems)

    async def test_disconnect_peer_cancels_owned_receive_task(self) -> None:
        core = I2PChatCore(profile="alice")
        writer = _FakeWriter()
        conn = (_FakeReader(b""), writer)
        normalized_peer = attach_mock_live_session(core, PEER_BARE, conn)
        receive_task = asyncio.create_task(asyncio.sleep(60))
        core._live_sessions[normalized_peer].receive_task = receive_task

        await core.disconnect_peer(normalized_peer)
        await asyncio.sleep(0)

        self.assertTrue(receive_task.cancelled())
        self.assertTrue(writer.closed)
        self.assertNotIn(normalized_peer, core._live_sessions)

    async def test_blindbox_root_not_sent_when_current_peer_not_in_saved_contacts(
        self,
    ) -> None:
        old_enabled = os.environ.get("I2PCHAT_BLINDBOX_ENABLED")
        old_replicas = os.environ.get("I2PCHAT_BLINDBOX_REPLICAS")
        os.environ["I2PCHAT_BLINDBOX_ENABLED"] = "1"
        os.environ["I2PCHAT_BLINDBOX_REPLICAS"] = "r1.b32.i2p"
        try:
            errors: list[str] = []
            core = I2PChatCore(profile="alice", on_error=errors.append)
            core.my_signing_seed = b"D" * 32
            core.current_peer_addr = OTHER_BARE
            core.my_dest = SimpleNamespace(base32=LOCAL_BARE)
            core.handshake_complete = True
            core.peer_identity_binding_verified = True
            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)

            self.assertEqual(writer.buffer, bytearray())
            self.assertTrue(
                any(
                    "Saved peer" in msg and "BlindBox root exchange blocked" in msg
                    for msg in errors
                ),
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
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
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
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
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

    async def test_blindbox_root_receiver_ignores_root_when_peer_not_saved(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
            },
            clear=False,
        ):
            errors: list[str] = []
            receiver = I2PChatCore(profile="alice", on_error=errors.append)
            receiver.my_signing_seed = b"D" * 32
            receiver.current_peer_addr = OTHER_BARE
            receiver.my_dest = SimpleNamespace(base32=LOCAL_BARE)
            receiver.handshake_complete = True
            receiver.peer_identity_binding_verified = True
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
                any(
                    "Saved peer" in msg and "BlindBox root ignored" in msg
                    for msg in errors
                ),
                errors,
            )

    async def test_blindbox_root_reconnect_resends_same_pending_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
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
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
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

    async def test_blindbox_root_ack_ignored_when_session_peer_not_saved(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
                "I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1",
            },
            clear=False,
        ):
            errors: list[str] = []
            core = self._make_blindbox_core(on_error=errors.append)

            def _saved_only_primary(addr: str) -> bool:
                try:
                    return core._normalize_peer_addr(addr or "") == PEER_BARE
                except Exception:
                    return False

            core.peer_in_saved_contacts = _saved_only_primary  # type: ignore[method-assign]

            writer = _FakeWriter()

            await core._send_blindbox_root_if_needed(writer)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)
            pending_secret = core._blindbox_pending_root_secret

            core.current_peer_addr = OTHER_BARE
            core._handle_blindbox_root_ack_signal("__SIGNAL__:BLINDBOX_ROOT_ACK|1")

            self.assertIsNone(core._blindbox_root_secret)
            self.assertEqual(core._blindbox_pending_root_secret, pending_secret)
            self.assertEqual(core._blindbox_pending_root_epoch, 1)
            self.assertTrue(
                any(
                    "Saved peer" in msg and "BlindBox root ACK ignored" in msg
                    for msg in errors
                ),
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

    async def test_group_blindbox_root_sender_waits_for_all_acks_before_commit(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = self._make_blindbox_core()
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                group_state = GroupState(
                    group_id="group-root-wait-all",
                    epoch=3,
                    members=(LOCAL_BARE, PEER_BARE, OTHER_BARE),
                    title="Wait all",
                )
                core.save_group_state(group_state)
                writer = _FakeWriter()

                await core._send_group_blindbox_root_if_needed(
                    writer,
                    group_state.group_id,
                    peer_id=PEER_BARE,
                )

                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertIsNone(snapshot.root_secret)
                self.assertIsNotNone(snapshot.pending_root_secret)
                self.assertEqual(
                    snapshot.pending_root_target_members,
                    (PEER_BARE, OTHER_BARE),
                )

                core._handle_group_blindbox_root_ack_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-wait-all|3|1",
                    peer_id=PEER_BARE,
                )
                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertIsNone(snapshot.root_secret)
                self.assertEqual(snapshot.pending_root_acked_members, {PEER_BARE})

                core._handle_group_blindbox_root_ack_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-wait-all|3|1",
                    peer_id=OTHER_BARE,
                )
                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertIsNotNone(snapshot.root_secret)
                self.assertIsNone(snapshot.pending_root_secret)

    async def test_group_blindbox_root_bootstrap_is_allowed_for_non_coordinator(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core.my_signing_seed = b"D" * 32
                core.my_dest = SimpleNamespace(base32=OTHER_BARE)
                core.current_peer_addr = LOCAL_BARE
                core.handshake_complete = True
                core.peer_identity_binding_verified = True
                core.peer_in_saved_contacts = lambda _addr: True  # type: ignore[method-assign]
                attach_mock_live_session(
                    core,
                    LOCAL_BARE,
                    (_FakeReader(b""), _FakeWriter()),
                    handshake_complete=True,
                    use_encryption=False,
                )
                group_state = GroupState(
                    group_id="group-root-bootstrap-non-coordinator",
                    epoch=1,
                    members=(LOCAL_BARE, OTHER_BARE),
                    title="Bootstrap",
                )
                core.save_group_state(group_state)
                writer = _FakeWriter()

                await core._send_group_blindbox_root_if_needed(
                    writer,
                    group_state.group_id,
                    peer_id=LOCAL_BARE,
                )

                self.assertNotEqual(writer.buffer, bytearray())
                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertIsNone(snapshot.root_secret)
                self.assertIsNotNone(snapshot.pending_root_secret)
                self.assertEqual(snapshot.pending_root_epoch, 1)

    async def test_group_blindbox_root_receiver_applies_root_for_group(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                receiver = self._make_blindbox_core()
                receiver.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                group_state = GroupState(
                    group_id="group-root-recv",
                    epoch=4,
                    members=(LOCAL_BARE, PEER_BARE),
                    title="Recv",
                )
                receiver.save_group_state(group_state)
                writer = _FakeWriter()
                root_secret = b"\x66" * 32

                await receiver._handle_incoming_group_blindbox_root_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT|group-root-recv|4|2|"
                    + root_secret.hex(),
                    writer,
                    peer_id=PEER_BARE,
                )

                snapshot_bundle = receiver._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertEqual(snapshot.root_secret, root_secret)
                self.assertEqual(snapshot.root_epoch, 2)
                frame = await self._decode_frame(bytes(writer.buffer))
                self.assertEqual(frame.msg_type, "S")
                self.assertEqual(
                    frame.payload.decode("utf-8"),
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-recv|4|2",
                )

    async def test_group_blindbox_root_receiver_accepts_newer_epoch_than_local_state(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                receiver = self._make_blindbox_core()
                receiver.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                group_state = GroupState(
                    group_id="group-root-recv-newer-epoch",
                    epoch=3,
                    members=(LOCAL_BARE, PEER_BARE),
                    title="Recv newer epoch",
                )
                receiver.save_group_state(group_state)
                writer = _FakeWriter()
                root_secret = b"\x67" * 32

                await receiver._handle_incoming_group_blindbox_root_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT|group-root-recv-newer-epoch|4|2|"
                    + root_secret.hex(),
                    writer,
                    peer_id=PEER_BARE,
                )

                snapshot_bundle = receiver._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertEqual(snapshot.group_epoch, 4)
                self.assertEqual(snapshot.root_secret, root_secret)
                self.assertEqual(snapshot.root_epoch, 2)
                frame = await self._decode_frame(bytes(writer.buffer))
                self.assertEqual(
                    frame.payload.decode("utf-8"),
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-recv-newer-epoch|4|2",
                )

    async def test_group_blindbox_root_ack_from_nonmember_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = self._make_blindbox_core()
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                group_state = GroupState(
                    group_id="group-root-ignore-nonmember",
                    epoch=2,
                    members=(LOCAL_BARE, PEER_BARE),
                    title="Ignore",
                )
                core.save_group_state(group_state)
                writer = _FakeWriter()

                await core._send_group_blindbox_root_if_needed(
                    writer,
                    group_state.group_id,
                    peer_id=PEER_BARE,
                )
                core._handle_group_blindbox_root_ack_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-ignore-nonmember|2|1",
                    peer_id=OTHER_BARE,
                )

                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertIsNone(snapshot.root_secret)
                self.assertIsNotNone(snapshot.pending_root_secret)
                self.assertEqual(snapshot.pending_root_acked_members, set())

    async def test_group_blindbox_root_rotation_preserves_previous_root(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = self._make_blindbox_core()
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                group_state = GroupState(
                    group_id="group-root-rotate",
                    epoch=5,
                    members=(LOCAL_BARE, PEER_BARE),
                    title="Rotate",
                )
                core.save_group_state(group_state)
                core._save_group_blindbox_channel(
                    group_state.group_id,
                    GroupBlindBoxChannel(
                        channel_id=f"group:{group_state.group_id}",
                        group_epoch=5,
                        state=BlindBoxState(send_index=3),
                        root_secret_enc=core._group_blindbox_encrypt_root_secret(
                            b"\x77" * 32,
                            group_state.group_id,
                        ),
                        root_epoch=7,
                    ),
                )
                writer = _FakeWriter()

                await core._send_group_blindbox_root_if_needed(
                    writer,
                    group_state.group_id,
                    force_rotate=True,
                    peer_id=PEER_BARE,
                )
                core._handle_group_blindbox_root_ack_signal(
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-root-rotate|5|8",
                    peer_id=PEER_BARE,
                )

                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertEqual(snapshot.root_epoch, 8)
                self.assertTrue(snapshot.prev_roots)
                self.assertEqual(snapshot.prev_roots[0]["root_epoch"], 7)
                self.assertEqual(snapshot.prev_roots[0]["secret"], b"\x77" * 32)

    async def test_blindbox_root_receiver_for_explicit_peer_updates_that_peer_snapshot(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                receiver = self._make_blindbox_core()
                receiver.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                receiver._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                writer = _FakeWriter()
                root_secret = b"\x55" * 32
                attach_mock_live_session(
                    receiver,
                    PEER_BARE,
                    (_FakeReader(b""), writer),
                    handshake_complete=True,
                    use_encryption=False,
                )
                receiver.current_peer_addr = OTHER_BARE

                await receiver._handle_incoming_blindbox_root_signal(
                    f"__SIGNAL__:BLINDBOX_ROOT|1|{root_secret.hex()}",
                    writer,
                    peer_id=PEER_BARE,
                )

                snapshot = receiver._load_blindbox_peer_snapshot(PEER_BARE)
                self.assertEqual(snapshot.root_secret, root_secret)
                self.assertEqual(snapshot.root_epoch, 1)
                self.assertIsNone(receiver._blindbox_root_secret)

    async def test_blindbox_root_ack_for_explicit_peer_commits_that_peer_snapshot(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = self._make_blindbox_core()
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                writer = _FakeWriter()
                attach_mock_live_session(
                    core,
                    PEER_BARE,
                    (_FakeReader(b""), writer),
                    handshake_complete=True,
                    use_encryption=False,
                )
                core.current_peer_addr = OTHER_BARE

                await core._send_blindbox_root_if_needed(writer, peer_id=PEER_BARE)
                pending = core._load_blindbox_peer_snapshot(PEER_BARE)
                self.assertIsNone(pending.root_secret)
                self.assertIsNotNone(pending.pending_root_secret)
                self.assertEqual(pending.pending_root_epoch, 1)

                core._handle_blindbox_root_ack_signal(
                    "__SIGNAL__:BLINDBOX_ROOT_ACK|1", peer_id=PEER_BARE
                )

                committed = core._load_blindbox_peer_snapshot(PEER_BARE)
                self.assertIsNotNone(committed.root_secret)
                self.assertEqual(committed.root_epoch, 1)
                self.assertIsNone(committed.pending_root_secret)
                self.assertIsNone(core._blindbox_root_secret)

    def test_blindbox_root_candidates_for_snapshot_include_previous_roots(self) -> None:
        core = I2PChatCore(profile="alice")
        snapshot = _BlindBoxPeerSnapshot(
            peer_addr=PEER_BARE,
            peer_id=PEER_BARE,
            state=core._blindbox_state,
        )
        snapshot.root_secret = b"\x77" * 32
        snapshot.root_epoch = 5
        snapshot.prev_roots = [
            {
                "epoch": 4,
                "secret": b"\x66" * 32,
                "expires_at": 2**31,
            }
        ]

        candidates = core._blindbox_root_candidates_for_snapshot(snapshot)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["epoch"], 5)
        self.assertEqual(candidates[1]["epoch"], 4)

    async def test_tofu_without_callback_requires_explicit_policy(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(profile="default", on_error=errors.append)

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_BARE,
            b"\x22" * 32,
        )

        self.assertFalse(ok)
        self.assertNotIn(EXAMPLE_BARE, core.peer_trusted_signing_keys)
        self.assertTrue(any("I2PCHAT_TRUST_AUTO=1" in msg for msg in errors), errors)

    async def test_tofu_auto_pin_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"I2PCHAT_TRUST_AUTO": "1"}, clear=False):
            core = I2PChatCore(profile="default")
            ok = await core._pin_or_verify_peer_signing_key(
                EXAMPLE_BARE,
                b"\x33" * 32,
            )

        self.assertTrue(ok)
        self.assertIn(EXAMPLE_BARE, core.peer_trusted_signing_keys)

    def test_forget_pinned_peer_key_removes_normalized_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("i2pchat.core.i2p_chat_core.get_profiles_dir", return_value=tmpdir):
                core = I2PChatCore(profile="alice")
                core.peer_trusted_signing_keys[EXAMPLE_BARE] = "ab" * 32

                removed = core.forget_pinned_peer_key(EXAMPLE_HOST)

                self.assertTrue(removed)
                self.assertNotIn(EXAMPLE_BARE, core.peer_trusted_signing_keys)
                with open(core._trust_store_path(), "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), "{}")

    async def test_signing_key_mismatch_emits_explicit_rejection_message(self) -> None:
        errors: list[str] = []
        systems: list[str] = []
        core = I2PChatCore(profile="alice", on_error=errors.append, on_system=systems.append)
        core.peer_trusted_signing_keys[EXAMPLE_BARE] = "11" * 32

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_BARE,
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
        core.peer_trusted_signing_keys[EXAMPLE_BARE] = "11" * 32

        ok = await core._pin_or_verify_peer_signing_key(
            EXAMPLE_BARE,
            b"\x22" * 32,
        )

        self.assertTrue(ok)
        self.assertEqual(core.peer_trusted_signing_keys[EXAMPLE_BARE], "22" * 32)
        self.assertEqual(len(decisions), 1)
        self.assertTrue(any("Updated trusted signing key" in msg for msg in systems))


if __name__ == "__main__":
    unittest.main()
