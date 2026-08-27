#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"
#include "i2pchat/groups/models.hpp"

/// Group messages as they travel: a prefixed canonical-JSON document inside an
/// ordinary text frame.
///
/// Two versions are live.
///
///   * v1 is per-recipient over the live secure channel. The peer is already
///     authenticated by the handshake, so nothing here is signed; the recipient
///     and delivery ids name one leg of the fan-out.
///   * v3 is one blob for the whole group via BlindBox. There is no
///     authenticated channel, so the payload carries an Ed25519 signature over
///     its own canonical form and must not name a recipient.
///
/// v2 was v3 without the signature and is refused on read: accepting it would
/// let anyone holding the group root forge messages from any member.
namespace i2pchat::groups {

inline constexpr std::string_view kTransportPrefix = "__I2PCHAT_GROUP__:";
inline constexpr int kTransportVersionV1 = 1;
inline constexpr int kTransportVersionV2 = 2;
inline constexpr int kTransportVersionV3 = 3;
inline constexpr std::string_view kDeliveryScopeGroupBlindBox = "group_blindbox";
inline constexpr std::string_view kDeliveryScopeRecipient = "recipient";
/// Untrusted input is bounded before it reaches the JSON parser.
inline constexpr std::size_t kMaxTransportBytes = 512 * 1024;

class WireError : public std::runtime_error {
public:
    explicit WireError(const std::string& message) : std::runtime_error(message) {}
};

struct DecodedTransportMessage {
    GroupState state;
    GroupEnvelope envelope;
    /// Set for v1 only.
    std::optional<std::string> recipient_id;
    std::optional<std::string> delivery_id;
    /// Set for v3 only. The signature is already verified against `signer_key`
    /// by the time this is returned; binding the key to a member is the caller's
    /// job, since only it knows the group's pinned keys.
    std::optional<Bytes> signer_key;
    std::optional<Bytes> signature;
    int version = kTransportVersionV1;
    std::string delivery_scope{kDeliveryScopeRecipient};
};

/// The v1 form, addressed to one member.
[[nodiscard]] std::string encode_transport_v1(const GroupState& state,
                                             const GroupEnvelope& envelope,
                                             const RecipientDelivery& delivery);

/// Exactly the bytes a v3 message is signed over: the canonical payload without
/// the `signature` field. Callers sign this and pass the result to
/// `encode_transport_v3`.
[[nodiscard]] Bytes v3_signature_payload(const GroupState& state,
                                        const GroupEnvelope& envelope,
                                        ByteView signer_key);

[[nodiscard]] std::string encode_transport_v3(const GroupState& state,
                                             const GroupEnvelope& envelope,
                                             ByteView signer_key, ByteView signature);

/// Returns nothing when the text is not a group transport message at all, so a
/// caller can treat it as ordinary chat. Throws `WireError` when it looks like
/// one but is malformed, unsupported, or fails its signature.
[[nodiscard]] std::optional<DecodedTransportMessage> decode_transport(
    std::string_view text);

}  // namespace i2pchat::groups
