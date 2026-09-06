#include "i2pchat/storage/replica_settings.hpp"

#include <cstdlib>
#include <fstream>
#include <iterator>
#include <set>
#include <utility>

#include "i2pchat/canonical_json.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/sealed_json.hpp"

namespace i2pchat::storage {
namespace {

std::string trimmed(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

/// Keep only non-empty tokens whose endpoint is in the list. A token for an
/// endpoint that is not configured has nowhere legitimate to go.
std::map<std::string, std::string> auth_subset(
    const std::vector<std::string>& endpoints,
    const std::map<std::string, std::string>& raw) {
    const std::set<std::string> known(endpoints.begin(), endpoints.end());
    std::map<std::string, std::string> out;
    for (const auto& [address, token] : raw) {
        const std::string key = trimmed(address);
        const std::string value = trimmed(token);
        if (key.empty() || value.empty() || known.count(key) == 0) {
            continue;
        }
        out.emplace(key, value);
    }
    return out;
}

std::map<std::string, std::string> string_map(const nlohmann::json& value) {
    std::map<std::string, std::string> out;
    if (!value.is_object()) {
        return out;
    }
    for (const auto& [key, item] : value.items()) {
        if (item.is_string()) {
            out.emplace(key, item.get<std::string>());
        }
    }
    return out;
}

}  // namespace

std::vector<std::string> normalize_replica_endpoints(
    const std::vector<std::string>& raw) {
    std::vector<std::string> out;
    std::set<std::string> seen;
    for (const std::string& item : raw) {
        const std::string candidate = trimmed(item);
        if (candidate.empty() || candidate.front() == '#' ||
            !seen.insert(candidate).second) {
            continue;
        }
        out.push_back(candidate);
    }
    return out;
}

Bytes derive_replica_auth_key(ByteView identity_key, ByteView salt) {
    const Bytes prk = crypto::hkdf_extract(as_bytes("I2PCHAT-REPLICA-AUTH"), identity_key);
    const Bytes profile_key =
        crypto::hkdf_expand(ByteView(prk), as_bytes("I2PCHAT-REPLICA-AUTH|profile-key"), 32);
    const Bytes file_prk = crypto::hkdf_extract(salt, ByteView(profile_key));
    return crypto::hkdf_expand(ByteView(file_prk),
                               as_bytes("I2PCHAT-REPLICA-AUTH|file-key"), 32);
}

std::string encrypt_replica_auth(const std::map<std::string, std::string>& auth,
                                 ByteView identity_key) {
    const Bytes salt = crypto::random_bytes(kReplicaAuthSaltSize);
    const Bytes key = derive_replica_auth_key(identity_key, ByteView(salt));

    nlohmann::json payload = nlohmann::json::object();
    for (const auto& [address, token] : auth) {
        payload[address] = token;
    }
    const std::string plaintext = json_canonical::dump(payload);
    const Bytes ciphertext = crypto::encrypt_message(ByteView(key), as_bytes(plaintext));

    Bytes blob;
    blob.reserve(4 + 2 + salt.size() + ciphertext.size());
    for (const char letter : kReplicaAuthMagic) {
        blob.push_back(static_cast<Byte>(letter));
    }
    blob.push_back(static_cast<Byte>(kReplicaAuthBlobVersion >> 8));
    blob.push_back(static_cast<Byte>(kReplicaAuthBlobVersion & 0xFF));
    blob.insert(blob.end(), salt.begin(), salt.end());
    blob.insert(blob.end(), ciphertext.begin(), ciphertext.end());
    return encoding::base64_encode(ByteView(blob));
}

std::map<std::string, std::string> decrypt_replica_auth(std::string_view blob,
                                                        ByteView identity_key) {
    const std::optional<Bytes> raw = encoding::base64_decode(blob);
    const std::size_t header_size = 4 + 2 + kReplicaAuthSaltSize;
    if (!raw.has_value() || raw->size() < header_size) {
        throw SealedJsonError("bad replica_auth blob header");
    }
    if (to_string(ByteView(raw->data(), 4)) != kReplicaAuthMagic) {
        throw SealedJsonError("bad replica_auth blob magic");
    }
    const std::uint16_t version =
        static_cast<std::uint16_t>((*raw)[4] << 8 | (*raw)[5]);
    if (version != kReplicaAuthBlobVersion) {
        throw SealedJsonError("unsupported replica_auth blob version " +
                              std::to_string(version));
    }
    const ByteView salt(raw->data() + 6, kReplicaAuthSaltSize);
    const ByteView ciphertext(raw->data() + header_size, raw->size() - header_size);
    const Bytes key = derive_replica_auth_key(identity_key, salt);
    const std::optional<Bytes> plaintext = crypto::decrypt_message(ByteView(key), ciphertext);
    if (!plaintext.has_value()) {
        throw SealedJsonError("replica_auth decryption failed (wrong key or tampered)");
    }
    const nlohmann::json document =
        nlohmann::json::parse(to_string(ByteView(*plaintext)), nullptr, false);
    if (document.is_discarded() || !document.is_object()) {
        throw SealedJsonError("replica_auth blob is not an object");
    }
    return string_map(document);
}

ReplicaSettings parse_replica_settings(const nlohmann::json& data,
                                      std::optional<ByteView> identity_key) {
    ReplicaSettings settings;
    if (!data.is_object()) {
        return settings;
    }
    const std::uint32_t version = data.value("version", 0U);
    if (version < 1 || version > kReplicaSettingsVersion) {
        return settings;
    }
    const nlohmann::json& replicas = data.contains("replicas") ? data.at("replicas")
                                                              : nlohmann::json::array();
    if (!replicas.is_array()) {
        return settings;
    }
    std::vector<std::string> raw;
    for (const auto& item : replicas) {
        if (item.is_string()) {
            raw.push_back(item.get<std::string>());
        }
    }
    settings.endpoints = normalize_replica_endpoints(raw);
    if (version == 1) {
        return settings;
    }

    if (version >= 3 && data.contains("replica_auth_enc") &&
        data.at("replica_auth_enc").is_string()) {
        const std::string blob = data.at("replica_auth_enc").get<std::string>();
        if (!blob.empty()) {
            if (!identity_key.has_value()) {
                settings.auth_locked = true;
                return settings;
            }
            try {
                settings.auth =
                    auth_subset(settings.endpoints,
                                decrypt_replica_auth(blob, *identity_key));
            } catch (const std::exception&) {
                settings.auth_locked = true;
            }
            return settings;
        }
        // A version 3 file may still carry plaintext tokens when it was written
        // without an identity key, so fall through rather than reporting none.
    }
    if (data.contains("replica_auth")) {
        settings.auth = auth_subset(settings.endpoints, string_map(data.at("replica_auth")));
    }
    return settings;
}

nlohmann::json replica_settings_to_json(const ReplicaSettings& settings,
                                        std::optional<ByteView> identity_key) {
    const std::vector<std::string> endpoints =
        normalize_replica_endpoints(settings.endpoints);
    const std::map<std::string, std::string> auth = auth_subset(endpoints, settings.auth);

    nlohmann::json document;
    document["version"] = kReplicaSettingsVersion;
    document["replicas"] = endpoints;
    if (auth.empty()) {
        return document;
    }
    if (identity_key.has_value()) {
        document["replica_auth_enc"] = encrypt_replica_auth(auth, *identity_key);
    } else {
        // Without a key the tokens go to disk in the clear rather than being
        // dropped: losing them would silently break every authenticated
        // replica, which is worse than the exposure.
        document["replica_auth"] = auth;
    }
    return document;
}

ReplicaSettings load_replica_settings(const std::filesystem::path& path,
                                     std::optional<ByteView> identity_key) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return {};
    }
    const std::string contents{std::istreambuf_iterator<char>(stream),
                               std::istreambuf_iterator<char>()};
    const nlohmann::json document = nlohmann::json::parse(contents, nullptr, false);
    if (document.is_discarded()) {
        return {};
    }
    return parse_replica_settings(document, identity_key);
}

void save_replica_settings(const std::filesystem::path& path,
                           const ReplicaSettings& settings,
                           std::optional<ByteView> identity_key) {
    atomic_write_json(path, replica_settings_to_json(settings, identity_key));
}

std::vector<std::string> default_release_blindbox_endpoints() {
    std::vector<std::string> out;
    out.reserve(std::size(kDefaultReleaseBlindboxEndpoints));
    for (const std::string_view ep : kDefaultReleaseBlindboxEndpoints) {
        out.emplace_back(ep);
    }
    return out;
}

bool same_as_release_builtin_endpoints(const std::vector<std::string>& endpoints) {
    const auto want = normalize_replica_endpoints(default_release_blindbox_endpoints());
    const auto have = normalize_replica_endpoints(endpoints);
    if (want.size() != have.size()) {
        return false;
    }
    std::set<std::string> left(want.begin(), want.end());
    std::set<std::string> right(have.begin(), have.end());
    return left == right;
}

bool builtin_release_replicas_disabled() {
    const char* raw = std::getenv("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS");
    if (raw == nullptr) {
        return false;
    }
    std::string value(raw);
    for (char& ch : value) {
        if (ch >= 'A' && ch <= 'Z') {
            ch = static_cast<char>(ch - 'A' + 'a');
        }
    }
    return value == "1" || value == "true" || value == "yes" || value == "on";
}

}  // namespace i2pchat::storage
