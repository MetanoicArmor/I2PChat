#include "i2pchat/storage/keyring.hpp"

#include <atomic>

#include "keyring_backend.hpp"

namespace i2pchat::storage::keyring {
namespace {

std::atomic<bool> g_enabled{true};

}  // namespace

void set_enabled(bool value) { g_enabled.store(value, std::memory_order_relaxed); }

bool enabled() { return g_enabled.load(std::memory_order_relaxed); }

bool available() { return enabled() && backend::available(); }

std::optional<std::string> get(std::string_view service, std::string_view account) {
    if (!available()) {
        return std::nullopt;
    }
    return backend::get(service, account);
}

bool set(std::string_view service, std::string_view account, std::string_view secret) {
    if (!available()) {
        return false;
    }
    return backend::set(service, account, secret);
}

bool erase(std::string_view service, std::string_view account) {
    if (!available()) {
        return false;
    }
    return backend::erase(service, account);
}

}  // namespace i2pchat::storage::keyring
