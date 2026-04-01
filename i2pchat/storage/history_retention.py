"""
History retention policy enforcement for I2PChat.

Provides configurable pruning of chat history by age and/or message count.
All destructive operations require confirmed=True to prevent accidental data loss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from i2pchat.storage.chat_history import (
    DEFAULT_MAX_MESSAGES,
    HistoryEntry,
    load_history,
    save_history,
)

logger = logging.getLogger("i2pchat.history_retention")

# Key used in gui.json to store retention settings
GUI_RETENTION_KEY = "history_retention"


@dataclass
class RetentionPolicy:
    """
    Policy controlling how much history to keep.

    Attributes:
        max_age_days: Remove messages older than this many days. None = no age limit.
        max_messages: Keep at most this many messages per peer. None = no count limit.
        per_peer: When True (default), limits apply per peer conversation.
    """
    max_age_days: Optional[int] = None
    max_messages: Optional[int] = DEFAULT_MAX_MESSAGES
    per_peer: bool = True

    def __post_init__(self) -> None:
        if self.max_age_days is not None and self.max_age_days <= 0:
            raise ValueError("max_age_days must be a positive integer")
        if self.max_messages is not None and self.max_messages <= 0:
            raise ValueError("max_messages must be a positive integer")


def apply_retention(
    entries: List[HistoryEntry],
    policy: RetentionPolicy,
) -> List[HistoryEntry]:
    """
    Apply retention policy to a list of history entries.

    Rules:
    - System messages (kind="system") are always preserved.
    - Oldest messages are removed first when count limit is exceeded.
    - Messages older than max_age_days (by ts field) are removed.
    - Both limits are applied; the stricter one wins.

    Args:
        entries: List of HistoryEntry objects (assumed chronological order).
        policy: RetentionPolicy to apply.

    Returns:
        Pruned list of HistoryEntry objects.
    """
    if not entries:
        return []

    result = list(entries)

    # Age-based pruning
    if policy.max_age_days is not None:
        cutoff = _age_cutoff(policy.max_age_days)
        result = [
            e for e in result
            if e.kind == "system" or _entry_ts(e) >= cutoff
        ]

    # Count-based pruning: keep most recent max_messages, preserving system messages
    if policy.max_messages is not None and len(result) > policy.max_messages:
        result = _prune_by_count(result, policy.max_messages)

    return result


def enforce_retention_for_peer(
    profile_name: str,
    identity_key: bytes,
    peer_addr: str,
    policy: RetentionPolicy,
    profile_data_dir: str,
    *,
    app_data_root: Optional[str] = None,
    confirmed: bool = False,
) -> int:
    """
    Load, prune, and save history for a single peer.

    Args:
        profile_name: Profile name.
        identity_key: 32-byte identity key.
        peer_addr: Peer address to apply retention to.
        policy: RetentionPolicy to enforce.
        profile_data_dir: ``profiles/<name>/`` directory for this profile.
        app_data_root: Optional application root for legacy flat history files.
        confirmed: Must be True to actually write the pruned history.

    Returns:
        Number of entries removed (0 if confirmed=False).

    Raises:
        RuntimeError: If confirmed=False (destructive operation not confirmed).
    """
    if not confirmed:
        raise RuntimeError(
            "enforce_retention_for_peer is a destructive operation — "
            "pass confirmed=True to proceed"
        )

    entries = load_history(
        profile_data_dir,
        profile_name,
        peer_addr,
        identity_key,
        app_data_root=app_data_root,
    )
    if not entries:
        return 0

    pruned = apply_retention(entries, policy)
    removed = len(entries) - len(pruned)

    if removed > 0:
        save_history(
            profile_data_dir,
            profile_name,
            peer_addr,
            pruned,
            identity_key,
            max_messages=policy.max_messages or DEFAULT_MAX_MESSAGES,
            app_data_root=app_data_root,
        )
        logger.info(
            "Retention applied for peer %s: removed %d entries (%d remain)",
            peer_addr, removed, len(pruned),
        )
    return removed


def enforce_retention_all(
    profile_name: str,
    identity_key: bytes,
    policy: RetentionPolicy,
    profile_data_dir: str,
    peer_addrs: List[str],
    *,
    app_data_root: Optional[str] = None,
    confirmed: bool = False,
) -> dict[str, int]:
    """
    Apply retention policy to all specified peers.

    Args:
        profile_name: Profile name.
        identity_key: 32-byte identity key.
        policy: RetentionPolicy to enforce.
        profile_data_dir: ``profiles/<name>/`` directory for this profile.
        app_data_root: Optional application root for legacy flat history files.
        peer_addrs: List of peer addresses to process.
        confirmed: Must be True to actually write pruned histories.

    Returns:
        Dict mapping peer_addr -> number of entries removed.

    Raises:
        RuntimeError: If confirmed=False.
    """
    if not confirmed:
        raise RuntimeError(
            "enforce_retention_all is a destructive operation — "
            "pass confirmed=True to proceed"
        )

    results: dict[str, int] = {}
    for peer_addr in peer_addrs:
        try:
            removed = enforce_retention_for_peer(
                profile_name,
                identity_key,
                peer_addr,
                policy,
                profile_data_dir,
                app_data_root=app_data_root,
                confirmed=True,
            )
            results[peer_addr] = removed
        except Exception as e:
            logger.warning("Failed to enforce retention for peer %s: %s", peer_addr, e)
            results[peer_addr] = 0
    return results


def policy_from_gui_settings(settings: dict) -> RetentionPolicy:
    """
    Build a RetentionPolicy from a gui.json settings dict.

    Reads the 'history_retention' key:
      {
        "max_age_days": int or null,
        "max_messages": int or null
      }

    Falls back to defaults if key is absent.
    """
    raw = settings.get(GUI_RETENTION_KEY, {})
    if not isinstance(raw, dict):
        raw = {}

    max_age_days = raw.get("max_age_days")
    max_messages = raw.get("max_messages", DEFAULT_MAX_MESSAGES)

    return RetentionPolicy(
        max_age_days=int(max_age_days) if max_age_days is not None else None,
        max_messages=int(max_messages) if max_messages is not None else None,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _age_cutoff(max_age_days: int) -> datetime:
    """Return a timezone-aware datetime representing the oldest allowed message time."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    return now - timedelta(days=max_age_days)


def _entry_ts(entry: HistoryEntry) -> datetime:
    """Parse entry.ts to a timezone-aware datetime. Returns epoch on parse failure."""
    try:
        ts = entry.ts
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _prune_by_count(entries: List[HistoryEntry], max_messages: int) -> List[HistoryEntry]:
    """
    Keep at most max_messages entries, preserving all system messages and
    preferring the most recent non-system messages.
    """
    system = [e for e in entries if e.kind == "system"]
    non_system = [e for e in entries if e.kind != "system"]

    # How many non-system slots remain after reserving space for system messages?
    # We always keep all system messages; they don't count against max_messages.
    keep_non_system = max(0, max_messages - len(system))
    if keep_non_system < len(non_system):
        non_system = non_system[-keep_non_system:]

    # Merge back in chronological order
    combined = system + non_system
    combined.sort(key=lambda e: _entry_ts(e))
    return combined
