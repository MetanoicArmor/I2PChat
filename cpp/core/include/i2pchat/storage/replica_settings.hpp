#pragma once

#include <filesystem>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"

/// The per-profile BlindBox replica list: `{profile}.blindbox_replicas.json`.
///
/// Plaintext JSON, because the endpoint list is not a secret and users edit it.
/// The bearer tokens are: they live in `replica_auth_enc` as a base64 `I2RA`
/// blob sealed under the profile identity key. Version 1 had no tokens at all
/// and version 2 kept them in the clear; both are still read, since a profile
/// written by an older client must keep working.
namespace i2pchat::storage {

inline constexpr std::uint32_t kReplicaSettingsVersion = 3;
inline constexpr std::string_view kReplicaAuthMagic = "I2RA";
inline constexpr std::uint16_t kReplicaAuthBlobVersion = 1;
inline constexpr std::size_t kReplicaAuthSaltSize = 32;

struct ReplicaSettings {
    /// Endpoints in first-seen order, as written.
    std::vector<std::string> endpoints;
    /// Tokens for endpoints that appear in `endpoints`, keyed by the exact
    /// address string.
    std::map<std::string, std::string> auth;
    /// True when tokens were present but could not be read, which is the
    /// difference between "this profile has no tokens" and "this profile's
    /// tokens need the identity key".
    bool auth_locked = false;
};

/// Project public BlindBox replica, matching Python `DEFAULT_RELEASE_BLINDBOX_ENDPOINTS`.
inline constexpr std::string_view kDefaultReleaseBlindboxEndpoints[] = {
    "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444",
};

[[nodiscard]] std::vector<std::string> default_release_blindbox_endpoints();
[[nodiscard]] bool same_as_release_builtin_endpoints(const std::vector<std::string>& endpoints);
[[nodiscard]] bool builtin_release_replicas_disabled();

/// Trim, drop blanks, comments and duplicates, keeping first-seen order.
[[nodiscard]] std::vector<std::string> normalize_replica_endpoints(
    const std::vector<std::string>& raw);

[[nodiscard]] Bytes derive_replica_auth_key(ByteView identity_key, ByteView salt);

/// Seal the token map into the base64 `I2RA` blob form.
[[nodiscard]] std::string encrypt_replica_auth(
    const std::map<std::string, std::string>& auth, ByteView identity_key);

/// Open an `I2RA` blob. Throws `SealedJsonError` on a bad header, an unsupported
/// version or a failed decryption.
[[nodiscard]] std::map<std::string, std::string> decrypt_replica_auth(
    std::string_view blob, ByteView identity_key);

[[nodiscard]] ReplicaSettings parse_replica_settings(
    const nlohmann::json& data, std::optional<ByteView> identity_key);
[[nodiscard]] nlohmann::json replica_settings_to_json(
    const ReplicaSettings& settings, std::optional<ByteView> identity_key);

/// Read the file, returning empty settings when it is missing or unreadable —
/// a broken replica list must not stop the profile from opening.
[[nodiscard]] ReplicaSettings load_replica_settings(
    const std::filesystem::path& path, std::optional<ByteView> identity_key);

void save_replica_settings(const std::filesystem::path& path,
                           const ReplicaSettings& settings,
                           std::optional<ByteView> identity_key);

}  // namespace i2pchat::storage
