#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/address.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cctype>
#include <filesystem>
#include <fstream>

#include "i2pchat/router/i2pd.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using i2pchat::testing::TempDir;

namespace {

router::RouterRuntime make_runtime(const std::filesystem::path& dir) {
    router::RouterRuntime runtime;
    runtime.data_dir = dir;
    runtime.conf_path = dir / "i2pd.conf";
    runtime.log_path = dir / "i2pd.log";
    return runtime;
}

}  // namespace

TEST_CASE("i2pd.conf matches the reference template", "[router]") {
    router::RouterRuntime runtime;
    runtime.sam_host = "127.0.0.1";
    runtime.sam_port = 7656;
    runtime.control_http_port = 7070;
    runtime.http_proxy_port = 4444;
    runtime.socks_proxy_port = 4447;
    runtime.log_path = "/tmp/i2pd.log";

    const std::string expected =
        "daemon = false\n"
        "service = false\n"
        "\n"
        "sam.enabled = true\n"
        "sam.address = 127.0.0.1\n"
        "sam.port = 7656\n"
        "\n"
        "http.enabled = true\n"
        "http.address = 127.0.0.1\n"
        "http.port = 7070\n"
        "\n"
        "httpproxy.enabled = true\n"
        "httpproxy.address = 127.0.0.1\n"
        "httpproxy.port = 4444\n"
        "\n"
        "socksproxy.enabled = true\n"
        "socksproxy.address = 127.0.0.1\n"
        "socksproxy.port = 4447\n"
        "\n"
        "log = file\n"
        "logfile = /tmp/i2pd.log\n";

    CHECK(router::render_i2pd_conf(runtime) == expected);
    CHECK(router::render_tunnels_conf().empty());
}

TEST_CASE("a non-loopback bind address is refused", "[router]") {
    // Binding SAM to a routable interface would hand full control of the local
    // identity to anyone on the network.
    CHECK_THROWS_AS(router::require_loopback_host("0.0.0.0"), router::RouterError);
    CHECK_THROWS_AS(router::require_loopback_host("192.168.1.10"), router::RouterError);
    CHECK_THROWS_AS(router::require_loopback_host("example.com"), router::RouterError);
    CHECK_THROWS_AS(router::require_loopback_host(""), router::RouterError);

    CHECK_NOTHROW(router::require_loopback_host("127.0.0.1"));
    CHECK_NOTHROW(router::require_loopback_host("127.0.0.2"));
    CHECK_NOTHROW(router::require_loopback_host("::1"));

    router::RouterRuntime runtime;
    runtime.sam_host = "0.0.0.0";
    CHECK_THROWS_AS(router::render_i2pd_conf(runtime), router::RouterError);
}

TEST_CASE("preparing the data directory writes both config files", "[router]") {
    TempDir dir;
    router::I2pdManager::Config config;
    config.data_dir = dir.path();
    config.runtime = make_runtime(dir.path());

    const router::I2pdManager manager(config);
    manager.prepare_data_dir();

    REQUIRE(std::filesystem::exists(config.runtime.conf_path));
    REQUIRE(std::filesystem::exists(dir.path() / "tunnels.conf"));

    std::ifstream stream(config.runtime.conf_path);
    const std::string contents((std::istreambuf_iterator<char>(stream)),
                               std::istreambuf_iterator<char>());
    CHECK(contents.find("sam.enabled = true") != std::string::npos);
    CHECK(contents.find(config.runtime.log_path.string()) != std::string::npos);

#ifndef _WIN32
    // Router config may reference key material paths; it must not be world-readable.
    const auto permissions = std::filesystem::status(config.runtime.conf_path).permissions();
    CHECK((permissions & std::filesystem::perms::others_read) ==
          std::filesystem::perms::none);
    CHECK((permissions & std::filesystem::perms::group_read) ==
          std::filesystem::perms::none);
#endif
}

TEST_CASE("a checksum sidecar mismatch refuses to launch the binary", "[router]") {
    TempDir dir;
    const std::filesystem::path binary = dir.path() / "i2pd";
    {
        std::ofstream stream(binary, std::ios::binary);
        stream << "not really a router";
    }

    SECTION("no sidecar means no check") {
        CHECK_NOTHROW(router::verify_bundled_binary(binary));
    }

    SECTION("a matching sidecar passes") {
        const std::string digest = router::file_sha256(binary);
        std::ofstream sidecar(binary.string() + ".sha256");
        sidecar << digest << "\n";
        sidecar.close();
        CHECK_NOTHROW(router::verify_bundled_binary(binary));
    }

    SECTION("a mismatching sidecar is fatal") {
        std::ofstream sidecar(binary.string() + ".sha256");
        sidecar << std::string(64, 'a') << "\n";
        sidecar.close();
        CHECK_THROWS_AS(router::verify_bundled_binary(binary), router::RouterError);
    }

    SECTION("the sidecar comparison is case-insensitive") {
        std::string digest = router::file_sha256(binary);
        for (char& ch : digest) {
            ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
        }
        std::ofstream sidecar(binary.string() + ".sha256");
        sidecar << digest << "\n";
        sidecar.close();
        CHECK_NOTHROW(router::verify_bundled_binary(binary));
    }
}

TEST_CASE("an adopted router is never stopped", "[router]") {
    // A router shared with another I2PChat instance, or started by the user,
    // must outlive our process.
    boost::asio::io_context context;
    boost::asio::ip::tcp::acceptor acceptor(
        context, boost::asio::ip::tcp::endpoint(
                     boost::asio::ip::make_address("127.0.0.1"), 0));
    acceptor.listen();
    const std::uint16_t busy_port = acceptor.local_endpoint().port();

    TempDir dir;
    router::I2pdManager::Config config;
    config.data_dir = dir.path();
    config.runtime = make_runtime(dir.path());
    config.runtime.sam_port = busy_port;
    config.adopt_existing = true;
    config.binary = "/nonexistent/i2pd";

    router::I2pdManager manager(config);
    // Adoption must win over the missing binary: nothing needs launching.
    CHECK_NOTHROW(manager.start());
    CHECK(manager.adopted());
    CHECK_FALSE(manager.pid().has_value());
    CHECK_NOTHROW(manager.stop());
}

TEST_CASE("a missing binary with adoption disabled is an error", "[router]") {
    TempDir dir;
    router::I2pdManager::Config config;
    config.data_dir = dir.path();
    config.runtime = make_runtime(dir.path());
    config.runtime.sam_port = 1;  // nothing listening
    config.adopt_existing = false;
    config.binary = "/nonexistent/i2pd";

    router::I2pdManager manager(config);
    CHECK_THROWS_AS(manager.start(), router::RouterError);
}

TEST_CASE("port probing finds a free port", "[router]") {
    boost::asio::io_context context;
    boost::asio::ip::tcp::acceptor acceptor(
        context, boost::asio::ip::tcp::endpoint(
                     boost::asio::ip::make_address("127.0.0.1"), 0));
    acceptor.listen();
    const std::uint16_t busy = acceptor.local_endpoint().port();

    CHECK(router::port_in_use("127.0.0.1", busy));
    // The search must skip the occupied port rather than returning it.
    CHECK(router::find_free_port("127.0.0.1", busy) != busy);
}
