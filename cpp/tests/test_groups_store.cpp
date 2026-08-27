#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>

#include "i2pchat/blindbox/state.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/groups/store.hpp"
#include "i2pchat/storage/profile_paths.hpp"
#include "temp_dir.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::groups::ContentType;
using i2pchat::groups::GroupState;
using i2pchat::groups::HistoryEntry;
using i2pchat::groups::StoredConversation;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;
using i2pchat::testing::TempDir;

namespace {

const std::string kAlice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kBob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

HistoryEntry text_entry(std::string msg_id, std::uint64_t group_seq, std::string text) {
    HistoryEntry entry;
    entry.kind = "me";
    entry.sender_id = kAlice;
    entry.content_type = ContentType::GroupText;
    entry.text = std::move(text);
    entry.msg_id = std::move(msg_id);
    entry.group_seq = group_seq;
    entry.epoch = 1;
    entry.created_at = "2026-02-03T04:05:06+00:00";
    return entry;
}

}  // namespace

TEST_CASE("the reference group conversation opens", "[groups][store][vectors]") {
    crypto::init();
    const nlohmann::json fixture = load_vector("group_store");
    const Bytes identity = hex_field(fixture, "identity_key_hex");
    const Bytes seed = hex_field(fixture, "signing_seed_hex");
    const std::string group_id = fixture.at("group_id").get<std::string>();
    const std::string profile = fixture.at("profile").get<std::string>();

    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), profile);
    {
        const Bytes blob = hex_field(fixture, "blob_hex");
        std::ofstream stream(paths.group_store(group_id), std::ios::binary);
        stream.write(reinterpret_cast<const char*>(blob.data()),
                     static_cast<std::streamsize>(blob.size()));
    }

    const std::optional<StoredConversation> loaded = groups::load_conversation(
        paths, group_id, ByteView(identity), ByteView(seed));
    REQUIRE(loaded.has_value());

    CHECK(loaded->state.group_id() == group_id);
    CHECK(loaded->state.epoch() == 2);
    CHECK(loaded->state.title() == "Группа β");
    CHECK(loaded->state.members() == std::vector<std::string>{kAlice, kBob});
    CHECK(loaded->created_at == "2026-02-03T04:05:06+00:00");
    CHECK(loaded->updated_at == "2026-02-03T04:05:06+00:00");
    CHECK(loaded->next_group_seq == 5);

    REQUIRE(loaded->history.size() == 2);
    CHECK(loaded->history[0].kind == "me");
    CHECK(loaded->history[0].text == "привет группе");
    CHECK(loaded->history[0].content_type == ContentType::GroupText);
    CHECK(loaded->history[0].delivery_results.at(kBob) == "delivered_live");
    CHECK(loaded->history[0].delivery_reasons.at(kBob) == "live-session");
    CHECK(loaded->history[0].source_peer.empty());
    CHECK(loaded->history[1].content_type == ContentType::GroupControl);
    CHECK(loaded->history[1].payload.at("op") == "member_left");
    CHECK(loaded->history[1].source_peer == kBob);
    CHECK(loaded->seen_msg_ids == std::vector<std::string>{"msg-1", "msg-2"});

    REQUIRE(loaded->pending_deliveries.size() == 1);
    const groups::PendingDelivery& pending = loaded->pending_deliveries.front();
    CHECK(pending.recipient_id == kBob);
    CHECK(pending.delivery_id == "msg-3:" + kBob);
    CHECK(pending.as_envelope().payload == "ещё не доставлено");
    CHECK(pending.as_group_state().members() == std::vector<std::string>{kAlice, kBob});

    REQUIRE(loaded->pending_blindbox_messages.size() == 1);
    CHECK(loaded->pending_blindbox_messages.front().msg_id == "msg-4");
    CHECK(loaded->pending_blindbox_messages.front().group_seq == 6);

    REQUIRE(loaded->blindbox_channel.has_value());
    const blindbox::GroupSnapshot& channel = *loaded->blindbox_channel;
    CHECK(channel.channel_id == "channel-beta");
    CHECK(channel.group_epoch == 2);
    CHECK(channel.state.send_index == 4);
    CHECK(channel.state.recv_base == 2);
    CHECK(channel.state.consumed_recv == std::set<std::uint64_t>{0, 1, 3});
    CHECK(channel.root_epoch == 6);
    CHECK(channel.root_send_index_base == 2);
    CHECK(channel.pending_root_epoch == 7);
    CHECK(channel.pending_root_target_members == std::vector<std::string>{kBob});
    CHECK(channel.pending_root_acked_members.empty());

    // The roots are the point of the fixture: they are wrapped under the
    // BlindBox local key for scope `group:<id>`, not under the record's key, so
    // reading the record is not enough to prove the channel is usable.
    REQUIRE(channel.root_secret.has_value());
    CHECK(encoding::hex_encode(ByteView(*channel.root_secret)) ==
          fixture.at("root_secret_hex").get<std::string>());
    REQUIRE(channel.pending_root_secret.has_value());
    CHECK(encoding::hex_encode(ByteView(*channel.pending_root_secret)) ==
          fixture.at("pending_root_secret_hex").get<std::string>());
    REQUIRE(channel.prev_roots.size() == 1);
    CHECK(channel.prev_roots.front().group_epoch == 1);
    CHECK(channel.prev_roots.front().root_epoch == 5);
    CHECK(encoding::hex_encode(ByteView(channel.prev_roots.front().secret)) ==
          fixture.at("previous_root_secret_hex").get<std::string>());
}

TEST_CASE("a group record written here reads back the same", "[groups][store][vectors]") {
    crypto::init();
    const nlohmann::json fixture = load_vector("group_store");
    const Bytes identity = hex_field(fixture, "identity_key_hex");
    const Bytes seed = hex_field(fixture, "signing_seed_hex");
    const std::string group_id = fixture.at("group_id").get<std::string>();
    const std::string profile = fixture.at("profile").get<std::string>();

    const StoredConversation original = groups::conversation_from_json(
        fixture.at("payload"), profile, ByteView(seed));

    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), profile);
    groups::save_conversation(paths, original, ByteView(identity), ByteView(seed));

    const std::optional<StoredConversation> reloaded = groups::load_conversation(
        paths, group_id, ByteView(identity), ByteView(seed));
    REQUIRE(reloaded.has_value());

    // Compared as payloads rather than field by field, so a field added to the
    // record later is covered without touching this test. Root wrapping uses a
    // fresh nonce per save, so those are compared after unwrapping instead.
    nlohmann::json written = groups::conversation_to_json(*reloaded, profile, ByteView(seed));
    nlohmann::json expected = fixture.at("payload");
    written.erase("blindbox_channel");
    expected.erase("blindbox_channel");
    CHECK(written == expected);
    CHECK(reloaded->blindbox_channel->root_secret == original.blindbox_channel->root_secret);
}

TEST_CASE("a missing group has no record", "[groups][store]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    const Bytes seed = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    CHECK_FALSE(groups::load_conversation(paths, "group-none", ByteView(identity),
                                          ByteView(seed))
                    .has_value());
    CHECK_FALSE(groups::delete_record(paths, "group-none"));
    CHECK_FALSE(groups::delete_record(paths, "  "));
}

TEST_CASE("appending history skips a message id already seen", "[groups][store]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    const Bytes seed = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    const GroupState state("group-1", 1, {kAlice, kBob}, "Team");

    const auto [first, appended] = groups::append_history(
        paths, state, text_entry("msg-1", 1, "hello"), ByteView(identity), ByteView(seed));
    CHECK(appended);
    CHECK(first.history.size() == 1);
    // The sequence floor follows the history, so the next message cannot reuse a
    // number a stored one already holds.
    CHECK(first.next_group_seq == 2);

    const auto [second, appended_again] = groups::append_history(
        paths, state, text_entry("msg-1", 1, "hello again"), ByteView(identity),
        ByteView(seed));
    CHECK_FALSE(appended_again);
    CHECK(second.history.size() == 1);
    CHECK(second.history.front().text == "hello");

    const auto [third, appended_third] = groups::append_history(
        paths, state, text_entry("msg-2", 7, "later"), ByteView(identity), ByteView(seed));
    CHECK(appended_third);
    CHECK(third.history.size() == 2);
    CHECK(third.next_group_seq == 8);
    CHECK(third.seen_msg_ids == std::vector<std::string>{"msg-1", "msg-2"});
}

TEST_CASE("an entry with no message id is always appended", "[groups][store]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    const Bytes seed = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");
    const GroupState state("group-1", 1, {kAlice, kBob});

    HistoryEntry entry = text_entry("", 1, "system note");
    for (int index = 0; index < 3; ++index) {
        const auto [conversation, appended] = groups::append_history(
            paths, state, entry, ByteView(identity), ByteView(seed));
        CHECK(appended);
        CHECK(conversation.history.size() == static_cast<std::size_t>(index) + 1);
        CHECK(conversation.seen_msg_ids.empty());
    }
}

TEST_CASE("upserting the state keeps history and pending work", "[groups][store]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    const Bytes seed = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    const GroupState first("group-1", 1, {kAlice, kBob}, "Team");
    (void)groups::append_history(paths, first, text_entry("msg-1", 1, "hello"),
                                 ByteView(identity), ByteView(seed));

    const GroupState grown("group-1", 2, {kAlice, kBob, "cccc"}, "Team renamed");
    const StoredConversation conversation =
        groups::upsert_state(paths, grown, ByteView(identity), ByteView(seed), 9);

    CHECK(conversation.state.epoch() == 2);
    CHECK(conversation.state.title() == "Team renamed");
    CHECK(conversation.state.members().size() == 3);
    CHECK(conversation.history.size() == 1);
    CHECK(conversation.next_group_seq == 9);
    // Not moved backwards by a lower request.
    CHECK(groups::upsert_state(paths, grown, ByteView(identity), ByteView(seed), 2)
              .next_group_seq == 9);
}

TEST_CASE("group listing is newest first and survives a broken record",
          "[groups][store]") {
    crypto::init();
    const Bytes identity = crypto::random_bytes(32);
    const Bytes seed = crypto::random_bytes(32);
    TempDir dir;
    const storage::ProfilePaths paths(dir.path(), "profile");

    StoredConversation older;
    older.state = GroupState("group-old", 1, {kAlice});
    older.created_at = "2026-01-01T00:00:00+00:00";
    older.updated_at = "2026-01-01T00:00:00+00:00";
    groups::save_conversation(paths, older, ByteView(identity), ByteView(seed));

    StoredConversation newer;
    newer.state = GroupState("group-new", 1, {kBob});
    newer.created_at = "2026-05-05T00:00:00+00:00";
    newer.updated_at = "2026-05-05T00:00:00+00:00";
    groups::save_conversation(paths, newer, ByteView(identity), ByteView(seed));

    {
        std::ofstream stream(paths.group_store("group-broken"), std::ios::binary);
        stream << "not a sealed record";
    }

    const std::vector<GroupState> states =
        groups::list_states(paths, ByteView(identity), ByteView(seed));
    REQUIRE(states.size() == 2);
    CHECK(states[0].group_id() == "group-new");
    CHECK(states[1].group_id() == "group-old");

    CHECK(groups::delete_record(paths, "group-old"));
    CHECK(groups::list_states(paths, ByteView(identity), ByteView(seed)).size() == 1);
}

TEST_CASE("a record from a future version is refused", "[groups][store]") {
    crypto::init();
    const Bytes seed = crypto::random_bytes(32);
    nlohmann::json payload = nlohmann::json::object();
    payload["version"] = groups::kGroupRecordVersion + 1;
    payload["state"] = {{"group_id", "group-1"}, {"epoch", 1}};

    CHECK_THROWS_AS(groups::conversation_from_json(payload, "profile", ByteView(seed)),
                    storage::SealedJsonError);
}

TEST_CASE("the duplicate filter is capped", "[groups][store]") {
    StoredConversation conversation;
    conversation.state = GroupState("group-1", 1, {kAlice});
    for (std::size_t index = 0; index < groups::kMaxSeenMessageIds + 10; ++index) {
        conversation.seen_msg_ids.push_back("msg-" + std::to_string(index));
    }
    conversation.normalize();

    REQUIRE(conversation.seen_msg_ids.size() == groups::kMaxSeenMessageIds);
    // The oldest ids go, so a recent replay is still suppressed.
    CHECK(conversation.seen_msg_ids.front() == "msg-10");
    CHECK(conversation.seen_msg_ids.back() ==
          "msg-" + std::to_string(groups::kMaxSeenMessageIds + 9));
}
