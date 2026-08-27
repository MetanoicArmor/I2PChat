#include "i2pchat/session/secure_channel.hpp"

#include "i2pchat/crypto.hpp"

namespace i2pchat::session {

SecureChannel::SecureChannel(SessionKeys keys, protocol::PaddingProfile padding)
    : keys_(std::move(keys)), padding_(padding) {
    if (!keys_.installed()) {
        throw HandshakeError("SecureChannel requires installed session keys");
    }
}

Bytes SecureChannel::encrypt_frame(char msg_type, ByteView plaintext,
                                   std::uint64_t msg_id) {
    const std::uint64_t seq = ++send_seq_;
    const Bytes padded = protocol::apply_padding(plaintext, padding_);
    const Bytes payload = protocol::build_encrypted_payload(
        ByteView(keys_.send_enc), ByteView(keys_.send_mac), msg_type, seq, msg_id,
        ByteView(padded));
    return protocol::encode_frame(msg_type, ByteView(payload), msg_id,
                                  protocol::kFlagEncrypted);
}

Bytes SecureChannel::decrypt_frame(const protocol::Frame& frame) {
    if (!frame.encrypted()) {
        // Accepting plaintext here would let an attacker strip encryption from
        // an established channel simply by clearing a flag bit.
        throw protocol::ProtocolError(
            "Plaintext application frame on an encrypted channel");
    }

    const protocol::EncryptedPayload parts =
        protocol::split_encrypted_payload(ByteView(frame.payload));
    if (parts.sealed.empty()) {
        throw protocol::ProtocolError("Encrypted frame body is empty");
    }

    // Authenticate before decrypting: the MAC covers the frame header fields as
    // well, so a peer cannot relabel a frame it captured.
    if (!crypto::verify_mac(ByteView(keys_.recv_mac), frame.msg_type, parts.sealed,
                            parts.mac, parts.seq, frame.msg_id, frame.flags)) {
        throw protocol::ProtocolError("Frame MAC verification failed");
    }

    // Sequence numbers must be consecutive, not merely increasing. The
    // underlying I2P stream is reliable and ordered, so a gap means frames were
    // dropped or injected — either way the channel is no longer trustworthy.
    if (parts.seq != last_recv_seq_ + 1) {
        throw protocol::ProtocolError("Replay protection triggered: frame sequence " +
                                      std::to_string(parts.seq) + ", expected " +
                                      std::to_string(last_recv_seq_ + 1));
    }

    std::optional<Bytes> plaintext =
        crypto::decrypt_message(ByteView(keys_.recv_enc), parts.sealed);
    if (!plaintext.has_value()) {
        throw protocol::ProtocolError("Frame decryption failed");
    }

    // Only advance the counter once the frame is fully accepted, so a rejected
    // frame cannot burn a sequence number.
    last_recv_seq_ = parts.seq;
    return protocol::remove_padding(ByteView(*plaintext));
}

}  // namespace i2pchat::session
