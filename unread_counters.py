"""
Pure logic for per-peer unread counts (GitHub backlog: unread indicators).

UI normalizes peer keys with chat_history.normalize_peer_addr before calling these helpers.
"""

from __future__ import annotations

from typing import Optional


def bump_unread_if_inactive(
    counts: dict[str, int],
    *,
    active_key: Optional[str],
    msg_peer_key: Optional[str],
) -> None:
    """Increment unread for msg_peer_key when it differs from the active conversation key."""
    if not msg_peer_key:
        return
    if msg_peer_key == active_key:
        return
    counts[msg_peer_key] = counts.get(msg_peer_key, 0) + 1


def clear_unread_for_peer(counts: dict[str, int], peer_key: Optional[str]) -> None:
    """Drop unread state for a peer when the user opens that conversation."""
    if peer_key is None:
        return
    counts.pop(peer_key, None)


def total_unread(counts: dict[str, int]) -> int:
    return sum(counts.values())
