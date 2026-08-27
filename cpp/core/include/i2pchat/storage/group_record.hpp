#pragma once

#include <filesystem>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/storage/profile_paths.hpp"

/// Group conversation records at rest, one file per group:
///
///   {profile}.group.{sha256(group_id)}.json
///
/// Sealed as `I2GS` under domain `I2PCHAT-GROUPSTORE`, with the group token
/// mixed into the file key. This module handles the file only; the meaning of
/// the payload — state, history, pending deliveries — belongs to the group
/// coordinator.
namespace i2pchat::storage {

/// Reads a sealed record, or a legacy plaintext one, which is re-encrypted on the
/// next save.
[[nodiscard]] nlohmann::json read_group_record(const std::filesystem::path& path,
                                               std::string_view group_id,
                                               std::optional<ByteView> identity_key);

void write_group_record(const std::filesystem::path& path, std::string_view group_id,
                        const nlohmann::json& payload,
                        std::optional<ByteView> identity_key);

/// The group ids this profile has records for, recovered by matching file names
/// against the digest of each candidate id.
///
/// The file name carries only the digest, so the ids cannot be read back from
/// disk; callers pass the ids they know about and get the subset that has a file.
[[nodiscard]] std::vector<std::string> known_group_records(
    const ProfilePaths& paths, const std::vector<std::string>& candidate_ids);

/// Paths of every group record for this profile, sorted. Useful for backup and
/// for counting records without knowing the ids.
[[nodiscard]] std::vector<std::filesystem::path> list_group_record_files(
    const ProfilePaths& paths);

}  // namespace i2pchat::storage
