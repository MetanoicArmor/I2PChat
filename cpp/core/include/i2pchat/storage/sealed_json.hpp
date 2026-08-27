#pragma once

#include <filesystem>
#include <nlohmann/json.hpp>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Identity-keyed JSON blobs at rest.
///
/// On-disk layout, shared by every sealed file in I2PChat:
///
///   magic(4) | version(uint16 BE) | salt(32) | SecretBox(JSON)
///
/// The plaintext is JSON serialised with `ensure_ascii=True` and
/// `separators=(",", ":")` — compact and ASCII-only, so the bytes do not depend
/// on the writer's locale.
namespace i2pchat::storage {

inline constexpr std::uint16_t kSealedJsonVersion = 1;
inline constexpr std::size_t kSealedJsonSaltSize = 32;
inline constexpr std::size_t kSealedJsonHeaderSize = 4 + 2 + kSealedJsonSaltSize;

class SealedJsonError : public std::runtime_error {
public:
    explicit SealedJsonError(const std::string& message)
        : std::runtime_error(message) {}
};

/// Identifies one sealed file kind: its magic, its header version and its HKDF
/// domain. Grouping them keeps the three from drifting apart, which is the sort
/// of mistake that renders a user's file unreadable.
struct SealedJsonFormat {
    std::string_view magic;
    std::string_view domain;
    std::uint16_t version = kSealedJsonVersion;
    /// Appended to the file-key info as `domain|file-key|<scope>`. Used by the
    /// per-peer history and per-group store files so that two files under one
    /// identity never share a key; empty for the profile-wide files.
    std::string scope;
};

/// Two-stage HKDF: bind the key to the domain first, then to this file's salt.
///
/// The intermediate profile key means one identity key can protect many files
/// without any of them sharing a file key, so compromising one file's key does
/// not unlock the rest.
Bytes derive_sealed_profile_key(ByteView identity_key, std::string_view domain);

Bytes derive_sealed_file_key(ByteView identity_key, ByteView salt,
                             std::string_view domain, std::string_view scope = {});

/// True when the file at `path` starts with `magic`, i.e. is sealed rather than
/// legacy plaintext.
bool is_sealed_json_file(const std::filesystem::path& path, std::string_view magic);

/// Read a sealed file, or a legacy plaintext JSON file.
///
/// Plaintext is still accepted because profiles written by older versions must
/// keep opening; they are re-encrypted on the next save.
nlohmann::json read_sealed_json(const std::filesystem::path& path,
                                std::optional<ByteView> identity_key,
                                const SealedJsonFormat& format);

/// Serialise and seal, writing atomically with mode 0600.
///
/// An existing file's salt is reused so that repeated saves do not rotate it —
/// matching the reference implementation, whose on-disk salt is stable for the
/// life of the file.
///
/// Without an identity key the payload is written as plaintext, but never over
/// an already-sealed file: silently downgrading a user's encrypted data would
/// be worse than failing to save.
void write_sealed_json(const std::filesystem::path& path, const nlohmann::json& payload,
                       std::optional<ByteView> identity_key,
                       const SealedJsonFormat& format);

/// The exact plaintext bytes that get sealed. Exposed for tests and for callers
/// that need to seal a payload without touching the filesystem.
std::string serialize_sealed_payload(const nlohmann::json& payload);

}  // namespace i2pchat::storage
