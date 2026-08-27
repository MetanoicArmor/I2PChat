#include "i2pchat/blindbox/coordinator.hpp"

#include <algorithm>
#include <chrono>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

namespace i2pchat::blindbox {
namespace {

std::int64_t system_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::int64_t resolve_now(std::int64_t now) { return now != 0 ? now : system_seconds(); }

std::string lowered(std::string_view text) {
    std::string result(text);
    std::transform(result.begin(), result.end(), result.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return result;
}

std::string trimmed_lower(std::string_view text) {
    std::string result = lowered(text);
    const auto begin = result.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return {};
    }
    const auto end = result.find_last_not_of(" \t\r\n");
    return result.substr(begin, end - begin + 1);
}

/// Strip the `.b32.i2p` suffix so two spellings of one destination compare
/// equal, which is what the reference's same-destination check amounts to.
std::string comparable_id(std::string_view text) {
    std::string result = trimmed_lower(text);
    static constexpr std::string_view kSuffix = ".b32.i2p";
    if (result.size() > kSuffix.size() && result.ends_with(kSuffix)) {
        result.resize(result.size() - kSuffix.size());
    }
    return result;
}

bool rotation_due(std::optional<Bytes> root_secret, std::int64_t root_created_at,
                  std::uint64_t send_index, std::uint64_t send_index_base,
                  const CoordinatorConfig& config, std::int64_t now) {
    if (!root_secret) {
        return false;
    }
    const std::int64_t moment = resolve_now(now);
    const std::int64_t created = root_created_at != 0 ? root_created_at : moment;
    const std::int64_t elapsed = std::max<std::int64_t>(0, moment - created);
    const std::uint64_t sent =
        send_index > send_index_base ? send_index - send_index_base : 0;
    return elapsed >= config.privacy.root_rotate_seconds ||
           sent >= config.privacy.root_rotate_messages;
}

}  // namespace

PrivacyProfile parse_privacy_profile(std::string_view text) {
    const std::string value = trimmed_lower(text);
    if (value == "medium") {
        return PrivacyProfile::Medium;
    }
    if (value == "high") {
        return PrivacyProfile::High;
    }
    // Anything unrecognised is the default rather than an error: a bad
    // environment variable must not stop the client from starting.
    return PrivacyProfile::Low;
}

std::string_view privacy_profile_name(PrivacyProfile profile) {
    switch (profile) {
        case PrivacyProfile::Medium:
            return "medium";
        case PrivacyProfile::High:
            return "high";
        case PrivacyProfile::Low:
            break;
    }
    return "low";
}

PrivacySettings privacy_settings(PrivacyProfile profile) {
    PrivacySettings settings;
    switch (profile) {
        case PrivacyProfile::Low:
            settings.cover_gets = 0;
            settings.padding_bucket = 256;
            settings.root_rotate_messages = 1024;
            settings.root_rotate_seconds = 24 * 3600;
            settings.max_previous_roots = 1;
            break;
        case PrivacyProfile::Medium:
            settings.cover_gets = 1;
            settings.padding_bucket = 512;
            settings.root_rotate_messages = 512;
            settings.root_rotate_seconds = 12 * 3600;
            settings.max_previous_roots = 2;
            break;
        case PrivacyProfile::High:
            settings.cover_gets = 2;
            settings.padding_bucket = 1024;
            settings.root_rotate_messages = 256;
            settings.root_rotate_seconds = 6 * 3600;
            settings.max_previous_roots = 2;
            break;
    }
    return settings;
}

std::vector<RootCandidate> root_candidates(const PeerSnapshot& snapshot,
                                           const CoordinatorConfig& config,
                                           std::int64_t now) {
    std::vector<RootCandidate> candidates;
    if (snapshot.root_secret) {
        candidates.push_back(RootCandidate{snapshot.root_epoch, *snapshot.root_secret});
    }
    for (const PreviousRoot& root : prune_previous_roots(
             snapshot.prev_roots, config.privacy.max_previous_roots, resolve_now(now))) {
        candidates.push_back(RootCandidate{root.epoch, root.secret});
    }
    return candidates;
}

std::vector<GroupRootCandidate> group_root_candidates(const GroupSnapshot& snapshot,
                                                      const CoordinatorConfig& config,
                                                      std::int64_t now) {
    std::vector<GroupRootCandidate> candidates;
    if (snapshot.root_secret) {
        candidates.push_back(GroupRootCandidate{snapshot.group_epoch, snapshot.root_epoch,
                                                *snapshot.root_secret});
    }
    for (const GroupPreviousRoot& root : prune_previous_roots(
             snapshot.prev_roots, config.privacy.max_previous_roots, resolve_now(now))) {
        candidates.push_back(
            GroupRootCandidate{root.group_epoch, root.root_epoch, root.secret});
    }
    return candidates;
}

std::vector<std::uint64_t> recv_candidates(const BlindBoxState& state,
                                           const CoordinatorConfig& config) {
    const std::uint64_t span =
        std::max<std::uint64_t>(state.recv_window, config.recv_lookahead);
    std::vector<std::uint64_t> ordered;
    ordered.reserve(config.recv_max_per_poll);

    // Forward from the base first: the next message is far likelier to be there
    // than in a slot already passed.
    for (std::uint64_t index = state.recv_base; index < state.recv_base + span; ++index) {
        if (!state.consumed_recv.contains(index)) {
            ordered.push_back(index);
        }
    }
    const std::uint64_t start =
        state.recv_base > config.recv_backtrack ? state.recv_base - config.recv_backtrack : 0;
    for (std::uint64_t index = start; index < state.recv_base; ++index) {
        if (!state.consumed_recv.contains(index)) {
            ordered.push_back(index);
        }
    }
    if (ordered.size() > config.recv_max_per_poll) {
        ordered.resize(config.recv_max_per_poll);
    }
    return ordered;
}

bool should_rotate_root(const PeerSnapshot& snapshot, const CoordinatorConfig& config,
                        std::int64_t now) {
    return rotation_due(snapshot.root_secret, snapshot.root_created_at,
                        snapshot.state.send_index, snapshot.root_send_index_base, config,
                        now);
}

bool should_rotate_root(const GroupSnapshot& snapshot, const CoordinatorConfig& config,
                        std::int64_t now) {
    return rotation_due(snapshot.root_secret, snapshot.root_created_at,
                        snapshot.state.send_index, snapshot.root_send_index_base, config,
                        now);
}

bool initiates_root_exchange(std::string_view local_peer_id,
                             std::string_view remote_peer_id) {
    const std::string local = comparable_id(local_peer_id);
    const std::string remote = comparable_id(remote_peer_id);
    if (local.empty() || remote.empty()) {
        return false;
    }
    return local < remote;
}

std::string group_root_coordinator(const std::vector<std::string>& members) {
    std::string lowest;
    for (const std::string& member : members) {
        const std::string candidate = comparable_id(member);
        if (candidate.empty()) {
            continue;
        }
        if (lowest.empty() || candidate < lowest) {
            lowest = candidate;
        }
    }
    return lowest;
}

std::optional<PendingRoot> ensure_pending_root(PeerSnapshot& snapshot,
                                               const CoordinatorConfig& config,
                                               std::int64_t now, bool force_rotate) {
    if (snapshot.pending_root_secret) {
        // Re-offer the same secret rather than minting another: the peer may
        // simply not have answered yet, and a second secret would leave one of
        // them stranded.
        return PendingRoot{snapshot.pending_root_epoch, *snapshot.pending_root_secret,
                           snapshot.root_secret ? "rotated" : "initialized", false};
    }

    const bool bootstrap = !snapshot.root_secret.has_value();
    const bool rotate = force_rotate || should_rotate_root(snapshot, config, now);
    if (!bootstrap && !rotate) {
        return std::nullopt;
    }

    crypto::init();
    snapshot.pending_root_epoch =
        std::max(snapshot.root_epoch, snapshot.pending_root_epoch) + 1;
    snapshot.pending_root_secret = crypto::random_bytes(32);
    snapshot.pending_root_created_at = resolve_now(now);
    snapshot.pending_root_send_index_base = snapshot.state.send_index;
    return PendingRoot{snapshot.pending_root_epoch, *snapshot.pending_root_secret,
                       bootstrap ? "initialized" : "rotated", true};
}

bool commit_pending_root(PeerSnapshot& snapshot, std::uint64_t acked_epoch,
                         const CoordinatorConfig& config, std::int64_t now) {
    if (!snapshot.pending_root_secret) {
        return false;
    }
    // A confirmation for any other epoch is stale or forged, and adopting on it
    // would leave the two sides deriving different keys.
    if (acked_epoch != snapshot.pending_root_epoch) {
        return false;
    }

    const std::int64_t moment = resolve_now(now);
    if (snapshot.root_secret) {
        snapshot.prev_roots.insert(
            snapshot.prev_roots.begin(),
            PreviousRoot{snapshot.root_epoch, *snapshot.root_secret,
                         moment + config.privacy.previous_grace_seconds});
    }
    snapshot.root_secret = snapshot.pending_root_secret;
    snapshot.root_epoch = snapshot.pending_root_epoch;
    snapshot.root_created_at = moment;
    snapshot.root_send_index_base = snapshot.pending_root_send_index_base;
    snapshot.prev_roots = prune_previous_roots(std::move(snapshot.prev_roots),
                                               config.privacy.max_previous_roots, moment);

    snapshot.pending_root_secret.reset();
    snapshot.pending_root_epoch = 0;
    snapshot.pending_root_created_at = 0;
    snapshot.pending_root_send_index_base = 0;
    return true;
}

std::optional<PendingRoot> ensure_pending_group_root(
    GroupSnapshot& snapshot, std::uint64_t group_epoch,
    const std::vector<std::string>& target_members, const CoordinatorConfig& config,
    std::int64_t now, bool force_rotate) {
    if (target_members.empty()) {
        // A group of one has nobody to deliver to, and a root nobody shares is
        // only a liability.
        return std::nullopt;
    }

    if (snapshot.pending_root_secret && snapshot.group_epoch == group_epoch) {
        return PendingRoot{snapshot.pending_root_epoch, *snapshot.pending_root_secret,
                           snapshot.root_secret ? "rotated" : "initialized", false};
    }

    // A membership change makes the old root unusable regardless of its age: it
    // is what a departed member still holds.
    const bool bootstrap =
        !snapshot.root_secret.has_value() || snapshot.group_epoch != group_epoch;
    const bool rotate = force_rotate || should_rotate_root(snapshot, config, now);
    if (!bootstrap && !rotate) {
        return std::nullopt;
    }

    crypto::init();
    snapshot.pending_root_epoch =
        std::max(snapshot.root_epoch, snapshot.pending_root_epoch) + 1;
    snapshot.pending_root_secret = crypto::random_bytes(32);
    snapshot.pending_root_created_at = resolve_now(now);
    snapshot.pending_root_send_index_base = snapshot.state.send_index;
    snapshot.pending_root_target_members.clear();
    snapshot.pending_root_acked_members.clear();
    for (const std::string& member : target_members) {
        const std::string normalized = comparable_id(member);
        if (normalized.empty()) {
            continue;
        }
        if (std::find(snapshot.pending_root_target_members.begin(),
                      snapshot.pending_root_target_members.end(),
                      normalized) == snapshot.pending_root_target_members.end()) {
            snapshot.pending_root_target_members.push_back(normalized);
        }
    }
    snapshot.group_epoch = group_epoch;
    return PendingRoot{snapshot.pending_root_epoch, *snapshot.pending_root_secret,
                       bootstrap ? "initialized" : "rotated", true};
}

bool record_group_root_ack(GroupSnapshot& snapshot, std::uint64_t acked_epoch,
                           std::string_view member_id) {
    if (!snapshot.pending_root_secret || acked_epoch != snapshot.pending_root_epoch) {
        return false;
    }
    const std::string normalized = comparable_id(member_id);
    if (normalized.empty()) {
        return false;
    }
    // Only a targeted member counts: otherwise anyone could complete the quorum
    // on behalf of a member that never received the root.
    if (std::find(snapshot.pending_root_target_members.begin(),
                  snapshot.pending_root_target_members.end(),
                  normalized) == snapshot.pending_root_target_members.end()) {
        return false;
    }
    snapshot.pending_root_acked_members.insert(normalized);
    return true;
}

bool group_root_fully_acked(const GroupSnapshot& snapshot) {
    if (!snapshot.pending_root_secret || snapshot.pending_root_target_members.empty()) {
        return false;
    }
    return std::all_of(snapshot.pending_root_target_members.begin(),
                       snapshot.pending_root_target_members.end(),
                       [&snapshot](const std::string& member) {
                           return snapshot.pending_root_acked_members.contains(member);
                       });
}

bool commit_pending_group_root(GroupSnapshot& snapshot, const CoordinatorConfig& config,
                               std::int64_t now) {
    if (!group_root_fully_acked(snapshot)) {
        return false;
    }

    const std::int64_t moment = resolve_now(now);
    if (snapshot.root_secret) {
        snapshot.prev_roots.insert(
            snapshot.prev_roots.begin(),
            GroupPreviousRoot{snapshot.group_epoch, snapshot.root_epoch,
                              *snapshot.root_secret,
                              moment + config.privacy.previous_grace_seconds});
    }
    snapshot.root_secret = snapshot.pending_root_secret;
    snapshot.root_epoch = snapshot.pending_root_epoch;
    snapshot.root_created_at = moment;
    snapshot.root_send_index_base = snapshot.pending_root_send_index_base;
    snapshot.prev_roots = prune_previous_roots(std::move(snapshot.prev_roots),
                                               config.privacy.max_previous_roots, moment);

    snapshot.pending_root_secret.reset();
    snapshot.pending_root_epoch = 0;
    snapshot.pending_root_created_at = 0;
    snapshot.pending_root_send_index_base = 0;
    snapshot.pending_root_target_members.clear();
    snapshot.pending_root_acked_members.clear();
    return true;
}

void invalidate_root_for_departed_member(PeerSnapshot& snapshot,
                                         const CoordinatorConfig& config,
                                         std::int64_t now) {
    const std::int64_t moment = resolve_now(now);
    // The departed member holds the current root, so it is dropped outright
    // rather than kept for grace: nothing new may be readable with it.
    snapshot.root_secret.reset();
    snapshot.root_epoch = std::max(snapshot.root_epoch, snapshot.pending_root_epoch);
    snapshot.root_created_at = 0;
    snapshot.pending_root_secret.reset();
    snapshot.pending_root_epoch = 0;
    snapshot.prev_roots.clear();
    (void)ensure_pending_root(snapshot, config, moment, true);
}

namespace {

MessageKeys keys_for_send(const PeerSnapshot& snapshot, std::string_view local_peer_id,
                          std::uint64_t index) {
    if (!snapshot.root_secret) {
        throw BlindBoxError("BlindBox channel has no root secret yet");
    }
    return derive_message_keys(ByteView(*snapshot.root_secret), local_peer_id,
                               snapshot.peer_id, Direction::Send, index,
                               snapshot.root_epoch);
}

}  // namespace

asio::awaitable<SendOutcome> send_pairwise(ReplicaClient& client, PeerSnapshot& snapshot,
                                           std::string local_peer_id, Bytes frame,
                                           CoordinatorConfig config, std::int64_t now) {
    if (frame.empty()) {
        throw BlindBoxError("BlindBox frame is empty");
    }
    if (frame.size() > config.max_frame_size) {
        throw BlindBoxError("BlindBox frame exceeds the maximum size");
    }

    const std::uint64_t index = snapshot.state.send_index;
    const MessageKeys keys = keys_for_send(snapshot, local_peer_id, index);
    const Bytes blob =
        encrypt_blob(ByteView(frame), ByteView(keys.blob_key), Direction::Send, index,
                     ByteView(keys.state_tag), config.privacy.padding_bucket);

    std::vector<PutResult> replicas = co_await client.put(keys.lookup_token, blob);

    // Only now does the index advance. A send that failed must leave the slot
    // free, or the retry would land where the peer is not looking.
    snapshot.state.send_index = index + 1;
    snapshot.state.updated_at = resolve_now(now);

    co_return SendOutcome{index, snapshot.root_epoch, keys.lookup_token,
                          std::move(replicas)};
}

asio::awaitable<SendOutcome> send_group(ReplicaClient& client, GroupSnapshot& snapshot,
                                        std::string sender_id, Bytes frame,
                                        CoordinatorConfig config, std::int64_t now) {
    if (frame.empty()) {
        throw BlindBoxError("BlindBox frame is empty");
    }
    if (frame.size() > config.max_frame_size) {
        throw BlindBoxError("BlindBox frame exceeds the maximum size");
    }
    if (!snapshot.root_secret) {
        throw BlindBoxError("Group BlindBox channel has no root secret yet");
    }

    const std::uint64_t index = snapshot.state.send_index;
    const GroupMessageKeys keys = derive_group_message_keys(
        ByteView(*snapshot.root_secret), snapshot.group_id, Direction::Send, index,
        snapshot.group_epoch, snapshot.root_epoch, sender_id);
    const Bytes blob =
        encrypt_blob(ByteView(frame), ByteView(keys.blob_key), Direction::Send, index,
                     ByteView(keys.state_tag), config.privacy.padding_bucket);

    std::vector<PutResult> replicas = co_await client.put(keys.lookup_token, blob);

    snapshot.state.send_index = index + 1;
    snapshot.state.updated_at = resolve_now(now);

    co_return SendOutcome{index, snapshot.root_epoch, keys.lookup_token,
                          std::move(replicas)};
}

namespace {

/// Try one slot against one candidate root. Returns the frame when the blob is
/// there and belongs to this slot.
///
/// The acceptance test runs inside the fetch, so a replica holding somebody
/// else's blob under the same token does not end the search.
///
/// The direction inside the blob is the sender's, not the reader's: the keys are
/// derived under `recv` but the blob was written under `send`.
asio::awaitable<std::optional<Bytes>> collect_slot(ReplicaClient& client,
                                                   std::string lookup_token,
                                                   Bytes blob_key, Bytes state_tag,
                                                   std::uint64_t index) {
    Bytes decrypted;
    const std::optional<Bytes> fetched = co_await client.get_first_accepted(
        lookup_token, [&](ByteView blob) {
            try {
                BlobExpectation expectation;
                expectation.direction = Direction::Send;
                expectation.index = index;
                expectation.state_tag = state_tag;
                decrypted = decrypt_blob(blob, ByteView(blob_key), expectation);
                return true;
            } catch (const std::exception&) {
                return false;
            }
        });
    if (!fetched) {
        co_return std::nullopt;
    }
    co_return decrypted;
}

}  // namespace

asio::awaitable<std::vector<ReceivedMessage>> poll_pairwise(ReplicaClient& client,
                                                            PeerSnapshot& snapshot,
                                                            std::string local_peer_id,
                                                            CoordinatorConfig config,
                                                            std::int64_t now) {
    std::vector<ReceivedMessage> received;
    const std::vector<RootCandidate> roots = root_candidates(snapshot, config, now);
    if (roots.empty()) {
        co_return received;
    }

    for (const std::uint64_t index : recv_candidates(snapshot.state, config)) {
        for (const RootCandidate& root : roots) {
            const MessageKeys keys =
                derive_message_keys(ByteView(root.secret), local_peer_id, snapshot.peer_id,
                                    Direction::Recv, index, root.epoch);
            const std::optional<Bytes> frame =
                co_await collect_slot(client, keys.lookup_token, keys.blob_key,
                                      keys.state_tag, index);
            if (!frame) {
                continue;
            }
            snapshot.state.mark_consumed(index, resolve_now(now));
            received.push_back(
                ReceivedMessage{*frame, index, root.epoch, keys.lookup_token});
            // One slot holds one message; the remaining roots cannot also have
            // filled it.
            break;
        }
    }
    co_return received;
}

asio::awaitable<std::vector<ReceivedMessage>> poll_group(ReplicaClient& client,
                                                         GroupSnapshot& snapshot,
                                                         std::string sender_id,
                                                         CoordinatorConfig config,
                                                         std::int64_t now) {
    std::vector<ReceivedMessage> received;
    const std::vector<GroupRootCandidate> roots =
        group_root_candidates(snapshot, config, now);
    if (roots.empty()) {
        co_return received;
    }

    for (const std::uint64_t index : recv_candidates(snapshot.state, config)) {
        for (const GroupRootCandidate& root : roots) {
            // Derived under the sender's own direction: a group slot belongs to
            // one sender, and every member reads it with that sender's label.
            const GroupMessageKeys keys = derive_group_message_keys(
                ByteView(root.secret), snapshot.group_id, Direction::Send, index,
                root.group_epoch, root.root_epoch, sender_id);
            const std::optional<Bytes> frame =
                co_await collect_slot(client, keys.lookup_token, keys.blob_key,
                                      keys.state_tag, index);
            if (!frame) {
                continue;
            }
            snapshot.state.mark_consumed(index, resolve_now(now));
            received.push_back(
                ReceivedMessage{*frame, index, root.root_epoch, keys.lookup_token});
            break;
        }
    }
    co_return received;
}

asio::awaitable<void> emit_cover_gets(ReplicaClient& client, std::size_t count) {
    crypto::init();
    for (std::size_t issued = 0; issued < count; ++issued) {
        // A random token is indistinguishable from a real one to a replica,
        // which is the whole point; a miss is the expected outcome.
        const std::string token = encoding::hex_encode(ByteView(crypto::random_bytes(32)));
        try {
            (void)co_await client.get(token, false);
        } catch (const std::exception&) {
            // A cover request has no result worth reporting, and a failure here
            // must not look like a failure to collect real mail.
        }
    }
}

}  // namespace i2pchat::blindbox
