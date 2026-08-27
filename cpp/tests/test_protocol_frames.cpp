#include <catch2/catch_test_macros.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/protocol/codec.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::hex_of;
using i2pchat::testing::load_vector;

namespace {

/// Read every frame currently available from a reader.
std::vector<protocol::Frame> drain(protocol::FrameReader& reader) {
    std::vector<protocol::Frame> frames;
    while (auto frame = reader.next()) {
        frames.push_back(std::move(*frame));
    }
    return frames;
}

}  // namespace

TEST_CASE("framing constants match the wire specification", "[protocol][vectors]") {
    const auto document = load_vector("protocol_frames");
    const auto& constants = document.at("constants");

    CHECK(hex_of(ByteView(protocol::kMagic.data(), protocol::kMagic.size())) ==
          constants.at("magic_hex").get<std::string>());
    CHECK(protocol::kProtocolVersion == constants.at("protocol_version").get<unsigned>());
    CHECK(protocol::kHeaderSize == constants.at("header_size").get<std::size_t>());
    CHECK(protocol::kFlagEncrypted == constants.at("flag_encrypted").get<unsigned>());
    CHECK(protocol::kEncryptedTrailerSize ==
          constants.at("encrypted_trailer_size").get<std::size_t>());
    CHECK(protocol::kMaxFrameBody == constants.at("max_frame_body").get<std::size_t>());
    CHECK(protocol::kDefaultResyncLimit == constants.at("resync_limit").get<std::size_t>());

    std::set<char> expected_types;
    for (const auto& entry : constants.at("allowed_types")) {
        expected_types.insert(entry.get<std::string>().at(0));
    }
    CHECK(protocol::default_allowed_types() == expected_types);
}

TEST_CASE("plaintext frame encoding is byte-exact", "[protocol][vectors]") {
    const auto document = load_vector("protocol_frames");

    for (const auto& entry : document.at("plaintext_frames")) {
        const Bytes payload = hex_field(entry, "payload_hex");
        const auto type = entry.at("msg_type").get<std::string>();
        const Bytes encoded = protocol::encode_frame(
            type.at(0), ByteView(payload), entry.at("msg_id").get<std::uint64_t>(),
            static_cast<std::uint8_t>(entry.at("flags").get<unsigned>()));
        CHECK(hex_of(ByteView(encoded)) == entry.at("encoded_hex").get<std::string>());

        // And the reader must recover exactly what was encoded.
        protocol::FrameReader reader;
        reader.feed(ByteView(encoded));
        const auto frames = drain(reader);
        REQUIRE(frames.size() == 1);
        CHECK(frames[0].msg_type == type.at(0));
        CHECK(frames[0].payload == payload);
        CHECK(frames[0].msg_id == entry.at("msg_id").get<std::uint64_t>());
    }
}

TEST_CASE("encrypted frames from the reference implementation decrypt",
          "[protocol][crypto][vectors]") {
    crypto::init();
    const auto document = load_vector("protocol_frames");

    for (const auto& entry : document.at("encrypted_frames")) {
        const Bytes encoded = hex_field(entry, "encoded_hex");
        const Bytes enc_key = hex_field(entry, "enc_key_hex");
        const Bytes mac_key = hex_field(entry, "mac_key_hex");
        const Bytes expected_plaintext = hex_field(entry, "plaintext_hex");
        const auto expected_seq = entry.at("seq").get<std::uint64_t>();

        protocol::FrameReader reader;
        reader.feed(ByteView(encoded));
        const auto frames = drain(reader);
        REQUIRE(frames.size() == 1);
        const protocol::Frame& frame = frames[0];
        REQUIRE(frame.encrypted());
        REQUIRE(frame.payload.size() > protocol::kEncryptedTrailerSize);

        // Layout: seq(8 BE) | SecretBox | HMAC(32)
        const auto seq = read_u64_be(ByteView(frame.payload).first(8));
        CHECK(seq == expected_seq);
        const ByteView sealed =
            ByteView(frame.payload).subspan(8, frame.payload.size() - 8 - 32);
        const ByteView mac = ByteView(frame.payload).last(32);

        CHECK(crypto::verify_mac(ByteView(mac_key), frame.msg_type, sealed, mac, seq,
                                 frame.msg_id, frame.flags));

        const std::optional<Bytes> plaintext =
            crypto::decrypt_message(ByteView(enc_key), sealed);
        REQUIRE(plaintext.has_value());
        CHECK(*plaintext == expected_plaintext);
    }
}

TEST_CASE("the reader resynchronizes on MAGIC after junk", "[protocol][vectors]") {
    const auto document = load_vector("protocol_frames");
    const auto& resync = document.at("resync");

    protocol::FrameReader reader;
    reader.feed(ByteView(hex_field(resync, "stream_hex")));
    const auto frames = drain(reader);
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].msg_type == resync.at("expected_msg_type").get<std::string>().at(0));
    CHECK(frames[0].payload == hex_field(resync, "expected_payload_hex"));
    CHECK(frames[0].msg_id == resync.at("expected_msg_id").get<std::uint64_t>());
}

TEST_CASE("malformed frames are rejected", "[protocol][vectors]") {
    const auto document = load_vector("protocol_frames");

    for (const auto& entry : document.at("must_reject")) {
        const Bytes stream = hex_field(entry, "stream_hex");
        protocol::FrameReader reader;
        reader.feed(ByteView(stream));
        CHECK_THROWS_AS(reader.next(), protocol::ProtocolError);
    }
}

TEST_CASE("the reader tolerates arbitrary fragmentation", "[protocol]") {
    // A stream is not a message boundary: feeding one byte at a time must yield
    // the same frames as feeding the whole buffer at once.
    const Bytes first = protocol::encode_frame('U', as_bytes("first"), 1);
    const Bytes second = protocol::encode_frame('D', as_bytes("second payload"), 2);
    Bytes stream = first;
    append(stream, ByteView(second));

    protocol::FrameReader reader;
    std::vector<protocol::Frame> frames;
    for (const Byte value : stream) {
        reader.feed(ByteView(&value, 1));
        while (auto frame = reader.next()) {
            frames.push_back(std::move(*frame));
        }
    }

    REQUIRE(frames.size() == 2);
    CHECK(frames[0].msg_type == 'U');
    CHECK(to_string(ByteView(frames[0].payload)) == "first");
    CHECK(frames[1].msg_type == 'D');
    CHECK(to_string(ByteView(frames[1].payload)) == "second payload");
}

TEST_CASE("resync gives up after the limit", "[protocol]") {
    // Junk without a MAGIC must not let a peer keep us scanning forever.
    protocol::FrameReader reader(protocol::default_allowed_types(),
                                 protocol::kMaxFrameBody, /*resync_limit=*/128);
    reader.feed(ByteView(Bytes(512, 0x00)));
    CHECK_THROWS_AS(reader.next(), protocol::ProtocolError);
}

TEST_CASE("encoding rejects disallowed types and oversized payloads", "[protocol]") {
    CHECK_THROWS_AS(protocol::encode_frame('Z', as_bytes("x"), 0),
                    protocol::ProtocolError);
    CHECK_THROWS_AS(
        protocol::encode_frame('U', ByteView(Bytes(protocol::kMaxFrameBody + 1, 0)), 0),
        protocol::ProtocolError);
}

TEST_CASE("empty payloads are valid frames", "[protocol]") {
    // Ping carries no body; an off-by-one in the length handling would break it.
    const Bytes encoded = protocol::encode_frame('P', ByteView(Bytes{}), 0);
    CHECK(encoded.size() == protocol::kHeaderSize);

    protocol::FrameReader reader;
    reader.feed(ByteView(encoded));
    const auto frame = reader.next();
    REQUIRE(frame.has_value());
    CHECK(frame->msg_type == 'P');
    CHECK(frame->payload.empty());
}
