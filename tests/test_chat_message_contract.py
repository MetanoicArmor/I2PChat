"""Contract tests for ChatMessage (e.g. source_peer for unread / notifications)."""

from __future__ import annotations

from datetime import datetime, timezone

from i2p_chat_core import ChatMessage


def test_chat_message_source_peer_defaults_to_none() -> None:
    ts = datetime.now(timezone.utc)
    m = ChatMessage(kind="peer", text="hi", timestamp=ts)
    assert m.source_peer is None


def test_chat_message_source_peer_round_trip() -> None:
    ts = datetime.now(timezone.utc)
    addr = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
    m = ChatMessage(kind="peer", text="x", timestamp=ts, source_peer=addr)
    assert m.source_peer == addr
