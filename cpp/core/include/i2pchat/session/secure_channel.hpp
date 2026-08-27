#pragma once

#include <chrono>
#include <cstdint>
#include <optional>
#include <string>

#include "i2pchat/bytes.hpp"
#include "i2pchat/protocol/codec.hpp"
#include "i2pchat/protocol/secure_frame.hpp"
#include "i2pchat/session/handshake.hpp"

namespace i2pchat::session {

/// Keepalive cadence and liveness timeout on an established channel.
inline constexpr std::chrono::seconds kKeepaliveInterval{15};
inline constexpr std::chrono::seconds kLivenessTimeout{90};
inline constexpr std::chrono::seconds kHandshakeTimeout{90};

/// The encrypted channel on top of an established handshake.
///
/// Owns the send/receive sequence counters and enforces the rules that make the
/// channel actually secure rather than merely encrypted:
///
///   * a plaintext application frame after the handshake is a downgrade attempt;
///   * sequence numbers must be consecutive, so a replay, a gap or a reorder is
///     fatal rather than recoverable;
///   * the MAC is checked before the ciphertext is touched.
///
/// Every one of these is a protocol violation that tears the session down: on a
/// channel that is supposed to be authenticated, "recovering" from one means
/// accepting attacker-chosen input.
class SecureChannel {
public:
    SecureChannel(SessionKeys keys, protocol::PaddingProfile padding =
                                        protocol::PaddingProfile::Balanced);

    /// Encrypt and frame an application message.
    Bytes encrypt_frame(char msg_type, ByteView plaintext, std::uint64_t msg_id);

    /// Verify and decrypt a received frame.
    ///
    /// Throws ProtocolError when the frame is unencrypted, fails its MAC, or
    /// carries a sequence number other than the next one expected.
    Bytes decrypt_frame(const protocol::Frame& frame);

    /// Frame types that remain legal in plaintext after the handshake: the
    /// handshake channel itself (`H`) and nothing else.
    [[nodiscard]] static bool plaintext_allowed_after_handshake(char msg_type) noexcept {
        return msg_type == 'H';
    }

    [[nodiscard]] std::uint64_t send_seq() const noexcept { return send_seq_; }
    [[nodiscard]] std::uint64_t last_recv_seq() const noexcept { return last_recv_seq_; }
    [[nodiscard]] protocol::PaddingProfile padding() const noexcept { return padding_; }

private:
    SessionKeys keys_;
    protocol::PaddingProfile padding_;
    std::uint64_t send_seq_ = 0;
    std::uint64_t last_recv_seq_ = 0;
};

}  // namespace i2pchat::session
