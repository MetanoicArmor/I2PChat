#include "i2pchat/updates/release_index.hpp"

#include <array>
#include <cctype>
#include <cstdlib>
#include <regex>
#include <stdexcept>
#include <string>

#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/utsname.h>
#endif

namespace i2pchat::updates {
namespace {

const std::regex kReleaseZip{
    R"(^I2PChat-(linux|macOS|windows)-([A-Za-z0-9_]+)-v(\d+\.\d+\.\d+)\.zip$)"};
const std::regex kZipCandidate{R"(I2PChat-[^\s\"'<>]+\.zip)"};

std::array<int, 3> parse_version_tuple(std::string_view version) {
    const std::string text(version);
    std::array<int, 3> parts{0, 0, 0};
    std::size_t start = 0;
    for (int i = 0; i < 3; ++i) {
        const std::size_t dot = text.find('.', start);
        const std::string token =
            text.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
        if (token.empty() || (i < 2 && dot == std::string::npos) || (i == 2 && dot != std::string::npos)) {
            throw std::invalid_argument("expected major.minor.patch, got " + text);
        }
        parts[static_cast<std::size_t>(i)] = std::stoi(token);
        start = dot == std::string::npos ? text.size() : dot + 1;
    }
    return parts;
}

std::string machine_lower() {
#if defined(_WIN32)
    return "amd64";
#else
    utsname info{};
    if (uname(&info) != 0) {
        return {};
    }
    std::string machine = info.machine;
    for (char& ch : machine) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return machine;
#endif
}

}  // namespace

std::string releases_page_url() {
    if (const char* raw = std::getenv("I2PCHAT_RELEASES_PAGE_URL");
        raw != nullptr && raw[0] != '\0') {
        return raw;
    }
    return std::string(kDefaultReleasesPageUrl);
}

std::string downloads_page_url() {
    std::string url = releases_page_url();
    if (url.find('#') != std::string::npos) {
        return url;
    }
    while (!url.empty() && url.back() == '/') {
        url.pop_back();
    }
    return url + "/#downloads";
}

std::optional<std::string> expected_artifact_prefix() {
    const std::string machine = machine_lower();
#if defined(__APPLE__)
    if (machine == "arm64") {
        return "I2PChat-macOS-arm64";
    }
    if (machine == "x86_64") {
        return "I2PChat-macOS-x64";
    }
    return std::nullopt;
#elif defined(_WIN32)
    return "I2PChat-windows-x64";
#else
    if (machine == "x86_64" || machine == "amd64") {
        return "I2PChat-linux-x86_64";
    }
    if (machine == "aarch64" || machine == "arm64") {
        return "I2PChat-linux-arm64";
    }
    return std::nullopt;
#endif
}

int compare_version_strings(std::string_view a, std::string_view b) {
    const auto ta = parse_version_tuple(a);
    const auto tb = parse_version_tuple(b);
    if (ta < tb) {
        return -1;
    }
    if (ta > tb) {
        return 1;
    }
    return 0;
}

std::optional<std::pair<std::string, std::string>> find_latest_for_prefix(
    std::string_view html, std::string_view artifact_prefix) {
    const std::string prefix_with_v = std::string(artifact_prefix) + "-v";
    std::optional<std::array<int, 3>> best_vt;
    std::optional<std::pair<std::string, std::string>> best;
    const std::string page(html);
    for (std::sregex_iterator it(page.begin(), page.end(), kZipCandidate), end; it != end; ++it) {
        const std::string name = it->str();
        std::smatch match;
        if (!std::regex_match(name, match, kReleaseZip)) {
            continue;
        }
        if (!name.starts_with(prefix_with_v)) {
            continue;
        }
        try {
            const auto vt = parse_version_tuple(match[3].str());
            if (!best_vt || vt > *best_vt) {
                best_vt = vt;
                best = std::make_pair(match[3].str(), name);
            }
        } catch (...) {
        }
    }
    return best;
}

UpdateCheckResult check_for_updates_from_html(std::string_view current_version,
                                              std::string_view html) {
    const auto prefix = expected_artifact_prefix();
    if (!prefix) {
        return {false, "unsupported",
                "Automatic update check is not available for this OS/CPU combination. "
                "Open the downloads page and pick a build manually.",
                std::nullopt, std::nullopt};
    }
    const auto latest = find_latest_for_prefix(html, *prefix);
    if (!latest) {
        return {true, "no_artifact",
                "No release file found for this platform (expected prefix " + *prefix +
                    "). You can still open the downloads page to look for other builds.",
                std::nullopt, std::nullopt};
    }
    try {
        const int cmp = compare_version_strings(current_version, latest->first);
        if (cmp >= 0) {
            return {true, "up_to_date",
                    "You are up to date (v" + std::string(current_version) + ").", latest->first,
                    latest->second};
        }
        return {true, "update_available",
                "A newer release is available: v" + latest->first + " (you have v" +
                    std::string(current_version) + ").",
                latest->first, latest->second};
    } catch (...) {
        return {false, "bad_local_version",
                "Invalid local version string: " + std::string(current_version), std::nullopt,
                std::nullopt};
    }
}

}  // namespace i2pchat::updates
