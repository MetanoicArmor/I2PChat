#pragma once

#include <boost/asio/awaitable.hpp>
#include <optional>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"
#include "i2pchat/sam/client.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/storage/profile_paths.hpp"

/// A profile's cryptographic identity: the I2P destination it is reachable at,
/// and the Ed25519 key it signs handshakes with.
///
/// The two are deliberately separate. The destination is the router's key and
/// lives in `{profile}.dat`; the signing key is the application's and lives in
/// the OS keyring, or in `{profile}.signing` when the keyring is unavailable.
/// Rotating one does not rotate the other, and peers pin the signing key rather
/// than the destination.
namespace i2pchat::runtime {

/// Keyring account suffix, shared with the reference implementation so a profile
/// created by either client finds the other's entry.
inline constexpr std::string_view kSigningKeyringSuffix = "__signing_seed__";
/// The profile that keeps nothing: a fresh destination and signing key every
/// run, and no trust pins.
inline constexpr std::string_view kTransientProfile = "random_address";

struct ProfileIdentity {
    std::string profile;
    /// The destination including its private key.
    std::string destination_base64;
    /// Base32 host without the `.b32.i2p` suffix — the form peers use as an id.
    std::string local_addr;
    /// The published destination, which is what a peer dials.
    std::string public_destination_base64;
    Bytes signing_seed;
    Bytes signing_public;
    /// Destination private key bytes. This is the key every sealed file in the
    /// profile is derived from, so losing it makes contacts and history
    /// unreadable.
    Bytes identity_key;

    [[nodiscard]] bool transient() const { return profile == kTransientProfile; }
};

[[nodiscard]] std::string signing_keyring_account(std::string_view profile);

/// Load the profile's signing seed, creating and storing one when absent.
///
/// The transient profile gets a fresh seed that is never written anywhere.
[[nodiscard]] Bytes load_or_create_signing_seed(const storage::ProfilePaths& paths);

/// Load the destination from `{profile}.dat`, or nothing when the profile has no
/// identity yet. A legacy plaintext `.dat` is re-encrypted in place.
[[nodiscard]] std::optional<sam::Destination> load_destination(
    const storage::ProfilePaths& paths);

/// Ask the router for a destination and store it. Only needed on first run,
/// because generating a destination requires a router but reading one does not.
[[nodiscard]] boost::asio::awaitable<sam::Destination> create_destination(
    sam::SamSession& session, const storage::ProfilePaths& paths, bool persist = true);

/// Load the whole identity, generating whatever is missing.
[[nodiscard]] boost::asio::awaitable<ProfileIdentity> load_identity(
    sam::SamSession& session, const storage::ProfilePaths& paths);

/// Assemble an identity from material already in hand, without touching the
/// filesystem or the router.
[[nodiscard]] ProfileIdentity identity_from(std::string profile,
                                            const sam::Destination& destination,
                                            ByteView signing_seed);

}  // namespace i2pchat::runtime
