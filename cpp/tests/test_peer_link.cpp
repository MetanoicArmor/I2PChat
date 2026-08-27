#include <catch2/catch_test_macros.hpp>

#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <chrono>
#include <functional>
#include <thread>
#include <memory>
#include <string>
#include <vector>

#include "i2pchat/encoding.hpp"
#include "i2pchat/runtime/peer_link.hpp"
#include "i2pchat/sam/destination.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::load_vector;
using tcp = boost::asio::ip::tcp;
namespace asio = boost::asio;

namespace {

struct Identity {
    sam::Destination destination;
    crypto::SigningKeyPair signing;
};

sam::Destination base_destination() {
    const auto document = load_vector("sam");
    return sam::Destination::from_public_base64(
        document.at("destination").at("public_base64").get<std::string>());
}

/// A second destination that hashes differently. The session layer does not
/// validate the key material, so perturbing a byte is enough.
sam::Destination other_destination(const sam::Destination& source) {
    Bytes data = source.data();
    data[0] = static_cast<Byte>(data[0] ^ 0xFF);
    return sam::Destination::from_public_base64(
        encoding::i2p_base64_encode(ByteView(data)));
}

runtime::PeerLinkConfig make_config(const Identity& local, const std::string& peer_addr,
                                    session::ConnectionDirection direction) {
    runtime::PeerLinkConfig config;
    config.session.local_dest_base64 = local.destination.base64();
    config.session.direction = direction;
    config.session.handshake.local_addr = local.destination.base32();
    config.session.handshake.peer_addr = peer_addr;
    config.session.handshake.signing_seed = local.signing.seed;
    config.session.handshake.signing_public = local.signing.public_key;
    return config;
}

/// A pair of connected loopback sockets, standing in for two SAM streams.
struct SocketPair {
    tcp::socket client;
    tcp::socket server;
};

SocketPair connected_pair(asio::io_context& context) {
    tcp::acceptor acceptor(context, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));
    tcp::socket client(context);
    client.connect(acceptor.local_endpoint());
    tcp::socket server = acceptor.accept();
    return SocketPair{std::move(client), std::move(server)};
}

/// Records what a link reported, so assertions read as a transcript.
struct Recorder {
    std::vector<std::string> received;
    std::vector<char> types;
    bool established = false;
    std::string closed_reason;
    bool closed = false;
    /// Echoes each received `U` back with a prefix, as the interop peer does.
    bool echo = false;

    runtime::PeerLink::Callbacks callbacks() {
        runtime::PeerLink::Callbacks callbacks;
        callbacks.on_established = [this](runtime::PeerLink&) { established = true; };
        callbacks.on_frame = [this](runtime::PeerLink& link,
                                    const runtime::PeerFrame& frame) {
            types.push_back(frame.msg_type);
            received.push_back(frame.text());
            if (echo && frame.msg_type == 'U') {
                link.send_text('U', "echo:" + frame.text(), frame.msg_id);
            }
        };
        callbacks.on_closed = [this](runtime::PeerLink&, const std::string& reason) {
            closed = true;
            closed_reason = reason;
        };
        return callbacks;
    }
};

/// Runs the context until `predicate` holds or the budget runs out. Poll rather
/// than `run()`: both links keep timers pending forever, so the context never
/// runs dry on its own.
bool run_until(asio::io_context& context, const std::function<bool()>& predicate,
               std::chrono::milliseconds budget = std::chrono::seconds(5)) {
    const auto deadline = std::chrono::steady_clock::now() + budget;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        if (context.poll() == 0) {
            // Nothing runnable: give the OS a moment to complete the pending
            // reads rather than spinning.
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (context.stopped()) {
            context.restart();
        }
    }
    return predicate();
}

}  // namespace

TEST_CASE("peer link carries a handshake and text in both directions") {
    crypto::init();
    const sam::Destination initiator_dest = base_destination();
    const sam::Destination responder_dest = other_destination(initiator_dest);
    const Identity initiator{initiator_dest, crypto::generate_signing_keypair()};
    const Identity responder{responder_dest, crypto::generate_signing_keypair()};

    asio::io_context context;
    SocketPair sockets = connected_pair(context);

    Recorder outbound_log;
    Recorder inbound_log;
    inbound_log.echo = true;

    // The inbound side does not know the peer address until the identity
    // preface arrives, so it is left empty here.
    auto inbound = runtime::PeerLink::create(
        std::move(sockets.server), Bytes{},
        make_config(responder, "", session::ConnectionDirection::Inbound),
        inbound_log.callbacks());
    auto outbound = runtime::PeerLink::create(
        std::move(sockets.client), Bytes{},
        make_config(initiator, responder_dest.base32(),
                    session::ConnectionDirection::Outbound),
        outbound_log.callbacks());

    inbound->start();
    outbound->start();

    REQUIRE(run_until(context, [&] {
        return outbound_log.established && inbound_log.established;
    }));
    CHECK(outbound->secure());
    CHECK(inbound->secure());
    CHECK(inbound->peer_addr() == initiator_dest.base32());

    outbound->send_text('U', "hello", 1);
    REQUIRE(run_until(context, [&] { return !outbound_log.received.empty(); }));
    CHECK(inbound_log.received == std::vector<std::string>{"hello"});
    CHECK(outbound_log.received == std::vector<std::string>{"echo:hello"});

    SECTION("a graceful close reports to both ends") {
        outbound->close_gracefully();
        CHECK(outbound->closed());
        CHECK(outbound_log.closed_reason == "local quit");
        REQUIRE(run_until(context, [&] { return inbound_log.closed; }));
        CHECK(inbound_log.closed_reason == "peer closed the stream");
    }

    SECTION("sending after a close is a no-op rather than a crash") {
        outbound->close("test");
        outbound->send_text('U', "ignored", 2);
        CHECK(inbound_log.received.size() == 1);
    }
}

TEST_CASE("peer link drops a peer that never finishes the handshake") {
    crypto::init();
    const sam::Destination local_dest = base_destination();
    const sam::Destination peer_dest = other_destination(local_dest);
    const Identity local{local_dest, crypto::generate_signing_keypair()};

    asio::io_context context;
    SocketPair sockets = connected_pair(context);

    Recorder log;
    runtime::PeerLinkConfig config =
        make_config(local, peer_dest.base32(), session::ConnectionDirection::Outbound);
    config.handshake_timeout = std::chrono::seconds(0);

    auto link = runtime::PeerLink::create(std::move(sockets.client), Bytes{}, config,
                                          log.callbacks());
    link->start();

    REQUIRE(run_until(context, [&] { return log.closed; }));
    CHECK(log.closed_reason == "handshake timed out");
    CHECK_FALSE(log.established);
}

TEST_CASE("peer link drops a silent peer once the liveness deadline passes") {
    crypto::init();
    const sam::Destination initiator_dest = base_destination();
    const sam::Destination responder_dest = other_destination(initiator_dest);
    const Identity initiator{initiator_dest, crypto::generate_signing_keypair()};
    const Identity responder{responder_dest, crypto::generate_signing_keypair()};

    asio::io_context context;
    SocketPair sockets = connected_pair(context);

    Recorder outbound_log;
    Recorder inbound_log;

    runtime::PeerLinkConfig outbound_config = make_config(
        initiator, responder_dest.base32(), session::ConnectionDirection::Outbound);
    // Keepalives every tick and a deadline that has already passed: the first
    // tick after the handshake must tear the link down.
    outbound_config.keepalive_interval = std::chrono::seconds(0);
    outbound_config.peer_timeout = std::chrono::seconds(0);

    auto inbound = runtime::PeerLink::create(
        std::move(sockets.server), Bytes{},
        make_config(responder, "", session::ConnectionDirection::Inbound),
        inbound_log.callbacks());
    auto outbound = runtime::PeerLink::create(std::move(sockets.client), Bytes{},
                                              outbound_config,
                                              outbound_log.callbacks());
    inbound->start();
    outbound->start();

    REQUIRE(run_until(context, [&] { return outbound_log.closed; }));
    CHECK(outbound_log.closed_reason == "peer timed out");
}
