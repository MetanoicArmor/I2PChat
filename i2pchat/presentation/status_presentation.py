"""
Human-readable status lines for the main window (GitHub backlog: simplified connection/delivery UX).

Pure functions: no Qt, no I2PChatCore imports.
"""

from __future__ import annotations

from dataclasses import dataclass


def network_display(code: str) -> str:
    """Short tag for I2P tunnel / SAM readiness (matches legacy Net: line)."""
    return {
        "initializing": "starting",
        "local_ok": "pending",
        "visible": "visible",
    }.get(code, code)


def chat_session_label(
    *,
    connected: bool,
    handshake_complete: bool,
    outbound_connect_busy: bool,
) -> str:
    """Live session with peer: Disconnected / Connecting / Online."""
    if outbound_connect_busy:
        return "Connecting…"
    if connected and not handshake_complete:
        return "Connecting…"
    if connected and handshake_complete:
        return "Online"
    return "Disconnected"


def delivery_route_label(delivery_state: str) -> str:
    """User-facing delivery / send route (no internal snake_case in the bar)."""
    return {
        "connecting-handshake": "Setting up secure chat…",
        "online-live": "Live",
        "offline-ready": "Will deliver later",
        "await-live-root": "Need live chat once",
        "blindbox-needs-locked-peer": "Lock peer first",
        "blindbox-needs-boxes": "Configure Blind Boxes",
        "blindbox-starting-local-session": "Waiting for I2P…",
        "blindbox-disabled-transient": "Live only (transient)",
        "blindbox-disabled": "Live only",
        "blindbox-initializing": "Offline delivery starting…",
    }.get(delivery_state, "Unavailable")


def i2p_network_friendly(network_status_raw: str) -> str:
    """One word for I2P visibility phase."""
    return {
        "initializing": "Starting",
        "local_ok": "Pending",
        "visible": "Online",
    }.get(network_status_raw, network_status_raw)


@dataclass(frozen=True)
class StatusPresentation:
    primary_short: str
    primary_full: str
    technical_detail: str


def build_status_presentation(
    *,
    network_status_raw: str,
    connected: bool,
    handshake_complete: bool,
    outbound_connect_busy: bool,
    delivery_state: str,
    send_in_flight: bool,
    profile_name: str,
    is_transient_profile: bool,
    peer_short: str,
    stored_short: str,
    link_state: str,
    secure_state: str,
    delivery_bar: str,
    blindbox_bar: str,
    blindbox_detail: str,
    ack_part: str,
) -> StatusPresentation:
    """
    Build main status line (short + full) and multi-line technical tooltip body.

    ``delivery_bar`` / ``blindbox_bar`` are the existing short segments from main_qt helpers.
    ``blindbox_detail`` is the long BlindBox explanation block for the tooltip.
    """
    net_d = network_display(network_status_raw)
    chat = chat_session_label(
        connected=connected,
        handshake_complete=handshake_complete,
        outbound_connect_busy=outbound_connect_busy,
    )
    delivery_f = delivery_route_label(delivery_state)
    i2p_w = i2p_network_friendly(network_status_raw)

    mode_tag = "T" if is_transient_profile else "P"
    send_prefix = "Sending… · " if send_in_flight else ""

    primary_short = (
        f"{send_prefix}{chat} · {delivery_f} · I2P {i2p_w}"
    )
    primary_full = (
        f"{send_prefix}Prof:{profile_name} ({mode_tag}) | Chat:{chat} | "
        f"Delivery:{delivery_f} | Peer:{peer_short} | I2P:{net_d} | "
        f"{blindbox_bar}"
    )

    tech_lines = [
        "Technical detail",
        f"Net:{net_d} | Link:{link_state} | Peer:{peer_short} | St:{stored_short} | Sec:{secure_state}",
        f"Delivery (internal): {delivery_state}",
        f"Send UI: {delivery_bar}",
        f"BlindBox bar: {blindbox_bar}",
        blindbox_detail.strip(),
        ack_part,
    ]
    technical_detail = "\n".join(line for line in tech_lines if line)

    return StatusPresentation(
        primary_short=primary_short,
        primary_full=primary_full,
        technical_detail=technical_detail,
    )
