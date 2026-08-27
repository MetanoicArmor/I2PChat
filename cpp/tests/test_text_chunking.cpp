#include <catch2/catch_test_macros.hpp>

#include "i2pchat/encoding.hpp"
#include "i2pchat/protocol/text_chunking.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::load_vector;

namespace {

/// Reconstruct a vector case's input, which is stored either literally or as a
/// repeated unit to keep the fixture small.
std::string case_text(const nlohmann::json& entry) {
    if (entry.contains("text_utf8") && !entry.at("text_utf8").is_null()) {
        return entry.at("text_utf8").get<std::string>();
    }
    const auto& repeat = entry.at("text_repeat");
    const auto unit = repeat.at("unit").get<std::string>();
    const auto count = repeat.at("count").get<std::size_t>();
    std::string out;
    out.reserve(unit.size() * count);
    for (std::size_t i = 0; i < count; ++i) {
        out += unit;
    }
    return out;
}

}  // namespace

TEST_CASE("chat text splits exactly as the reference implementation does",
          "[chunking][vectors]") {
    const auto document = load_vector("text_chunking");
    CHECK(protocol::kMaxChatMessageChars ==
          document.at("max_chat_message_chars").get<std::size_t>());
    CHECK(protocol::kMinBreakLookbackFraction ==
          document.at("min_break_lookback_fraction").get<std::size_t>());

    for (const auto& entry : document.at("cases")) {
        const std::string text = case_text(entry);
        const auto max_chars = entry.at("max_chars").get<std::size_t>();

        // Sanity-check the fixture itself, so a bad vector cannot mask a bug.
        REQUIRE(encoding::utf8_length(text) ==
                entry.at("code_point_count").get<std::size_t>());
        REQUIRE(text.size() == entry.at("utf8_byte_count").get<std::size_t>());

        const std::vector<std::string> chunks =
            protocol::split_long_chat_text(text, max_chars);

        CHECK(chunks.size() == entry.at("chunk_count").get<std::size_t>());

        std::vector<std::size_t> lengths;
        for (const std::string& chunk : chunks) {
            const auto length = encoding::utf8_length(chunk);
            REQUIRE(length.has_value());
            lengths.push_back(*length);
        }
        CHECK(lengths ==
              entry.at("chunk_code_point_lengths").get<std::vector<std::size_t>>());

        if (entry.contains("chunks_utf8")) {
            CHECK(chunks == entry.at("chunks_utf8").get<std::vector<std::string>>());
        }

        // Every chunk must respect the limit in code points, not bytes.
        for (const std::size_t length : lengths) {
            CHECK(length <= max_chars);
        }
    }
}

TEST_CASE("multi-byte characters are counted as single code points",
          "[chunking]") {
    // 40 earth emoji is 160 UTF-8 bytes but only 40 code points, so a
    // byte-counting implementation would split this and a code-point one
    // would not.
    std::string text;
    for (int i = 0; i < 40; ++i) {
        text += "\xF0\x9F\x8C\x8D";
    }
    const auto chunks = protocol::split_long_chat_text(text, 64);
    CHECK(chunks.size() == 1);
    CHECK(chunks[0] == text);
}

TEST_CASE("chunking rejects an unusably small limit", "[chunking]") {
    CHECK_THROWS_AS(protocol::split_long_chat_text("text", 31), std::invalid_argument);
}

TEST_CASE("empty input yields no chunks", "[chunking]") {
    CHECK(protocol::split_long_chat_text("").empty());
}
