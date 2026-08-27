#pragma once

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace i2pchat::protocol {

/// Telegram-style cap: one chat message is at most this many Unicode code
/// points — not bytes, not UTF-16 units.
inline constexpr std::size_t kMaxChatMessageChars = 4096;

/// Do not break immediately after the start of a part, which would leave a
/// tiny leading chunk. Mirrors _MIN_BREAK_LOOKBACK_FRAC.
inline constexpr std::size_t kMinBreakLookbackFraction = 4;

/// Split a long message into parts of at most `max_chars` code points.
///
/// Break preference inside the window: the last newline, then the last space,
/// then a hard cut at the limit. A break is only taken at or beyond
/// `max_chars / kMinBreakLookbackFraction`. Empty input yields an empty vector.
///
/// Throws std::invalid_argument when `max_chars` is below 32, matching
/// i2pchat/protocol/chat_text_chunking.py.
std::vector<std::string> split_long_chat_text(std::string_view text,
                                              std::size_t max_chars =
                                                  kMaxChatMessageChars);

}  // namespace i2pchat::protocol
