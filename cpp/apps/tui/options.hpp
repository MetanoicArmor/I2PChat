#pragma once

#include <chrono>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace i2pchat::tui {

/// What the command line asked for.
struct Options {
    std::filesystem::path app_root;
    std::string profile = "default";
    bool profile_from_cli = false;
    std::string sam_host = "127.0.0.1";
    std::uint16_t sam_port = 7656;
    /// Start the bundled router from this binary before connecting. Empty means
    /// an already-running router is expected on the SAM endpoint.
    std::filesystem::path bundled_router;
    /// Dial this peer once the session is up.
    std::string connect;
    /// Replica endpoints, overriding whatever the profile stores.
    std::vector<std::string> replicas;
    bool blindbox_over_sam = true;
    std::chrono::seconds blindbox_poll{25};
    /// Print the help text and exit.
    bool help = false;
    bool version = false;
};

/// Where profile data lives when `--app-root` is not given: the same directory
/// the reference implementation uses, so the two clients see one set of
/// profiles.
[[nodiscard]] std::filesystem::path default_app_root();

struct ParseResult {
    Options options;
    /// Set when the arguments could not be understood. The caller prints it and
    /// exits non-zero.
    std::string error;
};

[[nodiscard]] ParseResult parse_options(const std::vector<std::string>& args);

[[nodiscard]] std::string usage_text();

}  // namespace i2pchat::tui
