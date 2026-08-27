#pragma once

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/read.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <array>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/sam/destination.hpp"

/// A fake SAM v3 router that actually carries streams between two clients.
///
/// `FakeSamServer` scripts replies and is right for testing the SAM client in
/// isolation. This one is for testing everything above it: two `ChatService`
/// instances connect to the same router, one dials the other's destination, and
/// the router splices the two TCP connections so a real handshake, real frames
/// and real transfers travel between them. No I2P, no threads in the test's
/// executor, and the whole conversation is observable.
///
/// The destinations it hands out are synthetic — random bytes shaped like an
/// I2P destination, with a zero-length certificate — because nothing above the
/// SAM layer inspects the key material. What matters is that a destination
/// hashes to a stable base32 address, which it does.
namespace i2pchat::testing {

namespace asio = boost::asio;
using asio::ip::tcp;

class FakeSamRouter {
public:
    FakeSamRouter()
        : acceptor_(context_, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0)) {
        crypto::init();
        acceptor_.listen();
        port_ = acceptor_.local_endpoint().port();
        asio::co_spawn(context_, accept_loop(), asio::detached);
        thread_ = std::thread([this] { context_.run(); });
    }

    ~FakeSamRouter() { stop(); }

    FakeSamRouter(const FakeSamRouter&) = delete;
    FakeSamRouter& operator=(const FakeSamRouter&) = delete;

    [[nodiscard]] std::uint16_t port() const noexcept { return port_; }

    /// Every command line the router received, in order.
    [[nodiscard]] std::vector<std::string> commands() const {
        const std::lock_guard<std::mutex> lock(mutex_);
        return commands_;
    }

    void stop() {
        if (stopped_) {
            return;
        }
        stopped_ = true;
        asio::post(context_, [this] {
            boost::system::error_code ignored;
            acceptor_.close(ignored);
        });
        context_.stop();
        if (thread_.joinable()) {
            thread_.join();
        }
    }

    /// A destination with private key material, shaped like the real thing.
    static sam::Destination generate_destination() {
        Bytes blob = crypto::random_bytes(sam::kPublicPrefixLen + 64);
        // Zero-length certificate, so the public part is exactly the prefix.
        blob[sam::kCertLenOffset] = 0;
        blob[sam::kCertLenOffset + 1] = 0;
        return sam::Destination::from_private_blob(ByteView(blob));
    }

private:
    struct Session {
        std::string id;
        /// Public destination this session is reachable at.
        std::string destination;
        /// Sockets parked in STREAM ACCEPT, oldest first.
        std::deque<std::shared_ptr<tcp::socket>> waiting_accepts;
    };

    asio::awaitable<void> accept_loop() {
        while (acceptor_.is_open()) {
            boost::system::error_code error;
            tcp::socket socket = co_await acceptor_.async_accept(
                asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                co_return;
            }
            asio::co_spawn(context_,
                           serve(std::make_shared<tcp::socket>(std::move(socket))),
                           asio::detached);
        }
    }

    static std::string field(const std::string& command, const std::string& key) {
        const std::string needle = key + "=";
        const std::size_t at = command.find(needle);
        if (at == std::string::npos) {
            return {};
        }
        const std::size_t start = at + needle.size();
        const std::size_t end = command.find(' ', start);
        return command.substr(start, end == std::string::npos ? std::string::npos
                                                              : end - start);
    }

    asio::awaitable<std::string> read_line(tcp::socket& socket, std::string& buffer) {
        while (true) {
            const std::size_t newline = buffer.find('\n');
            if (newline != std::string::npos) {
                const std::string line = buffer.substr(0, newline);
                buffer.erase(0, newline + 1);
                co_return line;
            }
            std::array<char, 1024> chunk{};
            boost::system::error_code error;
            const std::size_t read = co_await socket.async_read_some(
                asio::buffer(chunk), asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                co_return std::string{};
            }
            buffer.append(chunk.data(), read);
        }
    }

    asio::awaitable<void> write(tcp::socket& socket, std::string text) {
        boost::system::error_code ignored;
        co_await asio::async_write(socket, asio::buffer(text),
                                   asio::redirect_error(asio::use_awaitable, ignored));
    }

    /// One control or stream connection. A connection is a control connection
    /// until a STREAM command turns it into one half of a spliced pair.
    asio::awaitable<void> serve(std::shared_ptr<tcp::socket> socket) {
        std::string buffer;
        while (true) {
            const std::string command = co_await read_line(*socket, buffer);
            if (command.empty()) {
                co_return;
            }
            {
                const std::lock_guard<std::mutex> lock(mutex_);
                commands_.push_back(command);
            }

            if (command.rfind("HELLO", 0) == 0) {
                co_await write(*socket, "HELLO REPLY RESULT=OK VERSION=3.1\n");
                continue;
            }
            if (command.rfind("DEST GENERATE", 0) == 0) {
                const sam::Destination destination = generate_destination();
                co_await write(*socket, "DEST REPLY PUB=" + destination.base64() +
                                            " PRIV=" + destination.private_key_base64() +
                                            "\n");
                continue;
            }
            if (command.rfind("SESSION CREATE", 0) == 0) {
                const std::string id = field(command, "ID");
                const std::string private_destination = field(command, "DESTINATION");
                std::string public_destination = private_destination;
                try {
                    public_destination =
                        sam::Destination::from_private_base64(private_destination).base64();
                } catch (const std::exception&) {
                    // A transient session sends "TRANSIENT"; give it one.
                    public_destination = generate_destination().base64();
                }
                auto session = std::make_shared<Session>();
                session->id = id;
                session->destination = public_destination;
                sessions_.emplace(id, session);
                co_await write(*socket,
                               "SESSION STATUS RESULT=OK DESTINATION=" +
                                   public_destination + "\n");
                continue;
            }
            if (command.rfind("NAMING LOOKUP", 0) == 0) {
                const std::string name = field(command, "NAME");
                const std::string resolved = destination_for_host(name);
                if (resolved.empty()) {
                    co_await write(*socket,
                                   "NAMING REPLY RESULT=KEY_NOT_FOUND NAME=" + name + "\n");
                    continue;
                }
                co_await write(*socket, "NAMING REPLY RESULT=OK NAME=" + name +
                                            " VALUE=" + resolved + "\n");
                continue;
            }
            if (command.rfind("STREAM ACCEPT", 0) == 0) {
                const std::string id = field(command, "ID");
                const auto session = sessions_.find(id);
                if (session == sessions_.end()) {
                    co_await write(*socket, "STREAM STATUS RESULT=INVALID_ID\n");
                    co_return;
                }
                co_await write(*socket, "STREAM STATUS RESULT=OK\n");
                session->second->waiting_accepts.push_back(socket);
                // Ownership passes to whichever CONNECT picks it up.
                co_return;
            }
            if (command.rfind("STREAM CONNECT", 0) == 0) {
                const std::string id = field(command, "ID");
                const std::string destination = field(command, "DESTINATION");
                co_await connect_streams(socket, id, destination);
                co_return;
            }
            co_await write(*socket, "RESULT=I2P_ERROR MESSAGE=unsupported\n");
        }
    }

    [[nodiscard]] std::string destination_for_host(const std::string& host) const {
        const std::string wanted = std::string(sam::normalize_peer_address(host));
        for (const auto& [id, session] : sessions_) {
            try {
                if (sam::Destination::from_public_base64(session->destination).base32() ==
                    wanted) {
                    return session->destination;
                }
            } catch (const std::exception&) {
                continue;
            }
        }
        return {};
    }

    asio::awaitable<void> connect_streams(std::shared_ptr<tcp::socket> caller,
                                          const std::string& caller_id,
                                          const std::string& destination) {
        std::string target_destination = destination;
        if (destination.find(".b32.i2p") != std::string::npos ||
            destination.size() < 100) {
            target_destination = destination_for_host(destination);
        }

        std::shared_ptr<Session> target;
        for (const auto& [id, session] : sessions_) {
            if (session->destination == target_destination) {
                target = session;
                break;
            }
        }
        if (!target || target->waiting_accepts.empty()) {
            // No listener parked in ACCEPT: the router reports the peer as
            // unreachable, exactly as it would for a destination with no
            // lease set.
            co_await write(*caller, "STREAM STATUS RESULT=CANT_REACH_PEER\n");
            co_return;
        }

        const std::shared_ptr<tcp::socket> callee = target->waiting_accepts.front();
        target->waiting_accepts.pop_front();

        const auto caller_session = sessions_.find(caller_id);
        const std::string caller_destination =
            caller_session == sessions_.end() ? std::string{}
                                              : caller_session->second->destination;

        co_await write(*caller, "STREAM STATUS RESULT=OK\n");
        // The accepting side is told who is calling, on its own line.
        co_await write(*callee, caller_destination + "\n");

        asio::co_spawn(context_, splice(caller, callee), asio::detached);
        asio::co_spawn(context_, splice(callee, caller), asio::detached);
    }

    asio::awaitable<void> splice(std::shared_ptr<tcp::socket> from,
                                 std::shared_ptr<tcp::socket> to) {
        std::array<Byte, 8192> chunk{};
        while (true) {
            boost::system::error_code error;
            const std::size_t read = co_await from->async_read_some(
                asio::buffer(chunk), asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                boost::system::error_code ignored;
                to->shutdown(tcp::socket::shutdown_send, ignored);
                co_return;
            }
            co_await asio::async_write(*to, asio::buffer(chunk.data(), read),
                                       asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                co_return;
            }
        }
    }

    asio::io_context context_;
    tcp::acceptor acceptor_;
    std::uint16_t port_ = 0;
    std::thread thread_;
    bool stopped_ = false;
    /// Touched only from the router's own thread.
    std::map<std::string, std::shared_ptr<Session>> sessions_;
    mutable std::mutex mutex_;
    std::vector<std::string> commands_;
};

}  // namespace i2pchat::testing
