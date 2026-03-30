from i2pchat.presentation.status_presentation import (
    build_status_presentation,
    chat_session_label,
    delivery_route_label,
    network_display,
)


def test_network_display() -> None:
    assert network_display("visible") == "visible"
    assert network_display("local_ok") == "pending"
    assert network_display("unknown") == "unknown"


def test_chat_session_label() -> None:
    assert chat_session_label(
        connected=False, handshake_complete=False, outbound_connect_busy=False
    ) == "Disconnected"
    assert chat_session_label(
        connected=True, handshake_complete=False, outbound_connect_busy=False
    ) == "Connecting…"
    assert chat_session_label(
        connected=True, handshake_complete=True, outbound_connect_busy=False
    ) == "Online"
    assert chat_session_label(
        connected=False, handshake_complete=False, outbound_connect_busy=True
    ) == "Connecting…"


def test_delivery_route_label_covers_core_states() -> None:
    assert delivery_route_label("offline-ready") == "Will deliver later"
    assert delivery_route_label("online-live") == "Live"
    assert delivery_route_label("blindbox-initializing") == "Offline delivery starting…"
    assert delivery_route_label("unknown-state") == "Unavailable"


def test_build_status_sending_prefix() -> None:
    p = build_status_presentation(
        network_status_raw="visible",
        connected=True,
        handshake_complete=True,
        outbound_connect_busy=False,
        delivery_state="online-live",
        send_in_flight=True,
        profile_name="alice",
        is_transient_profile=False,
        peer_short="ab..cd.b32.i2p",
        stored_short="none",
        link_state="online",
        secure_state="on",
        delivery_bar="Send: live",
        blindbox_bar="BlindBox: on",
        blindbox_detail="BB detail",
        ack_part="ACKdrop:0",
    )
    assert p.primary_short.startswith("Sending… · ")
    assert "Online" in p.primary_short
    assert "Live" in p.primary_short
    assert "delivery (internal): online-live" in p.technical_detail.lower()


def test_build_status_compact_no_raw_tx() -> None:
    p = build_status_presentation(
        network_status_raw="local_ok",
        connected=False,
        handshake_complete=False,
        outbound_connect_busy=False,
        delivery_state="offline-ready",
        send_in_flight=False,
        profile_name="default",
        is_transient_profile=True,
        peer_short="none",
        stored_short="none",
        link_state="offline",
        secure_state="off",
        delivery_bar="Send: offline queue",
        blindbox_bar="BlindBox: on (polling Blind Boxes)",
        blindbox_detail="",
        ack_part="ACKdrop:0",
    )
    assert "offline-ready" not in p.primary_short
    assert "Will deliver later" in p.primary_short
    assert "offline-ready" in p.technical_detail
