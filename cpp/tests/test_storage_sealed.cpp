#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/profile_dat.hpp"
#include "i2pchat/storage/profile_paths.hpp"
#include "i2pchat/storage/sealed_json.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

/// Find one entry of the sealed-file fixture by its `kind`.
nlohmann::json sealed_fixture(std::string_view kind) {
    const auto document = load_vector("sealed_files");
    for (const auto& entry : document.at("files")) {
        if (entry.at("kind").get<std::string>() == kind) {
            return entry;
        }
    }
    FAIL("no sealed-file fixture named " + std::string(kind));
    return {};
}

void write_bytes(const std::filesystem::path& path, ByteView data) {
    std::ofstream stream(path, std::ios::binary);
    stream.write(reinterpret_cast<const char*>(data.data()),
                 static_cast<std::streamsize>(data.size()));
}

storage::SealedJsonFormat format_for(const nlohmann::json& fixture) {
    const std::string kind = fixture.at("kind").get<std::string>();
    if (kind == "contacts") {
        return storage::kContactsFormat;
    }
    if (kind == "compose_drafts") {
        return storage::kComposeDraftsFormat;
    }
    if (kind == "chat_history") {
        return storage::chat_history_format(fixture.at("peer").get<std::string>());
    }
    if (kind == "group_store") {
        return storage::group_store_format(fixture.at("group_id").get<std::string>());
    }
    FAIL("no sealed format for " + kind);
    return {};
}

}  // namespace

TEST_CASE("the sealed header layout matches the reference", "[storage][vectors]") {
    const auto document = load_vector("sealed_files");
    const auto& layout = document.at("common_layout");
    CHECK(layout.at("header").get<std::string>() ==
          "magic(4) | version(uint16 BE) | salt(32)");
    CHECK(storage::kSealedJsonHeaderSize == 4 + 2 + 32);
    CHECK(storage::kSealedJsonSaltSize == 32);
}

TEST_CASE("every sealed file kind decrypts to the reference plaintext",
          "[storage][vectors]") {
    crypto::init();
    TempDir dir;

    for (const std::string kind :
         {"contacts", "compose_drafts", "chat_history", "group_store"}) {
        const nlohmann::json fixture = sealed_fixture(kind);
        const storage::SealedJsonFormat format = format_for(fixture);
        const Bytes identity_key =
            hex_field(fixture.at("key_material"), "identity_key_hex");
        const Bytes blob = hex_field(fixture, "blob_hex");

        const auto path = dir.file(kind + ".sealed");
        write_bytes(path, ByteView(blob));

        // The blob carries the header the Python implementation wrote, so this
        // reads the reference bytes rather than anything we produced.
        CHECK(storage::is_sealed_json_file(path, format.magic));

        const nlohmann::json payload =
            storage::read_sealed_json(path, ByteView(identity_key), format);
        const nlohmann::json expected =
            nlohmann::json::parse(fixture.at("plaintext_utf8").get<std::string>());
        CHECK(payload == expected);
    }
}

TEST_CASE("sealed payloads are compact and ASCII-only", "[storage][vectors]") {
    // Unlike a signed payload, a sealed one is not serialised with sorted keys,
    // so its bytes are not a compatibility contract — both sides just parse
    // JSON. What does matter is that the separators are compact and non-ASCII is
    // escaped, so the bytes never depend on the writer's locale.
    const nlohmann::json fixture = sealed_fixture("contacts");
    const std::string reference = fixture.at("plaintext_utf8").get<std::string>();
    CHECK(reference.find(", ") == std::string::npos);
    CHECK(reference.find(": ") == std::string::npos);

    nlohmann::json payload;
    payload["b"] = "привет";
    payload["a"] = 1;
    const std::string serialized = storage::serialize_sealed_payload(payload);
    CHECK(serialized.find(", ") == std::string::npos);
    CHECK(serialized.find("\\u043f") != std::string::npos);
}

TEST_CASE("scoped file keys match the reference", "[storage][vectors]") {
    // History and the group store fold the peer or group into the file-key info.
    // Getting that wrong yields a key that decrypts nothing, so the fixture
    // publishes the expected keys directly.
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("chat_history");
    const Bytes identity_key = hex_field(fixture.at("key_material"), "identity_key_hex");
    const auto& kdf = fixture.at("kdf");

    CHECK(encoding::hex_encode(ByteView(storage::derive_sealed_profile_key(
              ByteView(identity_key), "I2PCHAT-HISTORY"))) ==
          kdf.at("profile_key_hex").get<std::string>());

    const Bytes blob = hex_field(fixture, "blob_hex");
    const ByteView salt = ByteView(blob).subspan(6, storage::kSealedJsonSaltSize);
    const std::string peer = fixture.at("peer").get<std::string>();
    CHECK(encoding::hex_encode(ByteView(storage::derive_sealed_file_key(
              ByteView(identity_key), salt, "I2PCHAT-HISTORY", peer))) ==
          kdf.at("file_key_hex").get<std::string>());
}

TEST_CASE("file names match the reference", "[storage][vectors]") {
    const nlohmann::json history = sealed_fixture("chat_history");
    const std::string peer = history.at("peer").get<std::string>();
    CHECK(storage::peer_file_id(peer) ==
          history.at("peer_file_id").get<std::string>());
    CHECK(storage::legacy_peer_file_id(peer) ==
          history.at("legacy_peer_file_id").get<std::string>());

    const nlohmann::json group = sealed_fixture("group_store");
    CHECK(storage::group_token(group.at("group_id").get<std::string>()) ==
          group.at("group_token").get<std::string>());

    // Mixed case and padding normalise away, but a `.b32.i2p` suffix does not:
    // the reference implementation hashes the address after trim and lowercase
    // only, so the suffixed form legitimately names a different file.
    CHECK(storage::peer_file_id("  " + peer + "  ") == storage::peer_file_id(peer));
    CHECK(storage::peer_file_id(peer + ".b32.i2p") != storage::peer_file_id(peer));

    storage::ProfilePaths paths("/tmp/profiles/alice", "alice");
    CHECK(paths.identity_dat().filename() == "alice.dat");
    CHECK(paths.trust_store().filename() == "alice.trust.json");
    CHECK(paths.contacts().filename() == "alice.contacts.json");
    CHECK(paths.compose_drafts().filename() == "alice.compose_drafts.json");
    CHECK(paths.chat_history(peer).filename() ==
          "alice.history." + storage::peer_file_id(peer) + ".enc");
    CHECK(paths.group_store("group-alpha").filename() ==
          "alice.group." + storage::group_token("group-alpha") + ".json");
}

TEST_CASE("a sealed file round trips through our own writer", "[storage]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    const Bytes identity_key = crypto::random_bytes(32);

    nlohmann::json payload;
    payload["version"] = 2;
    payload["contacts"] = nlohmann::json::array({"первый", "второй"});

    storage::write_sealed_json(path, payload, ByteView(identity_key),
                               storage::kContactsFormat);
    CHECK(storage::is_sealed_json_file(path, storage::kContactsFormat.magic));
    CHECK(storage::read_sealed_json(path, ByteView(identity_key),
                                    storage::kContactsFormat) == payload);
}

TEST_CASE("the salt is stable across saves", "[storage]") {
    // The reference implementation keeps a file's salt for its lifetime, so a
    // rotating salt would make the two implementations disagree about a file
    // they both wrote.
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    const Bytes identity_key = crypto::random_bytes(32);

    storage::write_sealed_json(path, {{"n", 1}}, ByteView(identity_key),
                               storage::kContactsFormat);
    const Bytes first = storage::read_file(path);

    storage::write_sealed_json(path, {{"n", 2}}, ByteView(identity_key),
                               storage::kContactsFormat);
    const Bytes second = storage::read_file(path);

    CHECK(Bytes(first.begin(), first.begin() + 38) ==
          Bytes(second.begin(), second.begin() + 38));
    CHECK(storage::read_sealed_json(path, ByteView(identity_key),
                                    storage::kContactsFormat)
              .at("n") == 2);
}

TEST_CASE("the wrong identity key is refused", "[storage]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");

    storage::write_sealed_json(path, {{"secret", true}},
                               ByteView(crypto::random_bytes(32)),
                               storage::kContactsFormat);
    const Bytes other = crypto::random_bytes(32);
    CHECK_THROWS_AS(
        storage::read_sealed_json(path, ByteView(other), storage::kContactsFormat),
        storage::SealedJsonError);
}

TEST_CASE("tampering with a sealed file is detected", "[storage]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    const Bytes identity_key = crypto::random_bytes(32);

    storage::write_sealed_json(path, {{"balance", 100}}, ByteView(identity_key),
                               storage::kContactsFormat);
    Bytes blob = storage::read_file(path);
    blob.back() ^= 0x01;
    write_bytes(path, ByteView(blob));

    CHECK_THROWS_AS(storage::read_sealed_json(path, ByteView(identity_key),
                                              storage::kContactsFormat),
                    storage::SealedJsonError);
}

TEST_CASE("legacy plaintext JSON is still read", "[storage]") {
    // Files written before at-rest encryption existed must keep opening.
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    storage::atomic_write_text(path, "{\"version\": 1, \"contacts\": []}");

    const Bytes identity_key = crypto::random_bytes(32);
    const nlohmann::json payload =
        storage::read_sealed_json(path, ByteView(identity_key),
                                  storage::kContactsFormat);
    CHECK(payload.at("version").get<int>() == 1);
    CHECK_FALSE(storage::is_sealed_json_file(path, storage::kContactsFormat.magic));

    // Saving upgrades it in place.
    storage::write_sealed_json(path, payload, ByteView(identity_key),
                               storage::kContactsFormat);
    CHECK(storage::is_sealed_json_file(path, storage::kContactsFormat.magic));
}

TEST_CASE("a sealed file is never overwritten with plaintext", "[storage]") {
    // Losing at-rest encryption because a key was momentarily unavailable would
    // be a silent downgrade of the user's data.
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    const Bytes identity_key = crypto::random_bytes(32);
    storage::write_sealed_json(path, {{"v", 1}}, ByteView(identity_key),
                               storage::kContactsFormat);

    CHECK_THROWS_AS(storage::write_sealed_json(path, {{"v", 2}}, std::nullopt,
                                               storage::kContactsFormat),
                    storage::SealedJsonError);
    CHECK(storage::is_sealed_json_file(path, storage::kContactsFormat.magic));
}

TEST_CASE("a truncated sealed record is refused", "[storage]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");

    Bytes truncated = to_bytes("I2CB");
    append_u16_be(truncated, 1);
    truncated.resize(20, 0);
    write_bytes(path, ByteView(truncated));

    const Bytes identity_key = crypto::random_bytes(32);
    CHECK_THROWS_AS(storage::read_sealed_json(path, ByteView(identity_key),
                                              storage::kContactsFormat),
                    storage::SealedJsonError);
}

TEST_CASE("an unexpected header version is refused", "[storage]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.contacts.json");
    const Bytes identity_key = crypto::random_bytes(32);
    storage::write_sealed_json(path, {{"v", 1}}, ByteView(identity_key),
                               storage::kContactsFormat);

    Bytes blob = storage::read_file(path);
    blob[4] = 0x00;
    blob[5] = 0x09;
    write_bytes(path, ByteView(blob));

    CHECK_THROWS_AS(storage::read_sealed_json(path, ByteView(identity_key),
                                              storage::kContactsFormat),
                    storage::SealedJsonError);
}

TEST_CASE("history and group stores of the same identity use different keys",
          "[storage]") {
    crypto::init();
    const Bytes identity_key = crypto::random_bytes(32);
    const Bytes salt = crypto::random_bytes(32);

    const Bytes peer_one = storage::derive_sealed_file_key(
        ByteView(identity_key), ByteView(salt), "I2PCHAT-HISTORY", "peer-one");
    const Bytes peer_two = storage::derive_sealed_file_key(
        ByteView(identity_key), ByteView(salt), "I2PCHAT-HISTORY", "peer-two");
    const Bytes unscoped = storage::derive_sealed_file_key(
        ByteView(identity_key), ByteView(salt), "I2PCHAT-HISTORY");

    CHECK(peer_one != peer_two);
    CHECK(peer_one != unscoped);
}
