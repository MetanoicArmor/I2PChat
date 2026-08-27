#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace i2pchat {

using Byte = std::uint8_t;
using Bytes = std::vector<Byte>;
using ByteView = std::span<const Byte>;

/// Reinterpret a text buffer as bytes. The referenced storage must outlive the
/// returned view.
inline ByteView as_bytes(std::string_view text) noexcept {
    return ByteView(reinterpret_cast<const Byte*>(text.data()), text.size());
}

inline Bytes to_bytes(std::string_view text) {
    return Bytes(text.begin(), text.end());
}

inline std::string to_string(ByteView data) {
    return std::string(reinterpret_cast<const char*>(data.data()), data.size());
}

inline void append(Bytes& target, ByteView data) {
    target.insert(target.end(), data.begin(), data.end());
}

inline void append(Bytes& target, std::string_view text) {
    target.insert(target.end(), text.begin(), text.end());
}

/// Big-endian encodings. The wire protocol and every at-rest header in
/// I2PChat is big-endian, so these are the only integer encoders provided.
inline void append_u64_be(Bytes& target, std::uint64_t value) {
    for (int shift = 56; shift >= 0; shift -= 8) {
        target.push_back(static_cast<Byte>((value >> shift) & 0xFFu));
    }
}

inline void append_u32_be(Bytes& target, std::uint32_t value) {
    for (int shift = 24; shift >= 0; shift -= 8) {
        target.push_back(static_cast<Byte>((value >> shift) & 0xFFu));
    }
}

inline void append_u16_be(Bytes& target, std::uint16_t value) {
    target.push_back(static_cast<Byte>((value >> 8) & 0xFFu));
    target.push_back(static_cast<Byte>(value & 0xFFu));
}

inline std::uint64_t read_u64_be(ByteView data) {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < 8 && i < data.size(); ++i) {
        value = (value << 8) | data[i];
    }
    return value;
}

inline std::uint32_t read_u32_be(ByteView data) {
    std::uint32_t value = 0;
    for (std::size_t i = 0; i < 4 && i < data.size(); ++i) {
        value = (value << 8) | data[i];
    }
    return value;
}

inline std::uint16_t read_u16_be(ByteView data) {
    if (data.size() < 2) {
        return 0;
    }
    return static_cast<std::uint16_t>((data[0] << 8) | data[1]);
}

}  // namespace i2pchat
