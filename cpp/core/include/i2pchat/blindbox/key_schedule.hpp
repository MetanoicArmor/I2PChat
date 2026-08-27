#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Deterministic per-message key derivation for offline delivery.
///
/// A BlindBox message is dropped at a replica under an opaque lookup token and
/// picked up later. Both sides derive the token and the keys from a shared root
/// secret and a message index, so the replica learns nothing but an opaque name
/// and a blob.
///
/// Domain separation is strict: the lookup token, the blob key and the state tag
/// come from three different labels over the same context, and the context binds
/// the peer pair (or the group and sender), the direction, the epoch and the
/// index. Getting any part of that context wrong yields keys that silently
/// disagree with the reference implementation, so this is vector-tested.
namespace i2pchat::blindbox {

class BlindBoxError : public std::runtime_error {
public:
    explicit BlindBoxError(const std::string& message) : std::runtime_error(message) {}
};

enum class Direction { Send, Recv };

/// `send` and `recv` as they appear on the wire and in stored state.
[[nodiscard]] std::string_view direction_name(Direction direction);
[[nodiscard]] Direction parse_direction(std::string_view text);

struct MessageKeys {
    /// Hex SHA-256 of the lookup key: what the replica sees.
    std::string lookup_token;
    Bytes lookup_key;
    Bytes blob_key;
    /// 16 bytes, carried inside the blob so a replayed or reordered blob can be
    /// told apart from the expected one.
    Bytes state_tag;
    /// `LOW_TO_HIGH` or `HIGH_TO_LOW` for a pair, `GROUP_SEND` / `GROUP_RECV`
    /// for a group.
    std::string direction_label;
    std::uint64_t index = 0;
    std::uint64_t epoch = 0;
};

struct GroupMessageKeys {
    std::string lookup_token;
    Bytes lookup_key;
    Bytes blob_key;
    Bytes state_tag;
    std::string direction_label;
    std::uint64_t index = 0;
    std::uint64_t group_epoch = 0;
    std::uint64_t root_epoch = 0;
    std::string sender_id;
};

/// Canonical peer id for the schedule: trimmed, lowercased, `.b32.i2p` removed.
/// Throws when empty, because an empty id would collapse two peers onto one
/// keyspace.
[[nodiscard]] std::string normalize_blindbox_peer_id(std::string_view peer_id);

/// The pair ordered so both sides derive the same context regardless of who is
/// asking. Throws when the two ids are equal.
[[nodiscard]] std::pair<std::string, std::string> canonical_pair(
    std::string_view local_peer_id, std::string_view remote_peer_id);

/// Derive the keys for one pairwise message.
///
/// `direction` is from the caller's point of view: the sender's `send` at index
/// `i` and the receiver's `recv` at index `i` produce identical keys.
[[nodiscard]] MessageKeys derive_message_keys(ByteView root_secret,
                                             std::string_view local_peer_id,
                                             std::string_view remote_peer_id,
                                             Direction direction, std::uint64_t index,
                                             std::uint64_t epoch = 0);

/// Derive the keys for one group message.
///
/// The sender is bound into the schedule so each member owns a disjoint slot
/// range on the shared group root. Without that, any member holding the root
/// could squat another member's slots or forge blobs attributed to them.
[[nodiscard]] GroupMessageKeys derive_group_message_keys(
    ByteView root_secret, std::string_view group_id, Direction direction,
    std::uint64_t index, std::uint64_t group_epoch, std::uint64_t root_epoch,
    std::string_view sender_id);

}  // namespace i2pchat::blindbox
