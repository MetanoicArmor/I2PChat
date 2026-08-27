#pragma once

#include <boost/asio/awaitable.hpp>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/groups/models.hpp"
#include "i2pchat/session/outbound_policy.hpp"

/// Group message fan-out.
///
/// A group message is not a broadcast: it is one envelope delivered to each
/// remaining member over that member's own transport, live or BlindBox. This
/// class decides the route per member and records the outcome; the transports
/// themselves are injected, so the decision logic is testable without a network.
namespace i2pchat::groups {

/// What a transport reports back about one leg.
struct TransportOutcome {
    bool accepted = false;
    /// Free text for the UI and the stored delivery record.
    std::string reason;
    /// The live channel's message id, when it produced one.
    std::string transport_message_id;
};

struct MemberDelivery {
    std::string recipient_id;
    DeliveryStatus status = DeliveryStatus::Failed;
    std::string reason;
    std::string transport_message_id;
    std::string delivery_id;
};

struct SendResult {
    GroupEnvelope envelope;
    /// One per remaining member, in membership order.
    std::vector<RecipientDelivery> recipients;
    std::vector<MemberDelivery> deliveries;

    [[nodiscard]] const MemberDelivery* find(std::string_view recipient_id) const;
};

class GroupCoordinator {
public:
    using Sender = std::function<boost::asio::awaitable<TransportOutcome>(
        const std::string& recipient_id, const GroupEnvelope& envelope,
        const RecipientDelivery& delivery)>;

    struct Callbacks {
        /// Send over the peer's secure channel. Only called when `live_ready`
        /// says the channel is up.
        Sender send_live;
        /// Seal into the peer's BlindBox and store it on the replicas.
        Sender send_offline;
        /// Per-peer transport truth: a secure channel to this peer exists.
        std::function<bool(const std::string& peer_id)> live_ready;
        /// New message id. Defaults to 16 random bytes as lowercase hex.
        std::function<std::string()> new_msg_id;
        /// Envelope timestamp. Defaults to the current time in ISO-8601 UTC.
        std::function<std::string()> now;
    };

    explicit GroupCoordinator(Callbacks callbacks);

    /// Seed the sequence counter from a stored record, so a restart does not
    /// reissue numbers that members have already seen.
    void prime_sequence(std::string_view group_id, std::uint64_t next_group_seq);
    /// Drop the counter after the group's record was deleted.
    void forget_group(std::string_view group_id);
    [[nodiscard]] std::uint64_t next_sequence(std::string_view group_id) const;

    [[nodiscard]] boost::asio::awaitable<SendResult> send_text(
        const GroupState& state, std::string sender_id, std::string text,
        session::OutboundRoute route = session::OutboundRoute::Auto);

    [[nodiscard]] boost::asio::awaitable<SendResult> send_control(
        const GroupState& state, std::string sender_id, nlohmann::json payload,
        session::OutboundRoute route = session::OutboundRoute::Auto);

    /// Throws `std::invalid_argument` on a content type that is not a group
    /// message.
    [[nodiscard]] boost::asio::awaitable<SendResult> send_payload(
        const GroupState& state, std::string sender_id, nlohmann::json payload,
        ContentType content_type, session::OutboundRoute route);

    /// Re-run one leg of an envelope that has already been built, for a stored
    /// pending delivery.
    [[nodiscard]] boost::asio::awaitable<MemberDelivery> retry_delivery(
        GroupEnvelope envelope, RecipientDelivery delivery,
        session::OutboundRoute route = session::OutboundRoute::Auto);

    /// The members a message from `sender_id` goes to: everyone else.
    [[nodiscard]] static std::vector<std::string> recipients_of(const GroupState& state,
                                                                std::string_view sender_id);

private:
    [[nodiscard]] boost::asio::awaitable<MemberDelivery> deliver(
        const GroupEnvelope& envelope, const RecipientDelivery& delivery,
        session::OutboundRoute route);

    [[nodiscard]] std::uint64_t take_sequence(const std::string& group_id);

    Callbacks callbacks_;
    std::map<std::string, std::uint64_t> last_sequence_;
};

// ----------------------------------------------------------------------------
// Mesh planner
// ----------------------------------------------------------------------------

/// What the runtime knows about one group member's transport right now.
struct MeshPeerSnapshot {
    std::string peer_id;
    /// `disconnected`, `connecting`, `handshaking`, `secure`, `stale`, `failed`.
    std::string peer_state = "disconnected";
    bool live_ready = false;
    bool active_session = false;
    bool blindbox_ready = false;
    /// Monotonic seconds before which a reconnect must not be attempted.
    double next_retry = 0.0;
};

struct MeshSettings {
    bool enabled = true;
    /// Seconds between scans.
    double interval = 20.0;
    std::size_t max_per_tick = 3;
    /// Whether a peer that can be reached over BlindBox is still worth a live
    /// bootstrap. Off by default: BlindBox already delivers, and dialling costs
    /// tunnels.
    bool connect_offline_ready = false;
};

/// Read the mesh settings from the environment, clamped to sane bounds.
///
/// `I2PCHAT_GROUP_AUTO_MESH` decides whether the planner runs, falling back to
/// the older `I2PCHAT_GROUP_AUTO_INTRO` when it is unset. The lookup is
/// injectable so tests do not have to mutate the process environment.
[[nodiscard]] MeshSettings mesh_settings_from_environment(
    std::function<std::optional<std::string>(const std::string& name)> getenv = {});

/// The members worth a live bootstrap this tick, most starved first.
///
/// Ordering is by transport state — failed, then stale, then disconnected —
/// then by how long the peer has been waiting, then by id for stability.
/// Members already live, mid-handshake or inside their retry backoff are left
/// alone.
[[nodiscard]] std::vector<std::string> due_peer_intros(
    const std::vector<GroupState>& states, std::string_view local_member_id,
    const std::function<MeshPeerSnapshot(const std::string&)>& snapshot_of,
    const MeshSettings& settings, double now);

// ----------------------------------------------------------------------------
// Observed topology
// ----------------------------------------------------------------------------

enum class LinkState { Live, Handshaking, AwaitRoot, BlindBox, Degraded, Failed, Idle };

[[nodiscard]] std::string_view link_state_name(LinkState state);

struct TopologyNode {
    std::string member_id;
    /// Short label for display: `You` locally, otherwise an abbreviated address.
    std::string label;
    bool is_local = false;
    std::string peer_state = "disconnected";
    bool live_ready = false;
    bool blindbox_ready = false;
    std::string last_delivery_status;
    std::string last_delivery_reason;
};

struct TopologyEdge {
    std::string source_id;
    std::string target_id;
    LinkState state = LinkState::Idle;
    std::string label;
    std::string peer_state = "disconnected";
    bool live_ready = false;
    bool blindbox_ready = false;
    std::string last_delivery_status;
    std::string last_delivery_reason;
};

/// One node's view of the group. Not the group's true topology: only links this
/// client can observe are represented, hence `observed_only`.
struct TopologySnapshot {
    std::string group_id;
    std::string title;
    std::string local_member_id;
    bool observed_only = true;
    bool group_blindbox_ready = false;
    bool await_group_root = false;
    std::vector<TopologyNode> nodes;
    std::vector<TopologyEdge> edges;
};

struct TopologyInputs {
    std::string local_member_id;
    std::map<std::string, bool> live_by_member;
    std::map<std::string, std::string> peer_state_by_member;
    std::map<std::string, bool> blindbox_ready_by_member;
    std::map<std::string, std::string> delivery_status_by_member;
    std::map<std::string, std::string> delivery_reason_by_member;
    bool group_blindbox_ready = false;
    bool await_group_root = false;
};

[[nodiscard]] TopologySnapshot build_observed_topology(const GroupState& state,
                                                       const TopologyInputs& inputs);

[[nodiscard]] std::string render_topology_ascii(const TopologySnapshot& snapshot);
[[nodiscard]] std::string render_topology_mermaid(const TopologySnapshot& snapshot);

}  // namespace i2pchat::groups
