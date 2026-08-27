#include <catch2/catch_test_macros.hpp>

#include "options.hpp"

using namespace i2pchat::tui;

TEST_CASE("tui option parsing") {
    SECTION("defaults") {
        const ParseResult result = parse_options({});
        CHECK(result.error.empty());
        CHECK(result.options.profile == "default");
        CHECK(result.options.sam_port == 7656);
        CHECK(result.options.help == false);
        CHECK_FALSE(result.options.app_root.empty());
    }

    SECTION("help and version short-circuit") {
        CHECK(parse_options({"-h"}).options.help);
        CHECK(parse_options({"--version"}).options.version);
    }

    SECTION("profile and sam") {
        const ParseResult result =
            parse_options({"-p", "work", "--sam-host", "10.0.0.1", "--sam-port", "9000"});
        CHECK(result.error.empty());
        CHECK(result.options.profile == "work");
        CHECK(result.options.sam_host == "10.0.0.1");
        CHECK(result.options.sam_port == 9000);
    }

    SECTION("replicas accept a comma-separated list") {
        const ParseResult result =
            parse_options({"--replica", "127.0.0.1:14000,127.0.0.1:14001", "--replica-direct"});
        CHECK(result.error.empty());
        REQUIRE(result.options.replicas.size() == 2);
        CHECK(result.options.replicas[0] == "127.0.0.1:14000");
        CHECK_FALSE(result.options.blindbox_over_sam);
    }

    SECTION("unknown flags and missing values fail") {
        CHECK_FALSE(parse_options({"--nope"}).error.empty());
        CHECK_FALSE(parse_options({"--profile"}).error.empty());
        CHECK_FALSE(parse_options({"--sam-port", "nope"}).error.empty());
    }

    SECTION("usage mentions the commands that matter") {
        const std::string usage = usage_text();
        CHECK(usage.find("--profile") != std::string::npos);
        CHECK(usage.find("/help") != std::string::npos);
    }
}
