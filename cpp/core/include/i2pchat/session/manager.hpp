#pragma once

#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/session/outbound_policy.hpp"
#include "i2pchat/session/peer_session.hpp"

/// Transport truth: which peers can be reached right now, and over what.
///
/// This is bookkeeping only — no sockets, no framing, no UI. Everything that
/// decides whether a message may take the live path reads from here, so the
/// rules stay in one testable place instead of being re-derived at each call
/// site, which is what went wrong in the reference implementation's monolith.
///
/// "Live" is always per peer. A running SAM session says nothing about whether
/// any particular peer has a secure channel, and treating it as if it did is how
/// messages get dropped.
namespace i2pchat::session {

enum class TransportState {
    Stopped,
    Starting,
    SamConnected,
    /// Tunnels are being built; the session exists but is not usable yet.
    WarmingTunnels,
    Ready,
    /// Reachable in principle, but the last attempts failed.
    Degraded,
    Reconnecting,
    ShuttingDown,
    Failed,
};

[[nodiscard]] std::string_view transport_state_name(TransportState state);

struct ReconnectMetadata {
    /// How many consecutive attempts have failed. Zero once a peer is healthy.
    unsigned attempt = 0;
    /// Monotonic seconds before which the next attempt must not be made.
    double next_retry = 0.0;
    double last_failure = 0.0;
    std::string last_failure_reason;
};

/// One peer's transport state.
struct PeerTransport {
    std::string peer_id;
    PeerState state = PeerState::Disconnected;
    bool connected = false;
    bool handshake_complete = false;
    /// Monotonic seconds. Zero means "not in this state".
    double secure_since = 0.0;
    double stale_since = 0.0;
    double last_activity = 0.0;
    double last_live_ok = 0.0;
    double last_live_failure = 0.0;
    std::string last_failure_reason;
    ReconnectMetadata reconnect;
    /// Open streams to this peer, keyed by destination. More than one occurs
    /// when both sides dial at once.
    std::set<std::string> streams;
    /// Message ids sent but not yet acknowledged.
    std::set<std::uint64_t> inflight;
};

struct SessionManagerConfig {
    /// How long a secure channel may sit idle before it counts as stale. Zero
    /// disables the check.
    double secure_session_ttl = 300.0;
    /// Whether a stale channel is refused for live delivery. Off by default:
    /// the channel usually still works, and refusing it would push traffic to
    /// BlindBox unnecessarily.
    bool treat_stale_as_offline = false;
    double reconnect_base_delay = 1.0;
    double reconnect_max_delay = 30.0;
};

class SessionManager {
public:
    struct Callbacks {
        std::function<void(TransportState from, TransportState to,
                           const std::string& reason)>
            on_transport_state;
        /// Fired per peer, on any change of that peer's state.
        std::function<void(const std::string& peer_id, PeerState from, PeerState to,
                           const std::string& reason)>
            on_peer_state;
        /// Monotonic seconds. Injectable so the TTL and backoff can be tested
        /// without waiting.
        std::function<double()> now;
        /// Backoff jitter in [0, 1). Injectable for the same reason.
        std::function<double()> jitter;
    };

    explicit SessionManager(SessionManagerConfig config = {}, Callbacks callbacks = {});

    [[nodiscard]] TransportState transport_state() const noexcept {
        return transport_state_;
    }
    void set_transport_state(TransportState state, std::string reason = {});

    /// The strongest state across all peers, which is what a status bar shows.
    [[nodiscard]] PeerState aggregate_peer_state() const;

    /// Create the entry if it does not exist. Throws `std::invalid_argument` on
    /// an empty id.
    PeerTransport& ensure_peer(std::string_view peer_id);
    [[nodiscard]] const PeerTransport* find_peer(std::string_view peer_id) const;
    [[nodiscard]] std::vector<std::string> peer_ids() const;

    void on_stream_open(std::string_view peer_id, std::string destination);
    /// Drops the stream; the peer falls back to disconnected once its last
    /// stream is gone.
    void on_stream_closed(std::string_view peer_id, std::string_view destination);

    void on_connecting(std::string_view peer_id);
    void on_handshaking(std::string_view peer_id);
    void on_secure(std::string_view peer_id, std::string reason = "handshake-ok");
    void on_disconnected(std::string_view peer_id, std::string reason = "disconnected",
                         bool keep_reconnect_metadata = true);
    void on_failed(std::string_view peer_id, std::string reason);
    /// Forget the peer entirely, for a contact that was deleted.
    bool forget_peer(std::string_view peer_id);

    /// Record traffic, which is what keeps a channel from going stale.
    void touch(std::string_view peer_id);
    void mark_live_ok(std::string_view peer_id);
    void mark_live_failure(std::string_view peer_id, std::string reason,
                           bool mark_stale = true);

    void register_inflight(std::string_view peer_id, std::uint64_t msg_id);
    /// Whether the id was actually in flight, so a duplicate ACK is visible to
    /// the caller rather than silently accepted.
    bool acknowledge_inflight(std::string_view peer_id, std::uint64_t msg_id);
    void clear_inflight(std::string_view peer_id);

    /// Move a secure-but-idle channel to stale. Called by the readiness checks,
    /// and worth calling from a timer so the UI does not lag behind.
    void refresh_health(std::string_view peer_id);
    void refresh_all_health();

    /// The one question the send path asks.
    [[nodiscard]] bool live_ready(std::string_view peer_id);

    [[nodiscard]] OutboundPolicy policy_for(std::string_view peer_id, OutboundRoute route);

    /// Arm the backoff after a failed attempt and return the delay in seconds.
    double schedule_reconnect(std::string_view peer_id, std::string reason);
    /// Whether a reconnect may be attempted now.
    [[nodiscard]] bool reconnect_due(std::string_view peer_id) const;
    [[nodiscard]] ReconnectMetadata reconnect_metadata(std::string_view peer_id) const;

    /// Drop every peer, for shutdown.
    void reset();

private:
    void set_peer_state(PeerTransport& peer, PeerState state, const std::string& reason);
    void promote_transport_on_live(std::string reason);
    [[nodiscard]] bool has_ready_peer() const;
    [[nodiscard]] double now() const;

    SessionManagerConfig config_;
    Callbacks callbacks_;
    TransportState transport_state_ = TransportState::Stopped;
    std::map<std::string, PeerTransport> peers_;
};

}  // namespace i2pchat::session
