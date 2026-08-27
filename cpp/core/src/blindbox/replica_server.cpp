#include "i2pchat/blindbox/replica_server.hpp"

#include <boost/asio/buffer.hpp>
#include <boost/asio/cancel_after.hpp>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <system_error>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "net/line_reader.hpp"

namespace i2pchat::blindbox {
namespace {

using asio::ip::tcp;

std::string trim(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

std::string to_lower(std::string_view text) {
    std::string lowered(text);
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return lowered;
}

std::vector<std::string> split_whitespace(std::string_view text) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (start < text.size()) {
        while (start < text.size() && std::isspace(static_cast<unsigned char>(text[start]))) {
            ++start;
        }
        std::size_t end = start;
        while (end < text.size() && !std::isspace(static_cast<unsigned char>(text[end]))) {
            ++end;
        }
        if (end > start) {
            parts.emplace_back(text.substr(start, end - start));
        }
        start = end;
    }
    return parts;
}

std::optional<std::uint64_t> parse_uint(std::string_view text) {
    const std::string trimmed = trim(text);
    if (trimmed.empty()) {
        return std::nullopt;
    }
    std::uint64_t value = 0;
    const auto* const begin = trimmed.data();
    const auto* const end = begin + trimmed.size();
    const auto result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{} || result.ptr != end) {
        return std::nullopt;
    }
    return value;
}

std::int64_t system_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::int64_t steady_millis() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

/// Constant-time comparison, so a wrong token cannot be found one byte at a
/// time by measuring how long the refusal took.
bool tokens_equal(std::string_view left, std::string_view right) {
    if (left.size() != right.size()) {
        return false;
    }
    unsigned char difference = 0;
    for (std::size_t i = 0; i < left.size(); ++i) {
        difference |= static_cast<unsigned char>(left[i]) ^
                      static_cast<unsigned char>(right[i]);
    }
    return difference == 0;
}

/// Printable ASCII only, without quotes or backslashes: a log line must not be
/// forgeable by a peer that puts a newline or a quote in a key.
std::string safe_text(std::string_view value) {
    std::string result;
    result.reserve(value.size());
    for (const char character : value) {
        const auto byte = static_cast<unsigned char>(character);
        const bool printable = byte >= 32 && byte < 127;
        result.push_back(printable && character != '"' && character != '\\' ? character
                                                                           : '_');
    }
    return result;
}

std::string field_text(const nlohmann::json& value) {
    if (value.is_string()) {
        return safe_text(value.get<std::string>());
    }
    return safe_text(value.dump());
}

std::string read_text_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open()) {
        return {};
    }
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

std::int64_t file_modified_seconds(const std::filesystem::path& path,
                                   std::error_code& error) {
    const auto time = std::filesystem::last_write_time(path, error);
    if (error) {
        return 0;
    }
    // No portable epoch conversion exists before C++20's clock_cast support is
    // universal, so the difference from the file clock's now is applied to the
    // system clock's now.
    const auto file_now = std::filesystem::file_time_type::clock::now();
    const auto age = std::chrono::duration_cast<std::chrono::seconds>(file_now - time);
    return system_seconds() - age.count();
}

}  // namespace

std::map<std::string, std::string> parse_dotenv(std::string_view contents) {
    std::map<std::string, std::string> values;
    std::istringstream stream{std::string(contents)};
    std::string raw;
    while (std::getline(stream, raw)) {
        const std::string line = trim(raw);
        if (line.empty() || line.front() == '#') {
            continue;
        }
        const auto separator = line.find('=');
        if (separator == std::string::npos) {
            continue;
        }
        const std::string key = trim(line.substr(0, separator));
        if (key.empty()) {
            continue;
        }
        std::string value = trim(line.substr(separator + 1));
        if (value.size() >= 2 && value.front() == value.back() &&
            (value.front() == '"' || value.front() == '\'')) {
            value = value.substr(1, value.size() - 2);
        }
        values.emplace(key, value);
    }
    return values;
}

ReplicaServerConfig config_from_environment(
    std::function<std::optional<std::string>(const std::string&)> getenv) {
    if (!getenv) {
        getenv = [](const std::string& name) -> std::optional<std::string> {
            const char* const value = std::getenv(name.c_str());
            if (value == nullptr) {
                return std::nullopt;
            }
            return std::string(value);
        };
    }

    ReplicaServerConfig config;
    const std::optional<std::string> home = getenv("HOME");
    config.base_dir = std::filesystem::path(home.value_or(".")) / ".i2pchat-blindbox";

    // A `.env` beside the binary or in the base directory fills in whatever the
    // environment does not already set, which is how the packaged daemon ships
    // its token without putting it on a command line.
    std::map<std::string, std::string> dotenv;
    for (const std::filesystem::path& candidate :
         {std::filesystem::path(".env"), config.base_dir / ".env"}) {
        for (auto& [key, value] : parse_dotenv(read_text_file(candidate))) {
            dotenv.emplace(std::move(key), std::move(value));
        }
    }

    const auto lookup = [&](const std::string& name) -> std::optional<std::string> {
        if (const std::optional<std::string> value = getenv(name)) {
            return value;
        }
        const auto found = dotenv.find(name);
        if (found == dotenv.end()) {
            return std::nullopt;
        }
        return found->second;
    };
    const auto text = [&](const std::string& name, std::string fallback) {
        const std::optional<std::string> value = lookup(name);
        return value ? trim(*value) : std::move(fallback);
    };
    const auto number = [&](const std::string& name, std::uint64_t fallback,
                            std::uint64_t minimum) {
        const std::optional<std::string> raw = lookup(name);
        if (!raw || trim(*raw).empty()) {
            return fallback;
        }
        const std::optional<std::uint64_t> value = parse_uint(*raw);
        if (!value) {
            return fallback;
        }
        return std::max(minimum, *value);
    };
    const auto flag = [&](const std::string& name, bool fallback) {
        const std::optional<std::string> raw = lookup(name);
        if (!raw) {
            return fallback;
        }
        const std::string value = to_lower(trim(*raw));
        if (value == "1" || value == "true" || value == "yes" || value == "on") {
            return true;
        }
        if (value == "0" || value == "false" || value == "no" || value == "off") {
            return false;
        }
        return fallback;
    };

    config.max_blob = static_cast<std::size_t>(number("BLINDBOX_MAX_BLOB", 1024 * 1024, 1));
    config.ttl = std::chrono::seconds(
        static_cast<std::int64_t>(number("BLINDBOX_TTL_SEC", 14 * 24 * 3600, 1)));
    config.max_files = static_cast<std::size_t>(number("BLINDBOX_MAX_FILES", 4096, 1));
    config.max_total_bytes =
        number("BLINDBOX_MAX_TOTAL_BYTES", 512ull * 1024 * 1024, 1);
    config.gc_interval = std::chrono::seconds(
        static_cast<std::int64_t>(number("BLINDBOX_GC_INTERVAL_SEC", 300, 1)));
    config.rate_limit_puts_per_minute = static_cast<std::size_t>(
        number("BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE", 240, 0));
    config.rate_limit_bytes_per_minute =
        number("BLINDBOX_RATE_LIMIT_BYTES_PER_MINUTE", 64ull * 1024 * 1024, 0);
    config.max_prefix_files =
        static_cast<std::size_t>(number("BLINDBOX_MAX_PREFIX_FILES", 256, 0));
    config.max_prefix_bytes =
        number("BLINDBOX_MAX_PREFIX_BYTES", 32ull * 1024 * 1024, 0);
    config.audit_log_max_bytes =
        number("BLINDBOX_AUDIT_LOG_MAX_BYTES", 1024 * 1024, 0);
    config.audit_log_backups =
        static_cast<int>(number("BLINDBOX_AUDIT_LOG_BACKUPS", 3, 0));
    config.auth_token = text("BLINDBOX_AUTH_TOKEN", "");
    config.admin_token = text("BLINDBOX_ADMIN_TOKEN", "");
    config.log_json = flag("BLINDBOX_LOG_JSON", true);
    config.http_status = flag("BLINDBOX_HTTP_STATUS", false);
    config.http_host = text("BLINDBOX_HTTP_HOST", "127.0.0.1");
    if (config.http_host.empty()) {
        config.http_host = "127.0.0.1";
    }
    config.http_port =
        static_cast<std::uint16_t>(number("BLINDBOX_HTTP_PORT", kDefaultStatusPort, 1));
    config.port = static_cast<std::uint16_t>(
        number("BLINDBOX_PORT", kDefaultReplicaPort, 1));
    config.host = text("BLINDBOX_HOST", "127.0.0.1");
    if (config.host.empty()) {
        config.host = "127.0.0.1";
    }
    const std::string metrics_json = text("BLINDBOX_METRICS_JSON_PATH", "");
    if (!metrics_json.empty()) {
        config.metrics_json_path = metrics_json;
    }
    const std::string metrics_prom = text("BLINDBOX_METRICS_PROM_PATH", "");
    if (!metrics_prom.empty()) {
        config.metrics_prom_path = metrics_prom;
    }
    return config;
}

ReplicaStore::ReplicaStore(ReplicaServerConfig config, std::function<std::int64_t()> now)
    : config_(std::move(config)),
      store_dir_(config_.base_dir / "store"),
      now_(std::move(now)) {
    ensure_layout();
}

std::int64_t ReplicaStore::now() const { return now_ ? now_() : system_seconds(); }

void ReplicaStore::ensure_layout() const {
    std::error_code ignored;
    std::filesystem::create_directories(store_dir_, ignored);
    std::filesystem::permissions(config_.base_dir, std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, ignored);
    std::filesystem::permissions(store_dir_, std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, ignored);
}

std::string ReplicaStore::prefix_for_key(std::string_view key) const {
    crypto::init();
    return encoding::hex_encode(ByteView(crypto::sha256(as_bytes(key)))).substr(0, 2);
}

std::filesystem::path ReplicaStore::path_for_key(std::string_view key) const {
    crypto::init();
    // The name is a hash, never the token itself: a token is chosen by the
    // client and must not be able to choose a path.
    const std::string digest =
        encoding::hex_encode(ByteView(crypto::sha256(as_bytes(key))));
    const std::filesystem::path directory = store_dir_ / digest.substr(0, 2);
    std::error_code ignored;
    std::filesystem::create_directories(directory, ignored);
    std::filesystem::permissions(directory, std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, ignored);
    return directory / digest;
}

void ReplicaStore::stamp(const std::filesystem::path& path) const {
    if (!now_) {
        return;
    }
    // Expiry is judged against this store's clock, so a blob must be dated by
    // that same clock rather than by whatever the filesystem thinks the time
    // is. Without this, a store told to run on its own clock would never expire
    // anything.
    const std::int64_t skew = system_seconds() - now();
    std::error_code ignored;
    std::filesystem::last_write_time(
        path, std::filesystem::file_time_type::clock::now() - std::chrono::seconds(skew),
        ignored);
}

std::vector<ReplicaStore::Entry> ReplicaStore::entries() const {
    std::vector<Entry> found;
    std::error_code error;
    if (!std::filesystem::exists(store_dir_, error)) {
        return found;
    }
    for (const auto& item :
         std::filesystem::recursive_directory_iterator(store_dir_, error)) {
        if (error) {
            break;
        }
        std::error_code entry_error;
        if (!item.is_regular_file(entry_error) || entry_error) {
            continue;
        }
        // Half-written temporaries are not blobs and must not be served or
        // counted against a quota.
        if (item.path().filename().string().starts_with(".")) {
            continue;
        }
        const auto size = std::filesystem::file_size(item.path(), entry_error);
        if (entry_error) {
            continue;
        }
        const std::int64_t modified = file_modified_seconds(item.path(), entry_error);
        if (entry_error) {
            continue;
        }
        found.push_back(Entry{item.path(), size, modified});
    }
    return found;
}

bool ReplicaStore::remove_if_expired(const std::filesystem::path& path) const {
    std::error_code error;
    const std::int64_t modified = file_modified_seconds(path, error);
    if (error) {
        return false;
    }
    if (now() - modified <= config_.ttl.count()) {
        return false;
    }
    return std::filesystem::remove(path, error) && !error;
}

ReplicaStore::PutOutcome ReplicaStore::put(std::string_view key, ByteView blob) {
    const std::filesystem::path path = path_for_key(key);
    std::error_code error;
    if (std::filesystem::exists(path, error) && !remove_if_expired(path)) {
        return PutOutcome::Exists;
    }
    if (!prune(blob.size())) {
        return PutOutcome::Full;
    }
    // Atomic, so a reader never sees a partially written blob and a crash
    // leaves either the old blob or none.
    storage::atomic_write_bytes(path, blob);
    stamp(path);
    return PutOutcome::Stored;
}

std::optional<Bytes> ReplicaStore::get(std::string_view key) {
    const std::filesystem::path path = path_for_key(key);
    std::error_code error;
    if (!std::filesystem::exists(path, error) || error) {
        return std::nullopt;
    }
    if (remove_if_expired(path)) {
        return std::nullopt;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open()) {
        return std::nullopt;
    }
    return Bytes(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

bool ReplicaStore::prune(std::uint64_t incoming_bytes) {
    ensure_layout();
    const std::int64_t moment = now();
    std::vector<Entry> live;
    std::uint64_t total = 0;
    for (const Entry& entry : entries()) {
        if (moment - entry.modified > config_.ttl.count()) {
            std::error_code ignored;
            std::filesystem::remove(entry.path, ignored);
            continue;
        }
        total += entry.size;
        live.push_back(entry);
    }

    if (incoming_bytes > config_.max_total_bytes) {
        return false;
    }

    // Oldest first: a replica under pressure drops the blobs least likely to
    // still be waited for.
    std::sort(live.begin(), live.end(), [](const Entry& left, const Entry& right) {
        return left.modified < right.modified;
    });
    std::size_t index = 0;
    while (index < live.size() &&
           (live.size() - index >= config_.max_files ||
            total + incoming_bytes > config_.max_total_bytes)) {
        std::error_code error;
        if (std::filesystem::remove(live[index].path, error) && !error) {
            total -= live[index].size;
        }
        ++index;
    }
    const std::size_t remaining = live.size() - index;
    return remaining < config_.max_files &&
           total + incoming_bytes <= config_.max_total_bytes;
}

ReplicaStore::Stats ReplicaStore::stats() const {
    const std::int64_t moment = now();
    Stats stats;
    for (const Entry& entry : entries()) {
        if (moment - entry.modified > config_.ttl.count()) {
            continue;
        }
        ++stats.files;
        stats.bytes += entry.size;
    }
    return stats;
}

ReplicaStore::Stats ReplicaStore::prefix_stats(std::string_view prefix) const {
    const std::filesystem::path directory = store_dir_ / std::string(prefix);
    const std::int64_t moment = now();
    Stats stats;
    for (const Entry& entry : entries()) {
        if (moment - entry.modified > config_.ttl.count()) {
            continue;
        }
        if (entry.path.parent_path() != directory) {
            continue;
        }
        ++stats.files;
        stats.bytes += entry.size;
    }
    return stats;
}

bool ReplicaStore::prefix_admits(std::string_view key, std::uint64_t size) const {
    const Stats stats = prefix_stats(prefix_for_key(key));
    if (config_.max_prefix_files > 0 && stats.files >= config_.max_prefix_files) {
        return false;
    }
    if (config_.max_prefix_bytes > 0 && stats.bytes + size > config_.max_prefix_bytes) {
        return false;
    }
    return true;
}

RateLimiter::RateLimiter(std::size_t max_operations, std::uint64_t max_bytes)
    : max_operations_(max_operations), max_bytes_(max_bytes) {}

void RateLimiter::purge(std::int64_t now_ms) {
    const std::int64_t cutoff = now_ms - 60000;
    const auto first_kept =
        std::find_if(window_.begin(), window_.end(),
                     [cutoff](const auto& item) { return item.first > cutoff; });
    window_.erase(window_.begin(), first_kept);
}

bool RateLimiter::admit(std::uint64_t size, std::int64_t now_ms) {
    purge(now_ms);
    if (max_operations_ > 0 && window_.size() >= max_operations_) {
        return false;
    }
    if (max_bytes_ > 0) {
        std::uint64_t used = 0;
        for (const auto& item : window_) {
            used += item.second;
        }
        if (used + size > max_bytes_) {
            return false;
        }
    }
    window_.emplace_back(now_ms, size);
    return true;
}

AuditLog::AuditLog(std::filesystem::path path, std::uint64_t max_bytes, int backups,
                   bool json, std::function<std::int64_t()> now)
    : path_(std::move(path)),
      max_bytes_(max_bytes),
      backups_(backups),
      json_(json),
      now_(std::move(now)) {}

std::string AuditLog::render_event(std::string_view name, const Fields& fields) const {
    const std::int64_t moment = now_ ? now_() : system_seconds();
    if (json_) {
        nlohmann::json payload = nlohmann::json::object();
        payload["ts"] = moment;
        payload["event"] = std::string(name);
        for (const auto& [key, value] : fields) {
            payload[key] = value;
        }
        // nlohmann orders object keys, which is what the reference asks for
        // with sort_keys.
        return payload.dump();
    }

    std::string line = "ts=" + std::to_string(moment) + " event=" + safe_text(name);
    for (const auto& [key, value] : fields) {
        line += " " + key + "=" + field_text(value);
    }
    return line;
}

std::string AuditLog::render_fail2ban(std::string_view reason, const Fields& fields) {
    // Fixed shape regardless of the log format: a jail regex depends on it.
    std::string line = "FAIL2BAN reason=" + safe_text(reason);
    for (const auto& [key, value] : fields) {
        line += " " + key + "=" + field_text(value);
    }
    return line;
}

void AuditLog::event(std::string_view name, const Fields& fields) {
    append(render_event(name, fields));
}

void AuditLog::fail2ban(std::string_view reason, const Fields& fields) {
    append(render_fail2ban(reason, fields));
}

void AuditLog::rotate() {
    if (path_.empty() || max_bytes_ == 0) {
        return;
    }
    std::error_code error;
    const auto size = std::filesystem::file_size(path_, error);
    if (error || size < max_bytes_) {
        return;
    }
    for (int index = backups_; index >= 1; --index) {
        const std::filesystem::path source =
            index == 1 ? path_ : std::filesystem::path(path_.string() + "." +
                                                       std::to_string(index - 1));
        const std::filesystem::path target =
            std::filesystem::path(path_.string() + "." + std::to_string(index));
        if (!std::filesystem::exists(source, error)) {
            continue;
        }
        if (index == backups_) {
            std::filesystem::remove(target, error);
        }
        std::filesystem::rename(source, target, error);
    }
}

void AuditLog::append(const std::string& line) {
    if (echo_) {
        // stderr is where a systemd unit collects it.
        std::cerr << line << "\n" << std::flush;
    }
    if (path_.empty()) {
        return;
    }

    std::error_code ignored;
    std::filesystem::create_directories(path_.parent_path(), ignored);
    std::filesystem::permissions(path_.parent_path(), std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, ignored);
    rotate();

    // Appending in place, rather than the reference's rewrite of the whole file
    // per line: the content is identical and the cost is not quadratic.
    std::ofstream out(path_, std::ios::binary | std::ios::app);
    if (!out.is_open()) {
        return;
    }
    out << line << "\n";
    out.flush();
    out.close();
    std::filesystem::permissions(
        path_, std::filesystem::perms::owner_read | std::filesystem::perms::owner_write,
        std::filesystem::perm_options::replace, ignored);
}

ReplicaService::ReplicaService(ReplicaServerConfig config, std::function<std::int64_t()> now)
    : config_(std::move(config)),
      store_(config_, now),
      rate_limiter_(config_.rate_limit_puts_per_minute,
                    config_.rate_limit_bytes_per_minute),
      audit_(config_.base_dir / "audit.log", config_.audit_log_max_bytes,
             config_.audit_log_backups, config_.log_json, now),
      now_(std::move(now)) {}

bool ReplicaService::token_ok(std::string_view provided) const {
    if (config_.auth_token.empty()) {
        return true;
    }
    return !provided.empty() && tokens_equal(provided, config_.auth_token);
}

bool ReplicaService::admin_token_ok(std::string_view provided) const {
    const std::string token = trim(provided);
    if (!config_.admin_token.empty()) {
        return !token.empty() && tokens_equal(token, config_.admin_token);
    }
    // Without a separate admin token the ordinary one governs the admin
    // commands too, and an open replica leaves them open.
    return config_.auth_token.empty() || token_ok(token);
}

void ReplicaService::count(const std::string& name) {
    ++metrics_[name];
    write_metrics_exports();
}

std::uint64_t ReplicaService::metric(const std::string& name) const {
    const auto found = metrics_.find(name);
    return found == metrics_.end() ? 0 : found->second;
}

void ReplicaService::reject(std::string_view event, std::string_view fail2ban_reason,
                            AuditLog::Fields fields) {
    audit_.event(event, fields);
    if (!fail2ban_reason.empty()) {
        audit_.fail2ban(fail2ban_reason, fields);
    }
    count(std::string(event));
}

nlohmann::json ReplicaService::status_payload() const {
    const ReplicaStore::Stats stats = store_.stats();
    nlohmann::json payload = nlohmann::json::object();
    payload["files"] = stats.files;
    payload["bytes"] = stats.bytes;
    payload["auth"] = config_.auth_token.empty() ? 0 : 1;
    payload["admin_auth"] = config_.admin_token.empty() ? 0 : 1;
    payload["ttl"] = config_.ttl.count();
    payload["max_files"] = config_.max_files;
    payload["max_total_bytes"] = config_.max_total_bytes;
    payload["puts_per_min"] = config_.rate_limit_puts_per_minute;
    payload["bytes_per_min"] = config_.rate_limit_bytes_per_minute;
    payload["max_prefix_files"] = config_.max_prefix_files;
    payload["max_prefix_bytes"] = config_.max_prefix_bytes;
    return payload;
}

std::string ReplicaService::status_line() const {
    const nlohmann::json payload = status_payload();
    std::string line = "OK";
    for (const char* key :
         {"files", "bytes", "auth", "admin_auth", "ttl", "max_files", "max_total_bytes",
          "puts_per_min", "bytes_per_min", "max_prefix_files", "max_prefix_bytes"}) {
        line += " " + std::string(key) + "=" + payload.at(key).dump();
    }
    return line;
}

std::string ReplicaService::status_json() const { return status_payload().dump(); }

std::string ReplicaService::prometheus_metrics() const {
    const nlohmann::json payload = status_payload();
    std::string text;
    const auto gauge = [&](const char* name, const nlohmann::json& value) {
        text += "# TYPE " + std::string(name) + " gauge\n";
        text += std::string(name) + " " + value.dump() + "\n";
    };
    gauge("blindbox_files", payload.at("files"));
    gauge("blindbox_bytes", payload.at("bytes"));
    gauge("blindbox_auth_enabled", payload.at("auth"));
    gauge("blindbox_ttl_seconds", payload.at("ttl"));
    gauge("blindbox_limit_files", payload.at("max_files"));
    gauge("blindbox_limit_total_bytes", payload.at("max_total_bytes"));
    gauge("blindbox_limit_prefix_files", payload.at("max_prefix_files"));
    gauge("blindbox_limit_prefix_bytes", payload.at("max_prefix_bytes"));
    for (const auto& [name, value] : metrics_) {
        text += "blindbox_events_total{event=\"" + safe_text(name) + "\"} " +
                std::to_string(value) + "\n";
    }
    return text;
}

void ReplicaService::write_metrics_exports() const {
    if (!config_.metrics_json_path.empty()) {
        nlohmann::json events = nlohmann::json::object();
        for (const auto& [name, value] : metrics_) {
            events[name] = value;
        }
        nlohmann::json document = nlohmann::json::object();
        document["status"] = status_payload();
        document["events"] = events;
        storage::atomic_write_text(config_.metrics_json_path, document.dump() + "\n");
    }
    if (!config_.metrics_prom_path.empty()) {
        storage::atomic_write_text(config_.metrics_prom_path, prometheus_metrics());
    }
}

void ReplicaService::collect_garbage() {
    store_.prune();
    write_metrics_exports();
}

ReplicaService::Plan ReplicaService::plan(std::string_view line, const PeerInfo& peer) {
    const AuditLog::Fields peer_fields{{"remote_host", peer.host},
                                       {"remote_port", peer.port}};
    const auto with_peer = [&peer](AuditLog::Fields fields) {
        fields.emplace_back("remote_host", peer.host);
        fields.emplace_back("remote_port", peer.port);
        return fields;
    };

    const std::vector<std::string> parts = split_whitespace(trim(line));
    if (parts.empty()) {
        audit_.event("request_empty", peer_fields);
        count("request_empty");
        return Plan{"ERR\n", true, std::nullopt};
    }

    const std::string& command = parts[0];

    if (command == "PING" && parts.size() == 1) {
        audit_.event("ping", peer_fields);
        count("ping");
        return Plan{config_.server_magic + "\n", false, std::nullopt};
    }

    // AUTH exists so a second instance can tell whether the replica already
    // listening on the port is one it may share.
    if (command == "AUTH" && parts.size() == 2) {
        const bool ok = config_.auth_token.empty() || token_ok(parts[1]);
        if (!ok) {
            reject("auth_fail", "BLINDBOX_AUTH_FAIL",
                   with_peer({{"command", "AUTH"}}));
        } else {
            count("auth_ok");
        }
        return Plan{ok ? "OK\n" : "ERR\n", false, std::nullopt};
    }

    if ((command == "STATUS" || command == "STATUS_JSON" || command == "METRICS") &&
        parts.size() <= 2) {
        const std::string token = parts.size() == 2 ? parts[1] : "";
        if (!admin_token_ok(token)) {
            reject("auth_fail", "BLINDBOX_AUTH_FAIL", with_peer({{"command", command}}));
            return Plan{"ERR\n", true, std::nullopt};
        }
        if (command == "STATUS") {
            audit_.event("status", with_peer({{"format", "text"}}));
            count("status");
            return Plan{status_line() + "\n", true, std::nullopt};
        }
        if (command == "STATUS_JSON") {
            audit_.event("status", with_peer({{"format", "json"}}));
            count("status_json");
            return Plan{status_json() + "\n", true, std::nullopt};
        }
        audit_.event("metrics", with_peer({{"format", "prometheus"}}));
        count("metrics");
        return Plan{prometheus_metrics(), true, std::nullopt};
    }

    if (command == "PUT" && parts.size() >= 3) {
        const std::string& key = parts[1];
        const std::optional<std::uint64_t> size = parse_uint(parts[2]);
        if (!size) {
            audit_.event("put_invalid_size", with_peer({{"key", key}}));
            count("put_invalid_size");
            return Plan{"ERR\n", true, std::nullopt};
        }
        const std::string token = parts.size() >= 4 ? parts[3] : "";
        if (!token_ok(token)) {
            reject("auth_fail", "BLINDBOX_AUTH_FAIL",
                   with_peer({{"command", "PUT"}, {"key", key}, {"size", *size}}));
            return Plan{"ERR\n", true, std::nullopt};
        }
        if (*size == 0 || *size > config_.max_blob) {
            audit_.event("put_rejected_size", with_peer({{"key", key}, {"size", *size}}));
            count("put_rejected_size");
            return Plan{"ERR\n", true, std::nullopt};
        }
        if (!rate_limiter_.admit(*size, now_ ? now_() * 1000 : steady_millis())) {
            reject("rate_limit", "BLINDBOX_RATE_LIMIT",
                   with_peer({{"command", "PUT"}, {"key", key}, {"size", *size}}));
            return Plan{"RATE\n", true, std::nullopt};
        }
        if (!store_.prefix_admits(key, *size)) {
            audit_.event("prefix_quota",
                         with_peer({{"command", "PUT"}, {"key", key}, {"size", *size}}));
            count("prefix_quota");
            return Plan{"FULL\n", true, std::nullopt};
        }
        // Only now is the body read: doing it earlier would let an
        // unauthenticated or rate-limited client make the replica buffer
        // whatever it claimed.
        return Plan{"", true, PendingPut{key, static_cast<std::size_t>(*size)}};
    }

    if (command == "GET" && parts.size() >= 2) {
        const std::string& key = parts[1];
        const std::string token = parts.size() >= 3 ? parts[2] : "";
        if (!token_ok(token)) {
            reject("auth_fail", "BLINDBOX_AUTH_FAIL",
                   with_peer({{"command", "GET"}, {"key", key}}));
            return Plan{"ERR\n", true, std::nullopt};
        }
        const std::optional<Bytes> blob = store_.get(key);
        if (!blob) {
            audit_.event("get_miss", with_peer({{"key", key}}));
            count("get_miss");
            return Plan{"MISS\n", true, std::nullopt};
        }
        audit_.event("get_ok", with_peer({{"key", key}, {"size", blob->size()}}));
        count("get_ok");
        std::string reply = "OK " + std::to_string(blob->size()) + "\n";
        reply += to_string(ByteView(*blob));
        return Plan{std::move(reply), true, std::nullopt};
    }

    audit_.event("request_invalid", with_peer({{"command", command}}));
    count("request_invalid");
    return Plan{"ERR\n", true, std::nullopt};
}

std::string ReplicaService::complete_put(const PendingPut& pending, ByteView body,
                                         const PeerInfo& peer) {
    const auto with_peer = [&peer, &pending](AuditLog::Fields fields) {
        fields.emplace_back("key", pending.key);
        fields.emplace_back("size", pending.size);
        fields.emplace_back("remote_host", peer.host);
        fields.emplace_back("remote_port", peer.port);
        return fields;
    };

    if (body.size() != pending.size) {
        audit_.event("put_truncated", with_peer({}));
        count("put_truncated");
        return "ERR\n";
    }

    switch (store_.put(pending.key, body)) {
        case ReplicaStore::PutOutcome::Exists:
            audit_.event("put_exists", with_peer({}));
            count("put_exists");
            return "EXISTS\n";
        case ReplicaStore::PutOutcome::Full:
            audit_.event("store_quota", with_peer({{"command", "PUT"}}));
            count("store_quota");
            return "FULL\n";
        case ReplicaStore::PutOutcome::Stored:
            break;
    }
    audit_.event("put_ok", with_peer({}));
    count("put_ok");
    return "OK\n";
}

std::tuple<int, std::string, std::string> ReplicaService::http_response(
    std::string_view method, std::string_view path, std::string_view bearer_token,
    const PeerInfo& peer) {
    const auto with_peer = [&peer](AuditLog::Fields fields) {
        fields.emplace_back("remote_host", peer.host);
        fields.emplace_back("remote_port", peer.port);
        return fields;
    };

    if (method != "GET") {
        audit_.event("http_method_reject",
                     with_peer({{"method", std::string(method)},
                                {"path", std::string(path)}}));
        count("http_method_reject");
        return {405, "text/plain; charset=utf-8", "method not allowed\n"};
    }
    if (!admin_token_ok(bearer_token)) {
        reject("http_auth_fail", "BLINDBOX_HTTP_AUTH_FAIL",
               with_peer({{"path", std::string(path)}}));
        return {401, "text/plain; charset=utf-8", "unauthorized\n"};
    }

    int status = 404;
    std::string content_type = "text/plain; charset=utf-8";
    std::string body = "not found\n";
    if (path == "/healthz") {
        status = 200;
        body = "ok\n";
    } else if (path == "/status.json") {
        status = 200;
        content_type = "application/json; charset=utf-8";
        body = status_json() + "\n";
    } else if (path == "/metrics") {
        status = 200;
        content_type = "text/plain; version=0.0.4; charset=utf-8";
        body = prometheus_metrics();
    }

    audit_.event("http_request",
                 with_peer({{"path", std::string(path)}, {"status", status}}));
    count("http_request");
    return {status, content_type, body};
}

namespace {

std::string http_reason(int status) {
    switch (status) {
        case 200:
            return "OK";
        case 401:
            return "Unauthorized";
        case 404:
            return "Not Found";
        case 405:
            return "Method Not Allowed";
        default:
            return "Error";
    }
}

std::string http_response_text(int status, const std::string& content_type,
                               const std::string& body) {
    return "HTTP/1.1 " + std::to_string(status) + " " + http_reason(status) +
           "\r\nContent-Type: " + content_type +
           "\r\nContent-Length: " + std::to_string(body.size()) +
           "\r\nConnection: close\r\n\r\n" + body;
}

}  // namespace

struct ReplicaServer::Impl {
    Impl(asio::any_io_executor exec, std::shared_ptr<ReplicaService> svc)
        : executor(std::move(exec)), service(std::move(svc)) {}

    asio::any_io_executor executor;
    std::shared_ptr<ReplicaService> service;
    std::optional<tcp::acceptor> acceptor;
    std::optional<tcp::acceptor> status_acceptor;
    std::optional<asio::steady_timer> gc_timer;
    bool stopped = false;

    static PeerInfo peer_of(const tcp::socket& socket) {
        boost::system::error_code error;
        const tcp::endpoint endpoint = socket.remote_endpoint(error);
        if (error) {
            return PeerInfo{};
        }
        return PeerInfo{endpoint.address().to_string(), endpoint.port()};
    }

    asio::awaitable<void> accept_loop() {
        while (acceptor && acceptor->is_open()) {
            boost::system::error_code error;
            tcp::socket socket = co_await acceptor->async_accept(
                asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                co_return;
            }
            asio::co_spawn(executor, serve(std::make_shared<tcp::socket>(std::move(socket))),
                           asio::detached);
        }
    }

    asio::awaitable<void> status_accept_loop() {
        while (status_acceptor && status_acceptor->is_open()) {
            boost::system::error_code error;
            tcp::socket socket = co_await status_acceptor->async_accept(
                asio::redirect_error(asio::use_awaitable, error));
            if (error) {
                co_return;
            }
            asio::co_spawn(executor,
                           serve_status(std::make_shared<tcp::socket>(std::move(socket))),
                           asio::detached);
        }
    }

    asio::awaitable<void> serve(std::shared_ptr<tcp::socket> socket) {
        const PeerInfo peer = peer_of(*socket);
        net::LineReader reader(*socket, {}, std::chrono::milliseconds(30000));
        boost::system::error_code ignored;
        try {
            while (true) {
                const std::optional<std::string> line = co_await reader.read_line();
                if (!line) {
                    break;
                }
                ReplicaService::Plan plan = service->plan(*line, peer);
                if (plan.pending_put) {
                    const std::optional<Bytes> body =
                        co_await reader.read_exactly(plan.pending_put->size);
                    const std::string reply = service->complete_put(
                        *plan.pending_put, body ? ByteView(*body) : ByteView{}, peer);
                    co_await asio::async_write(*socket, asio::buffer(reply),
                                               asio::use_awaitable);
                    break;
                }
                if (!plan.reply.empty()) {
                    co_await asio::async_write(*socket, asio::buffer(plan.reply),
                                               asio::use_awaitable);
                }
                if (plan.close_after) {
                    break;
                }
            }
        } catch (const std::exception&) {
            // A client that vanishes mid-command is ordinary, not an error
            // worth logging: the audit log already carries what it asked for.
        }
        socket->shutdown(tcp::socket::shutdown_both, ignored);
        socket->close(ignored);
    }

    asio::awaitable<void> serve_status(std::shared_ptr<tcp::socket> socket) {
        const PeerInfo peer = peer_of(*socket);
        net::LineReader reader(*socket, {}, std::chrono::milliseconds(10000));
        boost::system::error_code ignored;
        try {
            const std::optional<std::string> request = co_await reader.read_line();
            if (request && !request->empty()) {
                const std::vector<std::string> parts = split_whitespace(*request);
                std::string bearer;
                // Headers up to the blank line, for the bearer token only.
                while (true) {
                    const std::optional<std::string> header = co_await reader.read_line();
                    if (!header || header->empty()) {
                        break;
                    }
                    const auto colon = header->find(':');
                    if (colon == std::string::npos) {
                        continue;
                    }
                    if (to_lower(trim(header->substr(0, colon))) == "authorization") {
                        const std::string value = trim(header->substr(colon + 1));
                        if (to_lower(value).starts_with("bearer ")) {
                            bearer = trim(value.substr(7));
                        }
                    }
                }

                std::string reply;
                if (parts.size() != 3) {
                    reply = http_response_text(405, "text/plain; charset=utf-8",
                                               "bad request\n");
                } else {
                    const auto [status, content_type, body] =
                        service->http_response(parts[0], parts[1], bearer, peer);
                    reply = http_response_text(status, content_type, body);
                }
                co_await asio::async_write(*socket, asio::buffer(reply),
                                           asio::use_awaitable);
            }
        } catch (const std::exception&) {
        }
        socket->shutdown(tcp::socket::shutdown_both, ignored);
        socket->close(ignored);
    }

    asio::awaitable<void> gc_loop() {
        while (!stopped) {
            gc_timer->expires_after(service->config().gc_interval);
            boost::system::error_code error;
            co_await gc_timer->async_wait(asio::redirect_error(asio::use_awaitable, error));
            if (error || stopped) {
                co_return;
            }
            try {
                service->collect_garbage();
            } catch (const std::exception&) {
                // A transient filesystem error must not take the replica down.
            }
        }
    }
};

ReplicaServer::ReplicaServer(asio::any_io_executor executor,
                             std::shared_ptr<ReplicaService> service)
    : impl_(std::make_unique<Impl>(std::move(executor), std::move(service))) {}

ReplicaServer::~ReplicaServer() { stop(); }

void ReplicaServer::start() {
    const ReplicaServerConfig& config = impl_->service->config();
    impl_->acceptor.emplace(
        impl_->executor,
        tcp::endpoint(asio::ip::make_address(config.host), config.port));
    impl_->acceptor->listen();
    asio::co_spawn(impl_->executor, impl_->accept_loop(), asio::detached);

    if (config.http_status) {
        impl_->status_acceptor.emplace(
            impl_->executor,
            tcp::endpoint(asio::ip::make_address(config.http_host), config.http_port));
        impl_->status_acceptor->listen();
        asio::co_spawn(impl_->executor, impl_->status_accept_loop(), asio::detached);
    }

    impl_->gc_timer.emplace(impl_->executor);
    asio::co_spawn(impl_->executor, impl_->gc_loop(), asio::detached);
}

void ReplicaServer::stop() {
    if (impl_->stopped) {
        return;
    }
    impl_->stopped = true;
    boost::system::error_code ignored;
    if (impl_->acceptor) {
        impl_->acceptor->close(ignored);
    }
    if (impl_->status_acceptor) {
        impl_->status_acceptor->close(ignored);
    }
    if (impl_->gc_timer) {
        impl_->gc_timer->cancel();
    }
}

std::uint16_t ReplicaServer::port() const {
    if (!impl_->acceptor) {
        return 0;
    }
    boost::system::error_code error;
    const tcp::endpoint endpoint = impl_->acceptor->local_endpoint(error);
    return error ? 0 : endpoint.port();
}

std::uint16_t ReplicaServer::status_port() const {
    if (!impl_->status_acceptor) {
        return 0;
    }
    boost::system::error_code error;
    const tcp::endpoint endpoint = impl_->status_acceptor->local_endpoint(error);
    return error ? 0 : endpoint.port();
}

asio::awaitable<bool> probe_replica(asio::any_io_executor executor, std::string host,
                                    std::uint16_t port, std::string auth_token,
                                    std::chrono::milliseconds timeout) {
    tcp::socket socket(executor);
    boost::system::error_code error;
    const auto address = asio::ip::make_address(host, error);
    if (error) {
        co_return false;
    }

    co_await socket.async_connect(
        tcp::endpoint(address, port),
        asio::cancel_after(timeout, asio::redirect_error(asio::use_awaitable, error)));
    if (error) {
        co_return false;
    }

    net::LineReader reader(socket, {}, timeout);
    co_await asio::async_write(socket, asio::buffer(std::string("PING\n")),
                               asio::redirect_error(asio::use_awaitable, error));
    if (error) {
        co_return false;
    }
    const std::optional<std::string> pong = co_await reader.read_line();
    if (!pong || (trim(*pong) != kDaemonMagic && trim(*pong) != kLocalReplicaMagic)) {
        co_return false;
    }

    const std::string token = trim(auth_token);
    if (token.empty()) {
        co_return true;
    }
    co_await asio::async_write(socket, asio::buffer("AUTH " + token + "\n"),
                               asio::redirect_error(asio::use_awaitable, error));
    if (error) {
        co_return false;
    }
    const std::optional<std::string> reply = co_await reader.read_line();
    co_return reply && trim(*reply) == "OK";
}

}  // namespace i2pchat::blindbox
