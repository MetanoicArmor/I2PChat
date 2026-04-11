"""Unit tests for multi-peer live session helpers (no I2P network)."""

import os
import unittest

from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.core.live_peer_session import LivePeerSession, max_concurrent_live_sessions

from tests.test_asyncio_regression import _FakeReader, _FakeWriter


class MultiLiveSessionsEnvTests(unittest.TestCase):
    def test_max_concurrent_live_sessions_clamps(self) -> None:
        old = os.environ.get("I2PCHAT_MAX_LIVE_SESSIONS")
        try:
            os.environ["I2PCHAT_MAX_LIVE_SESSIONS"] = "200"
            self.assertEqual(max_concurrent_live_sessions(), 64)
            os.environ["I2PCHAT_MAX_LIVE_SESSIONS"] = "0"
            self.assertEqual(max_concurrent_live_sessions(), 1)
            os.environ["I2PCHAT_MAX_LIVE_SESSIONS"] = "3"
            self.assertEqual(max_concurrent_live_sessions(), 3)
        finally:
            if old is None:
                os.environ.pop("I2PCHAT_MAX_LIVE_SESSIONS", None)
            else:
                os.environ["I2PCHAT_MAX_LIVE_SESSIONS"] = old


class LivePeerSessionDataclassTests(unittest.TestCase):
    def test_reset_crypto_clears_seq_and_acks(self) -> None:
        s = LivePeerSession(peer_id="testpeer.b32.i2p")
        s.shared_key = b"\x00" * 32
        s._send_seq = 5
        s._recv_seq = 5
        s._pending_text_acks[1] = object()
        s.reset_crypto()
        self.assertIsNone(s.shared_key)
        self.assertEqual(s._send_seq, 0)
        self.assertEqual(s._recv_seq, 0)
        self.assertEqual(len(s._pending_text_acks), 0)


class LiveStreamCountTests(unittest.TestCase):
    def test_live_stream_count_legacy_plus_extra(self) -> None:
        core = I2PChatCore(profile="alice")
        peer1 = "cccccccccccccccccccccccccccccccccccccccc.b32.i2p"
        peer2 = "dddddddddddddddddddddddddddddddddddddddd.b32.i2p"
        k1 = core._normalize_peer_addr(peer1)
        k2 = core._normalize_peer_addr(peer2)
        ls1 = LivePeerSession(peer_id=k1)
        ls1.conn = (_FakeReader(b""), _FakeWriter())
        ls2 = LivePeerSession(peer_id=k2)
        ls2.conn = (_FakeReader(b""), _FakeWriter())
        core._live_sessions[k1] = ls1
        core._live_sessions[k2] = ls2
        self.assertEqual(core.live_stream_count(), 2)

    def test_legacy_peer_stays_routable_when_current_peer_is_another(self) -> None:
        """Поле current_peer_addr (UI) не должно скрывать активный поток другого пира."""
        core = I2PChatCore(profile="alice")
        peer_a = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"
        peer_b = "dddddddddddddddddddddddddddddddddddddddd.b32.i2p"
        norm_a = core._normalize_peer_addr(peer_a)
        norm_b = core._normalize_peer_addr(peer_b)
        ls_a = LivePeerSession(peer_id=norm_a)
        ls_a.conn = (_FakeReader(b""), _FakeWriter())
        core._live_sessions[norm_a] = ls_a
        ls_b = LivePeerSession(peer_id=norm_b)
        ls_b.conn = (_FakeReader(b""), _FakeWriter())
        core._live_sessions[norm_b] = ls_b
        core.current_peer_addr = norm_b
        self.assertTrue(core._has_active_session_for_peer(norm_a))
        w, fpid, _ = core._writer_frame_peer_and_text_acks(norm_a)
        self.assertIsNotNone(w)
        self.assertEqual(fpid, norm_a)
