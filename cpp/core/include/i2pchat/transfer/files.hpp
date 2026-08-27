#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Filesystem-facing helpers for incoming transfers.
///
/// Everything here exists because the other end of the connection chooses the
/// filename. A peer is not trusted to pick a path, an extension, or a size.
namespace i2pchat::transfer {

/// The largest file the reference accepts.
inline constexpr std::uint64_t kMaxFileSize = 2ull * 1024 * 1024 * 1024;
/// The largest inline image, which is much smaller because it is held in
/// memory until it can be validated.
inline constexpr std::uint64_t kMaxImageSize = 5ull * 1024 * 1024;
/// Lines accepted for a text-rendered image before it is truncated.
inline constexpr std::size_t kMaxImageLines = 500;

/// Reduce a peer-supplied name to something safe to create in a directory.
///
/// Directory components are dropped, characters that could confuse a shell or a
/// filesystem are replaced, and a name that is empty or hidden is replaced
/// outright. Unicode is preserved: a Cyrillic or CJK filename is not a security
/// problem and mangling it would be a bug, not caution.
///
/// `now_seconds` seeds the generated fallback name; it is a parameter so the
/// result is reproducible in tests.
[[nodiscard]] std::string sanitize_filename(std::string_view name,
                                            std::optional<std::int64_t> now_seconds =
                                                std::nullopt);

/// A path in `directory` that does not exist yet, resolving collisions as
/// `name (1).ext` rather than overwriting. A peer must not be able to replace a
/// file the user already has by naming a transfer after it.
///
/// Throws `std::filesystem::filesystem_error` when no free name is found.
[[nodiscard]] std::filesystem::path allocate_unique_filename(
    const std::filesystem::path& directory, std::string_view filename,
    int max_attempts = 1000);

/// The longest base64 text that can encode `byte_count` bytes.
///
/// Used to reject an oversized chunk before decoding it, since decoding is
/// where the memory is spent.
[[nodiscard]] std::size_t max_base64_chars_for_bytes(std::uint64_t byte_count);

/// The extension for a recognised inline image ("png", "jpeg", "webp"), or
/// nothing. Sniffing the magic bytes is what stops a peer from sending an
/// executable named `photo.png`.
[[nodiscard]] std::optional<std::string> detect_inline_image_format(ByteView header);

}  // namespace i2pchat::transfer
