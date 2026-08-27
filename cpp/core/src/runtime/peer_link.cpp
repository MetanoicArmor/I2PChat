#include "i2pchat/runtime/peer_link.hpp"

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <array>
#include <utility>

#include "i2pchat/protocol/signals.hpp"

namespace i2pchat::runtime {
namespace {

constexpr std::size_t kReadBuffer = 16 * 1024;

}  // namespace

PeerLink::PeerLink(asio::ip::tcp::socket socket, Bytes prebuffered,
                   PeerLinkConfig config, Callbacks callbacks)
    : socket_(std::move(socket)),
      prebuffered_(std::move(prebuffered)),
      config_(std::move(config)),
      callbacks_(std::move(callbacks)),
      peer_(config_.session),
      keepalive_timer_(socket_.get_executor()),
      handshake_timer_(socket_.get_executor()),
      last_seen_(std::chrono::steady_clock::now()) {}

std::shared_ptr<PeerLink> PeerLink::create(asio::ip::tcp::socket socket,
                                           Bytes prebuffered, PeerLinkConfig config,
                                           Callbacks callbacks) {
    return std::shared_ptr<PeerLink>(new PeerLink(std::move(socket),
                                                  std::move(prebuffered),
                                                  std::move(config),
                                                  std::move(callbacks)));
}

void PeerLink::start() {
    if (started_ || closed_) {
        return;
    }
    started_ = true;

    boost::system::error_code ignored;
    socket_.set_option(asio::ip::tcp::no_delay(true), ignored);

    if (!apply(peer_.on_stream_open())) {
        return;
    }

    auto self = shared_from_this();
    asio::co_spawn(socket_.get_executor(), [self] { return self->read_loop(); },
                   asio::detached);
    asio::co_spawn(socket_.get_executor(), [self] { return self->keepalive_loop(); },
                   asio::detached);
    asio::co_spawn(socket_.get_executor(), [self] { return self->handshake_guard(); },
                   asio::detached);
}

void PeerLink::send(char msg_type, ByteView payload, std::uint64_t msg_id) {
    if (closed_ || !peer_.secure()) {
        return;
    }
    try {
        enqueue(peer_.send_message(msg_type, payload, msg_id));
    } catch (const std::exception& error) {
        finish(std::string("send failed: ") + error.what());
    }
}

void PeerLink::send_signal(std::string_view signal_payload, std::uint64_t msg_id) {
    const std::string body = protocol::signal_body(signal_payload);
    send('S', as_bytes(body), msg_id);
}

void PeerLink::close_gracefully() {
    if (!closed_ && peer_.secure()) {
        send_signal(protocol::build_quit());
    }
    close("local quit");
}

void PeerLink::close(std::string reason) { finish(std::move(reason)); }

void PeerLink::enqueue(Bytes bytes) {
    if (closed_ || bytes.empty()) {
        return;
    }
    outbox_.push_back(std::move(bytes));
    if (writing_) {
        return;
    }
    writing_ = true;
    auto self = shared_from_this();
    asio::co_spawn(socket_.get_executor(), [self] { return self->drain_outbox(); },
                   asio::detached);
}

asio::awaitable<void> PeerLink::drain_outbox() {
    while (!closed_ && !outbox_.empty()) {
        const Bytes frame = std::move(outbox_.front());
        outbox_.pop_front();
        boost::system::error_code error;
        co_await asio::async_write(socket_, asio::buffer(frame),
                                   asio::redirect_error(asio::use_awaitable, error));
        if (error) {
            finish("write failed: " + error.message());
            break;
        }
    }
    writing_ = false;
}

bool PeerLink::apply(const session::SessionActions& actions) {
    for (const session::SessionAction& action : actions) {
        switch (action.kind) {
            case session::SessionAction::Kind::Send:
                enqueue(action.bytes);
                break;
            case session::SessionAction::Kind::Established:
                handshake_timer_.cancel();
                if (callbacks_.on_established) {
                    callbacks_.on_established(*this);
                }
                break;
            case session::SessionAction::Kind::Deliver: {
                // Keepalives are transport bookkeeping; the read loop has
                // already refreshed `last_seen_` for them.
                if (action.msg_type == 'P' || action.msg_type == 'O') {
                    break;
                }
                if (callbacks_.on_frame) {
                    PeerFrame frame;
                    frame.msg_type = action.msg_type;
                    frame.msg_id = action.msg_id;
                    frame.payload = action.bytes;
                    callbacks_.on_frame(*this, frame);
                }
                break;
            }
            case session::SessionAction::Kind::Disconnect:
                finish(action.reason.empty() ? "peer disconnect" : action.reason);
                return false;
        }
        if (closed_) {
            return false;
        }
    }
    return !closed_;
}

asio::awaitable<void> PeerLink::read_loop() {
    if (!prebuffered_.empty()) {
        const Bytes initial = std::move(prebuffered_);
        prebuffered_.clear();
        try {
            if (!apply(peer_.on_bytes(ByteView(initial)))) {
                co_return;
            }
        } catch (const std::exception& error) {
            finish(std::string("protocol error: ") + error.what());
            co_return;
        }
    }

    std::array<Byte, kReadBuffer> buffer{};
    while (!closed_) {
        boost::system::error_code error;
        const std::size_t read = co_await socket_.async_read_some(
            asio::buffer(buffer), asio::redirect_error(asio::use_awaitable, error));
        if (error) {
            finish(error == asio::error::eof ? "peer closed the stream"
                                             : "read failed: " + error.message());
            co_return;
        }
        last_seen_ = std::chrono::steady_clock::now();
        try {
            if (!apply(peer_.on_bytes(ByteView(buffer.data(), read)))) {
                co_return;
            }
        } catch (const std::exception& failure) {
            // A framing or crypto violation is fatal by design: the reference
            // implementation never tries to resynchronize a broken channel.
            finish(std::string("protocol error: ") + failure.what());
            co_return;
        }
    }
}

asio::awaitable<void> PeerLink::keepalive_loop() {
    while (!closed_) {
        keepalive_timer_.expires_after(config_.keepalive_interval);
        boost::system::error_code error;
        co_await keepalive_timer_.async_wait(
            asio::redirect_error(asio::use_awaitable, error));
        if (error || closed_) {
            co_return;
        }
        if (!peer_.secure()) {
            continue;
        }
        if (std::chrono::steady_clock::now() - last_seen_ > config_.peer_timeout) {
            finish("peer timed out");
            co_return;
        }
        try {
            enqueue(peer_.build_keepalive(next_keepalive_id_++));
        } catch (const std::exception& failure) {
            finish(std::string("keepalive failed: ") + failure.what());
            co_return;
        }
    }
}

asio::awaitable<void> PeerLink::handshake_guard() {
    handshake_timer_.expires_after(config_.handshake_timeout);
    boost::system::error_code error;
    co_await handshake_timer_.async_wait(
        asio::redirect_error(asio::use_awaitable, error));
    if (error || closed_ || peer_.secure()) {
        co_return;
    }
    finish("handshake timed out");
}

void PeerLink::finish(std::string reason) {
    if (closed_) {
        return;
    }
    closed_ = true;

    boost::system::error_code ignored;
    socket_.shutdown(asio::ip::tcp::socket::shutdown_both, ignored);
    socket_.close(ignored);
    keepalive_timer_.cancel();
    handshake_timer_.cancel();
    outbox_.clear();

    if (callbacks_.on_closed) {
        callbacks_.on_closed(*this, reason);
    }
}

}  // namespace i2pchat::runtime
