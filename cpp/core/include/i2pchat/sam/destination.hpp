#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

namespace i2pchat::sam {

/// Signature types understood by SAM. Ed25519 is the default for new
/// identities.
enum class SigType : int {
    EcdsaSha256P256 = 1,
    EcdsaSha384P384 = 2,
    EcdsaSha512P521 = 3,
    EddsaSha512Ed25519 = 7,
};

inline constexpr SigType kDefaultSigType = SigType::EddsaSha512Ed25519;

/// Offset of the big-endian uint16 certificate length inside a destination.
inline constexpr std::size_t kCertLenOffset = 385;
/// Public destination length with a zero-length certificate.
inline constexpr std::size_t kPublicPrefixLen = 387;

class DestinationError : public std::runtime_error {
public:
    explicit DestinationError(const std::string& message)
        : std::runtime_error(message) {}
};

/// An I2P destination, optionally carrying private key material.
///
/// A private blob is `public || private`, where the public part is
/// `387 + cert_len` bytes. Getting that boundary wrong would splice private key
/// bytes into the published address, so the constructor validates it.
class Destination {
public:
    /// Parse a public destination from its I2P-base64 form.
    static Destination from_public_base64(std::string_view base64);

    /// Parse a destination that includes private key material.
    static Destination from_private_blob(ByteView blob);

    /// Parse a destination that includes private key material, I2P-base64
    /// encoded (the form stored in a profile's .dat file).
    static Destination from_private_base64(std::string_view base64);

    [[nodiscard]] const Bytes& data() const noexcept { return data_; }
    [[nodiscard]] const std::string& base64() const noexcept { return base64_; }

    /// The .b32.i2p host without its suffix: base32(sha256(data))[:52] lowered.
    [[nodiscard]] std::string base32() const;

    [[nodiscard]] bool has_private_key() const noexcept {
        return private_key_.has_value();
    }
    [[nodiscard]] const Bytes& private_key() const;
    [[nodiscard]] std::string private_key_base64() const;

private:
    Destination() = default;

    Bytes data_;                        // public destination bytes
    std::string base64_;                // I2P-base64 of data_
    std::optional<Bytes> private_key_;  // full private blob when present
};

/// Canonical peer id: the lowercase base32 host with any `.b32.i2p` suffix
/// stripped. Tolerates surrounding text, as pasted addresses often carry it.
/// Returns an empty string when no address is found.
std::string normalize_peer_address(std::string_view raw);

}  // namespace i2pchat::sam
