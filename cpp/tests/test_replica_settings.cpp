#include <catch2/catch_test_macros.hpp>

#include <map>
#include <string>
#include <vector>

#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/replica_settings.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/sealed_json.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

std::vector<std::string> string_list(const nlohmann::json& value) {
    std::vector<std::string> out;
    for (const auto& item : value) {
        out.push_back(item.get<std::string>());
    }
    return out;
}

std::map<std::string, std::string> string_map(const nlohmann::json& value) {
    std::map<std::string, std::string> out;
    for (const auto& [key, item] : value.items()) {
        out.emplace(key, item.get<std::string>());
    }
    return out;
}

}  // namespace

TEST_CASE("replica settings match the reference implementation") {
    const auto vector = load_vector("replica_settings");
    const Bytes identity_key = hex_field(vector, "identity_key_hex");

    SECTION("endpoint normalisation") {
        CHECK(storage::normalize_replica_endpoints(
                  string_list(vector.at("raw_endpoints"))) ==
              string_list(vector.at("normalized_endpoints")));
    }

    SECTION("a sealed auth blob written by Python opens") {
        const std::map<std::string, std::string> auth = storage::decrypt_replica_auth(
            vector.at("auth_blob_base64").get<std::string>(), ByteView(identity_key));
        CHECK(auth == string_map(vector.at("expected_auth")));
    }

    SECTION("a file written by Python is read whole") {
        TempDir dir;
        const std::filesystem::path path = dir.path() / "profile.blindbox_replicas.json";
        storage::atomic_write_text(path, vector.at("file_utf8").get<std::string>());

        const storage::ReplicaSettings settings =
            storage::load_replica_settings(path, ByteView(identity_key));
        CHECK(settings.endpoints == string_list(vector.at("expected_endpoints")));
        CHECK(settings.auth == string_map(vector.at("expected_auth")));
        CHECK_FALSE(settings.auth_locked);
    }

    SECTION("without the identity key the endpoints still load and tokens do not") {
        TempDir dir;
        const std::filesystem::path path = dir.path() / "profile.blindbox_replicas.json";
        storage::atomic_write_text(path, vector.at("file_utf8").get<std::string>());

        const storage::ReplicaSettings settings =
            storage::load_replica_settings(path, std::nullopt);
        CHECK(settings.endpoints == string_list(vector.at("expected_endpoints")));
        CHECK(settings.auth.empty());
        CHECK(settings.auth_locked);
    }

    SECTION("version 1 carried no tokens") {
        const storage::ReplicaSettings settings = storage::parse_replica_settings(
            vector.at("legacy_version_1"), ByteView(identity_key));
        CHECK(settings.endpoints == string_list(vector.at("normalized_endpoints")));
        CHECK(settings.auth.empty());
        CHECK_FALSE(settings.auth_locked);
    }

    SECTION("version 2 kept tokens in the clear") {
        const storage::ReplicaSettings settings = storage::parse_replica_settings(
            vector.at("legacy_version_2"), ByteView(identity_key));
        CHECK(settings.auth == string_map(vector.at("expected_auth")));
    }

    SECTION("a future version is refused rather than half-read") {
        nlohmann::json document = vector.at("legacy_version_2");
        document["version"] = 99;
        const storage::ReplicaSettings settings =
            storage::parse_replica_settings(document, ByteView(identity_key));
        CHECK(settings.endpoints.empty());
    }
}

TEST_CASE("replica settings round-trip through a file") {
    const auto vector = load_vector("replica_settings");
    const Bytes identity_key = hex_field(vector, "identity_key_hex");

    storage::ReplicaSettings settings;
    settings.endpoints = {"127.0.0.1:19444", "127.0.0.1:19444", "  b.b32.i2p:19444 "};
    settings.auth = {{"127.0.0.1:19444", "token-a"},
                     {"b.b32.i2p:19444", "token-b"},
                     {"unlisted:1", "token-c"}};

    TempDir dir;
    const std::filesystem::path path = dir.path() / "profile.blindbox_replicas.json";
    storage::save_replica_settings(path, settings, ByteView(identity_key));

    const storage::ReplicaSettings reloaded =
        storage::load_replica_settings(path, ByteView(identity_key));
    CHECK(reloaded.endpoints ==
          std::vector<std::string>{"127.0.0.1:19444", "b.b32.i2p:19444"});
    CHECK(reloaded.auth == std::map<std::string, std::string>{
                               {"127.0.0.1:19444", "token-a"},
                               {"b.b32.i2p:19444", "token-b"}});

    SECTION("tokens written without a key are readable, and marked as such") {
        storage::save_replica_settings(path, settings, std::nullopt);
        const storage::ReplicaSettings plain =
            storage::load_replica_settings(path, ByteView(identity_key));
        CHECK(plain.auth.size() == 2);
        CHECK_FALSE(plain.auth_locked);
    }

    SECTION("the wrong identity key locks the tokens instead of yielding garbage") {
        Bytes other = identity_key;
        other[0] = static_cast<Byte>(other[0] ^ 0xFF);
        const storage::ReplicaSettings locked =
            storage::load_replica_settings(path, ByteView(other));
        CHECK(locked.endpoints.size() == 2);
        CHECK(locked.auth.empty());
        CHECK(locked.auth_locked);
    }
}

TEST_CASE("a missing or corrupt replica file yields empty settings") {
    TempDir dir;
    CHECK(storage::load_replica_settings(dir.path() / "absent.json", std::nullopt)
              .endpoints.empty());

    const std::filesystem::path path = dir.path() / "broken.json";
    storage::atomic_write_text(path, "{not json");
    CHECK(storage::load_replica_settings(path, std::nullopt).endpoints.empty());
}

TEST_CASE("release builtin BlindBox endpoints match the Python pool") {
    const auto eps = storage::default_release_blindbox_endpoints();
    REQUIRE(eps.size() == 1);
    CHECK(eps[0] ==
          "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444");
    CHECK(storage::same_as_release_builtin_endpoints(eps));
}
