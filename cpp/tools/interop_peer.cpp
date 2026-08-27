/// A minimal peer that speaks the I2PChat wire protocol over plain TCP.
///
/// Its only purpose is to be driven by `tests/test_cpp_interop.py`, which runs
/// the Python 1.4.x crypto and codec modules on the other end of the socket.
/// That gives the Phase 2 gate — handshake and text exchange with the Python
/// client, in both roles — without needing a live I2P network in CI. The real
/// SAM interop run still has to happen on a machine with a router; this covers
/// every byte-level contract that run would exercise.
///
/// Usage:
///   interop_peer --role inbound  --port N --local-dest <b64> --seed <hex>
///   interop_peer --role outbound --port N --local-dest <b64> --seed <hex>
///                --peer-dest <b64>
///
/// It echoes each received `U` message back with an "echo:" prefix, prints
/// diagnostics to stderr, and exits 0 only if the channel became secure and at
/// least one message round-tripped.

#include <boost/asio/connect.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/read.hpp>
#include <boost/asio/write.hpp>
#include <array>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>

#include "i2pchat/encoding.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/session/peer_session.hpp"

namespace asio = boost::asio;
using asio::ip::tcp;
using namespace i2pchat;

namespace {

struct Options {
    std::string role = "inbound";
    unsigned short port = 0;
    std::string local_dest;
    std::string peer_dest;
    std::string seed_hex;
    int messages = 1;
};

std::optional<Options> parse_options(int argc, char** argv) {
    Options options;
    for (int i = 1; i + 1 < argc; i += 2) {
        const std::string key = argv[i];
        const std::string value = argv[i + 1];
        if (key == "--role") {
            options.role = value;
        } else if (key == "--port") {
            options.port = static_cast<unsigned short>(std::stoi(value));
        } else if (key == "--local-dest") {
            options.local_dest = value;
        } else if (key == "--peer-dest") {
            options.peer_dest = value;
        } else if (key == "--seed") {
            options.seed_hex = value;
        } else if (key == "--messages") {
            options.messages = std::stoi(value);
        } else {
            std::cerr << "unknown option " << key << "\n";
            return std::nullopt;
        }
    }
    if (options.port == 0 || options.local_dest.empty() || options.seed_hex.empty()) {
        std::cerr << "missing --port, --local-dest or --seed\n";
        return std::nullopt;
    }
    if (options.role == "outbound" && options.peer_dest.empty()) {
        std::cerr << "an outbound peer needs --peer-dest\n";
        return std::nullopt;
    }
    return options;
}

session::PeerSessionConfig build_config(const Options& options) {
    const Bytes seed = encoding::hex_decode(options.seed_hex).value();

    session::PeerSessionConfig config;
    config.local_dest_base64 = options.local_dest;
    config.direction = options.role == "outbound" ? session::ConnectionDirection::Outbound
                                                  : session::ConnectionDirection::Inbound;
    config.handshake.local_addr =
        sam::Destination::from_public_base64(options.local_dest).base32();
    if (config.direction == session::ConnectionDirection::Outbound) {
        config.handshake.peer_addr =
            sam::Destination::from_public_base64(options.peer_dest).base32();
    }
    config.handshake.signing_seed = seed;
    config.handshake.signing_public = crypto::get_verify_key_from_seed(ByteView(seed));
    return config;
}

tcp::socket open_socket(asio::io_context& context, const Options& options) {
    if (options.role == "outbound") {
        tcp::socket socket(context);
        socket.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), options.port));
        return socket;
    }
    tcp::acceptor acceptor(
        context, tcp::endpoint(asio::ip::make_address("127.0.0.1"), options.port));
    return acceptor.accept();
}

}  // namespace

int main(int argc, char** argv) {
    const std::optional<Options> options = parse_options(argc, argv);
    if (!options.has_value()) {
        return 2;
    }
    crypto::init();

    try {
        asio::io_context context;
        tcp::socket socket = open_socket(context, *options);
        socket.set_option(tcp::no_delay(true));

        session::PeerSession peer(build_config(*options));
        int echoed = 0;

        const auto perform = [&](const session::SessionActions& actions) -> bool {
            for (const auto& action : actions) {
                switch (action.kind) {
                    case session::SessionAction::Kind::Send:
                        asio::write(socket, asio::buffer(action.bytes));
                        break;
                    case session::SessionAction::Kind::Established:
                        std::cerr << "secure channel established\n";
                        break;
                    case session::SessionAction::Kind::Deliver: {
                        std::cerr << "received type " << action.msg_type << " ("
                                  << action.bytes.size() << " bytes)\n";
                        if (action.msg_type == 'U') {
                            const Bytes reply = peer.send_message(
                                'U', as_bytes("echo:" + to_string(ByteView(action.bytes))),
                                action.msg_id);
                            asio::write(socket, asio::buffer(reply));
                            ++echoed;
                        }
                        break;
                    }
                    case session::SessionAction::Kind::Disconnect:
                        std::cerr << "disconnect: " << action.reason << "\n";
                        return false;
                }
            }
            return true;
        };

        if (!perform(peer.on_stream_open())) {
            return 1;
        }

        // Read until the peer closes rather than stopping at the message count:
        // the tests that check a rule violation need us still listening after
        // the well-behaved part of the conversation is over.
        std::array<Byte, 8192> buffer{};
        while (true) {
            boost::system::error_code error;
            const std::size_t read = socket.read_some(asio::buffer(buffer), error);
            if (error == asio::error::eof) {
                break;
            }
            if (error) {
                std::cerr << "read failed: " << error.message() << "\n";
                break;
            }
            if (!perform(peer.on_bytes(ByteView(buffer.data(), read)))) {
                // A disconnect is a successful outcome for the tests that
                // provoke one, so it gets its own exit code.
                return 3;
            }
        }

        if (!peer.secure()) {
            std::cerr << "channel never became secure\n";
            return 1;
        }
        if (echoed < options->messages) {
            std::cerr << "echoed " << echoed << " of " << options->messages
                      << " messages\n";
            return 1;
        }
        std::cerr << "ok\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "fatal: " << error.what() << "\n";
        return 1;
    }
}
