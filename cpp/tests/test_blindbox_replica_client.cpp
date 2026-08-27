#include <boost/asio/io_context.hpp>
#include <catch2/catch_test_macros.hpp>

#include <memory>

#include "fake_replica.hpp"
#include "i2pchat/blindbox/replica_client.hpp"
#include "run_awaitable.hpp"

using namespace i2pchat;
using i2pchat::testing::FakeReplica;
using i2pchat::testing::FakeReplicaOptions;
using i2pchat::testing::run_awaitable;

namespace {

namespace asio = boost::asio;

const std::string kKey = "1375592dc5645d4d4f0688524b92f0b12bfb377d3c97e248e8554721507f6b6c";

Bytes blob_of(std::string_view text) { return to_bytes(text); }

/// A client over the given replica addresses, with the retry and grace timings
/// tightened so tests do not spend seconds waiting for backoff.
std::unique_ptr<blindbox::ReplicaClient> make_client(asio::io_context& context,
                                                     std::vector<std::string> endpoints,
                                                     blindbox::ReplicaClientConfig config = {}) {
    config.endpoints = std::move(endpoints);
    config.retry_backoff_base = std::chrono::milliseconds(5);
    config.io_timeout = std::chrono::milliseconds(2000);
    config.get_first_accept_grace = std::chrono::milliseconds(100);
    return std::make_unique<blindbox::ReplicaClient>(
        context.get_executor(), config,
        blindbox::direct_stream_factory(context.get_executor(),
                                        std::chrono::milliseconds(1000)));
}

/// An address nothing is listening on: a port is bound, then released.
std::string dead_address(asio::io_context& context) {
    asio::ip::tcp::acceptor acceptor(
        context, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));
    const std::uint16_t port = acceptor.local_endpoint().port();
    acceptor.close();
    return "127.0.0.1:" + std::to_string(port);
}

}  // namespace

TEST_CASE("a blob is stored on a replica and read back", "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica replica;
    auto client = make_client(context, {replica.address()});
    const Bytes blob = blob_of("sealed blindbox frame");

    const auto results = run_awaitable(context, client->put(kKey, blob));
    REQUIRE(results.size() == 1);
    CHECK(results[0].address == replica.address());
    CHECK(results[0].status == blindbox::PutStatus::Ok);
    CHECK(replica.stored(kKey) == blob);

    const auto blobs = run_awaitable(context, client->get(kKey));
    REQUIRE(blobs.size() == 1);
    CHECK(blobs[0] == blob);

    const auto commands = replica.commands();
    REQUIRE(commands.size() == 2);
    CHECK(commands[0] == "PUT " + kKey + " " + std::to_string(blob.size()));
    CHECK(commands[1] == "GET " + kKey);
}

TEST_CASE("an empty mailbox is not an error", "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica replica;
    auto client = make_client(context, {replica.address()});

    CHECK(run_awaitable(context, client->get(kKey)).empty());
}

TEST_CASE("replicas holding the same blob report it once", "[blindbox][replica]") {
    // Redundancy is the point of several replicas, so duplicates are expected
    // and the caller should not have to filter them.
    asio::io_context context;
    FakeReplica first;
    FakeReplica second;
    FakeReplica third;
    const Bytes blob = blob_of("sealed blindbox frame");
    const Bytes other = blob_of("a different frame entirely");
    first.store(kKey, blob);
    second.store(kKey, blob);
    third.store(kKey, other);

    blindbox::ReplicaClientConfig config;
    config.get_quorum = 3;
    auto client =
        make_client(context, {first.address(), second.address(), third.address()}, config);

    const auto blobs = run_awaitable(context, client->get(kKey));
    CHECK(blobs.size() == 2);
}

TEST_CASE("a store that only reaches too few replicas fails", "[blindbox][replica]") {
    // Reporting success here would lose the message: the sender would move on
    // believing it was handed off.
    asio::io_context context;
    FakeReplica replica;
    blindbox::ReplicaClientConfig config;
    config.put_quorum = 2;
    config.retry_attempts = 1;
    auto client =
        make_client(context, {replica.address(), dead_address(context)}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, blob_of("frame"))),
                    blindbox::ReplicaError);
}

TEST_CASE("a read that cannot reach a quorum fails rather than reporting empty",
          "[blindbox][replica]") {
    // "Nothing waiting" and "could not ask" must not look the same, or a peer
    // would appear to have sent nothing while its messages sat unread.
    asio::io_context context;
    FakeReplica replica;
    blindbox::ReplicaClientConfig config;
    config.get_quorum = 2;
    config.retry_attempts = 1;
    auto client =
        make_client(context, {replica.address(), dead_address(context)}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->get(kKey)), blindbox::ReplicaError);
    // Without the quorum requirement the same call reports what it could read.
    CHECK(run_awaitable(context, client->get(kKey, /*require_quorum=*/false)).empty());
}

TEST_CASE("an occupied slot holding our own blob counts as stored",
          "[blindbox][replica]") {
    // A retry after a connection dropped mid-answer lands on a slot that is
    // already correct, which is delivery, not collision.
    asio::io_context context;
    FakeReplica replica;
    const Bytes blob = blob_of("sealed blindbox frame");
    replica.store(kKey, blob);
    auto client = make_client(context, {replica.address()});

    const auto results = run_awaitable(context, client->put(kKey, blob));
    REQUIRE(results.size() == 1);
    CHECK(results[0].status == blindbox::PutStatus::Exists);
}

TEST_CASE("an occupied slot holding a different blob does not count as stored",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica replica;
    replica.store(kKey, blob_of("somebody else's frame"));
    auto client = make_client(context, {replica.address()});

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, blob_of("our frame"))),
                    blindbox::ReplicaError);
}

TEST_CASE("the quorum fanout verifies each occupied slot", "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica good;
    FakeReplica squatted;
    const Bytes blob = blob_of("our frame");
    good.store(kKey, blob);
    squatted.store(kKey, blob_of("somebody else's frame"));

    blindbox::ReplicaClientConfig config;
    config.put_quorum = 2;
    config.retry_attempts = 1;
    auto client = make_client(context, {good.address(), squatted.address()}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, blob)),
                    blindbox::ReplicaError);
}

TEST_CASE("a replica that drops the first connection is retried",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplicaOptions options;
    options.fail_first_connections = 1;
    FakeReplica replica(options);

    blindbox::ReplicaClientConfig config;
    config.retry_attempts = 3;
    auto client = make_client(context, {replica.address()}, config);
    const Bytes blob = blob_of("frame");

    // put() with a quorum of one deliberately makes a single attempt per
    // replica, so the retry is observed through get().
    replica.store(kKey, blob);
    const auto blobs = run_awaitable(context, client->get(kKey));
    REQUIRE(blobs.size() == 1);
    CHECK(blobs[0] == blob);
    CHECK(replica.connections() == 2);
}

TEST_CASE("a replica that talks nonsense is treated as unreachable",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplicaOptions options;
    options.garbage_replies = true;
    FakeReplica replica(options);

    blindbox::ReplicaClientConfig config;
    config.retry_attempts = 1;
    auto client = make_client(context, {replica.address()}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, blob_of("frame"))),
                    blindbox::ReplicaError);
    CHECK_THROWS_AS(run_awaitable(context, client->get(kKey)), blindbox::ReplicaError);
}

TEST_CASE("a replica cannot make the client allocate a huge buffer",
          "[blindbox][replica]") {
    // The declared size is a claim from an untrusted party. Believing it would
    // let any replica exhaust the client's memory with one line of text.
    asio::io_context context;
    FakeReplicaOptions options;
    options.lie_about_size = 64ull * 1024 * 1024;
    FakeReplica replica(options);
    replica.store(kKey, blob_of("frame"));

    blindbox::ReplicaClientConfig config;
    config.retry_attempts = 1;
    auto client = make_client(context, {replica.address()}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->get(kKey)), blindbox::ReplicaError);
}

TEST_CASE("a truncated blob is refused rather than returned short",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplicaOptions options;
    options.truncate_blob = true;
    FakeReplica replica(options);
    replica.store(kKey, blob_of("a frame long enough to be cut short"));

    blindbox::ReplicaClientConfig config;
    config.retry_attempts = 1;
    auto client = make_client(context, {replica.address()}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->get(kKey)), blindbox::ReplicaError);
}

TEST_CASE("the first acceptable blob wins and the rest are abandoned",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica quick;
    FakeReplicaOptions slow_options;
    slow_options.delay = std::chrono::milliseconds(400);
    FakeReplica slow(slow_options);

    const Bytes blob = blob_of("sealed blindbox frame");
    quick.store(kKey, blob);
    slow.store(kKey, blob);

    auto client = make_client(context, {quick.address(), slow.address()});

    blindbox::GetFirstDiagnostics diagnostics;
    const auto found = run_awaitable(
        context, client->get_first_accepted(
                     kKey, [](ByteView) { return true; }, std::nullopt, &diagnostics));

    REQUIRE(found.has_value());
    CHECK(*found == blob);
    CHECK(diagnostics.first_result_kind == "blob");
    CHECK(diagnostics.accepted_address == quick.address());
    CHECK(diagnostics.cancelled == std::vector<std::string>{slow.address()});
}

TEST_CASE("a blob the caller does not want does not end the search",
          "[blindbox][replica]") {
    // The poll asks for one index at a time, and a replica may still hold an
    // older blob in that slot; accepting it would replay a message.
    asio::io_context context;
    FakeReplica stale;
    FakeReplica fresh;
    const Bytes wanted = blob_of("the frame we are waiting for");
    stale.store(kKey, blob_of("a stale frame"));
    fresh.store(kKey, wanted);

    auto client = make_client(context, {stale.address(), fresh.address()});

    const auto found = run_awaitable(
        context, client->get_first_accepted(kKey, [&wanted](ByteView blob) {
            return Bytes(blob.begin(), blob.end()) == wanted;
        }));

    REQUIRE(found.has_value());
    CHECK(*found == wanted);
}

TEST_CASE("no acceptable blob anywhere returns nothing", "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica first;
    FakeReplica second;
    auto client = make_client(context, {first.address(), second.address()});

    blindbox::GetFirstDiagnostics diagnostics;
    const auto found = run_awaitable(
        context, client->get_first_accepted(
                     kKey, [](ByteView) { return true; }, std::nullopt, &diagnostics));

    CHECK_FALSE(found.has_value());
    CHECK(diagnostics.first_result_kind == "miss");
    CHECK(diagnostics.completed.size() == 2);
}

TEST_CASE("a replica that does not answer in time is given up on",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica quick;
    FakeReplicaOptions stalled_options;
    stalled_options.delay = std::chrono::milliseconds(1500);
    FakeReplica stalled(stalled_options);
    stalled.store(kKey, blob_of("too late"));

    blindbox::ReplicaClientConfig config;
    config.get_first_accept_grace = std::chrono::milliseconds(50);
    auto client = make_client(context, {quick.address(), stalled.address()}, config);
    // make_client overrides the grace, so pass it explicitly.
    blindbox::GetFirstDiagnostics diagnostics;
    const auto found = run_awaitable(
        context,
        client->get_first_accepted(kKey, [](ByteView) { return true; },
                                   std::chrono::milliseconds(50), &diagnostics));

    CHECK_FALSE(found.has_value());
    CHECK(diagnostics.cancelled == std::vector<std::string>{stalled.address()});
}

TEST_CASE("a loopback replica gets the local token and a remote one does not",
          "[blindbox][replica]") {
    // The local token authorises writes to the user's own replica. Sending it
    // to a box on the network would hand that authority away.
    asio::io_context context;
    FakeReplicaOptions options;
    options.required_token = "s3cret";
    FakeReplica replica(options);

    blindbox::ReplicaClientConfig config;
    config.local_auth_token = "s3cret";
    auto client = make_client(context, {replica.address()}, config);

    const Bytes blob = blob_of("frame");
    run_awaitable(context, client->put(kKey, blob));
    CHECK(replica.stored(kKey) == blob);
    CHECK(replica.commands().front().ends_with(" s3cret"));

    CHECK(client->auth_token_for(replica.address()) == "s3cret");
    CHECK(client->auth_token_for("someboxaddress.b32.i2p:19444").empty());
}

TEST_CASE("a per-replica token is used even for a remote replica",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplicaOptions options;
    options.required_token = "per-box";
    FakeReplica replica(options);

    blindbox::ReplicaClientConfig config;
    config.local_auth_token = "loopback-only";
    config.replica_auth = {{replica.address(), "per-box"}};
    auto client = make_client(context, {replica.address()}, config);

    run_awaitable(context, client->put(kKey, blob_of("frame")));
    CHECK(replica.commands().front().ends_with(" per-box"));
}

TEST_CASE("a replica that rejects the token is not treated as a store",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplicaOptions options;
    options.required_token = "s3cret";
    FakeReplica replica(options);

    blindbox::ReplicaClientConfig config;
    config.retry_attempts = 1;
    auto client = make_client(context, {replica.address()}, config);

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, blob_of("frame"))),
                    blindbox::ReplicaError);
    CHECK_FALSE(replica.stored(kKey).has_value());
}

TEST_CASE("a key the replica protocol cannot express is refused",
          "[blindbox][replica]") {
    // A space in a key would be read by the replica as the start of an auth
    // token, and a newline as the start of another command.
    CHECK(blindbox::validate_lookup_key("  " + kKey + " ") == kKey);
    CHECK_THROWS_AS(blindbox::validate_lookup_key(""), blindbox::ReplicaError);
    CHECK_THROWS_AS(blindbox::validate_lookup_key("token with space"),
                    blindbox::ReplicaError);
    CHECK_THROWS_AS(blindbox::validate_lookup_key("token\nGET other"),
                    blindbox::ReplicaError);
    CHECK_THROWS_AS(blindbox::validate_lookup_key(std::string("token\0", 6)),
                    blindbox::ReplicaError);
}

TEST_CASE("an empty blob is refused before any replica is contacted",
          "[blindbox][replica]") {
    asio::io_context context;
    FakeReplica replica;
    auto client = make_client(context, {replica.address()});

    CHECK_THROWS_AS(run_awaitable(context, client->put(kKey, Bytes{})),
                    blindbox::ReplicaError);
    CHECK(replica.commands().empty());
}

TEST_CASE("a client that cannot honour its own configuration is refused",
          "[blindbox][replica]") {
    asio::io_context context;
    const auto build = [&](blindbox::ReplicaClientConfig config) {
        return blindbox::ReplicaClient(
            context.get_executor(), std::move(config),
            blindbox::direct_stream_factory(context.get_executor()));
    };

    CHECK_THROWS_AS(build({}), blindbox::ReplicaError);

    blindbox::ReplicaClientConfig too_large;
    too_large.endpoints = {"127.0.0.1:19444"};
    too_large.put_quorum = 2;
    CHECK_THROWS_AS(build(too_large), blindbox::ReplicaError);

    blindbox::ReplicaClientConfig no_attempts;
    no_attempts.endpoints = {"127.0.0.1:19444"};
    no_attempts.retry_attempts = 0;
    CHECK_THROWS_AS(build(no_attempts), blindbox::ReplicaError);

    blindbox::ReplicaClientConfig no_factory;
    no_factory.endpoints = {"127.0.0.1:19444"};
    CHECK_THROWS_AS(blindbox::ReplicaClient(context.get_executor(), no_factory, nullptr),
                    blindbox::ReplicaError);
}

TEST_CASE("a replica address is split into a SAM destination and a port hint",
          "[blindbox][replica]") {
    // `host.b32.i2p:19444` names the replica's TCP port for direct mode. Passing
    // the suffix to SAM STREAM CONNECT is rejected by the router as an invalid
    // key, which is a confusing way to learn about a parsing bug.
    CHECK(blindbox::sam_destination_from_endpoint("abc.b32.i2p:19444") == "abc.b32.i2p");
    CHECK(blindbox::sam_destination_from_endpoint(" abc.b32.i2p ") == "abc.b32.i2p");
    CHECK(blindbox::sam_destination_from_endpoint("host.i2p:1234") == "host.i2p");
    CHECK(blindbox::sam_destination_from_endpoint("127.0.0.1:19444") == "127.0.0.1:19444");
    CHECK(blindbox::sam_destination_from_endpoint("base64destination-with~chars") ==
          "base64destination-with~chars");
}

TEST_CASE("loopback replicas are recognised in every spelling",
          "[blindbox][replica]") {
    CHECK(blindbox::is_loopback_endpoint("127.0.0.1:19444"));
    CHECK(blindbox::is_loopback_endpoint("localhost:19444"));
    CHECK(blindbox::is_loopback_endpoint("[::1]:19444"));
    CHECK(blindbox::is_loopback_endpoint("LOCALHOST"));
    CHECK_FALSE(blindbox::is_loopback_endpoint("192.168.1.5:19444"));
    CHECK_FALSE(blindbox::is_loopback_endpoint("abc.b32.i2p:19444"));
}
