#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

#include "i2pchat/bytes.hpp"

/// The BlindBox state a client keeps on disk between runs.
///
/// Two things live here. The message counters — how far the send index has got,
/// which received indexes have already been consumed — decide which slots the
/// key schedule derives next, so losing them either replays an old message or
/// skips a waiting one. And the root secrets those slots hang off, which are the
/// only thing in the file worth stealing and are therefore wrapped under a key
/// derived from the local signing seed.
///
/// The file itself is plain JSON, matching the reference implementation: the
/// counters are not secret, and keeping them readable is what lets a user see
/// and repair their own state.
namespace i2pchat::blindbox {

inline constexpr std::string_view kStateVersion = "BLINDBOX_STATE_V1";
/// Derived from the profile and peer alone. Read-only: state written by this
/// client always uses v2, but state left by an older release must still open.
inline constexpr int kLocalWrapVersionLegacy = 1;
/// Binds the local signing seed, so a copied state file is useless without the
/// identity that wrote it.
inline constexpr int kLocalWrapVersionCurrent = 2;
inline constexpr std::size_t kDefaultRecvWindow = 16;
inline constexpr std::size_t kMaxRecvWindow = 4096;

/// Message counters for one channel, pairwise or group.
struct BlindBoxState {
    /// The next index this side will send at.
    std::uint64_t send_index = 0;
    /// The lowest index not yet consumed. Slots below it are settled.
    std::uint64_t recv_base = 0;
    /// How far above `recv_base` to look for a waiting message. A wider window
    /// costs a replica request per slot.
    std::size_t recv_window = kDefaultRecvWindow;
    /// Consumed indexes at or above `recv_base`. What stops a blob that is
    /// still sitting on a replica from being delivered twice.
    std::set<std::uint64_t> consumed_recv;
    std::int64_t updated_at = 0;

    /// Record `index` as delivered and settle everything now contiguous.
    void mark_consumed(std::uint64_t index, std::int64_t now = 0);
    void advance_recv_base();

    /// The indexes worth asking a replica about, in order.
    [[nodiscard]] std::vector<std::uint64_t> pending_recv_indexes() const;

    [[nodiscard]] nlohmann::json to_json() const;
    /// Throws `BlindBoxError` on an unknown version or an out-of-range window,
    /// rather than guessing: a misread state file replays messages.
    [[nodiscard]] static BlindBoxState from_json(const nlohmann::json& value);
};

/// A root secret that has been replaced but whose messages may still arrive.
struct PreviousRoot {
    std::uint64_t epoch = 0;
    Bytes secret;
    std::int64_t expires_at = 0;
};

/// Everything stored for one pairwise channel.
struct PeerSnapshot {
    std::string peer_id;
    BlindBoxState state;

    /// Absent until the peers have agreed a root over the live channel.
    std::optional<Bytes> root_secret;
    std::uint64_t root_epoch = 0;
    std::int64_t root_created_at = 0;
    /// The send index at which this root took over, so rotation does not reuse
    /// a slot that the previous root already spent.
    std::uint64_t root_send_index_base = 0;

    /// A root offered but not yet confirmed by the peer. Kept separate so a
    /// rotation that the peer never received cannot strand messages.
    std::optional<Bytes> pending_root_secret;
    std::uint64_t pending_root_epoch = 0;
    std::int64_t pending_root_created_at = 0;
    std::uint64_t pending_root_send_index_base = 0;

    std::vector<PreviousRoot> prev_roots;

    /// Which wrap version the loaded file used, so a legacy file can be
    /// recognised and rewritten.
    int wrap_version = kLocalWrapVersionCurrent;
};

/// A replaced group root, which also records the membership epoch it belonged
/// to: a member who left must not be able to read anything written after.
struct GroupPreviousRoot {
    std::uint64_t group_epoch = 0;
    std::uint64_t root_epoch = 0;
    Bytes secret;
    std::int64_t expires_at = 0;
};

/// Everything stored for one group channel. Lives inside the group's own
/// sealed record rather than a file of its own.
struct GroupSnapshot {
    std::string group_id;
    /// The BlindBox channel identity, distinct from the group id.
    std::string channel_id;
    std::uint64_t group_epoch = 0;
    BlindBoxState state;

    std::optional<Bytes> root_secret;
    std::uint64_t root_epoch = 0;
    std::int64_t root_created_at = 0;
    std::uint64_t root_send_index_base = 0;

    std::optional<Bytes> pending_root_secret;
    std::uint64_t pending_root_epoch = 0;
    std::int64_t pending_root_created_at = 0;
    std::uint64_t pending_root_send_index_base = 0;
    /// Members the pending root was pushed to, and those that acknowledged it.
    /// A group root is only adopted once every target has confirmed, otherwise
    /// a member who missed the push would be unable to read anything.
    std::vector<std::string> pending_root_target_members;
    std::set<std::string> pending_root_acked_members;

    std::vector<GroupPreviousRoot> prev_roots;
    int wrap_version = kLocalWrapVersionCurrent;
};

/// The wrapping scope for a peer: lowercased, trimmed, `.b32.i2p` removed.
[[nodiscard]] std::string wrap_scope_for_peer(std::string_view peer_id);
/// The wrapping scope for a group: `group:<group_id>`.
[[nodiscard]] std::string wrap_scope_for_group(std::string_view group_id);

/// Derive the key that wraps root secrets for one scope.
///
/// v2 binds the local signing seed; v1 does not and exists only to open state
/// written before that changed.
[[nodiscard]] Bytes local_wrap_key(std::string_view profile, std::string_view scope,
                                   ByteView signing_seed,
                                   int wrap_version = kLocalWrapVersionCurrent);

/// Wrap a root secret for storage, as lowercase hex.
[[nodiscard]] std::string encrypt_root_secret(ByteView root_secret,
                                              std::string_view profile,
                                              std::string_view scope,
                                              ByteView signing_seed);

/// Unwrap a stored root secret, trying `wrap_version` first and then every
/// other known version. Returns the secret and the version that opened it, so
/// a caller can tell that a rewrite is due.
[[nodiscard]] std::pair<Bytes, int> decrypt_root_secret(
    std::string_view encrypted_hex, std::string_view profile, std::string_view scope,
    ByteView signing_seed, std::optional<int> wrap_version = std::nullopt);

/// The state file name for one peer: `{profile}.blindbox.{safe_peer}.json`,
/// where the peer id is lowercased and everything outside `[a-z0-9._-]` becomes
/// an underscore.
[[nodiscard]] std::string peer_state_filename(std::string_view profile,
                                              std::string_view peer_id);

/// How long a replaced root stays usable for incoming messages.
inline constexpr std::int64_t kPreviousRootRetentionSeconds = 7 * 24 * 3600;

/// Drop expired previous roots and keep at most `max_roots`, newest first.
[[nodiscard]] std::vector<PreviousRoot> prune_previous_roots(
    std::vector<PreviousRoot> roots, std::size_t max_roots, std::int64_t now);

[[nodiscard]] std::vector<GroupPreviousRoot> prune_previous_roots(
    std::vector<GroupPreviousRoot> roots, std::size_t max_roots, std::int64_t now);

/// Read one peer's state. Returns an empty snapshot when the file is absent,
/// which is the ordinary first-contact case.
[[nodiscard]] PeerSnapshot load_peer_snapshot(const std::filesystem::path& path,
                                             std::string_view peer_id,
                                             std::string_view profile,
                                             ByteView signing_seed);

/// Write one peer's state, wrapping the roots under the current version.
///
/// A snapshot with no root at all is not written: there would be nothing to
/// protect and an empty file would only mask first contact.
void save_peer_snapshot(const std::filesystem::path& path, const PeerSnapshot& snapshot,
                        std::string_view profile, ByteView signing_seed);

/// Serialise a peer snapshot without touching the filesystem.
[[nodiscard]] nlohmann::json peer_snapshot_to_json(const PeerSnapshot& snapshot,
                                                   std::string_view profile,
                                                   ByteView signing_seed);
[[nodiscard]] PeerSnapshot peer_snapshot_from_json(const nlohmann::json& value,
                                                   std::string_view peer_id,
                                                   std::string_view profile,
                                                   ByteView signing_seed);

/// The same pair for a group, wrapped under `group:<group_id>`.
[[nodiscard]] nlohmann::json group_snapshot_to_json(const GroupSnapshot& snapshot,
                                                    std::string_view profile,
                                                    ByteView signing_seed);
[[nodiscard]] GroupSnapshot group_snapshot_from_json(const nlohmann::json& value,
                                                     std::string_view group_id,
                                                     std::string_view profile,
                                                     ByteView signing_seed);

}  // namespace i2pchat::blindbox
