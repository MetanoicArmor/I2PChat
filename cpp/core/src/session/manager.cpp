#include "i2pchat/session/manager.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <random>
#include <stdexcept>
#include <utility>

#include "i2pchat/storage/contacts.hpp"

namespace i2pchat::session {
namespace {

/// The manager's key for a peer: the canonical contact form when the value is
/// an address, otherwise the trimmed lowercase text.
std::string peer_key(std::string_view peer_id) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = peer_id.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = peer_id.find_last_not_of(kWhitespace);
    std::string value(peer_id.substr(first, last - first + 1));
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    const std::string canonical = storage::normalize_contact_address(value);
    return canonical.empty() ? value : canonical;
}

double monotonic_seconds() {
    const auto since = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration<double>(since).count();
}

double default_jitter() {
    static thread_local std::mt19937 engine{std::random_device{}()};
    std::uniform_real_distribution<double> distribution(0.0, 1.0);
    return distribution(engine);
}

}  // namespace

std::string_view transport_state_name(TransportState state) {
    switch (state) {
        case TransportState::Starting:
            return "starting";
        case TransportState::SamConnected:
            return "sam_connected";
        case TransportState::WarmingTunnels:
            return "warming_tunnels";
        case TransportState::Ready:
            return "ready";
        case TransportState::Degraded:
            return "degraded";
        case TransportState::Reconnecting:
            return "reconnecting";
        case TransportState::ShuttingDown:
            return "shutting_down";
        case TransportState::Failed:
            return "failed";
        case TransportState::Stopped:
            break;
    }
    return "stopped";
}

SessionManager::SessionManager(SessionManagerConfig config, Callbacks callbacks)
    : config_(config), callbacks_(std::move(callbacks)) {
    config_.secure_session_ttl = std::max(0.0, config_.secure_session_ttl);
    if (!callbacks_.now) {
        callbacks_.now = monotonic_seconds;
    }
    if (!callbacks_.jitter) {
        callbacks_.jitter = default_jitter;
    }
}

double SessionManager::now() const { return callbacks_.now(); }

void SessionManager::set_transport_state(TransportState state, std::string reason) {
    if (transport_state_ == state) {
        return;
    }
    const TransportState previous = transport_state_;
    transport_state_ = state;
    if (callbacks_.on_transport_state) {
        callbacks_.on_transport_state(previous, state, reason);
    }
}

void SessionManager::set_peer_state(PeerTransport& peer, PeerState state,
                                    const std::string& reason) {
    if (peer.state == state) {
        return;
    }
    const PeerState previous = peer.state;
    peer.state = state;
    if (callbacks_.on_peer_state) {
        callbacks_.on_peer_state(peer.peer_id, previous, state, reason);
    }
}

PeerState SessionManager::aggregate_peer_state() const {
    // Ordered by how much the UI cares: one secure peer outranks any number of
    // failed ones.
    static constexpr PeerState kOrder[] = {PeerState::Secure, PeerState::Handshaking,
                                           PeerState::Connecting, PeerState::Stale,
                                           PeerState::Failed};
    for (const PeerState wanted : kOrder) {
        for (const auto& [id, peer] : peers_) {
            if (peer.state == wanted) {
                return wanted;
            }
        }
    }
    return PeerState::Disconnected;
}

PeerTransport& SessionManager::ensure_peer(std::string_view peer_id) {
    const std::string key = peer_key(peer_id);
    if (key.empty()) {
        throw std::invalid_argument("A peer id is required");
    }
    PeerTransport& peer = peers_[key];
    if (peer.peer_id.empty()) {
        peer.peer_id = key;
        peer.last_activity = now();
    }
    return peer;
}

const PeerTransport* SessionManager::find_peer(std::string_view peer_id) const {
    const auto found = peers_.find(peer_key(peer_id));
    return found == peers_.end() ? nullptr : &found->second;
}

std::vector<std::string> SessionManager::peer_ids() const {
    std::vector<std::string> ids;
    ids.reserve(peers_.size());
    for (const auto& [id, peer] : peers_) {
        ids.push_back(id);
    }
    return ids;
}

void SessionManager::on_stream_open(std::string_view peer_id, std::string destination) {
    PeerTransport& peer = ensure_peer(peer_id);
    peer.streams.insert(std::move(destination));
    peer.connected = true;
    peer.last_activity = now();
    if (peer.state != PeerState::Secure && peer.state != PeerState::Handshaking) {
        set_peer_state(peer, PeerState::Connecting, "stream-open");
    }
}

void SessionManager::on_stream_closed(std::string_view peer_id,
                                      std::string_view destination) {
    const auto found = peers_.find(peer_key(peer_id));
    if (found == peers_.end()) {
        return;
    }
    PeerTransport& peer = found->second;
    peer.streams.erase(std::string(destination));
    peer.last_activity = now();
    if (!peer.streams.empty()) {
        return;
    }
    // The last stream is gone, so nothing is secure any more regardless of what
    // the handshake achieved.
    peer.connected = false;
    peer.handshake_complete = false;
    peer.secure_since = 0.0;
    peer.stale_since = 0.0;
    peer.inflight.clear();
    set_peer_state(peer, PeerState::Disconnected, "stream-closed");
}

void SessionManager::on_connecting(std::string_view peer_id) {
    PeerTransport& peer = ensure_peer(peer_id);
    peer.last_activity = now();
    set_peer_state(peer, PeerState::Connecting, "connecting");
}

void SessionManager::on_handshaking(std::string_view peer_id) {
    PeerTransport& peer = ensure_peer(peer_id);
    peer.connected = true;
    peer.handshake_complete = false;
    peer.last_activity = now();
    set_peer_state(peer, PeerState::Handshaking, "handshaking");
}

void SessionManager::on_secure(std::string_view peer_id, std::string reason) {
    const double stamp = now();
    PeerTransport& peer = ensure_peer(peer_id);
    peer.connected = true;
    peer.handshake_complete = true;
    peer.secure_since = stamp;
    peer.stale_since = 0.0;
    peer.last_activity = stamp;
    peer.last_live_ok = stamp;
    // A completed handshake clears the backoff: the next failure starts from one
    // attempt again rather than inheriting an old ladder.
    peer.reconnect = ReconnectMetadata{};
    set_peer_state(peer, PeerState::Secure, reason);
    promote_transport_on_live("peer-secure");
}

void SessionManager::on_disconnected(std::string_view peer_id, std::string reason,
                                     bool keep_reconnect_metadata) {
    const auto found = peers_.find(peer_key(peer_id));
    if (found == peers_.end()) {
        return;
    }
    PeerTransport& peer = found->second;
    peer.connected = false;
    peer.handshake_complete = false;
    peer.secure_since = 0.0;
    peer.stale_since = 0.0;
    peer.streams.clear();
    peer.inflight.clear();
    peer.last_activity = now();
    if (!keep_reconnect_metadata) {
        peer.reconnect = ReconnectMetadata{};
    }
    set_peer_state(peer, PeerState::Disconnected, reason);
}

void SessionManager::on_failed(std::string_view peer_id, std::string reason) {
    const double stamp = now();
    PeerTransport& peer = ensure_peer(peer_id);
    peer.connected = false;
    peer.handshake_complete = false;
    peer.secure_since = 0.0;
    peer.stale_since = 0.0;
    peer.streams.clear();
    peer.inflight.clear();
    peer.last_activity = stamp;
    peer.last_live_failure = stamp;
    peer.last_failure_reason = reason;
    set_peer_state(peer, PeerState::Failed, reason);
    if (!has_ready_peer()) {
        set_transport_state(TransportState::Degraded, reason);
    }
}

bool SessionManager::forget_peer(std::string_view peer_id) {
    return peers_.erase(peer_key(peer_id)) > 0;
}

void SessionManager::touch(std::string_view peer_id) {
    const auto found = peers_.find(peer_key(peer_id));
    if (found != peers_.end()) {
        found->second.last_activity = now();
    }
}

void SessionManager::mark_live_ok(std::string_view peer_id) {
    const double stamp = now();
    PeerTransport& peer = ensure_peer(peer_id);
    peer.connected = true;
    peer.handshake_complete = true;
    peer.last_activity = stamp;
    peer.last_live_ok = stamp;
    peer.stale_since = 0.0;
    if (peer.secure_since <= 0.0) {
        peer.secure_since = stamp;
    }
    peer.reconnect = ReconnectMetadata{};
    set_peer_state(peer, PeerState::Secure, "live-ok");
    promote_transport_on_live("live-ok");
}

void SessionManager::mark_live_failure(std::string_view peer_id, std::string reason,
                                       bool mark_stale) {
    const double stamp = now();
    PeerTransport& peer = ensure_peer(peer_id);
    peer.last_live_failure = stamp;
    peer.last_failure_reason = reason;
    peer.last_activity = stamp;
    if (mark_stale) {
        if (peer.stale_since <= 0.0) {
            peer.stale_since = stamp;
        }
        set_peer_state(peer, PeerState::Stale, reason);
    }
    if (!has_ready_peer()) {
        set_transport_state(TransportState::Degraded, reason);
    }
}

void SessionManager::register_inflight(std::string_view peer_id, std::uint64_t msg_id) {
    PeerTransport& peer = ensure_peer(peer_id);
    peer.inflight.insert(msg_id);
    peer.last_activity = now();
}

bool SessionManager::acknowledge_inflight(std::string_view peer_id, std::uint64_t msg_id) {
    const auto found = peers_.find(peer_key(peer_id));
    if (found == peers_.end()) {
        return false;
    }
    const bool removed = found->second.inflight.erase(msg_id) > 0;
    if (removed) {
        found->second.last_activity = now();
    }
    return removed;
}

void SessionManager::clear_inflight(std::string_view peer_id) {
    const auto found = peers_.find(peer_key(peer_id));
    if (found != peers_.end()) {
        found->second.inflight.clear();
    }
}

void SessionManager::refresh_health(std::string_view peer_id) {
    if (config_.secure_session_ttl <= 0.0) {
        return;
    }
    const auto found = peers_.find(peer_key(peer_id));
    if (found == peers_.end()) {
        return;
    }
    PeerTransport& peer = found->second;
    if (!peer.connected || !peer.handshake_complete || peer.secure_since <= 0.0) {
        return;
    }
    const double anchor =
        std::max({peer.last_activity, peer.last_live_ok, peer.secure_since});
    if (now() - anchor < config_.secure_session_ttl) {
        return;
    }
    if (peer.stale_since <= 0.0) {
        peer.stale_since = now();
    }
    if (peer.state == PeerState::Secure) {
        set_peer_state(peer, PeerState::Stale, "session-ttl");
    }
}

void SessionManager::refresh_all_health() {
    for (auto& [id, peer] : peers_) {
        refresh_health(id);
    }
}

bool SessionManager::live_ready(std::string_view peer_id) {
    const std::string key = peer_key(peer_id);
    if (key.empty()) {
        return false;
    }
    const auto found = peers_.find(key);
    if (found == peers_.end()) {
        return false;
    }
    refresh_health(key);
    const PeerTransport& peer = found->second;
    if (!peer.connected || !peer.handshake_complete) {
        return false;
    }
    return !(peer.state == PeerState::Stale && config_.treat_stale_as_offline);
}

OutboundPolicy SessionManager::policy_for(std::string_view peer_id, OutboundRoute route) {
    return select_outbound_policy(route, live_ready(peer_id));
}

double SessionManager::schedule_reconnect(std::string_view peer_id, std::string reason) {
    PeerTransport& peer = ensure_peer(peer_id);
    const unsigned attempt = peer.reconnect.attempt + 1;
    const double delay = std::min(config_.reconnect_max_delay,
                                  config_.reconnect_base_delay *
                                      std::pow(2.0, static_cast<double>(attempt - 1)));
    // Jitter spreads a herd of clients that all lost the same router, capped so
    // it never dominates a short delay.
    const double jitter = callbacks_.jitter() * std::min(0.75, delay * 0.25);
    const double effective = delay + jitter;
    const double stamp = now();

    peer.reconnect.attempt = attempt;
    peer.reconnect.next_retry = stamp + effective;
    peer.reconnect.last_failure = stamp;
    peer.reconnect.last_failure_reason = reason;
    peer.last_failure_reason = reason;
    peer.last_live_failure = stamp;
    peer.last_activity = stamp;

    if (!has_ready_peer()) {
        set_transport_state(TransportState::Reconnecting, reason);
    }
    return effective;
}

bool SessionManager::reconnect_due(std::string_view peer_id) const {
    const PeerTransport* const peer = find_peer(peer_id);
    return peer == nullptr || peer->reconnect.next_retry <= callbacks_.now();
}

ReconnectMetadata SessionManager::reconnect_metadata(std::string_view peer_id) const {
    const PeerTransport* const peer = find_peer(peer_id);
    return peer == nullptr ? ReconnectMetadata{} : peer->reconnect;
}

void SessionManager::reset() {
    peers_.clear();
    set_transport_state(TransportState::Stopped, "reset");
}

void SessionManager::promote_transport_on_live(std::string reason) {
    // Only from a state that means "trying": a transport the caller has not
    // started, or is shutting down, must not be dragged to ready by one peer.
    switch (transport_state_) {
        case TransportState::SamConnected:
        case TransportState::WarmingTunnels:
        case TransportState::Degraded:
        case TransportState::Reconnecting:
            set_transport_state(TransportState::Ready, std::move(reason));
            break;
        default:
            break;
    }
}

bool SessionManager::has_ready_peer() const {
    for (const auto& [id, peer] : peers_) {
        if (peer.connected && peer.handshake_complete &&
            !(peer.state == PeerState::Stale && config_.treat_stale_as_offline)) {
            return true;
        }
    }
    return false;
}

}  // namespace i2pchat::session
