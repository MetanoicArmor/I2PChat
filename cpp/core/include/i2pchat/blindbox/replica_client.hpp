#pragma once

#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "i2pchat/blindbox/blob.hpp"
#include "i2pchat/bytes.hpp"
#include "i2pchat/sam/client.hpp"

/// Client for a set of BlindBox replicas — the store-and-forward boxes that
/// hold a message until its recipient is next online.
///
/// A replica speaks a two-command line protocol on a stream:
///
///   PUT <lookup_token> <size> [auth_token]\n  followed by exactly size bytes
///     -> "OK" or "EXISTS"
///   GET <lookup_token> [auth_token]\n
///     -> "MISS", or "OK <size>\n" followed by exactly size bytes
///
/// A replica sees only opaque tokens and sealed blobs: the lookup token is
/// derived from a shared root secret (see key_schedule.hpp) and the blob is
/// encrypted under a per-message key. It learns neither who is talking to whom
/// nor when a message is read, so replicas are deliberately untrusted and
/// several are used at once.
///
/// Redundancy is expressed as quorums. A write must land on `put_quorum` boxes
/// to count as sent; a read must reach `get_quorum` boxes before a miss is
/// believed to mean "nothing waiting" rather than "could not ask".
namespace i2pchat::blindbox {

namespace asio = boost::asio;

class ReplicaError : public std::runtime_error {
public:
    explicit ReplicaError(const std::string& message) : std::runtime_error(message) {}
};

enum class PutStatus {
    /// The replica stored the blob.
    Ok,
    /// The slot already held a blob. Only counts towards the quorum once the
    /// stored bytes are read back and found identical, since a slot holding
    /// somebody else's blob means the message was not delivered.
    Exists,
};

struct PutResult {
    std::string address;
    PutStatus status = PutStatus::Ok;
};

/// Opens a stream to one replica address. Provided by the caller so the same
/// client works over SAM, over plain TCP for a loopback replica, and over a
/// socket pair in tests.
using StreamFactory = std::function<asio::awaitable<sam::SamStream>(std::string address)>;

/// Decides whether a fetched blob is the one being waited for. Used by
/// `get_first_accepted` to stop as soon as any replica answers usefully,
/// instead of waiting out the slowest one.
using BlobAcceptor = std::function<bool(ByteView blob)>;

struct ReplicaClientConfig {
    std::vector<std::string> endpoints;
    std::size_t put_quorum = 1;
    std::size_t get_quorum = 1;
    /// Attempts per replica, including the first. Backoff doubles each time.
    int retry_attempts = 3;
    std::chrono::milliseconds retry_backoff_base{250};
    std::chrono::milliseconds io_timeout{15000};
    /// Once one replica has answered, how long to keep waiting for a better
    /// answer from a slower one in `get_first_accepted`.
    std::chrono::milliseconds get_first_accept_grace{1200};
    /// Sent to loopback replicas that have no entry in `replica_auth`. A
    /// non-loopback box never receives it: the token would then travel to a
    /// host that has no business holding it.
    std::string local_auth_token;
    /// Per-endpoint tokens, keyed by the exact address string in `endpoints`.
    std::map<std::string, std::string> replica_auth;
    std::size_t max_blob_size = kMaxBlobFrameSize;
};

/// Diagnostics for one `get_first_accepted` call, for the BlindBox screens.
struct GetFirstDiagnostics {
    std::vector<std::string> completed;
    /// "blob", "miss", "error" or "timeout" — whichever came back first.
    std::string first_result_kind;
    std::string first_result_address;
    std::string accepted_address;
    std::vector<std::string> cancelled;
    std::chrono::milliseconds elapsed{0};
};

class ReplicaClient {
public:
    /// Throws `ReplicaError` when the configuration cannot be satisfied, for
    /// instance a quorum larger than the number of replicas.
    ReplicaClient(asio::any_io_executor executor, ReplicaClientConfig config,
                  StreamFactory stream_factory);
    ~ReplicaClient();

    ReplicaClient(const ReplicaClient&) = delete;
    ReplicaClient& operator=(const ReplicaClient&) = delete;

    /// Store `blob` under `key` on at least `put_quorum` replicas.
    ///
    /// Throws `ReplicaError` when the quorum is not reached; the returned list
    /// holds every replica that answered, quorum or not.
    asio::awaitable<std::vector<PutResult>> put(std::string key, Bytes blob);

    /// Fetch every distinct blob stored under `key`.
    ///
    /// Replicas that hold nothing are not failures; replicas that cannot be
    /// reached are. With `require_quorum`, fewer than `get_quorum` reachable
    /// replicas throws rather than reporting an empty mailbox.
    asio::awaitable<std::vector<Bytes>> get(std::string key, bool require_quorum = true);

    /// Ask every replica at once and return the first blob `accept` approves.
    ///
    /// Returns nothing when no replica holds an acceptable blob. This is the
    /// hot path of the receive poll: one usable answer is enough, so the slow
    /// replicas are abandoned rather than awaited.
    asio::awaitable<std::optional<Bytes>> get_first_accepted(
        std::string key, BlobAcceptor accept,
        std::optional<std::chrono::milliseconds> grace = std::nullopt,
        GetFirstDiagnostics* diagnostics = nullptr);

    [[nodiscard]] const ReplicaClientConfig& config() const noexcept { return config_; }

    /// The token to send to `address`, empty when none applies.
    [[nodiscard]] std::string auth_token_for(const std::string& address) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    ReplicaClientConfig config_;
};

/// A lookup token as accepted by a replica: no whitespace, no control bytes,
/// non-empty. Throws `ReplicaError` otherwise, because a key with a space in it
/// would be read by the replica as a key plus an auth token.
[[nodiscard]] std::string validate_lookup_key(std::string_view key);

/// True for 127.0.0.1, ::1 and localhost, with or without a port.
[[nodiscard]] bool is_loopback_endpoint(std::string_view address);

/// The destination part of a replica address for SAM: `host.b32.i2p:19444`
/// carries the replica's TCP port as a hint for direct mode, and passing it to
/// SAM STREAM CONNECT is rejected by the router as an invalid key.
[[nodiscard]] std::string sam_destination_from_endpoint(std::string_view address);

/// Stream factory that dials `host:port` directly, for a replica on the local
/// machine or reachable without I2P.
[[nodiscard]] StreamFactory direct_stream_factory(
    asio::any_io_executor executor,
    std::chrono::milliseconds timeout = std::chrono::milliseconds(15000));

/// Stream factory that opens each stream through an existing SAM session,
/// resolving `.b32.i2p` addresses first and falling back to the raw address
/// when the router has no lease set for them yet.
[[nodiscard]] StreamFactory sam_stream_factory(std::shared_ptr<sam::SamSession> session);

}  // namespace i2pchat::blindbox
