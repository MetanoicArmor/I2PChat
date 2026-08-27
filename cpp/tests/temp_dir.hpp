#pragma once

#include <atomic>
#include <filesystem>
#include <string>

#ifndef _WIN32
#include <unistd.h>
#else
#include <process.h>
#endif

namespace i2pchat::testing {

/// A temporary directory that removes itself on destruction, so a failing
/// assertion cannot leave state behind that makes the next run pass or fail
/// spuriously.
class TempDir {
public:
    explicit TempDir(std::string prefix = "i2pchat-test") {
#ifdef _WIN32
        const auto pid = _getpid();
#else
        const auto pid = ::getpid();
#endif
        static std::atomic<int> counter{0};
        path_ = std::filesystem::temp_directory_path() /
                (prefix + "-" + std::to_string(pid) + "-" +
                 std::to_string(counter.fetch_add(1)));
        std::filesystem::create_directories(path_);
    }

    ~TempDir() {
        std::error_code ignored;
        std::filesystem::remove_all(path_, ignored);
    }

    TempDir(const TempDir&) = delete;
    TempDir& operator=(const TempDir&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

    [[nodiscard]] std::filesystem::path file(const std::string& name) const {
        return path_ / name;
    }

private:
    std::filesystem::path path_;
};

}  // namespace i2pchat::testing
