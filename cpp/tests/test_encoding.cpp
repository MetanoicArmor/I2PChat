#include <catch2/catch_test_macros.hpp>

#include "i2pchat/encoding.hpp"
#include "i2pchat/sam/destination.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::hex_of;
using i2pchat::testing::load_vector;

TEST_CASE("the three base64 alphabets match the reference implementation",
          "[encoding][vectors]") {
    // Mixing these up is the classic I2PChat bug: destinations use '-~',
    // file chunks use standard base64, invite tokens use base64url unpadded.
    const auto document = load_vector("sam");
    const auto& alphabet = document.at("base64_alphabet");
    CHECK(alphabet.at("i2p_altchars").get<std::string>() == "-~");

    for (const auto& entry : alphabet.at("cases")) {
        const Bytes raw = hex_field(entry, "raw_hex");
        CHECK(encoding::i2p_base64_encode(ByteView(raw)) ==
              entry.at("i2p_base64").get<std::string>());
        CHECK(encoding::base64_encode(ByteView(raw)) ==
              entry.at("standard_base64").get<std::string>());

        // Round-trips.
        const auto i2p_decoded =
            encoding::i2p_base64_decode(entry.at("i2p_base64").get<std::string>());
        REQUIRE(i2p_decoded.has_value());
        CHECK(*i2p_decoded == raw);

        const auto std_decoded =
            encoding::base64_decode(entry.at("standard_base64").get<std::string>());
        REQUIRE(std_decoded.has_value());
        CHECK(*std_decoded == raw);
    }
}

TEST_CASE("base64 decoders reject the wrong alphabet", "[encoding]") {
    // '~' is valid in I2P base64 and invalid in the standard alphabet.
    CHECK_FALSE(encoding::base64_decode("ab~d").has_value());
    CHECK_FALSE(encoding::i2p_base64_decode("ab/d").has_value());
    CHECK_FALSE(encoding::base64url_decode("ab+d").has_value());
}

TEST_CASE("base64url encoding omits padding", "[encoding]") {
    const Bytes one{0x00};
    const Bytes two{0x00, 0x01};
    CHECK(encoding::base64url_encode_nopad(ByteView(one)).find('=') == std::string::npos);
    CHECK(encoding::base64url_encode_nopad(ByteView(two)).find('=') == std::string::npos);

    // Decoding must accept the unpadded form.
    const auto decoded =
        encoding::base64url_decode(encoding::base64url_encode_nopad(ByteView(two)));
    REQUIRE(decoded.has_value());
    CHECK(*decoded == two);
}

TEST_CASE("hex round-trips and rejects malformed input", "[encoding]") {
    const Bytes raw{0x00, 0x0f, 0xff, 0xa5};
    CHECK(encoding::hex_encode(ByteView(raw)) == "000fffa5");
    const auto decoded = encoding::hex_decode("000FFFA5");
    REQUIRE(decoded.has_value());
    CHECK(*decoded == raw);

    CHECK_FALSE(encoding::hex_decode("abc").has_value());   // odd length
    CHECK_FALSE(encoding::hex_decode("zz").has_value());    // not hex
}

TEST_CASE("destination parsing and base32 derivation match", "[encoding][sam][vectors]") {
    const auto document = load_vector("sam");
    const auto& expected = document.at("destination");

    const Bytes private_blob = hex_field(expected, "private_blob_hex");
    const sam::Destination dest = sam::Destination::from_private_blob(ByteView(private_blob));

    CHECK(hex_of(ByteView(dest.data())) ==
          expected.at("public_data_hex").get<std::string>());
    CHECK(dest.base64() == expected.at("public_base64").get<std::string>());
    CHECK(dest.base32() == expected.at("base32").get<std::string>());
    CHECK(dest.base32().size() == 52);
    CHECK(dest.has_private_key());

    // The public part must stop at 387 + cert_len, never spilling private bytes.
    CHECK(dest.data().size() == expected.at("public_prefix_len").get<std::size_t>());
    CHECK(dest.data().size() < private_blob.size());
}

TEST_CASE("destination parsing rejects a certificate length that overruns the blob",
          "[sam]") {
    Bytes blob(400, 0x00);
    // cert_len = 0xFFFF at offset 385 claims far more than the blob holds.
    blob[385] = 0xFF;
    blob[386] = 0xFF;
    CHECK_THROWS_AS(sam::Destination::from_private_blob(ByteView(blob)),
                    sam::DestinationError);

    // A blob with no private bytes past the public prefix is also invalid.
    Bytes exact(387, 0x00);
    CHECK_THROWS_AS(sam::Destination::from_private_blob(ByteView(exact)),
                    sam::DestinationError);

    Bytes too_short(100, 0x00);
    CHECK_THROWS_AS(sam::Destination::from_private_blob(ByteView(too_short)),
                    sam::DestinationError);
}

TEST_CASE("peer address normalization strips suffixes and surrounding text",
          "[sam]") {
    const std::string host = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";

    CHECK(sam::normalize_peer_address(host) == host);
    CHECK(sam::normalize_peer_address(host + ".b32.i2p") == host);
    CHECK(sam::normalize_peer_address("  " + host + ".b32.i2p  ") == host);
    CHECK(sam::normalize_peer_address("My Addr: " + host + ".b32.i2p") == host);

    std::string upper = host;
    for (char& ch : upper) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
    }
    CHECK(sam::normalize_peer_address(upper + ".B32.I2P") == host);

    CHECK(sam::normalize_peer_address("").empty());
    CHECK(sam::normalize_peer_address("not an address").empty());
    // '1' and '8' are outside the base32 alphabet.
    CHECK(sam::normalize_peer_address("111118888").empty());
}

TEST_CASE("UTF-8 length counts code points and rejects malformed input",
          "[encoding]") {
    CHECK(encoding::utf8_length("") == 0);
    CHECK(encoding::utf8_length("abc") == 3);
    CHECK(encoding::utf8_length("\xD0\xBF") == 1);              // п
    CHECK(encoding::utf8_length("\xF0\x9F\x8C\x8D") == 1);      // 🌍

    CHECK_FALSE(encoding::utf8_length("\xFF").has_value());          // invalid lead
    CHECK_FALSE(encoding::utf8_length("\xD0").has_value());          // truncated
    CHECK_FALSE(encoding::utf8_length("\xC0\x80").has_value());      // overlong
    CHECK_FALSE(encoding::utf8_length("\xED\xA0\x80").has_value());  // surrogate
}

TEST_CASE("base32 encoding is lowercase and unpadded", "[encoding]") {
    const Bytes raw = to_bytes("foobar");
    const std::string encoded = encoding::base32_encode_lower(ByteView(raw));
    CHECK(encoded == "mzxw6ytboi");
    CHECK(encoded.find('=') == std::string::npos);
}
