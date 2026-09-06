#include "i2pchat/presentation/chat_view.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <ctime>

#include "i2pchat/encoding.hpp"

namespace i2pchat::presentation {
namespace {

/// The address prefix a label shows. See `short_address`.
constexpr std::size_t kAddressPrefix = 8;
constexpr std::size_t kPreviewWidth = 40;

std::string lowered(std::string_view text) {
    std::string out;
    out.reserve(text.size());
    for (const char ch : text) {
        out.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }
    return out;
}

}  // namespace

std::string short_address(std::string_view addr) {
    if (addr.empty()) {
        return "(unknown)";
    }
    std::string host = lowered(addr);
    const std::string_view suffix = ".b32.i2p";
    if (host.size() > suffix.size() &&
        host.compare(host.size() - suffix.size(), suffix.size(), suffix) == 0) {
        host.resize(host.size() - suffix.size());
    }
    if (host.size() <= kAddressPrefix) {
        return host;
    }
    return host.substr(0, kAddressPrefix) + "…";
}

std::string format_clock(std::string_view iso_ts) {
    const auto when = storage::parse_iso8601_utc(iso_ts);
    if (!when) {
        return {};
    }
    const std::time_t seconds = std::chrono::system_clock::to_time_t(*when);
    std::tm local{};
#ifdef _WIN32
    localtime_s(&local, &seconds);
#else
    localtime_r(&seconds, &local);
#endif
    std::array<char, 16> buffer{};
    const std::size_t written =
        std::strftime(buffer.data(), buffer.size(), "%H:%M:%S", &local);
    return std::string(buffer.data(), written);
}

std::string delivery_marker(std::string_view state) {
    if (state == "delivered") {
        return "✓";
    }
    if (state == "queued") {
        return "⧗";
    }
    if (state == "failed") {
        return "✗";
    }
    if (state == "sent") {
        return "·";
    }
    return {};
}

std::string author_label(const storage::ContactBook& contacts, std::string_view addr) {
    if (const storage::ContactRecord* record = contacts.get(addr);
        record != nullptr && !record->display_name.empty()) {
        return record->display_name;
    }
    return short_address(addr);
}

ChatLine line_from_history(const storage::HistoryEntry& entry,
                           const storage::ContactBook& contacts,
                           std::string_view peer_addr) {
    ChatLine line;
    line.time = format_clock(entry.ts);
    line.text = entry.text;

    const std::string kind = lowered(entry.kind);
    // Python history uses me/peer/system; the C++ writer uses in/out/sys/err.
    if (kind == "in" || kind == "peer") {
        line.kind = LineKind::Incoming;
        line.author = author_label(contacts, peer_addr);
    } else if (kind == "out" || kind == "me") {
        line.kind = LineKind::Outgoing;
        line.author = "you";
        if (entry.delivery_state) {
            line.marker = delivery_marker(*entry.delivery_state);
        }
        if (!entry.delivery_reason.empty()) {
            line.detail = entry.delivery_reason;
        } else if (!entry.delivery_hint.empty()) {
            line.detail = entry.delivery_hint;
        }
    } else if (kind == "err" || kind == "error" || kind == "disconnect") {
        line.kind = LineKind::Error;
    } else {
        line.kind = LineKind::System;
    }
    return line;
}

std::vector<ChatLine> lines_from_history(const std::vector<storage::HistoryEntry>& entries,
                                         const storage::ContactBook& contacts,
                                         std::string_view peer_addr) {
    std::vector<ChatLine> lines;
    lines.reserve(entries.size());
    for (const storage::HistoryEntry& entry : entries) {
        lines.push_back(line_from_history(entry, contacts, peer_addr));
    }
    return lines;
}

std::vector<ContactRow> contact_rows(
    const storage::ContactBook& contacts, const std::vector<std::string>& live_peers,
    std::string_view selected,
    const std::vector<std::pair<std::string, unsigned>>& unread) {
    std::vector<ContactRow> rows;
    const std::vector<storage::ContactRecord>& records = contacts.contacts();
    rows.reserve(records.size());

    std::size_t index = 1;
    for (const storage::ContactRecord& record : records) {
        ContactRow row;
        row.index = index++;
        row.addr = record.addr;
        row.label = record.display_name.empty()
                        ? short_address(record.addr)
                        : record.display_name + " (" + short_address(record.addr) + ")";
        row.preview = ellipsize(record.last_preview, kPreviewWidth);
        row.live = std::any_of(live_peers.begin(), live_peers.end(),
                               [&](const std::string& peer) {
                                   return storage::same_i2p_destination(peer, record.addr);
                               });
        row.selected = !selected.empty() &&
                       storage::same_i2p_destination(selected, record.addr);
        const auto found = std::find_if(unread.begin(), unread.end(),
                                        [&](const auto& pair) {
                                            return storage::same_i2p_destination(
                                                pair.first, record.addr);
                                        });
        row.unread = found == unread.end() ? 0U : found->second;
        rows.push_back(std::move(row));
    }
    return rows;
}

std::vector<InfoRow> status_rows(const StatusInput& input) {
    std::vector<InfoRow> rows;
    rows.push_back({"profile", input.profile});
    rows.push_back({"address", input.local_addr.empty() ? "(not yet known)"
                                                        : input.local_addr});
    std::string transport(session::transport_state_name(input.transport));
    if (!input.transport_reason.empty()) {
        transport += " (" + input.transport_reason + ")";
    }
    rows.push_back({"transport", transport});
    rows.push_back({"connected", std::to_string(input.live_peers.size())});
    rows.push_back({"selected", input.selected.empty()
                                    ? "(none)"
                                    : short_address(input.selected)});
    rows.push_back({"contacts", std::to_string(input.contacts)});
    rows.push_back({"offline delivery", input.blindbox_ready ? "ready" : "unavailable"});
    if (input.pending_offline > 0) {
        rows.push_back({"queued offline", std::to_string(input.pending_offline)});
    }
    return rows;
}

std::string status_line(const StatusInput& input) {
    std::string line = input.profile;
    line += " · ";
    line += session::transport_state_name(input.transport);
    line += " · ";
    line += std::to_string(input.live_peers.size());
    line += input.live_peers.size() == 1 ? " peer" : " peers";
    if (!input.selected.empty()) {
        line += " · ";
        line += short_address(input.selected);
    }
    if (input.blindbox_ready) {
        line += " · offline ready";
    }
    return line;
}

std::string group_fingerprint(std::string_view hex) {
    std::string out;
    out.reserve(hex.size() + hex.size() / 4);
    for (std::size_t index = 0; index < hex.size(); ++index) {
        if (index > 0 && index % 4 == 0) {
            out.push_back(' ');
        }
        out.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(hex[index]))));
    }
    return out;
}

TrustPromptView trust_prompt_view(session::TrustPrompt prompt, std::string_view peer_addr,
                                  std::string_view new_key_hex,
                                  std::string_view old_key_hex) {
    TrustPromptView view;
    view.fingerprint = group_fingerprint(new_key_hex);
    if (prompt == session::TrustPrompt::FirstSighting) {
        view.title = "New peer: " + short_address(peer_addr);
        view.body =
            "This is the first time this peer has been seen. Accepting pins the "
            "key below, and any later change will be refused until you clear the "
            "pin. Compare it with the peer through another channel if you can.";
        view.dangerous = false;
        return view;
    }

    view.title = "Key changed: " + short_address(peer_addr);
    view.body =
        "This peer is presenting a different signing key than the one pinned "
        "earlier. That happens after a legitimate reinstall — and it is also "
        "exactly what an impersonation attempt looks like. Do not accept unless "
        "the peer confirmed the new key over another channel.";
    view.previous_fingerprint = group_fingerprint(old_key_hex);
    view.dangerous = true;
    return view;
}

std::string format_bytes(std::uint64_t bytes) {
    constexpr std::array<std::string_view, 5> units{"B", "KiB", "MiB", "GiB", "TiB"};
    if (bytes < 1024) {
        return std::to_string(bytes) + " B";
    }
    double value = static_cast<double>(bytes);
    std::size_t unit = 0;
    while (value >= 1024.0 && unit + 1 < units.size()) {
        value /= 1024.0;
        ++unit;
    }
    std::array<char, 32> buffer{};
    const int written = std::snprintf(buffer.data(), buffer.size(), "%.1f %s", value,
                                      std::string(units[unit]).c_str());
    return std::string(buffer.data(), written > 0 ? static_cast<std::size_t>(written) : 0);
}

std::string transfer_row(std::string_view peer, const transfer::Progress& progress) {
    const bool incoming = progress.direction == transfer::Direction::Incoming;
    std::string row = incoming ? "← " : "→ ";
    row += short_address(peer);
    row += "  ";
    row += progress.name.empty() ? "(unnamed)" : progress.name;
    row += "  ";
    if (progress.size > 0) {
        const auto percent = static_cast<unsigned>(
            std::llround(100.0 * static_cast<double>(progress.transferred) /
                         static_cast<double>(progress.size)));
        row += std::to_string(std::min(percent, 100U));
        row += "% of ";
        row += format_bytes(progress.size);
    } else {
        row += format_bytes(progress.transferred);
    }

    switch (progress.outcome) {
        case transfer::Outcome::Active:
            break;
        case transfer::Outcome::Completed:
            row += "  done";
            break;
        case transfer::Outcome::Failed:
            row += "  failed";
            break;
    }
    return row;
}

std::string ellipsize(std::string_view text, std::size_t limit) {
    if (limit == 0) {
        return {};
    }
    const std::optional<std::size_t> length = encoding::utf8_length(text);
    if (!length) {
        // Not valid UTF-8. Cut on a byte boundary rather than returning
        // nothing: this is a preview, and showing something beats showing
        // nothing.
        return text.size() <= limit ? std::string(text)
                                    : std::string(text.substr(0, limit)) + "…";
    }
    if (*length <= limit) {
        return std::string(text);
    }
    const std::optional<std::size_t> offset =
        encoding::utf8_offset_of_code_point(text, limit);
    if (!offset) {
        return std::string(text);
    }
    return std::string(text.substr(0, *offset)) + "…";
}

}  // namespace i2pchat::presentation
