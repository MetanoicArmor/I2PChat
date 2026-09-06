#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

/// Parse a releases listing and compare versions with the local build.
///
/// Source of truth: i2pchat/updates/release_index.py. Fetching the page is a
/// GUI concern (Qt Network + I2P HTTP proxy); this module only interprets HTML.
namespace i2pchat::updates {

inline constexpr std::string_view kDefaultReleasesPageUrl =
    "http://i2pchatsfjisxgbfpjqg52qfv4unspxgcizvvh7mfirn2uzj2udq.b32.i2p/";

struct UpdateCheckResult {
    bool ok = false;
    std::string kind;
    std::string message;
    std::optional<std::string> remote_version;
    std::optional<std::string> remote_filename;
};

[[nodiscard]] std::string releases_page_url();
[[nodiscard]] std::string downloads_page_url();
[[nodiscard]] std::optional<std::string> expected_artifact_prefix();
[[nodiscard]] int compare_version_strings(std::string_view a, std::string_view b);
[[nodiscard]] std::optional<std::pair<std::string, std::string>> find_latest_for_prefix(
    std::string_view html, std::string_view artifact_prefix);
[[nodiscard]] UpdateCheckResult check_for_updates_from_html(std::string_view current_version,
                                                            std::string_view html);

}  // namespace i2pchat::updates
