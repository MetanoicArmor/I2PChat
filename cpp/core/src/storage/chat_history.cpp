#include "i2pchat/storage/chat_history.hpp"

#include <algorithm>
#include <charconv>
#include <format>
#include <system_error>

#include "i2pchat/storage/sealed_json.hpp"

namespace i2pchat::storage {
namespace {

constexpr std::uint32_t kHistoryPayloadVersion = 2;

std::string trim(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    return std::string(text.substr(first, last - first + 1));
}

std::optional<int> parse_int(std::string_view text) {
    int value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc() || result.ptr != text.data() + text.size()) {
        return std::nullopt;
    }
    return value;
}

/// Days since the Unix epoch, by Howard Hinnant's civil-from-days algorithm.
/// Doing the arithmetic ourselves keeps the result independent of the host's
/// timezone, which `std::mktime` would drag in.
std::int64_t days_from_civil(int year, int month, int day) {
    year -= month <= 2 ? 1 : 0;
    const std::int64_t era = (year >= 0 ? year : year - 399) / 400;
    const auto year_of_era = static_cast<unsigned>(year - era * 400);
    const unsigned day_of_year =
        static_cast<unsigned>((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5) +
        static_cast<unsigned>(day) - 1;
    const unsigned day_of_era =
        year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    return era * 146097 + static_cast<std::int64_t>(day_of_era) - 719468;
}

std::string text_or_empty(const nlohmann::json& object, const char* key) {
    const auto it = object.find(key);
    if (it == object.end() || !it->is_string()) {
        return "";
    }
    return it->get<std::string>();
}

/// An empty or absent string means "unset". A JSON null is treated the same way;
/// the reference reader turns it into the literal text "None", which is a bug we
/// are not obliged to reproduce because nothing on disk depends on it.
std::optional<std::string> optional_text(const nlohmann::json& object, const char* key) {
    const std::string value = text_or_empty(object, key);
    if (value.empty()) {
        return std::nullopt;
    }
    return value;
}

nlohmann::json optional_to_json(const std::optional<std::string>& value) {
    return value.has_value() ? nlohmann::json(*value) : nlohmann::json();
}

}  // namespace

std::optional<std::chrono::system_clock::time_point> parse_iso8601_utc(
    std::string_view text) {
    std::string value = trim(text);
    if (value.size() < 10) {
        return std::nullopt;
    }

    // Offset suffix, if any. A trailing 'Z' and an explicit +00:00 mean the same.
    std::int64_t offset_seconds = 0;
    if (value.back() == 'Z' || value.back() == 'z') {
        value.pop_back();
    } else {
        // Look for a sign after the date part, so the date's own dashes are not
        // mistaken for the offset.
        const auto sign = value.find_last_of("+-");
        if (sign != std::string::npos && sign > 10) {
            std::string suffix = value.substr(sign + 1);
            const int factor = value[sign] == '-' ? -1 : 1;
            suffix.erase(std::remove(suffix.begin(), suffix.end(), ':'), suffix.end());
            if (suffix.size() != 2 && suffix.size() != 4) {
                return std::nullopt;
            }
            const std::optional<int> hours = parse_int(std::string_view(suffix).substr(0, 2));
            const std::optional<int> minutes =
                suffix.size() == 4 ? parse_int(std::string_view(suffix).substr(2, 2))
                                   : std::optional<int>(0);
            if (!hours.has_value() || !minutes.has_value()) {
                return std::nullopt;
            }
            offset_seconds = factor * (*hours * 3600 + *minutes * 60);
            value.erase(sign);
        }
    }

    if (value.size() < 10 || value[4] != '-' || value[7] != '-') {
        return std::nullopt;
    }
    const std::optional<int> year = parse_int(std::string_view(value).substr(0, 4));
    const std::optional<int> month = parse_int(std::string_view(value).substr(5, 2));
    const std::optional<int> day = parse_int(std::string_view(value).substr(8, 2));
    if (!year.has_value() || !month.has_value() || !day.has_value() || *month < 1 ||
        *month > 12 || *day < 1 || *day > 31) {
        return std::nullopt;
    }

    int hour = 0;
    int minute = 0;
    int second = 0;
    if (value.size() > 10) {
        if (value[10] != 'T' && value[10] != 't' && value[10] != ' ') {
            return std::nullopt;
        }
        const std::string time = value.substr(11);
        if (time.size() < 5 || time[2] != ':') {
            return std::nullopt;
        }
        const std::optional<int> parsed_hour = parse_int(std::string_view(time).substr(0, 2));
        const std::optional<int> parsed_minute =
            parse_int(std::string_view(time).substr(3, 2));
        if (!parsed_hour.has_value() || !parsed_minute.has_value()) {
            return std::nullopt;
        }
        hour = *parsed_hour;
        minute = *parsed_minute;
        if (time.size() >= 8 && time[5] == ':') {
            const std::optional<int> parsed_second =
                parse_int(std::string_view(time).substr(6, 2));
            if (!parsed_second.has_value()) {
                return std::nullopt;
            }
            second = *parsed_second;
            // Fractional seconds are accepted and discarded: retention works in
            // days, so sub-second precision cannot change the outcome.
            if (time.size() > 8 && time[8] != '.' && time[8] != ',') {
                return std::nullopt;
            }
        } else if (time.size() != 5) {
            return std::nullopt;
        }
    }
    if (hour > 23 || minute > 59 || second > 60) {
        return std::nullopt;
    }

    const std::int64_t days = days_from_civil(*year, *month, *day);
    const std::int64_t seconds =
        days * 86400 + hour * 3600 + minute * 60 + second - offset_seconds;
    return std::chrono::system_clock::time_point(std::chrono::seconds(seconds));
}

std::string format_iso8601_utc(std::chrono::system_clock::time_point when) {
    const auto micros =
        std::chrono::duration_cast<std::chrono::microseconds>(when.time_since_epoch());
    const auto whole_seconds = std::chrono::floor<std::chrono::seconds>(micros);
    const auto fraction = (micros - whole_seconds).count();

    const std::chrono::year_month_day date{
        std::chrono::floor<std::chrono::days>(std::chrono::sys_seconds(whole_seconds))};
    const std::chrono::hh_mm_ss time{
        whole_seconds - std::chrono::floor<std::chrono::days>(whole_seconds)};

    std::string result = std::format(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}", static_cast<int>(date.year()),
        static_cast<unsigned>(date.month()), static_cast<unsigned>(date.day()),
        time.hours().count(), time.minutes().count(), time.seconds().count());
    if (fraction != 0) {
        result += std::format(".{:06}", fraction);
    }
    return result + "+00:00";
}

std::string now_iso8601_utc() {
    return format_iso8601_utc(std::chrono::system_clock::now());
}

RetentionResult apply_history_retention(
    const std::vector<HistoryEntry>& entries, const RetentionPolicy& policy,
    std::optional<std::chrono::system_clock::time_point> now) {
    RetentionResult result;
    result.retained = entries;

    if (policy.max_age_days > 0) {
        const auto reference = now.value_or(std::chrono::system_clock::now());
        const auto cutoff = reference - std::chrono::hours(24 * policy.max_age_days);

        std::vector<HistoryEntry> kept;
        kept.reserve(result.retained.size());
        std::optional<std::string> dropped_first_ts;
        for (const HistoryEntry& entry : result.retained) {
            const auto stamp = parse_iso8601_utc(entry.ts);
            // An unparseable timestamp is kept: dropping a message because its
            // clock string is odd would silently destroy history.
            if (stamp.has_value() && *stamp < cutoff) {
                if (!dropped_first_ts.has_value()) {
                    dropped_first_ts = entry.ts;
                }
                continue;
            }
            kept.push_back(entry);
        }
        if (dropped_first_ts.has_value() && !dropped_first_ts->empty()) {
            result.truncated_at = dropped_first_ts;
        }
        result.retained = std::move(kept);
    }

    if (policy.max_messages > 0 && result.retained.size() > policy.max_messages) {
        const std::size_t drop_count = result.retained.size() - policy.max_messages;
        result.truncated_at = result.retained.front().ts;
        result.retained.erase(result.retained.begin(),
                              result.retained.begin() +
                                  static_cast<std::ptrdiff_t>(drop_count));
    }
    return result;
}

nlohmann::json history_to_json(std::string_view peer_addr,
                               const std::vector<HistoryEntry>& entries,
                               const std::optional<std::string>& truncated_at) {
    nlohmann::json messages = nlohmann::json::array();
    for (const HistoryEntry& entry : entries) {
        nlohmann::json item = nlohmann::json::object();
        item["kind"] = entry.kind;
        item["text"] = entry.text;
        item["ts"] = entry.ts;
        item["message_id"] = optional_to_json(entry.message_id);
        item["delivery_state"] = optional_to_json(entry.delivery_state);
        item["delivery_route"] = optional_to_json(entry.delivery_route);
        item["delivery_hint"] = entry.delivery_hint;
        item["delivery_reason"] = entry.delivery_reason;
        item["retryable"] = entry.retryable;
        messages.push_back(std::move(item));
    }

    nlohmann::json out = nlohmann::json::object();
    out["version"] = kHistoryPayloadVersion;
    out["peer"] = history_peer_key(peer_addr);
    out["messages"] = std::move(messages);
    out["truncated_at"] = optional_to_json(truncated_at);
    return out;
}

HistoryDocument parse_history_json(const nlohmann::json& data) {
    if (!data.is_object()) {
        throw SealedJsonError("History payload must be an object");
    }
    const std::uint32_t version = data.value("version", 1U);
    if (version != 1 && version != kHistoryPayloadVersion) {
        throw SealedJsonError("Unsupported history format version " +
                              std::to_string(version));
    }

    HistoryDocument document;
    document.peer = text_or_empty(data, "peer");

    const auto messages = data.find("messages");
    if (messages != data.end() && messages->is_array()) {
        for (const nlohmann::json& item : *messages) {
            if (!item.is_object()) {
                continue;
            }
            HistoryEntry entry;
            // `peer` is the reference default for a message with no kind at all.
            entry.kind = item.contains("kind") ? text_or_empty(item, "kind") : "peer";
            entry.text = text_or_empty(item, "text");
            entry.ts = text_or_empty(item, "ts");
            entry.message_id = optional_text(item, "message_id");
            entry.delivery_state = optional_text(item, "delivery_state");
            entry.delivery_route = optional_text(item, "delivery_route");
            entry.delivery_hint = text_or_empty(item, "delivery_hint");
            entry.delivery_reason = text_or_empty(item, "delivery_reason");
            const auto retryable = item.find("retryable");
            entry.retryable = retryable != item.end() && retryable->is_boolean() &&
                              retryable->get<bool>();
            document.entries.push_back(std::move(entry));
        }
    }

    const auto truncated = data.find("truncated_at");
    if (truncated != data.end() && truncated->is_string()) {
        document.truncated_at = truncated->get<std::string>();
    }
    return document;
}

std::vector<HistoryEntry> load_history(const ProfilePaths& paths,
                                       std::string_view peer_addr,
                                       ByteView identity_key) {
    const SealedJsonFormat format = chat_history_format(peer_addr);
    for (const std::filesystem::path& path :
         {paths.chat_history(peer_addr), paths.legacy_chat_history(peer_addr)}) {
        if (!std::filesystem::is_regular_file(path)) {
            continue;
        }
        // Unlike the other sealed files there is no plaintext fallback here: a
        // history file without the magic is not history.
        if (!is_sealed_json_file(path, format.magic)) {
            continue;
        }
        try {
            const HistoryDocument document =
                parse_history_json(read_sealed_json(path, identity_key, format));
            if (history_peer_key(document.peer) != history_peer_key(peer_addr)) {
                // The file is keyed by this peer but claims another. Either the
                // name collided or the file was swapped in; refuse it either way.
                return {};
            }
            return document.entries;
        } catch (const std::exception&) {
            return {};
        }
    }
    return {};
}

void save_history(const ProfilePaths& paths, std::string_view peer_addr,
                  const std::vector<HistoryEntry>& entries, ByteView identity_key,
                  const RetentionPolicy& policy) {
    if (entries.empty()) {
        return;
    }
    const RetentionResult retained = apply_history_retention(entries, policy);
    write_sealed_json(paths.chat_history(peer_addr),
                      history_to_json(peer_addr, retained.retained, retained.truncated_at),
                      identity_key, chat_history_format(peer_addr));
}

bool delete_history(const ProfilePaths& paths, std::string_view peer_addr) {
    bool deleted = false;
    for (const std::filesystem::path& path :
         {paths.chat_history(peer_addr), paths.legacy_chat_history(peer_addr)}) {
        std::error_code error;
        if (std::filesystem::remove(path, error)) {
            deleted = true;
        }
    }
    return deleted;
}

std::vector<std::filesystem::path> list_history_files(const ProfilePaths& paths) {
    const std::string prefix = paths.profile() + ".history.";
    std::vector<std::filesystem::path> found;

    std::error_code error;
    for (const auto& entry : std::filesystem::directory_iterator(paths.data_dir(), error)) {
        const std::string name = entry.path().filename().string();
        if (name.size() > prefix.size() + 4 && name.starts_with(prefix) &&
            name.ends_with(".enc")) {
            found.push_back(entry.path());
        }
    }
    std::sort(found.begin(), found.end());
    return found;
}

}  // namespace i2pchat::storage
