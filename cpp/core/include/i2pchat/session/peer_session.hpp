#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

#include "i2pchat/protocol/codec.hpp"
#include "i2pchat/session/handshake.hpp"
#include "i2pchat/session/secure_channel.hpp"

namespace i2pchat::session {

enum class PeerState {
    Disconnected,
    /// A stream to the peer is being opened. Set by the session manager; a
    /// `PeerSession` itself only exists once there is a stream.
    Connecting,
    /// TCP stream up, identities exchanged, handshake in flight.
    Handshaking,
    /// Secure channel established.
    Secure,
    /// Secure but idle past the session TTL: usable, though the manager may be
    /// told to treat it as offline.
    Stale,
    Failed,
};

[[nodiscard]] std::string_view peer_state_name(PeerState state);

/// Which end of the stream we are, decided by who dialled.
enum class ConnectionDirection { Outbound, Inbound };

/// One action the transport must perform.
struct SessionAction {
    enum class Kind {
        /// Write these bytes to the peer stream.
        Send,
        /// Deliver a decrypted application frame upwards.
        Deliver,
        /// Tear the connection down. `reason` explains why.
        Disconnect,
        /// The channel just became secure.
        Established,
    };

    Kind kind = Kind::Send;
    Bytes bytes;
    char msg_type = 0;
    std::uint64_t msg_id = 0;
    std::string reason;
};

using SessionActions = std::vector<SessionAction>;

/// Confirms that a claimed base32 address resolves, in the router's netDB, to
/// the base64 destination presented alongside it.
///
/// This is not what authenticates the peer — the base32 is a hash of the
/// destination, so the two always agree by construction, and control of the
/// identity is proven by the handshake signature and the TOFU pin. The lookup
/// is a cheap sanity check that the address is really published, and it is
/// optional: with no verifier installed the session relies on the handshake
/// alone, as it must.
using IdentityVerifier =
    std::function<bool(const std::string& peer_addr, const std::string& dest_base64)>;

struct PeerSessionConfig {
    HandshakeConfig handshake;
    /// Local destination in I2P base64, sent as the identity preface.
    std::string local_dest_base64;
    ConnectionDirection direction = ConnectionDirection::Outbound;
    IdentityVerifier identity_verifier;
    protocol::PaddingProfile padding = protocol::PaddingProfile::Balanced;
};

/// Drives one peer connection: identity preface, handshake, then the encrypted
/// channel. Like HandshakeMachine it performs no I/O — it consumes received
/// bytes and returns actions — which is what makes the protocol rules below
/// testable without a network.
///
/// The rules it enforces, all of them taken from the deployed protocol:
///
///   * before the handshake completes, only `S`, `H`, `P` and `O` frames are
///     allowed; anything else is data smuggled past authentication;
///   * after it completes, a plaintext frame or another `H` frame is a
///     downgrade attempt;
///   * an `S` frame may not change the peer identity mid-session.
class PeerSession {
public:
    explicit PeerSession(PeerSessionConfig config);

    /// Bytes to send immediately after the stream opens.
    ///
    /// The outbound side sends a bare base64 line before its `S` frame: the
    /// accepting side reads that line to learn who is calling.
    SessionActions on_stream_open();

    /// Feed bytes received from the peer.
    SessionActions on_bytes(ByteView data);

    /// Encrypt and frame an outgoing application message. Only valid once the
    /// channel is secure.
    Bytes send_message(char msg_type, ByteView payload, std::uint64_t msg_id);

    /// Build a keepalive ping. The peer answers with `O`.
    Bytes build_keepalive(std::uint64_t msg_id);

    [[nodiscard]] PeerState state() const noexcept { return state_; }
    [[nodiscard]] const std::string& peer_addr() const noexcept {
        return config_.handshake.peer_addr;
    }
    [[nodiscard]] bool secure() const noexcept { return state_ == PeerState::Secure; }
    /// The handshake machine, absent on an inbound session until the peer's
    /// identity preface names who we are talking to.
    [[nodiscard]] const std::optional<HandshakeMachine>& handshake() const noexcept {
        return handshake_;
    }

    /// Frame types accepted before the secure channel exists.
    [[nodiscard]] static bool allowed_before_handshake(char msg_type) noexcept;

private:
    void handle_frame(const protocol::Frame& frame, SessionActions& actions);
    void handle_identity_frame(const std::string& body, SessionActions& actions);
    void handle_handshake_frame(const std::string& body, SessionActions& actions);
    [[nodiscard]] bool consume_identity_line(ByteView data, std::size_t& consumed,
                                             SessionActions& actions);

    PeerSessionConfig config_;
    /// Constructed once the peer address is known: outbound at stream open,
    /// inbound after the identity preface.
    std::optional<HandshakeMachine> handshake_;
    protocol::FrameReader reader_;
    std::optional<SecureChannel> channel_;
    PeerState state_ = PeerState::Disconnected;
    /// The inbound side must read the peer's base64 line before any frame.
    bool awaiting_identity_line_;
    bool peer_identity_confirmed_;
    Bytes line_buffer_;
};

}  // namespace i2pchat::session
