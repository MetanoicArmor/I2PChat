#include "i2pchat/blindbox/blob.hpp"

#include <algorithm>

#include "i2pchat/crypto.hpp"

namespace i2pchat::blindbox {
namespace {

constexpr std::uint8_t kDirectionSend = 1;
constexpr std::uint8_t kDirectionRecv = 2;

std::uint8_t direction_code(Direction direction) {
    return direction == Direction::Send ? kDirectionSend : kDirectionRecv;
}

std::optional<Direction> direction_from_code(std::uint8_t code) {
    if (code == kDirectionSend) {
        return Direction::Send;
    }
    if (code == kDirectionRecv) {
        return Direction::Recv;
    }
    return std::nullopt;
}

}  // namespace

Bytes encrypt_blob(ByteView frame, ByteView blob_key, Direction direction,
                   std::uint64_t index, ByteView state_tag, std::size_t padding_bucket) {
    if (frame.empty()) {
        throw BlindBoxError("frame must be non-empty");
    }
    if (frame.size() > kMaxBlobFrameSize) {
        throw BlindBoxError("frame is too large");
    }
    if (blob_key.size() != 32) {
        throw BlindBoxError("blob_key must be 32 bytes");
    }
    if (state_tag.size() != 16) {
        throw BlindBoxError("state_tag must be 16 bytes");
    }
    if (padding_bucket == 0) {
        throw BlindBoxError("padding_bucket must be positive");
    }

    Bytes plaintext;
    plaintext.reserve(kBlobHeaderSize + frame.size() + padding_bucket);
    append(plaintext, kBlobMagic);
    plaintext.push_back(kBlobVersion);
    plaintext.push_back(direction_code(direction));
    append_u64_be(plaintext, index);
    append(plaintext, state_tag);
    append_u32_be(plaintext, static_cast<std::uint32_t>(frame.size()));
    append(plaintext, frame);

    const std::size_t target =
        ((plaintext.size() + padding_bucket - 1) / padding_bucket) * padding_bucket;
    if (target > plaintext.size()) {
        append(plaintext, ByteView(crypto::random_bytes(target - plaintext.size())));
    }

    return crypto::encrypt_message(blob_key, ByteView(plaintext));
}

Bytes decrypt_blob(ByteView blob, ByteView blob_key, const BlobExpectation& expected) {
    if (blob_key.size() != 32) {
        throw BlindBoxError("blob_key must be 32 bytes");
    }
    if (expected.state_tag.has_value() && expected.state_tag->size() != 16) {
        throw BlindBoxError("expected state_tag must be 16 bytes");
    }

    const std::optional<Bytes> decrypted = crypto::decrypt_message(blob_key, blob);
    if (!decrypted.has_value()) {
        throw BlindBoxError("BlindBox blob decryption failed");
    }
    if (decrypted->size() < kBlobHeaderSize) {
        throw BlindBoxError("BlindBox blob too short");
    }

    const ByteView plaintext(*decrypted);
    if (!std::equal(kBlobMagic.begin(), kBlobMagic.end(), plaintext.begin())) {
        throw BlindBoxError("BlindBox blob magic mismatch");
    }
    if (plaintext[8] != kBlobVersion) {
        throw BlindBoxError("Unsupported BlindBox blob version");
    }
    const std::optional<Direction> direction = direction_from_code(plaintext[9]);
    if (!direction.has_value()) {
        throw BlindBoxError("Invalid BlindBox direction code");
    }
    const std::uint64_t index = read_u64_be(plaintext.subspan(10, 8));
    const ByteView state_tag = plaintext.subspan(18, 16);
    const std::uint32_t frame_len = read_u32_be(plaintext.subspan(34, 4));

    if (frame_len == 0 || frame_len > kMaxBlobFrameSize) {
        throw BlindBoxError("Invalid BlindBox frame length");
    }
    const ByteView payload = plaintext.subspan(kBlobHeaderSize);
    if (frame_len > payload.size()) {
        throw BlindBoxError("Malformed BlindBox blob payload");
    }

    if (expected.direction.has_value() && *expected.direction != *direction) {
        throw BlindBoxError("BlindBox direction mismatch");
    }
    if (expected.index.has_value() && *expected.index != index) {
        throw BlindBoxError("BlindBox index mismatch");
    }
    if (expected.state_tag.has_value() &&
        !crypto::constant_time_equal(ByteView(*expected.state_tag), state_tag)) {
        throw BlindBoxError("BlindBox state tag mismatch");
    }

    return Bytes(payload.begin(), payload.begin() + frame_len);
}

}  // namespace i2pchat::blindbox
