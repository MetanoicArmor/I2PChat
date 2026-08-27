#pragma once

#include <cstdint>
#include <optional>
#include <string_view>

#include "i2pchat/bytes.hpp"
#include "i2pchat/protocol/codec.hpp"

/// The encrypted layer that sits inside a vNext frame body:
///
///   SEQ(8 BE) | SecretBox(nonce24 || ciphertext || tag16) | HMAC-SHA256(32)
///
/// and the traffic-shaping padding envelope applied to the plaintext before
/// encryption.
namespace i2pchat::protocol {

inline constexpr std::string_view kPaddingEnvelopeMagic = "I2PPAD1";
inline constexpr std::size_t kPaddingBalancedBlock = 128;

enum class PaddingProfile {
    /// No padding. Frame length leaks the message length.
    Off,
    /// Wrap and pad to a multiple of 128 bytes. The default.
    Balanced,
};

/// Wrap and pad the plaintext:
/// `"I2PPAD1" | original_len(4 BE) | body | random`.
///
/// `Off` returns the body unchanged, which is what keeps the two profiles
/// interoperable: the receiver decides by looking for the magic.
Bytes apply_padding(ByteView body, PaddingProfile profile);

/// Unwrap a padded payload. A payload without the magic is returned unchanged,
/// so a peer with padding disabled still works.
///
/// Throws ProtocolError when the envelope is present but malformed.
Bytes remove_padding(ByteView payload);

/// Assemble the encrypted frame body. `nonce` is exposed only so tests can
/// replay fixtures; production callers omit it and get a fresh random nonce.
Bytes build_encrypted_payload(ByteView enc_key, ByteView mac_key, char msg_type,
                              std::uint64_t seq, std::uint64_t msg_id,
                              ByteView plaintext,
                              std::optional<ByteView> nonce = std::nullopt);

struct EncryptedPayload {
    std::uint64_t seq = 0;
    ByteView sealed;  // SecretBox output, references the caller's buffer
    ByteView mac;
};

/// Split an encrypted frame body into its parts without copying.
/// Throws ProtocolError when the body is too short to hold seq and MAC.
EncryptedPayload split_encrypted_payload(ByteView body);

}  // namespace i2pchat::protocol
