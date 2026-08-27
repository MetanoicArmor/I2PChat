#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "i2pchat/bytes.hpp"

/// Cryptographic primitives, mirroring i2pchat/crypto.py exactly.
///
/// Every function here has a golden vector in cpp/testdata/vectors. Deviating
/// from the reference behaviour breaks interoperability with released 1.4.x
/// clients, so this module intentionally offers no "improved" variants.
namespace i2pchat::crypto {

inline constexpr std::size_t kHmacSize = 32;
inline constexpr std::size_t kNonceSize = 32;
inline constexpr std::size_t kKeySize = 32;
inline constexpr std::size_t kSecretBoxNonceSize = 24;
inline constexpr std::size_t kSecretBoxMacSize = 16;
inline constexpr std::size_t kEd25519SeedSize = 32;
inline constexpr std::size_t kEd25519PublicSize = 32;
inline constexpr std::size_t kEd25519SignatureSize = 64;
inline constexpr std::size_t kX25519KeySize = 32;

/// Thrown for misuse (wrong key sizes, invalid public keys). Authentication
/// failures are reported as an empty optional instead, matching the reference
/// implementation's decrypt-returns-None contract.
class CryptoError : public std::runtime_error {
public:
    explicit CryptoError(const std::string& message) : std::runtime_error(message) {}
};

/// Must be called once before any other function in this namespace.
void init();

// --- Randomness -------------------------------------------------------------

Bytes random_bytes(std::size_t size);
Bytes generate_nonce();  // 32 bytes, matching crypto.generate_nonce()
std::string random_hex(std::size_t byte_count);

/// Constant-time comparison. Returns false for differing lengths.
bool constant_time_equal(ByteView left, ByteView right);

// --- Hashing and HMAC -------------------------------------------------------

Bytes sha256(ByteView data);
Bytes hmac_sha256(ByteView key, ByteView message);

/// HKDF-SHA256 (RFC 5869). An empty salt is replaced by 32 zero bytes, exactly
/// as the reference implementation does.
Bytes hkdf_extract(ByteView salt, ByteView ikm);
Bytes hkdf_expand(ByteView prk, ByteView info, std::size_t length);

// --- Authenticated encryption (NaCl SecretBox) ------------------------------

/// XSalsa20-Poly1305. Output layout is nonce(24) || ciphertext || tag(16),
/// matching PyNaCl's SecretBox.encrypt().
Bytes encrypt_message(ByteView key, ByteView plaintext);

/// Explicit-nonce variant, used by tests replaying golden vectors.
Bytes encrypt_message_with_nonce(ByteView key, ByteView plaintext, ByteView nonce);

/// Returns nullopt when authentication fails.
std::optional<Bytes> decrypt_message(ByteView key, ByteView ciphertext);

// --- Ed25519 ----------------------------------------------------------------

struct SigningKeyPair {
    Bytes seed;        // 32 bytes
    Bytes public_key;  // 32 bytes
};

SigningKeyPair generate_signing_keypair();
Bytes get_verify_key_from_seed(ByteView seed);
Bytes sign_data(ByteView signing_seed, ByteView data);
bool verify_signature(ByteView verify_key, ByteView data, ByteView signature);

// --- X25519 -----------------------------------------------------------------

struct EphemeralKeyPair {
    Bytes private_key;  // 32 bytes
    Bytes public_key;   // 32 bytes
};

EphemeralKeyPair generate_ephemeral_keypair();

/// NaCl Box shared key: HSalsa20 applied to the X25519 scalar multiplication
/// result (crypto_box_beforenm), *not* the raw scalarmult output. Throws
/// CryptoError for a malformed, all-zero or low-order peer public key.
Bytes compute_dh_shared_secret(ByteView my_private, ByteView peer_public);

// --- Handshake (HS4) --------------------------------------------------------

struct HandshakeSubkeys {
    Bytes k_enc_i2r;
    Bytes k_mac_i2r;
    Bytes k_enc_r2i;
    Bytes k_mac_r2i;
};

/// Four independent directional keys. Directional separation is what makes a
/// reflected frame fail its MAC.
HandshakeSubkeys derive_handshake_subkeys(ByteView dh_shared, ByteView nonce_init,
                                         ByteView nonce_resp);

Bytes compute_handshake_transcript_hash(ByteView resp_sig_payload);
Bytes compute_handshake_finished(ByteView mac_key, ByteView transcript_hash);
bool verify_handshake_finished(ByteView mac_key, ByteView transcript_hash, ByteView tag);

// --- Per-frame MAC ----------------------------------------------------------

/// HMAC over msg_type || seq(8 BE) || flags(1) || msg_id(8 BE) || body. Each
/// optional field is omitted entirely when not present, matching the reference
/// implementation's argument-dependent layout.
Bytes compute_mac(ByteView key, char msg_type, ByteView body,
                  std::optional<std::uint64_t> seq = std::nullopt,
                  std::optional<std::uint64_t> msg_id = std::nullopt,
                  std::optional<std::uint8_t> flags = std::nullopt);

bool verify_mac(ByteView key, char msg_type, ByteView body, ByteView mac,
                std::optional<std::uint64_t> seq = std::nullopt,
                std::optional<std::uint64_t> msg_id = std::nullopt,
                std::optional<std::uint8_t> flags = std::nullopt);

}  // namespace i2pchat::crypto
