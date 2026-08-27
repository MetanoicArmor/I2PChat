#include "i2pchat/router/i2pd.hpp"

#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/address.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <fstream>
#include <sstream>
#include <thread>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

#ifndef _WIN32
#include <csignal>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace i2pchat::router {
namespace {

namespace asio = boost::asio;

void write_file(const std::filesystem::path& path, std::string_view contents) {
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw RouterError("Cannot write " + path.string());
    }
    stream.write(contents.data(), static_cast<std::streamsize>(contents.size()));
    if (!stream) {
        throw RouterError("Short write to " + path.string());
    }
}

void tighten_permissions(const std::filesystem::path& path, unsigned mode) {
#ifndef _WIN32
    // Router configuration, keys and logs must not be world-readable.
    ::chmod(path.c_str(), static_cast<mode_t>(mode));
#else
    (void)path;
    (void)mode;
#endif
}

}  // namespace

void require_loopback_host(std::string_view host) {
    boost::system::error_code error;
    const auto address = asio::ip::make_address(std::string(host), error);
    if (error || !address.is_loopback()) {
        throw RouterError("Router bind address must be loopback, got: " +
                          std::string(host));
    }
}

std::string render_i2pd_conf(const RouterRuntime& runtime) {
    require_loopback_host(runtime.sam_host);

    std::ostringstream out;
    out << "daemon = false\n"
        << "service = false\n"
        << "\n"
        << "sam.enabled = true\n"
        << "sam.address = " << runtime.sam_host << "\n"
        << "sam.port = " << runtime.sam_port << "\n"
        << "\n"
        << "http.enabled = true\n"
        << "http.address = 127.0.0.1\n"
        << "http.port = " << runtime.control_http_port << "\n"
        << "\n"
        << "httpproxy.enabled = true\n"
        << "httpproxy.address = 127.0.0.1\n"
        << "httpproxy.port = " << runtime.http_proxy_port << "\n"
        << "\n"
        << "socksproxy.enabled = true\n"
        << "socksproxy.address = 127.0.0.1\n"
        << "socksproxy.port = " << runtime.socks_proxy_port << "\n"
        << "\n"
        << "log = file\n"
        << "logfile = " << runtime.log_path.string() << "\n";
    return out.str();
}

std::string file_sha256(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw RouterError("Cannot read " + path.string());
    }
    // Hash in chunks: a router binary is tens of megabytes.
    Bytes buffer(1024 * 1024);
    Bytes contents;
    while (stream) {
        stream.read(reinterpret_cast<char*>(buffer.data()),
                    static_cast<std::streamsize>(buffer.size()));
        const auto read = static_cast<std::size_t>(stream.gcount());
        contents.insert(contents.end(), buffer.begin(),
                        buffer.begin() + static_cast<std::ptrdiff_t>(read));
    }
    return encoding::hex_encode(ByteView(crypto::sha256(ByteView(contents))));
}

void verify_bundled_binary(const std::filesystem::path& binary) {
    const std::filesystem::path sidecar = binary.string() + ".sha256";
    if (!std::filesystem::exists(sidecar)) {
        // Source builds and system routers have no sidecar; the release archive
        // is signed as a whole, so absence is not a failure.
        return;
    }
    std::ifstream stream(sidecar);
    std::string pinned;
    stream >> pinned;
    if (pinned.empty()) {
        return;
    }
    for (char& ch : pinned) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }

    const std::string actual = file_sha256(binary);
    const Bytes actual_bytes = to_bytes(actual);
    const Bytes pinned_bytes = to_bytes(pinned);
    if (!crypto::constant_time_equal(ByteView(actual_bytes), ByteView(pinned_bytes))) {
        throw RouterError(
            "Bundled i2pd integrity check failed: on-disk binary does not match the "
            "pinned checksum (" +
            binary.string() +
            "). Refusing to launch a possibly tampered router. Reinstall from a "
            "verified release.");
    }
}

bool port_in_use(const std::string& host, std::uint16_t port) {
    asio::io_context context;
    asio::ip::tcp::socket socket(context);
    boost::system::error_code error;
    const auto address = asio::ip::make_address(host, error);
    if (error) {
        return false;
    }
    socket.connect(asio::ip::tcp::endpoint(address, port), error);
    return !error;
}

std::uint16_t find_free_port(const std::string& host, std::uint16_t first,
                             unsigned attempts) {
    for (unsigned offset = 0; offset < attempts; ++offset) {
        const auto candidate = static_cast<std::uint16_t>(first + offset);
        if (candidate < first) {
            break;  // wrapped past 65535
        }
        if (!port_in_use(host, candidate)) {
            return candidate;
        }
    }
    throw RouterError("No free port found starting at " + std::to_string(first));
}

I2pdManager::I2pdManager(Config config) : config_(std::move(config)) {}

I2pdManager::~I2pdManager() {
    try {
        stop();
    } catch (...) {
        // A destructor must not propagate: the process is either gone or will
        // be reaped by the OS.
    }
}

void I2pdManager::prepare_data_dir() const {
    std::filesystem::create_directories(config_.data_dir);
    tighten_permissions(config_.data_dir, 0700);

    write_file(config_.runtime.conf_path, render_i2pd_conf(config_.runtime));
    tighten_permissions(config_.runtime.conf_path, 0600);

    const std::filesystem::path tunnels = config_.data_dir / "tunnels.conf";
    write_file(tunnels, render_tunnels_conf());
    tighten_permissions(tunnels, 0600);
}

void I2pdManager::start() {
    if (config_.adopt_existing &&
        port_in_use(config_.runtime.sam_host, config_.runtime.sam_port)) {
        // Another I2PChat instance or a user-managed router is already serving
        // SAM here. Use it and, critically, never stop it on shutdown.
        adopted_ = true;
        return;
    }
    if (config_.binary.empty()) {
        throw RouterError("No i2pd binary configured and no router to adopt");
    }
    if (!std::filesystem::exists(config_.binary)) {
        throw RouterError("i2pd binary not found: " + config_.binary.string());
    }
    verify_bundled_binary(config_.binary);
    prepare_data_dir();

#ifndef _WIN32
    const pid_t child = ::fork();
    if (child < 0) {
        throw RouterError("fork() failed while starting i2pd");
    }
    if (child == 0) {
        // Child: detach from the parent's controlling terminal so a Ctrl-C in
        // the TUI does not kill the router mid-write.
        ::setsid();
        const std::string conf = "--conf=" + config_.runtime.conf_path.string();
        const std::string datadir = "--datadir=" + config_.data_dir.string();
        ::execl(config_.binary.c_str(), config_.binary.c_str(), conf.c_str(),
                datadir.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);  // exec failed
    }
    pid_ = static_cast<int>(child);
#else
    throw RouterError("Starting a bundled i2pd is not implemented on Windows yet");
#endif
}

void I2pdManager::stop(std::chrono::steady_clock::duration grace) {
    if (adopted_ || !pid_.has_value()) {
        return;  // never stop a router we did not start
    }
#ifndef _WIN32
    const pid_t child = static_cast<pid_t>(*pid_);
    ::kill(child, SIGTERM);

    const auto deadline = std::chrono::steady_clock::now() + grace;
    while (std::chrono::steady_clock::now() < deadline) {
        int status = 0;
        const pid_t reaped = ::waitpid(child, &status, WNOHANG);
        if (reaped == child || reaped < 0) {
            pid_.reset();
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    // Escalate: a router that ignores SIGTERM would otherwise hold the SAM port.
    ::kill(child, SIGKILL);
    int status = 0;
    ::waitpid(child, &status, 0);
#endif
    pid_.reset();
}

bool I2pdManager::is_running() const {
    if (adopted_) {
        return port_in_use(config_.runtime.sam_host, config_.runtime.sam_port);
    }
    if (!pid_.has_value()) {
        return false;
    }
#ifndef _WIN32
    return ::kill(static_cast<pid_t>(*pid_), 0) == 0;
#else
    return false;
#endif
}

}  // namespace i2pchat::router
