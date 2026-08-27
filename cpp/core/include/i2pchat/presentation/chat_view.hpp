#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/session/manager.hpp"
#include "i2pchat/session/peer_session.hpp"
#include "i2pchat/session/trust_store.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/contacts.hpp"
#include "i2pchat/transfer/manager.hpp"

/// Turning core state into text a front end can draw.
///
/// Every function here is pure: state in, strings out. The reference
/// implementation grew two copies of this — once in its Textual TUI and once in
/// its Qt window — which is why the two disagreed about things like how a
/// truncated address is written. One copy, with tests.
namespace i2pchat::presentation {

enum class LineKind {
    Incoming,
    Outgoing,
    System,
    Error,
};

/// One drawable line of a conversation.
struct ChatLine {
    LineKind kind = LineKind::System;
    /// `HH:MM:SS`, local time. Empty when the source had no usable timestamp.
    std::string time;
    /// Who to show as the author: a contact's name, a short address, or "you".
    std::string author;
    std::string text;
    /// A one- or two-character delivery marker for outgoing lines: `·` sent,
    /// `✓` delivered, `⧗` queued offline, `✗` failed. Empty otherwise.
    std::string marker;
    /// Why a delivery failed, when it did.
    std::string detail;
};

/// Shorten an address for display: the first 8 characters and an ellipsis.
///
/// Addresses are 52 base32 characters and no window is wide enough for a column
/// of them. Eight characters is 40 bits, plenty to tell apart the handful of
/// peers a user talks to, and it is only ever a label — the full address is what
/// gets compared.
[[nodiscard]] std::string short_address(std::string_view addr);

/// `HH:MM:SS` in local time from an ISO-8601 UTC timestamp. Empty when the
/// timestamp cannot be parsed, so a corrupt history entry still shows its text.
[[nodiscard]] std::string format_clock(std::string_view iso_ts);

/// The delivery marker for a state name as history stores it (`sent`,
/// `delivered`, `queued`, `failed`).
[[nodiscard]] std::string delivery_marker(std::string_view state);

/// How to label the author of a peer's message: display name when the contact
/// book has one, a short address otherwise.
[[nodiscard]] std::string author_label(const storage::ContactBook& contacts,
                                       std::string_view addr);

/// Render one stored history entry.
[[nodiscard]] ChatLine line_from_history(const storage::HistoryEntry& entry,
                                         const storage::ContactBook& contacts,
                                         std::string_view peer_addr);

/// Render a whole conversation, newest last.
[[nodiscard]] std::vector<ChatLine> lines_from_history(
    const std::vector<storage::HistoryEntry>& entries,
    const storage::ContactBook& contacts, std::string_view peer_addr);

/// One row of the contact list.
struct ContactRow {
    /// 1-based, matching what `/contact-use <index>` accepts.
    std::size_t index = 0;
    std::string addr;
    /// `name (addr…)` or just `addr…` when the contact has no name.
    std::string label;
    std::string preview;
    bool live = false;
    bool selected = false;
    /// The peer has messages waiting that the user has not looked at.
    unsigned unread = 0;
};

/// Build the contact rows in most-recently-used order.
[[nodiscard]] std::vector<ContactRow> contact_rows(
    const storage::ContactBook& contacts, const std::vector<std::string>& live_peers,
    std::string_view selected, const std::vector<std::pair<std::string, unsigned>>&
                                  unread = {});

/// A `name: value` pair for the status, router and diagnostics screens, which
/// are all lists of facts.
struct InfoRow {
    std::string name;
    std::string value;
};

struct StatusInput {
    std::string profile;
    std::string local_addr;
    session::TransportState transport = session::TransportState::Stopped;
    std::string transport_reason;
    std::vector<std::string> live_peers;
    std::string selected;
    bool blindbox_ready = false;
    std::size_t contacts = 0;
    std::size_t pending_offline = 0;
};

[[nodiscard]] std::vector<InfoRow> status_rows(const StatusInput& input);

/// The one-line header: profile, transport, selected peer.
[[nodiscard]] std::string status_line(const StatusInput& input);

/// What the TOFU dialog says.
struct TrustPromptView {
    std::string title;
    /// One paragraph explaining what is being decided and what the risk is.
    std::string body;
    /// The key to read out, grouped in fours so a user can compare it aloud.
    std::string fingerprint;
    /// The previously pinned key, for a key change. Empty on first sighting.
    std::string previous_fingerprint;
    /// True for a key change, which is the dangerous case and should not
    /// default to accepting.
    bool dangerous = false;
};

[[nodiscard]] TrustPromptView trust_prompt_view(session::TrustPrompt prompt,
                                                std::string_view peer_addr,
                                                std::string_view new_key_hex,
                                                std::string_view old_key_hex);

/// Group a hex key in fours: `a1b2 c3d4 …`. What a user reads over a phone.
[[nodiscard]] std::string group_fingerprint(std::string_view hex);

/// One row of the transfers screen.
[[nodiscard]] std::string transfer_row(std::string_view peer,
                                       const transfer::Progress& progress);

/// A size in bytes as `12.3 MiB`.
[[nodiscard]] std::string format_bytes(std::uint64_t bytes);

/// Truncate to `limit` Unicode code points, appending an ellipsis when it had
/// to cut. Code points, not bytes: a preview of Cyrillic text must not be cut
/// mid-character.
[[nodiscard]] std::string ellipsize(std::string_view text, std::size_t limit);

}  // namespace i2pchat::presentation
