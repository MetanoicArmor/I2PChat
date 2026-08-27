#pragma once

#include <boost/asio/awaitable.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <chrono>
#include <cstddef>
#include <optional>
#include <string>

#include "i2pchat/bytes.hpp"

namespace i2pchat::net {

namespace asio = boost::asio;

/// Newline-oriented reader over a stream socket.
///
/// Both protocols spoken over plain sockets here — SAM v3 and the BlindBox
/// replica protocol — put a text line first and raw bytes right after it. A
/// socket read does not respect line boundaries, so bytes that arrive in the
/// same read as the line must be retained: they are already payload. Losing
/// them truncates whatever came next, which looks like a protocol bug rather
/// than an I/O one. Keeping the surplus in one place makes that impossible to
/// forget.
class LineReader {
public:
    /// `timeout` bounds each individual read. Without one a peer that opens a
    /// connection and says nothing keeps the operation pending forever.
    explicit LineReader(asio::ip::tcp::socket& socket, Bytes prebuffered = {},
                        std::optional<std::chrono::milliseconds> timeout = std::nullopt)
        : socket_(socket), buffer_(std::move(prebuffered)), timeout_(timeout) {}

    /// One line without its terminator, or nothing when the peer closed the
    /// connection before sending one.
    asio::awaitable<std::optional<std::string>> read_line();

    /// Exactly `count` bytes, or nothing when the connection ended early.
    asio::awaitable<std::optional<Bytes>> read_exactly(std::size_t count);

    /// Bytes read past the last consumed line. Belongs to the data stream.
    [[nodiscard]] Bytes take_surplus() { return std::move(buffer_); }

private:
    /// One read into `buffer_`. Returns false at end of stream or on timeout.
    asio::awaitable<bool> fill(std::size_t at_least);

    asio::ip::tcp::socket& socket_;
    Bytes buffer_;
    std::optional<std::chrono::milliseconds> timeout_;
};

}  // namespace i2pchat::net
