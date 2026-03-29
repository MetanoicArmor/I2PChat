from unread_counters import (
    bump_unread_if_inactive,
    clear_unread_for_peer,
    total_unread,
)


def test_bump_skips_when_no_msg_peer_key() -> None:
    c: dict[str, int] = {}
    bump_unread_if_inactive(c, active_key=None, msg_peer_key=None)
    assert c == {}


def test_bump_skips_when_same_as_active() -> None:
    c: dict[str, int] = {}
    bump_unread_if_inactive(c, active_key="a.b32.i2p", msg_peer_key="a.b32.i2p")
    assert c == {}


def test_bumps_inactive_peer() -> None:
    c: dict[str, int] = {}
    bump_unread_if_inactive(c, active_key="a.b32.i2p", msg_peer_key="b.b32.i2p")
    assert c == {"b.b32.i2p": 1}
    bump_unread_if_inactive(c, active_key="a.b32.i2p", msg_peer_key="b.b32.i2p")
    assert c == {"b.b32.i2p": 2}


def test_clear_removes_key() -> None:
    c = {"x.b32.i2p": 2, "y.b32.i2p": 1}
    clear_unread_for_peer(c, "x.b32.i2p")
    assert c == {"y.b32.i2p": 1}
    clear_unread_for_peer(c, None)
    assert c == {"y.b32.i2p": 1}


def test_total_unread() -> None:
    assert total_unread({}) == 0
    assert total_unread({"a": 2, "b": 3}) == 5
