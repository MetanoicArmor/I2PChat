#include "i2pchat/protocol/codec.hpp"

#include <algorithm>

namespace i2pchat::protocol {

Bytes encode_frame(char msg_type, ByteView payload, std::uint64_t msg_id,
                   std::uint8_t flags, const std::set<char>& allowed_types,
                   std::size_t max_frame_body) {
    if (allowed_types.find(msg_type) == allowed_types.end()) {
        throw ProtocolError(std::string("Unknown message type: ") + msg_type);
    }
    if (payload.size() > max_frame_body) {
        throw ProtocolError("Frame too large: " + std::to_string(payload.size()));
    }

    Bytes out;
    out.reserve(kHeaderSize + payload.size());
    out.insert(out.end(), kMagic.begin(), kMagic.end());
    out.push_back(kProtocolVersion);
    out.push_back(static_cast<Byte>(msg_type));
    out.push_back(flags);
    append_u64_be(out, msg_id);
    append_u32_be(out, static_cast<std::uint32_t>(payload.size()));
    append(out, payload);
    return out;
}

FrameReader::FrameReader(std::set<char> allowed_types, std::size_t max_frame_body,
                         std::size_t resync_limit)
    : allowed_types_(std::move(allowed_types)),
      max_frame_body_(max_frame_body),
      resync_limit_(std::max(resync_limit, kHeaderSize)) {}

void FrameReader::feed(ByteView data) {
    // Drop already-consumed bytes so a long-lived session does not grow the
    // buffer without bound.
    if (consumed_ > 0 && consumed_ == buffer_.size()) {
        buffer_.clear();
        consumed_ = 0;
    } else if (consumed_ > 64 * 1024) {
        buffer_.erase(buffer_.begin(),
                      buffer_.begin() + static_cast<std::ptrdiff_t>(consumed_));
        consumed_ = 0;
    }
    append(buffer_, data);
}

bool FrameReader::synchronize() {
    if (synchronized_) {
        return true;
    }
    // Advance one byte at a time looking for MAGIC, mirroring the reference
    // reader's semantics including how it counts bytes against the limit.
    while (buffer_.size() - consumed_ >= kMagic.size()) {
        if (std::equal(kMagic.begin(), kMagic.end(), buffer_.begin() +
                                                         static_cast<std::ptrdiff_t>(
                                                             consumed_))) {
            scanned_ += kMagic.size();
            if (scanned_ > resync_limit_) {
                throw ProtocolError("Resync limit exceeded while searching for MAGIC");
            }
            synchronized_ = true;
            return true;
        }
        ++consumed_;
        ++scanned_;
        if (scanned_ > resync_limit_) {
            throw ProtocolError("Resync limit exceeded while searching for MAGIC");
        }
    }
    return false;
}

std::optional<Frame> FrameReader::next() {
    if (!synchronize()) {
        return std::nullopt;
    }
    if (buffer_.size() - consumed_ < kHeaderSize) {
        return std::nullopt;
    }

    const Byte* header = buffer_.data() + consumed_;
    const std::uint8_t version = header[4];
    const char msg_type = static_cast<char>(header[5]);
    const std::uint8_t flags = header[6];
    const std::uint64_t msg_id = read_u64_be(ByteView(header + 7, 8));
    const std::uint32_t declared_len = read_u32_be(ByteView(header + 15, 4));

    if (version != kProtocolVersion) {
        throw ProtocolError("Unsupported protocol version: " + std::to_string(version));
    }
    if (allowed_types_.find(msg_type) == allowed_types_.end()) {
        throw ProtocolError(std::string("Unknown frame type: ") + msg_type);
    }
    if (declared_len > max_frame_body_) {
        throw ProtocolError("Frame too large: " + std::to_string(declared_len));
    }
    if (buffer_.size() - consumed_ < kHeaderSize + declared_len) {
        return std::nullopt;  // body still in flight
    }

    Frame frame;
    frame.msg_type = msg_type;
    frame.flags = flags;
    frame.msg_id = msg_id;
    const Byte* body = buffer_.data() + consumed_ + kHeaderSize;
    frame.payload.assign(body, body + declared_len);

    consumed_ += kHeaderSize + declared_len;
    synchronized_ = false;
    scanned_ = 0;
    return frame;
}

}  // namespace i2pchat::protocol
