#pragma once

#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/steady_timer.hpp>
#include <chrono>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <string>

#include "i2pchat/bytes.hpp"
#include "i2pchat/session/peer_session.hpp"

/// One live connection to one peer, with the I/O attached.
///
/// `session::PeerSession` is the protocol; `PeerLink` is the plumbing around it:
/// a read loop, a serialized write queue, keepalives, and the two timeouts that
/// keep a half-dead I2P tunnel from looking healthy forever.
///
/// Every callback runs on the link's executor, in the read loop's coroutine —
/// never concurrently with itself. A callback may call `send` and `close`; it
/// must not block.
namespace i2pchat::runtime {

namespace asio = boost::asio;

/// Keepalive cadence and liveness deadline, both matching the reference client.
inline constexpr std::chrono::seconds kKeepaliveInterval{15};
inline constexpr std::chrono::seconds kPeerTimeout{90};
inline constexpr std::chrono::seconds kHandshakeTimeout{45};

struct PeerLinkConfig {
    session::PeerSessionConfig session;
    std::chrono::seconds keepalive_interval = kKeepaliveInterval;
    std::chrono::seconds peer_timeout = kPeerTimeout;
    std::chrono::seconds handshake_timeout = kHandshakeTimeout;
};

/// Received application frame, after decryption.
struct PeerFrame {
    char msg_type = 0;
    std::uint64_t msg_id = 0;
    Bytes payload;

    [[nodiscard]] std::string text() const {
        return std::string(reinterpret_cast<const char*>(payload.data()), payload.size());
    }
};

class PeerLink : public std::enable_shared_from_this<PeerLink> {
public:
    struct Callbacks {
        /// A decrypted application frame arrived. Keepalives are handled
        /// internally and are not reported here.
        std::function<void(PeerLink&, const PeerFrame&)> on_frame;
        /// The channel became secure. Nothing may be sent before this.
        std::function<void(PeerLink&)> on_established;
        /// The link is finished, for any reason: peer hung up, protocol
        /// violation, timeout, or a local `close`. Fires exactly once.
        std::function<void(PeerLink&, const std::string& reason)> on_closed;
    };

    /// Takes ownership of an already-connected stream. `prebuffered` carries the
    /// bytes that arrived alongside the SAM reply line; feeding them before the
    /// first read is what keeps an inbound peer's identity preface intact.
    static std::shared_ptr<PeerLink> create(asio::ip::tcp::socket socket,
                                            Bytes prebuffered, PeerLinkConfig config,
                                            Callbacks callbacks);

    PeerLink(const PeerLink&) = delete;
    PeerLink& operator=(const PeerLink&) = delete;

    /// Sends the identity preface and starts the read, keepalive and timeout
    /// loops. Returns immediately; everything after this is callback-driven.
    void start();

    /// Queues an application frame. Silently dropped once the link is closed,
    /// which is the only sane behaviour for a fire-and-forget send on a
    /// connection the caller cannot hold a lock on.
    void send(char msg_type, ByteView payload, std::uint64_t msg_id);
    void send_text(char msg_type, std::string_view payload, std::uint64_t msg_id) {
        send(msg_type, as_bytes(payload), msg_id);
    }

    /// Sends `__SIGNAL__:`-prefixed control text in an `S` frame.
    void send_signal(std::string_view signal_payload, std::uint64_t msg_id = 0);

    /// Sends QUIT, then tears the link down.
    void close_gracefully();

    /// Tears the link down now. `reason` is reported to `on_closed`.
    void close(std::string reason = "local close");

    [[nodiscard]] bool secure() const noexcept { return peer_.secure(); }
    [[nodiscard]] bool closed() const noexcept { return closed_; }
    [[nodiscard]] session::PeerState state() const noexcept { return peer_.state(); }
    [[nodiscard]] const std::string& peer_addr() const noexcept {
        return peer_.peer_addr();
    }
    [[nodiscard]] session::ConnectionDirection direction() const noexcept {
        return config_.session.direction;
    }
    /// Steady-clock time of the last byte received, for staleness checks.
    [[nodiscard]] std::chrono::steady_clock::time_point last_seen() const noexcept {
        return last_seen_;
    }
    [[nodiscard]] asio::any_io_executor executor() { return socket_.get_executor(); }

private:
    PeerLink(asio::ip::tcp::socket socket, Bytes prebuffered, PeerLinkConfig config,
             Callbacks callbacks);

    asio::awaitable<void> read_loop();
    asio::awaitable<void> keepalive_loop();
    asio::awaitable<void> handshake_guard();
    /// Drains the outbox. At most one instance runs at a time, which is what
    /// serializes writes on the socket.
    asio::awaitable<void> drain_outbox();

    /// Applies the session's actions: writes, deliveries, teardown. Returns
    /// false once the link is finished.
    bool apply(const session::SessionActions& actions);
    void enqueue(Bytes bytes);
    void finish(std::string reason);

    asio::ip::tcp::socket socket_;
    Bytes prebuffered_;
    PeerLinkConfig config_;
    Callbacks callbacks_;
    session::PeerSession peer_;
    asio::steady_timer keepalive_timer_;
    asio::steady_timer handshake_timer_;
    std::deque<Bytes> outbox_;
    std::chrono::steady_clock::time_point last_seen_;
    std::uint64_t next_keepalive_id_ = 1;
    bool started_ = false;
    bool closed_ = false;
    bool writing_ = false;
};

}  // namespace i2pchat::runtime
