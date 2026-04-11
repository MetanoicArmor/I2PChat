from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from i2pchat.groups.models import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupMemberDeliveryResult,
)
from i2pchat.storage.group_store import GroupHistoryEntry

GROUP_CONVERSATION_KEY_PREFIX = "group:"


def make_group_conversation_key(group_id: str) -> str:
    normalized_group_id = (group_id or "").strip()
    if not normalized_group_id:
        raise ValueError("Group id is required")
    return f"{GROUP_CONVERSATION_KEY_PREFIX}{normalized_group_id}"


def is_group_conversation_key(value: str | None) -> bool:
    return bool(value and value.startswith(GROUP_CONVERSATION_KEY_PREFIX))


def group_id_from_conversation_key(value: str | None) -> str | None:
    if not is_group_conversation_key(value):
        return None
    group_id = str(value)[len(GROUP_CONVERSATION_KEY_PREFIX) :].strip()
    return group_id or None


def split_group_member_tokens(raw_value: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in re.split(r"[\s,;]+", raw_value or ""):
        token = raw_token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def short_member_label(member_id: str, *, fallback: str = "Peer") -> str:
    normalized = (member_id or "").strip()
    if not normalized:
        return fallback
    if normalized.endswith(".b32.i2p"):
        normalized = normalized[: -len(".b32.i2p")]
    if len(normalized) <= 14:
        return normalized
    return f"{normalized[:6]}..{normalized[-6:]}"


def render_group_delivery_summary(
    delivery_results: Mapping[str, GroupMemberDeliveryResult | str],
    *,
    delivery_reasons: Mapping[str, str] | None = None,
) -> str:
    if not delivery_results:
        return "Only you are in this group."

    counts = {
        GroupDeliveryStatus.DELIVERED_LIVE: 0,
        GroupDeliveryStatus.QUEUED_OFFLINE: 0,
        GroupDeliveryStatus.FAILED: 0,
    }
    for result in delivery_results.values():
        if isinstance(result, GroupMemberDeliveryResult):
            status = result.status
        else:
            try:
                status = GroupDeliveryStatus(str(result))
            except ValueError:
                continue
        counts[status] += 1

    parts: list[str] = []
    if counts[GroupDeliveryStatus.DELIVERED_LIVE]:
        parts.append(f"{counts[GroupDeliveryStatus.DELIVERED_LIVE]} live")
    if counts[GroupDeliveryStatus.QUEUED_OFFLINE]:
        parts.append(f"{counts[GroupDeliveryStatus.QUEUED_OFFLINE]} queued")
    if counts[GroupDeliveryStatus.FAILED]:
        parts.append(f"{counts[GroupDeliveryStatus.FAILED]} failed")
    if not parts:
        return "No member delivery results were recorded."
    line = "Delivery: " + ", ".join(parts)
    reasons = dict(delivery_reasons or {})
    if not reasons:
        return line
    detail_bits: list[str] = []
    for peer_id, reason in sorted(reasons.items(), key=lambda kv: kv[0]):
        r = (reason or "").strip()
        if not r:
            continue
        detail_bits.append(f"{short_member_label(peer_id)}: {r}")
    if not detail_bits:
        return line
    return line + "\n" + "Details: " + "; ".join(detail_bits)


def render_group_control_text(
    payload: Mapping[str, Any] | None,
    *,
    actor_label: str | None = None,
) -> str:
    actor = (actor_label or "").strip() or "Group"
    control_payload = dict(payload or {})
    detail_parts: list[str] = []

    title = str(control_payload.get("title") or "").strip()
    if title:
        detail_parts.append(f'title "{title}"')

    members_value = control_payload.get("members")
    if isinstance(members_value, (list, tuple)):
        member_count = len([str(member).strip() for member in members_value if str(member).strip()])
        if member_count:
            detail_parts.append(f"{member_count} members")

    if "epoch" in control_payload:
        try:
            detail_parts.append(f"epoch {int(control_payload['epoch'])}")
        except (TypeError, ValueError):
            pass

    if not detail_parts and control_payload:
        visible_keys = ", ".join(sorted(str(key) for key in control_payload.keys()))
        detail_parts.append(f"fields {visible_keys}")

    if detail_parts:
        return f"{actor} updated group settings: " + ", ".join(detail_parts)
    return f"{actor} sent a group control update."


def render_group_history_preview(entry: GroupHistoryEntry) -> str:
    if entry.content_type == GroupContentType.GROUP_TEXT:
        sender_prefix = ""
        if entry.kind != "me":
            sender_prefix = short_member_label(entry.sender_id) + ": "
        return sender_prefix + str(entry.text or "")
    if entry.content_type == GroupContentType.GROUP_CONTROL:
        actor_label = "You" if entry.kind == "me" else short_member_label(entry.sender_id)
        return render_group_control_text(
            entry.payload if isinstance(entry.payload, Mapping) else None,
            actor_label=actor_label,
        )
    return str(entry.text or "")
