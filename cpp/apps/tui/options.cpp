#include "options.hpp"

#include <charconv>
#include <cstdlib>

namespace i2pchat::tui {
namespace {

std::filesystem::path home_dir() {
    if (const char* home = std::getenv("HOME"); home != nullptr && *home != '\0') {
        return std::filesystem::path(home);
    }
#ifdef _WIN32
    if (const char* profile = std::getenv("USERPROFILE");
        profile != nullptr && *profile != '\0') {
        return std::filesystem::path(profile);
    }
#endif
    return std::filesystem::current_path();
}

bool parse_port(std::string_view text, std::uint16_t& out) {
    unsigned value = 0;
    const char* begin = text.data();
    const char* end = text.data() + text.size();
    const std::from_chars_result result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{} || result.ptr != end || value == 0 || value > 65535) {
        return false;
    }
    out = static_cast<std::uint16_t>(value);
    return true;
}

std::vector<std::string> split_commas(std::string_view text) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (start <= text.size()) {
        const std::size_t comma = text.find(',', start);
        const std::string_view part = text.substr(
            start, comma == std::string_view::npos ? std::string_view::npos : comma - start);
        if (!part.empty()) {
            parts.emplace_back(part);
        }
        if (comma == std::string_view::npos) {
            break;
        }
        start = comma + 1;
    }
    return parts;
}

}  // namespace

std::filesystem::path default_app_root() {
#if defined(__APPLE__)
    return home_dir() / "Library" / "Application Support" / "I2PChat";
#elif defined(_WIN32)
    if (const char* appdata = std::getenv("APPDATA");
        appdata != nullptr && *appdata != '\0') {
        return std::filesystem::path(appdata) / "I2PChat";
    }
    return home_dir() / "I2PChat";
#else
    return home_dir() / ".i2pchat";
#endif
}

ParseResult parse_options(const std::vector<std::string>& args) {
    ParseResult result;
    result.options.app_root = default_app_root();

    for (std::size_t index = 0; index < args.size(); ++index) {
        const std::string& arg = args[index];
        const auto value_for = [&](std::string_view name) -> std::optional<std::string> {
            if (index + 1 >= args.size()) {
                result.error = std::string(name) + " needs a value";
                return std::nullopt;
            }
            return args[++index];
        };

        if (arg == "-h" || arg == "--help") {
            result.options.help = true;
            return result;
        }
        if (arg == "--version") {
            result.options.version = true;
            return result;
        }
        if (arg == "--profile" || arg == "-p") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            result.options.profile = *value;
            continue;
        }
        if (arg == "--app-root") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            result.options.app_root = *value;
            continue;
        }
        if (arg == "--sam-host") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            result.options.sam_host = *value;
            continue;
        }
        if (arg == "--sam-port") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            if (!parse_port(*value, result.options.sam_port)) {
                result.error = "not a port number: " + *value;
                return result;
            }
            continue;
        }
        if (arg == "--bundled-router") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            result.options.bundled_router = *value;
            continue;
        }
        if (arg == "--connect") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            result.options.connect = *value;
            continue;
        }
        if (arg == "--replica") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            for (std::string& endpoint : split_commas(*value)) {
                result.options.replicas.push_back(std::move(endpoint));
            }
            continue;
        }
        if (arg == "--replica-direct") {
            result.options.blindbox_over_sam = false;
            continue;
        }
        if (arg == "--poll-seconds") {
            const std::optional<std::string> value = value_for(arg);
            if (!value) {
                return result;
            }
            std::uint16_t seconds = 0;
            if (!parse_port(*value, seconds)) {
                result.error = "not a number of seconds: " + *value;
                return result;
            }
            result.options.blindbox_poll = std::chrono::seconds(seconds);
            continue;
        }
        result.error = "unknown option: " + arg;
        return result;
    }

    if (result.options.profile.empty()) {
        result.error = "the profile name cannot be empty";
    }
    return result;
}

std::string usage_text() {
    return R"(i2pchat-tui — I2PChat in the terminal

Usage: i2pchat-tui [options]

  -p, --profile NAME      Profile to open (default: default)
      --app-root DIR      Application data directory
      --sam-host HOST     SAM API host (default: 127.0.0.1)
      --sam-port PORT     SAM API port (default: 7656)
      --bundled-router BIN  Start this i2pd binary before connecting
      --connect ADDR      Dial a peer once the session is up
      --replica ENDPOINT  BlindBox replica, repeatable or comma-separated
      --replica-direct    Reach replicas over plain TCP instead of through I2P
      --poll-seconds N    How often to check for offline messages (default: 25)
  -h, --help              Show this text
      --version           Show the version

Inside the client, /help lists every command.
)";
}

}  // namespace i2pchat::tui
