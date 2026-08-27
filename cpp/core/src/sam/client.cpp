#include "i2pchat/sam/client.hpp"

#include <boost/asio/cancel_after.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/read.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>

#include "net/line_reader.hpp"

#include <algorithm>
#include <optional>

namespace i2pchat::sam {
namespace {

using asio::ip::tcp;

/// A reply-line reader that turns a truncated connection into a SAM error.
///
/// Losing bytes that arrive in the same read as the reply line would truncate
/// the peer's first frame, which is where the identity preface lives; the
/// surplus handling lives in `net::LineReader`.
class LineReader {
public:
    explicit LineReader(tcp::socket& socket) : inner_(socket) {}

    asio::awaitable<std::string> read_line() {
        std::optional<std::string> line = co_await inner_.read_line();
        if (!line) {
            throw SamError(SamErrorKind::Protocol, "SAM connection closed mid-line", "");
        }
        co_return *line;
    }

    [[nodiscard]] Bytes take_surplus() { return inner_.take_surplus(); }

private:
    net::LineReader inner_;
};

asio::awaitable<void> write_all(tcp::socket& socket, const std::string& text) {
    co_await asio::async_write(socket, asio::buffer(text), asio::use_awaitable);
}

/// Send a command and parse the single reply line it produces, checking that
/// the reply belongs to the command that was issued.
asio::awaitable<SamReply> exchange(tcp::socket& socket, LineReader& reader,
                                   const std::string& command,
                                   std::string_view expected_command) {
    co_await write_all(socket, command);
    const std::string line = co_await reader.read_line();
    const SamReply reply = parse_reply_line(line);
    if (reply.command != expected_command) {
        throw SamError(SamErrorKind::Protocol,
                       "Expected " + std::string(expected_command) + " reply, got " +
                           reply.command,
                       reply.raw_line);
    }
    expect_ok(reply);
    co_return reply;
}

}  // namespace

SamSession::SamSession(asio::any_io_executor executor, SamEndpoint endpoint)
    : executor_(std::move(executor)), endpoint_(std::move(endpoint)) {}

SamSession::~SamSession() { close(); }

asio::awaitable<tcp::socket> SamSession::dial() {
    tcp::socket socket(executor_);
    boost::system::error_code parse_error;
    const auto address = asio::ip::make_address(endpoint_.host, parse_error);
    if (!parse_error) {
        co_await socket.async_connect(tcp::endpoint(address, endpoint_.port),
                                      asio::cancel_after(timeout_, asio::use_awaitable));
    } else {
        tcp::resolver resolver(executor_);
        const auto results = co_await resolver.async_resolve(
            endpoint_.host, std::to_string(endpoint_.port),
            asio::cancel_after(timeout_, asio::use_awaitable));
        co_await asio::async_connect(socket, results,
                                     asio::cancel_after(timeout_, asio::use_awaitable));
    }
    boost::system::error_code ignored;
    socket.set_option(tcp::no_delay(true), ignored);
    co_return socket;
}

asio::awaitable<void> SamSession::open(SessionOptions options) {
    if (options.session_id.empty()) {
        throw SamError(SamErrorKind::Protocol, "SAM session id is required", "");
    }

    tcp::socket socket = co_await dial();
    LineReader reader(socket);
    co_await exchange(socket, reader, build_hello(), "HELLO");

    const SamReply reply = co_await exchange(
        socket, reader,
        build_session_create("STREAM", options.session_id, options.destination,
                             options.options, options.sig_type),
        "SESSION");
    if (const auto destination = reply.field("DESTINATION")) {
        local_destination_ = *destination;
    }

    session_id_ = options.session_id;
    // The control socket stays open: closing it destroys the session and every
    // stream that belongs to it.
    control_.emplace(std::move(socket));
}

asio::awaitable<Destination> SamSession::generate_destination(int sig_type) {
    tcp::socket socket = co_await dial();
    LineReader reader(socket);
    co_await exchange(socket, reader, build_hello(), "HELLO");
    const SamReply reply =
        co_await exchange(socket, reader, build_dest_generate(sig_type), "DEST");

    const auto priv = reply.field("PRIV");
    if (!priv.has_value() || priv->empty()) {
        throw SamError(SamErrorKind::Protocol, "DEST REPLY carries no PRIV",
                       reply.raw_line);
    }
    co_return Destination::from_private_base64(*priv);
}

asio::awaitable<std::string> SamSession::naming_lookup(std::string name) {
    tcp::socket socket = co_await dial();
    LineReader reader(socket);
    co_await exchange(socket, reader, build_hello(), "HELLO");
    const SamReply reply =
        co_await exchange(socket, reader, build_naming_lookup(name), "NAMING");

    const auto value = reply.field("VALUE");
    if (!value.has_value() || value->empty()) {
        throw SamError(SamErrorKind::KeyNotFound, "NAMING REPLY carries no VALUE",
                       reply.raw_line);
    }
    co_return *value;
}

asio::awaitable<SamStream> SamSession::connect_stream(std::string destination) {
    if (session_id_.empty()) {
        throw SamError(SamErrorKind::Protocol, "SAM session is not open", "");
    }
    tcp::socket socket = co_await dial();
    LineReader reader(socket);
    co_await exchange(socket, reader, build_hello(), "HELLO");
    co_await exchange(socket, reader, build_stream_connect(session_id_, destination),
                      "STREAM");

    co_return SamStream{std::move(socket), reader.take_surplus(), {}};
}

asio::awaitable<SamStream> SamSession::accept_stream() {
    if (session_id_.empty()) {
        throw SamError(SamErrorKind::Protocol, "SAM session is not open", "");
    }
    tcp::socket socket = co_await dial();
    LineReader reader(socket);
    co_await exchange(socket, reader, build_hello(), "HELLO");
    co_await exchange(socket, reader, build_stream_accept(session_id_), "STREAM");

    // With SILENT=false the router sends the peer's destination on its own line
    // once a peer actually connects. Waiting for that is unbounded by design,
    // so no timeout applies.
    const std::string peer_destination = co_await reader.read_line();
    if (peer_destination.empty()) {
        throw SamError(SamErrorKind::Protocol,
                       "STREAM ACCEPT did not report a peer destination", "");
    }

    co_return SamStream{std::move(socket), reader.take_surplus(), peer_destination};
}

void SamSession::close() {
    if (control_.has_value()) {
        boost::system::error_code ignored;
        control_->shutdown(tcp::socket::shutdown_both, ignored);
        control_->close(ignored);
        control_.reset();
    }
}

bool SamSession::is_open() const { return control_.has_value() && control_->is_open(); }

asio::awaitable<bool> probe_sam(asio::any_io_executor executor, SamEndpoint endpoint,
                                std::chrono::steady_clock::duration timeout) {
    SamSession session(executor, std::move(endpoint));
    session.set_timeout(timeout);
    try {
        // A router that completes HELLO is ready to serve sessions. NAMING
        // LOOKUP for "ME" is the cheapest way to drive a full exchange.
        co_await session.naming_lookup("ME");
        co_return true;
    } catch (const SamError& error) {
        // Any protocol-level answer proves the router is alive; only transport
        // failures mean "not ready".
        co_return error.kind() != SamErrorKind::Protocol;
    } catch (const std::exception&) {
        co_return false;
    }
}

}  // namespace i2pchat::sam
