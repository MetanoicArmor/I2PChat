"""Protocol v4 handshake hardening tests.

Covers:
- Directional session subkeys (i2r / r2i) derivation.
- FINISHED key-confirmation compute/verify.
- X25519 shared-secret input hardening.
- Reflection protection at the receive layer (a frame reflected back to its
  sender must fail the MAC because send/recv keys differ).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import sys
import types
import unittest

from i2pchat import crypto

if "PIL" not in sys.modules:  # pragma: no cover - test shim
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat.core.i2p_chat_core import I2PChatCore
from tests.live_session_helpers import attach_mock_live_session

PEER = "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk.b32.i2p"


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
        data = bytes(self._buf)
        self._buf.clear()
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


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class HandshakeCryptoV4Tests(unittest.TestCase):
    def test_directional_subkeys_are_four_distinct_keys(self) -> None:
        dh = secrets.token_bytes(32)
        ni = crypto.generate_nonce()
        nr = crypto.generate_nonce()
        keys = crypto.derive_handshake_subkeys(dh, ni, nr)
        self.assertEqual(len(keys), 4)
        for k in keys:
            self.assertEqual(len(k), 32)
        self.assertEqual(len(set(keys)), 4, "directional subkeys must be distinct")

    def test_subkeys_are_deterministic_for_both_sides(self) -> None:
        dh = secrets.token_bytes(32)
        ni = crypto.generate_nonce()
        nr = crypto.generate_nonce()
        # Both peers derive the same 4-tuple from the same DH + nonces; the
        # initiator's send pair equals the responder's recv pair (i2r), etc.
        self.assertEqual(
            crypto.derive_handshake_subkeys(dh, ni, nr),
            crypto.derive_handshake_subkeys(dh, ni, nr),
        )

    def test_finished_confirmation_roundtrip(self) -> None:
        mac_key = secrets.token_bytes(32)
        transcript = crypto.compute_handshake_transcript_hash(b"RESP|payload")
        tag = crypto.compute_handshake_finished(mac_key, transcript)
        self.assertTrue(crypto.verify_handshake_finished(mac_key, transcript, tag))
        # Wrong key or tampered transcript must fail.
        self.assertFalse(
            crypto.verify_handshake_finished(secrets.token_bytes(32), transcript, tag)
        )
        other = crypto.compute_handshake_transcript_hash(b"RESP|other")
        self.assertFalse(crypto.verify_handshake_finished(mac_key, other, tag))

    def test_dh_shared_secret_roundtrip_and_hardening(self) -> None:
        a_priv, a_pub = crypto.generate_ephemeral_keypair()
        b_priv, b_pub = crypto.generate_ephemeral_keypair()
        self.assertEqual(
            crypto.compute_dh_shared_secret(a_priv, b_pub),
            crypto.compute_dh_shared_secret(b_priv, a_pub),
        )
        with self.assertRaises(ValueError):
            crypto.compute_dh_shared_secret(a_priv, bytes(32))  # all-zero point
        with self.assertRaises(ValueError):
            crypto.compute_dh_shared_secret(a_priv, b"short")  # wrong length

    def test_compute_shared_key_removed(self) -> None:
        self.assertFalse(hasattr(crypto, "compute_shared_key"))


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class ReflectionProtectionTests(unittest.IsolatedAsyncioTestCase):
    def _core(self):
        errors: list[str] = []
        messages: list[str] = []
        core = I2PChatCore(
            on_error=errors.append,
            on_message=lambda m: messages.append(m.text),
        )
        core._reset_crypto_state = lambda: None  # type: ignore[assignment]
        core._start_handshake_watchdog = lambda *_a, **_k: None  # type: ignore[assignment]
        return core, errors, messages

    def _attach(self, core, send_enc, send_mac, recv_enc, recv_mac):
        writer = _Writer()
        k = attach_mock_live_session(
            core,
            PEER,
            (None, writer),
            handshake_complete=True,
            use_encryption=True,
            shared_key=send_enc,
        )
        ls = core._live_sessions[k]
        ls.send_key, ls.send_mac_key = send_enc, send_mac
        ls.recv_key, ls.recv_mac_key = recv_enc, recv_mac
        ls.shared_mac_key = None
        return k, ls, writer

    async def test_reflected_frame_fails_mac(self) -> None:
        core, errors, messages = self._core()
        i2r_enc, i2r_mac = secrets.token_bytes(32), secrets.token_bytes(32)
        r2i_enc, r2i_mac = secrets.token_bytes(32), secrets.token_bytes(32)
        # Session for the sender side: send with i2r, receive with r2i.
        k, ls, _ = self._attach(core, i2r_enc, i2r_mac, r2i_enc, r2i_mac)
        frame = core.frame_message("U", "reflect-me", peer_id=k)
        # Reflect the sender's own frame back at it.
        conn = (_Reader(frame), _Writer())
        ls.conn = conn
        await core.receive_loop(conn, peer_id=k)
        self.assertNotIn("reflect-me", messages)
        self.assertTrue(
            any("integrity" in e.lower() for e in errors), errors
        )

    async def test_matching_direction_delivers(self) -> None:
        # Control: when recv keys match the send keys used to build the frame
        # (symmetric mock), the very same frame decrypts and is delivered.
        core, errors, messages = self._core()
        enc, mac = secrets.token_bytes(32), secrets.token_bytes(32)
        k, ls, _ = self._attach(core, enc, mac, enc, mac)
        frame = core.frame_message("U", "hello-loop", peer_id=k)
        conn = (_Reader(frame), _Writer())
        ls.conn = conn
        await core.receive_loop(conn, peer_id=k)
        self.assertIn("hello-loop", messages)


class _Dest:
    def __init__(self, b32: str) -> None:
        self.base32 = b32.split(".")[0]


class _PipeWriter:
    def __init__(self, peer_queue: asyncio.Queue) -> None:
        self.q = peer_queue

    def write(self, data: bytes) -> None:
        self.q.put_nowait(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False

    def get_extra_info(self, name: str, default=None):
        return default


class _PipeReader:
    def __init__(self, q: asyncio.Queue) -> None:
        self.q = q
        self.buf = bytearray()

    async def readexactly(self, n: int) -> bytes:
        while len(self.buf) < n:
            self.buf.extend(await self.q.get())
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data

    async def read(self, n: int = -1) -> bytes:
        if not self.buf:
            self.buf.extend(await self.q.get())
        if n < 0:
            data = bytes(self.buf)
            self.buf.clear()
            return data
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class SecureLiveDesyncTests(unittest.TestCase):
    """Ready + receive must imply Send (LivePeerSession beats stale session_manager)."""

    def test_secure_live_heals_session_manager_desync(self) -> None:
        from i2pchat.core.live_peer_session import LivePeerSession
        from i2pchat.core.session_manager import PeerState

        peer = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        core = I2PChatCore()
        ls = LivePeerSession(peer_id=peer)
        ls.conn = (None, _Writer())
        ls.handshake_complete = True
        ls.use_encryption = True
        ls.shared_key = secrets.token_bytes(32)
        core._live_sessions[peer] = ls
        core.current_peer_addr = peer
        # Desync: manager still thinks handshake is incomplete.
        core.session_manager.ensure_peer_transport(peer)
        pt = core.session_manager.peer_transport[peer]
        pt.connected = True
        pt.handshake_complete = False
        pt.peer_state = PeerState.HANDSHAKING

        self.assertFalse(core.session_manager.is_live_path_alive(peer_id=peer))
        self.assertTrue(core.is_secure_live_for_peer(peer))
        d = core.get_delivery_telemetry()
        self.assertTrue(d["secure_live"], d)
        self.assertEqual(d["state"], "online-live")
        # Heal persisted into session_manager.
        self.assertTrue(core.session_manager.is_live_path_alive(peer_id=peer))

    def test_resolve_secure_live_peer_overrides_stale_preferred(self) -> None:
        from i2pchat.core.live_peer_session import LivePeerSession

        live = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        stale = "cccccccccccccccccccccccccccccccccccccccc"
        core = I2PChatCore()
        ls = LivePeerSession(peer_id=live)
        ls.conn = (None, _Writer())
        ls.handshake_complete = True
        ls.use_encryption = True
        ls.shared_key = secrets.token_bytes(32)
        core._live_sessions[live] = ls
        core.current_peer_addr = live
        self.assertEqual(core.resolve_secure_live_peer(stale), live)
        self.assertEqual(core.resolve_secure_live_peer(""), live)

    def test_session_manager_ghost_cannot_hijack_resolve(self) -> None:
        """Acceptor bug: last_active marked secure in session_manager, inbound live is other peer."""
        from i2pchat.core.live_peer_session import LivePeerSession

        live = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        ghost = "cccccccccccccccccccccccccccccccccccccccc"
        core = I2PChatCore()
        ls = LivePeerSession(peer_id=live)
        ls.conn = (None, _Writer())
        ls.handshake_complete = True
        ls.use_encryption = True
        ls.shared_key = secrets.token_bytes(32)
        core._live_sessions[live] = ls
        core.current_peer_addr = live
        # Ghost preferred: session_manager says secure, but no LivePeerSession writer.
        core.session_manager.set_peer_handshake_complete(ghost)
        self.assertTrue(core.session_manager.is_live_path_alive(peer_id=ghost))
        self.assertFalse(core.is_secure_live_for_peer(ghost))
        self.assertEqual(core.resolve_secure_live_peer(ghost), live)
        d = core.get_delivery_telemetry()
        self.assertTrue(d["secure_live"], d)
        self.assertEqual(d["state"], "online-live")

    def test_half_open_preferred_slot_remaps_telemetry(self) -> None:
        from i2pchat.core.live_peer_session import LivePeerSession

        live = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        half = "dddddddddddddddddddddddddddddddddddddddd"
        core = I2PChatCore()
        ls_live = LivePeerSession(peer_id=live)
        ls_live.conn = (None, _Writer())
        ls_live.handshake_complete = True
        ls_live.use_encryption = True
        ls_live.shared_key = secrets.token_bytes(32)
        ls_half = LivePeerSession(peer_id=half)
        ls_half.conn = (None, _Writer())
        ls_half.handshake_complete = False
        core._live_sessions[live] = ls_live
        core._live_sessions[half] = ls_half
        core.current_peer_addr = half
        d = core.get_delivery_telemetry()
        self.assertTrue(d["secure_live"], d)
        self.assertTrue(core.resolve_secure_live_peer(half) == live)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class HandshakeE2EV4Tests(unittest.IsolatedAsyncioTestCase):
    ALICE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
    BOB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"

    def _make_core(self, me: str, peer: str, out_q, in_q, events, *, tofu_cb=None):
        from i2pchat.core.live_peer_session import LivePeerSession

        core = I2PChatCore(
            on_error=lambda e: events.append(("ERR", e)),
            on_message=lambda m: events.append(("MSG", getattr(m, "text", m))),
            on_system=lambda s: events.append(("SYS", s)),
        )
        core.my_dest = _Dest(me)
        seed, pub = crypto.generate_signing_keypair()
        core.my_signing_seed = seed
        core.my_signing_public = pub
        if tofu_cb is not None:
            core.on_trust_decision = tofu_cb
        else:
            core.on_trust_decision = None
            core._trust_auto = True

        async def _noop(*_a, **_k):
            return None

        core._send_blindbox_root_if_needed = _noop  # type: ignore[method-assign]
        core._notify_group_mesh_manager = lambda: None  # type: ignore[method-assign]
        core._schedule_group_pending_flush = lambda *_a, **_k: None  # type: ignore[method-assign]
        core._trigger_blindbox_hot_poll = lambda *_a, **_k: None  # type: ignore[method-assign]
        k = core._normalize_peer_addr(peer)
        ls = LivePeerSession(peer_id=k)
        ls.conn = (_PipeReader(in_q), _PipeWriter(out_q))
        core._live_sessions[k] = ls
        core.current_peer_addr = k
        return core, k

    async def test_full_handshake_pinned_keys(self) -> None:
        events: list = []
        a2b: asyncio.Queue = asyncio.Queue()
        b2a: asyncio.Queue = asyncio.Queue()
        alice, ak = self._make_core(self.ALICE, self.BOB, a2b, b2a, events)
        bob, bk = self._make_core(self.BOB, self.ALICE, b2a, a2b, events)
        alice.peer_trusted_signing_keys[alice._normalize_peer_addr(self.BOB)] = (
            bob.my_signing_public.hex()
        )
        bob.peer_trusted_signing_keys[bob._normalize_peer_addr(self.ALICE)] = (
            alice.my_signing_public.hex()
        )
        ta = asyncio.create_task(
            alice.receive_loop(alice._live_sessions[ak].conn, peer_id=ak)
        )
        tb = asyncio.create_task(
            bob.receive_loop(bob._live_sessions[bk].conn, peer_id=bk)
        )
        try:
            self.assertTrue(await alice.initiate_secure_handshake(ak))
            for _ in range(40):
                await asyncio.sleep(0.025)
                if (
                    alice._live_sessions[ak].handshake_complete
                    and bob._live_sessions[bk].handshake_complete
                ):
                    break
            self.assertTrue(alice._live_sessions[ak].handshake_complete)
            self.assertTrue(bob._live_sessions[bk].handshake_complete)
            self.assertTrue(alice._live_sessions[ak].use_encryption)
            self.assertTrue(bob._live_sessions[bk].use_encryption)
            # Acceptor (bob) must be able to reply live even if UI preferred is stale.
            ghost = "cccccccccccccccccccccccccccccccccccccccc"
            bob_send = await bob.send_text("reply-from-acceptor", peer_address=ghost)
            self.assertTrue(bob_send.accepted, bob_send)
            self.assertEqual(bob_send.route, "online-live")
            alice_send = await alice.send_text("hello-from-initiator", peer_address=ghost)
            self.assertTrue(alice_send.accepted, alice_send)
            self.assertEqual(alice_send.route, "online-live")
            await asyncio.sleep(0.05)
            texts = [e[1] for e in events if e[0] == "MSG" and isinstance(e[1], str)]
            self.assertIn("reply-from-acceptor", texts)
            self.assertIn("hello-from-initiator", texts)
        finally:
            ta.cancel()
            tb.cancel()
            for t in (ta, tb):
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    async def test_deferred_tofu_does_not_block_resp(self) -> None:
        """First-contact TOFU must not stall RESP (initiator hang fix)."""
        events: list = []
        tofu_calls: list[str] = []
        a2b: asyncio.Queue = asyncio.Queue()
        b2a: asyncio.Queue = asyncio.Queue()

        async def tofu_ok(peer_addr, fingerprint, signing_key_hex):
            tofu_calls.append(peer_addr)
            return True

        alice, ak = self._make_core(
            self.ALICE, self.BOB, a2b, b2a, events, tofu_cb=tofu_ok
        )
        bob, bk = self._make_core(
            self.BOB, self.ALICE, b2a, a2b, events, tofu_cb=tofu_ok
        )
        # Pre-pin only alice→bob so bob (responder) hits deferred first-contact TOFU.
        alice.peer_trusted_signing_keys[alice._normalize_peer_addr(self.BOB)] = (
            bob.my_signing_public.hex()
        )

        ta = asyncio.create_task(
            alice.receive_loop(alice._live_sessions[ak].conn, peer_id=ak)
        )
        tb = asyncio.create_task(
            bob.receive_loop(bob._live_sessions[bk].conn, peer_id=bk)
        )
        try:
            self.assertTrue(await alice.initiate_secure_handshake(ak))
            for _ in range(60):
                await asyncio.sleep(0.025)
                if (
                    alice._live_sessions[ak].handshake_complete
                    and bob._live_sessions[bk].handshake_complete
                ):
                    break
            self.assertTrue(
                alice._live_sessions[ak].handshake_complete,
                "initiator stuck — RESP was likely blocked by TOFU",
            )
            self.assertTrue(bob._live_sessions[bk].handshake_complete)
            self.assertTrue(tofu_calls, "deferred TOFU should still run at finalize")
        finally:
            ta.cancel()
            tb.cancel()
            for t in (ta, tb):
                with contextlib.suppress(asyncio.CancelledError):
                    await t


if __name__ == "__main__":
    unittest.main()
