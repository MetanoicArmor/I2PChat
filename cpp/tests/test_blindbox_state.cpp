#include <catch2/catch_test_macros.hpp>

#include <fstream>

#include "i2pchat/blindbox/key_schedule.hpp"
#include "i2pchat/blindbox/state.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using namespace i2pchat::blindbox;
using i2pchat::testing::hex_field;
using i2pchat::testing::hex_of;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

const nlohmann::json& state_vectors() {
    static const nlohmann::json document = load_vector("blindbox_state");
    return document;
}

Bytes seed_from_vectors() {
    return hex_field(state_vectors().at("wrap_keys").at(0), "signing_seed_hex");
}

std::string profile_from_vectors() {
    return state_vectors().at("wrap_keys").at(0).at("profile").get<std::string>();
}

}  // namespace

TEST_CASE("local wrap keys match the reference for both versions") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();

    for (const nlohmann::json& entry : state_vectors().at("wrap_keys")) {
        const auto scope = entry.at("scope").get<std::string>();
        CHECK(hex_of(ByteView(local_wrap_key(profile, scope, ByteView(seed),
                                             kLocalWrapVersionCurrent))) ==
              entry.at("wrap_key_v2_hex").get<std::string>());
        // v1 ignores the seed entirely, which is why it was replaced; it must
        // still derive identically or old state files will not open.
        CHECK(hex_of(ByteView(local_wrap_key(profile, scope, {},
                                             kLocalWrapVersionLegacy))) ==
              entry.at("wrap_key_v1_hex").get<std::string>());
    }
}

TEST_CASE("the wrap scope ignores the b32 suffix and case") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const auto& entries = state_vectors().at("wrap_keys");

    // The first two vector entries are the same peer with and without the
    // suffix, and must land on one key: otherwise a contact stored one way
    // could not read state written the other.
    REQUIRE(entries.size() >= 2);
    CHECK(entries.at(0).at("wrap_key_v2_hex") == entries.at(1).at("wrap_key_v2_hex"));

    const auto scope = entries.at(0).at("scope").get<std::string>();
    std::string shouting = scope;
    for (char& character : shouting) {
        character = static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
    }
    CHECK(local_wrap_key(profile, shouting + ".B32.I2P", ByteView(seed)) ==
          local_wrap_key(profile, scope, ByteView(seed)));

    CHECK_THROWS_AS(wrap_scope_for_peer("   "), BlindBoxError);
    CHECK(wrap_scope_for_group(" group-alpha ") == "group:group-alpha");
}

TEST_CASE("a wrap key is bound to the identity and the profile") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const Bytes other_seed(32, 0x7f);

    // Both bindings matter: the profile keeps two profiles on one machine
    // apart, and the seed makes a copied state file useless to a thief.
    CHECK(local_wrap_key(profile, "peer-1", ByteView(seed)) !=
          local_wrap_key(profile, "peer-1", ByteView(other_seed)));
    CHECK(local_wrap_key(profile, "peer-1", ByteView(seed)) !=
          local_wrap_key("other-profile", "peer-1", ByteView(seed)));
    CHECK(local_wrap_key(profile, "peer-1", ByteView(seed)) !=
          local_wrap_key(profile, "peer-2", ByteView(seed)));

    // v2 without a seed is refused rather than silently falling back to a key
    // anybody could recompute.
    CHECK_THROWS_AS(local_wrap_key(profile, "peer-1", {}), BlindBoxError);
    CHECK_THROWS_AS(local_wrap_key(profile, "peer-1", ByteView(seed), 3), BlindBoxError);
}

TEST_CASE("a root secret wrapped by the reference implementation opens") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const auto peer = state_vectors().at("peer_id").get<std::string>();
    const Bytes expected = hex_field(state_vectors(), "root_secret_hex");

    {
        const auto [secret, version] = decrypt_root_secret(
            state_vectors().at("encrypted_root_v2_hex").get<std::string>(), profile, peer,
            ByteView(seed));
        CHECK(secret == expected);
        CHECK(version == kLocalWrapVersionCurrent);
    }
    {
        // A legacy blob is recognised without being told which version it is,
        // so a profile that has not been rewritten yet still opens.
        const auto [secret, version] = decrypt_root_secret(
            state_vectors().at("encrypted_root_v1_hex").get<std::string>(), profile, peer,
            ByteView(seed));
        CHECK(secret == expected);
        CHECK(version == kLocalWrapVersionLegacy);
    }

    CHECK_THROWS_AS(decrypt_root_secret("not-hex", profile, peer, ByteView(seed)),
                    BlindBoxError);
    CHECK_THROWS_AS(decrypt_root_secret("00112233", profile, peer, ByteView(seed)),
                    BlindBoxError);
}

TEST_CASE("a root secret round-trips through our own wrapping") {
    const Bytes seed(32, 0x11);
    const Bytes secret(32, 0x42);

    const std::string wrapped =
        encrypt_root_secret(ByteView(secret), "default", "peer-1", ByteView(seed));
    const auto [opened, version] =
        decrypt_root_secret(wrapped, "default", "peer-1", ByteView(seed));
    CHECK(opened == secret);
    CHECK(version == kLocalWrapVersionCurrent);

    // The wrong identity does not open it, and the failure is loud.
    const Bytes wrong_seed(32, 0x22);
    CHECK_THROWS_AS(decrypt_root_secret(wrapped, "default", "peer-1", ByteView(wrong_seed)),
                    BlindBoxError);
}

TEST_CASE("the state file name matches the reference") {
    CHECK(peer_state_filename("default", "AbC.b32.i2p") ==
          "default.blindbox.abc.b32.i2p.json");
    // The peer id comes off the wire, so separators are replaced. Dots survive
    // because a b32 address is full of them, and they are harmless once no
    // separator can follow them.
    CHECK(peer_state_filename("default", "../../etc/passwd") ==
          "default.blindbox..._.._etc_passwd.json");
    CHECK(peer_state_filename("default", "peer with spaces") ==
          "default.blindbox.peer_with_spaces.json");
    CHECK_THROWS_AS(peer_state_filename("default", ""), BlindBoxError);
}

TEST_CASE("message counters serialise as the reference does") {
    const nlohmann::json expected = state_vectors().at("state");

    BlindBoxState state;
    state.send_index = expected.at("send_index").get<std::uint64_t>();
    state.recv_base = 0;
    for (const std::uint64_t index : {0u, 1u, 2u, 5u}) {
        state.mark_consumed(index, 1700000000);
    }
    state.updated_at = expected.at("updated_at").get<std::int64_t>();

    const nlohmann::json produced = state.to_json();
    CHECK(produced.at("version") == expected.at("version"));
    CHECK(produced.at("send_index") == expected.at("send_index"));
    // Consuming 0,1,2 settles the base at 3; 5 stays pending because 3 and 4
    // have not arrived.
    CHECK(produced.at("recv_base") == expected.at("recv_base"));
    CHECK(produced.at("recv_window") == expected.at("recv_window"));
    CHECK(produced.at("updated_at") == expected.at("updated_at"));

    const BlindBoxState reloaded = BlindBoxState::from_json(expected);
    CHECK(reloaded.send_index == state.send_index);
    CHECK(reloaded.recv_base == state.recv_base);
    CHECK(reloaded.consumed_recv.contains(5));
}

TEST_CASE("the receive window skips settled slots") {
    BlindBoxState state;
    state.recv_window = 4;
    CHECK(state.pending_recv_indexes() == std::vector<std::uint64_t>{0, 1, 2, 3});

    state.mark_consumed(1, 1700000000);
    // 1 is settled but the base cannot move past 0, so the window shows the
    // gap rather than pretending 1 is still waiting.
    CHECK(state.pending_recv_indexes() == std::vector<std::uint64_t>{0, 2, 3});

    state.mark_consumed(0, 1700000000);
    CHECK(state.recv_base == 2);
    CHECK(state.pending_recv_indexes() == std::vector<std::uint64_t>{2, 3, 4, 5});
}

TEST_CASE("a malformed state file is refused rather than guessed at") {
    CHECK_THROWS_AS(BlindBoxState::from_json(nlohmann::json::array()), BlindBoxError);
    CHECK_THROWS_AS(BlindBoxState::from_json({{"version", "BLINDBOX_STATE_V2"}}),
                    BlindBoxError);
    CHECK_THROWS_AS(
        BlindBoxState::from_json({{"version", "BLINDBOX_STATE_V1"}, {"recv_window", 0}}),
        BlindBoxError);
    CHECK_THROWS_AS(BlindBoxState::from_json(
                        {{"version", "BLINDBOX_STATE_V1"}, {"recv_window", 4097}}),
                    BlindBoxError);
    // A negative index would derive keys for a slot that cannot exist.
    CHECK_THROWS_AS(BlindBoxState::from_json(
                        {{"version", "BLINDBOX_STATE_V1"}, {"send_index", -1}}),
                    BlindBoxError);
    CHECK_THROWS_AS(
        BlindBoxState::from_json({{"version", "BLINDBOX_STATE_V1"},
                                  {"consumed_recv", nlohmann::json::array({-2})}}),
        BlindBoxError);
}

TEST_CASE("a peer state file written by the reference loads whole") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const auto peer = state_vectors().at("peer_id").get<std::string>();
    const nlohmann::json document = state_vectors().at("state_file");

    const PeerSnapshot snapshot =
        peer_snapshot_from_json(document, peer, profile, ByteView(seed));

    CHECK(snapshot.state.send_index == 7);
    CHECK(snapshot.state.recv_base == 3);
    REQUIRE(snapshot.root_secret.has_value());
    CHECK(*snapshot.root_secret == hex_field(state_vectors(), "root_secret_hex"));
    CHECK(snapshot.root_epoch == 4);
    CHECK(snapshot.root_created_at == 1699990000);
    CHECK(snapshot.root_send_index_base == 5);
    CHECK_FALSE(snapshot.pending_root_secret.has_value());
    REQUIRE(snapshot.prev_roots.size() == 1);
    CHECK(snapshot.prev_roots[0].epoch == 3);
    CHECK(snapshot.prev_roots[0].expires_at == 1700003600);
    CHECK(snapshot.prev_roots[0].secret == Bytes(32, 0x31));
}

TEST_CASE("a peer snapshot survives a save and load") {
    TempDir dir;
    const std::string profile = "default";
    const Bytes seed(32, 0x11);
    const std::filesystem::path path =
        dir.file(peer_state_filename(profile, "peer-one.b32.i2p"));

    PeerSnapshot snapshot;
    snapshot.peer_id = "peer-one";
    snapshot.state.send_index = 12;
    snapshot.state.mark_consumed(0, 1700000000);
    snapshot.root_secret = Bytes(32, 0xaa);
    snapshot.root_epoch = 2;
    snapshot.root_created_at = 1699999999;
    snapshot.root_send_index_base = 10;
    snapshot.pending_root_secret = Bytes(32, 0xbb);
    snapshot.pending_root_epoch = 3;
    snapshot.prev_roots.push_back(PreviousRoot{1, Bytes(32, 0xcc), 1900000000});

    save_peer_snapshot(path, snapshot, profile, ByteView(seed));
    REQUIRE(std::filesystem::exists(path));

    const PeerSnapshot loaded =
        load_peer_snapshot(path, "peer-one", profile, ByteView(seed));
    CHECK(loaded.state.send_index == 12);
    CHECK(loaded.state.recv_base == 1);
    CHECK(loaded.root_secret == snapshot.root_secret);
    CHECK(loaded.root_epoch == 2);
    CHECK(loaded.root_send_index_base == 10);
    CHECK(loaded.pending_root_secret == snapshot.pending_root_secret);
    CHECK(loaded.pending_root_epoch == 3);
    REQUIRE(loaded.prev_roots.size() == 1);
    CHECK(loaded.prev_roots[0].secret == Bytes(32, 0xcc));

    // The counters are readable, which is what lets a user inspect and repair
    // their own state; only the roots are wrapped.
    std::ifstream in(path);
    const nlohmann::json raw = nlohmann::json::parse(in);
    CHECK(raw.at("send_index").get<int>() == 12);
    CHECK(raw.at("blindbox_root_secret_enc").get<std::string>().find("aaaa") ==
          std::string::npos);
}

TEST_CASE("a missing state file reads as first contact") {
    TempDir dir;
    const PeerSnapshot snapshot = load_peer_snapshot(dir.file("absent.json"), "peer-one",
                                                     "default", ByteView(Bytes(32, 1)));
    CHECK(snapshot.peer_id == "peer-one");
    CHECK_FALSE(snapshot.root_secret.has_value());
    CHECK(snapshot.state.send_index == 0);
}

TEST_CASE("a rootless snapshot is not written at all") {
    TempDir dir;
    const std::filesystem::path path = dir.file("default.blindbox.peer.json");

    PeerSnapshot snapshot;
    snapshot.peer_id = "peer-one";
    snapshot.state.send_index = 3;
    save_peer_snapshot(path, snapshot, "default", ByteView(Bytes(32, 1)));

    // A file with counters and no root would read as an established channel
    // that cannot decrypt anything.
    CHECK_FALSE(std::filesystem::exists(path));
}

TEST_CASE("state written under a legacy wrap is rewritten under the current one") {
    TempDir dir;
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const auto peer = state_vectors().at("peer_id").get<std::string>();

    nlohmann::json legacy = state_vectors().at("state_file");
    legacy["blindbox_wrap_version"] = kLocalWrapVersionLegacy;
    legacy["blindbox_root_secret_enc"] =
        state_vectors().at("encrypted_root_v1_hex").get<std::string>();
    legacy.erase("blindbox_prev_roots");

    const PeerSnapshot snapshot =
        peer_snapshot_from_json(legacy, peer, profile, ByteView(seed));
    CHECK(snapshot.wrap_version == kLocalWrapVersionLegacy);
    REQUIRE(snapshot.root_secret.has_value());

    const std::filesystem::path path = dir.file(peer_state_filename(profile, peer));
    save_peer_snapshot(path, snapshot, profile, ByteView(seed));

    std::ifstream in(path);
    const nlohmann::json rewritten = nlohmann::json::parse(in);
    CHECK(rewritten.at("blindbox_wrap_version").get<int>() == kLocalWrapVersionCurrent);
    // And it still opens, now under the seed-bound key.
    const PeerSnapshot reloaded =
        load_peer_snapshot(path, peer, profile, ByteView(seed));
    CHECK(reloaded.wrap_version == kLocalWrapVersionCurrent);
    CHECK(reloaded.root_secret == snapshot.root_secret);
}

TEST_CASE("an unreadable previous root does not cost the whole file") {
    const std::string profile = profile_from_vectors();
    const Bytes seed = seed_from_vectors();
    const auto peer = state_vectors().at("peer_id").get<std::string>();

    nlohmann::json document = state_vectors().at("state_file");
    document["blindbox_prev_roots"].push_back(
        {{"epoch", 2}, {"expires_at", 1700003600}, {"secret_enc", "00112233"}});
    document["blindbox_prev_roots"].push_back(
        {{"epoch", 1}, {"expires_at", 1700003600}, {"secret_enc", "not-hex"}});

    const PeerSnapshot snapshot =
        peer_snapshot_from_json(document, peer, profile, ByteView(seed));
    // The current root is what the channel needs to keep working; a spoiled
    // history entry only costs the ability to read one old message.
    REQUIRE(snapshot.root_secret.has_value());
    CHECK(snapshot.prev_roots.size() == 1);
}

TEST_CASE("previous roots are pruned by expiry and count") {
    const std::int64_t now = 1700000000;
    std::vector<PreviousRoot> roots{
        PreviousRoot{1, Bytes(32, 0x01), now - 1},
        PreviousRoot{2, Bytes(32, 0x02), now + 100},
        PreviousRoot{3, Bytes(32, 0x03), now + 300},
        PreviousRoot{4, Bytes(32, 0x04), now + 200},
        // A wrong-length secret cannot be a root and is dropped.
        PreviousRoot{5, Bytes(16, 0x05), now + 400},
    };

    const std::vector<PreviousRoot> pruned = prune_previous_roots(roots, 2, now);
    REQUIRE(pruned.size() == 2);
    // Newest first: an old root only serves messages still in flight.
    CHECK(pruned[0].epoch == 3);
    CHECK(pruned[1].epoch == 4);

    CHECK(prune_previous_roots(roots, 10, now).size() == 3);
    CHECK(prune_previous_roots(std::vector<PreviousRoot>{}, 4, now).empty());
}

TEST_CASE("a group channel round-trips through its stored form") {
    const std::string profile = "default";
    const Bytes seed(32, 0x11);

    GroupSnapshot snapshot;
    snapshot.group_id = "group-alpha";
    snapshot.channel_id = "chan-1";
    snapshot.group_epoch = 4;
    snapshot.state.send_index = 9;
    snapshot.state.mark_consumed(0, 1700000000);
    snapshot.root_secret = Bytes(32, 0xaa);
    snapshot.root_epoch = 2;
    snapshot.root_created_at = 1699999999;
    snapshot.root_send_index_base = 8;
    snapshot.pending_root_secret = Bytes(32, 0xbb);
    snapshot.pending_root_epoch = 3;
    snapshot.pending_root_target_members = {"member-a", "member-b"};
    snapshot.pending_root_acked_members = {"member-a"};
    snapshot.prev_roots.push_back(GroupPreviousRoot{3, 1, Bytes(32, 0xcc), 1900000000});

    const nlohmann::json stored =
        group_snapshot_to_json(snapshot, profile, ByteView(seed));
    const GroupSnapshot loaded =
        group_snapshot_from_json(stored, "group-alpha", profile, ByteView(seed));

    CHECK(loaded.channel_id == "chan-1");
    CHECK(loaded.group_epoch == 4);
    CHECK(loaded.state.send_index == 9);
    CHECK(loaded.state.recv_base == 1);
    CHECK(loaded.root_secret == snapshot.root_secret);
    CHECK(loaded.pending_root_secret == snapshot.pending_root_secret);
    CHECK(loaded.pending_root_target_members == snapshot.pending_root_target_members);
    CHECK(loaded.pending_root_acked_members == snapshot.pending_root_acked_members);
    REQUIRE(loaded.prev_roots.size() == 1);
    CHECK(loaded.prev_roots[0].group_epoch == 3);
    CHECK(loaded.prev_roots[0].root_epoch == 1);

    // A group root is wrapped under the group's own scope, so a compromised
    // pairwise channel does not hand over group history.
    CHECK_THROWS_AS(
        decrypt_root_secret(stored.at("root_secret_enc").get<std::string>(), profile,
                            "group-alpha", ByteView(seed)),
        BlindBoxError);
}

TEST_CASE("a group channel with no root stores an explicit null") {
    GroupSnapshot snapshot;
    snapshot.group_id = "group-alpha";
    const nlohmann::json stored =
        group_snapshot_to_json(snapshot, "default", ByteView(Bytes(32, 1)));

    // The reference stores null rather than omitting the field, and a client
    // reading it must be able to tell "no root yet" from "field missing".
    CHECK(stored.at("root_secret_enc").is_null());
    CHECK(stored.at("pending_root_secret_enc").is_null());

    const GroupSnapshot loaded = group_snapshot_from_json(stored, "group-alpha", "default",
                                                          ByteView(Bytes(32, 1)));
    CHECK_FALSE(loaded.root_secret.has_value());
}
