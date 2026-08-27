#include <catch2/catch_test_macros.hpp>

#include <string>
#include <vector>

#include "i2pchat/session/manager.hpp"

using namespace i2pchat;
using i2pchat::session::OutboundPolicy;
using i2pchat::session::OutboundRoute;
using i2pchat::session::PeerState;
using i2pchat::session::SessionManager;
using i2pchat::session::SessionManagerConfig;
using i2pchat::session::TransportState;

namespace {

const std::string kAlice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kBob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

/// A manager on a clock the test drives, so the TTL and the backoff are exact
/// rather than approximately right after a sleep.
struct Fixture {
    double clock = 1000.0;
    std::vector<std::string> peer_events;
    std::vector<std::string> transport_events;
    SessionManager manager;

    explicit Fixture(SessionManagerConfig config = {}) : manager(config, callbacks()) {}

    SessionManager::Callbacks callbacks() {
        SessionManager::Callbacks callbacks;
        callbacks.now = [this] { return clock; };
        // No randomness: a test asserting on a delay should not have to allow a
        // range.
        callbacks.jitter = [] { return 0.0; };
        callbacks.on_peer_state = [this](const std::string& peer_id, PeerState,
                                         PeerState to, const std::string& reason) {
            peer_events.push_back(peer_id.substr(0, 4) + ":" +
                                  std::string(session::peer_state_name(to)) + ":" + reason);
        };
        callbacks.on_transport_state = [this](TransportState, TransportState to,
                                              const std::string& reason) {
            transport_events.push_back(std::string(session::transport_state_name(to)) + ":" +
                                       reason);
        };
        return callbacks;
    }
};

}  // namespace

TEST_CASE("a peer is only live once its handshake finishes", "[session][manager]") {
    Fixture fixture;

    CHECK_FALSE(fixture.manager.live_ready(kBob));
    fixture.manager.on_stream_open(kBob, "dest-1");
    CHECK_FALSE(fixture.manager.live_ready(kBob));
    fixture.manager.on_handshaking(kBob);
    CHECK_FALSE(fixture.manager.live_ready(kBob));
    fixture.manager.on_secure(kBob);
    CHECK(fixture.manager.live_ready(kBob));

    CHECK(fixture.peer_events == std::vector<std::string>{
                                     "nnnn:connecting:stream-open",
                                     "nnnn:handshaking:handshaking",
                                     "nnnn:secure:handshake-ok",
                                 });
}

TEST_CASE("one peer's channel says nothing about another's", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob);

    CHECK(fixture.manager.live_ready(kBob));
    CHECK_FALSE(fixture.manager.live_ready(kAlice));
    CHECK(fixture.manager.aggregate_peer_state() == PeerState::Secure);
}

TEST_CASE("the same destination spelled two ways is one peer", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob + ".b32.i2p");

    CHECK(fixture.manager.live_ready(kBob));
    CHECK(fixture.manager.peer_ids() == std::vector<std::string>{kBob});
    CHECK_THROWS_AS(fixture.manager.ensure_peer("   "), std::invalid_argument);
}

TEST_CASE("an idle secure channel goes stale", "[session][manager]") {
    Fixture fixture({.secure_session_ttl = 60.0});
    fixture.manager.on_secure(kBob);

    fixture.clock += 59.0;
    fixture.manager.refresh_health(kBob);
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Secure);

    fixture.clock += 2.0;
    fixture.manager.refresh_health(kBob);
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Stale);
    // Stale is still usable by default: the channel probably works, and refusing
    // it would push traffic to BlindBox for no reason.
    CHECK(fixture.manager.live_ready(kBob));

    // Traffic resets the clock.
    fixture.manager.mark_live_ok(kBob);
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Secure);
    fixture.clock += 30.0;
    fixture.manager.refresh_health(kBob);
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Secure);
}

TEST_CASE("a transport nobody started is not dragged to ready", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob);
    CHECK(fixture.manager.transport_state() == TransportState::Stopped);
}

TEST_CASE("a stale channel can be treated as offline", "[session][manager]") {
    Fixture fixture({.secure_session_ttl = 60.0, .treat_stale_as_offline = true});
    fixture.manager.on_secure(kBob);
    fixture.clock += 61.0;

    CHECK_FALSE(fixture.manager.live_ready(kBob));
    CHECK(fixture.manager.policy_for(kBob, OutboundRoute::Auto) ==
          OutboundPolicy::QueueThenRetryLive);
}

TEST_CASE("the TTL check can be switched off", "[session][manager]") {
    Fixture fixture({.secure_session_ttl = 0.0});
    fixture.manager.on_secure(kBob);
    fixture.clock += 100000.0;
    fixture.manager.refresh_all_health();

    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Secure);
    CHECK(fixture.manager.live_ready(kBob));
}

TEST_CASE("losing the last stream ends the session", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_stream_open(kBob, "dest-1");
    fixture.manager.on_stream_open(kBob, "dest-2");
    fixture.manager.on_secure(kBob);
    fixture.manager.register_inflight(kBob, 7);

    // Both sides dialled at once, so one stream closing is not a disconnect.
    fixture.manager.on_stream_closed(kBob, "dest-1");
    CHECK(fixture.manager.live_ready(kBob));

    fixture.manager.on_stream_closed(kBob, "dest-2");
    CHECK_FALSE(fixture.manager.live_ready(kBob));
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Disconnected);
    CHECK(fixture.manager.find_peer(kBob)->inflight.empty());
}

TEST_CASE("in-flight message ids are tracked per peer", "[session][manager]") {
    Fixture fixture;
    fixture.manager.register_inflight(kBob, 11);
    fixture.manager.register_inflight(kBob, 12);

    CHECK(fixture.manager.acknowledge_inflight(kBob, 11));
    // A second ACK for the same id is not silently accepted.
    CHECK_FALSE(fixture.manager.acknowledge_inflight(kBob, 11));
    CHECK_FALSE(fixture.manager.acknowledge_inflight(kAlice, 12));

    fixture.manager.clear_inflight(kBob);
    CHECK(fixture.manager.find_peer(kBob)->inflight.empty());
}

TEST_CASE("the reconnect backoff doubles and is capped", "[session][manager]") {
    Fixture fixture({.reconnect_base_delay = 1.0, .reconnect_max_delay = 8.0});

    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") == 1.0);
    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") == 2.0);
    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") == 4.0);
    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") == 8.0);
    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") == 8.0);

    const session::ReconnectMetadata metadata = fixture.manager.reconnect_metadata(kBob);
    CHECK(metadata.attempt == 5);
    CHECK(metadata.last_failure_reason == "refused");
    CHECK(metadata.next_retry == fixture.clock + 8.0);

    CHECK_FALSE(fixture.manager.reconnect_due(kBob));
    fixture.clock += 8.0;
    CHECK(fixture.manager.reconnect_due(kBob));
    // A peer that was never tried may be dialled immediately.
    CHECK(fixture.manager.reconnect_due(kAlice));
}

TEST_CASE("a completed handshake clears the backoff ladder", "[session][manager]") {
    Fixture fixture;
    fixture.manager.schedule_reconnect(kBob, "refused");
    fixture.manager.schedule_reconnect(kBob, "refused");
    REQUIRE(fixture.manager.reconnect_metadata(kBob).attempt == 2);

    fixture.manager.on_secure(kBob);
    CHECK(fixture.manager.reconnect_metadata(kBob).attempt == 0);
    CHECK(fixture.manager.schedule_reconnect(kBob, "refused") ==
          1.0);  // starts from the first rung again
}

TEST_CASE("transport state follows the peers", "[session][manager]") {
    Fixture fixture;
    fixture.manager.set_transport_state(TransportState::Starting, "boot");
    fixture.manager.set_transport_state(TransportState::SamConnected, "sam");
    fixture.manager.set_transport_state(TransportState::WarmingTunnels, "tunnels");
    // Setting the same state twice is not an event.
    fixture.manager.set_transport_state(TransportState::WarmingTunnels, "tunnels");

    fixture.manager.on_secure(kBob);
    CHECK(fixture.manager.transport_state() == TransportState::Ready);

    fixture.manager.on_failed(kBob, "stream-reset");
    CHECK(fixture.manager.transport_state() == TransportState::Degraded);
    CHECK(fixture.manager.aggregate_peer_state() == PeerState::Failed);

    fixture.manager.schedule_reconnect(kBob, "stream-reset");
    CHECK(fixture.manager.transport_state() == TransportState::Reconnecting);

    fixture.manager.mark_live_ok(kBob);
    CHECK(fixture.manager.transport_state() == TransportState::Ready);

    CHECK(fixture.transport_events ==
          std::vector<std::string>{
              "starting:boot", "sam_connected:sam", "warming_tunnels:tunnels",
              "ready:peer-secure",
              "degraded:stream-reset", "reconnecting:stream-reset", "ready:live-ok",
          });
}

TEST_CASE("one healthy peer keeps the transport ready", "[session][manager]") {
    Fixture fixture;
    fixture.manager.set_transport_state(TransportState::WarmingTunnels, "tunnels");
    fixture.manager.on_secure(kAlice);
    fixture.manager.on_secure(kBob);
    REQUIRE(fixture.manager.transport_state() == TransportState::Ready);

    // Alice is still reachable, so losing Bob is not a transport-wide problem.
    fixture.manager.on_failed(kBob, "stream-reset");
    CHECK(fixture.manager.transport_state() == TransportState::Ready);

    fixture.manager.on_failed(kAlice, "stream-reset");
    CHECK(fixture.manager.transport_state() == TransportState::Degraded);
}

TEST_CASE("an explicit route overrides transport truth", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob);

    CHECK(fixture.manager.policy_for(kBob, OutboundRoute::Auto) ==
          OutboundPolicy::PreferLiveFallbackBlindBox);
    CHECK(fixture.manager.policy_for(kBob, OutboundRoute::Offline) ==
          OutboundPolicy::BlindBoxOnly);
    CHECK(fixture.manager.policy_for(kAlice, OutboundRoute::Live) ==
          OutboundPolicy::LiveOnly);
    CHECK(fixture.manager.policy_for(kAlice, OutboundRoute::Auto) ==
          OutboundPolicy::QueueThenRetryLive);
}

TEST_CASE("a forgotten peer leaves no transport state", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob);
    REQUIRE(fixture.manager.find_peer(kBob) != nullptr);

    CHECK(fixture.manager.forget_peer(kBob));
    CHECK(fixture.manager.find_peer(kBob) == nullptr);
    CHECK_FALSE(fixture.manager.forget_peer(kBob));
    CHECK(fixture.manager.aggregate_peer_state() == PeerState::Disconnected);

    fixture.manager.on_secure(kAlice);
    fixture.manager.reset();
    CHECK(fixture.manager.peer_ids().empty());
    CHECK(fixture.manager.transport_state() == TransportState::Stopped);
}

TEST_CASE("a disconnect can keep or drop the backoff", "[session][manager]") {
    Fixture fixture;
    fixture.manager.schedule_reconnect(kBob, "refused");
    fixture.manager.on_disconnected(kBob, "user-request", true);
    CHECK(fixture.manager.reconnect_metadata(kBob).attempt == 1);

    fixture.manager.on_disconnected(kBob, "user-request", false);
    CHECK(fixture.manager.reconnect_metadata(kBob).attempt == 0);
    // A peer nobody has heard of cannot be disconnected.
    fixture.manager.on_disconnected(kAlice, "user-request");
    CHECK(fixture.manager.find_peer(kAlice) == nullptr);
}

TEST_CASE("a live failure marks the peer stale", "[session][manager]") {
    Fixture fixture;
    fixture.manager.on_secure(kBob);
    fixture.manager.mark_live_failure(kBob, "write-timeout");

    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Stale);
    CHECK(fixture.manager.find_peer(kBob)->last_failure_reason == "write-timeout");
    CHECK(fixture.manager.find_peer(kBob)->stale_since == fixture.clock);

    fixture.manager.mark_live_failure(kBob, "quiet-probe", false);
    CHECK(fixture.manager.find_peer(kBob)->state == PeerState::Stale);
}
