#include "i2pchat/session/handshake.hpp"

#include <algorithm>
#include <array>
#include <cctype>

#include "i2pchat/encoding.hpp"

namespace i2pchat::session {
namespace {

std::string to_lower_trimmed(std::string_view text) {
    std::size_t begin = 0;
    while (begin < text.size() &&
           std::isspace(static_cast<unsigned char>(text[begin])) != 0) {
        ++begin;
    }
    std::size_t end = text.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
        --end;
    }
    std::string out(text.substr(begin, end - begin));
    std::transform(out.begin(), out.end(), out.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return out;
}

/// Parsed INIT or RESP payload. The hex strings are retained because the signed
/// transcript is built from them verbatim: re-encoding the decoded bytes would
/// be equivalent only as long as the peer used lowercase hex, and relying on
/// that would be a signature bug waiting to happen.
struct SignedPayload {
    Bytes nonce;
    Bytes eph_pub;
    Bytes sign_pub;
    Bytes signature;
    std::string nonce_hex;
    std::string eph_hex;
    std::string sign_pub_hex;
};

Bytes decode_hex_field(const std::string& text, std::size_t expected_size,
                       std::string_view what) {
    const std::optional<Bytes> decoded = encoding::hex_decode(text);
    if (!decoded.has_value()) {
        throw HandshakeError("Malformed hex in handshake " + std::string(what));
    }
    if (decoded->size() != expected_size) {
        throw HandshakeError("Invalid handshake " + std::string(what) + " length");
    }
    return *decoded;
}

SignedPayload parse_signed_payload(std::string_view payload) {
    std::array<std::string, 4> parts;
    std::size_t index = 0;
    std::size_t start = 0;
    while (index < parts.size()) {
        const std::size_t separator = payload.find(':', start);
        if (index == parts.size() - 1) {
            if (separator != std::string_view::npos) {
                throw HandshakeError(
                    "Handshake payload must contain exactly nonce, ephemeral key, "
                    "signing key and signature");
            }
            parts[index] = to_lower_trimmed(payload.substr(start));
            break;
        }
        if (separator == std::string_view::npos) {
            throw HandshakeError(
                "Handshake payload must contain exactly nonce, ephemeral key, "
                "signing key and signature");
        }
        parts[index] = to_lower_trimmed(payload.substr(start, separator - start));
        start = separator + 1;
        ++index;
    }

    SignedPayload result;
    result.nonce_hex = parts[0];
    result.eph_hex = parts[1];
    result.sign_pub_hex = parts[2];
    result.nonce = decode_hex_field(parts[0], crypto::kNonceSize, "nonce");
    result.eph_pub = decode_hex_field(parts[1], crypto::kX25519KeySize, "ephemeral key");
    result.sign_pub =
        decode_hex_field(parts[2], crypto::kEd25519PublicSize, "signing key");
    result.signature =
        decode_hex_field(parts[3], crypto::kEd25519SignatureSize, "signature");
    return result;
}

}  // namespace

Bytes build_init_sig_payload(std::string_view signer_addr, std::string_view remote_addr,
                            std::string_view nonce_hex, std::string_view eph_hex,
                            std::string_view sign_pub_hex) {
    std::string payload;
    payload.reserve(256);
    payload += "I2PCHAT-HS3|INIT|";
    payload += signer_addr;
    payload += '|';
    payload += remote_addr;
    payload += '|';
    payload += nonce_hex;
    payload += '|';
    payload += eph_hex;
    payload += '|';
    payload += sign_pub_hex;
    return to_bytes(payload);
}

Bytes build_resp_sig_payload(std::string_view signer_addr, std::string_view remote_addr,
                            std::string_view init_nonce_hex,
                            std::string_view init_eph_hex,
                            std::string_view init_sign_pub_hex,
                            std::string_view resp_nonce_hex,
                            std::string_view resp_eph_hex,
                            std::string_view resp_sign_pub_hex) {
    std::string payload;
    payload.reserve(512);
    payload += "I2PCHAT-HS3|RESP|";
    payload += signer_addr;
    payload += '|';
    payload += remote_addr;
    payload += '|';
    payload += init_nonce_hex;
    payload += '|';
    payload += init_eph_hex;
    payload += '|';
    payload += init_sign_pub_hex;
    payload += '|';
    payload += resp_nonce_hex;
    payload += '|';
    payload += resp_eph_hex;
    payload += '|';
    payload += resp_sign_pub_hex;
    return to_bytes(payload);
}

HandshakeMachine::HandshakeMachine(HandshakeConfig config) : config_(std::move(config)) {
    if (config_.local_addr.empty() || config_.peer_addr.empty()) {
        throw HandshakeError("Handshake needs both local and peer addresses");
    }
    if (config_.signing_seed.size() < crypto::kEd25519SeedSize ||
        config_.signing_public.size() != crypto::kEd25519PublicSize) {
        throw HandshakeError("Handshake needs a local signing keypair");
    }
}

void HandshakeMachine::set_ephemeral_for_test(Bytes private_key, Bytes public_key) {
    my_ephemeral_private_ = std::move(private_key);
    my_ephemeral_public_ = std::move(public_key);
}

void HandshakeMachine::set_nonce_for_test(Bytes nonce) { my_nonce_ = std::move(nonce); }

void HandshakeMachine::ensure_ephemeral() {
    if (my_ephemeral_private_.empty() || my_ephemeral_public_.empty()) {
        crypto::EphemeralKeyPair pair = crypto::generate_ephemeral_keypair();
        my_ephemeral_private_ = std::move(pair.private_key);
        my_ephemeral_public_ = std::move(pair.public_key);
    }
    if (my_nonce_.empty()) {
        my_nonce_ = crypto::generate_nonce();
    }
}

void HandshakeMachine::fail(const std::string& reason) {
    state_ = HandshakeState::Failed;
    throw HandshakeError(reason);
}

void HandshakeMachine::install_keys(bool is_initiator) {
    if (my_ephemeral_private_.empty() || peer_ephemeral_public_.empty()) {
        fail("Missing ephemeral keys");
    }
    if (my_nonce_.empty() || peer_nonce_.empty()) {
        fail("Missing handshake nonces");
    }

    const Bytes dh_shared = crypto::compute_dh_shared_secret(
        ByteView(my_ephemeral_private_), ByteView(peer_ephemeral_public_));

    const ByteView nonce_init = is_initiator ? ByteView(my_nonce_) : ByteView(peer_nonce_);
    const ByteView nonce_resp = is_initiator ? ByteView(peer_nonce_) : ByteView(my_nonce_);
    const crypto::HandshakeSubkeys subkeys =
        crypto::derive_handshake_subkeys(ByteView(dh_shared), nonce_init, nonce_resp);

    if (is_initiator) {
        keys_ = SessionKeys{subkeys.k_enc_i2r, subkeys.k_mac_i2r, subkeys.k_enc_r2i,
                            subkeys.k_mac_r2i};
    } else {
        keys_ = SessionKeys{subkeys.k_enc_r2i, subkeys.k_mac_r2i, subkeys.k_enc_i2r,
                            subkeys.k_mac_i2r};
    }

    // Forward secrecy: the ephemeral private key is of no further use, and its
    // absence from memory is the whole point.
    std::fill(my_ephemeral_private_.begin(), my_ephemeral_private_.end(), Byte{0});
    my_ephemeral_private_.clear();
}

HandshakeOutput HandshakeMachine::start_as_initiator() {
    if (state_ != HandshakeState::Idle) {
        fail("INIT can only be sent from the idle state");
    }
    ensure_ephemeral();

    const std::string nonce_hex = encoding::hex_encode(ByteView(my_nonce_));
    const std::string eph_hex = encoding::hex_encode(ByteView(my_ephemeral_public_));
    const std::string sign_pub_hex =
        encoding::hex_encode(ByteView(config_.signing_public));

    const Bytes payload = build_init_sig_payload(config_.local_addr, config_.peer_addr,
                                                nonce_hex, eph_hex, sign_pub_hex);
    const Bytes signature =
        crypto::sign_data(ByteView(config_.signing_seed), ByteView(payload));

    role_ = HandshakeRole::Initiator;
    state_ = HandshakeState::InitSent;

    HandshakeOutput output;
    output.frames.push_back("INIT:" + nonce_hex + ":" + eph_hex + ":" + sign_pub_hex +
                            ":" + encoding::hex_encode(ByteView(signature)));
    return output;
}

HandshakeOutput HandshakeMachine::on_message(std::string_view body) {
    if (state_ == HandshakeState::Failed) {
        throw HandshakeError("Handshake already failed");
    }
    if (body.rfind("INIT:", 0) == 0) {
        return handle_init(body.substr(5));
    }
    if (body.rfind("RESP:", 0) == 0) {
        return handle_resp(body.substr(5));
    }
    if (body.rfind("FINISHED:", 0) == 0) {
        return handle_finished(body.substr(9));
    }
    // Unknown handshake verbs are ignored rather than fatal, matching the
    // reference implementation's tolerance for future extensions.
    return {};
}

HandshakeOutput HandshakeMachine::handle_init(std::string_view payload) {
    if (state_ == HandshakeState::InitSent) {
        // Both sides dialled at once. Continuing would leave the two ends
        // disagreeing about which nonce is nonce_init, so tear down and retry.
        fail("Handshake role conflict: INIT received while local INIT is pending");
    }
    if (state_ != HandshakeState::Idle) {
        fail("Unexpected INIT for the current handshake state");
    }

    const SignedPayload parsed = parse_signed_payload(payload);

    // The initiator signs with itself as signer and us as remote.
    const Bytes sig_payload =
        build_init_sig_payload(config_.peer_addr, config_.local_addr, parsed.nonce_hex,
                               parsed.eph_hex, parsed.sign_pub_hex);
    if (!crypto::verify_signature(ByteView(parsed.sign_pub), ByteView(sig_payload),
                                  ByteView(parsed.signature))) {
        fail("INIT signature verification failed");
    }
    if (config_.trust_verifier &&
        config_.trust_verifier(config_.peer_addr, ByteView(parsed.sign_pub)) !=
            TrustDecision::Accept) {
        fail("Peer signing key does not match the pinned key");
    }

    peer_nonce_ = parsed.nonce;
    peer_ephemeral_public_ = parsed.eph_pub;
    peer_signing_public_ = parsed.sign_pub;

    ensure_ephemeral();
    const std::string resp_nonce_hex = encoding::hex_encode(ByteView(my_nonce_));
    const std::string resp_eph_hex =
        encoding::hex_encode(ByteView(my_ephemeral_public_));
    const std::string resp_sign_pub_hex =
        encoding::hex_encode(ByteView(config_.signing_public));

    const Bytes resp_sig_payload = build_resp_sig_payload(
        config_.local_addr, config_.peer_addr, parsed.nonce_hex, parsed.eph_hex,
        parsed.sign_pub_hex, resp_nonce_hex, resp_eph_hex, resp_sign_pub_hex);
    const Bytes signature =
        crypto::sign_data(ByteView(config_.signing_seed), ByteView(resp_sig_payload));

    role_ = HandshakeRole::Responder;
    install_keys(/*is_initiator=*/false);
    transcript_hash_ = crypto::compute_handshake_transcript_hash(
        ByteView(resp_sig_payload));
    state_ = HandshakeState::AwaitingFinished;

    HandshakeOutput output;
    output.frames.push_back("RESP:" + resp_nonce_hex + ":" + resp_eph_hex + ":" +
                            resp_sign_pub_hex + ":" +
                            encoding::hex_encode(ByteView(signature)));
    output.frames.push_back(
        "FINISHED:" + encoding::hex_encode(ByteView(crypto::compute_handshake_finished(
                          ByteView(keys_.send_mac), ByteView(transcript_hash_)))));
    finished_sent_ = true;
    return output;
}

HandshakeOutput HandshakeMachine::handle_resp(std::string_view payload) {
    if (state_ != HandshakeState::InitSent) {
        // A RESP without a matching INIT means the peer is confused or hostile.
        fail("RESP received without a pending INIT");
    }

    const SignedPayload parsed = parse_signed_payload(payload);

    const std::string init_nonce_hex = encoding::hex_encode(ByteView(my_nonce_));
    const std::string init_eph_hex =
        encoding::hex_encode(ByteView(my_ephemeral_public_));
    const std::string init_sign_pub_hex =
        encoding::hex_encode(ByteView(config_.signing_public));

    // The responder signs with itself as signer and us as remote.
    const Bytes resp_sig_payload = build_resp_sig_payload(
        config_.peer_addr, config_.local_addr, init_nonce_hex, init_eph_hex,
        init_sign_pub_hex, parsed.nonce_hex, parsed.eph_hex, parsed.sign_pub_hex);
    if (!crypto::verify_signature(ByteView(parsed.sign_pub), ByteView(resp_sig_payload),
                                  ByteView(parsed.signature))) {
        fail("RESP signature verification failed");
    }
    if (config_.trust_verifier &&
        config_.trust_verifier(config_.peer_addr, ByteView(parsed.sign_pub)) !=
            TrustDecision::Accept) {
        fail("Peer signing key does not match the pinned key");
    }

    peer_nonce_ = parsed.nonce;
    peer_ephemeral_public_ = parsed.eph_pub;
    peer_signing_public_ = parsed.sign_pub;

    install_keys(/*is_initiator=*/true);
    transcript_hash_ =
        crypto::compute_handshake_transcript_hash(ByteView(resp_sig_payload));
    state_ = HandshakeState::AwaitingFinished;

    HandshakeOutput output;
    output.frames.push_back(
        "FINISHED:" + encoding::hex_encode(ByteView(crypto::compute_handshake_finished(
                          ByteView(keys_.send_mac), ByteView(transcript_hash_)))));
    finished_sent_ = true;
    return output;
}

HandshakeOutput HandshakeMachine::handle_finished(std::string_view payload) {
    if (state_ != HandshakeState::AwaitingFinished || transcript_hash_.empty() ||
        !keys_.installed()) {
        fail("FINISHED received before key derivation");
    }
    const std::optional<Bytes> tag = encoding::hex_decode(to_lower_trimmed(payload));
    if (!tag.has_value()) {
        fail("Malformed FINISHED payload");
    }
    if (!crypto::verify_handshake_finished(ByteView(keys_.recv_mac),
                                           ByteView(transcript_hash_),
                                           ByteView(*tag))) {
        fail("Handshake key confirmation (FINISHED) failed");
    }

    peer_finished_ = true;
    HandshakeOutput output;
    if (!finished_sent_) {
        // Should not happen: both roles send FINISHED as soon as keys exist.
        output.frames.push_back(
            "FINISHED:" + encoding::hex_encode(ByteView(crypto::compute_handshake_finished(
                              ByteView(keys_.send_mac), ByteView(transcript_hash_)))));
        finished_sent_ = true;
    }
    state_ = HandshakeState::Established;
    output.established = true;
    return output;
}

}  // namespace i2pchat::session
