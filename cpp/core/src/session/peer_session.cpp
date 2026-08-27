#include "i2pchat/session/peer_session.hpp"

#include <algorithm>

#include "i2pchat/protocol/signals.hpp"
#include "i2pchat/sam/destination.hpp"

namespace i2pchat::session {

std::string_view peer_state_name(PeerState state) {
    switch (state) {
        case PeerState::Connecting:
            return "connecting";
        case PeerState::Handshaking:
            return "handshaking";
        case PeerState::Secure:
            return "secure";
        case PeerState::Stale:
            return "stale";
        case PeerState::Failed:
            return "failed";
        case PeerState::Disconnected:
            break;
    }
    return "disconnected";
}

namespace {

/// The identity preface line is a base64 destination; anything much longer than
/// a destination is not one, and buffering without a bound invites a peer to
/// exhaust our memory before it has authenticated anything at all.
constexpr std::size_t kMaxIdentityLineSize = 8192;

SessionAction send_action(Bytes bytes) {
    SessionAction action;
    action.kind = SessionAction::Kind::Send;
    action.bytes = std::move(bytes);
    return action;
}

SessionAction disconnect_action(std::string reason) {
    SessionAction action;
    action.kind = SessionAction::Kind::Disconnect;
    action.reason = std::move(reason);
    return action;
}

/// Parse a peer-supplied destination without letting a malformed one throw:
/// everything here arrives from an unauthenticated peer.
std::optional<sam::Destination> try_parse_destination(std::string_view base64) {
    try {
        return sam::Destination::from_public_base64(base64);
    } catch (const sam::DestinationError&) {
        return std::nullopt;
    }
}

}  // namespace

bool PeerSession::allowed_before_handshake(char msg_type) noexcept {
    return msg_type == 'S' || msg_type == 'H' || msg_type == 'P' || msg_type == 'O';
}

PeerSession::PeerSession(PeerSessionConfig config)
    : config_(std::move(config)),
      awaiting_identity_line_(config_.direction == ConnectionDirection::Inbound),
      // We dialled a specific address, so the outbound side starts out knowing
      // who it is talking to; the inbound side must be told by the preface.
      peer_identity_confirmed_(config_.direction == ConnectionDirection::Outbound) {
    if (config_.direction == ConnectionDirection::Outbound) {
        handshake_.emplace(config_.handshake);
    } else if (!config_.handshake.peer_addr.empty()) {
        throw HandshakeError(
            "An inbound session learns the peer address from the preface");
    }
}

SessionActions PeerSession::on_stream_open() {
    SessionActions actions;
    state_ = PeerState::Handshaking;

    if (config_.direction == ConnectionDirection::Inbound) {
        // The accepting side stays silent until the preface names the caller;
        // its own `S` frame goes out from consume_identity_line.
        return actions;
    }

    // A bare line first, then the same destination in an `S` frame. The
    // duplication is deliberate: the line keeps older peers working, which read
    // the identity with a plain readline before parsing frames.
    Bytes preface = to_bytes(config_.local_dest_base64);
    preface.push_back('\n');
    actions.push_back(send_action(std::move(preface)));
    actions.push_back(send_action(
        protocol::encode_frame('S', as_bytes(config_.local_dest_base64), 0, 0)));

    for (const std::string& frame : handshake_->start_as_initiator().frames) {
        actions.push_back(send_action(protocol::encode_frame('H', as_bytes(frame), 0, 0)));
    }
    return actions;
}

bool PeerSession::consume_identity_line(ByteView data, std::size_t& consumed,
                                        SessionActions& actions) {
    const auto newline = std::find(data.begin(), data.end(), Byte{'\n'});
    if (newline == data.end()) {
        if (line_buffer_.size() + data.size() > kMaxIdentityLineSize) {
            actions.push_back(disconnect_action("Identity preface is too long"));
            state_ = PeerState::Failed;
            return false;
        }
        append(line_buffer_, data);
        consumed = data.size();
        return true;
    }

    const auto prefix_length = static_cast<std::size_t>(newline - data.begin());
    append(line_buffer_, data.first(prefix_length));
    consumed = prefix_length + 1;

    std::string line = to_string(ByteView(line_buffer_));
    line_buffer_.clear();
    awaiting_identity_line_ = false;

    while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) {
        line.pop_back();
    }

    const std::optional<sam::Destination> destination = try_parse_destination(line);
    if (!destination.has_value()) {
        actions.push_back(disconnect_action("Invalid identity preface"));
        state_ = PeerState::Failed;
        return false;
    }
    const std::string peer_addr = destination->base32();

    if (config_.identity_verifier &&
        !config_.identity_verifier(peer_addr, destination->base64())) {
        // The claimed address does not resolve to the claimed destination, so
        // the caller is impersonating someone.
        actions.push_back(disconnect_action("Identity binding not confirmed by SAM"));
        state_ = PeerState::Failed;
        return false;
    }

    // The inbound side learns who is calling only now, so the handshake config
    // is completed here rather than at construction.
    config_.handshake.peer_addr = peer_addr;
    handshake_.emplace(config_.handshake);
    peer_identity_confirmed_ = true;

    actions.push_back(send_action(
        protocol::encode_frame('S', as_bytes(config_.local_dest_base64), 0, 0)));
    return true;
}

SessionActions PeerSession::on_bytes(ByteView data) {
    SessionActions actions;
    if (state_ == PeerState::Failed || state_ == PeerState::Disconnected) {
        return actions;
    }

    while (awaiting_identity_line_ && !data.empty()) {
        std::size_t consumed = 0;
        if (!consume_identity_line(data, consumed, actions)) {
            return actions;
        }
        data = data.subspan(consumed);
    }
    if (awaiting_identity_line_) {
        return actions;
    }

    try {
        reader_.feed(data);
        while (std::optional<protocol::Frame> frame = reader_.next()) {
            handle_frame(*frame, actions);
            if (state_ == PeerState::Failed) {
                break;
            }
        }
    } catch (const protocol::ProtocolError& error) {
        actions.push_back(disconnect_action(error.what()));
        state_ = PeerState::Failed;
    }
    return actions;
}

void PeerSession::handle_frame(const protocol::Frame& frame, SessionActions& actions) {
    const bool secure = state_ == PeerState::Secure;

    if (secure && frame.msg_type == 'H') {
        // Renegotiation is not part of the protocol; an `H` frame here is an
        // attempt to reset keys on an authenticated channel.
        actions.push_back(
            disconnect_action("Protocol violation: unexpected handshake frame"));
        state_ = PeerState::Failed;
        return;
    }
    if (!secure && !allowed_before_handshake(frame.msg_type)) {
        actions.push_back(
            disconnect_action("Protocol violation: data before secure handshake"));
        state_ = PeerState::Failed;
        return;
    }
    if (!secure && frame.encrypted()) {
        actions.push_back(
            disconnect_action("Protocol error: encrypted frame before handshake"));
        state_ = PeerState::Failed;
        return;
    }

    Bytes body;
    if (secure) {
        try {
            body = channel_->decrypt_frame(frame);
        } catch (const protocol::ProtocolError& error) {
            actions.push_back(disconnect_action(error.what()));
            state_ = PeerState::Failed;
            return;
        }
    } else {
        body = frame.payload;
    }

    switch (frame.msg_type) {
        case 'S': {
            // An `S` frame is either the peer's identity preface or a control
            // signal; the `__SIGNAL__:` prefix is what tells them apart.
            const std::string text = to_string(ByteView(body));
            const std::optional<std::string> payload = protocol::signal_payload(text);
            if (!payload.has_value()) {
                handle_identity_frame(text, actions);
                return;
            }
            if (!secure &&
                !protocol::honoured_before_handshake(protocol::parse_signal(text))) {
                // A signal before the handshake is unauthenticated. QUIT is the
                // one exception: a peer hanging up has nothing to gain by
                // lying, and ignoring it would leave us waiting for a timeout.
                return;
            }
            break;
        }
        case 'H':
            handle_handshake_frame(to_string(ByteView(body)), actions);
            return;
        case 'P': {
            // Keepalive. Answered in whatever mode the channel is currently in.
            SessionAction reply = send_action(
                secure ? channel_->encrypt_frame('O', ByteView(Bytes{}), frame.msg_id)
                       : protocol::encode_frame('O', ByteView(Bytes{}), frame.msg_id, 0));
            actions.push_back(std::move(reply));
            return;
        }
        case 'O':
            // Keepalive acknowledgement; liveness tracking lives in the caller.
            return;
        default:
            break;
    }

    SessionAction deliver;
    deliver.kind = SessionAction::Kind::Deliver;
    deliver.msg_type = frame.msg_type;
    deliver.msg_id = frame.msg_id;
    deliver.bytes = std::move(body);
    actions.push_back(std::move(deliver));
}

void PeerSession::handle_identity_frame(const std::string& body,
                                        SessionActions& actions) {
    const std::optional<sam::Destination> destination = try_parse_destination(body);
    if (!destination.has_value()) {
        actions.push_back(disconnect_action("Malformed identity frame"));
        state_ = PeerState::Failed;
        return;
    }
    const std::string claimed = destination->base32();

    if (!config_.handshake.peer_addr.empty() && claimed != config_.handshake.peer_addr) {
        // Allowing the identity to change mid-session would let a peer that
        // authenticated as one address act as another.
        actions.push_back(disconnect_action("Blocked identity mismatch"));
        state_ = PeerState::Failed;
        return;
    }
    if (!peer_identity_confirmed_ && config_.identity_verifier &&
        !config_.identity_verifier(claimed, destination->base64())) {
        actions.push_back(disconnect_action("Identity binding not confirmed by SAM"));
        state_ = PeerState::Failed;
        return;
    }
    peer_identity_confirmed_ = true;
}

void PeerSession::handle_handshake_frame(const std::string& body,
                                         SessionActions& actions) {
    if (!peer_identity_confirmed_ || !handshake_.has_value()) {
        // Handshaking before the identity is settled would bind the session to
        // an address we have not verified.
        actions.push_back(disconnect_action("Handshake before identity preface"));
        state_ = PeerState::Failed;
        return;
    }

    HandshakeOutput output;
    try {
        output = handshake_->on_message(body);
    } catch (const HandshakeError& error) {
        actions.push_back(disconnect_action(error.what()));
        state_ = PeerState::Failed;
        return;
    }

    for (const std::string& frame : output.frames) {
        actions.push_back(send_action(protocol::encode_frame('H', as_bytes(frame), 0, 0)));
    }
    if (output.established) {
        channel_.emplace(handshake_->keys(), config_.padding);
        state_ = PeerState::Secure;
        SessionAction established;
        established.kind = SessionAction::Kind::Established;
        actions.push_back(std::move(established));
    }
}

Bytes PeerSession::send_message(char msg_type, ByteView payload,
                                std::uint64_t msg_id) {
    if (!secure()) {
        throw protocol::ProtocolError("Cannot send before the channel is secure");
    }
    return channel_->encrypt_frame(msg_type, payload, msg_id);
}

Bytes PeerSession::build_keepalive(std::uint64_t msg_id) {
    if (!secure()) {
        return protocol::encode_frame('P', ByteView(Bytes{}), msg_id, 0);
    }
    return channel_->encrypt_frame('P', ByteView(Bytes{}), msg_id);
}

}  // namespace i2pchat::session
