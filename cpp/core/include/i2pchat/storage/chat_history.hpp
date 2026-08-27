#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/storage/profile_paths.hpp"

/// Per-peer chat history at rest.
///
/// One file per conversation, named after the digest of the peer address so the
/// address itself never appears on disk:
///
///   {profile}.history.{sha256(peer)}.enc
///
/// Sealed as `I2CH` version 2 under domain `I2PCHAT-HISTORY`, with the peer
/// address mixed into the file key so one conversation's key never opens
/// another's.
namespace i2pchat::storage {

inline constexpr std::size_t kDefaultMaxHistoryMessages = 1000;

struct HistoryEntry {
    /// Direction or event marker as the UI writes it, e.g. `in`, `out`, `sys`.
    std::string kind;
    std::string text;
    /// ISO-8601, UTC.
    std::string ts;
    std::optional<std::string> message_id;
    std::optional<std::string> delivery_state;
    std::optional<std::string> delivery_route;
    std::string delivery_hint;
    std::string delivery_reason;
    bool retryable = false;
};

struct RetentionPolicy {
    /// Keep at most this many of the newest messages. Zero means no limit.
    std::size_t max_messages = kDefaultMaxHistoryMessages;
    /// Drop messages older than this many days. Zero means no age limit.
    unsigned max_age_days = 0;
};

struct RetentionResult {
    std::vector<HistoryEntry> retained;
    /// Timestamp of the oldest dropped message, so the UI can show where the
    /// record begins. Absent when nothing was dropped.
    std::optional<std::string> truncated_at;
};

/// Parse an ISO-8601 timestamp as UTC. Accepts a trailing `Z` or a `+HH:MM`
/// offset, and treats a naive timestamp as UTC, matching the reference reader.
[[nodiscard]] std::optional<std::chrono::system_clock::time_point> parse_iso8601_utc(
    std::string_view text);

/// Render a timestamp the way the reference implementation's `isoformat()` does:
/// `+00:00` rather than `Z`, and microseconds only when they are non-zero.
[[nodiscard]] std::string format_iso8601_utc(std::chrono::system_clock::time_point when);
[[nodiscard]] std::string now_iso8601_utc();

/// Apply the retention policy. Age is filtered first, then the count limit, so
/// `truncated_at` reports the oldest message actually dropped.
[[nodiscard]] RetentionResult apply_history_retention(
    const std::vector<HistoryEntry>& entries, const RetentionPolicy& policy,
    std::optional<std::chrono::system_clock::time_point> now = std::nullopt);

[[nodiscard]] nlohmann::json history_to_json(std::string_view peer_addr,
                                             const std::vector<HistoryEntry>& entries,
                                             const std::optional<std::string>& truncated_at);

struct HistoryDocument {
    std::string peer;
    std::vector<HistoryEntry> entries;
    std::optional<std::string> truncated_at;
};

/// Throws `SealedJsonError` on an unsupported payload version.
[[nodiscard]] HistoryDocument parse_history_json(const nlohmann::json& data);

/// Load a conversation, checking the current and the pre-1.4 short file name.
///
/// Returns an empty list on any failure — a corrupt or unreadable history file
/// must not stop the conversation from opening.
[[nodiscard]] std::vector<HistoryEntry> load_history(const ProfilePaths& paths,
                                                    std::string_view peer_addr,
                                                    ByteView identity_key);

/// Encrypt and write atomically. An empty list is not written at all, matching
/// the reference implementation: it would otherwise create a file that says
/// nothing but reveals that the conversation exists.
void save_history(const ProfilePaths& paths, std::string_view peer_addr,
                  const std::vector<HistoryEntry>& entries, ByteView identity_key,
                  const RetentionPolicy& policy = {});

/// Remove both the current and the legacy file. Returns whether anything went.
bool delete_history(const ProfilePaths& paths, std::string_view peer_addr);

/// Absolute paths of every history file belonging to this profile, sorted.
[[nodiscard]] std::vector<std::filesystem::path> list_history_files(
    const ProfilePaths& paths);

}  // namespace i2pchat::storage
