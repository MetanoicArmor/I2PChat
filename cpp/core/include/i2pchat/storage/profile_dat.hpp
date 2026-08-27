#pragma once

#include <filesystem>
#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Encrypted at-rest storage for a profile's identity `.dat`.
///
///   magic "I2PK" | version(uint16 BE) = 1 | salt(32) | SecretBox(key line)
///
/// The wrapping key is not derived from anything the file contains — it comes
/// from the OS keyring, or from a `{profile}.dat.wrap` sidecar when the keyring
/// is unavailable. A leaked `.dat` on its own is therefore useless.
namespace i2pchat::storage {

inline constexpr std::string_view kProfileDatMagic = "I2PK";
inline constexpr std::uint16_t kProfileDatVersion = 1;
inline constexpr std::size_t kProfileDatSaltSize = 32;
inline constexpr std::size_t kProfileDatHeaderSize = 4 + 2 + kProfileDatSaltSize;
inline constexpr std::string_view kDatWrapKeyringSuffix = "__dat_wrap__";
inline constexpr std::string_view kProfileDatDomain = "I2PCHAT-PROFILE-DAT";

class ProfileDatError : public std::runtime_error {
public:
    explicit ProfileDatError(const std::string& message)
        : std::runtime_error(message) {}
};

/// What a `.dat` yielded, plus whether it needs re-encrypting.
struct ProfileDatContents {
    /// The identity private key, I2P-base64 encoded.
    std::optional<std::string> private_key_base64;
    /// A peer address that older versions stored on the second line.
    std::optional<std::string> legacy_peer;
    /// True when the file was legacy plaintext, so the caller should re-write it
    /// encrypted.
    bool was_plaintext = false;
};

[[nodiscard]] bool is_encrypted_profile_dat(ByteView raw);

[[nodiscard]] std::filesystem::path profile_dat_wrap_path(
    const std::filesystem::path& profile_data_dir, std::string_view profile);

[[nodiscard]] std::string dat_wrap_keyring_account(std::string_view profile);

/// Fetch this profile's wrap key, creating one if it does not exist yet.
///
/// A sidecar is always written alongside the keyring entry: without it, copying
/// a profile directory to another machine would produce a `.dat` nobody can
/// open, and users do copy profile directories.
Bytes get_or_create_dat_wrap_key(std::string_view profile,
                                 const std::filesystem::path& profile_data_dir);

/// Fetch an existing wrap key without creating one.
std::optional<Bytes> load_dat_wrap_key(std::string_view profile,
                                       const std::filesystem::path& profile_data_dir);

Bytes derive_profile_dat_file_key(ByteView wrap_key, ByteView salt);

Bytes encrypt_profile_dat(std::string_view private_key_base64, ByteView wrap_key);

std::string decrypt_profile_dat(ByteView raw, ByteView wrap_key);

/// Decides whether a line is a peer address rather than a private key. Supplied
/// by the caller because legacy files stored the two without a marker.
using PeerAddressPredicate = std::function<bool(const std::string& line)>;

ProfileDatContents parse_plaintext_profile_dat(std::string_view text,
                                               const PeerAddressPredicate& is_peer);

ProfileDatContents read_profile_dat_file(const std::filesystem::path& path,
                                         std::string_view profile,
                                         const std::filesystem::path& profile_data_dir,
                                         const PeerAddressPredicate& is_peer = {},
                                         bool create_wrap_key = true);

void write_encrypted_profile_dat(const std::filesystem::path& path,
                                 std::string_view private_key_base64,
                                 std::string_view profile,
                                 const std::filesystem::path& profile_data_dir);

}  // namespace i2pchat::storage
