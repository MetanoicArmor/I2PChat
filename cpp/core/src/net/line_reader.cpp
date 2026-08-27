#include "net/line_reader.hpp"

#include <boost/asio/buffer.hpp>
#include <boost/asio/cancel_after.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>

#include <algorithm>

namespace i2pchat::net {
namespace {

constexpr std::size_t kReadChunk = 4096;

}  // namespace

asio::awaitable<bool> LineReader::fill(std::size_t at_least) {
    Bytes chunk(std::max(kReadChunk, at_least));
    boost::system::error_code error;
    std::size_t read = 0;
    if (timeout_) {
        read = co_await socket_.async_read_some(
            asio::buffer(chunk),
            asio::cancel_after(*timeout_,
                               asio::redirect_error(asio::use_awaitable, error)));
    } else {
        read = co_await socket_.async_read_some(
            asio::buffer(chunk), asio::redirect_error(asio::use_awaitable, error));
    }
    if (error || read == 0) {
        co_return false;
    }
    buffer_.insert(buffer_.end(), chunk.begin(),
                   chunk.begin() + static_cast<std::ptrdiff_t>(read));
    co_return true;
}

asio::awaitable<std::optional<std::string>> LineReader::read_line() {
    while (true) {
        const auto newline =
            std::find(buffer_.begin(), buffer_.end(), static_cast<Byte>('\n'));
        if (newline != buffer_.end()) {
            std::string line(buffer_.begin(), newline);
            buffer_.erase(buffer_.begin(), newline + 1);
            while (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            co_return line;
        }

        if (!co_await fill(kReadChunk)) {
            co_return std::nullopt;
        }
    }
}

asio::awaitable<std::optional<Bytes>> LineReader::read_exactly(std::size_t count) {
    while (buffer_.size() < count) {
        if (!co_await fill(count - buffer_.size())) {
            co_return std::nullopt;
        }
    }

    Bytes result(buffer_.begin(), buffer_.begin() + static_cast<std::ptrdiff_t>(count));
    buffer_.erase(buffer_.begin(), buffer_.begin() + static_cast<std::ptrdiff_t>(count));
    co_return result;
}

}  // namespace i2pchat::net
