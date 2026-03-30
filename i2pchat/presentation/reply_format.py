"""
Build a quoted reply block for the compose field (issue: message context actions).
"""

from __future__ import annotations


def format_reply_quote(sender: str, text: str) -> str:
    """Return markdown-style quoted lines with a short header; ends with blank line for typing."""
    who = (sender or "").strip() or "message"
    body = (text or "").rstrip("\n")
    lines = body.split("\n")
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return f"@{who} wrote:\n{quoted}\n\n"
