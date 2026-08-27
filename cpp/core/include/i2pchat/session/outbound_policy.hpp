#pragma once

#include <string_view>

/// Which transport a message should take.
namespace i2pchat::session {

/// What the user asked for. `Auto` lets the current transport truth decide.
enum class OutboundRoute { Auto, Live, Offline };

/// Anything unrecognised is `Auto`, matching the reference implementation: an
/// unknown route name must not silently pin a message to one transport.
[[nodiscard]] OutboundRoute parse_outbound_route(std::string_view text);
[[nodiscard]] std::string_view outbound_route_name(OutboundRoute route);

enum class OutboundPolicy {
    /// Fail rather than fall back. `route=live`.
    LiveOnly,
    /// Try the live channel, queue on BlindBox if it will not take the message.
    PreferLiveFallbackBlindBox,
    /// No live channel right now: queue and let reconnection carry the retry.
    QueueThenRetryLive,
    /// Never touch the live channel. `route=offline`.
    BlindBoxOnly,
};

[[nodiscard]] std::string_view outbound_policy_name(OutboundPolicy policy);

/// `live_alive` must come from per-peer transport truth — a secure channel to
/// this peer specifically, not merely a running session.
[[nodiscard]] OutboundPolicy select_outbound_policy(OutboundRoute route, bool live_alive);

}  // namespace i2pchat::session
