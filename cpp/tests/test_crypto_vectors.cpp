#include <catch2/catch_test_macros.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::hex_of;
using i2pchat::testing::load_vector;

TEST_CASE("HKDF-SHA256 matches the reference implementation", "[crypto][vectors]") {
    crypto::init();
    const auto document = load_vector("crypto_hkdf");

    for (const auto& entry : document.at("hkdf")) {
        const Bytes salt = hex_field(entry, "salt_hex");
        const Bytes ikm = hex_field(entry, "ikm_hex");
        const Bytes info = hex_field(entry, "info_hex");
        const auto length = entry.at("length").get<std::size_t>();

        const Bytes prk = crypto::hkdf_extract(ByteView(salt), ByteView(ikm));
        CHECK(hex_of(ByteView(prk)) == entry.at("prk_hex").get<std::string>());

        const Bytes okm = crypto::hkdf_expand(ByteView(prk), ByteView(info), length);
        CHECK(hex_of(ByteView(okm)) == entry.at("okm_hex").get<std::string>());
        CHECK(okm.size() == length);
    }
}

TEST_CASE("HMAC-SHA256 and SHA-256 match the reference implementation",
          "[crypto][vectors]") {
    crypto::init();
    const auto document = load_vector("crypto_hkdf");

    for (const auto& entry : document.at("hmac_sha256")) {
        const Bytes key = hex_field(entry, "key_hex");
        const Bytes message = hex_field(entry, "message_hex");

        const Bytes mac = crypto::hmac_sha256(ByteView(key), ByteView(message));
        CHECK(hex_of(ByteView(mac)) == entry.at("hmac_sha256_hex").get<std::string>());

        const Bytes digest = crypto::sha256(ByteView(message));
        CHECK(hex_of(ByteView(digest)) == entry.at("sha256_hex").get<std::string>());
    }
}

TEST_CASE("HS4 handshake reproduces the reference transcript", "[crypto][handshake][vectors]") {
    crypto::init();
    const auto document = load_vector("crypto_handshake");
    const auto& parties = document.at("parties");

    const Bytes init_eph_priv = hex_field(parties, "initiator_eph_priv_hex");
    const Bytes init_eph_pub = hex_field(parties, "initiator_eph_pub_hex");
    const Bytes resp_eph_priv = hex_field(parties, "responder_eph_priv_hex");
    const Bytes resp_eph_pub = hex_field(parties, "responder_eph_pub_hex");
    const Bytes nonce_init = hex_field(parties, "nonce_init_hex");
    const Bytes nonce_resp = hex_field(parties, "nonce_resp_hex");
    const Bytes init_seed = hex_field(parties, "initiator_signing_seed_hex");
    const Bytes resp_seed = hex_field(parties, "responder_signing_seed_hex");

    SECTION("Ed25519 public keys derive from the seeds") {
        CHECK(hex_of(ByteView(crypto::get_verify_key_from_seed(ByteView(init_seed)))) ==
              parties.at("initiator_signing_pub_hex").get<std::string>());
        CHECK(hex_of(ByteView(crypto::get_verify_key_from_seed(ByteView(resp_seed)))) ==
              parties.at("responder_signing_pub_hex").get<std::string>());
    }

    SECTION("X25519 agreement uses the NaCl Box shared key, not raw scalarmult") {
        const Bytes from_init =
            crypto::compute_dh_shared_secret(ByteView(init_eph_priv), ByteView(resp_eph_pub));
        const Bytes from_resp =
            crypto::compute_dh_shared_secret(ByteView(resp_eph_priv), ByteView(init_eph_pub));
        CHECK(from_init == from_resp);
        CHECK(hex_of(ByteView(from_init)) ==
              document.at("dh_shared_hex").get<std::string>());
    }

    SECTION("directional subkeys match") {
        const Bytes dh = hex_field(document, "dh_shared_hex");
        const crypto::HandshakeSubkeys keys = crypto::derive_handshake_subkeys(
            ByteView(dh), ByteView(nonce_init), ByteView(nonce_resp));
        const auto& expected = document.at("subkeys");
        CHECK(hex_of(ByteView(keys.k_enc_i2r)) ==
              expected.at("k_enc_i2r_hex").get<std::string>());
        CHECK(hex_of(ByteView(keys.k_mac_i2r)) ==
              expected.at("k_mac_i2r_hex").get<std::string>());
        CHECK(hex_of(ByteView(keys.k_enc_r2i)) ==
              expected.at("k_enc_r2i_hex").get<std::string>());
        CHECK(hex_of(ByteView(keys.k_mac_r2i)) ==
              expected.at("k_mac_r2i_hex").get<std::string>());

        // Directional separation is the anti-reflection property; if any two of
        // these collide a reflected frame would verify.
        CHECK(keys.k_enc_i2r != keys.k_enc_r2i);
        CHECK(keys.k_mac_i2r != keys.k_mac_r2i);
        CHECK(keys.k_enc_i2r != keys.k_mac_i2r);
    }

    SECTION("INIT and RESP signatures verify and reproduce byte-for-byte") {
        const auto& signatures = document.at("signatures");
        const std::string init_payload =
            signatures.at("init_sig_payload_utf8").get<std::string>();
        const std::string resp_payload =
            signatures.at("resp_sig_payload_utf8").get<std::string>();

        // The signed transcript deliberately carries the HS3 label while the KDF
        // uses HS4. Guard against a well-meaning "fix".
        CHECK(init_payload.rfind("I2PCHAT-HS3|INIT|", 0) == 0);
        CHECK(resp_payload.rfind("I2PCHAT-HS3|RESP|", 0) == 0);

        const Bytes init_sig = crypto::sign_data(ByteView(init_seed), as_bytes(init_payload));
        CHECK(hex_of(ByteView(init_sig)) ==
              signatures.at("init_signature_hex").get<std::string>());

        const Bytes resp_sig = crypto::sign_data(ByteView(resp_seed), as_bytes(resp_payload));
        CHECK(hex_of(ByteView(resp_sig)) ==
              signatures.at("resp_signature_hex").get<std::string>());

        const Bytes init_pub = hex_field(parties, "initiator_signing_pub_hex");
        CHECK(crypto::verify_signature(ByteView(init_pub), as_bytes(init_payload),
                                       ByteView(init_sig)));
        // A single flipped byte must fail.
        Bytes tampered = init_sig;
        tampered[0] ^= 0x01;
        CHECK_FALSE(crypto::verify_signature(ByteView(init_pub), as_bytes(init_payload),
                                             ByteView(tampered)));
    }

    SECTION("FINISHED key confirmation matches") {
        const auto& signatures = document.at("signatures");
        const auto& confirmation = document.at("key_confirmation");
        const std::string resp_payload =
            signatures.at("resp_sig_payload_utf8").get<std::string>();

        const Bytes transcript =
            crypto::compute_handshake_transcript_hash(as_bytes(resp_payload));
        CHECK(hex_of(ByteView(transcript)) ==
              confirmation.at("transcript_hash_hex").get<std::string>());

        const Bytes mac_i2r = hex_field(document.at("subkeys"), "k_mac_i2r_hex");
        const Bytes mac_r2i = hex_field(document.at("subkeys"), "k_mac_r2i_hex");
        const Bytes finished_i2r =
            crypto::compute_handshake_finished(ByteView(mac_i2r), ByteView(transcript));
        const Bytes finished_r2i =
            crypto::compute_handshake_finished(ByteView(mac_r2i), ByteView(transcript));
        CHECK(hex_of(ByteView(finished_i2r)) ==
              confirmation.at("finished_i2r_hex").get<std::string>());
        CHECK(hex_of(ByteView(finished_r2i)) ==
              confirmation.at("finished_r2i_hex").get<std::string>());

        CHECK(crypto::verify_handshake_finished(ByteView(mac_i2r), ByteView(transcript),
                                                ByteView(finished_i2r)));
        CHECK_FALSE(crypto::verify_handshake_finished(
            ByteView(mac_i2r), ByteView(transcript), ByteView(finished_r2i)));
    }

    SECTION("per-frame MAC layout matches") {
        for (const auto& entry : document.at("frame_macs")) {
            const Bytes key = hex_field(entry, "key_hex");
            const Bytes body = hex_field(entry, "body_hex");
            const auto type = entry.at("msg_type").get<std::string>();
            const Bytes mac = crypto::compute_mac(
                ByteView(key), type.at(0), ByteView(body),
                entry.at("seq").get<std::uint64_t>(),
                entry.at("msg_id").get<std::uint64_t>(),
                static_cast<std::uint8_t>(entry.at("flags").get<unsigned>()));
            CHECK(hex_of(ByteView(mac)) == entry.at("mac_hex").get<std::string>());
        }
    }

    SECTION("invalid X25519 public keys are rejected") {
        for (const auto& entry : document.at("invalid_dh_public_keys_hex")) {
            const std::optional<Bytes> bad =
                encoding::hex_decode(entry.get<std::string>());
            REQUIRE(bad.has_value());
            CHECK_THROWS_AS(
                crypto::compute_dh_shared_secret(ByteView(init_eph_priv), ByteView(*bad)),
                crypto::CryptoError);
        }
    }
}

TEST_CASE("SecretBox round-trips and rejects tampering", "[crypto]") {
    crypto::init();
    const Bytes key(32, 0x42);
    const Bytes plaintext = to_bytes("secret payload");

    const Bytes sealed = crypto::encrypt_message(ByteView(key), ByteView(plaintext));
    // nonce(24) || ciphertext || tag(16)
    CHECK(sealed.size() == 24 + plaintext.size() + 16);

    const std::optional<Bytes> opened =
        crypto::decrypt_message(ByteView(key), ByteView(sealed));
    REQUIRE(opened.has_value());
    CHECK(*opened == plaintext);

    Bytes tampered = sealed;
    tampered.back() ^= 0x01;
    CHECK_FALSE(crypto::decrypt_message(ByteView(key), ByteView(tampered)).has_value());

    const Bytes wrong_key(32, 0x43);
    CHECK_FALSE(crypto::decrypt_message(ByteView(wrong_key), ByteView(sealed)).has_value());

    // Truncated input must not read out of bounds.
    CHECK_FALSE(crypto::decrypt_message(ByteView(key), ByteView(Bytes(10, 0))).has_value());
}

TEST_CASE("constant-time comparison handles length mismatches", "[crypto]") {
    crypto::init();
    const Bytes a = to_bytes("abc");
    const Bytes b = to_bytes("abc");
    const Bytes c = to_bytes("abd");
    const Bytes d = to_bytes("abcd");

    CHECK(crypto::constant_time_equal(ByteView(a), ByteView(b)));
    CHECK_FALSE(crypto::constant_time_equal(ByteView(a), ByteView(c)));
    CHECK_FALSE(crypto::constant_time_equal(ByteView(a), ByteView(d)));
    CHECK(crypto::constant_time_equal(ByteView(Bytes{}), ByteView(Bytes{})));
}
