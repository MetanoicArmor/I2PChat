#pragma once

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/executor_work_guard.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <array>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "i2pchat/bytes.hpp"

namespace i2pchat::testing {

namespace asio = boost::asio;
using asio::ip::tcp;

/// A minimal in-process SAM v3 router for exercising the client without I2P.
///
/// The server runs on its own io_context and thread. Sharing the client's
/// context would not work: an accept loop is permanently outstanding work, so
/// `io_context::run()` would never return and every test would hang.
///
/// Behaviour is scripted per connection: the handler receives each command line
/// and returns the reply to send. That keeps tests explicit about the router
/// dialect being simulated — including i2pd's habit of omitting RESULT=OK —
/// rather than hiding it in a general-purpose mock.
class FakeSamServer {
public:
    /// Returns the reply for `command`, or an empty string to send nothing.
    /// `history` accumulates the commands seen on this connection.
    using Handler = std::function<std::string(const std::string& command,
                                             std::vector<std::string>& history)>;

    explicit FakeSamServer(Handler handler)
        : handler_(std::move(handler)),
          acceptor_(context_, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0)) {
        acceptor_.listen();
        port_ = acceptor_.local_endpoint().port();
        asio::co_spawn(context_, run(), asio::detached);
        thread_ = std::thread([this] { context_.run(); });
    }

    ~FakeSamServer() { stop(); }

    FakeSamServer(const FakeSamServer&) = delete;
    FakeSamServer& operator=(const FakeSamServer&) = delete;

    [[nodiscard]] std::uint16_t port() const noexcept { return port_; }

    /// Every command line received, across all connections.
    [[nodiscard]] std::vector<std::string> received() const {
        const std::lock_guard<std::mutex> lock(mutex_);
        return received_;
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

private:
    asio::awaitable<void> run() {
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

    asio::awaitable<void> serve(std::shared_ptr<tcp::socket> socket) {
        std::string buffer;
        std::vector<std::string> history;
        try {
            while (true) {
                std::size_t newline = buffer.find('\n');
                while (newline == std::string::npos) {
                    std::array<char, 1024> chunk{};
                    const std::size_t read = co_await socket->async_read_some(
                        asio::buffer(chunk), asio::use_awaitable);
                    buffer.append(chunk.data(), read);
                    newline = buffer.find('\n');
                }
                const std::string command = buffer.substr(0, newline);
                buffer.erase(0, newline + 1);

                {
                    const std::lock_guard<std::mutex> lock(mutex_);
                    received_.push_back(command);
                }
                history.push_back(command);

                const std::string reply = handler_(command, history);
                if (!reply.empty()) {
                    co_await asio::async_write(*socket, asio::buffer(reply),
                                               asio::use_awaitable);
                }
            }
        } catch (const std::exception&) {
            // Client disconnected or the scripted exchange finished.
        }
    }

    Handler handler_;
    asio::io_context context_;
    tcp::acceptor acceptor_;
    std::uint16_t port_ = 0;
    std::thread thread_;
    bool stopped_ = false;
    mutable std::mutex mutex_;
    std::vector<std::string> received_;
};

/// Handler covering the happy path of every command the client issues.
inline FakeSamServer::Handler default_sam_handler(
    std::string session_destination = "local-destination-b64") {
    return [session_destination](const std::string& command,
                                 std::vector<std::string>&) -> std::string {
        if (command.rfind("HELLO", 0) == 0) {
            return "HELLO REPLY RESULT=OK VERSION=3.1\n";
        }
        if (command.rfind("SESSION CREATE", 0) == 0) {
            // i2pd frequently omits RESULT=OK here.
            return "SESSION STATUS DESTINATION=" + session_destination + "\n";
        }
        if (command.rfind("NAMING LOOKUP", 0) == 0) {
            return "NAMING REPLY RESULT=OK NAME=host VALUE=resolved-destination-b64\n";
        }
        if (command.rfind("STREAM CONNECT", 0) == 0) {
            return "STREAM STATUS RESULT=OK\n";
        }
        if (command.rfind("STREAM ACCEPT", 0) == 0) {
            // Status line, peer destination and the first payload bytes in a
            // single write. That is what makes over-reading past a line
            // boundary observable to the test.
            return "STREAM STATUS RESULT=OK\npeer-destination-b64\nHELLO-PAYLOAD";
        }
        return "";
    };
}

}  // namespace i2pchat::testing
