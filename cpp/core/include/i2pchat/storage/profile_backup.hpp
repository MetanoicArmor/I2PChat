#pragma once

#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>

/// Password-protected profile and history backup bundles.
///
/// Source of truth: i2pchat/storage/profile_backup.py. Layout is
/// `I2PBKP1` | version | salt(32) | SecretBox(gzip-tar of manifest + payload/).
namespace i2pchat::storage {

class BackupError : public std::runtime_error {
public:
    explicit BackupError(const std::string& message) : std::runtime_error(message) {}
};

struct BackupExportSummary {
    std::string bundle_type;
    std::string profile;
    std::size_t file_count = 0;
    std::size_t history_files = 0;
    std::size_t sidecar_files = 0;
};

struct BackupImportSummary {
    std::string bundle_type;
    std::string source_profile;
    std::string target_profile;
    std::size_t restored_files = 0;
    std::size_t history_files = 0;
    std::size_t skipped_files = 0;
};

BackupExportSummary export_profile_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view profile,
                                          std::string_view passphrase,
                                          bool include_history = true);

BackupExportSummary export_history_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view profile,
                                          std::string_view passphrase);

BackupImportSummary import_profile_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view passphrase,
                                          std::string_view requested_profile = {});

BackupImportSummary import_history_bundle(const std::filesystem::path& bundle_path,
                                          const std::filesystem::path& app_root,
                                          std::string_view target_profile,
                                          std::string_view passphrase,
                                          bool overwrite = false);

}  // namespace i2pchat::storage
