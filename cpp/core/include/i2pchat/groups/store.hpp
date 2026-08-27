#pragma once

#include <cstdint>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "i2pchat/blindbox/state.hpp"
#include "i2pchat/bytes.hpp"
#include "i2pchat/groups/models.hpp"
#include "i2pchat/storage/profile_paths.hpp"

/// One group conversation as it lives on disk: membership, history, deliveries
/// that have not landed yet, and the group's BlindBox channel.
///
/// The file layer belongs to `storage::read_group_record`; this module owns the
/// payload, which the reference implementation writes from
/// `i2pchat/storage/group_store.py`. Field names and defaults follow it exactly,
/// because a record written by either client must open in the other.
namespace i2pchat::groups {

inline constexpr int kGroupRecordVersion = 1;
/// How many message ids the duplicate filter remembers. Past this the oldest are
/// forgotten, which risks re-displaying a very old replayed message but bounds
/// the file.
inline constexpr std::size_t kMaxSeenMessageIds = 4096;

/// One line of the conversation, sent or received.
struct HistoryEntry {
    /// `me` for locally sent, `peer` for everything else.
    std::string kind = "peer";
    std::string sender_id;
    ContentType content_type = ContentType::GroupText;
    /// The text of a `GROUP_TEXT` entry, empty for control payloads.
    std::string text;
    /// Mirrors `text` for `GROUP_TEXT`, the control object otherwise.
    nlohmann::json payload;
    std::string msg_id;
    std::uint64_t group_seq = 0;
    std::uint64_t epoch = 0;
    /// ISO-8601 UTC, as written by the client that created the entry.
    std::string created_at;
    /// The authenticated peer the message arrived from, which need not be the
    /// sender: group traffic is relayed.
    std::string source_peer;
    /// Per-recipient delivery outcome and its reason, for locally sent entries.
    std::map<std::string, std::string> delivery_results;
    std::map<std::string, std::string> delivery_reasons;
};

/// A fan-out leg that has not been delivered, kept so a retry survives a
/// restart. Carries its own copy of the group view because the group may have
/// moved on by the time the retry runs.
struct PendingDelivery {
    std::string group_id;
    std::string group_title;
    std::vector<std::string> group_members;
    std::string sender_id;
    std::string recipient_id;
    std::string delivery_id;
    std::string msg_id;
    std::uint64_t group_seq = 0;
    std::uint64_t epoch = 0;
    ContentType content_type = ContentType::GroupText;
    nlohmann::json payload;
    std::string created_at;

    [[nodiscard]] GroupState as_group_state() const;
    [[nodiscard]] GroupEnvelope as_envelope() const;
    [[nodiscard]] RecipientDelivery as_delivery() const;
};

/// A group message waiting for the group root, to be sealed into the group
/// BlindBox channel once every member has one. Unlike `PendingDelivery` this is
/// group-wide rather than per-recipient.
struct PendingBlindBoxMessage {
    std::string group_id;
    std::string group_title;
    std::vector<std::string> group_members;
    std::string sender_id;
    std::string msg_id;
    std::uint64_t group_seq = 0;
    std::uint64_t epoch = 0;
    ContentType content_type = ContentType::GroupText;
    nlohmann::json payload;
    std::string created_at;

    [[nodiscard]] GroupState as_group_state() const;
    [[nodiscard]] GroupEnvelope as_envelope() const;
};

struct StoredConversation {
    GroupState state;
    /// ISO-8601 UTC. Not part of `GroupState`, which is the signed view of the
    /// group and carries no timestamps.
    std::string created_at;
    std::string updated_at;
    /// The next sequence number this client will assign. Never below 1, and
    /// never at or below the highest sequence already in `history`.
    std::uint64_t next_group_seq = 1;
    std::vector<HistoryEntry> history;
    std::vector<std::string> seen_msg_ids;
    std::vector<PendingDelivery> pending_deliveries;
    std::optional<blindbox::GroupSnapshot> blindbox_channel;
    std::vector<PendingBlindBoxMessage> pending_blindbox_messages;

    /// Apply the invariants the reference implementation enforces on
    /// construction: sequence floor, duplicate filter contents and cap, and
    /// dropping pending entries with no id or a repeated one.
    void normalize();
};

/// Serialise for storage. `signing_seed` wraps the channel's root secrets; it is
/// unused when there is no channel.
[[nodiscard]] nlohmann::json conversation_to_json(const StoredConversation& conversation,
                                                  std::string_view profile,
                                                  ByteView signing_seed);

/// Throws `storage::SealedJsonError` on an unsupported record version.
[[nodiscard]] StoredConversation conversation_from_json(const nlohmann::json& payload,
                                                        std::string_view profile,
                                                        ByteView signing_seed);

/// Returns nothing when this profile has no record for the group.
[[nodiscard]] std::optional<StoredConversation> load_conversation(
    const storage::ProfilePaths& paths, std::string_view group_id,
    std::optional<ByteView> identity_key, ByteView signing_seed);

void save_conversation(const storage::ProfilePaths& paths,
                       const StoredConversation& conversation,
                       std::optional<ByteView> identity_key, ByteView signing_seed);

/// Replace the group view, keeping history and everything pending. Used when
/// membership or the epoch changes.
[[nodiscard]] StoredConversation upsert_state(
    const storage::ProfilePaths& paths, const GroupState& state,
    std::optional<ByteView> identity_key, ByteView signing_seed,
    std::optional<std::uint64_t> next_group_seq = std::nullopt);

/// Append one entry unless its message id has been seen. The flag says whether
/// it was appended; the record is written either way, since the group view may
/// have changed even when the message is a duplicate.
[[nodiscard]] std::pair<StoredConversation, bool> append_history(
    const storage::ProfilePaths& paths, const GroupState& state,
    const HistoryEntry& entry, std::optional<ByteView> identity_key,
    ByteView signing_seed, std::optional<std::uint64_t> next_group_seq = std::nullopt);

/// Every group this profile has a readable record for, most recently updated
/// first. Unreadable records are skipped rather than failing the whole listing.
[[nodiscard]] std::vector<GroupState> list_states(const storage::ProfilePaths& paths,
                                                  std::optional<ByteView> identity_key,
                                                  ByteView signing_seed);

/// Returns whether a file was removed.
bool delete_record(const storage::ProfilePaths& paths, std::string_view group_id);

}  // namespace i2pchat::groups
