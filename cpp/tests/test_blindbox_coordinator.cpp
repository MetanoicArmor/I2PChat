#include <catch2/catch_test_macros.hpp>

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>

#include <optional>

#include "i2pchat/blindbox/coordinator.hpp"
#include "i2pchat/blindbox/replica_server.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using namespace i2pchat::blindbox;
using i2pchat::testing::TempDir;

namespace {

constexpr std::int64_t kNow = 1700000000;
constexpr std::string_view kLocalPeer = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllll";
constexpr std::string_view kRemotePeer = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyy";

CoordinatorConfig config_low() {
    CoordinatorConfig config;
    config.privacy = privacy_settings(PrivacyProfile::Low);
    return config;
}

PeerSnapshot rooted_snapshot(std::int64_t created_at = kNow) {
    PeerSnapshot snapshot;
    snapshot.peer_id = std::string(kRemotePeer);
    snapshot.root_secret = Bytes(32, 0xaa);
    snapshot.root_epoch = 1;
    snapshot.root_created_at = created_at;
    return snapshot;
}

/// Runs one coroutine while a server keeps the context permanently busy.
template <typename Awaitable>
auto run_step(boost::asio::io_context& context, Awaitable awaitable) {
    using Value = typename Awaitable::value_type;
    std::exception_ptr failure;
    std::optional<Value> result;
    boost::asio::co_spawn(
        context,
        [&]() -> boost::asio::awaitable<void> {
            try {
                result.emplace(co_await std::move(awaitable));
            } catch (...) {
                failure = std::current_exception();
            }
            context.stop();
        },
        boost::asio::detached);
    context.run();
    context.restart();
    if (failure) {
        std::rethrow_exception(failure);
    }
    REQUIRE(result.has_value());
    return std::move(*result);
}

/// A replica and a client wired to it, for the tests that need real storage.
struct Harness {
    explicit Harness(const TempDir& dir) {
        ReplicaServerConfig server_config;
        server_config.base_dir = dir.path();
        server_config.port = 0;
        server_config.gc_interval = std::chrono::seconds(3600);
        service = std::make_shared<ReplicaService>(server_config);
        service->audit().set_stderr_echo(false);
        server = std::make_unique<ReplicaServer>(context.get_executor(), service);
        server->start();

        ReplicaClientConfig client_config;
        client_config.endpoints = {"127.0.0.1:" + std::to_string(server->port())};
        client = std::make_unique<ReplicaClient>(
            context.get_executor(), client_config,
            direct_stream_factory(context.get_executor()));
    }

    ~Harness() { server->stop(); }

    boost::asio::io_context context;
    std::shared_ptr<ReplicaService> service;
    std::unique_ptr<ReplicaServer> server;
    std::unique_ptr<ReplicaClient> client;
};

}  // namespace

TEST_CASE("privacy profiles trade bandwidth for resistance") {
    CHECK(parse_privacy_profile("HIGH") == PrivacyProfile::High);
    CHECK(parse_privacy_profile(" medium ") == PrivacyProfile::Medium);
    // An unrecognised value must not stop the client from starting.
    CHECK(parse_privacy_profile("paranoid") == PrivacyProfile::Low);
    CHECK(privacy_profile_name(PrivacyProfile::High) == "high");

    const PrivacySettings low = privacy_settings(PrivacyProfile::Low);
    const PrivacySettings high = privacy_settings(PrivacyProfile::High);
    // Higher privacy means more cover traffic, more padding and shorter root
    // lifetimes — each of which costs something.
    CHECK(high.cover_gets > low.cover_gets);
    CHECK(high.padding_bucket > low.padding_bucket);
    CHECK(high.root_rotate_messages < low.root_rotate_messages);
    CHECK(high.root_rotate_seconds < low.root_rotate_seconds);
}

TEST_CASE("only one side of a pair offers the root") {
    // Exactly one of the two must decide to initiate, or they either both mint
    // a root or neither does.
    CHECK(initiates_root_exchange("aaa", "bbb"));
    CHECK_FALSE(initiates_root_exchange("bbb", "aaa"));
    CHECK_FALSE(initiates_root_exchange("aaa", "aaa"));

    // The suffix and the case are not part of the comparison, so both sides
    // agree regardless of how the address was written down.
    CHECK(initiates_root_exchange("AAA.b32.i2p", "bbb"));
    CHECK_FALSE(initiates_root_exchange("BBB", "aaa.b32.i2p"));

    CHECK_FALSE(initiates_root_exchange("", "bbb"));
    CHECK_FALSE(initiates_root_exchange("aaa", "  "));
}

TEST_CASE("the group root coordinator is the lowest member") {
    CHECK(group_root_coordinator({"ccc", "aaa", "bbb"}) == "aaa");
    CHECK(group_root_coordinator({"CCC.b32.i2p", "bbb"}) == "bbb");
    CHECK(group_root_coordinator({}).empty());
    CHECK(group_root_coordinator({"", "   "}).empty());
}

TEST_CASE("a channel with no root asks for one") {
    PeerSnapshot snapshot;
    snapshot.peer_id = std::string(kRemotePeer);
    const CoordinatorConfig config = config_low();

    const std::optional<PendingRoot> pending =
        ensure_pending_root(snapshot, config, kNow);
    REQUIRE(pending.has_value());
    CHECK(pending->created);
    CHECK(pending->reason == "initialized");
    CHECK(pending->epoch == 1);
    CHECK(pending->secret.size() == 32);
    CHECK(snapshot.pending_root_secret == pending->secret);

    // Asking again re-offers the same secret: the peer may simply not have
    // answered yet, and a second secret would strand one of them.
    const std::optional<PendingRoot> again =
        ensure_pending_root(snapshot, config, kNow);
    REQUIRE(again.has_value());
    CHECK_FALSE(again->created);
    CHECK(again->secret == pending->secret);
    CHECK(again->epoch == pending->epoch);
}

TEST_CASE("a healthy root is left alone") {
    PeerSnapshot snapshot = rooted_snapshot();
    snapshot.state.send_index = 10;
    const CoordinatorConfig config = config_low();

    CHECK_FALSE(should_rotate_root(snapshot, config, kNow));
    CHECK_FALSE(ensure_pending_root(snapshot, config, kNow).has_value());
    CHECK_FALSE(snapshot.pending_root_secret.has_value());
}

TEST_CASE("a root is rotated on age or on volume") {
    const CoordinatorConfig config = config_low();

    {
        PeerSnapshot snapshot = rooted_snapshot(kNow - config.privacy.root_rotate_seconds);
        CHECK(should_rotate_root(snapshot, config, kNow));
        const std::optional<PendingRoot> pending =
            ensure_pending_root(snapshot, config, kNow);
        REQUIRE(pending.has_value());
        CHECK(pending->reason == "rotated");
        CHECK(pending->epoch == 2);
    }
    {
        PeerSnapshot snapshot = rooted_snapshot();
        snapshot.root_send_index_base = 0;
        snapshot.state.send_index = config.privacy.root_rotate_messages;
        CHECK(should_rotate_root(snapshot, config, kNow));
    }
    {
        // The count is measured from where this root took over, not from zero:
        // otherwise every rotation would immediately be due again.
        PeerSnapshot snapshot = rooted_snapshot();
        snapshot.root_send_index_base = config.privacy.root_rotate_messages;
        snapshot.state.send_index = config.privacy.root_rotate_messages + 1;
        CHECK_FALSE(should_rotate_root(snapshot, config, kNow));
    }
    {
        // A forced rotation does not wait for either threshold.
        PeerSnapshot snapshot = rooted_snapshot();
        const std::optional<PendingRoot> pending =
            ensure_pending_root(snapshot, config, kNow, true);
        REQUIRE(pending.has_value());
        CHECK(pending->reason == "rotated");
    }
}

TEST_CASE("a root is adopted only on a confirmation of its own epoch") {
    PeerSnapshot snapshot = rooted_snapshot();
    const CoordinatorConfig config = config_low();
    const Bytes old_secret = *snapshot.root_secret;

    const std::optional<PendingRoot> pending =
        ensure_pending_root(snapshot, config, kNow, true);
    REQUIRE(pending.has_value());

    // A confirmation for another epoch is stale or forged; acting on it would
    // leave the two sides deriving different keys.
    CHECK_FALSE(commit_pending_root(snapshot, pending->epoch + 1, config, kNow));
    CHECK_FALSE(commit_pending_root(snapshot, 0, config, kNow));
    CHECK(snapshot.root_secret == old_secret);

    CHECK(commit_pending_root(snapshot, pending->epoch, config, kNow));
    CHECK(snapshot.root_secret == pending->secret);
    CHECK(snapshot.root_epoch == pending->epoch);
    CHECK(snapshot.root_created_at == kNow);
    CHECK_FALSE(snapshot.pending_root_secret.has_value());

    // The replaced root is kept for a while: a message sent just before the
    // change is still in flight under it.
    REQUIRE(snapshot.prev_roots.size() == 1);
    CHECK(snapshot.prev_roots[0].secret == old_secret);
    CHECK(snapshot.prev_roots[0].expires_at ==
          kNow + config.privacy.previous_grace_seconds);

    // Nothing left to commit.
    CHECK_FALSE(commit_pending_root(snapshot, pending->epoch, config, kNow));
}

TEST_CASE("the previous root list is capped by the privacy profile") {
    CoordinatorConfig config = config_low();
    config.privacy.max_previous_roots = 1;

    PeerSnapshot snapshot = rooted_snapshot();
    for (int round = 0; round < 3; ++round) {
        const std::optional<PendingRoot> pending =
            ensure_pending_root(snapshot, config, kNow, true);
        REQUIRE(pending.has_value());
        REQUIRE(commit_pending_root(snapshot, pending->epoch, config, kNow));
    }
    CHECK(snapshot.prev_roots.size() == 1);
    CHECK(snapshot.root_epoch == 4);
}

TEST_CASE("a poll looks under every root that could still be in use") {
    CoordinatorConfig config = config_low();
    config.privacy.max_previous_roots = 2;

    PeerSnapshot snapshot = rooted_snapshot();
    snapshot.prev_roots = {
        PreviousRoot{0, Bytes(32, 0xbb), kNow + 100},
        // Past its grace period: a message under it is no longer collectable,
        // and asking for it would only cost a request.
        PreviousRoot{0, Bytes(32, 0xcc), kNow - 1},
    };

    const std::vector<RootCandidate> candidates = root_candidates(snapshot, config, kNow);
    REQUIRE(candidates.size() == 2);
    // The current root first: the next message is likeliest to be under it.
    CHECK(candidates[0].secret == Bytes(32, 0xaa));
    CHECK(candidates[1].secret == Bytes(32, 0xbb));

    PeerSnapshot rootless;
    rootless.peer_id = std::string(kRemotePeer);
    CHECK(root_candidates(rootless, config, kNow).empty());
}

TEST_CASE("the poll window covers gaps and is capped") {
    CoordinatorConfig config = config_low();
    config.recv_lookahead = 4;
    config.recv_max_per_poll = 64;

    BlindBoxState state;
    state.recv_window = 2;
    // The lookahead wins over a narrower stored window: a peer that sent
    // several messages while we were away is otherwise never found.
    CHECK(recv_candidates(state, config) == std::vector<std::uint64_t>{0, 1, 2, 3});

    state.consumed_recv = {1};
    CHECK(recv_candidates(state, config) == std::vector<std::uint64_t>{0, 2, 3});

    // Backtrack is off by default, so a settled slot is never asked about again.
    state.recv_base = 4;
    state.consumed_recv.clear();
    CHECK(recv_candidates(state, config) == std::vector<std::uint64_t>{4, 5, 6, 7});

    config.recv_backtrack = 2;
    const std::vector<std::uint64_t> with_backtrack = recv_candidates(state, config);
    // Forward first, then the backtrack: the next message is far likelier ahead.
    CHECK(with_backtrack == std::vector<std::uint64_t>{4, 5, 6, 7, 2, 3});

    config.recv_max_per_poll = 3;
    CHECK(recv_candidates(state, config) == std::vector<std::uint64_t>{4, 5, 6});
}

TEST_CASE("a group root needs every member before it is adopted") {
    GroupSnapshot snapshot;
    snapshot.group_id = "group-alpha";
    const CoordinatorConfig config = config_low();
    const std::vector<std::string> members{"member-a", "member-b"};

    const std::optional<PendingRoot> pending =
        ensure_pending_group_root(snapshot, 1, members, config, kNow);
    REQUIRE(pending.has_value());
    CHECK(pending->reason == "initialized");
    CHECK(snapshot.group_epoch == 1);
    CHECK(snapshot.pending_root_target_members == members);

    CHECK(record_group_root_ack(snapshot, pending->epoch, "member-a"));
    CHECK_FALSE(group_root_fully_acked(snapshot));
    // Adopting now would leave member-b unable to read anything.
    CHECK_FALSE(commit_pending_group_root(snapshot, config, kNow));

    // A confirmation from somebody who was not a target cannot complete the
    // quorum on a member's behalf.
    CHECK_FALSE(record_group_root_ack(snapshot, pending->epoch, "stranger"));
    // Nor can one for the wrong epoch.
    CHECK_FALSE(record_group_root_ack(snapshot, pending->epoch + 1, "member-b"));
    CHECK_FALSE(group_root_fully_acked(snapshot));

    CHECK(record_group_root_ack(snapshot, pending->epoch, "member-b"));
    CHECK(group_root_fully_acked(snapshot));
    CHECK(commit_pending_group_root(snapshot, config, kNow));
    CHECK(snapshot.root_secret == pending->secret);
    CHECK(snapshot.pending_root_target_members.empty());
}

TEST_CASE("a membership change forces a new group root") {
    GroupSnapshot snapshot;
    snapshot.group_id = "group-alpha";
    const CoordinatorConfig config = config_low();

    const std::optional<PendingRoot> first =
        ensure_pending_group_root(snapshot, 1, {"member-a"}, config, kNow);
    REQUIRE(first.has_value());
    REQUIRE(record_group_root_ack(snapshot, first->epoch, "member-a"));
    REQUIRE(commit_pending_group_root(snapshot, config, kNow));

    // Same epoch, healthy root: nothing to do.
    CHECK_FALSE(
        ensure_pending_group_root(snapshot, 1, {"member-a"}, config, kNow).has_value());

    // A new membership epoch invalidates the root regardless of its age,
    // because a departed member still holds it.
    const std::optional<PendingRoot> rotated =
        ensure_pending_group_root(snapshot, 2, {"member-a", "member-c"}, config, kNow);
    REQUIRE(rotated.has_value());
    CHECK(rotated->reason == "initialized");
    CHECK(rotated->secret != first->secret);
    CHECK(snapshot.group_epoch == 2);
    CHECK(snapshot.pending_root_target_members.size() == 2);
}

TEST_CASE("a group of one gets no root at all") {
    GroupSnapshot snapshot;
    snapshot.group_id = "group-alpha";
    // Nobody to deliver to, so a root would only be a liability.
    CHECK_FALSE(ensure_pending_group_root(snapshot, 1, {}, config_low(), kNow).has_value());
    CHECK_FALSE(snapshot.pending_root_secret.has_value());
}

TEST_CASE("a departure drops the pairwise root immediately") {
    PeerSnapshot snapshot = rooted_snapshot();
    snapshot.prev_roots = {PreviousRoot{0, Bytes(32, 0xbb), kNow + 1000}};
    const CoordinatorConfig config = config_low();

    invalidate_root_for_departed_member(snapshot, config, kNow);

    // The departed member holds the old root, so it is not kept for grace: no
    // message written after they left may be readable with it.
    CHECK_FALSE(snapshot.root_secret.has_value());
    CHECK(snapshot.prev_roots.empty());
    REQUIRE(snapshot.pending_root_secret.has_value());
    CHECK(snapshot.pending_root_secret != Bytes(32, 0xaa));
}

TEST_CASE("a message sent through a replica comes back") {
    TempDir dir;
    Harness harness(dir);
    const CoordinatorConfig config = config_low();

    // Both sides derive from the same root, with send and recv mirrored.
    const Bytes root(32, 0x5a);
    PeerSnapshot sender;
    sender.peer_id = std::string(kRemotePeer);
    sender.root_secret = root;
    sender.root_epoch = 3;

    PeerSnapshot receiver;
    receiver.peer_id = std::string(kLocalPeer);
    receiver.root_secret = root;
    receiver.root_epoch = 3;

    const ByteView frame_view = as_bytes("hello from the offline path");
    const Bytes frame(frame_view.begin(), frame_view.end());
    const SendOutcome outcome = run_step(
        harness.context, send_pairwise(*harness.client, sender, std::string(kLocalPeer),
                                       frame, config, kNow));
    CHECK(outcome.index == 0);
    CHECK(outcome.epoch == 3);
    CHECK(sender.state.send_index == 1);

    const std::vector<ReceivedMessage> received = run_step(
        harness.context, poll_pairwise(*harness.client, receiver,
                                       std::string(kRemotePeer), config, kNow));
    REQUIRE(received.size() == 1);
    CHECK(received[0].frame == frame);
    CHECK(received[0].index == 0);
    CHECK(received[0].epoch == 3);
    CHECK(received[0].lookup_token == outcome.lookup_token);

    // The slot is settled, so a blob still sitting on the replica is not
    // delivered a second time.
    CHECK(receiver.state.recv_base == 1);
    CHECK(run_step(harness.context,
                   poll_pairwise(*harness.client, receiver, std::string(kRemotePeer),
                                 config, kNow))
              .empty());
}

TEST_CASE("a message under a replaced root is still collected") {
    TempDir dir;
    Harness harness(dir);
    CoordinatorConfig config = config_low();
    config.privacy.max_previous_roots = 2;

    const Bytes old_root(32, 0x11);
    const Bytes new_root(32, 0x22);

    PeerSnapshot sender;
    sender.peer_id = std::string(kRemotePeer);
    sender.root_secret = old_root;
    sender.root_epoch = 1;

    // The receiver has already rotated, but keeps the old root for its grace
    // period — which is exactly the message in flight this protects.
    PeerSnapshot receiver;
    receiver.peer_id = std::string(kLocalPeer);
    receiver.root_secret = new_root;
    receiver.root_epoch = 2;
    receiver.prev_roots = {PreviousRoot{1, old_root, kNow + 3600}};

    const ByteView frame_view = as_bytes("sent just before the rotation");
    const Bytes frame(frame_view.begin(), frame_view.end());
    (void)run_step(harness.context,
                   send_pairwise(*harness.client, sender, std::string(kLocalPeer), frame,
                                 config, kNow));

    const std::vector<ReceivedMessage> received = run_step(
        harness.context, poll_pairwise(*harness.client, receiver,
                                       std::string(kRemotePeer), config, kNow));
    REQUIRE(received.size() == 1);
    CHECK(received[0].frame == frame);
    CHECK(received[0].epoch == 1);

    // And once the old root's grace has passed, it is not even asked about.
    receiver.prev_roots = {PreviousRoot{1, old_root, kNow - 1}};
    receiver.state = BlindBoxState{};
    CHECK(run_step(harness.context,
                   poll_pairwise(*harness.client, receiver, std::string(kRemotePeer),
                                 config, kNow))
              .empty());
}

TEST_CASE("several waiting messages are collected in order") {
    TempDir dir;
    Harness harness(dir);
    const CoordinatorConfig config = config_low();

    const Bytes root(32, 0x5a);
    PeerSnapshot sender;
    sender.peer_id = std::string(kRemotePeer);
    sender.root_secret = root;
    PeerSnapshot receiver;
    receiver.peer_id = std::string(kLocalPeer);
    receiver.root_secret = root;

    for (const char* text : {"first", "second", "third"}) {
        const ByteView view = as_bytes(text);
        (void)run_step(harness.context,
                       send_pairwise(*harness.client, sender, std::string(kLocalPeer),
                                     Bytes(view.begin(), view.end()), config, kNow));
    }
    CHECK(sender.state.send_index == 3);

    const std::vector<ReceivedMessage> received = run_step(
        harness.context, poll_pairwise(*harness.client, receiver,
                                       std::string(kRemotePeer), config, kNow));
    REQUIRE(received.size() == 3);
    CHECK(to_string(ByteView(received[0].frame)) == "first");
    CHECK(to_string(ByteView(received[1].frame)) == "second");
    CHECK(to_string(ByteView(received[2].frame)) == "third");
    CHECK(receiver.state.recv_base == 3);
}

TEST_CASE("a group message reaches a member through a replica") {
    TempDir dir;
    Harness harness(dir);
    const CoordinatorConfig config = config_low();

    const Bytes root(32, 0x7c);
    GroupSnapshot sender;
    sender.group_id = "group-alpha";
    sender.group_epoch = 2;
    sender.root_epoch = 1;
    sender.root_secret = root;

    GroupSnapshot receiver = sender;
    receiver.state = BlindBoxState{};

    const ByteView view = as_bytes("group text");
    const SendOutcome outcome = run_step(
        harness.context, send_group(*harness.client, sender, std::string(kLocalPeer),
                                    Bytes(view.begin(), view.end()), config, kNow));
    CHECK(sender.state.send_index == 1);

    // The sender is part of the schedule, so a member polls per sender: this is
    // what keeps two members from colliding on one slot.
    const std::vector<ReceivedMessage> received =
        run_step(harness.context, poll_group(*harness.client, receiver,
                                             std::string(kLocalPeer), config, kNow));
    REQUIRE(received.size() == 1);
    CHECK(to_string(ByteView(received[0].frame)) == "group text");
    CHECK(received[0].lookup_token == outcome.lookup_token);

    // A different sender's slots are a different keyspace entirely.
    GroupSnapshot other = sender;
    other.state = BlindBoxState{};
    CHECK(run_step(harness.context, poll_group(*harness.client, other,
                                               std::string(kRemotePeer), config, kNow))
              .empty());
}

TEST_CASE("a failed send leaves the slot free") {
    TempDir dir;
    boost::asio::io_context context;

    // No replica listening at all: the put cannot reach a quorum.
    ReplicaClientConfig client_config;
    client_config.endpoints = {"127.0.0.1:1"};
    client_config.retry_attempts = 1;
    client_config.io_timeout = std::chrono::milliseconds(200);
    ReplicaClient client(context.get_executor(), client_config,
                         direct_stream_factory(context.get_executor(),
                                               std::chrono::milliseconds(200)));

    PeerSnapshot snapshot;
    snapshot.peer_id = std::string(kRemotePeer);
    snapshot.root_secret = Bytes(32, 0x5a);
    const ByteView view = as_bytes("never lands");

    CHECK_THROWS_AS(run_step(context, send_pairwise(client, snapshot,
                                                    std::string(kLocalPeer),
                                                    Bytes(view.begin(), view.end()),
                                                    config_low(), kNow)),
                    ReplicaError);
    // The index must not advance: a retry has to reuse this slot, or the peer
    // would never look where the message ended up.
    CHECK(snapshot.state.send_index == 0);
}

TEST_CASE("sending without a root or with an impossible frame is refused") {
    TempDir dir;
    Harness harness(dir);
    CoordinatorConfig config = config_low();

    PeerSnapshot rootless;
    rootless.peer_id = std::string(kRemotePeer);
    const ByteView view = as_bytes("payload");
    CHECK_THROWS_AS(run_step(harness.context,
                             send_pairwise(*harness.client, rootless,
                                           std::string(kLocalPeer),
                                           Bytes(view.begin(), view.end()), config, kNow)),
                    BlindBoxError);

    PeerSnapshot snapshot;
    snapshot.peer_id = std::string(kRemotePeer);
    snapshot.root_secret = Bytes(32, 0x5a);
    CHECK_THROWS_AS(run_step(harness.context,
                             send_pairwise(*harness.client, snapshot,
                                           std::string(kLocalPeer), Bytes{}, config, kNow)),
                    BlindBoxError);

    config.max_frame_size = 4;
    const ByteView big = as_bytes("far too long");
    CHECK_THROWS_AS(run_step(harness.context,
                             send_pairwise(*harness.client, snapshot,
                                           std::string(kLocalPeer),
                                           Bytes(big.begin(), big.end()), config, kNow)),
                    BlindBoxError);
    CHECK(snapshot.state.send_index == 0);
}

TEST_CASE("polling a channel with no root asks nothing") {
    TempDir dir;
    Harness harness(dir);

    PeerSnapshot snapshot;
    snapshot.peer_id = std::string(kRemotePeer);
    CHECK(run_step(harness.context,
                   poll_pairwise(*harness.client, snapshot, std::string(kLocalPeer),
                                 config_low(), kNow))
              .empty());
    // Nothing was asked of the replica, so nothing shows up in its counters.
    CHECK(harness.service->metric("get_miss") == 0);
}

TEST_CASE("cover fetches are indistinguishable requests that ignore failure") {
    TempDir dir;
    Harness harness(dir);

    run_step(harness.context,
             [&]() -> boost::asio::awaitable<bool> {
                 co_await emit_cover_gets(*harness.client, 3);
                 co_return true;
             }());

    // Three requests reached the replica, each a miss: that is what makes an
    // idle poll look like a collection.
    CHECK(harness.service->metric("get_miss") == 3);
}
