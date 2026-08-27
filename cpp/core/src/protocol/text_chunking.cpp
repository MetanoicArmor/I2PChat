#include "i2pchat/protocol/text_chunking.hpp"

#include <algorithm>
#include <stdexcept>

#include "i2pchat/encoding.hpp"

namespace i2pchat::protocol {
namespace {

/// Byte offsets of every code point boundary, plus the end offset. Working
/// through this index keeps the code-point semantics of the reference
/// implementation while operating on UTF-8 storage.
std::vector<std::size_t> code_point_offsets(std::string_view text) {
    std::vector<std::size_t> offsets;
    offsets.reserve(text.size() + 1);
    std::size_t i = 0;
    while (i < text.size()) {
        offsets.push_back(i);
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 1;
        if ((lead & 0xE0u) == 0xC0u) {
            width = 2;
        } else if ((lead & 0xF0u) == 0xE0u) {
            width = 3;
        } else if ((lead & 0xF8u) == 0xF0u) {
            width = 4;
        }
        i += std::min(width, text.size() - i);
    }
    offsets.push_back(text.size());
    return offsets;
}

}  // namespace

std::vector<std::string> split_long_chat_text(std::string_view text,
                                              std::size_t max_chars) {
    if (max_chars < 32) {
        throw std::invalid_argument("max_chars must be at least 32");
    }
    if (text.empty()) {
        return {};
    }

    const std::vector<std::size_t> offsets = code_point_offsets(text);
    const std::size_t total = offsets.size() - 1;  // code point count
    if (total <= max_chars) {
        return {std::string(text)};
    }

    const std::size_t min_lookback = std::max<std::size_t>(1, max_chars / kMinBreakLookbackFraction);

    std::vector<std::string> parts;
    std::size_t start = 0;  // code point index of the current part

    while (start < total) {
        const std::size_t remaining = total - start;
        if (remaining <= max_chars) {
            parts.emplace_back(text.substr(offsets[start], offsets[total] - offsets[start]));
            break;
        }

        // Window is [start, start + max_chars) in code points.
        const std::size_t window_end = start + max_chars;

        // Last newline within the window, as an offset relative to `start`.
        std::size_t break_at = std::string::npos;
        for (std::size_t idx = window_end; idx > start; --idx) {
            if (text[offsets[idx - 1]] == '\n') {
                break_at = idx - 1 - start;
                break;
            }
        }
        if (break_at != std::string::npos && break_at >= min_lookback) {
            const std::size_t cut = start + break_at + 1;  // keep the newline
            parts.emplace_back(text.substr(offsets[start], offsets[cut] - offsets[start]));
            start = cut;
            continue;
        }

        // Last space within the window.
        break_at = std::string::npos;
        for (std::size_t idx = window_end; idx > start; --idx) {
            if (text[offsets[idx - 1]] == ' ') {
                break_at = idx - 1 - start;
                break;
            }
        }
        if (break_at != std::string::npos && break_at >= min_lookback) {
            const std::size_t cut = start + break_at;  // drop the space
            parts.emplace_back(text.substr(offsets[start], offsets[cut] - offsets[start]));
            start = cut + 1;
            continue;
        }

        parts.emplace_back(
            text.substr(offsets[start], offsets[window_end] - offsets[start]));
        start = window_end;
    }

    // The reference implementation drops empty parts before returning.
    parts.erase(std::remove_if(parts.begin(), parts.end(),
                               [](const std::string& part) { return part.empty(); }),
                parts.end());
    return parts;
}

}  // namespace i2pchat::protocol
