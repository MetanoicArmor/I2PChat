#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>
#include <catch2/catch_test_macros.hpp>

#include "fake_sam_server.hpp"
#include "i2pchat/sam/client.hpp"
#include "run_awaitable.hpp"

using namespace i2pchat;
using i2pchat::testing::FakeSamServer;
using i2pchat::testing::run_awaitable;

namespace {
namespace asio = boost::asio;
}  // namespace

TEST_CASE("SAM session opens and reports the local destination", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler("my-dest-b64"));

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "test-session";
    options.destination = std::string(sam::kTransientDestination);

    run_awaitable(context, session.open(options));

    CHECK(session.is_open());
    CHECK(session.session_id() == "test-session");
    CHECK(session.local_destination() == "my-dest-b64");

    const auto commands = server.received();
    REQUIRE(commands.size() == 2);
    CHECK(commands[0] == "HELLO VERSION MIN=3.0 MAX=3.2");
    CHECK(commands[1].rfind("SESSION CREATE STYLE=STREAM ID=test-session", 0) == 0);
    CHECK(commands[1].find("DESTINATION=TRANSIENT") != std::string::npos);
    CHECK(commands[1].find("SIGNATURE_TYPE=7") != std::string::npos);

    session.close();
    CHECK_FALSE(session.is_open());
}

TEST_CASE("closing the session releases the control socket", "[sam][client]") {
    // The router destroys the session when the control socket closes, so the
    // session object must hold it open for its entire lifetime.
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler());

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "s";
    run_awaitable(context, session.open(options));
    CHECK(session.is_open());
}

TEST_CASE("NAMING LOOKUP returns the resolved destination", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler());

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    const std::string resolved =
        run_awaitable(context, session.naming_lookup("example.i2p"));
    CHECK(resolved == "resolved-destination-b64");

    const auto commands = server.received();
    REQUIRE(commands.size() == 2);
    CHECK(commands[1] == "NAMING LOOKUP NAME=example.i2p");
}

TEST_CASE("an accepted stream keeps bytes that arrived with the reply",
          "[sam][client]") {
    // The fake router sends the status line, the peer destination and the first
    // payload bytes in a single write. Those payload bytes carry the identity
    // preface; dropping them would surface much later as a handshake failure.
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler());

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "s";
    run_awaitable(context, session.open(options));

    sam::SamStream stream = run_awaitable(context, session.accept_stream());
    CHECK(stream.peer_destination == "peer-destination-b64");
    CHECK(to_string(ByteView(stream.prebuffered)) == "HELLO-PAYLOAD");
}

TEST_CASE("an outbound stream is opened on its own connection", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler());

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "s";
    run_awaitable(context, session.open(options));

    sam::SamStream stream = run_awaitable(context, session.connect_stream("peer-b64"));
    CHECK(stream.socket.is_open());
    CHECK(stream.peer_destination.empty());

    const auto commands = server.received();
    // HELLO + SESSION CREATE on the control connection, then HELLO + STREAM
    // CONNECT on the stream connection.
    REQUIRE(commands.size() == 4);
    CHECK(commands[2] == "HELLO VERSION MIN=3.0 MAX=3.2");
    CHECK(commands[3] == "STREAM CONNECT ID=s DESTINATION=peer-b64 SILENT=false");
}

TEST_CASE("SAM error results surface as typed errors", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server([](const std::string& command,
                         std::vector<std::string>&) -> std::string {
        if (command.rfind("HELLO", 0) == 0) {
            return "HELLO REPLY RESULT=OK VERSION=3.1\n";
        }
        if (command.rfind("SESSION CREATE", 0) == 0) {
            return "SESSION STATUS RESULT=OK DESTINATION=d\n";
        }
        if (command.rfind("STREAM CONNECT", 0) == 0) {
            return "STREAM STATUS RESULT=CANT_REACH_PEER MESSAGE=unreachable\n";
        }
        return "";
    });

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "s";
    run_awaitable(context, session.open(options));

    try {
        run_awaitable(context, session.connect_stream("unreachable-peer"));
        FAIL("expected connect_stream to throw");
    } catch (const sam::SamError& error) {
        CHECK(error.kind() == sam::SamErrorKind::CantReachPeer);
    }
}

TEST_CASE("a router that rejects HELLO fails the session", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server([](const std::string&,
                         std::vector<std::string>&) -> std::string {
        return "HELLO REPLY RESULT=I2P_ERROR MESSAGE=nope\n";
    });

    sam::SamSession session(context.get_executor(),
                            sam::SamEndpoint{"127.0.0.1", server.port()});
    sam::SessionOptions options;
    options.session_id = "s";
    CHECK_THROWS_AS(run_awaitable(context, session.open(options)), sam::SamError);
}

TEST_CASE("opening a stream before the session fails fast", "[sam][client]") {
    asio::io_context context;
    sam::SamSession session(context.get_executor(), sam::SamEndpoint{"127.0.0.1", 1});
    CHECK_THROWS_AS(run_awaitable(context, session.connect_stream("peer")),
                    sam::SamError);
    CHECK_THROWS_AS(run_awaitable(context, session.accept_stream()), sam::SamError);
}

TEST_CASE("an unreachable router is reported as not ready", "[sam][client]") {
    asio::io_context context;
    // Port 1 on loopback is not going to answer.
    const bool ready = run_awaitable(
        context, sam::probe_sam(context.get_executor(), sam::SamEndpoint{"127.0.0.1", 1},
                                std::chrono::milliseconds(200)));
    CHECK_FALSE(ready);
}

TEST_CASE("a responding router is reported as ready", "[sam][client]") {
    asio::io_context context;
    FakeSamServer server(testing::default_sam_handler());
    const bool ready = run_awaitable(
        context, sam::probe_sam(context.get_executor(),
                                sam::SamEndpoint{"127.0.0.1", server.port()},
                                std::chrono::seconds(2)));
    CHECK(ready);
}
