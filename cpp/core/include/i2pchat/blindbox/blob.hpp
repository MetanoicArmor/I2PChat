#pragma once

#include <cstdint>
#include <optional>

#include "i2pchat/blindbox/key_schedule.hpp"
#include "i2pchat/bytes.hpp"

/// The encrypted envelope a BlindBox message travels in.
///
/// Plaintext before encryption:
///
///   magic(8)="BLNDBX01" | version(1) | direction(1) | index(8 BE)
///   | state_tag(16) | frame_len(4 BE) | frame | random padding
///
/// The whole thing goes into a SecretBox under the blob key, so a replica sees
/// only opaque bytes. Padding rounds the plaintext up to a bucket so the length
/// leaks nothing beyond the bucket.
///
/// Direction, index and state tag are inside the ciphertext rather than beside
/// it: a replica cannot alter them, and the reader can tell a replay or a
/// misfiled blob from the one it expects.
namespace i2pchat::blindbox {

inline constexpr std::string_view kBlobMagic = "BLNDBX01";
inline constexpr std::uint8_t kBlobVersion = 1;
inline constexpr std::size_t kBlobHeaderSize = 8 + 1 + 1 + 8 + 16 + 4;
inline constexpr std::size_t kMaxBlobFrameSize = 2 * 1024 * 1024;
inline constexpr std::size_t kDefaultPaddingBucket = 256;

/// What the reader knows in advance and will not accept a blob without.
struct BlobExpectation {
    std::optional<Direction> direction;
    std::optional<std::uint64_t> index;
    /// 16 bytes when set.
    std::optional<Bytes> state_tag;
};

[[nodiscard]] Bytes encrypt_blob(ByteView frame, ByteView blob_key, Direction direction,
                                 std::uint64_t index, ByteView state_tag,
                                 std::size_t padding_bucket = kDefaultPaddingBucket);

/// Returns the frame, or throws `BlindBoxError` if the blob does not decrypt or
/// does not match `expected`.
[[nodiscard]] Bytes decrypt_blob(ByteView blob, ByteView blob_key,
                                 const BlobExpectation& expected = {});

}  // namespace i2pchat::blindbox
