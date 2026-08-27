#pragma once

#include <optional>
#include <string>
#include <string_view>

/// The platform half of the keyring. One translation unit per OS implements
/// these; `keyring.cpp` owns the enable switch and forwards to them.
namespace i2pchat::storage::keyring::backend {

[[nodiscard]] bool available();

[[nodiscard]] std::optional<std::string> get(std::string_view service,
                                             std::string_view account);

[[nodiscard]] bool set(std::string_view service, std::string_view account,
                       std::string_view secret);

[[nodiscard]] bool erase(std::string_view service, std::string_view account);

}  // namespace i2pchat::storage::keyring::backend
