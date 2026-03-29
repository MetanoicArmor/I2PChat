from reply_format import format_reply_quote


def test_format_reply_quote_multiline() -> None:
    s = format_reply_quote("Peer", "line1\nline2")
    assert "@Peer wrote:" in s
    assert "> line1" in s
    assert "> line2" in s
    assert s.endswith("\n\n")


def test_format_reply_empty_sender_uses_fallback() -> None:
    s = format_reply_quote("", "hi")
    assert "@message wrote:" in s
    assert "> hi" in s


def test_format_reply_strips_trailing_newlines_from_body() -> None:
    s = format_reply_quote("Me", "a\n\n")
    assert s.endswith("\n\n")
    assert "> a" in s
    assert "> " not in s.split("> a")[1]  # no extra blank quoted lines from trailing \n
