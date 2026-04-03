"""
Pure formatting helpers for BlindBox diagnostics UI.
"""

from __future__ import annotations

from typing import Any


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _replica_source_label(raw: Any) -> str:
    value = str(raw or "").strip()
    return {
        "release-builtin": "release defaults",
        "profile-file": "saved in profile",
        "env": "environment override",
        "env-default": "deployment defaults from environment",
        "file-default": "deployment defaults file",
        "local-auto": "local auto fallback",
        "none": "not configured",
    }.get(value, value or "unknown")


def _transport_label(blindbox: dict[str, Any]) -> str:
    if blindbox.get("use_sam_for_replicas"):
        return "I2P / SAM"
    if blindbox.get("has_loopback_replicas"):
        return "direct TCP / loopback"
    return "direct TCP"


def _auth_mode_label(blindbox: dict[str, Any]) -> str:
    if blindbox.get("insecure_local_mode"):
        return "insecure local mode"
    if blindbox.get("local_auth_token_enabled") and not blindbox.get("use_sam_for_replicas"):
        return "local/direct token enabled"
    if blindbox.get("use_sam_for_replicas"):
        return "public I2P endpoints or per-replica token"
    return "public / no token"


def _status_block(
    delivery: dict[str, Any], blindbox: dict[str, Any]
) -> tuple[str, list[str], list[str]]:
    state = str(delivery.get("state", "unknown"))
    status_title = "Status unknown"
    details: list[str] = []
    actions: list[str] = []

    if state == "offline-ready":
        status_title = "Offline queue is ready"
        details = [
            "You can send text now without a live secure session.",
            "Live connect is optional right now.",
        ]
        actions = [
            "Send text now — it will be queued for delayed delivery.",
            "Connect live only if you want immediate chat, file transfer, or images.",
            "No fix is required.",
        ]
    elif state == "online-live":
        status_title = "Live secure session is active"
        details = [
            "Messages can be delivered immediately.",
            "Offline delivery remains available as a fallback when configured.",
        ]
        actions = [
            "Send text, images, or files normally.",
            "No fix is required.",
        ]
    elif state == "await-live-root":
        status_title = "Offline queue is not ready yet"
        details = [
            "BlindBox is configured, but the first offline key exchange is still missing.",
            "One successful live secure chat is required before delayed delivery can start.",
        ]
        actions = [
            "Press Connect once and complete one secure live session with this peer.",
            "Keep this peer locked to the current profile.",
        ]
    elif state == "blindbox-needs-locked-peer":
        status_title = "Offline queue needs a locked peer"
        details = [
            "BlindBox only works for the peer locked into this persistent profile.",
        ]
        actions = [
            "Lock this profile to the target peer first.",
        ]
    elif state == "blindbox-needs-boxes":
        status_title = "No BlindBox replicas are configured"
        details = [
            "Delayed delivery cannot work until at least one replica endpoint is configured.",
        ]
        actions = [
            "Add replicas in this dialog or through deployment environment settings.",
        ]
    elif state == "blindbox-starting-local-session":
        status_title = "Local BlindBox session is still starting"
        details = [
            "The app is waiting for the local I2P runtime before delayed delivery can start.",
        ]
        actions = [
            "Wait a moment and retry.",
        ]
    elif state == "blindbox-disabled-transient":
        status_title = "Offline queue is disabled in TRANSIENT mode"
        details = [
            "The random_address / transient profile does not keep persistent offline-delivery state.",
        ]
        actions = [
            "Switch to a named persistent profile if you want BlindBox delivery.",
        ]
    elif state == "blindbox-disabled":
        status_title = "Offline queue is disabled by configuration"
        details = [
            "BlindBox is currently turned off for this profile or deployment.",
        ]
        actions = [
            "Enable BlindBox or use a live connection instead.",
        ]
    elif state == "connecting-handshake":
        status_title = "Live secure session is being established"
        details = [
            "The app is in the secure-handshake phase right now.",
        ]
        actions = [
            "Wait for the handshake to finish.",
        ]

    if blindbox.get("insecure_local_mode"):
        details.append("Warning: insecure local BlindBox mode is active.")
        actions.append("Set a local token and disable insecure local mode when possible.")

    return status_title, details, actions


def build_blindbox_diagnostics_text(
    *,
    profile: str,
    selected_peer: str,
    delivery: dict[str, Any],
    blindbox: dict[str, Any],
    ack: dict[str, int],
) -> str:
    state = str(delivery.get("state", "unknown"))
    status_title, status_details, actions = _status_block(delivery, blindbox)
    ack_total = sum(int(v) for v in ack.values())
    recv_base = int(blindbox.get("recv_base", 0))
    recv_window = max(1, int(blindbox.get("recv_window", 1)))
    recv_end = recv_base + recv_window - 1

    lines = [
        "Profile",
        f"- Name: {profile}",
        "",
        "Peer",
        f"- Selected peer: {selected_peer or '—'}",
        "",
        "Status",
        f"- {status_title}",
    ]
    for item in status_details:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "What you can do now",
        ]
    )
    for item in actions:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "Security",
            f"- Locked to peer: {_yes_no(delivery.get('stored_peer'))}",
            f"- Live secure session: {_yes_no(delivery.get('secure_live'))}",
            f"- Offline key exchange completed: {_yes_no(blindbox.get('has_root_secret'))}",
            f"- Replica transport: {_transport_label(blindbox)}",
            f"- Replica auth mode: {_auth_mode_label(blindbox)}",
        ]
    )
    if blindbox.get("insecure_local_mode"):
        lines.append("- Local insecure mode: yes (warning)")
    else:
        lines.append("- Local insecure mode: no")
    lines.extend(
        [
            "",
            "Replicas",
            f"- Enabled: {_yes_no(blindbox.get('enabled'))}",
            f"- Runtime ready: {_yes_no(blindbox.get('ready'))}",
            f"- Replica source: {_replica_source_label(blindbox.get('replicas_source') or blindbox.get('blind_boxes_source'))}",
            f"- Count: {blindbox.get('blind_boxes', 0)}",
            f"- Background polling: {'active' if blindbox.get('poller_running') else 'idle'}",
            f"- Privacy profile: {blindbox.get('privacy_profile', 'unknown')}",
        ]
    )
    reps = blindbox.get("replica_endpoints")
    if isinstance(reps, list) and reps:
        lines.append("- Endpoints:")
        for i, ep in enumerate(reps, 1):
            lines.append(f"  {i}. {ep}")
    if blindbox.get("replicas_gui_locked"):
        lines.append("- Replica list: locked by environment (use env vars / global replicas file)")
    lines.extend(
        [
            "",
            "Advanced",
            f"- Delivery state (raw): {state}",
            f"- Target peer available: {_yes_no(delivery.get('has_target'))}",
            f"- Send index: {blindbox.get('send_index', 0)}",
            f"- Receive window: {recv_base}..{recv_end}",
            f"- Root epoch: {blindbox.get('root_epoch', 0)}",
            f"- PUT quorum: {blindbox.get('put_quorum', 0)}",
            f"- GET quorum: {blindbox.get('get_quorum', 0)}",
            f"- Cover GETs per cycle: {blindbox.get('cover_gets', 0)}",
            f"- Padding bucket: {blindbox.get('padding_bucket', 0)}",
            f"- ACK issues: {ack_total}",
        ]
    )
    for key in sorted(ack):
        lines.append(f"  - {key}: {ack[key]}")
    lines.extend(
        [
            "",
            "State guide",
            "- `offline-ready` means delayed delivery is ready now.",
            "- `await-live-root` means you still need one successful live secure chat.",
            "- `online-live` means the secure live path is active right now.",
            "- `sending` / `queued` / `delivered` / `failed` are per-message states shown in chat history and bubbles.",
        ]
    )
    return "\n".join(lines)
