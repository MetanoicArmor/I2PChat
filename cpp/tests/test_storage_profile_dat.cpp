#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/keyring.hpp"
#include "i2pchat/storage/profile_dat.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

nlohmann::json profile_dat_fixture() {
    const auto document = load_vector("sealed_files");
    for (const auto& entry : document.at("files")) {
        if (entry.at("kind").get<std::string>() == "profile_dat") {
            return entry;
        }
    }
    FAIL("no profile_dat fixture");
    return {};
}

/// Peer addresses in a legacy `.dat` are lowercase base32 of 40-80 characters.
bool looks_like_peer(const std::string& line) {
    if (line.size() < 40 || line.size() > 80) {
        return false;
    }
    for (const char ch : line) {
        const bool base32 = (ch >= 'a' && ch <= 'z') || (ch >= '2' && ch <= '7');
        if (!base32) {
            return false;
        }
    }
    return true;
}

void write_bytes(const std::filesystem::path& path, ByteView data) {
    std::ofstream stream(path, std::ios::binary);
    stream.write(reinterpret_cast<const char*>(data.data()),
                 static_cast<std::streamsize>(data.size()));
}

const std::string kPeerAddr = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

/// Keeps the tests off the machine's real credential store. Without this they
/// would read and write entries under whatever profile names they use, so a run
/// could clobber a user's actual wrap key and its result would depend on state
/// left behind by earlier runs.
class NoKeyring {
public:
    NoKeyring() { storage::keyring::set_enabled(false); }
    ~NoKeyring() { storage::keyring::set_enabled(true); }
    NoKeyring(const NoKeyring&) = delete;
    NoKeyring& operator=(const NoKeyring&) = delete;
};

}  // namespace

TEST_CASE("a reference profile .dat decrypts to its key line", "[profile][vectors]") {
    crypto::init();
    const nlohmann::json fixture = profile_dat_fixture();
    const Bytes wrap_key = hex_field(fixture.at("key_material"), "wrap_key_hex");
    const Bytes blob = hex_field(fixture, "blob_hex");

    // The stored plaintext is the key line terminated by a newline; the reader
    // hands back the line itself.
    const std::string stored = fixture.at("plaintext_utf8").get<std::string>();
    REQUIRE(stored.back() == '\n');

    CHECK(storage::is_encrypted_profile_dat(ByteView(blob)));
    CHECK(storage::decrypt_profile_dat(ByteView(blob), ByteView(wrap_key)) ==
          stored.substr(0, stored.size() - 1));
}

TEST_CASE("the profile .dat header and KDF match the reference",
          "[profile][vectors]") {
    const nlohmann::json fixture = profile_dat_fixture();
    CHECK(storage::kProfileDatMagic == fixture.at("magic").get<std::string>());
    CHECK(storage::kProfileDatVersion == fixture.at("version").get<std::uint16_t>());
    CHECK(storage::kProfileDatHeaderSize == 4 + 2 + 32);

    const auto& kdf = fixture.at("kdf");
    CHECK(kdf.at("stage1_extract_salt").get<std::string>() ==
          storage::kProfileDatDomain);
    CHECK(kdf.at("stage1_expand_info").get<std::string>() ==
          std::string(storage::kProfileDatDomain) + "|profile-key");
    CHECK(kdf.at("stage2_expand_info").get<std::string>() ==
          std::string(storage::kProfileDatDomain) + "|file-key");

    const auto& keyring_spec = fixture.at("keyring");
    CHECK(keyring_spec.at("service").get<std::string>() == storage::kKeyringService);
    CHECK(storage::dat_wrap_keyring_account("alice") == "alice__dat_wrap__");
    CHECK(storage::profile_dat_wrap_path("/tmp", "alice").filename() ==
          "alice.dat.wrap");
}

TEST_CASE("a profile .dat round trips through our own writer", "[profile]") {
    crypto::init();
    const Bytes wrap_key = crypto::random_bytes(32);
    const std::string key = "AAECAwQFBgcICQoLDA0ODw==";

    const Bytes blob = storage::encrypt_profile_dat(key, ByteView(wrap_key));
    CHECK(storage::is_encrypted_profile_dat(ByteView(blob)));
    CHECK(storage::decrypt_profile_dat(ByteView(blob), ByteView(wrap_key)) == key);
}

TEST_CASE("each write uses a fresh salt", "[profile]") {
    // The identity key is the most sensitive thing on disk; reusing a salt would
    // make two blobs of the same key byte-identical and so trivially linkable.
    crypto::init();
    const Bytes wrap_key = crypto::random_bytes(32);
    const Bytes first = storage::encrypt_profile_dat("key", ByteView(wrap_key));
    const Bytes second = storage::encrypt_profile_dat("key", ByteView(wrap_key));
    CHECK(Bytes(first.begin() + 6, first.begin() + 38) !=
          Bytes(second.begin() + 6, second.begin() + 38));
}

TEST_CASE("the wrong wrap key is refused", "[profile]") {
    crypto::init();
    const Bytes blob =
        storage::encrypt_profile_dat("key", ByteView(crypto::random_bytes(32)));
    CHECK_THROWS_AS(
        storage::decrypt_profile_dat(ByteView(blob), ByteView(crypto::random_bytes(32))),
        storage::ProfileDatError);
}

TEST_CASE("an empty key is refused", "[profile]") {
    crypto::init();
    const Bytes wrap_key = crypto::random_bytes(32);
    CHECK_THROWS_AS(storage::encrypt_profile_dat("", ByteView(wrap_key)),
                    storage::ProfileDatError);
    CHECK_THROWS_AS(storage::encrypt_profile_dat("   \n ", ByteView(wrap_key)),
                    storage::ProfileDatError);
}

TEST_CASE("a plaintext blob is not mistaken for an encrypted one", "[profile]") {
    crypto::init();
    const Bytes plaintext = to_bytes("AAECAwQFBgcICQoLDA0ODw==\n");
    CHECK_FALSE(storage::is_encrypted_profile_dat(ByteView(plaintext)));
    CHECK_THROWS_AS(storage::decrypt_profile_dat(ByteView(plaintext),
                                                 ByteView(crypto::random_bytes(32))),
                    storage::ProfileDatError);
}

TEST_CASE("legacy plaintext .dat layouts are parsed", "[profile]") {
    crypto::init();

    SECTION("key only") {
        const auto contents =
            storage::parse_plaintext_profile_dat("SOMEKEY==\n", looks_like_peer);
        CHECK(contents.private_key_base64 == "SOMEKEY==");
        CHECK_FALSE(contents.legacy_peer.has_value());
    }

    SECTION("key plus a locked peer on the second line") {
        const auto contents = storage::parse_plaintext_profile_dat(
            "SOMEKEY==\n" + kPeerAddr + "\n", looks_like_peer);
        CHECK(contents.private_key_base64 == "SOMEKEY==");
        CHECK(contents.legacy_peer == kPeerAddr);
    }

    SECTION("peer only, from a keyring-held key") {
        const auto contents =
            storage::parse_plaintext_profile_dat(kPeerAddr + "\n", looks_like_peer);
        CHECK_FALSE(contents.private_key_base64.has_value());
        CHECK(contents.legacy_peer == kPeerAddr);
    }

    SECTION("blank and whitespace-only lines are ignored") {
        const auto contents = storage::parse_plaintext_profile_dat(
            "\n  \nSOMEKEY==\n\n" + kPeerAddr + "\n  \n", looks_like_peer);
        CHECK(contents.private_key_base64 == "SOMEKEY==");
        CHECK(contents.legacy_peer == kPeerAddr);
    }

    SECTION("an empty file yields nothing") {
        const auto contents = storage::parse_plaintext_profile_dat("", looks_like_peer);
        CHECK_FALSE(contents.private_key_base64.has_value());
        CHECK_FALSE(contents.legacy_peer.has_value());
    }
}

TEST_CASE("a legacy .dat is reported so the caller can migrate it", "[profile]") {
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const auto path = dir.file("alice.dat");
    storage::atomic_write_text(path, "SOMEKEY==\n" + kPeerAddr + "\n");

    const auto contents = storage::read_profile_dat_file(path, "alice", dir.path(),
                                                         looks_like_peer);
    CHECK(contents.was_plaintext);
    CHECK(contents.private_key_base64 == "SOMEKEY==");
    CHECK(contents.legacy_peer == kPeerAddr);
}

TEST_CASE("a missing or empty .dat yields nothing", "[profile]") {
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    CHECK_FALSE(storage::read_profile_dat_file(dir.file("absent.dat"), "absent",
                                               dir.path())
                    .private_key_base64.has_value());

    const auto empty = dir.file("empty.dat");
    write_bytes(empty, ByteView(Bytes{}));
    CHECK_FALSE(
        storage::read_profile_dat_file(empty, "empty", dir.path())
            .private_key_base64.has_value());
}

TEST_CASE("an encrypted .dat with no wrap key available fails clearly",
          "[profile]") {
    // This is the situation a user hits after copying only the `.dat`: the error
    // has to name the missing pieces, or it looks like data corruption.
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const auto path = dir.file("orphan.dat");
    write_bytes(path,
                ByteView(storage::encrypt_profile_dat("key",
                                                      ByteView(crypto::random_bytes(32)))));

    CHECK_THROWS_AS(storage::read_profile_dat_file(path, "orphan", dir.path(), {},
                                                   /*create_wrap_key=*/false),
                    storage::ProfileDatError);
}

TEST_CASE("writing a .dat creates a sidecar that can open it", "[profile]") {
    // A profile directory copied to a machine with no keyring must still open,
    // which is the whole reason the sidecar exists.
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const auto path = dir.file("alice.dat");
    const std::string key = "AAECAwQFBgcICQoLDA0ODw==";

    storage::write_encrypted_profile_dat(path, key, "alice", dir.path());

    const auto sidecar = storage::profile_dat_wrap_path(dir.path(), "alice");
    REQUIRE(std::filesystem::exists(sidecar));

    const std::optional<Bytes> wrap = storage::load_dat_wrap_key("alice", dir.path());
    REQUIRE(wrap.has_value());
    CHECK(wrap->size() == 32);

    const auto contents = storage::read_profile_dat_file(path, "alice", dir.path());
    CHECK(contents.private_key_base64 == key);
    CHECK_FALSE(contents.was_plaintext);
}

TEST_CASE("the wrap key is stable across writes", "[profile]") {
    // Rotating it would orphan every previously written `.dat`.
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const auto path = dir.file("alice.dat");

    storage::write_encrypted_profile_dat(path, "first-key", "alice", dir.path());
    const std::optional<Bytes> first = storage::load_dat_wrap_key("alice", dir.path());

    storage::write_encrypted_profile_dat(path, "second-key", "alice", dir.path());
    const std::optional<Bytes> second = storage::load_dat_wrap_key("alice", dir.path());

    CHECK(first == second);
    CHECK(storage::read_profile_dat_file(path, "alice", dir.path())
              .private_key_base64 == "second-key");
}

TEST_CASE("the sidecar is the base64 form the reference writes", "[profile]") {
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const Bytes wrap = storage::get_or_create_dat_wrap_key("alice", dir.path());

    const auto sidecar = storage::profile_dat_wrap_path(dir.path(), "alice");
    const std::string text = to_string(ByteView(storage::read_file(sidecar)));
    CHECK(text == encoding::base64_encode(ByteView(wrap)) + "\n");
}

TEST_CASE("a corrupt sidecar is ignored rather than trusted", "[profile]") {
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const auto sidecar = storage::profile_dat_wrap_path(dir.path(), "alice");

    SECTION("not base64") {
        storage::atomic_write_text(sidecar, "!!! not base64 !!!\n");
        CHECK_FALSE(storage::load_dat_wrap_key("alice", dir.path()).has_value());
    }

    SECTION("wrong length") {
        storage::atomic_write_text(sidecar, encoding::base64_encode(
                                                ByteView(Bytes(16, 0x01))) + "\n");
        CHECK_FALSE(storage::load_dat_wrap_key("alice", dir.path()).has_value());
    }
}

TEST_CASE("a sidecar wrap key opens a .dat without any keyring", "[profile]") {
    crypto::init();
    const NoKeyring no_keyring;
    TempDir dir;
    const Bytes wrap = crypto::random_bytes(32);
    storage::atomic_write_text(storage::profile_dat_wrap_path(dir.path(), "carol"),
                               encoding::base64_encode(ByteView(wrap)) + "\n");

    const auto path = dir.file("carol.dat");
    write_bytes(path, ByteView(storage::encrypt_profile_dat("carol-key", ByteView(wrap))));

    CHECK(storage::read_profile_dat_file(path, "carol", dir.path(), {},
                                         /*create_wrap_key=*/false)
              .private_key_base64 == "carol-key");
}

TEST_CASE("disabling the keyring falls back to the sidecar", "[profile]") {
    const NoKeyring no_keyring;
    CHECK_FALSE(storage::keyring::available());
    CHECK_FALSE(storage::keyring::get(storage::kKeyringService, "any").has_value());
    CHECK_FALSE(storage::keyring::set(storage::kKeyringService, "any", "value"));
}

TEST_CASE("a keyring entry outranks the sidecar", "[profile][keyring]") {
    // The keyring is the authoritative copy; the sidecar exists for machines
    // without one. If a stale sidecar won, a profile would stop opening after
    // the wrap key was rotated in the credential store.
    crypto::init();
    if (!storage::keyring::available()) {
        SUCCEED("no keyring backend on this platform");
        return;
    }

    TempDir dir;
    const std::string profile =
        "i2pchat-test-" + encoding::hex_encode(ByteView(crypto::random_bytes(8)));
    const Bytes keyring_wrap = crypto::random_bytes(32);
    if (!storage::keyring::set(storage::kKeyringService,
                               storage::dat_wrap_keyring_account(profile),
                               encoding::base64_encode(ByteView(keyring_wrap)))) {
        SUCCEED("the keyring is present but not writable in this environment");
        return;
    }

    storage::atomic_write_text(storage::profile_dat_wrap_path(dir.path(), profile),
                               encoding::base64_encode(ByteView(crypto::random_bytes(32))) +
                                   "\n");

    CHECK(storage::load_dat_wrap_key(profile, dir.path()) == keyring_wrap);

    const auto path = dir.file(profile + ".dat");
    write_bytes(path, ByteView(storage::encrypt_profile_dat("keyring-key",
                                                           ByteView(keyring_wrap))));
    CHECK(storage::read_profile_dat_file(path, profile, dir.path()).private_key_base64 ==
          "keyring-key");

    CHECK(storage::keyring::erase(storage::kKeyringService,
                                  storage::dat_wrap_keyring_account(profile)));
}

TEST_CASE("the OS keyring round trips a secret", "[profile][keyring]") {
    if (!storage::keyring::available()) {
        SUCCEED("no keyring backend on this platform");
        return;
    }

    // A distinct account name per run, so a crashed earlier run cannot make this
    // one pass or fail spuriously.
    const std::string account =
        "i2pchat-test-" + encoding::hex_encode(ByteView(crypto::random_bytes(8)));
    const std::string secret = "c2VjcmV0LXZhbHVl";

    if (!storage::keyring::set(storage::kKeyringService, account, secret)) {
        // A locked or absent credential store is a normal condition, and the
        // sidecar fallback covers it.
        SUCCEED("the keyring is present but not writable in this environment");
        return;
    }

    CHECK(storage::keyring::get(storage::kKeyringService, account) == secret);
    CHECK(storage::keyring::set(storage::kKeyringService, account, "dXBkYXRlZA=="));
    CHECK(storage::keyring::get(storage::kKeyringService, account) == "dXBkYXRlZA==");
    CHECK(storage::keyring::erase(storage::kKeyringService, account));
    CHECK_FALSE(
        storage::keyring::get(storage::kKeyringService, account).has_value());
}
