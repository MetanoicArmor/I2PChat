#pragma once

#include <array>
#include <cstdint>
#include <deque>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>

#include "i2pchat/bytes.hpp"

/// vNext binary framing (protocol version 4).
///
///   MAGIC(4) | VER(1) | TYPE(1) | FLAGS(1) | MSG_ID(8 BE) | LEN(4 BE) | PAYLOAD
///
/// Source of truth: i2pchat/protocol/protocol_codec.py.
namespace i2pchat::protocol {

inline constexpr std::array<Byte, 4> kMagic = {0x89, 'I', '2', 'P'};
inline constexpr std::uint8_t kProtocolVersion = 4;
inline constexpr std::size_t kHeaderSize = 19;  // 4 + 1 + 1 + 1 + 8 + 4
inline constexpr std::uint8_t kFlagEncrypted = 0x01;
inline constexpr std::size_t kEncryptedTrailerSize = 8 + 32;  // seq + mac
inline constexpr std::size_t kMaxFrameBody = 2 * 1024 * 1024;
inline constexpr std::size_t kDefaultResyncLimit = 64 * 1024;

/// The frame types accepted on the live channel. Anything else is a violation.
inline const std::set<char>& default_allowed_types() {
    static const std::set<char> types{'U', 'S', 'P', 'O', 'F', 'D', 'E', 'I', 'H', 'G'};
    return types;
}

struct Frame {
    char msg_type = '\0';
    Bytes payload;
    std::uint8_t flags = 0;
    std::uint64_t msg_id = 0;

    [[nodiscard]] bool encrypted() const noexcept {
        return (flags & kFlagEncrypted) != 0;
    }
};

/// A peer violated the framing contract. The session must be torn down rather
/// than attempting to recover: the reference implementation treats every one of
/// these as fatal.
class ProtocolError : public std::runtime_error {
public:
    explicit ProtocolError(const std::string& message) : std::runtime_error(message) {}
};

/// Encodes a single frame. Throws ProtocolError for a disallowed type or an
/// oversized payload.
Bytes encode_frame(char msg_type, ByteView payload, std::uint64_t msg_id,
                   std::uint8_t flags, const std::set<char>& allowed_types,
                   std::size_t max_frame_body = kMaxFrameBody);

inline Bytes encode_frame(char msg_type, ByteView payload, std::uint64_t msg_id,
                          std::uint8_t flags = 0) {
    return encode_frame(msg_type, payload, msg_id, flags, default_allowed_types());
}

/// Incremental frame parser.
///
/// Bytes are pushed in as they arrive and completed frames are pulled out, so
/// the same parser serves an asio socket, a test buffer or a replayed fixture.
/// Before each frame the reader scans forward for MAGIC, discarding junk, and
/// gives up after `resync_limit` bytes.
class FrameReader {
public:
    explicit FrameReader(std::set<char> allowed_types = default_allowed_types(),
                         std::size_t max_frame_body = kMaxFrameBody,
                         std::size_t resync_limit = kDefaultResyncLimit);

    void feed(ByteView data);

    /// Returns the next complete frame, or nullopt when more bytes are needed.
    /// Throws ProtocolError on a framing violation.
    std::optional<Frame> next();

    [[nodiscard]] std::size_t buffered() const noexcept { return buffer_.size(); }

private:
    bool synchronize();

    std::set<char> allowed_types_;
    std::size_t max_frame_body_;
    std::size_t resync_limit_;
    Bytes buffer_;
    std::size_t consumed_ = 0;      // read cursor into buffer_
    std::size_t scanned_ = 0;       // bytes examined during the current resync
    bool synchronized_ = false;     // MAGIC located at the cursor
};

}  // namespace i2pchat::protocol
