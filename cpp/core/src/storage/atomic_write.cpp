#include "i2pchat/storage/atomic_write.hpp"

#include <cstdio>
#include <fstream>
#include <random>
#include <stdexcept>

#ifndef _WIN32
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace i2pchat::storage {
namespace {

std::filesystem::path temp_sibling(const std::filesystem::path& path) {
    // The temporary file must live in the same directory: rename() is only
    // atomic within a filesystem.
    static std::mt19937_64 generator{std::random_device{}()};
    const auto suffix = std::to_string(generator());
    return path.parent_path() / ("." + path.filename().string() + ".tmp" + suffix);
}

void set_owner_only(const std::filesystem::path& path) {
#ifndef _WIN32
    ::chmod(path.c_str(), S_IRUSR | S_IWUSR);
#else
    (void)path;
#endif
}

}  // namespace

void atomic_write_bytes(const std::filesystem::path& path, ByteView contents) {
    if (!path.parent_path().empty()) {
        std::filesystem::create_directories(path.parent_path());
    }
    const std::filesystem::path temp = temp_sibling(path);

    {
        std::ofstream stream(temp, std::ios::binary | std::ios::trunc);
        if (!stream) {
            throw std::runtime_error("Cannot create temporary file " + temp.string());
        }
        set_owner_only(temp);
        stream.write(reinterpret_cast<const char*>(contents.data()),
                     static_cast<std::streamsize>(contents.size()));
        stream.flush();
        if (!stream) {
            std::error_code ignored;
            std::filesystem::remove(temp, ignored);
            throw std::runtime_error("Short write to " + temp.string());
        }
    }

    std::error_code error;
    std::filesystem::rename(temp, path, error);
    if (error) {
        std::error_code ignored;
        std::filesystem::remove(temp, ignored);
        throw std::runtime_error("Cannot replace " + path.string() + ": " +
                                 error.message());
    }
    set_owner_only(path);
}

void atomic_write_text(const std::filesystem::path& path, std::string_view contents) {
    atomic_write_bytes(path, as_bytes(contents));
}

void atomic_write_json(const std::filesystem::path& path, const nlohmann::json& value) {
    const std::string text = value.dump(2, ' ', /*ensure_ascii=*/true);
    atomic_write_text(path, text + "\n");
}

Bytes read_file(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("Cannot read " + path.string());
    }
    return Bytes(std::istreambuf_iterator<char>(stream),
                 std::istreambuf_iterator<char>());
}

}  // namespace i2pchat::storage
