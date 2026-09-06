#include "i2pchat/storage/profile_backup.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <regex>
#include <sstream>
#include <zlib.h>
#include <sodium.h>

#include <nlohmann/json.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/profile_dat.hpp"
#include "i2pchat/storage/profile_paths.hpp"

namespace i2pchat::storage {
namespace {

constexpr std::string_view kMagic = "I2PBKP1";
constexpr std::uint8_t kVersion = 1;
constexpr std::size_t kSaltSize = 32;

const std::regex kSafeSegment{R"(^[A-Za-z0-9._-]+$)"};
const std::regex kSafeBlindbox{R"(^blindbox\.[A-Za-z0-9._-]+\.json$)"};
const std::regex kProfileName{R"(^[A-Za-z0-9._-]{1,64}$)"};

[[noreturn]] void fail(const std::string& message) { throw BackupError(message); }

bool is_valid_profile_name(std::string_view name) {
    return std::regex_match(std::string(name), kProfileName);
}

std::string require_profile_name(std::string_view name) {
    const std::string trimmed = std::string(name);
    if (!is_valid_profile_name(trimmed)) {
        fail("Invalid profile name. Allowed characters: a-z A-Z 0-9 . _ - (1..64 chars).");
    }
    return trimmed;
}

std::filesystem::path profile_data_dir(const std::filesystem::path& app_root,
                                       std::string_view profile) {
    return app_root / "profiles" / std::string(profile);
}

std::string sha256_hex(ByteView data) {
    return encoding::hex_encode(ByteView(crypto::sha256(data)));
}

Bytes derive_backup_key(std::string_view passphrase, ByteView salt) {
    if (passphrase.empty()) {
        fail("Backup passphrase is required");
    }
    crypto::init();
    Bytes key(32);
    if (crypto_pwhash_scryptsalsa208sha256_ll(
            reinterpret_cast<const unsigned char*>(passphrase.data()), passphrase.size(),
            salt.data(), salt.size(), 1ULL << 14, 8, 1, key.data(), key.size()) != 0) {
        fail("Could not derive backup key");
    }
    return key;
}

Bytes gzip_bytes(ByteView raw) {
    z_stream stream{};
    if (deflateInit2(&stream, Z_BEST_COMPRESSION, Z_DEFLATED, 15 + 16, 8,
                     Z_DEFAULT_STRATEGY) != Z_OK) {
        fail("gzip init failed");
    }
    stream.next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(raw.data()));
    stream.avail_in = static_cast<uInt>(raw.size());
    Bytes out;
    std::array<Byte, 16384> buf{};
    int rc = Z_OK;
    while (rc != Z_STREAM_END) {
        stream.next_out = buf.data();
        stream.avail_out = static_cast<uInt>(buf.size());
        rc = deflate(&stream, Z_FINISH);
        out.insert(out.end(), buf.begin(), buf.begin() + (buf.size() - stream.avail_out));
        if (rc != Z_OK && rc != Z_STREAM_END) {
            deflateEnd(&stream);
            fail("gzip compress failed");
        }
    }
    deflateEnd(&stream);
    return out;
}

Bytes gunzip_bytes(ByteView raw) {
    z_stream stream{};
    if (inflateInit2(&stream, 15 + 16) != Z_OK) {
        fail("gunzip init failed");
    }
    stream.next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(raw.data()));
    stream.avail_in = static_cast<uInt>(raw.size());
    Bytes out;
    std::array<Byte, 16384> buf{};
    int rc = Z_OK;
    while (rc != Z_STREAM_END) {
        stream.next_out = buf.data();
        stream.avail_out = static_cast<uInt>(buf.size());
        rc = inflate(&stream, Z_NO_FLUSH);
        out.insert(out.end(), buf.begin(), buf.begin() + (buf.size() - stream.avail_out));
        if (rc == Z_STREAM_END) {
            break;
        }
        if (rc != Z_OK) {
            inflateEnd(&stream);
            fail("Failed to parse backup payload: gzip inflate failed");
        }
    }
    inflateEnd(&stream);
    return out;
}

void tar_put_octal(char* dest, std::size_t width, std::uint64_t value) {
    std::snprintf(dest, width, "%0*llo", static_cast<int>(width - 1),
                  static_cast<unsigned long long>(value));
}

void append_tar_file(Bytes& tar, const std::string& name, ByteView content,
                     std::int64_t mtime) {
    if (name.size() >= 100) {
        fail("Backup member name is too long: " + name);
    }
    std::array<char, 512> header{};
    std::memcpy(header.data(), name.data(), name.size());
    std::memcpy(header.data() + 100, "0000644", 7);
    tar_put_octal(header.data() + 124, 12, content.size());
    tar_put_octal(header.data() + 136, 12, static_cast<std::uint64_t>(mtime));
    std::memset(header.data() + 148, ' ', 8);
    header[156] = '0';
    std::memcpy(header.data() + 257, "ustar", 5);
    header[262] = 0;
    std::memcpy(header.data() + 263, "00", 2);
    unsigned sum = 0;
    for (const char ch : header) {
        sum += static_cast<unsigned char>(ch);
    }
    std::snprintf(header.data() + 148, 8, "%06o", sum);
    header[154] = '\0';
    header[155] = ' ';
    tar.insert(tar.end(), header.begin(), header.end());
    tar.insert(tar.end(), content.begin(), content.end());
    const std::size_t pad = (512 - (content.size() % 512)) % 512;
    tar.resize(tar.size() + pad, 0);
}

std::string safe_member_name(std::string name) {
    std::replace(name.begin(), name.end(), '\\', '/');
    while (!name.empty() && name.front() == '/') {
        name.erase(name.begin());
    }
    if (name.empty() || name.starts_with("../") || name.find("/../") != std::string::npos) {
        fail("Unsafe bundle path: " + name);
    }
    return name;
}

bool is_safe_path_segment(std::string_view segment) {
    const std::string text(segment);
    return !text.empty() && text != "." && text != ".." && text.find('/') == std::string::npos &&
           text.find('\\') == std::string::npos && text.find('\0') == std::string::npos &&
           std::regex_match(text, kSafeSegment);
}

Bytes encrypt_payload(ByteView payload, std::string_view passphrase) {
    const Bytes salt = crypto::random_bytes(kSaltSize);
    const Bytes key = derive_backup_key(passphrase, ByteView(salt));
    const Bytes ciphertext = crypto::encrypt_message(ByteView(key), payload);
    Bytes out;
    append(out, kMagic);
    out.push_back(kVersion);
    append(out, ByteView(salt));
    append(out, ByteView(ciphertext));
    return out;
}

Bytes decrypt_payload(ByteView raw, std::string_view passphrase) {
    const std::size_t min_len = kMagic.size() + 1 + kSaltSize + 1;
    if (raw.size() < min_len) {
        fail("Backup bundle is too short");
    }
    if (!std::equal(kMagic.begin(), kMagic.end(), raw.begin())) {
        fail("Unsupported backup bundle magic");
    }
    const std::uint8_t version = raw[kMagic.size()];
    if (version != kVersion) {
        fail("Unsupported backup bundle version: " + std::to_string(version));
    }
    const ByteView salt = raw.subspan(kMagic.size() + 1, kSaltSize);
    const ByteView ciphertext = raw.subspan(kMagic.size() + 1 + kSaltSize);
    const Bytes key = derive_backup_key(passphrase, salt);
    const std::optional<Bytes> plaintext = crypto::decrypt_message(ByteView(key), ciphertext);
    if (!plaintext) {
        fail("Failed to decrypt backup bundle (wrong passphrase or corrupted data)");
    }
    return *plaintext;
}

Bytes build_tar_payload(const nlohmann::json& manifest,
                        const std::map<std::string, Bytes>& files) {
    const std::string manifest_text = manifest.dump(2, ' ', true) + "\n";
    const auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    Bytes tar;
    append_tar_file(tar, "manifest.json", as_bytes(manifest_text), now);
    for (const auto& [name, content] : files) {
        append_tar_file(tar, "payload/" + safe_member_name(name), ByteView(content), now);
    }
    tar.resize(tar.size() + 1024, 0);
    return gzip_bytes(ByteView(tar));
}

std::uint64_t parse_octal(const char* field, std::size_t width) {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < width; ++i) {
        const char ch = field[i];
        if (ch == 0 || ch == ' ') {
            break;
        }
        if (ch < '0' || ch > '7') {
            continue;
        }
        value = (value << 3) | static_cast<unsigned>(ch - '0');
    }
    return value;
}

std::pair<nlohmann::json, std::map<std::string, Bytes>> read_tar_payload(ByteView gzipped) {
    const Bytes tar = gunzip_bytes(gzipped);
    std::map<std::string, Bytes> files;
    nlohmann::json manifest;
    bool have_manifest = false;
    std::size_t offset = 0;
    while (offset + 512 <= tar.size()) {
        const char* header = reinterpret_cast<const char*>(tar.data() + offset);
        bool zero = true;
        for (int i = 0; i < 512; ++i) {
            if (header[i] != 0) {
                zero = false;
                break;
            }
        }
        if (zero) {
            break;
        }
        std::string name(header, strnlen(header, 100));
        const std::uint64_t size = parse_octal(header + 124, 12);
        offset += 512;
        if (offset + size > tar.size()) {
            fail("Failed to parse backup payload: truncated tar member");
        }
        ByteView content(tar.data() + offset, static_cast<std::size_t>(size));
        offset += static_cast<std::size_t>(size);
        offset += (512 - (size % 512)) % 512;
        if (name == "manifest.json") {
            try {
                manifest = nlohmann::json::parse(to_string(content));
            } catch (const std::exception& error) {
                fail(std::string("Failed to parse backup payload: ") + error.what());
            }
            if (!manifest.is_object()) {
                fail("Backup manifest must be a JSON object");
            }
            have_manifest = true;
            continue;
        }
        if (!name.starts_with("payload/")) {
            fail("Unexpected bundle member: " + name);
        }
        files[safe_member_name(name.substr(8))] = Bytes(content.begin(), content.end());
    }
    if (!have_manifest) {
        fail("Backup bundle manifest is unreadable");
    }
    return {manifest, files};
}

std::pair<std::string, std::string> validate_manifest_files(
    const nlohmann::json& manifest, const std::map<std::string, Bytes>& files) {
    const std::string bundle_type = manifest.value("bundle_type", std::string{});
    const std::string source_profile = manifest.value("source_profile", std::string{});
    if (bundle_type != "profile" && bundle_type != "history") {
        fail("Unsupported backup bundle type");
    }
    if (source_profile.empty()) {
        fail("Backup manifest is missing source profile");
    }
    if (!manifest.contains("entries") || !manifest["entries"].is_array()) {
        fail("Backup manifest is missing entries list");
    }
    std::vector<std::string> expected;
    for (const auto& entry : manifest["entries"]) {
        if (!entry.is_object()) {
            fail("Backup manifest entry must be an object");
        }
        const std::string name = safe_member_name(entry.value("path", std::string{}));
        expected.push_back(name);
        const auto found = files.find(name);
        if (found == files.end()) {
            fail("Backup payload is missing file: " + name);
        }
        if (static_cast<std::size_t>(entry.value("size", -1)) != found->second.size()) {
            fail("Backup payload size mismatch for " + name);
        }
        std::string expected_sha = entry.value("sha256", std::string{});
        for (char& ch : expected_sha) {
            ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        }
        if (expected_sha != sha256_hex(ByteView(found->second))) {
            fail("Backup payload checksum mismatch for " + name);
        }
    }
    if (expected.size() != files.size()) {
        fail("Backup payload has unexpected files");
    }
    return {bundle_type, source_profile};
}

std::optional<Bytes> collect_optional_file(const std::filesystem::path& path) {
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec)) {
        return std::nullopt;
    }
    return read_file(path);
}

nlohmann::json build_manifest(const std::string& bundle_type, const std::string& profile,
                              const std::map<std::string, Bytes>& files) {
    nlohmann::json entries = nlohmann::json::array();
    for (const auto& [name, content] : files) {
        std::string kind = "sidecar";
        if (name == "profile.dat") {
            kind = "profile_dat";
        } else if (name.starts_with("history/")) {
            kind = "history";
        } else if (name.starts_with("blindbox/")) {
            kind = "blindbox";
        }
        entries.push_back({{"kind", kind},
                           {"path", name},
                           {"sha256", sha256_hex(ByteView(content))},
                           {"size", content.size()}});
    }
    return {{"bundle_type", bundle_type},
            {"created_utc",
             static_cast<std::int64_t>(std::chrono::duration_cast<std::chrono::seconds>(
                                           std::chrono::system_clock::now().time_since_epoch())
                                           .count())},
            {"entries", entries},
            {"format_version", kVersion},
            {"source_profile", profile}};
}

std::tuple<std::map<std::string, Bytes>, std::size_t, std::size_t> collect_profile_files(
    const std::filesystem::path& app_root, const std::string& profile, bool include_history) {
    std::map<std::string, Bytes> files;
    std::size_t sidecars = 0;
    std::size_t history = 0;
    const auto pdir = profile_data_dir(app_root, profile);
    ProfilePaths paths(pdir, profile);
    const auto dat_path = paths.identity_dat();
    auto profile_dat = collect_optional_file(dat_path);
    if (!profile_dat) {
        fail("Profile data file not found: " + profile + ".dat");
    }
    if (is_encrypted_profile_dat(ByteView(*profile_dat))) {
        const ProfileDatContents contents =
            read_profile_dat_file(dat_path, profile, pdir, {}, false);
        if (!contents.private_key_base64) {
            fail("Failed to unwrap encrypted profile .dat for backup");
        }
        const std::string line = *contents.private_key_base64 + "\n";
        profile_dat = to_bytes(line);
    }
    files["profile.dat"] = *profile_dat;

    if (auto contacts = collect_optional_file(paths.contacts())) {
        files["contacts.json"] = *contacts;
        ++sidecars;
    }
    if (auto drafts = collect_optional_file(paths.compose_drafts())) {
        files["compose_drafts.json"] = *drafts;
        ++sidecars;
    }

    std::error_code ec;
    if (std::filesystem::is_directory(pdir, ec)) {
        const std::string prefix = profile + ".blindbox.";
        for (const auto& entry : std::filesystem::directory_iterator(pdir, ec)) {
            const std::string name = entry.path().filename().string();
            if (!(name.starts_with(prefix) && name.ends_with(".json"))) {
                continue;
            }
            files["blindbox/" + name.substr(profile.size() + 1)] = read_file(entry.path());
            ++sidecars;
        }
    }

    if (include_history) {
        const std::string prefix = profile + ".history.";
        for (const auto& path : list_history_files(paths)) {
            const std::string name = path.filename().string();
            files["history/" + name.substr(prefix.size())] = read_file(path);
            ++history;
        }
    }
    return {files, history, sidecars};
}

std::pair<std::map<std::string, Bytes>, std::size_t> collect_history_files(
    const std::filesystem::path& app_root, const std::string& profile) {
    std::map<std::string, Bytes> files;
    ProfilePaths paths(profile_data_dir(app_root, profile), profile);
    const std::string prefix = profile + ".history.";
    for (const auto& path : list_history_files(paths)) {
        const std::string name = path.filename().string();
        files["history/" + name.substr(prefix.size())] = read_file(path);
    }
    if (files.empty()) {
        fail("No saved history files found for profile '" + profile + "'");
    }
    return {files, files.size()};
}

std::string import_dat_atomic(const Bytes& profile_dat, const std::filesystem::path& app_root,
                              std::string base_name) {
    base_name = require_profile_name(base_name);
    std::filesystem::create_directories(app_root / "profiles");
    for (int idx = 0; idx <= 1000; ++idx) {
        std::string candidate = base_name;
        if (idx > 0) {
            const std::string suffix = "_" + std::to_string(idx);
            if (suffix.size() >= 64) {
                break;
            }
            candidate = base_name.substr(0, 64 - suffix.size()) + suffix;
            if (!is_valid_profile_name(candidate)) {
                continue;
            }
        }
        const auto dest = profile_data_dir(app_root, candidate) / (candidate + ".dat");
        std::error_code ec;
        std::filesystem::create_directories(dest.parent_path(), ec);
        if (std::filesystem::exists(dest, ec)) {
            continue;
        }
        atomic_write_bytes(dest, ByteView(profile_dat));
        return candidate;
    }
    fail("Cannot allocate unique profile name for '" + base_name + "'");
}

}  // namespace

BackupExportSummary export_profile_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view profile,
                                          std::string_view passphrase, bool include_history) {
    const std::string name = require_profile_name(profile);
    auto [files, history, sidecars] = collect_profile_files(app_root, name, include_history);
    const nlohmann::json manifest = build_manifest("profile", name, files);
    atomic_write_bytes(bundle_path,
                       ByteView(encrypt_payload(ByteView(build_tar_payload(manifest, files)),
                                                passphrase)));
    return {"profile", name, files.size(), history, sidecars};
}

BackupExportSummary export_history_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view profile,
                                          std::string_view passphrase) {
    const std::string name = require_profile_name(profile);
    auto [files, history] = collect_history_files(app_root, name);
    const nlohmann::json manifest = build_manifest("history", name, files);
    atomic_write_bytes(bundle_path,
                       ByteView(encrypt_payload(ByteView(build_tar_payload(manifest, files)),
                                                passphrase)));
    return {"history", name, files.size(), history, 0};
}

BackupImportSummary import_profile_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view passphrase,
                                          std::string_view requested_profile) {
    const Bytes raw = read_file(bundle_path);
    auto [manifest, files] = read_tar_payload(ByteView(decrypt_payload(ByteView(raw), passphrase)));
    auto [bundle_type, source_profile] = validate_manifest_files(manifest, files);
    if (bundle_type != "profile") {
        fail("This backup bundle does not contain a full profile export");
    }
    const auto found = files.find("profile.dat");
    if (found == files.end()) {
        fail("Profile bundle is missing profile.dat");
    }
    const std::string base =
        require_profile_name(requested_profile.empty() ? source_profile : std::string(requested_profile));
    const std::string target = import_dat_atomic(found->second, app_root, base);
    const auto pdir = profile_data_dir(app_root, target);
    std::size_t restored = 1;
    std::size_t history = 0;
    for (const auto& [logical, content] : files) {
        if (logical == "profile.dat") {
            continue;
        }
        std::string dest_name;
        if (logical == "contacts.json") {
            dest_name = target + ".contacts.json";
        } else if (logical == "compose_drafts.json") {
            dest_name = target + ".compose_drafts.json";
        } else if (logical.starts_with("blindbox/")) {
            const std::string suffix = logical.substr(9);
            if (!std::regex_match(suffix, kSafeBlindbox)) {
                fail("Unexpected blindbox entry in profile bundle: " + logical);
            }
            dest_name = target + "." + suffix;
        } else if (logical.starts_with("history/")) {
            const std::string suffix = logical.substr(8);
            if (!is_safe_path_segment(suffix)) {
                fail("Unsafe history entry in profile bundle: " + logical);
            }
            dest_name = target + ".history." + suffix;
            ++history;
        } else {
            fail("Unexpected file in profile bundle: " + logical);
        }
        atomic_write_bytes(pdir / dest_name, ByteView(content));
        ++restored;
    }
    return {"profile", source_profile, target, restored, history, 0};
}

BackupImportSummary import_history_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view target_profile,
                                          std::string_view passphrase, bool overwrite) {
    const std::string target = require_profile_name(target_profile);
    const Bytes raw = read_file(bundle_path);
    auto [manifest, files] = read_tar_payload(ByteView(decrypt_payload(ByteView(raw), passphrase)));
    auto [bundle_type, source_profile] = validate_manifest_files(manifest, files);
    if (bundle_type != "history") {
        fail("This backup bundle does not contain a history export");
    }
    const auto pdir = profile_data_dir(app_root, target);
    std::filesystem::create_directories(pdir);
    std::size_t restored = 0;
    std::size_t skipped = 0;
    std::size_t history = 0;
    for (const auto& [logical, content] : files) {
        if (!logical.starts_with("history/")) {
            fail("Unexpected file in history bundle: " + logical);
        }
        const std::string suffix = logical.substr(8);
        const auto dest = pdir / (target + ".history." + suffix);
        ++history;
        std::error_code ec;
        if (std::filesystem::exists(dest, ec) && !overwrite) {
            ++skipped;
            continue;
        }
        atomic_write_bytes(dest, ByteView(content));
        ++restored;
    }
    return {"history", source_profile, target, restored, history, skipped};
}

}  // namespace i2pchat::storage
