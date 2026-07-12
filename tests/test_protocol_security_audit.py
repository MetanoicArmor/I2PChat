"""
Protocol security-audit regression tests.

Covers two hardening fixes:

1. Group message sender authentication: for 1:1-delivered ("recipient" scope)
   group transport, the self-declared ``sender_id`` must match the
   cryptographically authenticated transport peer (``source_peer``). A connected
   member must not be able to spoof another member's identity.

2. Pre-handshake control-signal rejection: unauthenticated ``__SIGNAL__`` control
   frames that arrive in plaintext before the secure channel is established are
   ignored (except a graceful QUIT).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest

# Stub out PIL if not installed (mirrors the other protocol tests).
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.groups import (
    GroupContentType,
    GroupEnvelope,
    GroupImportStatus,
    GroupRecipientDeliveryMetadata,
    GroupState,
)
from i2pchat.groups.wire import (
    encode_group_transport_text,
    encode_group_transport_text_v2,
    group_blindbox_signature_payload,
)

from tests.live_session_helpers import attach_mock_live_session

ALICE_BARE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB_BARE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAROL_BARE = "cccccccccccccccccccccccccccccccccccccccc"

TEST_PEER_B32 = "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk.b32.i2p"


class _DummyDest:
    def __init__(self, base32: str) -> None:
        self.base32 = base32


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


def _make_group_wire(sender_id: str, recipient_id: str, *, group_id: str) -> str:
    state = GroupState(
        group_id=group_id,
        epoch=1,
        members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
        title="Audit group",
    )
    envelope = GroupEnvelope(
        group_id=group_id,
        epoch=1,
        msg_id=f"msg-from-{sender_id[:6]}",
        sender_id=sender_id,
        group_seq=1,
        content_type=GroupContentType.GROUP_TEXT,
        payload="hello group",
    )
    metadata = GroupRecipientDeliveryMetadata(
        recipient_id=recipient_id,
        delivery_id=f"{envelope.msg_id}:{recipient_id[:6]}",
    )
    return encode_group_transport_text(state, envelope, metadata)


def _make_signed_group_blindbox_wire(
    sender_id: str,
    signing_seed: bytes,
    signing_public: bytes,
    *,
    group_id: str,
) -> str:
    state = GroupState(
        group_id=group_id,
        epoch=1,
        members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
        title="Audit group",
    )
    envelope = GroupEnvelope(
        group_id=group_id,
        epoch=1,
        msg_id=f"offline-from-{sender_id[:6]}",
        sender_id=sender_id,
        group_seq=1,
        content_type=GroupContentType.GROUP_TEXT,
        payload="offline hello",
    )
    signature = crypto.sign_data(
        signing_seed,
        group_blindbox_signature_payload(state, envelope, signing_public),
    )
    return encode_group_transport_text_v2(
        state,
        envelope,
        signer_key=signing_public,
        signature=signature,
    )


class GroupSenderAuthenticationTests(unittest.TestCase):
    def _make_core(self, tmpdir: str) -> I2PChatCore:
        core = I2PChatCore(profile="alice")
        core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
        core.my_dest = _DummyDest(ALICE_BARE)
        core.create_group(
            title="Audit group",
            members=[BOB_BARE, CAROL_BARE],
            group_id="audit-group-1",
            epoch=1,
        )
        return core

    def test_spoofed_sender_over_authenticated_peer_is_rejected(self) -> None:
        """Bob's channel claiming a message from Carol must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            wire = _make_group_wire(
                CAROL_BARE, ALICE_BARE, group_id="audit-group-1"
            )
            result = core.import_group_transport(wire, source_peer=BOB_BARE)
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.INVALID)
            self.assertIn("authenticated peer", (result.error or "").lower())
            # Nothing must be written to group history.
            self.assertEqual(core.load_group_history("audit-group-1"), [])

    def test_matching_sender_and_authenticated_peer_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            wire = _make_group_wire(
                BOB_BARE, ALICE_BARE, group_id="audit-group-1"
            )
            result = core.import_group_transport(wire, source_peer=BOB_BARE)
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.IMPORTED)
            history = core.load_group_history("audit-group-1")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].sender_id, BOB_BARE)

    def test_sender_authentication_handles_b32_suffix(self) -> None:
        """A ``.b32.i2p`` source_peer must still match a bare sender id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            wire = _make_group_wire(
                BOB_BARE, ALICE_BARE, group_id="audit-group-1"
            )
            result = core.import_group_transport(
                wire, source_peer=f"{BOB_BARE}.b32.i2p"
            )
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.IMPORTED)

    def test_group_blindbox_rejects_member_spoofing_another_sender(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            bob_seed, bob_public = crypto.generate_signing_keypair()
            _carol_seed, carol_public = crypto.generate_signing_keypair()
            core.peer_trusted_signing_keys[CAROL_BARE] = carol_public.hex()
            wire = _make_signed_group_blindbox_wire(
                CAROL_BARE,
                bob_seed,
                bob_public,
                group_id="audit-group-1",
            )
            result = core.import_group_transport(wire)
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.INVALID)
            self.assertIn("pinned sender key", (result.error or "").lower())
            self.assertEqual(core.load_group_history("audit-group-1"), [])

    def test_group_blindbox_accepts_signature_from_pinned_sender(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            bob_seed, bob_public = crypto.generate_signing_keypair()
            core.peer_trusted_signing_keys[BOB_BARE] = bob_public.hex()
            wire = _make_signed_group_blindbox_wire(
                BOB_BARE,
                bob_seed,
                bob_public,
                group_id="audit-group-1",
            )
            result = core.import_group_transport(wire)
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.IMPORTED)

    def test_group_blindbox_rejects_legacy_unsigned_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = self._make_core(tmpdir)
            bob_seed, bob_public = crypto.generate_signing_keypair()
            core.peer_trusted_signing_keys[BOB_BARE] = bob_public.hex()
            wire = _make_signed_group_blindbox_wire(
                BOB_BARE,
                bob_seed,
                bob_public,
                group_id="audit-group-1",
            ).replace('"version":3', '"version":2')
            result = core.import_group_transport(wire)
            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.INVALID)
            self.assertIn("unsigned", (result.error or "").lower())


class PreHandshakeSignalRejectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import i2pchat.core.i2p_chat_core as core_module

        self._core_module = core_module
        self._originals = _patch_crypto(core_module)

    def tearDown(self):
        _restore_crypto(self._core_module, self._originals)

    def _make_core(self, **kwargs) -> I2PChatCore:
        core = I2PChatCore(**kwargs)
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]
        return core

    async def test_plaintext_control_signal_before_handshake_is_ignored(self) -> None:
        core = self._make_core(on_error=lambda _m: None)
        # Build a plaintext ABORT_FILE control signal (no secure channel yet).
        payload = core.frame_message_plain("S", "__SIGNAL__:ABORT_FILE")
        conn = (_Reader(payload), _Writer())
        k = attach_mock_live_session(
            core,
            TEST_PEER_B32,
            conn,
            handshake_complete=False,
            use_encryption=False,
        )
        ls = core._live_sessions[k]
        await core.receive_loop(conn, peer_id=k)
        # The unauthenticated signal must not mutate transfer state.
        self.assertFalse(ls._transfer_aborted_by_peer)

    async def test_encrypted_control_signal_after_handshake_is_processed(self) -> None:
        core = self._make_core(on_error=lambda _m: None)
        conn = (None, _Writer())
        k = attach_mock_live_session(
            core,
            TEST_PEER_B32,
            conn,
            handshake_complete=True,
            use_encryption=True,
            shared_key=b"x" * 32,
        )
        ls = core._live_sessions[k]
        # Encrypted signal frame is produced on the same session (seq starts at 0).
        payload = core.frame_message("S", "__SIGNAL__:ABORT_FILE", peer_id=k)
        conn = (_Reader(payload), conn[1])
        ls.conn = conn
        await core.receive_loop(conn, peer_id=k)
        self.assertTrue(ls._transfer_aborted_by_peer)

    async def test_plaintext_quit_before_handshake_is_honored(self) -> None:
        """A graceful QUIT remains valid as a pre-handshake plaintext signal."""
        systems: list[str] = []
        core = self._make_core(on_system=systems.append, on_error=lambda _m: None)
        payload = core.frame_message_plain("S", "__SIGNAL__:QUIT")
        conn = (_Reader(payload), _Writer())
        k = attach_mock_live_session(
            core,
            TEST_PEER_B32,
            conn,
            handshake_complete=False,
            use_encryption=False,
        )
        await core.receive_loop(conn, peer_id=k)
        self.assertTrue(
            any("disconnect" in s.lower() for s in systems),
            systems,
        )


if __name__ == "__main__":
    unittest.main()
