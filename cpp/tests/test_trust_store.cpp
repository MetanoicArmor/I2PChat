#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>

#include "i2pchat/encoding.hpp"
#include "i2pchat/session/trust_store.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using i2pchat::testing::TempDir;

namespace {

Bytes key_of(Byte filler) { return Bytes(crypto::kEd25519PublicSize, filler); }

nlohmann::json read_json(const std::filesystem::path& path) {
    const Bytes raw = storage::read_file(path);
    return nlohmann::json::parse(to_string(ByteView(raw)));
}

void write_text(const std::filesystem::path& path, const std::string& text) {
    std::ofstream stream(path);
    stream << text;
}

}  // namespace

TEST_CASE("a first sighting is pinned and persisted", "[tofu]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.trust.json");

    session::TrustStore store(path);
    store.load();
    CHECK(store.pins().empty());

    const Bytes key = key_of(0x11);
    CHECK(store.verify_or_pin("peer-one", ByteView(key)) == session::TrustDecision::Accept);

    // The same key must be accepted on every later connection.
    CHECK(store.verify_or_pin("peer-one", ByteView(key)) == session::TrustDecision::Accept);

    const nlohmann::json document = read_json(path);
    CHECK(document.at("version").get<int>() == 2);
    const auto& pin = document.at("pins").at("peer-one");
    CHECK(pin.at("signing_key_hex").get<std::string>() ==
          encoding::hex_encode(ByteView(key)));
    CHECK(pin.at("oob_verified").get<bool>() == false);

    // A fresh store must see the same pin.
    session::TrustStore reloaded(path);
    reloaded.load();
    REQUIRE(reloaded.pin_for("peer-one").has_value());
    CHECK(reloaded.pin_for("peer-one")->signing_key_hex ==
          encoding::hex_encode(ByteView(key)));
}

TEST_CASE("a changed key is refused by default", "[tofu]") {
    // Silently accepting a new key would defeat the entire point of pinning:
    // an impersonator looks exactly like a peer who reinstalled.
    crypto::init();
    TempDir dir;
    session::TrustStore store(dir.file("alice.trust.json"));

    const Bytes original = key_of(0x22);
    REQUIRE(store.verify_or_pin("peer", ByteView(original)) ==
            session::TrustDecision::Accept);

    const Bytes impostor = key_of(0x33);
    CHECK(store.verify_or_pin("peer", ByteView(impostor)) ==
          session::TrustDecision::Reject);
    // The original pin must survive the rejection.
    CHECK(store.pin_for("peer")->signing_key_hex ==
          encoding::hex_encode(ByteView(original)));
}

TEST_CASE("the prompt handler decides on a key change", "[tofu]") {
    crypto::init();
    TempDir dir;
    session::TrustStore store(dir.file("alice.trust.json"));

    const Bytes original = key_of(0x44);
    store.verify_or_pin("peer", ByteView(original));

    session::TrustPrompt seen_prompt = session::TrustPrompt::FirstSighting;
    std::string seen_old;
    store.set_prompt_handler([&](session::TrustPrompt prompt, const std::string&,
                                const std::string&, const std::string& old_key) {
        seen_prompt = prompt;
        seen_old = old_key;
        return session::TrustDecision::Accept;
    });

    const Bytes replacement = key_of(0x55);
    CHECK(store.verify_or_pin("peer", ByteView(replacement)) ==
          session::TrustDecision::Accept);
    CHECK(seen_prompt == session::TrustPrompt::KeyChanged);
    CHECK(seen_old == encoding::hex_encode(ByteView(original)));
    CHECK(store.pin_for("peer")->signing_key_hex ==
          encoding::hex_encode(ByteView(replacement)));
}

TEST_CASE("the prompt handler can refuse a first sighting", "[tofu]") {
    crypto::init();
    TempDir dir;
    session::TrustStore store(dir.file("alice.trust.json"));
    store.set_prompt_handler([](session::TrustPrompt, const std::string&,
                                const std::string&,
                                const std::string&) {
        return session::TrustDecision::Reject;
    });

    const Bytes key = key_of(0x66);
    CHECK(store.verify_or_pin("peer", ByteView(key)) == session::TrustDecision::Reject);
    // Nothing may be pinned when the user said no.
    CHECK(store.pins().empty());
}

TEST_CASE("out-of-band verification is recorded", "[tofu]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("alice.trust.json");
    session::TrustStore store(path);

    CHECK_FALSE(store.mark_oob_verified("unknown"));

    const Bytes key = key_of(0x77);
    store.verify_or_pin("peer", ByteView(key));
    CHECK(store.mark_oob_verified("peer"));
    CHECK(store.pin_for("peer")->oob_verified);
    CHECK(read_json(path).at("pins").at("peer").at("oob_verified").get<bool>());

    CHECK(store.mark_oob_verified("peer", false));
    CHECK_FALSE(store.pin_for("peer")->oob_verified);
}

TEST_CASE("forgetting a pin restores first-sighting behaviour", "[tofu]") {
    crypto::init();
    TempDir dir;
    session::TrustStore store(dir.file("alice.trust.json"));

    const Bytes key = key_of(0x88);
    store.verify_or_pin("peer", ByteView(key));
    CHECK(store.forget("peer"));
    CHECK_FALSE(store.forget("peer"));
    CHECK_FALSE(store.pin_for("peer").has_value());

    // A different key is now accepted, because there is nothing to contradict.
    CHECK(store.verify_or_pin("peer", ByteView(key_of(0x99))) ==
          session::TrustDecision::Accept);
}

TEST_CASE("the version 1 flat format is still read", "[tofu]") {
    crypto::init();
    TempDir dir;
    const auto path = dir.file("legacy.trust.json");
    const std::string key_hex(64, 'a');
    write_text(path, "{\"peer-one\": \"" + key_hex + "\"}");

    session::TrustStore store(path);
    store.load();
    REQUIRE(store.pin_for("peer-one").has_value());
    CHECK(store.pin_for("peer-one")->signing_key_hex == key_hex);
    CHECK_FALSE(store.pin_for("peer-one")->oob_verified);

    // Saving upgrades the file in place.
    store.save();
    CHECK(read_json(path).at("version").get<int>() == 2);
}

TEST_CASE("hex pins are normalized to lowercase", "[tofu]") {
    // The reference implementation lowercases on load; a stored uppercase pin
    // must not read as a key change.
    crypto::init();
    TempDir dir;
    const auto path = dir.file("upper.trust.json");
    const Bytes key = key_of(0xAB);
    const std::string upper_hex = "ABABABABABABABABABABABABABABABAB"
                                  "ABABABABABABABABABABABABABABABAB";
    write_text(path, "{\"version\": 2, \"pins\": {\"peer\": {\"signing_key_hex\": \"" +
                         upper_hex + "\", \"oob_verified\": true}}}");

    session::TrustStore store(path);
    store.load();
    CHECK(store.pin_for("peer")->signing_key_hex == encoding::hex_encode(ByteView(key)));
    CHECK(store.pin_for("peer")->oob_verified);
    CHECK(store.verify_or_pin("peer", ByteView(key)) == session::TrustDecision::Accept);
}

TEST_CASE("a corrupt trust store degrades to no pins", "[tofu]") {
    // Refusing to start because a JSON file is broken would leave the user with
    // no way out; losing pins is visible and recoverable.
    crypto::init();
    TempDir dir;
    const auto path = dir.file("broken.trust.json");
    write_text(path, "{not json at all");

    session::TrustStore store(path);
    CHECK_NOTHROW(store.load());
    CHECK(store.pins().empty());
}

TEST_CASE("a store without a path never persists", "[tofu]") {
    // The transient profile is expected to forget everything on exit.
    crypto::init();
    session::TrustStore store;
    CHECK_FALSE(store.persistent());
    CHECK_NOTHROW(store.load());
    CHECK(store.verify_or_pin("peer", ByteView(key_of(0x01))) ==
          session::TrustDecision::Accept);
    CHECK_NOTHROW(store.save());
}

TEST_CASE("malformed keys are refused", "[tofu]") {
    crypto::init();
    TempDir dir;
    session::TrustStore store(dir.file("alice.trust.json"));

    CHECK(store.verify_or_pin("", ByteView(key_of(0x01))) ==
          session::TrustDecision::Reject);
    const Bytes too_short(16, 0x01);
    CHECK(store.verify_or_pin("peer", ByteView(too_short)) ==
          session::TrustDecision::Reject);
    CHECK(store.pins().empty());
}

TEST_CASE("atomic writes replace contents and keep them private", "[storage]") {
    TempDir dir;
    const auto path = dir.file("data.bin");

    storage::atomic_write_text(path, "first");
    CHECK(to_string(ByteView(storage::read_file(path))) == "first");

    storage::atomic_write_text(path, "second");
    CHECK(to_string(ByteView(storage::read_file(path))) == "second");

    // No temporary files may be left behind.
    int entries = 0;
    for (const auto& entry : std::filesystem::directory_iterator(path.parent_path())) {
        (void)entry;
        ++entries;
    }
    CHECK(entries == 1);

#ifndef _WIN32
    const auto permissions = std::filesystem::status(path).permissions();
    CHECK((permissions & std::filesystem::perms::owner_read) !=
          std::filesystem::perms::none);
    CHECK((permissions & std::filesystem::perms::group_all) ==
          std::filesystem::perms::none);
    CHECK((permissions & std::filesystem::perms::others_all) ==
          std::filesystem::perms::none);
#endif
}

TEST_CASE("atomic JSON writes are sorted, indented and ASCII-escaped",
          "[storage]") {
    TempDir dir;
    const auto path = dir.file("value.json");
    nlohmann::json value;
    value["b"] = 1;
    value["a"] = "привет";
    storage::atomic_write_json(path, value);

    const std::string text = to_string(ByteView(storage::read_file(path)));
    // nlohmann sorts object keys, so "a" precedes "b" on disk.
    CHECK(text.find("\"a\"") < text.find("\"b\""));
    CHECK(text.find("\\u043f") != std::string::npos);
    CHECK(text.find('\n') != std::string::npos);
}
