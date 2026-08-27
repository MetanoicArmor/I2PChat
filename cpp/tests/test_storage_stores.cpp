#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/compose_drafts.hpp"
#include "i2pchat/storage/contacts.hpp"
#include "i2pchat/storage/group_record.hpp"
#include "i2pchat/storage/profile_paths.hpp"
#include "i2pchat/storage/sealed_json.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

nlohmann::json sealed_fixture(const std::string& kind) {
    const auto document = load_vector("sealed_files");
    for (const auto& entry : document.at("files")) {
        if (entry.at("kind").get<std::string>() == kind) {
            return entry;
        }
    }
    FAIL("no fixture for " + kind);
    return {};
}

void write_bytes(const std::filesystem::path& path, ByteView data) {
    std::ofstream stream(path, std::ios::binary);
    stream.write(reinterpret_cast<const char*>(data.data()),
                 static_cast<std::streamsize>(data.size()));
}

/// The identity key every sealed fixture is keyed with.
Bytes fixture_identity_key(const nlohmann::json& fixture) {
    return hex_field(fixture.at("key_material"), "identity_key_hex");
}

const std::string kAlice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kBob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

storage::HistoryEntry entry(std::string kind, std::string text, std::string ts) {
    storage::HistoryEntry out;
    out.kind = std::move(kind);
    out.text = std::move(text);
    out.ts = std::move(ts);
    return out;
}

}  // namespace

TEST_CASE("the reference contact book opens and parses", "[stores][vectors]") {
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("contacts");
    const Bytes identity = fixture_identity_key(fixture);

    TempDir dir;
    const auto path = dir.file("profile.contacts.json");
    write_bytes(path, ByteView(hex_field(fixture, "blob_hex")));

    const storage::ContactBook book =
        storage::load_contact_book(path, ByteView(identity));

    REQUIRE(book.contacts().size() == 1);
    const storage::ContactRecord& record = book.contacts().front();
    CHECK(record.addr == kBob);
    // The fixture carries non-ASCII and an emoji, which is the point: the sealed
    // payload is ASCII-escaped JSON and has to survive the round trip.
    CHECK(record.display_name == "Тест 🙂");
    CHECK(record.note == "note");
    CHECK(record.last_preview == "hi");
    CHECK(record.last_activity_ts == "2026-01-01T00:00:00+00:00");
    CHECK(book.last_active_peer() == kBob);
}

TEST_CASE("a contact book we write is read back unchanged", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const auto path = dir.file("profile.contacts.json");

    storage::ContactBook book;
    REQUIRE(book.remember_peer(kAlice));
    REQUIRE(book.set_peer_profile(kAlice, "  Алиса  ", " note "));
    REQUIRE(book.touch_peer_message_meta(kAlice, "line one\nline two", " 2026-01-01T00:00:00+00:00 "));
    REQUIRE(book.set_last_active_peer(kAlice));

    storage::save_contact_book(path, book, ByteView(identity));
    const storage::ContactBook loaded = storage::load_contact_book(path, ByteView(identity));

    REQUIRE(loaded.contacts().size() == 1);
    CHECK(loaded.contacts().front().display_name == "Алиса");
    CHECK(loaded.contacts().front().note == "note");
    CHECK(loaded.contacts().front().last_preview == "line one line two");
    CHECK(loaded.contacts().front().last_activity_ts == "2026-01-01T00:00:00+00:00");
    CHECK(loaded.last_active_peer() == kAlice);
}

TEST_CASE("contact addresses are canonicalised strictly", "[stores]") {
    CHECK(storage::normalize_contact_address(kBob) == kBob);
    CHECK(storage::normalize_contact_address("  " + kBob + ".B32.I2P  ") == kBob);
    // Unlike the lenient parser used for pasted text, a contact address has to
    // be the whole string.
    CHECK(storage::normalize_contact_address("peer: " + kBob + ".b32.i2p").empty());
    CHECK(storage::normalize_contact_address("too-short").empty());
    CHECK(storage::normalize_contact_address("").empty());
    // '1' and '8' are outside the base32 alphabet.
    CHECK(storage::normalize_contact_address(std::string(52, '1')).empty());

    CHECK(storage::same_i2p_destination(kBob, kBob + ".b32.i2p"));
    CHECK(storage::same_i2p_destination("Group-7", "group-7"));
    CHECK_FALSE(storage::same_i2p_destination(kBob, kAlice));
}

TEST_CASE("remembering a peer keeps most-recently-used order", "[stores]") {
    storage::ContactBook book;
    CHECK(book.remember_peer(kAlice));
    CHECK(book.remember_peer(kBob));
    CHECK(book.ordered_peer_addrs() == std::vector<std::string>{kBob, kAlice});

    // Re-remembering the front peer changes nothing, so the caller can skip a save.
    CHECK_FALSE(book.remember_peer(kBob));
    CHECK(book.remember_peer(kAlice));
    CHECK(book.ordered_peer_addrs() == std::vector<std::string>{kAlice, kBob});

    CHECK_FALSE(book.remember_peer("not an address"));
    CHECK(book.contacts().size() == 2);
}

TEST_CASE("removing a peer clears it as last active", "[stores]") {
    storage::ContactBook book;
    REQUIRE(book.remember_peer(kAlice));
    REQUIRE(book.set_last_active_peer(kAlice));

    CHECK(book.remove_peer(kAlice + ".b32.i2p"));
    CHECK(book.contacts().empty());
    CHECK_FALSE(book.last_active_peer().has_value());
    CHECK_FALSE(book.remove_peer(kAlice));
}

TEST_CASE("a long preview is cut by code points, not bytes", "[stores]") {
    storage::ContactBook book;
    REQUIRE(book.remember_peer(kAlice));

    // 200 Cyrillic characters: 400 bytes, so a byte-based cut would split one in
    // half and produce invalid UTF-8.
    std::string long_text;
    for (int i = 0; i < 200; ++i) {
        long_text += "я";
    }
    REQUIRE(book.touch_peer_message_meta(kAlice, long_text, "ts"));

    const std::string& preview = book.contacts().front().last_preview;
    const auto length = encoding::utf8_length(preview);
    REQUIRE(length.has_value());
    CHECK(*length == storage::kPreviewMaxLength);
    CHECK(preview.ends_with("…"));
}

TEST_CASE("the version 1 contact list is upgraded on read", "[stores]") {
    const nlohmann::json legacy = {
        {"version", 1},
        {"contacts", {kAlice, kBob + ".b32.i2p", kAlice, "junk"}},
        {"last_active_peer", kBob},
    };
    const storage::ContactBook book = storage::parse_contact_book(legacy);

    // Duplicates collapse, the suffixed form normalises, junk is dropped.
    CHECK(book.ordered_peer_addrs() == std::vector<std::string>{kAlice, kBob});
    CHECK(book.last_active_peer() == kBob);
    CHECK(storage::contact_book_to_json(book).at("version") == 2);
}

TEST_CASE("a last active peer that is not in the list is dropped", "[stores]") {
    const nlohmann::json data = {
        {"version", 2},
        {"contacts", nlohmann::json::array({{{"addr", kAlice}}})},
        {"last_active_peer", kBob},
    };
    CHECK_FALSE(storage::parse_contact_book(data).last_active_peer().has_value());
}

TEST_CASE("the contact list is capped", "[stores]") {
    storage::ContactBook book;
    for (std::size_t i = 0; i < storage::kMaxContacts + 10; ++i) {
        // Vary the address while keeping it inside the base32 alphabet.
        std::string addr = kAlice;
        addr[0] = static_cast<char>('a' + (i % 20));
        addr[1] = static_cast<char>('a' + ((i / 20) % 20));
        addr[2] = static_cast<char>('2' + ((i / 400) % 6));
        book.remember_peer(addr);
    }
    CHECK(book.contacts().size() == storage::kMaxContacts);
    CHECK(storage::contact_book_to_json(book).at("contacts").size() ==
          storage::kMaxContacts);
}

TEST_CASE("the reference drafts file opens", "[stores][vectors]") {
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("compose_drafts");
    TempDir dir;
    const auto path = dir.file("profile.compose_drafts.json");
    write_bytes(path, ByteView(hex_field(fixture, "blob_hex")));

    const storage::ComposeDrafts drafts =
        storage::load_compose_drafts(path, ByteView(fixture_identity_key(fixture)));
    REQUIRE(drafts.size() == 1);
    CHECK(drafts.at(kBob) == "unsent draft");
}

TEST_CASE("drafts survive a save and load", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const auto path = dir.file("profile.compose_drafts.json");

    // Group ids are stored verbatim, so the keys here are not all addresses.
    const storage::ComposeDrafts drafts{{kAlice, "half-written"}, {"group-7", "мысль"}};
    storage::save_compose_drafts(path, drafts, ByteView(identity));

    CHECK(storage::load_compose_drafts(path, ByteView(identity)) == drafts);
    CHECK(storage::load_compose_drafts(dir.file("absent.json"), ByteView(identity)).empty());
}

TEST_CASE("the reference history file opens", "[stores][vectors]") {
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("chat_history");
    const Bytes identity = fixture_identity_key(fixture);
    const std::string peer = fixture.at("peer").get<std::string>();

    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    // The file name has to match, or the reader will not find it at all.
    REQUIRE(storage::peer_file_id(peer) == fixture.at("peer_file_id").get<std::string>());
    REQUIRE(storage::legacy_peer_file_id(peer) ==
            fixture.at("legacy_peer_file_id").get<std::string>());
    write_bytes(paths.chat_history(peer), ByteView(hex_field(fixture, "blob_hex")));

    const std::vector<storage::HistoryEntry> entries =
        storage::load_history(paths, peer, ByteView(identity));
    REQUIRE(entries.size() == 2);
    CHECK(entries[0].kind == "out");
    CHECK(entries[0].text == "привет");
    CHECK(entries[1].kind == "in");
    CHECK(entries[1].text == "hello");
    // The fixture omits the delivery fields, which must read as unset.
    CHECK_FALSE(entries[0].message_id.has_value());
    CHECK_FALSE(entries[0].retryable);
}

TEST_CASE("the history file key matches the reference derivation",
          "[stores][vectors]") {
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("chat_history");
    const Bytes identity = fixture_identity_key(fixture);
    const std::string peer = fixture.at("peer").get<std::string>();
    const Bytes blob = hex_field(fixture, "blob_hex");

    CHECK(storage::derive_sealed_profile_key(ByteView(identity), "I2PCHAT-HISTORY") ==
          hex_field(fixture.at("kdf"), "profile_key_hex"));

    const ByteView salt = ByteView(blob).subspan(6, 32);
    CHECK(storage::derive_sealed_file_key(ByteView(identity), salt, "I2PCHAT-HISTORY",
                                          peer) ==
          hex_field(fixture.at("kdf"), "file_key_hex"));
}

TEST_CASE("history file names follow the address verbatim", "[stores]") {
    // The reference implementation hashes the address after trim and lowercase
    // only, so the suffixed form names a *different* file. Canonicalising further
    // here would send us looking for a file Python never wrote.
    CHECK(storage::history_peer_key("  " + kBob + "  ") == kBob);
    CHECK(storage::history_peer_key(kBob + ".B32.I2P") == kBob + ".b32.i2p");
    CHECK(storage::peer_file_id(kBob) != storage::peer_file_id(kBob + ".b32.i2p"));
    CHECK(storage::legacy_peer_file_id(kBob).size() == 16);
}

TEST_CASE("history round trips with every field set", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    storage::HistoryEntry rich = entry("out", "текст", "2026-01-01T00:00:00+00:00");
    rich.message_id = "abc123";
    rich.delivery_state = "sent";
    rich.delivery_route = "direct";
    rich.delivery_hint = "hint";
    rich.delivery_reason = "reason";
    rich.retryable = true;

    storage::save_history(paths, kBob, {rich}, ByteView(identity));
    const auto loaded = storage::load_history(paths, kBob, ByteView(identity));

    REQUIRE(loaded.size() == 1);
    CHECK(loaded[0].text == "текст");
    CHECK(loaded[0].message_id == "abc123");
    CHECK(loaded[0].delivery_state == "sent");
    CHECK(loaded[0].delivery_route == "direct");
    CHECK(loaded[0].delivery_hint == "hint");
    CHECK(loaded[0].delivery_reason == "reason");
    CHECK(loaded[0].retryable);
}

TEST_CASE("an empty history is not written at all", "[stores]") {
    // Writing an empty file would still reveal that the conversation exists.
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    storage::save_history(paths, kBob, {}, ByteView(identity));
    CHECK_FALSE(std::filesystem::exists(paths.chat_history(kBob)));
}

TEST_CASE("the history salt is stable across saves", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    storage::save_history(paths, kBob, {entry("out", "one", "2026-01-01T00:00:00+00:00")},
                          ByteView(identity));
    const Bytes first = storage::read_file(paths.chat_history(kBob));

    storage::save_history(paths, kBob, {entry("out", "two", "2026-01-01T00:00:01+00:00")},
                          ByteView(identity));
    const Bytes second = storage::read_file(paths.chat_history(kBob));

    CHECK(Bytes(first.begin() + 6, first.begin() + 38) ==
          Bytes(second.begin() + 6, second.begin() + 38));
    CHECK(storage::load_history(paths, kBob, ByteView(identity)).at(0).text == "two");
}

TEST_CASE("a history file naming another peer is refused", "[stores]") {
    // Same key, wrong conversation: either the file was swapped in or something
    // is badly confused, and showing Bob's messages under Alice is unacceptable.
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    const nlohmann::json payload =
        storage::history_to_json(kAlice, {entry("in", "hi", "2026-01-01T00:00:00+00:00")},
                                 std::nullopt);
    storage::write_sealed_json(paths.chat_history(kBob), payload, ByteView(identity),
                               storage::chat_history_format(kBob));

    CHECK(storage::load_history(paths, kBob, ByteView(identity)).empty());
}

TEST_CASE("the legacy short history file name is still read", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    const nlohmann::json payload =
        storage::history_to_json(kBob, {entry("in", "old", "2026-01-01T00:00:00+00:00")},
                                 std::nullopt);
    storage::write_sealed_json(paths.legacy_chat_history(kBob), payload, ByteView(identity),
                               storage::chat_history_format(kBob));

    const auto loaded = storage::load_history(paths, kBob, ByteView(identity));
    REQUIRE(loaded.size() == 1);
    CHECK(loaded[0].text == "old");
}

TEST_CASE("a wrong identity key yields no history rather than an error",
          "[stores]") {
    crypto::init();
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    storage::save_history(paths, kBob, {entry("in", "hi", "2026-01-01T00:00:00+00:00")},
                          ByteView(crypto::random_bytes(32)));

    CHECK(storage::load_history(paths, kBob, ByteView(crypto::random_bytes(32))).empty());
}

TEST_CASE("history files are listed and deleted", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    storage::save_history(paths, kAlice, {entry("in", "a", "2026-01-01T00:00:00+00:00")},
                          ByteView(identity));
    storage::save_history(paths, kBob, {entry("in", "b", "2026-01-01T00:00:00+00:00")},
                          ByteView(identity));
    CHECK(storage::list_history_files(paths).size() == 2);

    CHECK(storage::delete_history(paths, kAlice));
    CHECK(storage::list_history_files(paths).size() == 1);
    CHECK_FALSE(storage::delete_history(paths, kAlice));
}

TEST_CASE("ISO-8601 timestamps parse to the same instant", "[stores]") {
    const auto utc = storage::parse_iso8601_utc("2026-01-01T00:00:00+00:00");
    REQUIRE(utc.has_value());
    CHECK(storage::parse_iso8601_utc("2026-01-01T00:00:00Z") == utc);
    // A naive timestamp is read as UTC, not as local time.
    CHECK(storage::parse_iso8601_utc("2026-01-01T00:00:00") == utc);
    CHECK(storage::parse_iso8601_utc("2026-01-01 00:00:00") == utc);
    CHECK(storage::parse_iso8601_utc("2026-01-01T03:00:00+03:00") == utc);
    CHECK(storage::parse_iso8601_utc("2025-12-31T21:00:00-03:00") == utc);
    CHECK(storage::parse_iso8601_utc("2026-01-01T00:00:00.123456+00:00") == utc);
    CHECK(storage::parse_iso8601_utc("2026-01-01") == utc);

    CHECK_FALSE(storage::parse_iso8601_utc("").has_value());
    CHECK_FALSE(storage::parse_iso8601_utc("not a timestamp").has_value());
    CHECK_FALSE(storage::parse_iso8601_utc("2026-13-01T00:00:00Z").has_value());
}

TEST_CASE("retention keeps the newest messages", "[stores]") {
    std::vector<storage::HistoryEntry> entries;
    for (int i = 0; i < 10; ++i) {
        entries.push_back(entry("in", std::to_string(i),
                                "2026-01-0" + std::to_string(i + 1) + "T00:00:00+00:00"));
    }

    const storage::RetentionResult result =
        storage::apply_history_retention(entries, {/*max_messages=*/3, /*max_age_days=*/0});
    REQUIRE(result.retained.size() == 3);
    CHECK(result.retained.front().text == "7");
    CHECK(result.retained.back().text == "9");
    // The marker names the oldest message that went, so the UI can say where the
    // record begins.
    CHECK(result.truncated_at == "2026-01-01T00:00:00+00:00");
}

TEST_CASE("retention drops messages past the age limit", "[stores]") {
    const auto now = storage::parse_iso8601_utc("2026-02-01T00:00:00Z");
    REQUIRE(now.has_value());

    const std::vector<storage::HistoryEntry> entries{
        entry("in", "ancient", "2025-01-01T00:00:00+00:00"),
        entry("in", "old", "2026-01-01T00:00:00+00:00"),
        entry("in", "fresh", "2026-01-31T00:00:00+00:00"),
        entry("in", "undated", ""),
    };

    const storage::RetentionResult result =
        storage::apply_history_retention(entries, {0, /*max_age_days=*/10}, now);

    // The undated message is kept: dropping history because a timestamp is odd
    // would lose data for no good reason.
    REQUIRE(result.retained.size() == 2);
    CHECK(result.retained[0].text == "fresh");
    CHECK(result.retained[1].text == "undated");
    CHECK(result.truncated_at == "2025-01-01T00:00:00+00:00");
}

TEST_CASE("retention with no limits changes nothing", "[stores]") {
    const std::vector<storage::HistoryEntry> entries{
        entry("in", "a", "2026-01-01T00:00:00+00:00"),
        entry("in", "b", "2026-01-02T00:00:00+00:00"),
    };
    const storage::RetentionResult result = storage::apply_history_retention(entries, {0, 0});
    CHECK(result.retained.size() == 2);
    CHECK_FALSE(result.truncated_at.has_value());
}

TEST_CASE("the reference group record opens", "[stores][vectors]") {
    crypto::init();
    const nlohmann::json fixture = sealed_fixture("group_store");
    const Bytes identity = fixture_identity_key(fixture);
    const std::string group_id = fixture.at("group_id").get<std::string>();

    REQUIRE(storage::group_token(group_id) ==
            fixture.at("group_token").get<std::string>());

    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    write_bytes(paths.group_store(group_id), ByteView(hex_field(fixture, "blob_hex")));

    const nlohmann::json payload = storage::read_group_record(
        paths.group_store(group_id), group_id, ByteView(identity));

    CHECK(payload.at("version") == 1);
    CHECK(payload.at("state").at("group_id") == group_id);
    CHECK(payload.at("state").at("title") == "Группа");
    CHECK(payload.at("state").at("members").size() == 2);
    CHECK(payload.at("next_group_seq") == 3);
}

TEST_CASE("group records round trip and are discoverable", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    const nlohmann::json payload = {{"version", 1}, {"state", {{"group_id", "group-7"}}}};
    storage::write_group_record(paths.group_store("group-7"), "group-7", payload,
                                ByteView(identity));

    CHECK(storage::read_group_record(paths.group_store("group-7"), "group-7",
                                     ByteView(identity)) == payload);
    CHECK(storage::list_group_record_files(paths).size() == 1);
    CHECK(storage::known_group_records(paths, {"group-7", "group-8"}) ==
          std::vector<std::string>{"group-7"});
}

TEST_CASE("one group record's key does not open another's", "[stores]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    storage::write_group_record(paths.group_store("group-7"), "group-7",
                                {{"version", 1}}, ByteView(identity));

    // Same identity, wrong group token: the file key is scoped per record, so
    // this must fail rather than quietly decrypt.
    CHECK_THROWS_AS(storage::read_group_record(paths.group_store("group-7"), "group-8",
                                               ByteView(identity)),
                    storage::SealedJsonError);
}

TEST_CASE("a legacy plaintext group record is still read", "[stores]") {
    crypto::init();
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    const auto path = paths.group_store("group-7");

    storage::atomic_write_json(path, {{"version", 1}, {"legacy", true}});
    const nlohmann::json payload =
        storage::read_group_record(path, "group-7", ByteView(crypto::random_bytes(32)));
    CHECK(payload.at("legacy") == true);
}

TEST_CASE("plaintext never overwrites a sealed record", "[stores]") {
    // Saving without a key must not silently strip a user's at-rest encryption.
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    const auto path = paths.group_store("group-7");

    storage::write_group_record(path, "group-7", {{"version", 1}}, ByteView(identity));
    CHECK_THROWS_AS(storage::write_group_record(path, "group-7", {{"version", 2}},
                                                std::nullopt),
                    storage::SealedJsonError);
}
