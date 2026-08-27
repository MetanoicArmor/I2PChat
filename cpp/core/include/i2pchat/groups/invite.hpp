#pragma once

#include <chrono>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/groups/wire.hpp"

/// Shareable group invites.
///
/// An invite is a roster snapshot signed by the inviter's handshake signing key
/// — the same key that is TOFU-pinned during the secure handshake — so accepting
/// one binds the group's membership to an identity the invitee can verify.
///
/// The signed JSON is then sealed:
///
///   token = base64url_nopad( wrap_key(32) || SecretBox(JSON) )
///
/// The wrap key travels with the ciphertext, so the seal is not a secret: it
/// only means the token has no recognisable prefix and does not show the title,
/// members or destinations to anyone glancing at it. Whoever holds the whole
/// token can open it.
///
/// v1 invites were unsigned and are refused.
namespace i2pchat::groups {

inline constexpr std::string_view kInvitePrefix = "__I2PCHAT_GROUP_INVITE__:";
inline constexpr int kInviteVersion = 2;
inline constexpr std::size_t kMaxInviteBytes = 256 * 1024;
inline constexpr std::string_view kInviteSignatureDomain = "I2PCHAT-GROUP-INVITE-v2";

struct GroupInvite {
    std::string invite_id;
    std::string group_id;
    /// Normalised and deduplicated; the inviter is always present.
    std::vector<std::string> members;
    std::uint64_t epoch = 0;
    std::string inviter_id;
    std::string title;
    /// ISO-8601 UTC. Kept as the exact string from the payload, because the
    /// signature covers those bytes and not a re-formatted timestamp.
    std::string created_at;
    /// Empty when the invite does not expire.
    std::string expires_at;
    /// Lowercase hex, 64 characters.
    std::string inviter_signing_pub;
    /// Lowercase hex, 128 characters.
    std::string signature;
    int version = kInviteVersion;
};

/// The exact bytes signed by the inviter: the domain label, a `|`, then the
/// canonical JSON of the invite without its signature.
[[nodiscard]] Bytes invite_signature_payload(const GroupInvite& invite);

/// Sign and seal, returning the shareable token.
///
/// `signing_seed` is the inviter's 32-byte handshake signing seed. `members`
/// gains the inviter if it does not already list them, and `inviter_signing_pub`
/// is derived from the seed rather than taken from the input.
[[nodiscard]] std::string encode_invite(const GroupInvite& invite, ByteView signing_seed);

/// Open a token or a legacy `__I2PCHAT_GROUP_INVITE__:` prefixed document,
/// verifying the signature. Throws `WireError` on anything malformed, expired or
/// unsigned.
///
/// `now` is the reference for the expiry check; the default is the system clock.
[[nodiscard]] GroupInvite decode_invite(
    std::string_view text,
    std::optional<std::chrono::system_clock::time_point> now = std::nullopt);

/// A cheap check for whether text is worth handing to `decode_invite`, for
/// deciding whether a pasted string is an invite or a chat message.
[[nodiscard]] bool looks_like_invite(std::string_view text);

}  // namespace i2pchat::groups
