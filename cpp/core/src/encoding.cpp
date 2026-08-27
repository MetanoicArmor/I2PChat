#include "i2pchat/encoding.hpp"

#include <array>
#include <cstring>

namespace i2pchat::encoding {
namespace {

constexpr std::string_view kHexDigits = "0123456789abcdef";
constexpr std::string_view kB64Standard =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
constexpr std::string_view kB32Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

std::string base64_encode_with(ByteView data, std::string_view alphabet, bool pad) {
    std::string out;
    out.reserve(((data.size() + 2) / 3) * 4);
    std::size_t i = 0;
    for (; i + 3 <= data.size(); i += 3) {
        const std::uint32_t triple = (static_cast<std::uint32_t>(data[i]) << 16) |
                                     (static_cast<std::uint32_t>(data[i + 1]) << 8) |
                                     static_cast<std::uint32_t>(data[i + 2]);
        out.push_back(alphabet[(triple >> 18) & 0x3F]);
        out.push_back(alphabet[(triple >> 12) & 0x3F]);
        out.push_back(alphabet[(triple >> 6) & 0x3F]);
        out.push_back(alphabet[triple & 0x3F]);
    }
    const std::size_t remaining = data.size() - i;
    if (remaining == 1) {
        const std::uint32_t triple = static_cast<std::uint32_t>(data[i]) << 16;
        out.push_back(alphabet[(triple >> 18) & 0x3F]);
        out.push_back(alphabet[(triple >> 12) & 0x3F]);
        if (pad) {
            out.append("==");
        }
    } else if (remaining == 2) {
        const std::uint32_t triple = (static_cast<std::uint32_t>(data[i]) << 16) |
                                     (static_cast<std::uint32_t>(data[i + 1]) << 8);
        out.push_back(alphabet[(triple >> 18) & 0x3F]);
        out.push_back(alphabet[(triple >> 12) & 0x3F]);
        out.push_back(alphabet[(triple >> 6) & 0x3F]);
        if (pad) {
            out.push_back('=');
        }
    }
    return out;
}

std::optional<Bytes> base64_decode_with(std::string_view text, std::string_view alphabet) {
    std::array<int, 256> lookup{};
    lookup.fill(-1);
    for (std::size_t i = 0; i < alphabet.size(); ++i) {
        lookup[static_cast<unsigned char>(alphabet[i])] = static_cast<int>(i);
    }

    std::uint32_t accumulator = 0;
    int bits = 0;
    bool padding_seen = false;
    Bytes out;
    out.reserve((text.size() / 4) * 3);

    for (const char ch : text) {
        if (ch == '=') {
            padding_seen = true;
            continue;
        }
        // Whitespace is not accepted: the reference implementation validates
        // strictly and callers strip it beforehand where that is intended.
        const int value = lookup[static_cast<unsigned char>(ch)];
        if (value < 0 || padding_seen) {
            return std::nullopt;
        }
        accumulator = (accumulator << 6) | static_cast<std::uint32_t>(value);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<Byte>((accumulator >> bits) & 0xFFu));
        }
    }
    // Leftover bits must be zero padding, never data.
    if (bits >= 6 || (accumulator & ((1u << bits) - 1u)) != 0) {
        return std::nullopt;
    }
    return out;
}

}  // namespace

std::string hex_encode(ByteView data) {
    std::string out;
    out.reserve(data.size() * 2);
    for (const Byte value : data) {
        out.push_back(kHexDigits[value >> 4]);
        out.push_back(kHexDigits[value & 0x0Fu]);
    }
    return out;
}

std::optional<Bytes> hex_decode(std::string_view text) {
    if (text.size() % 2 != 0) {
        return std::nullopt;
    }
    const auto nibble = [](char ch) -> int {
        if (ch >= '0' && ch <= '9') return ch - '0';
        if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
        if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
        return -1;
    };
    Bytes out;
    out.reserve(text.size() / 2);
    for (std::size_t i = 0; i < text.size(); i += 2) {
        const int hi = nibble(text[i]);
        const int lo = nibble(text[i + 1]);
        if (hi < 0 || lo < 0) {
            return std::nullopt;
        }
        out.push_back(static_cast<Byte>((hi << 4) | lo));
    }
    return out;
}

std::string base64_encode(ByteView data) {
    return base64_encode_with(data, kB64Standard, true);
}

std::optional<Bytes> base64_decode(std::string_view text) {
    return base64_decode_with(text, kB64Standard);
}

std::string i2p_base64_encode(ByteView data) {
    std::string alphabet(kB64Standard);
    alphabet[62] = '-';
    alphabet[63] = '~';
    return base64_encode_with(data, alphabet, true);
}

std::optional<Bytes> i2p_base64_decode(std::string_view text) {
    std::string alphabet(kB64Standard);
    alphabet[62] = '-';
    alphabet[63] = '~';
    return base64_decode_with(text, alphabet);
}

std::string base64url_encode_nopad(ByteView data) {
    std::string alphabet(kB64Standard);
    alphabet[62] = '-';
    alphabet[63] = '_';
    return base64_encode_with(data, alphabet, false);
}

std::optional<Bytes> base64url_decode(std::string_view text) {
    std::string alphabet(kB64Standard);
    alphabet[62] = '-';
    alphabet[63] = '_';
    return base64_decode_with(text, alphabet);
}

std::string base32_encode_lower(ByteView data) {
    std::string out;
    out.reserve(((data.size() * 8) + 4) / 5);
    std::uint32_t accumulator = 0;
    int bits = 0;
    for (const Byte value : data) {
        accumulator = (accumulator << 8) | value;
        bits += 8;
        while (bits >= 5) {
            bits -= 5;
            out.push_back(kB32Alphabet[(accumulator >> bits) & 0x1Fu]);
        }
    }
    if (bits > 0) {
        out.push_back(kB32Alphabet[(accumulator << (5 - bits)) & 0x1Fu]);
    }
    for (char& ch : out) {
        if (ch >= 'A' && ch <= 'Z') {
            ch = static_cast<char>(ch - 'A' + 'a');
        }
    }
    return out;
}

std::optional<std::size_t> utf8_length(std::string_view text) {
    std::size_t count = 0;
    std::size_t i = 0;
    while (i < text.size()) {
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 0;
        std::uint32_t code_point = 0;
        if (lead < 0x80) {
            width = 1;
            code_point = lead;
        } else if ((lead & 0xE0u) == 0xC0u) {
            width = 2;
            code_point = lead & 0x1Fu;
        } else if ((lead & 0xF0u) == 0xE0u) {
            width = 3;
            code_point = lead & 0x0Fu;
        } else if ((lead & 0xF8u) == 0xF0u) {
            width = 4;
            code_point = lead & 0x07u;
        } else {
            return std::nullopt;
        }
        if (i + width > text.size()) {
            return std::nullopt;
        }
        for (std::size_t k = 1; k < width; ++k) {
            const auto cont = static_cast<unsigned char>(text[i + k]);
            if ((cont & 0xC0u) != 0x80u) {
                return std::nullopt;
            }
            code_point = (code_point << 6) | (cont & 0x3Fu);
        }
        // Reject overlong encodings, surrogates and out-of-range code points.
        static constexpr std::uint32_t kMinimum[5] = {0, 0, 0x80, 0x800, 0x10000};
        if (code_point < kMinimum[width] || code_point > 0x10FFFFu ||
            (code_point >= 0xD800u && code_point <= 0xDFFFu)) {
            return std::nullopt;
        }
        i += width;
        ++count;
    }
    return count;
}

std::optional<std::size_t> utf8_offset_of_code_point(std::string_view text,
                                                    std::size_t index) {
    std::size_t count = 0;
    std::size_t i = 0;
    while (i <= text.size()) {
        if (count == index) {
            return i;
        }
        if (i == text.size()) {
            return std::nullopt;
        }
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 1;
        if ((lead & 0xE0u) == 0xC0u) {
            width = 2;
        } else if ((lead & 0xF0u) == 0xE0u) {
            width = 3;
        } else if ((lead & 0xF8u) == 0xF0u) {
            width = 4;
        }
        i += width;
        ++count;
    }
    return std::nullopt;
}

}  // namespace i2pchat::encoding
