#pragma once

#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <chrono>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/sam/protocol.hpp"

namespace i2pchat::sam {

namespace asio = boost::asio;

struct SamEndpoint {
    std::string host = "127.0.0.1";
    std::uint16_t port = 7656;
};

struct SessionOptions {
    /// Session id, unique per router. Must not contain spaces or '='.
    std::string session_id;
    /// I2P-base64 private destination, or "TRANSIENT" for a throwaway identity.
    std::string destination{kTransientDestination};
    /// I2CP and streaming options passed through as name=value tokens.
    std::vector<std::pair<std::string, std::string>> options;
    std::optional<int> sig_type = kSigTypeEd25519;
};

/// An established I2P stream.
///
/// `prebuffered` holds bytes that arrived in the same read as the SAM reply
/// line. Reading a line from a socket can consume past the newline, so those
/// bytes must be processed before touching the socket again — dropping them
/// silently truncates the peer's first frame, which is exactly where the
/// identity preface lives.
struct SamStream {
    asio::ip::tcp::socket socket;
    Bytes prebuffered;
    /// Peer's I2P-base64 destination. Populated for accepted streams only.
    std::string peer_destination;
};

/// A SAM v3 session.
///
/// The control socket must stay open for the session's lifetime: closing it
/// tears down the session and every stream belonging to it. Streams are carried
/// on their own TCP connections.
class SamSession {
public:
    SamSession(asio::any_io_executor executor, SamEndpoint endpoint);
    ~SamSession();

    SamSession(const SamSession&) = delete;
    SamSession& operator=(const SamSession&) = delete;

    /// HELLO followed by SESSION CREATE on a freshly opened control socket.
    asio::awaitable<void> open(SessionOptions options);

    /// Ask the router for a new destination keypair. Uses its own short-lived
    /// connection so it can be called before a session exists.
    asio::awaitable<Destination> generate_destination(int sig_type = kSigTypeEd25519);

    /// Resolve a hostname or b32 address to a full destination.
    asio::awaitable<std::string> naming_lookup(std::string name);

    /// Outbound stream to `destination` (I2P-base64 or a .b32.i2p host).
    asio::awaitable<SamStream> connect_stream(std::string destination);

    /// Wait for one inbound stream. SAM accepts one connection per ACCEPT, so
    /// call this again for the next peer.
    asio::awaitable<SamStream> accept_stream();

    void close();

    [[nodiscard]] bool is_open() const;
    /// Local destination as reported by SESSION STATUS, when the router sends it.
    [[nodiscard]] const std::string& local_destination() const noexcept {
        return local_destination_;
    }
    [[nodiscard]] const std::string& session_id() const noexcept { return session_id_; }

    /// Per-operation timeout. Applies to connect, command write and reply read.
    void set_timeout(std::chrono::steady_clock::duration timeout) { timeout_ = timeout; }

private:
    asio::awaitable<asio::ip::tcp::socket> dial();
    asio::awaitable<std::shared_ptr<SamStream>> dial_with_hello();

    asio::any_io_executor executor_;
    SamEndpoint endpoint_;
    std::string session_id_;
    std::string local_destination_;
    std::optional<asio::ip::tcp::socket> control_;
    std::chrono::steady_clock::duration timeout_{std::chrono::seconds(30)};
};

/// Probe whether a SAM router is reachable and speaks the protocol. Used both
/// for the readiness check after starting a bundled router and for diagnostics.
asio::awaitable<bool> probe_sam(asio::any_io_executor executor, SamEndpoint endpoint,
                                std::chrono::steady_clock::duration timeout =
                                    std::chrono::seconds(5));

}  // namespace i2pchat::sam
