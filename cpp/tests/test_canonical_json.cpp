#include <catch2/catch_test_macros.hpp>

#include "i2pchat/canonical_json.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::load_vector;

TEST_CASE("canonical JSON matches Python's signed-payload serialization",
          "[json][vectors]") {
    // This is the single most fragile compatibility surface: a mismatch here
    // silently breaks invite and group-wire signature verification against real
    // peers, with no error message pointing at the serializer.
    const auto document = load_vector("canonical_json");

    const auto& serializer = document.at("serializer");
    REQUIRE(serializer.at("sort_keys").get<bool>());
    REQUIRE(serializer.at("ensure_ascii").get<bool>());
    REQUIRE(serializer.at("separators").at(0).get<std::string>() == ",");
    REQUIRE(serializer.at("separators").at(1).get<std::string>() == ":");

    for (const auto& entry : document.at("cases")) {
        const std::string expected = entry.at("canonical_utf8").get<std::string>();
        CHECK(json_canonical::dump(entry.at("payload")) == expected);
    }
}

TEST_CASE("canonical JSON escapes non-BMP characters as surrogate pairs",
          "[json]") {
    const json_canonical::Json payload = {{"emoji", "\xF0\x9F\x94\x90"}};
    CHECK(json_canonical::dump(payload) == R"({"emoji":"\ud83d\udd10"})");
}

TEST_CASE("canonical JSON sorts keys by code point, not case-insensitively",
          "[json]") {
    json_canonical::Json payload;
    payload["z"] = 1;
    payload["A"] = 2;
    payload["_"] = 3;
    payload["a"] = 4;
    // ASCII order: 'A'(0x41) < '_'(0x5F) < 'a'(0x61) < 'z'(0x7A).
    CHECK(json_canonical::dump(payload) == R"({"A":2,"_":3,"a":4,"z":1})");
}
