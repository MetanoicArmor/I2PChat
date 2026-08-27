#pragma once

#include <boost/asio/awaitable.hpp>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/blindbox/blob.hpp"
#include "i2pchat/blindbox/key_schedule.hpp"
#include "i2pchat/blindbox/replica_client.hpp"
#include "i2pchat/blindbox/state.hpp"

/// Offline delivery: what to send, where to look for what arrived, and when to
/// change the root secret everything hangs off.
///
/// The transport lives in `ReplicaClient` and the key derivation in
/// `key_schedule`; this is the part that decides. Every decision here is a plain
/// function over a snapshot and a timestamp, so the awkward cases — a rotation
/// the peer never confirmed, a message arriving under a root that has since been
/// replaced, two sides rotating at once — are testable without a network.
///
/// Root rotation is forward secrecy for the offline path: a root that has been
/// used for a while is replaced, and the old one is kept only long enough for
/// messages already in flight under it to be collected.
namespace i2pchat::blindbox {

namespace asio = boost::asio;

/// How much traffic analysis resistance to buy with bandwidth and latency.
enum class PrivacyProfile { Low, Medium, High };

[[nodiscard]] PrivacyProfile parse_privacy_profile(std::string_view text);
[[nodiscard]] std::string_view privacy_profile_name(PrivacyProfile profile);

struct PrivacySettings {
    double poll_min_seconds = 20.0;
    double poll_max_seconds = 30.0;
    /// Fetches for slots we do not expect anything in, so a replica cannot tell
    /// a real collection from an idle one.
    std::size_t cover_gets = 0;
    std::size_t padding_bucket = kDefaultPaddingBucket;
    /// Rotate the root after this many sent messages, or this many seconds,
    /// whichever comes first.
    std::uint64_t root_rotate_messages = 1024;
    std::int64_t root_rotate_seconds = 24 * 3600;
    /// How long a replaced root stays usable for collection.
    std::int64_t previous_grace_seconds = 24 * 3600;
    std::size_t max_previous_roots = 1;
};

[[nodiscard]] PrivacySettings privacy_settings(PrivacyProfile profile);

struct CoordinatorConfig {
    PrivacySettings privacy = privacy_settings(PrivacyProfile::Low);
    /// Slots below `recv_base` to re-examine. Nonzero only helps after state was
    /// restored from a backup that had already consumed them.
    std::size_t recv_backtrack = 0;
    /// How far above `recv_base` to look, independent of the stored window.
    std::size_t recv_lookahead = 64;
    std::size_t recv_max_per_poll = 64;
    std::size_t max_frame_size = kMaxBlobFrameSize;
};

/// Which of a peer's roots a received blob might have been sealed under, newest
/// first.
struct RootCandidate {
    std::uint64_t epoch = 0;
    Bytes secret;
};

struct GroupRootCandidate {
    std::uint64_t group_epoch = 0;
    std::uint64_t root_epoch = 0;
    Bytes secret;
};

[[nodiscard]] std::vector<RootCandidate> root_candidates(const PeerSnapshot& snapshot,
                                                         const CoordinatorConfig& config,
                                                         std::int64_t now);
[[nodiscard]] std::vector<GroupRootCandidate> group_root_candidates(
    const GroupSnapshot& snapshot, const CoordinatorConfig& config, std::int64_t now);

/// The slots worth asking about this poll: forward from `recv_base` first, then
/// any backtrack, capped so one poll cannot turn into hundreds of requests.
[[nodiscard]] std::vector<std::uint64_t> recv_candidates(const BlindBoxState& state,
                                                         const CoordinatorConfig& config);

/// True when the root has been in use long enough, or for enough messages, to
/// be replaced.
[[nodiscard]] bool should_rotate_root(const PeerSnapshot& snapshot,
                                      const CoordinatorConfig& config,
                                      std::int64_t now);
[[nodiscard]] bool should_rotate_root(const GroupSnapshot& snapshot,
                                      const CoordinatorConfig& config,
                                      std::int64_t now);

/// Which side offers the root, so both do not offer at once. The lexicographic
/// lower id wins — arbitrary but agreed without a round trip.
[[nodiscard]] bool initiates_root_exchange(std::string_view local_peer_id,
                                           std::string_view remote_peer_id);

/// The member responsible for a group's root: the lowest member id. Empty when
/// the group has no members.
[[nodiscard]] std::string group_root_coordinator(const std::vector<std::string>& members);

struct PendingRoot {
    std::uint64_t epoch = 0;
    Bytes secret;
    /// "initialized" for a first root, "rotated" for a replacement. Reported to
    /// the peer and shown in diagnostics.
    std::string reason;
    /// False when an existing pending root was returned instead of a new one,
    /// which is how a re-offer resends the same secret rather than a new one.
    bool created = false;
};

/// The root to offer the peer, creating one when the channel has none or the
/// current one is due for replacement. Nothing when the current root is fine.
[[nodiscard]] std::optional<PendingRoot> ensure_pending_root(
    PeerSnapshot& snapshot, const CoordinatorConfig& config, std::int64_t now,
    bool force_rotate = false);

/// Adopt the pending root once the peer confirms its epoch.
///
/// The replaced root moves into the previous list with a grace period, so a
/// message sent just before the change can still be collected. Returns false
/// when the acknowledged epoch is not the pending one, which is what makes a
/// stale or forged acknowledgement harmless.
[[nodiscard]] bool commit_pending_root(PeerSnapshot& snapshot,
                                       std::uint64_t acked_epoch,
                                       const CoordinatorConfig& config,
                                       std::int64_t now);

/// The group equivalent. `target_members` are those the root must reach; a
/// group root is adopted only once every one of them has confirmed, since a
/// member who missed it could read nothing.
[[nodiscard]] std::optional<PendingRoot> ensure_pending_group_root(
    GroupSnapshot& snapshot, std::uint64_t group_epoch,
    const std::vector<std::string>& target_members, const CoordinatorConfig& config,
    std::int64_t now, bool force_rotate = false);

/// Record one member's acknowledgement. Returns true when it was for the
/// pending epoch and from a targeted member.
[[nodiscard]] bool record_group_root_ack(GroupSnapshot& snapshot,
                                         std::uint64_t acked_epoch,
                                         std::string_view member_id);

/// True when every target has acknowledged.
[[nodiscard]] bool group_root_fully_acked(const GroupSnapshot& snapshot);

[[nodiscard]] bool commit_pending_group_root(GroupSnapshot& snapshot,
                                             const CoordinatorConfig& config,
                                             std::int64_t now);

/// Drop the current root and offer a new one, for when a member leaves a group:
/// whoever left must not be able to read what follows.
void invalidate_root_for_departed_member(PeerSnapshot& snapshot,
                                         const CoordinatorConfig& config,
                                         std::int64_t now);

struct SendOutcome {
    std::uint64_t index = 0;
    std::uint64_t epoch = 0;
    std::string lookup_token;
    std::vector<PutResult> replicas;
};

/// Seal `frame` for the peer and store it on the replicas.
///
/// The send index only advances once a quorum has accepted: a failed send must
/// leave the slot free, or the retry would land in a slot the peer is not
/// looking at. Throws `ReplicaError` when the quorum is not reached and
/// `BlindBoxError` when the channel has no root yet.
[[nodiscard]] asio::awaitable<SendOutcome> send_pairwise(
    ReplicaClient& client, PeerSnapshot& snapshot, std::string local_peer_id,
    Bytes frame, CoordinatorConfig config, std::int64_t now = 0);

/// Seal `frame` for a group and store it on the replicas.
[[nodiscard]] asio::awaitable<SendOutcome> send_group(
    ReplicaClient& client, GroupSnapshot& snapshot, std::string sender_id, Bytes frame,
    CoordinatorConfig config, std::int64_t now = 0);

struct ReceivedMessage {
    Bytes frame;
    std::uint64_t index = 0;
    std::uint64_t epoch = 0;
    std::string lookup_token;
};

/// Collect whatever is waiting for this peer.
///
/// Every candidate slot is tried against every candidate root, so a message
/// sealed under a root that has since been replaced is still read. Consumed
/// slots are recorded on `snapshot`, which is what stops a blob still sitting on
/// a replica from being delivered twice.
[[nodiscard]] asio::awaitable<std::vector<ReceivedMessage>> poll_pairwise(
    ReplicaClient& client, PeerSnapshot& snapshot, std::string local_peer_id,
    CoordinatorConfig config, std::int64_t now = 0);

[[nodiscard]] asio::awaitable<std::vector<ReceivedMessage>> poll_group(
    ReplicaClient& client, GroupSnapshot& snapshot, std::string sender_id,
    CoordinatorConfig config, std::int64_t now = 0);

/// Fetch slots nothing is expected in, so an observer cannot tell a collection
/// from an idle poll. Failures are ignored: a cover request has no result worth
/// reporting.
[[nodiscard]] asio::awaitable<void> emit_cover_gets(ReplicaClient& client,
                                                    std::size_t count);

}  // namespace i2pchat::blindbox
