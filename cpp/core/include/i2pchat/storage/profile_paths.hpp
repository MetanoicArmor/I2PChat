#pragma once

#include <filesystem>
#include <string>
#include <string_view>

#include "i2pchat/storage/sealed_json.hpp"

/// File names and sealed-file formats for one profile.
///
/// Every magic, domain and header version in one place, because a mismatch in
/// any of them makes a user's file unreadable and the failure surfaces only when
/// they try to open it.
namespace i2pchat::storage {

/// Number of hex characters in the pre-1.4 per-peer history file id.
inline constexpr std::size_t kLegacyPeerIdHexLength = 16;

/// The peer address as the history files key it: trimmed and lowercased, with
/// no further canonicalisation. Distinct from `sam::normalize_peer_address`,
/// which also strips a `.b32.i2p` suffix — doing that here would name a
/// different file than the reference implementation.
[[nodiscard]] std::string history_peer_key(std::string_view peer_addr);

/// SHA-256 of `history_peer_key`, full hex digest. Used to name the per-peer
/// history file without putting the address itself on disk.
[[nodiscard]] std::string peer_file_id(std::string_view peer_addr);

/// The pre-1.4 form: the first 16 hex characters of the same digest.
[[nodiscard]] std::string legacy_peer_file_id(std::string_view peer_addr);

/// SHA-256 of the group id, full hex digest.
[[nodiscard]] std::string group_token(std::string_view group_id);

class ProfilePaths {
public:
    ProfilePaths(std::filesystem::path data_dir, std::string profile);

    [[nodiscard]] const std::filesystem::path& data_dir() const noexcept {
        return data_dir_;
    }
    [[nodiscard]] const std::string& profile() const noexcept { return profile_; }

    [[nodiscard]] std::filesystem::path identity_dat() const;
    [[nodiscard]] std::filesystem::path identity_dat_wrap() const;
    [[nodiscard]] std::filesystem::path trust_store() const;
    [[nodiscard]] std::filesystem::path contacts() const;
    [[nodiscard]] std::filesystem::path compose_drafts() const;
    /// Plaintext JSON whose `auth` value is a separately sealed `I2RA` blob,
    /// rather than a sealed file in its own right.
    [[nodiscard]] std::filesystem::path blindbox_replicas() const;
    [[nodiscard]] std::filesystem::path chat_history(std::string_view peer_addr) const;
    [[nodiscard]] std::filesystem::path legacy_chat_history(
        std::string_view peer_addr) const;
    [[nodiscard]] std::filesystem::path group_store(std::string_view group_id) const;

private:
    std::filesystem::path data_dir_;
    std::string profile_;
};

/// The sealed-file formats. History and the group store scope their file key by
/// peer or group, so two files under one identity never share a key.
inline const SealedJsonFormat kContactsFormat{"I2CB", "I2PCHAT-CONTACTS", 1, {}};
inline const SealedJsonFormat kComposeDraftsFormat{"I2CD", "I2PCHAT-COMPOSE-DRAFTS", 1,
                                                   {}};

[[nodiscard]] SealedJsonFormat chat_history_format(std::string_view peer_addr);
[[nodiscard]] SealedJsonFormat group_store_format(std::string_view group_id);

/// The same format keyed by the token that names the file. Needed when walking
/// the directory, where the group id is not recoverable from the name.
[[nodiscard]] SealedJsonFormat group_store_format_for_token(std::string_view token);

}  // namespace i2pchat::storage
