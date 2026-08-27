#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>

#include "i2pchat/blindbox/blob.hpp"
#include "i2pchat/blindbox/key_schedule.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;

namespace {

blindbox::Direction direction_of(const nlohmann::json& entry) {
    return blindbox::parse_direction(entry.at("direction").get<std::string>());
}

}  // namespace

TEST_CASE("pairwise message keys match the reference schedule",
          "[blindbox][vectors]") {
    const nlohmann::json document = load_vector("blindbox");
    const Bytes root = hex_field(document, "root_secret_hex");

    for (const auto& entry : document.at("pairwise_keys")) {
        const blindbox::MessageKeys keys = blindbox::derive_message_keys(
            ByteView(root), entry.at("local_peer_id").get<std::string>(),
            entry.at("remote_peer_id").get<std::string>(), direction_of(entry),
            entry.at("index").get<std::uint64_t>(),
            entry.at("epoch").get<std::uint64_t>());

        CHECK(keys.direction_label == entry.at("direction_label").get<std::string>());
        CHECK(keys.lookup_token == entry.at("lookup_token").get<std::string>());
        CHECK(keys.lookup_key == hex_field(entry, "lookup_key_hex"));
        CHECK(keys.blob_key == hex_field(entry, "blob_key_hex"));
        CHECK(keys.state_tag == hex_field(entry, "state_tag_hex"));
        CHECK(keys.state_tag.size() == 16);
    }
}

TEST_CASE("the sender and the receiver of one message derive the same keys",
          "[blindbox][vectors]") {
    // This is the whole point of ordering the pair: neither side has to be told
    // which label to use, and a message sent at index i is read at index i.
    const nlohmann::json document = load_vector("blindbox");
    const Bytes root = hex_field(document, "root_secret_hex");
    const auto& send_entry = document.at("pairwise_keys").at(0);
    const auto& mirrored = document.at("mirrored_direction_check");

    const blindbox::MessageKeys receiver = blindbox::derive_message_keys(
        ByteView(root), send_entry.at("remote_peer_id").get<std::string>(),
        send_entry.at("local_peer_id").get<std::string>(), blindbox::Direction::Recv,
        send_entry.at("index").get<std::uint64_t>(),
        send_entry.at("epoch").get<std::uint64_t>());

    CHECK(receiver.lookup_token == mirrored.at("lookup_token").get<std::string>());
    CHECK(receiver.direction_label == mirrored.at("direction_label").get<std::string>());
    CHECK(receiver.lookup_token == send_entry.at("lookup_token").get<std::string>());
}

TEST_CASE("group message keys match the reference schedule", "[blindbox][vectors]") {
    const nlohmann::json document = load_vector("blindbox");
    const Bytes root = hex_field(document, "root_secret_hex");

    for (const auto& entry : document.at("group_keys")) {
        const blindbox::GroupMessageKeys keys = blindbox::derive_group_message_keys(
            ByteView(root), entry.at("group_id").get<std::string>(), direction_of(entry),
            entry.at("index").get<std::uint64_t>(),
            entry.at("group_epoch").get<std::uint64_t>(),
            entry.at("root_epoch").get<std::uint64_t>(),
            entry.at("sender_id").get<std::string>());

        CHECK(keys.direction_label == entry.at("direction_label").get<std::string>());
        CHECK(keys.lookup_token == entry.at("lookup_token").get<std::string>());
        CHECK(keys.lookup_key == hex_field(entry, "lookup_key_hex"));
        CHECK(keys.blob_key == hex_field(entry, "blob_key_hex"));
        CHECK(keys.state_tag == hex_field(entry, "state_tag_hex"));
    }
}

TEST_CASE("every part of the context changes the keys", "[blindbox]") {
    // If any of these collided, two different messages would land on the same
    // replica slot and one would overwrite the other.
    crypto::init();
    const Bytes root = crypto::random_bytes(32);
    const std::string alice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
    const std::string bob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

    const auto keys = [&](std::uint64_t index, std::uint64_t epoch,
                          blindbox::Direction direction) {
        return blindbox::derive_message_keys(ByteView(root), alice, bob, direction, index,
                                             epoch)
            .lookup_token;
    };

    const std::string base = keys(0, 0, blindbox::Direction::Send);
    CHECK(base != keys(1, 0, blindbox::Direction::Send));
    CHECK(base != keys(0, 1, blindbox::Direction::Send));
    CHECK(base != keys(0, 0, blindbox::Direction::Recv));

    const Bytes other_root = crypto::random_bytes(32);
    CHECK(base != blindbox::derive_message_keys(ByteView(other_root), alice, bob,
                                                blindbox::Direction::Send, 0, 0)
                      .lookup_token);
}

TEST_CASE("each group member gets a disjoint keyspace", "[blindbox]") {
    // Without binding the sender, any member holding the group root could squat
    // another member's slots or forge blobs attributed to them.
    crypto::init();
    const Bytes root = crypto::random_bytes(32);
    const std::string alice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
    const std::string bob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

    const auto token = [&](const std::string& sender) {
        return blindbox::derive_group_message_keys(ByteView(root), "group-7",
                                                   blindbox::Direction::Send, 0, 0, 0,
                                                   sender)
            .lookup_token;
    };
    CHECK(token(alice) != token(bob));
}

TEST_CASE("the key schedule refuses inputs it cannot separate", "[blindbox]") {
    crypto::init();
    const Bytes root = crypto::random_bytes(32);
    const std::string alice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";

    // An empty id would collapse two peers onto one keyspace.
    CHECK_THROWS_AS(blindbox::derive_message_keys(ByteView(root), "", alice,
                                                  blindbox::Direction::Send, 0),
                    blindbox::BlindBoxError);
    // A peer talking to itself has no direction to distinguish.
    CHECK_THROWS_AS(blindbox::derive_message_keys(ByteView(root), alice, alice,
                                                  blindbox::Direction::Send, 0),
                    blindbox::BlindBoxError);
    // A short root secret would not carry 32 bytes of entropy into the keys.
    const Bytes short_root = crypto::random_bytes(8);
    CHECK_THROWS_AS(blindbox::derive_message_keys(ByteView(short_root), alice, "peer-2",
                                                  blindbox::Direction::Send, 0),
                    blindbox::BlindBoxError);
    CHECK_THROWS_AS(blindbox::parse_direction("sideways"), blindbox::BlindBoxError);
}

TEST_CASE("the address suffix does not change the keys", "[blindbox]") {
    crypto::init();
    const Bytes root = crypto::random_bytes(32);
    const std::string alice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
    const std::string bob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

    CHECK(blindbox::derive_message_keys(ByteView(root), alice, bob,
                                       blindbox::Direction::Send, 0)
              .lookup_token ==
          blindbox::derive_message_keys(ByteView(root), alice + ".b32.i2p",
                                        "  " + bob + ".B32.I2P  ",
                                        blindbox::Direction::Send, 0)
              .lookup_token);
}

TEST_CASE("reference blobs decrypt to their frames", "[blindbox][vectors]") {
    crypto::init();
    const nlohmann::json document = load_vector("blindbox");

    for (const auto& entry : document.at("blobs")) {
        const Bytes blob = hex_field(entry, "blob_hex");
        const Bytes blob_key = hex_field(entry, "blob_key_hex");
        const Bytes state_tag = hex_field(entry, "state_tag_hex");
        const Bytes frame = hex_field(entry, "frame_hex");

        blindbox::BlobExpectation expected;
        expected.direction = direction_of(entry);
        expected.index = entry.at("index").get<std::uint64_t>();
        expected.state_tag = state_tag;

        CHECK(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key), expected) == frame);
    }
}

TEST_CASE("blob constants match the reference", "[blindbox][vectors]") {
    const nlohmann::json constants = load_vector("blindbox").at("constants");
    CHECK(blindbox::kBlobMagic == constants.at("blob_magic").get<std::string>());
    CHECK(blindbox::kBlobVersion == constants.at("blob_version").get<unsigned>());
    CHECK(blindbox::kDefaultPaddingBucket ==
          constants.at("padding_bucket").get<std::size_t>());
    CHECK(blindbox::kMaxBlobFrameSize ==
          constants.at("max_frame_size").get<std::size_t>());
    // ">8sBBQ16sI": magic, version, direction, index, state tag, frame length.
    CHECK(blindbox::kBlobHeaderSize == 38);
}

TEST_CASE("a blob round trips through our own writer", "[blindbox]") {
    crypto::init();
    const Bytes blob_key = crypto::random_bytes(32);
    const Bytes state_tag = crypto::random_bytes(16);
    const Bytes frame = to_bytes("offline message frame");

    const Bytes blob = blindbox::encrypt_blob(ByteView(frame), ByteView(blob_key),
                                              blindbox::Direction::Send, 3,
                                              ByteView(state_tag));
    blindbox::BlobExpectation expected;
    expected.direction = blindbox::Direction::Send;
    expected.index = 3;
    expected.state_tag = state_tag;
    CHECK(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key), expected) == frame);
}

TEST_CASE("blob length only leaks the padding bucket", "[blindbox]") {
    crypto::init();
    const Bytes blob_key = crypto::random_bytes(32);
    const Bytes state_tag = crypto::random_bytes(16);

    const auto size_of = [&](std::size_t frame_size) {
        return blindbox::encrypt_blob(ByteView(Bytes(frame_size, 0x41)), ByteView(blob_key),
                                      blindbox::Direction::Send, 0, ByteView(state_tag))
            .size();
    };

    // A one-byte message and a hundred-byte one are indistinguishable by length.
    CHECK(size_of(1) == size_of(100));
    CHECK(size_of(1) < size_of(1000));
    // The plaintext is a whole number of buckets; the ciphertext adds the
    // SecretBox nonce and tag on top.
    CHECK((size_of(1) - 24 - 16) % blindbox::kDefaultPaddingBucket == 0);
}

TEST_CASE("a blob that does not match what was expected is refused", "[blindbox]") {
    // Direction, index and state tag live inside the ciphertext precisely so a
    // replica cannot rearrange them without being noticed.
    crypto::init();
    const Bytes blob_key = crypto::random_bytes(32);
    const Bytes state_tag = crypto::random_bytes(16);
    const Bytes frame = to_bytes("payload");
    const Bytes blob = blindbox::encrypt_blob(ByteView(frame), ByteView(blob_key),
                                              blindbox::Direction::Send, 5,
                                              ByteView(state_tag));

    SECTION("wrong direction") {
        blindbox::BlobExpectation expected;
        expected.direction = blindbox::Direction::Recv;
        CHECK_THROWS_AS(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key), expected),
                        blindbox::BlindBoxError);
    }
    SECTION("wrong index, which is what a replay looks like") {
        blindbox::BlobExpectation expected;
        expected.index = 6;
        CHECK_THROWS_AS(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key), expected),
                        blindbox::BlindBoxError);
    }
    SECTION("wrong state tag") {
        blindbox::BlobExpectation expected;
        expected.state_tag = crypto::random_bytes(16);
        CHECK_THROWS_AS(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key), expected),
                        blindbox::BlindBoxError);
    }
    SECTION("wrong key") {
        CHECK_THROWS_AS(blindbox::decrypt_blob(ByteView(blob),
                                               ByteView(crypto::random_bytes(32))),
                        blindbox::BlindBoxError);
    }
}

TEST_CASE("a tampered blob does not decrypt", "[blindbox]") {
    crypto::init();
    const Bytes blob_key = crypto::random_bytes(32);
    const Bytes state_tag = crypto::random_bytes(16);
    Bytes blob = blindbox::encrypt_blob(ByteView(to_bytes("payload")), ByteView(blob_key),
                                        blindbox::Direction::Send, 0, ByteView(state_tag));

    blob[blob.size() / 2] ^= 0x01;
    CHECK_THROWS_AS(blindbox::decrypt_blob(ByteView(blob), ByteView(blob_key)),
                    blindbox::BlindBoxError);
}

TEST_CASE("the blob writer rejects arguments it cannot encode", "[blindbox]") {
    crypto::init();
    const Bytes blob_key = crypto::random_bytes(32);
    const Bytes state_tag = crypto::random_bytes(16);
    const Bytes frame = to_bytes("payload");

    CHECK_THROWS_AS(blindbox::encrypt_blob(ByteView(Bytes{}), ByteView(blob_key),
                                           blindbox::Direction::Send, 0,
                                           ByteView(state_tag)),
                    blindbox::BlindBoxError);
    CHECK_THROWS_AS(blindbox::encrypt_blob(ByteView(frame),
                                           ByteView(crypto::random_bytes(16)),
                                           blindbox::Direction::Send, 0,
                                           ByteView(state_tag)),
                    blindbox::BlindBoxError);
    CHECK_THROWS_AS(blindbox::encrypt_blob(ByteView(frame), ByteView(blob_key),
                                           blindbox::Direction::Send, 0,
                                           ByteView(crypto::random_bytes(8))),
                    blindbox::BlindBoxError);
    CHECK_THROWS_AS(blindbox::encrypt_blob(ByteView(frame), ByteView(blob_key),
                                           blindbox::Direction::Send, 0,
                                           ByteView(state_tag), /*padding_bucket=*/0),
                    blindbox::BlindBoxError);
}
