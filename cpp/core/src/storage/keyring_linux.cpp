#include "keyring_backend.hpp"

#if defined(__linux__)

#include <string>

#if defined(I2PCHAT_HAVE_LIBSECRET)
#include <libsecret/secret.h>
#endif

namespace i2pchat::storage::keyring::backend {

#if defined(I2PCHAT_HAVE_LIBSECRET)
namespace {

/// The attribute schema Python's `keyring` SecretService backend uses. Anything
/// else would store the secret where the other client cannot find it.
const SecretSchema* schema() {
    static const SecretSchema instance = {
        "org.freedesktop.Secret.Generic",
        SECRET_SCHEMA_NONE,
        {
            {"service", SECRET_SCHEMA_ATTRIBUTE_STRING},
            {"username", SECRET_SCHEMA_ATTRIBUTE_STRING},
            {nullptr, SECRET_SCHEMA_ATTRIBUTE_STRING},
        },
        0, 0, 0, 0, 0, 0, 0, 0,
    };
    return &instance;
}

}  // namespace

bool available() { return true; }

std::optional<std::string> get(std::string_view service, std::string_view account) {
    GError* error = nullptr;
    gchar* value = secret_password_lookup_sync(
        schema(), nullptr, &error, "service", std::string(service).c_str(), "username",
        std::string(account).c_str(), nullptr);
    if (error != nullptr) {
        g_error_free(error);
        return std::nullopt;
    }
    if (value == nullptr) {
        return std::nullopt;
    }
    std::string secret(value);
    secret_password_free(value);
    return secret;
}

bool set(std::string_view service, std::string_view account, std::string_view secret) {
    GError* error = nullptr;
    const std::string label = "i2pchat " + std::string(account);
    const gboolean stored = secret_password_store_sync(
        schema(), SECRET_COLLECTION_DEFAULT, label.c_str(),
        std::string(secret).c_str(), nullptr, &error, "service",
        std::string(service).c_str(), "username", std::string(account).c_str(), nullptr);
    if (error != nullptr) {
        g_error_free(error);
        return false;
    }
    return stored == TRUE;
}

bool erase(std::string_view service, std::string_view account) {
    GError* error = nullptr;
    const gboolean removed = secret_password_clear_sync(
        schema(), nullptr, &error, "service", std::string(service).c_str(), "username",
        std::string(account).c_str(), nullptr);
    if (error != nullptr) {
        g_error_free(error);
        return false;
    }
    return removed == TRUE;
}

#else  // no libsecret at build time

// Without libsecret the sidecar file is the only wrap-key store. That is a
// supported configuration, not a broken one, so report unavailability plainly
// instead of failing to build.
bool available() { return false; }

std::optional<std::string> get(std::string_view, std::string_view) {
    return std::nullopt;
}

bool set(std::string_view, std::string_view, std::string_view) { return false; }

bool erase(std::string_view, std::string_view) { return false; }

#endif

}  // namespace i2pchat::storage::keyring::backend

#endif  // __linux__
