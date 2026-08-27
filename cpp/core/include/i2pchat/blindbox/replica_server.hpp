#pragma once

#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "i2pchat/bytes.hpp"

/// The replica side of BlindBox: a store-and-forward box that holds sealed
/// blobs until their recipient collects them.
///
/// A replica is deliberately ignorant. It sees a lookup token, a blob it cannot
/// read, and nothing else — no addresses, no group membership, no message
/// order. That is what allows a user to run one for their friends, or rent one,
/// without being trusted.
///
/// Because it is exposed and untrusted in both directions, every limit here is
/// load-bearing: blob size, per-prefix and total quotas, a time-to-live, and a
/// per-minute rate limit. Refusals are written to an audit log in a form
/// fail2ban can act on.
namespace i2pchat::blindbox {

namespace asio = boost::asio;

inline constexpr std::string_view kDaemonMagic = "PONG BLINDBOX_SERVER_EXAMPLE_V1";
inline constexpr std::string_view kLocalReplicaMagic = "PONG BLINDBOX_LOCAL_REPLICA_V1";
inline constexpr std::uint16_t kDefaultReplicaPort = 19444;
inline constexpr std::uint16_t kDefaultStatusPort = 19445;

struct ReplicaServerConfig {
    /// Everything the replica owns lives under here: `store/` and `audit.log`.
    std::filesystem::path base_dir;
    std::string host{"127.0.0.1"};
    std::uint16_t port = kDefaultReplicaPort;

    std::size_t max_blob = 1024 * 1024;
    std::chrono::seconds ttl{14 * 24 * 3600};
    std::size_t max_files = 4096;
    std::uint64_t max_total_bytes = 512ull * 1024 * 1024;
    std::chrono::seconds gc_interval{300};

    /// Zero disables the corresponding limit.
    std::size_t rate_limit_puts_per_minute = 240;
    std::uint64_t rate_limit_bytes_per_minute = 64ull * 1024 * 1024;
    std::size_t max_prefix_files = 256;
    std::uint64_t max_prefix_bytes = 32ull * 1024 * 1024;

    std::uint64_t audit_log_max_bytes = 1024 * 1024;
    int audit_log_backups = 3;
    bool log_json = true;

    /// Required for PUT and GET when set. An empty token means an open replica,
    /// which is a supported configuration: the blobs are sealed anyway.
    std::string auth_token;
    /// Required for STATUS, METRICS and the HTTP endpoints. Falls back to
    /// `auth_token` when empty.
    std::string admin_token;

    std::string server_magic{kDaemonMagic};

    bool http_status = false;
    std::string http_host{"127.0.0.1"};
    std::uint16_t http_port = kDefaultStatusPort;
    std::filesystem::path metrics_json_path;
    std::filesystem::path metrics_prom_path;
};

/// Read configuration from the environment, after loading `.env` files from the
/// working directory and the base directory for anything not already set.
[[nodiscard]] ReplicaServerConfig config_from_environment(
    std::function<std::optional<std::string>(const std::string& name)> getenv = nullptr);

/// Parse `KEY=value` lines, ignoring blanks and `#` comments and stripping one
/// layer of matching quotes.
[[nodiscard]] std::map<std::string, std::string> parse_dotenv(std::string_view contents);

/// The blob store on disk.
///
/// Blobs are filed by the SHA-256 of their lookup token, split into a two-hex
/// prefix directory. Hashing keeps a hostile token from choosing a path, and
/// the prefix both spreads the directory out and gives the per-prefix quota
/// something to count.
class ReplicaStore {
public:
    enum class PutOutcome {
        Stored,
        /// The slot is taken and unexpired. The client verifies whether the
        /// stored blob is its own.
        Exists,
        /// A quota would be exceeded even after pruning.
        Full,
    };

    struct Stats {
        std::size_t files = 0;
        std::uint64_t bytes = 0;
    };

    /// `now` is injectable so expiry and quotas are testable without waiting.
    explicit ReplicaStore(ReplicaServerConfig config,
                          std::function<std::int64_t()> now = nullptr);

    [[nodiscard]] std::filesystem::path path_for_key(std::string_view key) const;
    /// The two-hex directory a key falls into.
    [[nodiscard]] std::string prefix_for_key(std::string_view key) const;

    [[nodiscard]] PutOutcome put(std::string_view key, ByteView blob);
    /// Nothing when the slot is empty or expired. An expired blob is removed on
    /// the way out, so a collection also cleans up.
    [[nodiscard]] std::optional<Bytes> get(std::string_view key);

    /// Drop expired blobs, then the oldest ones until `incoming_bytes` would
    /// fit. Returns whether it would.
    bool prune(std::uint64_t incoming_bytes = 0);

    [[nodiscard]] Stats stats() const;
    [[nodiscard]] Stats prefix_stats(std::string_view prefix) const;
    [[nodiscard]] bool prefix_admits(std::string_view key, std::uint64_t size) const;

    [[nodiscard]] const std::filesystem::path& store_dir() const noexcept {
        return store_dir_;
    }

private:
    struct Entry {
        std::filesystem::path path;
        std::uint64_t size = 0;
        std::int64_t modified = 0;
    };

    [[nodiscard]] std::vector<Entry> entries() const;
    [[nodiscard]] std::int64_t now() const;
    void ensure_layout() const;
    /// Date a freshly written blob by this store's clock, which matters only
    /// when that clock is not the system one.
    void stamp(const std::filesystem::path& path) const;
    /// True when the blob was expired and has been removed.
    bool remove_if_expired(const std::filesystem::path& path) const;

    ReplicaServerConfig config_;
    std::filesystem::path store_dir_;
    std::function<std::int64_t()> now_;
};

/// A sliding one-minute window over PUT count and PUT bytes.
class RateLimiter {
public:
    RateLimiter(std::size_t max_operations, std::uint64_t max_bytes);

    /// Records the operation and returns true when it is within both limits.
    [[nodiscard]] bool admit(std::uint64_t size, std::int64_t now_ms);

private:
    void purge(std::int64_t now_ms);

    std::size_t max_operations_ = 0;
    std::uint64_t max_bytes_ = 0;
    std::vector<std::pair<std::int64_t, std::uint64_t>> window_;
};

/// The audit log, in the format fail2ban and log shippers read.
///
/// Events are one line each: JSON with sorted keys, or `key=value` pairs when
/// JSON is switched off. Refusals additionally emit a `FAIL2BAN` line whose
/// shape is fixed, since a jail regex depends on it.
class AuditLog {
public:
    using Fields = std::vector<std::pair<std::string, nlohmann::json>>;

    AuditLog(std::filesystem::path path, std::uint64_t max_bytes, int backups,
             bool json, std::function<std::int64_t()> now = nullptr);

    void event(std::string_view name, const Fields& fields = {});
    void fail2ban(std::string_view reason, const Fields& fields = {});

    /// Also written to stderr, which is where a systemd unit picks it up.
    void set_stderr_echo(bool echo) { echo_ = echo; }

    [[nodiscard]] std::string render_event(std::string_view name,
                                           const Fields& fields) const;
    [[nodiscard]] static std::string render_fail2ban(std::string_view reason,
                                                     const Fields& fields);

private:
    void append(const std::string& line);
    void rotate();

    std::filesystem::path path_;
    std::uint64_t max_bytes_ = 0;
    int backups_ = 0;
    bool json_ = true;
    bool echo_ = true;
    std::function<std::int64_t()> now_;
};

/// Which peer a command came from, for the audit log.
struct PeerInfo {
    std::string host{"unknown"};
    std::uint16_t port = 0;
};

/// The replica's command protocol, independent of the transport.
///
/// A PUT is answered in two steps because its body must not be read until the
/// header has been checked: reading first would let an unauthenticated client
/// make the replica buffer whatever it claimed.
class ReplicaService {
public:
    struct PendingPut {
        std::string key;
        std::size_t size = 0;
    };

    struct Plan {
        /// Sent immediately. Empty when a body is expected first.
        std::string reply;
        /// Whether to hang up afterwards. The reference keeps the connection
        /// open only for PING and AUTH.
        bool close_after = true;
        std::optional<PendingPut> pending_put;
    };

    ReplicaService(ReplicaServerConfig config, std::function<std::int64_t()> now = nullptr);

    /// Plan the response to one command line.
    [[nodiscard]] Plan plan(std::string_view line, const PeerInfo& peer);

    /// Finish a PUT once its body has been read. `body` shorter than the
    /// declared size means the client vanished mid-blob.
    [[nodiscard]] std::string complete_put(const PendingPut& pending, ByteView body,
                                           const PeerInfo& peer);

    /// An HTTP status request: returns status code, content type and body.
    [[nodiscard]] std::tuple<int, std::string, std::string> http_response(
        std::string_view method, std::string_view path, std::string_view bearer_token,
        const PeerInfo& peer);

    [[nodiscard]] std::string status_line() const;
    [[nodiscard]] std::string status_json() const;
    [[nodiscard]] std::string prometheus_metrics() const;

    /// Drop expired blobs and refresh the exported metrics files.
    void collect_garbage();

    [[nodiscard]] ReplicaStore& store() noexcept { return store_; }
    [[nodiscard]] const ReplicaServerConfig& config() const noexcept { return config_; }
    [[nodiscard]] AuditLog& audit() noexcept { return audit_; }
    [[nodiscard]] std::uint64_t metric(const std::string& name) const;

private:
    [[nodiscard]] nlohmann::json status_payload() const;
    [[nodiscard]] bool token_ok(std::string_view provided) const;
    [[nodiscard]] bool admin_token_ok(std::string_view provided) const;
    void count(const std::string& name);
    void write_metrics_exports() const;
    void reject(std::string_view event, std::string_view fail2ban_reason,
                AuditLog::Fields fields);

    ReplicaServerConfig config_;
    ReplicaStore store_;
    RateLimiter rate_limiter_;
    AuditLog audit_;
    std::map<std::string, std::uint64_t> metrics_;
    std::function<std::int64_t()> now_;
};

/// Serves `ReplicaService` over TCP, plus the optional HTTP status listener.
class ReplicaServer {
public:
    ReplicaServer(asio::any_io_executor executor, std::shared_ptr<ReplicaService> service);
    ~ReplicaServer();

    ReplicaServer(const ReplicaServer&) = delete;
    ReplicaServer& operator=(const ReplicaServer&) = delete;

    /// Binds and starts accepting. Throws `boost::system::system_error` when the
    /// port is taken, which is how a second instance is detected.
    void start();
    void stop();

    /// The bound port, which differs from the configured one when it was zero.
    [[nodiscard]] std::uint16_t port() const;
    [[nodiscard]] std::uint16_t status_port() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

/// Ask a replica whether it is alive and, when a token is given, whether it
/// accepts it. Used before binding, to tell "another instance is already
/// running" from "the port is taken by something else".
[[nodiscard]] asio::awaitable<bool> probe_replica(
    asio::any_io_executor executor, std::string host, std::uint16_t port,
    std::string auth_token = {},
    std::chrono::milliseconds timeout = std::chrono::milliseconds(1500));

}  // namespace i2pchat::blindbox
