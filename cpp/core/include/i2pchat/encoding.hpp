#pragma once

#include <optional>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Text encodings used on the wire and at rest.
///
/// I2PChat uses three different base64 alphabets and mixing them up is a
/// classic source of subtle bugs, so each one gets its own named function:
///
///   * standard  — file and image chunks inside frames;
///   * I2P       — destinations, with '-' and '~' replacing '+' and '/';
///   * base64url — group invite tokens, without padding.
namespace i2pchat::encoding {

std::string hex_encode(ByteView data);
std::optional<Bytes> hex_decode(std::string_view text);

std::string base64_encode(ByteView data);
std::optional<Bytes> base64_decode(std::string_view text);

/// I2P's base64 variant: '+' -> '-' and '/' -> '~', padding retained.
std::string i2p_base64_encode(ByteView data);
std::optional<Bytes> i2p_base64_decode(std::string_view text);

/// base64url without padding, used for group invite tokens.
std::string base64url_encode_nopad(ByteView data);
std::optional<Bytes> base64url_decode(std::string_view text);

/// Lowercase RFC 4648 base32 without padding, as used for .b32.i2p addresses.
std::string base32_encode_lower(ByteView data);

/// Number of Unicode code points in a UTF-8 string. Returns nullopt when the
/// input is not well-formed UTF-8.
std::optional<std::size_t> utf8_length(std::string_view text);

/// Byte offset of the given code point index, or nullopt if out of range.
std::optional<std::size_t> utf8_offset_of_code_point(std::string_view text,
                                                     std::size_t index);

}  // namespace i2pchat::encoding
