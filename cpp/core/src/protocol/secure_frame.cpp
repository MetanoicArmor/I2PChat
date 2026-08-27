#include "i2pchat/protocol/secure_frame.hpp"

#include <algorithm>

#include "i2pchat/crypto.hpp"

namespace i2pchat::protocol {
namespace {

constexpr std::size_t kPaddingHeaderSize = 7 + 4;  // magic + original length

bool has_padding_envelope(ByteView payload) {
    if (payload.size() < kPaddingEnvelopeMagic.size()) {
        return false;
    }
    return std::equal(kPaddingEnvelopeMagic.begin(), kPaddingEnvelopeMagic.end(),
                      payload.begin());
}

}  // namespace

Bytes apply_padding(ByteView body, PaddingProfile profile) {
    if (profile == PaddingProfile::Off) {
        return Bytes(body.begin(), body.end());
    }

    Bytes wrapped;
    wrapped.reserve(kPaddingHeaderSize + body.size() + kPaddingBalancedBlock);
    append(wrapped, kPaddingEnvelopeMagic);
    append_u32_be(wrapped, static_cast<std::uint32_t>(body.size()));
    append(wrapped, body);

    const std::size_t target =
        ((wrapped.size() + kPaddingBalancedBlock - 1) / kPaddingBalancedBlock) *
        kPaddingBalancedBlock;
    if (target > wrapped.size()) {
        append(wrapped, ByteView(crypto::random_bytes(target - wrapped.size())));
    }
    return wrapped;
}

Bytes remove_padding(ByteView payload) {
    if (!has_padding_envelope(payload)) {
        // The peer is running with padding off, or this is a legacy frame.
        return Bytes(payload.begin(), payload.end());
    }
    if (payload.size() < kPaddingHeaderSize) {
        throw ProtocolError("Malformed padded payload header");
    }
    const std::uint32_t original_len =
        read_u32_be(payload.subspan(kPaddingEnvelopeMagic.size(), 4));
    const ByteView body = payload.subspan(kPaddingHeaderSize);
    if (original_len > body.size()) {
        throw ProtocolError("Padded payload declares more data than it carries");
    }
    const ByteView unpadded = body.first(original_len);
    return Bytes(unpadded.begin(), unpadded.end());
}

Bytes build_encrypted_payload(ByteView enc_key, ByteView mac_key, char msg_type,
                              std::uint64_t seq, std::uint64_t msg_id,
                              ByteView plaintext, std::optional<ByteView> nonce) {
    const Bytes sealed = nonce.has_value()
                             ? crypto::encrypt_message_with_nonce(enc_key, plaintext,
                                                                  *nonce)
                             : crypto::encrypt_message(enc_key, plaintext);
    const Bytes mac = crypto::compute_mac(mac_key, msg_type, ByteView(sealed), seq,
                                          msg_id, kFlagEncrypted);

    Bytes payload;
    payload.reserve(8 + sealed.size() + mac.size());
    append_u64_be(payload, seq);
    append(payload, ByteView(sealed));
    append(payload, ByteView(mac));
    return payload;
}

EncryptedPayload split_encrypted_payload(ByteView body) {
    if (body.size() <= kEncryptedTrailerSize) {
        throw ProtocolError("Encrypted frame body is too short");
    }
    EncryptedPayload parts;
    parts.seq = read_u64_be(body.first(8));
    parts.sealed = body.subspan(8, body.size() - 8 - crypto::kHmacSize);
    parts.mac = body.last(crypto::kHmacSize);
    return parts;
}

}  // namespace i2pchat::protocol
