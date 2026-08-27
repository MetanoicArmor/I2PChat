#include <catch2/catch_test_macros.hpp>

#include "i2pchat/protocol/secure_frame.hpp"
#include "i2pchat/session/secure_channel.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;

namespace {

/// A pair of channels wired to each other, as a completed handshake leaves them.
struct ChannelPair {
    session::SecureChannel initiator;
    session::SecureChannel responder;
};

ChannelPair make_pair(protocol::PaddingProfile padding =
                          protocol::PaddingProfile::Balanced) {
    crypto::init();
    const Bytes enc_i2r = crypto::random_bytes(32);
    const Bytes mac_i2r = crypto::random_bytes(32);
    const Bytes enc_r2i = crypto::random_bytes(32);
    const Bytes mac_r2i = crypto::random_bytes(32);

    return ChannelPair{
        session::SecureChannel({enc_i2r, mac_i2r, enc_r2i, mac_r2i}, padding),
        session::SecureChannel({enc_r2i, mac_r2i, enc_i2r, mac_i2r}, padding),
    };
}

/// Decode a single frame from an encoded buffer.
protocol::Frame decode_one(ByteView encoded) {
    protocol::FrameReader reader;
    reader.feed(encoded);
    auto frame = reader.next();
    REQUIRE(frame.has_value());
    return *frame;
}

}  // namespace

TEST_CASE("balanced padding matches the reference envelope", "[secure][vectors]") {
    const auto document = load_vector("protocol_frames");
    const auto& constants = document.at("constants");
    CHECK(protocol::kPaddingBalancedBlock ==
          constants.at("padding_balanced_block").get<std::size_t>());

    for (const auto& entry : document.at("padding_balanced")) {
        const Bytes body = hex_field(entry, "body_hex");
        const Bytes expected_prefix = hex_field(entry, "envelope_prefix_hex");
        const auto expected_length = entry.at("padded_total_length").get<std::size_t>();

        const Bytes padded =
            protocol::apply_padding(ByteView(body), protocol::PaddingProfile::Balanced);
        CHECK(padded.size() == expected_length);
        CHECK(padded.size() % protocol::kPaddingBalancedBlock == 0);
        // The envelope header and body must be byte-identical; only the random
        // filler after them differs between runs.
        REQUIRE(padded.size() >= expected_prefix.size());
        CHECK(Bytes(padded.begin(),
                    padded.begin() + static_cast<std::ptrdiff_t>(expected_prefix.size())) ==
              expected_prefix);

        CHECK(protocol::remove_padding(ByteView(padded)) == body);
    }
}

TEST_CASE("padding profiles interoperate", "[secure]") {
    // A peer with padding off sends an unwrapped payload; the receiver decides
    // by looking for the magic, so the two profiles must mix freely.
    const Bytes body = to_bytes("no envelope here");
    const Bytes unpadded =
        protocol::apply_padding(ByteView(body), protocol::PaddingProfile::Off);
    CHECK(unpadded == body);
    CHECK(protocol::remove_padding(ByteView(unpadded)) == body);
}

TEST_CASE("a malformed padding envelope is rejected", "[secure]") {
    Bytes truncated = to_bytes("I2PPAD1");
    truncated.push_back(0x00);
    CHECK_THROWS_AS(protocol::remove_padding(ByteView(truncated)),
                    protocol::ProtocolError);

    // Declares more payload than it carries.
    Bytes lying = to_bytes("I2PPAD1");
    append_u32_be(lying, 1000);
    append(lying, std::string_view("short"));
    CHECK_THROWS_AS(protocol::remove_padding(ByteView(lying)), protocol::ProtocolError);
}

TEST_CASE("an encrypted round trip preserves the payload", "[secure]") {
    ChannelPair pair = make_pair();
    const Bytes plaintext = to_bytes("привет, мир 🔐");

    const Bytes encoded = pair.initiator.encrypt_frame('U', ByteView(plaintext), 42);
    const protocol::Frame frame = decode_one(ByteView(encoded));
    CHECK(frame.encrypted());
    CHECK(frame.msg_id == 42);

    CHECK(pair.responder.decrypt_frame(frame) == plaintext);
}

TEST_CASE("sequence numbers increase and replays are refused", "[secure]") {
    ChannelPair pair = make_pair();

    const Bytes first = pair.initiator.encrypt_frame('U', as_bytes("one"), 1);
    const Bytes second = pair.initiator.encrypt_frame('U', as_bytes("two"), 2);
    CHECK(pair.initiator.send_seq() == 2);

    const protocol::Frame frame_one = decode_one(ByteView(first));
    const protocol::Frame frame_two = decode_one(ByteView(second));

    CHECK(to_string(ByteView(pair.responder.decrypt_frame(frame_one))) == "one");
    CHECK(pair.responder.last_recv_seq() == 1);
    CHECK(to_string(ByteView(pair.responder.decrypt_frame(frame_two))) == "two");
    CHECK(pair.responder.last_recv_seq() == 2);

    // Replaying either frame must fail: a duplicate is indistinguishable from
    // an attacker resending captured traffic.
    CHECK_THROWS_AS(pair.responder.decrypt_frame(frame_one), protocol::ProtocolError);
    CHECK_THROWS_AS(pair.responder.decrypt_frame(frame_two), protocol::ProtocolError);
}

TEST_CASE("frames delivered out of order are refused", "[secure]") {
    ChannelPair pair = make_pair();
    const Bytes first = pair.initiator.encrypt_frame('U', as_bytes("one"), 1);
    const Bytes second = pair.initiator.encrypt_frame('U', as_bytes("two"), 2);

    // Seq 2 arriving first is a gap, and the ordered I2P stream does not produce
    // gaps on its own, so it must be refused rather than buffered.
    CHECK_THROWS_AS(pair.responder.decrypt_frame(decode_one(ByteView(second))),
                    protocol::ProtocolError);
    CHECK(pair.responder.last_recv_seq() == 0);

    CHECK(to_string(ByteView(pair.responder.decrypt_frame(decode_one(ByteView(first))))) ==
          "one");
    CHECK(to_string(ByteView(pair.responder.decrypt_frame(decode_one(ByteView(second))))) ==
          "two");
}

TEST_CASE("a rejected frame does not consume a sequence number", "[secure]") {
    ChannelPair pair = make_pair();
    const Bytes good = pair.initiator.encrypt_frame('U', as_bytes("first"), 1);

    protocol::Frame tampered = decode_one(ByteView(good));
    tampered.payload.back() ^= 0x01;  // corrupt the MAC
    CHECK_THROWS_AS(pair.responder.decrypt_frame(tampered), protocol::ProtocolError);
    CHECK(pair.responder.last_recv_seq() == 0);

    // The untampered frame must still be accepted afterwards.
    CHECK(to_string(ByteView(pair.responder.decrypt_frame(decode_one(ByteView(good))))) ==
          "first");
}

TEST_CASE("a reflected frame fails its MAC", "[secure]") {
    // Directional keys are the anti-reflection defence: a frame echoed back to
    // its sender is checked against the opposite direction's key.
    ChannelPair pair = make_pair();
    const Bytes sent = pair.initiator.encrypt_frame('U', as_bytes("mine"), 1);
    CHECK_THROWS_AS(pair.initiator.decrypt_frame(decode_one(ByteView(sent))),
                    protocol::ProtocolError);
}

TEST_CASE("a plaintext application frame is refused on a secure channel",
          "[secure]") {
    // Clearing the encrypted flag must not downgrade the channel.
    ChannelPair pair = make_pair();
    const Bytes plain = protocol::encode_frame('U', as_bytes("cleartext"), 1, 0);
    CHECK_THROWS_AS(pair.responder.decrypt_frame(decode_one(ByteView(plain))),
                    protocol::ProtocolError);

    // Only the handshake channel stays legal in plaintext.
    CHECK(session::SecureChannel::plaintext_allowed_after_handshake('H'));
    CHECK_FALSE(session::SecureChannel::plaintext_allowed_after_handshake('U'));
    CHECK_FALSE(session::SecureChannel::plaintext_allowed_after_handshake('S'));
}

TEST_CASE("relabelling a captured frame fails its MAC", "[secure]") {
    // The MAC covers the type, flags and msg_id as well as the body, so a peer
    // cannot repurpose a captured frame as a different message type.
    ChannelPair pair = make_pair();
    const Bytes sent = pair.initiator.encrypt_frame('U', as_bytes("payload"), 7);

    protocol::Frame relabelled = decode_one(ByteView(sent));
    relabelled.msg_type = 'D';
    CHECK_THROWS_AS(pair.responder.decrypt_frame(relabelled), protocol::ProtocolError);

    protocol::Frame renumbered = decode_one(ByteView(sent));
    renumbered.msg_id = 8;
    CHECK_THROWS_AS(pair.responder.decrypt_frame(renumbered), protocol::ProtocolError);
}

TEST_CASE("a truncated encrypted body is rejected", "[secure]") {
    ChannelPair pair = make_pair();
    protocol::Frame frame;
    frame.msg_type = 'U';
    frame.flags = protocol::kFlagEncrypted;
    frame.payload = Bytes(protocol::kEncryptedTrailerSize, 0);
    CHECK_THROWS_AS(pair.responder.decrypt_frame(frame), protocol::ProtocolError);
}

TEST_CASE("an empty message survives the encrypted round trip", "[secure]") {
    ChannelPair pair = make_pair();
    const Bytes encoded = pair.initiator.encrypt_frame('P', ByteView(Bytes{}), 0);
    CHECK(pair.responder.decrypt_frame(decode_one(ByteView(encoded))).empty());
}

TEST_CASE("large payloads round trip", "[secure]") {
    ChannelPair pair = make_pair();
    const Bytes payload = crypto::random_bytes(64 * 1024);
    const Bytes encoded = pair.initiator.encrypt_frame('D', ByteView(payload), 1);
    CHECK(pair.responder.decrypt_frame(decode_one(ByteView(encoded))) == payload);
}

TEST_CASE("padding hides the plaintext length", "[secure]") {
    // Two messages of different lengths within the same 128-byte bucket must
    // produce frames of identical size, or padding buys nothing.
    ChannelPair pair = make_pair(protocol::PaddingProfile::Balanced);
    const Bytes shorter = pair.initiator.encrypt_frame('U', as_bytes("a"), 1);
    const Bytes longer = pair.initiator.encrypt_frame('U', as_bytes("aaaaaaaaaa"), 2);
    CHECK(shorter.size() == longer.size());
}

TEST_CASE("the timing constants match the protocol", "[secure]") {
    const auto document = load_vector("crypto_handshake");
    const auto& timeouts = document.at("timeouts_seconds");
    CHECK(session::kHandshakeTimeout.count() == timeouts.at("handshake").get<long>());
    CHECK(session::kKeepaliveInterval.count() ==
          timeouts.at("keepalive_interval").get<long>());
    CHECK(session::kLivenessTimeout.count() == timeouts.at("liveness").get<long>());
}

TEST_CASE("a channel cannot be built without keys", "[secure]") {
    CHECK_THROWS_AS(session::SecureChannel(session::SessionKeys{}),
                    session::HandshakeError);
}
