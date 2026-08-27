#pragma once

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <array>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "i2pchat/bytes.hpp"

namespace i2pchat::testing {

namespace asio = boost::asio;
using asio::ip::tcp;

/// How a fake replica should misbehave, if at all.
struct FakeReplicaOptions {
    /// When set, commands without this exact token are answered "ERR AUTH".
    std::string required_token;
    /// Held before answering, for exercising quorum races and grace periods.
    std::chrono::milliseconds delay{0};
    /// Connections closed without a reply, counted from the first. Lets a test
    /// see the retry loop recover.
    int fail_first_connections = 0;
    /// Answer every command with a line the client cannot parse.
    bool garbage_replies = false;
    /// Claim this many bytes in the GET header regardless of what is stored.
    std::optional<std::size_t> lie_about_size;
    /// Send the GET header, then close before the blob.
    bool truncate_blob = false;
};

/// An in-process BlindBox replica.
///
/// Runs on its own io_context and thread, because an accept loop is permanently
/// outstanding work: sharing the client's context would mean `run()` never
/// returns and every test hangs.
class FakeReplica {
public:
    explicit FakeReplica(FakeReplicaOptions options = {})
        : options_(std::move(options)),
          acceptor_(context_, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0)) {
        acceptor_.listen();
        port_ = acceptor_.local_endpoint().port();
        asio::co_spawn(context_, run(), asio::detached);
        thread_ = std::thread([this] { context_.run(); });
    }

    ~FakeReplica() { stop(); }

    FakeReplica(const FakeReplica&) = delete;
    FakeReplica& operator=(const FakeReplica&) = delete;

    [[nodiscard]] std::uint16_t port() const noexcept { return port_; }
    [[nodiscard]] std::string address() const {
        return "127.0.0.1:" + std::to_string(port_);
    }

    /// Seed a slot, for the case where a message is already waiting.
    void store(const std::string& key, Bytes blob) {
        const std::lock_guard<std::mutex> lock(mutex_);
        slots_[key] = std::move(blob);
    }

    [[nodiscard]] std::optional<Bytes> stored(const std::string& key) const {
        const std::lock_guard<std::mutex> lock(mutex_);
        const auto found = slots_.find(key);
        if (found == slots_.end()) {
            return std::nullopt;
        }
        return found->second;
    }

    /// Every command line received, in order.
    [[nodiscard]] std::vector<std::string> commands() const {
        const std::lock_guard<std::mutex> lock(mutex_);
        return commands_;
    }

    [[nodiscard]] int connections() const {
        const std::lock_guard<std::mutex> lock(mutex_);
        return connections_;
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

    asio::awaitable<std::string> read_line(tcp::socket& socket, std::string& buffer) {
        std::size_t newline = buffer.find('\n');
        while (newline == std::string::npos) {
            std::array<char, 1024> chunk{};
            const std::size_t read =
                co_await socket.async_read_some(asio::buffer(chunk), asio::use_awaitable);
            buffer.append(chunk.data(), read);
            newline = buffer.find('\n');
        }
        std::string line = buffer.substr(0, newline);
        buffer.erase(0, newline + 1);
        while (!line.empty() && (line.back() == '\r')) {
            line.pop_back();
        }
        co_return line;
    }

    asio::awaitable<Bytes> read_body(tcp::socket& socket, std::string& buffer,
                                     std::size_t size) {
        while (buffer.size() < size) {
            std::array<char, 4096> chunk{};
            const std::size_t read =
                co_await socket.async_read_some(asio::buffer(chunk), asio::use_awaitable);
            buffer.append(chunk.data(), read);
        }
        Bytes body(buffer.begin(), buffer.begin() + static_cast<std::ptrdiff_t>(size));
        buffer.erase(0, size);
        co_return body;
    }

    asio::awaitable<void> serve(std::shared_ptr<tcp::socket> socket) {
        {
            const std::lock_guard<std::mutex> lock(mutex_);
            ++connections_;
            if (connections_ <= options_.fail_first_connections) {
                co_return;
            }
        }

        std::string buffer;
        try {
            while (true) {
                const std::string command = co_await read_line(*socket, buffer);
                std::vector<std::string> parts;
                std::size_t start = 0;
                while (start <= command.size()) {
                    const std::size_t space = command.find(' ', start);
                    if (space == std::string::npos) {
                        parts.push_back(command.substr(start));
                        break;
                    }
                    parts.push_back(command.substr(start, space - start));
                    start = space + 1;
                }

                {
                    const std::lock_guard<std::mutex> lock(mutex_);
                    commands_.push_back(command);
                }

                std::string reply;
                Bytes body;
                if (parts.empty()) {
                    reply = "ERR\n";
                } else if (options_.garbage_replies) {
                    reply = "WAT\n";
                } else if (parts[0] == "PUT" && parts.size() >= 3) {
                    const std::size_t size = std::stoul(parts[2]);
                    const Bytes blob = co_await read_body(*socket, buffer, size);
                    const std::string token = parts.size() > 3 ? parts[3] : "";
                    if (!options_.required_token.empty() &&
                        token != options_.required_token) {
                        reply = "ERR AUTH\n";
                    } else {
                        const std::lock_guard<std::mutex> lock(mutex_);
                        if (slots_.count(parts[1]) != 0) {
                            reply = "EXISTS\n";
                        } else {
                            slots_[parts[1]] = blob;
                            reply = "OK\n";
                        }
                    }
                } else if (parts[0] == "GET" && parts.size() >= 2) {
                    const std::string token = parts.size() > 2 ? parts[2] : "";
                    if (!options_.required_token.empty() &&
                        token != options_.required_token) {
                        reply = "ERR AUTH\n";
                    } else {
                        std::optional<Bytes> blob;
                        {
                            const std::lock_guard<std::mutex> lock(mutex_);
                            const auto found = slots_.find(parts[1]);
                            if (found != slots_.end()) {
                                blob = found->second;
                            }
                        }
                        if (!blob) {
                            reply = "MISS\n";
                        } else if (options_.lie_about_size) {
                            reply = "OK " + std::to_string(*options_.lie_about_size) + "\n";
                        } else if (options_.truncate_blob) {
                            reply = "OK " + std::to_string(blob->size()) + "\n";
                            body.assign(blob->begin(), blob->begin() + 1);
                        } else {
                            reply = "OK " + std::to_string(blob->size()) + "\n";
                            body = *blob;
                        }
                    }
                } else {
                    reply = "ERR\n";
                }

                if (options_.delay > std::chrono::milliseconds::zero()) {
                    asio::steady_timer timer(context_);
                    timer.expires_after(options_.delay);
                    co_await timer.async_wait(asio::use_awaitable);
                }

                co_await asio::async_write(*socket, asio::buffer(reply),
                                           asio::use_awaitable);
                if (!body.empty()) {
                    co_await asio::async_write(*socket, asio::buffer(body),
                                               asio::use_awaitable);
                }
                if (options_.truncate_blob || options_.lie_about_size) {
                    co_return;
                }
            }
        } catch (const std::exception&) {
            // The client closed its side, which is the normal end of an
            // exchange: it opens one connection per command.
        }
    }

    FakeReplicaOptions options_;
    asio::io_context context_;
    tcp::acceptor acceptor_;
    std::uint16_t port_ = 0;
    std::thread thread_;
    bool stopped_ = false;
    mutable std::mutex mutex_;
    std::map<std::string, Bytes> slots_;
    std::vector<std::string> commands_;
    int connections_ = 0;
};

}  // namespace i2pchat::testing
