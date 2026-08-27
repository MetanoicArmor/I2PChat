#include <catch2/catch_test_macros.hpp>

#include <boost/asio/io_context.hpp>
#include <map>
#include <string>
#include <vector>

#include "i2pchat/groups/coordinator.hpp"
#include "run_awaitable.hpp"

namespace asio = boost::asio;

using namespace i2pchat;
using i2pchat::groups::ContentType;
using i2pchat::groups::DeliveryStatus;
using i2pchat::groups::GroupCoordinator;
using i2pchat::groups::GroupEnvelope;
using i2pchat::groups::GroupState;
using i2pchat::groups::LinkState;
using i2pchat::groups::MemberDelivery;
using i2pchat::groups::MeshPeerSnapshot;
using i2pchat::groups::MeshSettings;
using i2pchat::groups::RecipientDelivery;
using i2pchat::groups::SendResult;
using i2pchat::groups::TopologyInputs;
using i2pchat::groups::TransportOutcome;
using i2pchat::session::OutboundRoute;
using i2pchat::testing::run_awaitable;

namespace {

const std::string kAlice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kBob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";
const std::string kCarol = "cccc1111dddd2222eeee3333ffff4444gggg5555hhhh6666iiii";

/// Records what each transport was asked to do, so a test can assert on the
/// route taken rather than only on the reported status.
struct Transports {
    std::vector<std::string> live_calls;
    std::vector<std::string> offline_calls;
    std::map<std::string, bool> live_ready;
    TransportOutcome live_result{true, "live-session", "42"};
    TransportOutcome offline_result{true, "", ""};

    GroupCoordinator::Callbacks callbacks() {
        GroupCoordinator::Callbacks callbacks;
        callbacks.send_live = [this](const std::string& recipient, const GroupEnvelope&,
                                     const RecipientDelivery&)
            -> asio::awaitable<TransportOutcome> {
            live_calls.push_back(recipient);
            co_return live_result;
        };
        callbacks.send_offline = [this](const std::string& recipient, const GroupEnvelope&,
                                        const RecipientDelivery&)
            -> asio::awaitable<TransportOutcome> {
            offline_calls.push_back(recipient);
            co_return offline_result;
        };
        callbacks.live_ready = [this](const std::string& peer) {
            const auto found = live_ready.find(peer);
            return found != live_ready.end() && found->second;
        };
        callbacks.new_msg_id = [] { return std::string("msg-fixed"); };
        callbacks.now = [] { return std::string("2026-02-03T04:05:06+00:00"); };
        return callbacks;
    }
};

MeshPeerSnapshot snapshot(std::string peer_id, std::string peer_state,
                          double next_retry = 0.0) {
    MeshPeerSnapshot out;
    out.peer_id = std::move(peer_id);
    out.peer_state = std::move(peer_state);
    out.next_retry = next_retry;
    return out;
}

}  // namespace

TEST_CASE("a route name picks the transport policy", "[groups][coordinator]") {
    using i2pchat::session::OutboundPolicy;
    using i2pchat::session::parse_outbound_route;
    using i2pchat::session::select_outbound_policy;

    CHECK(parse_outbound_route(" LIVE ") == OutboundRoute::Live);
    CHECK(parse_outbound_route("Offline") == OutboundRoute::Offline);
    CHECK(parse_outbound_route("") == OutboundRoute::Auto);
    // An unknown name must not pin the message to one transport.
    CHECK(parse_outbound_route("carrier-pigeon") == OutboundRoute::Auto);

    CHECK(select_outbound_policy(OutboundRoute::Live, false) == OutboundPolicy::LiveOnly);
    CHECK(select_outbound_policy(OutboundRoute::Offline, true) ==
          OutboundPolicy::BlindBoxOnly);
    CHECK(select_outbound_policy(OutboundRoute::Auto, true) ==
          OutboundPolicy::PreferLiveFallbackBlindBox);
    CHECK(select_outbound_policy(OutboundRoute::Auto, false) ==
          OutboundPolicy::QueueThenRetryLive);
}

TEST_CASE("the sender is not among the recipients", "[groups][coordinator]") {
    const GroupState state("group-1", 1, {kAlice, kBob, kCarol});

    CHECK(GroupCoordinator::recipients_of(state, kAlice) ==
          std::vector<std::string>{kBob, kCarol});
    // The same destination spelled with its suffix is still the sender.
    CHECK(GroupCoordinator::recipients_of(state, kAlice + ".b32.i2p") ==
          std::vector<std::string>{kBob, kCarol});
    CHECK(GroupCoordinator::recipients_of(state, "someone-else").size() == 3);
}

TEST_CASE("each member is delivered to over its own transport",
          "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    transports.live_ready[kBob] = true;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob, kCarol});

    const SendResult result = run_awaitable(
        context, coordinator.send_text(state, kAlice, "hello", OutboundRoute::Auto));

    CHECK(result.envelope.group_id == "group-1");
    CHECK(result.envelope.sender_id == kAlice);
    CHECK(result.envelope.msg_id == "msg-fixed");
    CHECK(result.envelope.group_seq == 1);
    CHECK(result.envelope.content_type == ContentType::GroupText);
    CHECK(result.envelope.payload == "hello");
    CHECK(result.envelope.created_at == "2026-02-03T04:05:06+00:00");

    REQUIRE(result.recipients.size() == 2);
    CHECK(result.recipients[0].delivery_id == "msg-fixed:" + kBob);

    REQUIRE(result.deliveries.size() == 2);
    const MemberDelivery* bob = result.find(kBob);
    REQUIRE(bob != nullptr);
    CHECK(bob->status == DeliveryStatus::DeliveredLive);
    CHECK(bob->reason == "live-session");
    CHECK(bob->transport_message_id == "42");

    const MemberDelivery* carol = result.find(kCarol);
    REQUIRE(carol != nullptr);
    CHECK(carol->status == DeliveryStatus::QueuedOffline);
    CHECK(carol->reason == "blindbox-ready");

    CHECK(transports.live_calls == std::vector<std::string>{kBob});
    CHECK(transports.offline_calls == std::vector<std::string>{kCarol});
}

TEST_CASE("a refused live send falls back to blindbox", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    transports.live_ready[kBob] = true;
    transports.live_result = {false, "stream-closed", ""};
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    const SendResult result =
        run_awaitable(context, coordinator.send_text(state, kAlice, "hello"));

    REQUIRE(result.deliveries.size() == 1);
    CHECK(result.deliveries[0].status == DeliveryStatus::QueuedOffline);
    CHECK(transports.live_calls == std::vector<std::string>{kBob});
    CHECK(transports.offline_calls == std::vector<std::string>{kBob});
}

TEST_CASE("route=live never queues", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    SECTION("with no channel at all") {
        const SendResult result = run_awaitable(
            context, coordinator.send_text(state, kAlice, "hello", OutboundRoute::Live));
        REQUIRE(result.deliveries.size() == 1);
        CHECK(result.deliveries[0].status == DeliveryStatus::Failed);
        CHECK(result.deliveries[0].reason == "needs-live-session");
        CHECK(transports.offline_calls.empty());
    }

    SECTION("with a channel that refuses the message") {
        transports.live_ready[kBob] = true;
        transports.live_result = {false, "", ""};
        const SendResult result = run_awaitable(
            context, coordinator.send_text(state, kAlice, "hello", OutboundRoute::Live));
        REQUIRE(result.deliveries.size() == 1);
        CHECK(result.deliveries[0].status == DeliveryStatus::Failed);
        CHECK(result.deliveries[0].reason == "needs-live-session");
        CHECK(transports.offline_calls.empty());
    }
}

TEST_CASE("route=offline ignores a live channel", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    transports.live_ready[kBob] = true;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    const SendResult result = run_awaitable(
        context, coordinator.send_text(state, kAlice, "hello", OutboundRoute::Offline));

    REQUIRE(result.deliveries.size() == 1);
    CHECK(result.deliveries[0].status == DeliveryStatus::QueuedOffline);
    CHECK(transports.live_calls.empty());
    CHECK(transports.offline_calls == std::vector<std::string>{kBob});
}

TEST_CASE("a failed blindbox leg reports why", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    transports.offline_result = {false, "blindbox-await-root", ""};
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    const SendResult result =
        run_awaitable(context, coordinator.send_text(state, kAlice, "hello"));

    REQUIRE(result.deliveries.size() == 1);
    CHECK(result.deliveries[0].status == DeliveryStatus::Failed);
    CHECK(result.deliveries[0].reason == "blindbox-await-root");
}

TEST_CASE("a control payload keeps its object shape", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 3, {kAlice, kBob});

    const nlohmann::json payload = {{"op", "member_left"}, {"member_id", kCarol}};
    const SendResult result =
        run_awaitable(context, coordinator.send_control(state, kAlice, payload));

    CHECK(result.envelope.content_type == ContentType::GroupControl);
    CHECK(result.envelope.payload == payload);
    CHECK(result.envelope.epoch == 3);
}

TEST_CASE("sequence numbers advance and can be primed", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    CHECK(coordinator.next_sequence("group-1") == 1);
    CHECK(run_awaitable(context, coordinator.send_text(state, kAlice, "one"))
              .envelope.group_seq == 1);
    CHECK(run_awaitable(context, coordinator.send_text(state, kAlice, "two"))
              .envelope.group_seq == 2);

    // Priming from a stored record must not hand out a number peers have seen.
    coordinator.prime_sequence("group-1", 10);
    CHECK(run_awaitable(context, coordinator.send_text(state, kAlice, "three"))
              .envelope.group_seq == 10);
    // Nor can it move the counter backwards.
    coordinator.prime_sequence("group-1", 4);
    CHECK(run_awaitable(context, coordinator.send_text(state, kAlice, "four"))
              .envelope.group_seq == 11);

    coordinator.forget_group("group-1");
    CHECK(run_awaitable(context, coordinator.send_text(state, kAlice, "five"))
              .envelope.group_seq == 1);
}

TEST_CASE("a retry re-runs one leg of an existing envelope", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice, kBob});

    const SendResult first =
        run_awaitable(context, coordinator.send_text(state, kAlice, "hello"));
    REQUIRE(first.deliveries.size() == 1);

    transports.live_ready[kBob] = true;
    const MemberDelivery retried = run_awaitable(
        context, coordinator.retry_delivery(first.envelope, first.recipients.front()));

    CHECK(retried.status == DeliveryStatus::DeliveredLive);
    CHECK(retried.delivery_id == first.recipients.front().delivery_id);
    // The envelope is reused, so the sequence number does not move.
    CHECK(coordinator.next_sequence("group-1") == 2);
}

TEST_CASE("a group of one has nothing to send", "[groups][coordinator]") {
    asio::io_context context;
    Transports transports;
    GroupCoordinator coordinator(transports.callbacks());
    const GroupState state("group-1", 1, {kAlice});

    const SendResult result =
        run_awaitable(context, coordinator.send_text(state, kAlice, "hello"));

    CHECK(result.recipients.empty());
    CHECK(result.deliveries.empty());
    CHECK(transports.live_calls.empty());
    CHECK(transports.offline_calls.empty());
}

TEST_CASE("mesh settings come from the environment", "[groups][mesh]") {
    std::map<std::string, std::string> environment;
    const auto getenv = [&environment](const std::string& name)
        -> std::optional<std::string> {
        const auto found = environment.find(name);
        if (found == environment.end()) {
            return std::nullopt;
        }
        return found->second;
    };

    const MeshSettings defaults = groups::mesh_settings_from_environment(getenv);
    CHECK(defaults.enabled);
    CHECK(defaults.interval == 20.0);
    CHECK(defaults.max_per_tick == 3);
    CHECK_FALSE(defaults.connect_offline_ready);

    environment["I2PCHAT_GROUP_AUTO_INTRO"] = "0";
    CHECK_FALSE(groups::mesh_settings_from_environment(getenv).enabled);
    // The newer name wins when both are set.
    environment["I2PCHAT_GROUP_AUTO_MESH"] = "1";
    CHECK(groups::mesh_settings_from_environment(getenv).enabled);

    environment["I2PCHAT_GROUP_AUTO_MESH_INTERVAL_SEC"] = "1";
    environment["I2PCHAT_GROUP_AUTO_MESH_MAX_PER_TICK"] = "999";
    const MeshSettings clamped = groups::mesh_settings_from_environment(getenv);
    CHECK(clamped.interval == 3.0);
    CHECK(clamped.max_per_tick == 64);

    environment["I2PCHAT_GROUP_AUTO_MESH_INTERVAL_SEC"] = "not a number";
    CHECK(groups::mesh_settings_from_environment(getenv).interval == 20.0);
}

TEST_CASE("the mesh planner picks the most starved peers", "[groups][mesh]") {
    const std::vector<GroupState> states{
        GroupState("group-1", 1, {kAlice, kBob, kCarol}),
        GroupState("group-2", 1, {kAlice, kBob}),
    };
    std::map<std::string, MeshPeerSnapshot> snapshots{
        {kBob, snapshot(kBob, "disconnected")},
        {kCarol, snapshot(kCarol, "failed")},
    };
    const auto snapshot_of = [&snapshots](const std::string& peer) {
        const auto found = snapshots.find(peer);
        return found != snapshots.end() ? found->second : snapshot(peer, "disconnected");
    };

    MeshSettings settings;
    // A failed peer is dialled before an idle one.
    CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0) ==
          std::vector<std::string>{kCarol, kBob});

    settings.max_per_tick = 1;
    CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0) ==
          std::vector<std::string>{kCarol});

    settings.enabled = false;
    CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0).empty());
}

TEST_CASE("the mesh planner leaves settled peers alone", "[groups][mesh]") {
    const std::vector<GroupState> states{GroupState("group-1", 1, {kAlice, kBob})};
    MeshPeerSnapshot state = snapshot(kBob, "disconnected");
    const auto snapshot_of = [&state](const std::string&) { return state; };
    const MeshSettings settings;

    SECTION("already live") {
        state.live_ready = true;
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0).empty());
    }
    SECTION("mid-handshake") {
        state.peer_state = "handshaking";
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0).empty());
    }
    SECTION("inside its backoff") {
        state.next_retry = 200.0;
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0).empty());
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 250.0) ==
              std::vector<std::string>{kBob});
    }
    SECTION("reachable over blindbox") {
        state.blindbox_ready = true;
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, settings, 100.0).empty());

        MeshSettings eager = settings;
        eager.connect_offline_ready = true;
        CHECK(groups::due_peer_intros(states, kAlice, snapshot_of, eager, 100.0) ==
              std::vector<std::string>{kBob});
    }
}

TEST_CASE("the planner needs to know who it is", "[groups][mesh]") {
    const std::vector<GroupState> states{GroupState("group-1", 1, {kAlice, kBob})};
    const auto snapshot_of = [](const std::string& peer) {
        return snapshot(peer, "disconnected");
    };
    CHECK(groups::due_peer_intros(states, "", snapshot_of, MeshSettings{}, 0.0).empty());
}

TEST_CASE("the observed topology describes the local view", "[groups][topology]") {
    const GroupState state("group-1", 2, {kAlice, kBob, kCarol}, "Team");
    TopologyInputs inputs;
    inputs.local_member_id = kAlice;
    inputs.live_by_member[kBob] = true;
    inputs.peer_state_by_member[kBob] = "secure";
    inputs.peer_state_by_member[kCarol] = "failed";
    inputs.blindbox_ready_by_member[kCarol] = true;
    inputs.delivery_status_by_member[kCarol] = "QUEUED_OFFLINE";
    inputs.delivery_reason_by_member[kCarol] = "blindbox-ready";

    const groups::TopologySnapshot snapshot = groups::build_observed_topology(state, inputs);

    CHECK(snapshot.group_id == "group-1");
    CHECK(snapshot.title == "Team");
    CHECK(snapshot.observed_only);
    REQUIRE(snapshot.nodes.size() == 3);
    CHECK(snapshot.nodes[0].is_local);
    CHECK(snapshot.nodes[0].label == "You");
    CHECK(snapshot.nodes[1].label == "nnnnoo..yyzzzz");

    // No edge to itself: the snapshot is what this node can observe.
    REQUIRE(snapshot.edges.size() == 2);
    CHECK(snapshot.edges[0].target_id == kBob);
    CHECK(snapshot.edges[0].state == LinkState::Live);
    CHECK(snapshot.edges[0].label == "live");
    CHECK(snapshot.edges[1].target_id == kCarol);
    CHECK(snapshot.edges[1].state == LinkState::Failed);
    CHECK(snapshot.edges[1].label == "failed, blindbox, last=queued_offline");
}

TEST_CASE("a link with no live path but a group root shows await-root",
          "[groups][topology]") {
    const GroupState state("group-1", 1, {kAlice, kBob});
    TopologyInputs inputs;
    inputs.local_member_id = kAlice;
    inputs.await_group_root = true;

    const groups::TopologySnapshot snapshot = groups::build_observed_topology(state, inputs);
    REQUIRE(snapshot.edges.size() == 1);
    CHECK(snapshot.edges[0].state == LinkState::AwaitRoot);
}

TEST_CASE("the topology renders for humans and for mermaid", "[groups][topology]") {
    const GroupState state("group-1", 1, {kAlice, kBob}, "Team");
    TopologyInputs inputs;
    inputs.local_member_id = kAlice;
    inputs.peer_state_by_member[kBob] = "stale";
    inputs.group_blindbox_ready = true;

    const groups::TopologySnapshot snapshot = groups::build_observed_topology(state, inputs);

    const std::string ascii = groups::render_topology_ascii(snapshot);
    CHECK(ascii.find("Observed group topology: Team [group-1]") != std::string::npos);
    CHECK(ascii.find("Group blindbox: ready") != std::string::npos);
    CHECK(ascii.find("Local: You") != std::string::npos);
    CHECK(ascii.find("- nnnnoo..yyzzzz: degraded, peer=stale") != std::string::npos);

    const std::string mermaid = groups::render_topology_mermaid(snapshot);
    CHECK(mermaid.starts_with("graph TD"));
    CHECK(mermaid.find(R"(group_blindbox_state["Group blindbox\nready"])") !=
          std::string::npos);
    CHECK(mermaid.find("-->|\"degraded\"|") != std::string::npos);
}

TEST_CASE("a topology with no remote members says so", "[groups][topology]") {
    const GroupState state("group-1", 1, {kAlice});
    TopologyInputs inputs;
    inputs.local_member_id = kAlice;

    const groups::TopologySnapshot snapshot = groups::build_observed_topology(state, inputs);
    CHECK(snapshot.edges.empty());
    CHECK(groups::render_topology_ascii(snapshot).find(
              "No remote members in this group.") != std::string::npos);
}
