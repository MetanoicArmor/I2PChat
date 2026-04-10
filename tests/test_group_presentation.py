from __future__ import annotations

from datetime import datetime, timezone

from i2pchat.groups.models import GroupContentType
from i2pchat.presentation.group_conversations import (
    group_id_from_conversation_key,
    is_group_conversation_key,
    make_group_conversation_key,
    render_group_control_text,
    render_group_delivery_summary,
    render_group_history_preview,
    split_group_member_tokens,
)
from i2pchat.storage.group_store import GroupHistoryEntry


PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


def test_group_conversation_key_round_trip() -> None:
    key = make_group_conversation_key("group-1")

    assert key == "group:group-1"
    assert is_group_conversation_key(key) is True
    assert group_id_from_conversation_key(key) == "group-1"
    assert group_id_from_conversation_key("peer") is None


def test_split_group_member_tokens_dedupes_preserving_order() -> None:
    tokens = split_group_member_tokens(f"{PEER_A}, {PEER_B}\n{PEER_A}  {PEER_B}")

    assert tokens == [PEER_A, PEER_B]


def test_render_group_delivery_summary_counts_each_status() -> None:
    summary = render_group_delivery_summary(
        {
            PEER_A: "delivered_live",
            PEER_B: "queued_offline",
            "cccccccccccccccccccccccccccccccccccccccc.b32.i2p": "failed",
        }
    )

    assert summary == "Delivery: 1 live, 1 queued, 1 failed"


def test_render_group_delivery_summary_includes_failure_reasons() -> None:
    failed_peer = "dddddddddddddddddddddddddddddddddddddddd.b32.i2p"
    summary = render_group_delivery_summary(
        {
            PEER_A: "delivered_live",
            failed_peer: "failed",
        },
        delivery_reasons={failed_peer: "blindbox-await-root"},
    )

    assert summary.startswith("Delivery: 1 live, 1 failed")
    assert "Details:" in summary
    assert "blindbox-await-root" in summary


def test_render_group_control_text_uses_known_fields() -> None:
    text = render_group_control_text(
        {
            "title": "Weekend plans",
            "members": [PEER_A, PEER_B],
            "epoch": 3,
        },
        actor_label="Alice",
    )

    assert 'Alice updated group settings: title "Weekend plans", 2 members, epoch 3' == text


def test_render_group_history_preview_formats_text_and_control_entries() -> None:
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    text_entry = GroupHistoryEntry(
        kind="peer",
        sender_id=PEER_A,
        content_type=GroupContentType.GROUP_TEXT,
        text="hello group",
        msg_id="msg-1",
        created_at=created_at,
    )
    control_entry = GroupHistoryEntry(
        kind="peer",
        sender_id=PEER_B,
        content_type=GroupContentType.GROUP_CONTROL,
        payload={"title": "Renamed"},
        msg_id="msg-2",
        created_at=created_at,
    )

    assert render_group_history_preview(text_entry).endswith(": hello group")
    assert 'updated group settings: title "Renamed"' in render_group_history_preview(
        control_entry
    )
