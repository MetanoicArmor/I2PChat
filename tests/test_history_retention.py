"""
Tests for history_retention.py — history retention policy enforcement.
"""

import secrets
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import crypto
from chat_history import DEFAULT_MAX_MESSAGES, HistoryEntry, load_history, save_history
from history_retention import (
    GUI_RETENTION_KEY,
    RetentionPolicy,
    apply_retention,
    enforce_retention_all,
    enforce_retention_for_peer,
    policy_from_gui_settings,
)

IDENTITY_KEY = secrets.token_bytes(32)
PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


def _ts(days_ago: float = 0) -> str:
    """ISO-8601 UTC timestamp N days in the past."""
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(text: str, days_ago: float = 0, kind: str = "me", msg_id: str = "") -> HistoryEntry:
    return HistoryEntry(kind=kind, text=text, ts=_ts(days_ago), message_id=msg_id or None)


def _system(text: str, days_ago: float = 0) -> HistoryEntry:
    return HistoryEntry(kind="system", text=text, ts=_ts(days_ago))


class RetentionPolicyValidationTests(unittest.TestCase):
    def test_valid_defaults(self) -> None:
        p = RetentionPolicy()
        self.assertIsNone(p.max_age_days)
        self.assertEqual(p.max_messages, DEFAULT_MAX_MESSAGES)
        self.assertTrue(p.per_peer)

    def test_invalid_max_age_days(self) -> None:
        with self.assertRaises(ValueError):
            RetentionPolicy(max_age_days=0)
        with self.assertRaises(ValueError):
            RetentionPolicy(max_age_days=-1)

    def test_invalid_max_messages(self) -> None:
        with self.assertRaises(ValueError):
            RetentionPolicy(max_messages=0)
        with self.assertRaises(ValueError):
            RetentionPolicy(max_messages=-5)

    def test_none_limits_allowed(self) -> None:
        p = RetentionPolicy(max_age_days=None, max_messages=None)
        self.assertIsNone(p.max_age_days)
        self.assertIsNone(p.max_messages)


class ApplyRetentionCountTests(unittest.TestCase):
    def test_no_pruning_needed(self) -> None:
        entries = [_entry(f"msg-{i}") for i in range(5)]
        policy = RetentionPolicy(max_messages=10)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 5)

    def test_prune_oldest_first(self) -> None:
        # entries 0..9, oldest first, keep 5
        entries = [_entry(f"msg-{i}", days_ago=10 - i) for i in range(10)]
        policy = RetentionPolicy(max_messages=5)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 5)
        texts = [e.text for e in result]
        # Most recent 5 kept: msg-5..msg-9
        self.assertIn("msg-9", texts)
        self.assertNotIn("msg-0", texts)

    def test_system_messages_preserved(self) -> None:
        entries = [_entry(f"msg-{i}", days_ago=10 - i) for i in range(8)]
        entries.append(_system("connected", days_ago=12))
        policy = RetentionPolicy(max_messages=5)
        result = apply_retention(entries, policy)
        kinds = [e.kind for e in result]
        # system message always preserved
        self.assertIn("system", kinds)

    def test_no_count_limit(self) -> None:
        entries = [_entry(f"msg-{i}") for i in range(20)]
        policy = RetentionPolicy(max_messages=None)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 20)

    def test_empty_entries(self) -> None:
        result = apply_retention([], RetentionPolicy())
        self.assertEqual(result, [])

    def test_exact_count_limit(self) -> None:
        entries = [_entry(f"msg-{i}") for i in range(5)]
        policy = RetentionPolicy(max_messages=5)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 5)


class ApplyRetentionAgeTests(unittest.TestCase):
    def test_prune_old_messages(self) -> None:
        entries = [
            _entry("old", days_ago=10),
            _entry("recent", days_ago=1),
        ]
        policy = RetentionPolicy(max_age_days=5, max_messages=None)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "recent")

    def test_system_messages_not_pruned_by_age(self) -> None:
        entries = [
            _system("old-system", days_ago=30),
            _entry("old-msg", days_ago=30),
            _entry("recent", days_ago=1),
        ]
        policy = RetentionPolicy(max_age_days=5, max_messages=None)
        result = apply_retention(entries, policy)
        texts = [e.text for e in result]
        self.assertIn("old-system", texts)
        self.assertNotIn("old-msg", texts)

    def test_no_age_limit(self) -> None:
        entries = [_entry("ancient", days_ago=3650)]
        policy = RetentionPolicy(max_age_days=None, max_messages=None)
        result = apply_retention(entries, policy)
        self.assertEqual(len(result), 1)

    def test_age_and_count_both_applied(self) -> None:
        # 5 messages: 3 old, 2 recent; max_age=5, max_messages=1
        entries = [
            _entry("old-1", days_ago=10),
            _entry("old-2", days_ago=8),
            _entry("old-3", days_ago=7),
            _entry("recent-1", days_ago=2),
            _entry("recent-2", days_ago=1),
        ]
        policy = RetentionPolicy(max_age_days=5, max_messages=1)
        result = apply_retention(entries, policy)
        # After age prune: recent-1, recent-2. After count prune: keep 1 (most recent)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "recent-2")

    def test_malformed_ts_treated_as_epoch(self) -> None:
        entry = HistoryEntry(kind="me", text="bad-ts", ts="not-a-date")
        policy = RetentionPolicy(max_age_days=1, max_messages=None)
        result = apply_retention([entry], policy)
        # Epoch is very old, so it gets pruned
        self.assertEqual(result, [])


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class EnforceRetentionForPeerTests(unittest.TestCase):
    def test_requires_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save_history(td, "alice", PEER_A, [_entry("x")], IDENTITY_KEY)
            with self.assertRaises(RuntimeError) as ctx:
                enforce_retention_for_peer(
                    "alice", IDENTITY_KEY, PEER_A,
                    RetentionPolicy(max_messages=1),
                    td, confirmed=False,
                )
            self.assertIn("confirmed=True", str(ctx.exception))

    def test_prunes_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = [_entry(f"msg-{i}", days_ago=10 - i) for i in range(10)]
            save_history(td, "alice", PEER_A, entries, IDENTITY_KEY)

            removed = enforce_retention_for_peer(
                "alice", IDENTITY_KEY, PEER_A,
                RetentionPolicy(max_messages=5),
                td, confirmed=True,
            )
            self.assertEqual(removed, 5)
            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            self.assertEqual(len(loaded), 5)

    def test_no_op_when_within_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries = [_entry(f"msg-{i}") for i in range(3)]
            save_history(td, "alice", PEER_A, entries, IDENTITY_KEY)

            removed = enforce_retention_for_peer(
                "alice", IDENTITY_KEY, PEER_A,
                RetentionPolicy(max_messages=10),
                td, confirmed=True,
            )
            self.assertEqual(removed, 0)
            loaded = load_history(td, "alice", PEER_A, IDENTITY_KEY)
            self.assertEqual(len(loaded), 3)

    def test_no_op_on_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            removed = enforce_retention_for_peer(
                "alice", IDENTITY_KEY, PEER_A,
                RetentionPolicy(max_messages=5),
                td, confirmed=True,
            )
            self.assertEqual(removed, 0)


@unittest.skipUnless(crypto.NACL_AVAILABLE, "PyNaCl required")
class EnforceRetentionAllTests(unittest.TestCase):
    def test_requires_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                enforce_retention_all(
                    "alice", IDENTITY_KEY,
                    RetentionPolicy(max_messages=1),
                    td, [PEER_A], confirmed=False,
                )

    def test_applies_to_all_peers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entries_a = [_entry(f"a-{i}", days_ago=10 - i) for i in range(8)]
            entries_b = [_entry(f"b-{i}", days_ago=10 - i) for i in range(6)]
            save_history(td, "alice", PEER_A, entries_a, IDENTITY_KEY)
            save_history(td, "alice", PEER_B, entries_b, IDENTITY_KEY)

            results = enforce_retention_all(
                "alice", IDENTITY_KEY,
                RetentionPolicy(max_messages=3),
                td, [PEER_A, PEER_B], confirmed=True,
            )

            self.assertEqual(results[PEER_A], 5)
            self.assertEqual(results[PEER_B], 3)
            self.assertEqual(len(load_history(td, "alice", PEER_A, IDENTITY_KEY)), 3)
            self.assertEqual(len(load_history(td, "alice", PEER_B, IDENTITY_KEY)), 3)

    def test_empty_peer_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            results = enforce_retention_all(
                "alice", IDENTITY_KEY,
                RetentionPolicy(), td, [], confirmed=True,
            )
            self.assertEqual(results, {})


class PolicyFromGuiSettingsTests(unittest.TestCase):
    def test_defaults_when_key_absent(self) -> None:
        policy = policy_from_gui_settings({})
        self.assertIsNone(policy.max_age_days)
        self.assertEqual(policy.max_messages, DEFAULT_MAX_MESSAGES)

    def test_reads_max_age_days(self) -> None:
        policy = policy_from_gui_settings({GUI_RETENTION_KEY: {"max_age_days": 30}})
        self.assertEqual(policy.max_age_days, 30)

    def test_reads_max_messages(self) -> None:
        policy = policy_from_gui_settings({GUI_RETENTION_KEY: {"max_messages": 500}})
        self.assertEqual(policy.max_messages, 500)

    def test_null_max_messages(self) -> None:
        policy = policy_from_gui_settings({GUI_RETENTION_KEY: {"max_messages": None}})
        self.assertIsNone(policy.max_messages)

    def test_null_max_age_days(self) -> None:
        policy = policy_from_gui_settings({GUI_RETENTION_KEY: {"max_age_days": None}})
        self.assertIsNone(policy.max_age_days)

    def test_invalid_retention_key_type_uses_defaults(self) -> None:
        # If gui.json has non-dict value for the key, fall back to defaults
        policy = policy_from_gui_settings({GUI_RETENTION_KEY: "bad"})
        self.assertEqual(policy.max_messages, DEFAULT_MAX_MESSAGES)


if __name__ == "__main__":
    unittest.main()
