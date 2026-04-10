from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .models import GroupState, normalize_member_id


class GroupTopologyLinkState(StrEnum):
    LIVE = "live"
    HANDSHAKING = "handshaking"
    BLINDBOX = "blindbox"
    DEGRADED = "degraded"
    FAILED = "failed"
    IDLE = "idle"


@dataclass(slots=True, frozen=True)
class GroupTopologyNode:
    member_id: str
    label: str
    is_local: bool = False
    peer_state: str = "disconnected"
    live_ready: bool = False
    blindbox_ready: bool = False
    last_delivery_status: str = ""
    last_delivery_reason: str = ""


@dataclass(slots=True, frozen=True)
class GroupTopologyEdge:
    source_id: str
    target_id: str
    state: GroupTopologyLinkState
    label: str
    peer_state: str = "disconnected"
    live_ready: bool = False
    blindbox_ready: bool = False
    last_delivery_status: str = ""
    last_delivery_reason: str = ""


@dataclass(slots=True, frozen=True)
class GroupTopologySnapshot:
    group_id: str
    title: str | None
    local_member_id: str
    observed_only: bool
    nodes: tuple[GroupTopologyNode, ...]
    edges: tuple[GroupTopologyEdge, ...]


def _short_member_label(member_id: str, *, fallback: str = "Peer") -> str:
    normalized = normalize_member_id(member_id)
    if not normalized:
        return fallback
    if normalized.endswith(".b32.i2p"):
        normalized = normalized[: -len(".b32.i2p")]
    if len(normalized) <= 14:
        return normalized
    return f"{normalized[:6]}..{normalized[-6:]}"


def _normalize_status_text(value: object) -> str:
    return str(value or "").strip().lower()


def _link_state_for_member(
    *,
    peer_state: str,
    live_ready: bool,
    blindbox_ready: bool,
) -> GroupTopologyLinkState:
    if live_ready:
        return GroupTopologyLinkState.LIVE
    if peer_state in {"handshaking", "connecting"}:
        return GroupTopologyLinkState.HANDSHAKING
    if peer_state == "failed":
        return GroupTopologyLinkState.FAILED
    if peer_state == "stale":
        return GroupTopologyLinkState.DEGRADED
    if blindbox_ready:
        return GroupTopologyLinkState.BLINDBOX
    return GroupTopologyLinkState.IDLE


def _edge_label(
    *,
    state: GroupTopologyLinkState,
    blindbox_ready: bool,
    last_delivery_status: str,
) -> str:
    parts = [state.value]
    if blindbox_ready and state != GroupTopologyLinkState.BLINDBOX:
        parts.append("blindbox")
    if last_delivery_status:
        parts.append(f"last={last_delivery_status}")
    return ", ".join(parts)


def build_observed_group_topology(
    state: GroupState,
    *,
    local_member_id: str,
    live_by_member: Mapping[str, bool],
    peer_state_by_member: Mapping[str, str],
    blindbox_ready_by_member: Mapping[str, bool] | None = None,
    delivery_status_by_member: Mapping[str, str] | None = None,
    delivery_reason_by_member: Mapping[str, str] | None = None,
) -> GroupTopologySnapshot:
    blindbox_by_member = {
        normalize_member_id(member_id): bool(ready)
        for member_id, ready in dict(blindbox_ready_by_member or {}).items()
        if normalize_member_id(member_id)
    }
    delivery_status = {
        normalize_member_id(member_id): _normalize_status_text(raw_status)
        for member_id, raw_status in dict(delivery_status_by_member or {}).items()
        if normalize_member_id(member_id) and _normalize_status_text(raw_status)
    }
    delivery_reason = {
        normalize_member_id(member_id): str(raw_reason or "").strip()
        for member_id, raw_reason in dict(delivery_reason_by_member or {}).items()
        if normalize_member_id(member_id) and str(raw_reason or "").strip()
    }

    normalized_local = normalize_member_id(local_member_id)
    nodes: list[GroupTopologyNode] = []
    edges: list[GroupTopologyEdge] = []
    for raw_member in state.members:
        member_id = normalize_member_id(raw_member)
        if not member_id:
            continue
        is_local = bool(normalized_local and member_id == normalized_local)
        live_ready = bool(live_by_member.get(member_id, False))
        peer_state = str(peer_state_by_member.get(member_id, "disconnected") or "disconnected")
        blindbox_ready = bool(blindbox_by_member.get(member_id, False))
        last_status = delivery_status.get(member_id, "")
        last_reason = delivery_reason.get(member_id, "")
        label = "You" if is_local else _short_member_label(member_id)
        nodes.append(
            GroupTopologyNode(
                member_id=member_id,
                label=label,
                is_local=is_local,
                peer_state=peer_state,
                live_ready=live_ready,
                blindbox_ready=blindbox_ready,
                last_delivery_status=last_status,
                last_delivery_reason=last_reason,
            )
        )
        if is_local or not normalized_local:
            continue
        edge_state = _link_state_for_member(
            peer_state=peer_state,
            live_ready=live_ready,
            blindbox_ready=blindbox_ready,
        )
        edges.append(
            GroupTopologyEdge(
                source_id=normalized_local,
                target_id=member_id,
                state=edge_state,
                label=_edge_label(
                    state=edge_state,
                    blindbox_ready=blindbox_ready,
                    last_delivery_status=last_status,
                ),
                peer_state=peer_state,
                live_ready=live_ready,
                blindbox_ready=blindbox_ready,
                last_delivery_status=last_status,
                last_delivery_reason=last_reason,
            )
        )

    return GroupTopologySnapshot(
        group_id=state.group_id,
        title=state.title,
        local_member_id=normalized_local,
        observed_only=True,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def render_group_topology_ascii(snapshot: GroupTopologySnapshot) -> str:
    title = snapshot.title or snapshot.group_id
    lines = [f"Observed group topology: {title} [{snapshot.group_id}]"]
    if snapshot.observed_only:
        lines.append("Scope: local node view only")
    local_node = next((node for node in snapshot.nodes if node.is_local), None)
    if local_node is not None:
        lines.append(f"Local: {local_node.label}")
    if not snapshot.edges:
        lines.append("No remote members in this group.")
        return "\n".join(lines)

    node_by_id = {node.member_id: node for node in snapshot.nodes}
    for edge in snapshot.edges:
        node = node_by_id.get(edge.target_id)
        if node is None:
            continue
        details = [edge.state.value]
        if node.peer_state and node.peer_state not in {"disconnected", edge.state.value}:
            details.append(f"peer={node.peer_state}")
        if node.blindbox_ready:
            details.append("blindbox-ready")
        if node.last_delivery_status:
            details.append(f"last={node.last_delivery_status}")
        if node.last_delivery_reason:
            details.append(f"reason={node.last_delivery_reason}")
        lines.append(f"- {node.label}: " + ", ".join(details))
    return "\n".join(lines)


def _mermaid_node_id(member_id: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]", "_", normalize_member_id(member_id))
    return token or "node"


def _mermaid_node_label(node: GroupTopologyNode) -> str:
    status_bits: list[str] = []
    if node.live_ready:
        status_bits.append("live")
    elif node.peer_state and node.peer_state != "disconnected":
        status_bits.append(node.peer_state)
    if node.blindbox_ready:
        status_bits.append("blindbox")
    if node.last_delivery_status:
        status_bits.append(f"last={node.last_delivery_status}")
    if status_bits:
        return node.label + "\\n" + "\\n".join(status_bits)
    return node.label


def render_group_topology_mermaid(snapshot: GroupTopologySnapshot) -> str:
    lines = ["graph TD"]
    for node in snapshot.nodes:
        node_id = _mermaid_node_id(node.member_id)
        lines.append(f'  {node_id}["{_mermaid_node_label(node)}"]')
    for edge in snapshot.edges:
        src = _mermaid_node_id(edge.source_id)
        dst = _mermaid_node_id(edge.target_id)
        lines.append(f'  {src} -->|"{edge.label}"| {dst}')
    return "\n".join(lines)
