#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <string>

#include "i2pchat/bytes.hpp"

/// Unsent message text, kept per conversation so it survives a restart.
///
/// Sealed as `I2CD` under domain `I2PCHAT-COMPOSE-DRAFTS`. Keys are conversation
/// ids as the UI uses them — peer addresses or group ids — and are stored
/// verbatim rather than normalised, because that is what the reference
/// implementation writes.
namespace i2pchat::storage {

inline constexpr std::uint32_t kComposeDraftsVersion = 1;

using ComposeDrafts = std::map<std::string, std::string>;

/// Returns an empty map when the file is missing or unreadable: a lost draft is
/// not a reason to fail startup.
[[nodiscard]] ComposeDrafts load_compose_drafts(const std::filesystem::path& path,
                                                std::optional<ByteView> identity_key);

void save_compose_drafts(const std::filesystem::path& path, const ComposeDrafts& drafts,
                         std::optional<ByteView> identity_key);

}  // namespace i2pchat::storage
