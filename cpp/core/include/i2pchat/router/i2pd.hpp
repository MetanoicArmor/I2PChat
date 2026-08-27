#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

#include "i2pchat/bytes.hpp"

/// Management of the bundled i2pd router process.
///
/// Source of truth: i2pchat/router/bundled_i2pd.py.
namespace i2pchat::router {

class RouterError : public std::runtime_error {
public:
    explicit RouterError(const std::string& message) : std::runtime_error(message) {}
};

/// Resolved ports and paths for one bundled router instance.
struct RouterRuntime {
    std::string sam_host{"127.0.0.1"};
    std::uint16_t sam_port = 7656;
    std::uint16_t control_http_port = 7070;
    std::uint16_t http_proxy_port = 4444;
    std::uint16_t socks_proxy_port = 4447;
    std::filesystem::path data_dir;
    std::filesystem::path conf_path;
    std::filesystem::path log_path;
};

/// Reject any non-loopback bind address. A router that binds a routable
/// interface exposes the SAM API — which grants full control over the identity
/// — to the local network.
void require_loopback_host(std::string_view host);

/// Render i2pd.conf. Byte-compatible with render_i2pd_conf in the reference
/// implementation so an existing bundled router directory stays usable.
std::string render_i2pd_conf(const RouterRuntime& runtime);

/// The reference implementation writes an empty tunnels.conf.
inline std::string render_tunnels_conf() { return ""; }

/// Verify a bundled binary against its pinned checksum sidecar
/// (`<binary>.sha256`). A missing sidecar is not an error: source builds and
/// system-provided routers have none, and the release archive is signed as a
/// whole. A *mismatch* is fatal — the binary may have been tampered with.
void verify_bundled_binary(const std::filesystem::path& binary);

/// Compute the lowercase hex SHA-256 of a file.
std::string file_sha256(const std::filesystem::path& path);

/// Supervises a bundled i2pd process.
///
/// The manager is deliberately conservative about ownership: it only stops a
/// process it started itself, so a router shared with another I2PChat instance
/// or started by the user survives shutdown.
class I2pdManager {
public:
    struct Config {
        std::filesystem::path binary;
        std::filesystem::path data_dir;
        RouterRuntime runtime;
        /// Reuse an already-running router on the configured SAM port instead of
        /// starting a second one.
        bool adopt_existing = true;
    };

    explicit I2pdManager(Config config);
    ~I2pdManager();

    I2pdManager(const I2pdManager&) = delete;
    I2pdManager& operator=(const I2pdManager&) = delete;

    /// Write the configuration files into the data directory with 0600 file and
    /// 0700 directory permissions.
    void prepare_data_dir() const;

    /// Start the router. Does nothing when a router was adopted.
    void start();

    /// Stop the router if this manager started it. Sends a termination signal,
    /// then escalates after `grace`.
    void stop(std::chrono::steady_clock::duration grace = std::chrono::seconds(10));

    [[nodiscard]] bool is_running() const;
    /// True when an already-running router was adopted rather than started.
    [[nodiscard]] bool adopted() const noexcept { return adopted_; }
    [[nodiscard]] std::optional<int> pid() const noexcept { return pid_; }
    [[nodiscard]] const RouterRuntime& runtime() const noexcept {
        return config_.runtime;
    }

private:
    Config config_;
    std::optional<int> pid_;
    bool adopted_ = false;
};

/// True when something is already listening on the given loopback port.
bool port_in_use(const std::string& host, std::uint16_t port);

/// First free port at or after `first`, searching at most `attempts` ports.
std::uint16_t find_free_port(const std::string& host, std::uint16_t first,
                             unsigned attempts = 64);

}  // namespace i2pchat::router
