#include <catch2/catch_test_macros.hpp>

#include <string>
#include <vector>

#include "i2pchat/presentation/chat_view.hpp"

using namespace i2pchat;
using namespace i2pchat::presentation;

namespace {

constexpr const char* kAlice =
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
constexpr const char* kBob = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

storage::ContactBook book_with(std::string_view addr, std::string_view name,
                               std::string_view preview = {}) {
    storage::ContactBook book;
    book.remember_peer(addr);
    book.set_peer_profile(addr, name, "");
    if (!preview.empty()) {
        book.touch_peer_message_meta(addr, preview, "2024-05-01T10:00:00+00:00");
    }
    return book;
}

}  // namespace

TEST_CASE("addresses are shortened to a stable prefix") {
    CHECK(short_address(kAlice) == "aaaaaaaa…");
    // The .b32.i2p suffix is noise in a label, and the case is normalised so the
    // same peer never appears twice under two spellings.
    CHECK(short_address("ABCDEFGHIJ.b32.i2p") == "abcdefgh…");
    CHECK(short_address("short") == "short");
    CHECK(short_address("") == "(unknown)");
}

TEST_CASE("delivery markers cover every state history writes") {
    CHECK(delivery_marker("sent") == "·");
    CHECK(delivery_marker("delivered") == "✓");
    CHECK(delivery_marker("queued") == "⧗");
    CHECK(delivery_marker("failed") == "✗");
    CHECK(delivery_marker("something-new").empty());
}

TEST_CASE("a clock is rendered from an ISO timestamp") {
    // Local time makes the exact digits machine-dependent; what must hold is the
    // shape, and that garbage does not throw or produce something misleading.
    const std::string clock = format_clock("2024-05-01T10:11:12+00:00");
    REQUIRE(clock.size() == 8);
    CHECK(clock[2] == ':');
    CHECK(clock[5] == ':');
    CHECK(format_clock("not a timestamp").empty());
    CHECK(format_clock("").empty());
}

TEST_CASE("history entries become chat lines") {
    const storage::ContactBook contacts = book_with(kAlice, "Alice");

    SECTION("incoming is attributed to the contact's name") {
        storage::HistoryEntry entry;
        entry.kind = "in";
        entry.text = "hi";
        entry.ts = "2024-05-01T10:00:00+00:00";
        const ChatLine line = line_from_history(entry, contacts, kAlice);
        CHECK(line.kind == LineKind::Incoming);
        CHECK(line.author == "Alice");
        CHECK(line.text == "hi");
        CHECK(line.marker.empty());
    }

    SECTION("a nameless peer falls back to the short address") {
        storage::HistoryEntry entry;
        entry.kind = "in";
        entry.text = "hi";
        const ChatLine line = line_from_history(entry, contacts, kBob);
        CHECK(line.author == "bbbbbbbb…");
    }

    SECTION("outgoing carries the delivery marker") {
        storage::HistoryEntry entry;
        entry.kind = "out";
        entry.text = "hello";
        entry.delivery_state = "delivered";
        const ChatLine line = line_from_history(entry, contacts, kAlice);
        CHECK(line.kind == LineKind::Outgoing);
        CHECK(line.author == "you");
        CHECK(line.marker == "✓");
    }

    SECTION("a failure reason is shown, and beats the hint") {
        storage::HistoryEntry entry;
        entry.kind = "out";
        entry.text = "hello";
        entry.delivery_state = "failed";
        entry.delivery_hint = "will retry";
        entry.delivery_reason = "no replicas reachable";
        const ChatLine line = line_from_history(entry, contacts, kAlice);
        CHECK(line.marker == "✗");
        CHECK(line.detail == "no replicas reachable");
    }

    SECTION("unknown kinds render as system notes rather than vanishing") {
        storage::HistoryEntry entry;
        entry.kind = "sys";
        entry.text = "connected";
        CHECK(line_from_history(entry, contacts, kAlice).kind == LineKind::System);
        entry.kind = "err";
        CHECK(line_from_history(entry, contacts, kAlice).kind == LineKind::Error);
        entry.kind = "from-a-future-version";
        CHECK(line_from_history(entry, contacts, kAlice).kind == LineKind::System);
    }

    SECTION("Python history kinds me/peer map to outgoing/incoming") {
        storage::HistoryEntry peer;
        peer.kind = "peer";
        peer.text = "from python";
        CHECK(line_from_history(peer, contacts, kAlice).kind == LineKind::Incoming);
        CHECK(line_from_history(peer, contacts, kAlice).author == "Alice");
        storage::HistoryEntry me;
        me.kind = "me";
        me.text = "from me";
        me.delivery_state = "sent";
        CHECK(line_from_history(me, contacts, kAlice).kind == LineKind::Outgoing);
        CHECK(line_from_history(me, contacts, kAlice).author == "you");
    }

    SECTION("a whole conversation keeps its order") {
        std::vector<storage::HistoryEntry> entries(3);
        entries[0].kind = "in";
        entries[0].text = "first";
        entries[1].kind = "out";
        entries[1].text = "second";
        entries[2].kind = "in";
        entries[2].text = "third";
        const std::vector<ChatLine> lines = lines_from_history(entries, contacts, kAlice);
        REQUIRE(lines.size() == 3);
        CHECK(lines[0].text == "first");
        CHECK(lines[2].text == "third");
    }
}

TEST_CASE("contact rows carry index, liveness and selection") {
    storage::ContactBook contacts = book_with(kAlice, "Alice", "last thing said");
    contacts.remember_peer(kBob);

    const std::vector<ContactRow> rows =
        contact_rows(contacts, {std::string(kBob)}, kAlice, {{kBob, 3}});
    REQUIRE(rows.size() == 2);

    // remember_peer puts the newest first, so Bob leads.
    CHECK(rows[0].addr == kBob);
    CHECK(rows[0].index == 1);
    CHECK(rows[0].live);
    CHECK_FALSE(rows[0].selected);
    CHECK(rows[0].unread == 3);

    CHECK(rows[1].index == 2);
    CHECK(rows[1].label == "Alice (aaaaaaaa…)");
    CHECK(rows[1].preview == "last thing said");
    CHECK_FALSE(rows[1].live);
    CHECK(rows[1].selected);
    CHECK(rows[1].unread == 0);
}

TEST_CASE("selection matches a contact through the .b32.i2p suffix") {
    const storage::ContactBook contacts = book_with(kAlice, "Alice");
    const std::vector<ContactRow> rows =
        contact_rows(contacts, {}, std::string(kAlice) + ".b32.i2p", {});
    REQUIRE(rows.size() == 1);
    CHECK(rows[0].selected);
}

TEST_CASE("status rows describe the session") {
    StatusInput input;
    input.profile = "default";
    input.local_addr = kAlice;
    input.transport = session::TransportState::Ready;
    input.live_peers = {std::string(kBob)};
    input.selected = kBob;
    input.blindbox_ready = true;
    input.contacts = 4;

    const std::vector<InfoRow> rows = status_rows(input);
    const auto value_of = [&](std::string_view name) -> std::string {
        for (const InfoRow& row : rows) {
            if (row.name == name) {
                return row.value;
            }
        }
        return "(missing)";
    };
    CHECK(value_of("profile") == "default");
    CHECK(value_of("address") == kAlice);
    CHECK(value_of("transport") == "ready");
    CHECK(value_of("connected") == "1");
    CHECK(value_of("selected") == "bbbbbbbb…");
    CHECK(value_of("contacts") == "4");
    CHECK(value_of("offline delivery") == "ready");
    // Nothing queued means the row is absent rather than showing a zero.
    CHECK(value_of("queued offline") == "(missing)");

    input.pending_offline = 2;
    CHECK(status_rows(input).back().value == "2");

    SECTION("an unstarted session says so instead of showing an empty address") {
        StatusInput fresh;
        fresh.profile = "default";
        const std::vector<InfoRow> fresh_rows = status_rows(fresh);
        CHECK(fresh_rows[1].value == "(not yet known)");
        CHECK(fresh_rows[4].value == "(none)");
    }
}

TEST_CASE("the header line is compact and mentions the peer count") {
    StatusInput input;
    input.profile = "work";
    input.transport = session::TransportState::WarmingTunnels;
    const std::string header = status_line(input);
    CHECK(header.find("work") != std::string::npos);
    CHECK(header.find("0 peers") != std::string::npos);

    input.live_peers = {std::string(kBob)};
    CHECK(status_line(input).find("1 peer") != std::string::npos);
}

TEST_CASE("the trust prompt distinguishes a first sighting from a key change") {
    const std::string new_key(64, 'a');
    const std::string old_key(64, 'b');

    const TrustPromptView first = trust_prompt_view(session::TrustPrompt::FirstSighting,
                                                    kAlice, new_key, "");
    CHECK_FALSE(first.dangerous);
    CHECK(first.title.find("aaaaaaaa…") != std::string::npos);
    CHECK(first.previous_fingerprint.empty());
    CHECK(first.fingerprint.rfind("aaaa aaaa", 0) == 0);

    const TrustPromptView changed = trust_prompt_view(session::TrustPrompt::KeyChanged,
                                                      kAlice, new_key, old_key);
    CHECK(changed.dangerous);
    CHECK_FALSE(changed.previous_fingerprint.empty());
    CHECK(changed.body.find("impersonation") != std::string::npos);
}

TEST_CASE("fingerprints are grouped in fours") {
    CHECK(group_fingerprint("0123456789ab") == "0123 4567 89ab");
    CHECK(group_fingerprint("ABC") == "abc");
    CHECK(group_fingerprint("").empty());
}

TEST_CASE("byte sizes read naturally") {
    CHECK(format_bytes(0) == "0 B");
    CHECK(format_bytes(512) == "512 B");
    CHECK(format_bytes(1024) == "1.0 KiB");
    CHECK(format_bytes(1536) == "1.5 KiB");
    CHECK(format_bytes(5ULL * 1024 * 1024) == "5.0 MiB");
    CHECK(format_bytes(3ULL * 1024 * 1024 * 1024) == "3.0 GiB");
}

TEST_CASE("transfer rows show direction and progress") {
    transfer::Progress progress;
    progress.name = "notes.txt";
    progress.size = 2048;
    progress.transferred = 1024;
    progress.direction = transfer::Direction::Incoming;

    const std::string row = transfer_row(kAlice, progress);
    CHECK(row.rfind("← ", 0) == 0);
    CHECK(row.find("notes.txt") != std::string::npos);
    CHECK(row.find("50% of 2.0 KiB") != std::string::npos);

    progress.direction = transfer::Direction::Outgoing;
    progress.outcome = transfer::Outcome::Completed;
    progress.transferred = 2048;
    const std::string done = transfer_row(kAlice, progress);
    CHECK(done.rfind("→ ", 0) == 0);
    CHECK(done.find("100% of") != std::string::npos);
    CHECK(done.find("done") != std::string::npos);

    SECTION("a transfer of unknown size shows what has arrived") {
        transfer::Progress unknown;
        unknown.name = "stream";
        unknown.transferred = 4096;
        CHECK(transfer_row(kAlice, unknown).find("4.0 KiB") != std::string::npos);
    }

    SECTION("a failure is labelled") {
        progress.outcome = transfer::Outcome::Failed;
        CHECK(transfer_row(kAlice, progress).find("failed") != std::string::npos);
    }
}

TEST_CASE("previews are cut on code points, not bytes") {
    CHECK(ellipsize("short", 10) == "short");
    CHECK(ellipsize("abcdef", 3) == "abc…");
    CHECK(ellipsize("", 5).empty());
    CHECK(ellipsize("anything", 0).empty());
    // Six two-byte code points: cutting at three must yield three characters,
    // not one and a half.
    CHECK(ellipsize("привет", 3) == "при…");
    CHECK(ellipsize("привет", 6) == "привет");
    // Invalid UTF-8 still produces something rather than an empty preview.
    CHECK_FALSE(ellipsize(std::string("\xff\xfe\xfd\xfc"), 2).empty());
}
