#include "keyring_backend.hpp"

#if defined(_WIN32)

#include <windows.h>
// wincred.h must follow windows.h.
#include <wincred.h>

#include <string>
#include <vector>

namespace i2pchat::storage::keyring::backend {
namespace {

/// Python's `keyring` Windows backend stores generic credentials named
/// `service:account` with the secret as a UTF-16 blob. Reproducing both the
/// naming and the encoding is what makes a profile portable between clients.
std::wstring target_name(std::string_view service, std::string_view account) {
    const std::string combined = std::string(service) + ":" + std::string(account);
    const int length = MultiByteToWideChar(CP_UTF8, 0, combined.data(),
                                           static_cast<int>(combined.size()), nullptr, 0);
    std::wstring wide(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, combined.data(), static_cast<int>(combined.size()),
                        wide.data(), length);
    return wide;
}

std::wstring to_wide(std::string_view text) {
    const int length = MultiByteToWideChar(CP_UTF8, 0, text.data(),
                                           static_cast<int>(text.size()), nullptr, 0);
    std::wstring wide(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                        wide.data(), length);
    return wide;
}

std::string from_wide(const wchar_t* text, std::size_t characters) {
    if (characters == 0) {
        return {};
    }
    const int length = WideCharToMultiByte(CP_UTF8, 0, text, static_cast<int>(characters),
                                           nullptr, 0, nullptr, nullptr);
    std::string narrow(static_cast<std::size_t>(length), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, static_cast<int>(characters), narrow.data(),
                        length, nullptr, nullptr);
    return narrow;
}

}  // namespace

bool available() { return true; }

std::optional<std::string> get(std::string_view service, std::string_view account) {
    const std::wstring target = target_name(service, account);
    PCREDENTIALW credential = nullptr;
    if (CredReadW(target.c_str(), CRED_TYPE_GENERIC, 0, &credential) == FALSE) {
        return std::nullopt;
    }
    std::optional<std::string> secret;
    if (credential->CredentialBlob != nullptr && credential->CredentialBlobSize > 0) {
        secret = from_wide(reinterpret_cast<const wchar_t*>(credential->CredentialBlob),
                           credential->CredentialBlobSize / sizeof(wchar_t));
    }
    CredFree(credential);
    return secret;
}

bool set(std::string_view service, std::string_view account, std::string_view secret) {
    std::wstring target = target_name(service, account);
    std::wstring wide_secret = to_wide(secret);
    std::wstring user = to_wide(account);

    CREDENTIALW credential{};
    credential.Type = CRED_TYPE_GENERIC;
    credential.TargetName = target.data();
    credential.UserName = user.data();
    credential.CredentialBlob = reinterpret_cast<LPBYTE>(wide_secret.data());
    credential.CredentialBlobSize =
        static_cast<DWORD>(wide_secret.size() * sizeof(wchar_t));
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE;

    return CredWriteW(&credential, 0) != FALSE;
}

bool erase(std::string_view service, std::string_view account) {
    const std::wstring target = target_name(service, account);
    return CredDeleteW(target.c_str(), CRED_TYPE_GENERIC, 0) != FALSE;
}

}  // namespace i2pchat::storage::keyring::backend

#endif  // _WIN32
