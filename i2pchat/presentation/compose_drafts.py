"""
Pure logic for per-peer compose draft switches (GitHub issue #6).

Qt layer in i2pchat/gui/main_qt.py applies the returned plain text to the compose QTextEdit and persists drafts.
"""

from __future__ import annotations

from typing import Optional


def apply_compose_draft_peer_switch(
    *,
    old_active_key: Optional[str],
    new_key: Optional[str],
    input_plain: str,
    drafts: dict[str, str],
) -> tuple[Optional[str], str, dict[str, str]]:
    """
    Compute state after switching the active peer key for the compose field.

    Returns ``(new_active_key, new_plain_text, updated_drafts)``.
    If ``new_key == old_active_key``, returns ``(old_active_key, input_plain, drafts)``
    without copying ``drafts``.
    """
    if new_key == old_active_key:
        return old_active_key, input_plain, drafts
    out = dict(drafts)
    orphan = ""
    if old_active_key is None and new_key is not None:
        orphan = input_plain
    if old_active_key is not None:
        out[old_active_key] = input_plain
    if new_key is None:
        text = ""
    else:
        text = out.get(new_key, "")
        if not text.strip() and orphan:
            text = orphan
    return new_key, text, out
