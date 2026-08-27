#include "i2pchat/crypto.hpp"

#include <sodium.h>

#include <mutex>

#include "i2pchat/encoding.hpp"

namespace i2pchat::crypto {
namespace {

std::once_flag g_init_flag;
bool g_initialized = false;

void require_initialized() {
    if (!g_initialized) {
        init();
    }
}

void require_size(ByteView data, std::size_t expected, std::string_view what) {
    if (data.size() != expected) {
        throw CryptoError(std::string(what) + " must be " + std::to_string(expected) +
                          " bytes, got " + std::to_string(data.size()));
    }
}

/// HMAC-SHA256 accepting an arbitrary-length key, as Python's hmac does.
/// libsodium's one-shot API insists on a 32-byte key, so drive the streaming
/// API instead: its init performs the standard HMAC key handling (zero-pad, or
/// hash when longer than the block size).
Bytes hmac_sha256_impl(ByteView key, ByteView message) {
    require_initialized();
    crypto_auth_hmacsha256_state state;
    crypto_auth_hmacsha256_init(&state, key.data(), key.size());
    crypto_auth_hmacsha256_update(&state, message.data(), message.size());
    Bytes out(crypto_auth_hmacsha256_BYTES);
    crypto_auth_hmacsha256_final(&state, out.data());
    return out;
}

}  // namespace

void init() {
    std::call_once(g_init_flag, [] {
        if (sodium_init() < 0) {
            throw CryptoError("libsodium initialization failed");
        }
        g_initialized = true;
    });
    if (!g_initialized) {
        throw CryptoError("libsodium initialization failed");
    }
}

Bytes random_bytes(std::size_t size) {
    require_initialized();
    Bytes out(size);
    if (size > 0) {
        randombytes_buf(out.data(), size);
    }
    return out;
}

Bytes generate_nonce() { return random_bytes(kNonceSize); }

std::string random_hex(std::size_t byte_count) {
    return encoding::hex_encode(random_bytes(byte_count));
}

bool constant_time_equal(ByteView left, ByteView right) {
    require_initialized();
    if (left.size() != right.size()) {
        return false;
    }
    if (left.empty()) {
        return true;
    }
    return sodium_memcmp(left.data(), right.data(), left.size()) == 0;
}

Bytes sha256(ByteView data) {
    require_initialized();
    Bytes out(crypto_hash_sha256_BYTES);
    crypto_hash_sha256(out.data(), data.data(), data.size());
    return out;
}

Bytes hmac_sha256(ByteView key, ByteView message) {
    return hmac_sha256_impl(key, message);
}

Bytes hkdf_extract(ByteView salt, ByteView ikm) {
    static const Bytes kZeroSalt(32, 0);
    const ByteView effective_salt = salt.empty() ? ByteView(kZeroSalt) : salt;
    return hmac_sha256_impl(effective_salt, ikm);
}

Bytes hkdf_expand(ByteView prk, ByteView info, std::size_t length) {
    if (length == 0) {
        throw CryptoError("HKDF output length must be positive");
    }
    Bytes okm;
    okm.reserve(length);
    Bytes previous;
    unsigned counter = 1;
    while (okm.size() < length) {
        Bytes block;
        block.reserve(previous.size() + info.size() + 1);
        append(block, ByteView(previous));
        append(block, info);
        block.push_back(static_cast<Byte>(counter));
        previous = hmac_sha256_impl(prk, ByteView(block));
        append(okm, ByteView(previous));
        if (++counter > 256) {
            throw CryptoError("HKDF output too large");
        }
    }
    okm.resize(length);
    return okm;
}

Bytes encrypt_message_with_nonce(ByteView key, ByteView plaintext, ByteView nonce) {
    require_initialized();
    require_size(key, crypto_secretbox_KEYBYTES, "SecretBox key");
    require_size(nonce, crypto_secretbox_NONCEBYTES, "SecretBox nonce");

    Bytes out(nonce.size() + crypto_secretbox_MACBYTES + plaintext.size());
    std::copy(nonce.begin(), nonce.end(), out.begin());
    if (crypto_secretbox_easy(out.data() + nonce.size(), plaintext.data(),
                              plaintext.size(), nonce.data(), key.data()) != 0) {
        throw CryptoError("SecretBox encryption failed");
    }
    return out;
}

Bytes encrypt_message(ByteView key, ByteView plaintext) {
    return encrypt_message_with_nonce(key, plaintext, ByteView(random_bytes(
                                                          kSecretBoxNonceSize)));
}

std::optional<Bytes> decrypt_message(ByteView key, ByteView ciphertext) {
    require_initialized();
    require_size(key, crypto_secretbox_KEYBYTES, "SecretBox key");
    if (ciphertext.size() < crypto_secretbox_NONCEBYTES + crypto_secretbox_MACBYTES) {
        return std::nullopt;
    }
    const ByteView nonce = ciphertext.first(crypto_secretbox_NONCEBYTES);
    const ByteView sealed = ciphertext.subspan(crypto_secretbox_NONCEBYTES);
    Bytes out(sealed.size() - crypto_secretbox_MACBYTES);
    if (crypto_secretbox_open_easy(out.data(), sealed.data(), sealed.size(),
                                   nonce.data(), key.data()) != 0) {
        return std::nullopt;
    }
    return out;
}

SigningKeyPair generate_signing_keypair() {
    require_initialized();
    Bytes seed = random_bytes(kEd25519SeedSize);
    return SigningKeyPair{seed, get_verify_key_from_seed(ByteView(seed))};
}

Bytes get_verify_key_from_seed(ByteView seed) {
    require_initialized();
    // The reference implementation slices seed[:32], so accept longer input.
    if (seed.size() < kEd25519SeedSize) {
        throw CryptoError("Ed25519 seed must be at least 32 bytes");
    }
    Bytes public_key(crypto_sign_PUBLICKEYBYTES);
    Bytes secret_key(crypto_sign_SECRETKEYBYTES);
    if (crypto_sign_seed_keypair(public_key.data(), secret_key.data(), seed.data()) != 0) {
        throw CryptoError("Ed25519 key derivation failed");
    }
    sodium_memzero(secret_key.data(), secret_key.size());
    return public_key;
}

Bytes sign_data(ByteView signing_seed, ByteView data) {
    require_initialized();
    if (signing_seed.size() < kEd25519SeedSize) {
        throw CryptoError("Ed25519 seed must be at least 32 bytes");
    }
    Bytes public_key(crypto_sign_PUBLICKEYBYTES);
    Bytes secret_key(crypto_sign_SECRETKEYBYTES);
    if (crypto_sign_seed_keypair(public_key.data(), secret_key.data(),
                                 signing_seed.data()) != 0) {
        throw CryptoError("Ed25519 key derivation failed");
    }
    Bytes signature(crypto_sign_BYTES);
    const int rc = crypto_sign_detached(signature.data(), nullptr, data.data(),
                                        data.size(), secret_key.data());
    sodium_memzero(secret_key.data(), secret_key.size());
    if (rc != 0) {
        throw CryptoError("Ed25519 signing failed");
    }
    return signature;
}

bool verify_signature(ByteView verify_key, ByteView data, ByteView signature) {
    require_initialized();
    if (verify_key.size() != crypto_sign_PUBLICKEYBYTES ||
        signature.size() != crypto_sign_BYTES) {
        return false;
    }
    return crypto_sign_verify_detached(signature.data(), data.data(), data.size(),
                                       verify_key.data()) == 0;
}

EphemeralKeyPair generate_ephemeral_keypair() {
    require_initialized();
    Bytes private_key(crypto_box_SECRETKEYBYTES);
    Bytes public_key(crypto_box_PUBLICKEYBYTES);
    if (crypto_box_keypair(public_key.data(), private_key.data()) != 0) {
        throw CryptoError("X25519 key generation failed");
    }
    return EphemeralKeyPair{private_key, public_key};
}

Bytes compute_dh_shared_secret(ByteView my_private, ByteView peer_public) {
    require_initialized();
    if (peer_public.size() != kX25519KeySize) {
        throw CryptoError("peer public key must be 32 bytes");
    }
    require_size(my_private, kX25519KeySize, "X25519 private key");

    static const Bytes kZero(kX25519KeySize, 0);
    if (constant_time_equal(peer_public, ByteView(kZero))) {
        throw CryptoError("peer public key is all-zero (invalid X25519 point)");
    }

    // NaCl's Box.shared_key() is crypto_box_beforenm: HSalsa20 over the
    // scalarmult output, not the scalarmult output itself.
    Bytes shared(crypto_box_BEFORENMBYTES);
    if (crypto_box_beforenm(shared.data(), peer_public.data(), my_private.data()) != 0) {
        throw CryptoError("invalid X25519 public key");
    }
    if (constant_time_equal(ByteView(shared), ByteView(kZero))) {
        throw CryptoError("X25519 produced an all-zero shared secret");
    }
    return shared;
}

HandshakeSubkeys derive_handshake_subkeys(ByteView dh_shared, ByteView nonce_init,
                                         ByteView nonce_resp) {
    Bytes salt_input;
    append(salt_input, std::string_view("I2PCHAT-HS4-SALT|"));
    append(salt_input, nonce_init);
    append(salt_input, nonce_resp);
    const Bytes salt = sha256(ByteView(salt_input));
    const Bytes prk = hkdf_extract(ByteView(salt), dh_shared);

    const auto expand = [&prk](std::string_view info) {
        return hkdf_expand(ByteView(prk), as_bytes(info), 32);
    };
    return HandshakeSubkeys{
        expand("I2PCHAT-HS4|key|enc|i2r"),
        expand("I2PCHAT-HS4|key|mac|i2r"),
        expand("I2PCHAT-HS4|key|enc|r2i"),
        expand("I2PCHAT-HS4|key|mac|r2i"),
    };
}

Bytes compute_handshake_transcript_hash(ByteView resp_sig_payload) {
    Bytes input;
    append(input, std::string_view("I2PCHAT-HS4-TRANSCRIPT|"));
    append(input, resp_sig_payload);
    return sha256(ByteView(input));
}

Bytes compute_handshake_finished(ByteView mac_key, ByteView transcript_hash) {
    Bytes message;
    append(message, std::string_view("I2PCHAT-HS4|FINISHED|"));
    append(message, transcript_hash);
    return hmac_sha256(mac_key, ByteView(message));
}

bool verify_handshake_finished(ByteView mac_key, ByteView transcript_hash,
                              ByteView tag) {
    const Bytes expected = compute_handshake_finished(mac_key, transcript_hash);
    return constant_time_equal(ByteView(expected), tag);
}

Bytes compute_mac(ByteView key, char msg_type, ByteView body,
                  std::optional<std::uint64_t> seq,
                  std::optional<std::uint64_t> msg_id,
                  std::optional<std::uint8_t> flags) {
    Bytes input;
    input.reserve(1 + 17 + body.size());
    input.push_back(static_cast<Byte>(msg_type));
    if (seq.has_value()) {
        append_u64_be(input, *seq);
    }
    if (flags.has_value()) {
        input.push_back(*flags);
    }
    if (msg_id.has_value()) {
        append_u64_be(input, *msg_id);
    }
    append(input, body);
    return hmac_sha256(key, ByteView(input));
}

bool verify_mac(ByteView key, char msg_type, ByteView body, ByteView mac,
                std::optional<std::uint64_t> seq, std::optional<std::uint64_t> msg_id,
                std::optional<std::uint8_t> flags) {
    const Bytes expected = compute_mac(key, msg_type, body, seq, msg_id, flags);
    return constant_time_equal(ByteView(expected), mac);
}

}  // namespace i2pchat::crypto
