#include <catch2/catch_test_macros.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/profile_backup.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using i2pchat::testing::TempDir;

TEST_CASE("profile backup round trips through a passphrase", "[backup]") {
    crypto::init();
    TempDir src("backup-src");
    TempDir dst("backup-dst");
    const auto pdir = src.path() / "profiles" / "alice";
    std::filesystem::create_directories(pdir);
    storage::atomic_write_text(pdir / "alice.dat", "privkey-line\n");
    storage::atomic_write_text(pdir / "alice.contacts.json", "{\"contacts\":[]}\n");
    storage::atomic_write_bytes(pdir / "alice.history.deadbeef.enc", as_bytes("hist"));

    const auto bundle = src.file("alice.i2pchat-profile-backup");
    const auto exported =
        storage::export_profile_bundle(bundle, src.path(), "alice", "passphrase", true);
    CHECK(exported.file_count >= 2);
    CHECK(exported.history_files == 1);

    CHECK_THROWS_AS(storage::import_profile_bundle(bundle, dst.path(), "wrong"),
                    storage::BackupError);

    const auto imported = storage::import_profile_bundle(bundle, dst.path(), "passphrase");
    CHECK(imported.target_profile == "alice");
    CHECK(std::filesystem::exists(dst.path() / "profiles" / "alice" / "alice.dat"));
    CHECK(std::filesystem::exists(dst.path() / "profiles" / "alice" / "alice.contacts.json"));
    CHECK(std::filesystem::exists(dst.path() / "profiles" / "alice" /
                                  "alice.history.deadbeef.enc"));
}

TEST_CASE("history backup skip vs overwrite", "[backup]") {
    crypto::init();
    TempDir dir("backup-hist");
    const auto pdir = dir.path() / "profiles" / "bob";
    std::filesystem::create_directories(pdir);
    storage::atomic_write_text(pdir / "bob.dat", "key\n");
    storage::atomic_write_bytes(pdir / "bob.history.aaa.enc", as_bytes("one"));

    const auto bundle = dir.file("bob.i2pchat-history-backup");
    storage::export_history_bundle(bundle, dir.path(), "bob", "secret");

    storage::atomic_write_bytes(pdir / "bob.history.aaa.enc", as_bytes("changed"));
    const auto skipped =
        storage::import_history_bundle(bundle, dir.path(), "bob", "secret", false);
    CHECK(skipped.skipped_files == 1);
    CHECK(storage::read_file(pdir / "bob.history.aaa.enc") == to_bytes("changed"));

    const auto overwritten =
        storage::import_history_bundle(bundle, dir.path(), "bob", "secret", true);
    CHECK(overwritten.restored_files == 1);
    CHECK(storage::read_file(pdir / "bob.history.aaa.enc") == to_bytes("one"));
}
