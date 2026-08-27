#pragma once

#include <filesystem>
#include <nlohmann/json.hpp>
#include <string_view>

#include "i2pchat/bytes.hpp"

namespace i2pchat::storage {

/// Write via a temporary file in the same directory, then rename.
///
/// The rename is what makes the write atomic: a crash leaves either the old
/// contents or the new ones, never a truncated file. Profile data is the user's
/// identity and message history, so a half-written file is not an acceptable
/// outcome. New files are created with mode 0600.
void atomic_write_bytes(const std::filesystem::path& path, ByteView contents);

void atomic_write_text(const std::filesystem::path& path, std::string_view contents);

/// Pretty-printed JSON, matching the reference implementation's
/// `indent=2, sort_keys=True, ensure_ascii=True`.
void atomic_write_json(const std::filesystem::path& path, const nlohmann::json& value);

/// Read a whole file. Throws std::runtime_error when it cannot be read.
Bytes read_file(const std::filesystem::path& path);

}  // namespace i2pchat::storage
