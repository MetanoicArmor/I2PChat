#include "keyring_backend.hpp"

#if defined(__APPLE__)

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include <string>

namespace i2pchat::storage::keyring::backend {
namespace {

/// A CFTypeRef that releases itself, so the many early returns below cannot
/// leak Core Foundation objects.
template <typename T>
class CfPtr {
public:
    CfPtr() = default;
    explicit CfPtr(T ref) : ref_(ref) {}
    ~CfPtr() {
        if (ref_ != nullptr) {
            CFRelease(ref_);
        }
    }
    CfPtr(const CfPtr&) = delete;
    CfPtr& operator=(const CfPtr&) = delete;
    CfPtr(CfPtr&& other) noexcept : ref_(other.ref_) { other.ref_ = nullptr; }

    [[nodiscard]] T get() const noexcept { return ref_; }
    [[nodiscard]] T* address() noexcept { return &ref_; }
    explicit operator bool() const noexcept { return ref_ != nullptr; }

private:
    T ref_ = nullptr;
};

CfPtr<CFStringRef> cf_string(std::string_view text) {
    return CfPtr<CFStringRef>(CFStringCreateWithBytes(
        kCFAllocatorDefault, reinterpret_cast<const UInt8*>(text.data()),
        static_cast<CFIndex>(text.size()), kCFStringEncodingUTF8, false));
}

/// The query that identifies one profile's entry. A generic password keyed by
/// service and account is exactly what Python's `keyring` writes on macOS, and
/// matching it is what lets the two clients share a profile's wrap key.
CfPtr<CFMutableDictionaryRef> base_query(CFStringRef service, CFStringRef account) {
    CfPtr<CFMutableDictionaryRef> query(CFDictionaryCreateMutable(
        kCFAllocatorDefault, 4, &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks));
    if (query) {
        CFDictionarySetValue(query.get(), kSecClass, kSecClassGenericPassword);
        CFDictionarySetValue(query.get(), kSecAttrService, service);
        CFDictionarySetValue(query.get(), kSecAttrAccount, account);
    }
    return query;
}

}  // namespace

bool available() { return true; }

std::optional<std::string> get(std::string_view service, std::string_view account) {
    const CfPtr<CFStringRef> service_ref = cf_string(service);
    const CfPtr<CFStringRef> account_ref = cf_string(account);
    if (!service_ref || !account_ref) {
        return std::nullopt;
    }

    CfPtr<CFMutableDictionaryRef> query =
        base_query(service_ref.get(), account_ref.get());
    if (!query) {
        return std::nullopt;
    }
    CFDictionarySetValue(query.get(), kSecReturnData, kCFBooleanTrue);
    CFDictionarySetValue(query.get(), kSecMatchLimit, kSecMatchLimitOne);

    CfPtr<CFDataRef> data;
    if (SecItemCopyMatching(query.get(),
                            reinterpret_cast<CFTypeRef*>(data.address())) !=
            errSecSuccess ||
        !data) {
        return std::nullopt;
    }
    return std::string(reinterpret_cast<const char*>(CFDataGetBytePtr(data.get())),
                       static_cast<std::size_t>(CFDataGetLength(data.get())));
}

bool set(std::string_view service, std::string_view account, std::string_view secret) {
    const CfPtr<CFStringRef> service_ref = cf_string(service);
    const CfPtr<CFStringRef> account_ref = cf_string(account);
    if (!service_ref || !account_ref) {
        return false;
    }
    const CfPtr<CFDataRef> secret_ref(CFDataCreate(
        kCFAllocatorDefault, reinterpret_cast<const UInt8*>(secret.data()),
        static_cast<CFIndex>(secret.size())));
    if (!secret_ref) {
        return false;
    }

    CfPtr<CFMutableDictionaryRef> query =
        base_query(service_ref.get(), account_ref.get());
    if (!query) {
        return false;
    }

    // SecItemAdd fails on an existing item, so update first and fall back to
    // adding. Doing it the other way round would leave a stale value behind.
    CfPtr<CFMutableDictionaryRef> update(CFDictionaryCreateMutable(
        kCFAllocatorDefault, 1, &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks));
    if (!update) {
        return false;
    }
    CFDictionarySetValue(update.get(), kSecValueData, secret_ref.get());

    const OSStatus updated = SecItemUpdate(query.get(), update.get());
    if (updated == errSecSuccess) {
        return true;
    }
    if (updated != errSecItemNotFound) {
        return false;
    }

    CFDictionarySetValue(query.get(), kSecValueData, secret_ref.get());
    return SecItemAdd(query.get(), nullptr) == errSecSuccess;
}

bool erase(std::string_view service, std::string_view account) {
    const CfPtr<CFStringRef> service_ref = cf_string(service);
    const CfPtr<CFStringRef> account_ref = cf_string(account);
    if (!service_ref || !account_ref) {
        return false;
    }
    const CfPtr<CFMutableDictionaryRef> query =
        base_query(service_ref.get(), account_ref.get());
    if (!query) {
        return false;
    }
    return SecItemDelete(query.get()) == errSecSuccess;
}

}  // namespace i2pchat::storage::keyring::backend

#endif  // __APPLE__
