#include "i2pchat/transfer/files.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <system_error>

namespace i2pchat::transfer {
namespace {

/// The reference's unsafe set: shell and Windows path metacharacters plus every
/// control byte. Bytes above 0x7F are left alone so UTF-8 names survive.
bool is_unsafe(unsigned char byte) {
    switch (byte) {
        case '<':
        case '>':
        case ':':
        case '"':
        case '/':
        case '\\':
        case '|':
        case '?':
        case '*':
            return true;
        default:
            return byte <= 0x1F;
    }
}

std::string trim(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

std::int64_t now_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

/// The name without its directory part, for both separators: a peer on Windows
/// may send a backslash path and a peer on Unix a forward-slash one.
std::string basename_of(std::string_view name) {
    const auto position = name.find_last_of("/\\");
    if (position == std::string_view::npos) {
        return std::string(name);
    }
    return std::string(name.substr(position + 1));
}

/// Splits at the last dot, matching `os.path.splitext`: a leading dot is part
/// of the stem, so ".bashrc" has no extension.
std::pair<std::string, std::string> split_extension(const std::string& name) {
    const auto dot = name.find_last_of('.');
    if (dot == std::string::npos || dot == 0) {
        return {name, {}};
    }
    return {name.substr(0, dot), name.substr(dot)};
}

}  // namespace

std::string sanitize_filename(std::string_view name,
                              std::optional<std::int64_t> timestamp) {
    const std::int64_t stamp = timestamp.value_or(now_seconds());
    std::string cleaned = trim(basename_of(name));
    for (char& character : cleaned) {
        if (is_unsafe(static_cast<unsigned char>(character))) {
            character = '_';
        }
    }

    // A hidden name is replaced rather than unhidden: silently creating
    // `.profile` in the downloads directory is worse than an opaque name.
    if (cleaned.empty() || cleaned.front() == '.') {
        return "file_" + std::to_string(stamp);
    }
    if (cleaned.size() > 200) {
        std::string extension = split_extension(cleaned).second;
        if (extension.size() > 10) {
            extension.resize(10);
        }
        return "file_" + std::to_string(stamp) + extension;
    }
    return cleaned;
}

std::filesystem::path allocate_unique_filename(const std::filesystem::path& directory,
                                               std::string_view filename,
                                               int max_attempts) {
    const std::string safe_name = sanitize_filename(filename);
    const std::filesystem::path first = directory / safe_name;
    if (!std::filesystem::exists(first)) {
        return first;
    }

    const auto [stem, extension] = split_extension(safe_name);
    for (int index = 1; index <= max_attempts; ++index) {
        const std::filesystem::path candidate =
            directory / (stem + " (" + std::to_string(index) + ")" + extension);
        if (!std::filesystem::exists(candidate)) {
            return candidate;
        }
    }
    throw std::filesystem::filesystem_error(
        "Cannot allocate a unique filename for " + safe_name, first,
        std::make_error_code(std::errc::file_exists));
}

std::size_t max_base64_chars_for_bytes(std::uint64_t byte_count) {
    if (byte_count == 0) {
        return 0;
    }
    return static_cast<std::size_t>(((byte_count + 2) / 3) * 4);
}

std::optional<std::string> detect_inline_image_format(ByteView header) {
    static constexpr std::array<Byte, 8> kPng{0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    if (header.size() >= kPng.size() &&
        std::equal(kPng.begin(), kPng.end(), header.begin())) {
        return "png";
    }
    if (header.size() >= 3 && header[0] == 0xFF && header[1] == 0xD8 && header[2] == 0xFF) {
        return "jpeg";
    }
    // RIFF, then four bytes of chunk size, then WEBP.
    if (header.size() >= 12 && to_string(header.subspan(0, 4)) == "RIFF" &&
        to_string(header.subspan(8, 4)) == "WEBP") {
        return "webp";
    }
    return std::nullopt;
}

}  // namespace i2pchat::transfer
