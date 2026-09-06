#include <catch2/catch_test_macros.hpp>

#include "i2pchat/updates/release_index.hpp"

TEST_CASE("release index finds the newest matching zip", "[updates]") {
    const std::string html =
        "href=\"I2PChat-macOS-arm64-v1.4.0.zip\" "
        "I2PChat-linux-x86_64-v1.5.1.zip "
        "I2PChat-macOS-arm64-v1.5.2.zip "
        "I2PChat-macOS-arm64-v1.5.0.zip";
    const auto latest = i2pchat::updates::find_latest_for_prefix(html, "I2PChat-macOS-arm64");
    REQUIRE(latest.has_value());
    CHECK(latest->first == "1.5.2");
    CHECK(latest->second == "I2PChat-macOS-arm64-v1.5.2.zip");
}

TEST_CASE("update check reports newer vs current", "[updates]") {
    const std::string html = "I2PChat-macOS-arm64-v9.9.9.zip I2PChat-linux-x86_64-v9.9.9.zip "
                             "I2PChat-macOS-x64-v9.9.9.zip I2PChat-windows-x64-v9.9.9.zip "
                             "I2PChat-linux-arm64-v9.9.9.zip";
    const auto newer = i2pchat::updates::check_for_updates_from_html("1.0.0", html);
    REQUIRE(newer.ok);
    CHECK(newer.kind == "update_available");

    const auto current = i2pchat::updates::check_for_updates_from_html("9.9.9", html);
    REQUIRE(current.ok);
    CHECK(current.kind == "up_to_date");
}

TEST_CASE("version compare matches the reference", "[updates]") {
    CHECK(i2pchat::updates::compare_version_strings("1.0.0", "1.0.1") < 0);
    CHECK(i2pchat::updates::compare_version_strings("1.5.0", "1.5.0") == 0);
    CHECK(i2pchat::updates::compare_version_strings("2.0.0", "1.9.9") > 0);
}
