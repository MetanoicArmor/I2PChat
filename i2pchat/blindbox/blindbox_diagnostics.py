"""
Pure formatting helpers for BlindBox diagnostics UI.
"""

from __future__ import annotations

from typing import Any


def build_blindbox_diagnostics_text(
    *,
    profile: str,
    selected_peer: str,
    delivery: dict[str, Any],
    blindbox: dict[str, Any],
    ack: dict[str, int],
) -> str:
    lines = [
        f"Profile: {profile}",
        f"Selected peer: {selected_peer or '—'}",
        "",
        "Delivery",
        f"- State: {delivery.get('state', 'unknown')}",
        f"- Live secure session: {'yes' if delivery.get('secure_live') else 'no'}",
        f"- Offline queue ready: {'yes' if delivery.get('state') == 'offline-ready' else 'no'}",
        f"- Target peer available: {'yes' if delivery.get('has_target') else 'no'}",
        "",
        "BlindBox",
        f"- Enabled: {'yes' if blindbox.get('enabled') else 'no'}",
        f"- Runtime ready: {'yes' if blindbox.get('ready') else 'no'}",
        f"- Poller running: {'yes' if blindbox.get('poller_running') else 'no'}",
        f"- Root secret initialized: {'yes' if blindbox.get('has_root_secret') else 'no'}",
        f"- Blind Boxes configured: {blindbox.get('blind_boxes', 0)}",
        f"- Privacy profile: {blindbox.get('privacy_profile', 'unknown')}",
        f"- Send index: {blindbox.get('send_index', 0)}",
        f"- Root epoch: {blindbox.get('root_epoch', 0)}",
    ]
    if blindbox.get("insecure_local_mode"):
        lines.append("- Warning: insecure local BlindBox mode is active")
    lines.extend(
        [
            "",
            "ACK telemetry",
            f"- Dropped/invalid ACK total: {sum(int(v) for v in ack.values())}",
        ]
    )
    for key in sorted(ack):
        lines.append(f"  - {key}: {ack[key]}")
    lines.extend(
        [
            "",
            "What this means",
            "- `offline-ready` means messages can be queued for delayed delivery.",
            "- `await-live-root` means one successful live secure chat is still required.",
            "- `sending` / `queued` / `delivered` / `failed` are per-message states in chat history and bubbles.",
        ]
    )
    return "\n".join(lines)
