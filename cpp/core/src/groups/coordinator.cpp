#include "i2pchat/groups/coordinator.hpp"

#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>

#include "i2pchat/crypto.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/contacts.hpp"

namespace asio = boost::asio;

namespace i2pchat::groups {
namespace {

std::string trimmed(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    return std::string(text.substr(first, last - first + 1));
}

std::string trimmed_lower(std::string_view text) {
    std::string result = trimmed(text);
    std::transform(result.begin(), result.end(), result.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return result;
}

/// The reason a transport gave, or a stated default. Never empty, because the
/// UI shows this text and "" tells the user nothing.
std::string reason_or(const std::string& reason, std::string_view fallback) {
    const std::string value = trimmed(reason);
    return value.empty() ? std::string(fallback) : value;
}

std::string short_member_label(std::string_view member_id) {
    std::string normalized = normalize_member_id(member_id);
    if (normalized.empty()) {
        return "Peer";
    }
    constexpr std::string_view kSuffix = ".b32.i2p";
    if (normalized.ends_with(kSuffix)) {
        normalized.resize(normalized.size() - kSuffix.size());
    }
    if (normalized.size() <= 14) {
        return normalized;
    }
    return normalized.substr(0, 6) + ".." + normalized.substr(normalized.size() - 6);
}

std::map<std::string, std::string> normalized_text_map(
    const std::map<std::string, std::string>& source, bool lower) {
    std::map<std::string, std::string> result;
    for (const auto& [raw_member, raw_value] : source) {
        const std::string member = normalize_member_id(raw_member);
        const std::string value = lower ? trimmed_lower(raw_value) : trimmed(raw_value);
        if (member.empty() || value.empty()) {
            continue;
        }
        result.emplace(member, value);
    }
    return result;
}

bool flag_for(const std::map<std::string, bool>& source, const std::string& member) {
    const auto found = source.find(member);
    return found != source.end() && found->second;
}

std::string text_for(const std::map<std::string, std::string>& source,
                     const std::string& member, std::string fallback = {}) {
    const auto found = source.find(member);
    return found != source.end() ? found->second : std::move(fallback);
}

LinkState link_state_for(const std::string& peer_state, bool live_ready,
                         bool blindbox_ready, bool await_group_root) {
    if (live_ready) {
        return LinkState::Live;
    }
    if (peer_state == "handshaking" || peer_state == "connecting") {
        return LinkState::Handshaking;
    }
    if (peer_state == "failed") {
        return LinkState::Failed;
    }
    if (peer_state == "stale") {
        return LinkState::Degraded;
    }
    if (blindbox_ready) {
        return LinkState::BlindBox;
    }
    if (await_group_root) {
        return LinkState::AwaitRoot;
    }
    return LinkState::Idle;
}

std::string join(const std::vector<std::string>& parts, std::string_view separator) {
    std::string result;
    for (const std::string& part : parts) {
        if (!result.empty()) {
            result.append(separator);
        }
        result.append(part);
    }
    return result;
}

std::string mermaid_node_id(std::string_view member_id) {
    std::string token = normalize_member_id(member_id);
    for (char& c : token) {
        const bool allowed = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                             (c >= '0' && c <= '9') || c == '_';
        if (!allowed) {
            c = '_';
        }
    }
    return token.empty() ? "node" : token;
}

}  // namespace

const MemberDelivery* SendResult::find(std::string_view recipient_id) const {
    const std::string wanted = normalize_member_id(recipient_id);
    for (const MemberDelivery& delivery : deliveries) {
        if (delivery.recipient_id == wanted) {
            return &delivery;
        }
    }
    return nullptr;
}

GroupCoordinator::GroupCoordinator(Callbacks callbacks) : callbacks_(std::move(callbacks)) {
    if (!callbacks_.new_msg_id) {
        callbacks_.new_msg_id = [] { return crypto::random_hex(16); };
    }
    if (!callbacks_.now) {
        callbacks_.now = [] { return storage::now_iso8601_utc(); };
    }
    if (!callbacks_.live_ready) {
        callbacks_.live_ready = [](const std::string&) { return false; };
    }
}

void GroupCoordinator::prime_sequence(std::string_view group_id,
                                      std::uint64_t next_group_seq) {
    const std::string key = trimmed(group_id);
    if (key.empty()) {
        return;
    }
    const std::uint64_t seeded = next_group_seq > 0 ? next_group_seq - 1 : 0;
    std::uint64_t& current = last_sequence_[key];
    current = std::max(current, seeded);
}

void GroupCoordinator::forget_group(std::string_view group_id) {
    const std::string key = trimmed(group_id);
    if (!key.empty()) {
        last_sequence_.erase(key);
    }
}

std::uint64_t GroupCoordinator::next_sequence(std::string_view group_id) const {
    const auto found = last_sequence_.find(trimmed(group_id));
    return found == last_sequence_.end() ? 1 : found->second + 1;
}

std::uint64_t GroupCoordinator::take_sequence(const std::string& group_id) {
    return ++last_sequence_[group_id];
}

std::vector<std::string> GroupCoordinator::recipients_of(const GroupState& state,
                                                         std::string_view sender_id) {
    const std::string sender = normalize_member_id(sender_id);
    std::vector<std::string> recipients;
    for (const std::string& member : state.members()) {
        if (member.empty() || storage::same_i2p_destination(member, sender)) {
            continue;
        }
        recipients.push_back(member);
    }
    return recipients;
}

asio::awaitable<SendResult> GroupCoordinator::send_text(const GroupState& state,
                                                        std::string sender_id,
                                                        std::string text,
                                                        session::OutboundRoute route) {
    return send_payload(state, std::move(sender_id), nlohmann::json(std::move(text)),
                        ContentType::GroupText, route);
}

asio::awaitable<SendResult> GroupCoordinator::send_control(const GroupState& state,
                                                           std::string sender_id,
                                                           nlohmann::json payload,
                                                           session::OutboundRoute route) {
    return send_payload(state, std::move(sender_id), std::move(payload),
                        ContentType::GroupControl, route);
}

asio::awaitable<SendResult> GroupCoordinator::send_payload(const GroupState& state,
                                                            std::string sender_id,
                                                            nlohmann::json payload,
                                                            ContentType content_type,
                                                            session::OutboundRoute route) {
    SendResult result;
    result.envelope.group_id = state.group_id();
    result.envelope.epoch = state.epoch();
    result.envelope.msg_id = callbacks_.new_msg_id();
    result.envelope.sender_id = normalize_member_id(sender_id);
    result.envelope.group_seq = take_sequence(state.group_id());
    result.envelope.content_type = content_type;
    result.envelope.payload = std::move(payload);
    result.envelope.created_at = callbacks_.now();

    for (const std::string& recipient : recipients_of(state, sender_id)) {
        result.recipients.push_back(
            RecipientDelivery{recipient, result.envelope.msg_id + ":" + recipient});
    }

    // Sequential rather than concurrent: each leg may open a tunnel or talk to
    // replicas, and the reference implementation's ordering is what peers see.
    for (const RecipientDelivery& delivery : result.recipients) {
        result.deliveries.push_back(co_await deliver(result.envelope, delivery, route));
    }
    co_return result;
}

asio::awaitable<MemberDelivery> GroupCoordinator::retry_delivery(
    GroupEnvelope envelope, RecipientDelivery delivery, session::OutboundRoute route) {
    co_return co_await deliver(envelope, delivery, route);
}

asio::awaitable<MemberDelivery> GroupCoordinator::deliver(const GroupEnvelope& envelope,
                                                          const RecipientDelivery& delivery,
                                                          session::OutboundRoute route) {
    MemberDelivery result;
    result.recipient_id = delivery.recipient_id;
    result.delivery_id = delivery.delivery_id;

    const bool live_alive = callbacks_.live_ready(delivery.recipient_id);
    const session::OutboundPolicy policy = session::select_outbound_policy(route, live_alive);
    const bool live_allowed =
        policy == session::OutboundPolicy::LiveOnly ||
        policy == session::OutboundPolicy::PreferLiveFallbackBlindBox;

    if (live_allowed && live_alive && callbacks_.send_live) {
        const TransportOutcome outcome =
            co_await callbacks_.send_live(delivery.recipient_id, envelope, delivery);
        if (outcome.accepted) {
            result.status = DeliveryStatus::DeliveredLive;
            result.reason = reason_or(outcome.reason, "live-session");
            result.transport_message_id = outcome.transport_message_id;
            co_return result;
        }
        if (policy == session::OutboundPolicy::LiveOnly) {
            result.status = DeliveryStatus::Failed;
            result.reason = reason_or(outcome.reason, "needs-live-session");
            result.transport_message_id = outcome.transport_message_id;
            co_return result;
        }
    } else if (policy == session::OutboundPolicy::LiveOnly) {
        // `route=live` with no channel: queueing would silently disobey the
        // user, who asked for the live path specifically.
        result.status = DeliveryStatus::Failed;
        result.reason = "needs-live-session";
        co_return result;
    }

    if (!callbacks_.send_offline) {
        result.status = DeliveryStatus::Failed;
        result.reason = "blindbox-unavailable";
        co_return result;
    }

    const TransportOutcome outcome =
        co_await callbacks_.send_offline(delivery.recipient_id, envelope, delivery);
    result.transport_message_id = outcome.transport_message_id;
    if (outcome.accepted) {
        result.status = DeliveryStatus::QueuedOffline;
        result.reason = reason_or(outcome.reason, "blindbox-ready");
        co_return result;
    }
    result.status = DeliveryStatus::Failed;
    result.reason = reason_or(outcome.reason, "blindbox-unavailable");
    co_return result;
}

// ----------------------------------------------------------------------------
// Mesh planner
// ----------------------------------------------------------------------------

MeshSettings mesh_settings_from_environment(
    std::function<std::optional<std::string>(const std::string&)> getenv) {
    if (!getenv) {
        getenv = [](const std::string& name) -> std::optional<std::string> {
            const char* const value = std::getenv(name.c_str());
            if (value == nullptr) {
                return std::nullopt;
            }
            return std::string(value);
        };
    }

    const auto truthy = [&](const std::string& name, bool fallback) {
        const std::optional<std::string> raw = getenv(name);
        if (!raw) {
            return fallback;
        }
        const std::string value = trimmed_lower(*raw);
        return value != "0" && value != "false" && value != "no" && value != "off";
    };
    const auto number = [&](const std::string& name, double fallback, double minimum,
                            double maximum) {
        const std::optional<std::string> raw = getenv(name);
        if (!raw) {
            return fallback;
        }
        try {
            // An unparseable value falls back rather than disabling the planner,
            // matching the reference implementation.
            const double parsed = std::stod(trimmed(*raw));
            return std::clamp(parsed, minimum, maximum);
        } catch (const std::exception&) {
            return fallback;
        }
    };

    MeshSettings settings;
    settings.enabled = getenv("I2PCHAT_GROUP_AUTO_MESH")
                           ? truthy("I2PCHAT_GROUP_AUTO_MESH", true)
                           : truthy("I2PCHAT_GROUP_AUTO_INTRO", true);
    settings.interval = number("I2PCHAT_GROUP_AUTO_MESH_INTERVAL_SEC", 20.0, 3.0, 600.0);
    settings.max_per_tick = static_cast<std::size_t>(
        number("I2PCHAT_GROUP_AUTO_MESH_MAX_PER_TICK", 3.0, 1.0, 64.0));
    settings.connect_offline_ready =
        truthy("I2PCHAT_GROUP_AUTO_MESH_CONNECT_OFFLINE_READY", false);
    return settings;
}

std::vector<std::string> due_peer_intros(
    const std::vector<GroupState>& states, std::string_view local_member_id,
    const std::function<MeshPeerSnapshot(const std::string&)>& snapshot_of,
    const MeshSettings& settings, double now) {
    if (!settings.enabled || !snapshot_of) {
        return {};
    }
    const std::string local = normalize_member_id(local_member_id);
    if (local.empty()) {
        return {};
    }

    std::vector<std::string> peers;
    std::set<std::string> seen;
    for (const GroupState& state : states) {
        for (const std::string& member : state.members()) {
            if (member.empty() || member == local || !seen.insert(member).second) {
                continue;
            }
            peers.push_back(member);
        }
    }

    std::vector<std::tuple<int, double, std::string>> candidates;
    for (const std::string& peer : peers) {
        const MeshPeerSnapshot snapshot = snapshot_of(peer);
        if (snapshot.live_ready || snapshot.active_session) {
            continue;
        }
        if (snapshot.peer_state == "connecting" || snapshot.peer_state == "handshaking" ||
            snapshot.peer_state == "secure") {
            continue;
        }
        if (snapshot.next_retry > now) {
            continue;
        }
        if (snapshot.blindbox_ready && !settings.connect_offline_ready) {
            continue;
        }

        int priority = 3;
        if (snapshot.peer_state == "failed") {
            priority = 0;
        } else if (snapshot.peer_state == "stale") {
            priority = 1;
        } else if (snapshot.peer_state == "disconnected") {
            priority = 2;
        }
        candidates.emplace_back(priority, snapshot.next_retry, snapshot.peer_id);
    }

    std::sort(candidates.begin(), candidates.end());
    if (candidates.size() > settings.max_per_tick) {
        candidates.resize(settings.max_per_tick);
    }

    std::vector<std::string> due;
    due.reserve(candidates.size());
    for (auto& [priority, next_retry, peer_id] : candidates) {
        due.push_back(std::move(peer_id));
    }
    return due;
}

// ----------------------------------------------------------------------------
// Observed topology
// ----------------------------------------------------------------------------

std::string_view link_state_name(LinkState state) {
    switch (state) {
        case LinkState::Live:
            return "live";
        case LinkState::Handshaking:
            return "handshaking";
        case LinkState::AwaitRoot:
            return "await-root";
        case LinkState::BlindBox:
            return "blindbox";
        case LinkState::Degraded:
            return "degraded";
        case LinkState::Failed:
            return "failed";
        case LinkState::Idle:
            break;
    }
    return "idle";
}

TopologySnapshot build_observed_topology(const GroupState& state,
                                         const TopologyInputs& inputs) {
    const std::map<std::string, std::string> peer_states =
        normalized_text_map(inputs.peer_state_by_member, false);
    const std::map<std::string, std::string> delivery_status =
        normalized_text_map(inputs.delivery_status_by_member, true);
    const std::map<std::string, std::string> delivery_reason =
        normalized_text_map(inputs.delivery_reason_by_member, false);

    std::map<std::string, bool> live;
    for (const auto& [raw_member, ready] : inputs.live_by_member) {
        const std::string member = normalize_member_id(raw_member);
        if (!member.empty()) {
            live.emplace(member, ready);
        }
    }
    std::map<std::string, bool> blindbox;
    for (const auto& [raw_member, ready] : inputs.blindbox_ready_by_member) {
        const std::string member = normalize_member_id(raw_member);
        if (!member.empty()) {
            blindbox.emplace(member, ready);
        }
    }

    TopologySnapshot snapshot;
    snapshot.group_id = state.group_id();
    snapshot.title = state.title();
    snapshot.local_member_id = normalize_member_id(inputs.local_member_id);
    snapshot.group_blindbox_ready = inputs.group_blindbox_ready;
    snapshot.await_group_root = inputs.await_group_root;

    for (const std::string& member : state.members()) {
        TopologyNode node;
        node.member_id = member;
        node.is_local = !snapshot.local_member_id.empty() && member == snapshot.local_member_id;
        node.peer_state = text_for(peer_states, member, "disconnected");
        node.live_ready = flag_for(live, member);
        node.blindbox_ready = flag_for(blindbox, member);
        node.last_delivery_status = text_for(delivery_status, member);
        node.last_delivery_reason = text_for(delivery_reason, member);
        node.label = node.is_local ? "You" : short_member_label(member);
        snapshot.nodes.push_back(node);

        if (node.is_local || snapshot.local_member_id.empty()) {
            continue;
        }
        TopologyEdge edge;
        edge.source_id = snapshot.local_member_id;
        edge.target_id = member;
        edge.state = link_state_for(node.peer_state, node.live_ready, node.blindbox_ready,
                                    inputs.await_group_root);
        edge.peer_state = node.peer_state;
        edge.live_ready = node.live_ready;
        edge.blindbox_ready = node.blindbox_ready;
        edge.last_delivery_status = node.last_delivery_status;
        edge.last_delivery_reason = node.last_delivery_reason;

        std::vector<std::string> label_parts{std::string(link_state_name(edge.state))};
        if (edge.blindbox_ready && edge.state != LinkState::BlindBox) {
            label_parts.emplace_back("blindbox");
        }
        if (!edge.last_delivery_status.empty()) {
            label_parts.push_back("last=" + edge.last_delivery_status);
        }
        edge.label = join(label_parts, ", ");
        snapshot.edges.push_back(std::move(edge));
    }
    return snapshot;
}

std::string render_topology_ascii(const TopologySnapshot& snapshot) {
    std::vector<std::string> lines;
    const std::string title = snapshot.title.empty() ? snapshot.group_id : snapshot.title;
    lines.push_back("Observed group topology: " + title + " [" + snapshot.group_id + "]");
    if (snapshot.observed_only) {
        lines.emplace_back("Scope: local node view only");
    }
    if (snapshot.group_blindbox_ready) {
        lines.emplace_back("Group blindbox: ready");
    } else if (snapshot.await_group_root) {
        lines.emplace_back("Group blindbox: await-root");
    }
    for (const TopologyNode& node : snapshot.nodes) {
        if (node.is_local) {
            lines.push_back("Local: " + node.label);
            break;
        }
    }
    if (snapshot.edges.empty()) {
        lines.emplace_back("No remote members in this group.");
        return join(lines, "\n");
    }

    for (const TopologyEdge& edge : snapshot.edges) {
        const auto node = std::find_if(snapshot.nodes.begin(), snapshot.nodes.end(),
                                       [&edge](const TopologyNode& candidate) {
                                           return candidate.member_id == edge.target_id;
                                       });
        if (node == snapshot.nodes.end()) {
            continue;
        }
        std::vector<std::string> details{std::string(link_state_name(edge.state))};
        if (!node->peer_state.empty() && node->peer_state != "disconnected" &&
            node->peer_state != link_state_name(edge.state)) {
            details.push_back("peer=" + node->peer_state);
        }
        if (node->blindbox_ready) {
            details.emplace_back("blindbox-ready");
        }
        if (!node->last_delivery_status.empty()) {
            details.push_back("last=" + node->last_delivery_status);
        }
        if (!node->last_delivery_reason.empty()) {
            details.push_back("reason=" + node->last_delivery_reason);
        }
        lines.push_back("- " + node->label + ": " + join(details, ", "));
    }
    return join(lines, "\n");
}

std::string render_topology_mermaid(const TopologySnapshot& snapshot) {
    std::vector<std::string> lines{"graph TD"};
    if (snapshot.group_blindbox_ready) {
        lines.emplace_back(R"(  group_blindbox_state["Group blindbox\nready"])");
    } else if (snapshot.await_group_root) {
        lines.emplace_back(R"(  group_blindbox_state["Group blindbox\nawait-root"])");
    }
    for (const TopologyNode& node : snapshot.nodes) {
        std::vector<std::string> status;
        if (node.live_ready) {
            status.emplace_back("live");
        } else if (!node.peer_state.empty() && node.peer_state != "disconnected") {
            status.push_back(node.peer_state);
        }
        if (node.blindbox_ready) {
            status.emplace_back("blindbox");
        }
        if (!node.last_delivery_status.empty()) {
            status.push_back("last=" + node.last_delivery_status);
        }
        std::string label = node.label;
        if (!status.empty()) {
            label += "\\n" + join(status, "\\n");
        }
        lines.push_back("  " + mermaid_node_id(node.member_id) + "[\"" + label + "\"]");
    }
    for (const TopologyEdge& edge : snapshot.edges) {
        lines.push_back("  " + mermaid_node_id(edge.source_id) + " -->|\"" + edge.label +
                        "\"| " + mermaid_node_id(edge.target_id));
    }
    return join(lines, "\n");
}

}  // namespace i2pchat::groups
