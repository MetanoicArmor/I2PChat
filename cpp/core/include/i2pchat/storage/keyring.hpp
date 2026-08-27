#pragma once

#include <optional>
#include <string>
#include <string_view>

namespace i2pchat::storage {

/// Keyring service name, shared with the Python implementation so that a
/// profile created by either client finds the other's entry.
inline constexpr std::string_view kKeyringService = "i2pchat";

/// OS credential store: Keychain on macOS, libsecret on Linux, Credential
/// Manager on Windows.
///
/// Every operation is best-effort and reports failure rather than throwing: a
/// missing or locked keyring is a normal condition, and the caller falls back to
/// the `.dat.wrap` sidecar. Values are stored as text, matching what Python's
/// `keyring` module writes, which is what makes the two implementations
/// interoperable.
namespace keyring {

/// Whether a backend is compiled in, enabled, and usable on this machine.
[[nodiscard]] bool available();

/// Turn the keyring off for this process, falling back to the `.dat.wrap`
/// sidecar for everything.
///
/// Needed for more than tests: on a headless box the credential store may
/// prompt or block, and a user is entitled to keep their secrets out of it. When
/// disabled, every operation below behaves as if no backend existed.
void set_enabled(bool enabled);
[[nodiscard]] bool enabled();

/// Fetch a secret. Returns nullopt when absent, or when the keyring cannot be
/// reached — the caller cannot distinguish the two, and does not need to.
[[nodiscard]] std::optional<std::string> get(std::string_view service,
                                             std::string_view account);

/// Store a secret, replacing any existing value. False on failure.
[[nodiscard]] bool set(std::string_view service, std::string_view account,
                       std::string_view secret);

/// Remove a secret. False when absent or on failure.
[[nodiscard]] bool erase(std::string_view service, std::string_view account);

}  // namespace keyring
}  // namespace i2pchat::storage
