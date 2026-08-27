#include "i2pchat/storage/sealed_json.hpp"

#include <algorithm>
#include <fstream>

#include "i2pchat/crypto.hpp"
#include "i2pchat/storage/atomic_write.hpp"

namespace i2pchat::storage {
namespace {

/// Read just the header, without pulling a potentially large file into memory.
std::optional<Bytes> read_header(const std::filesystem::path& path,
                                 std::string_view magic) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return std::nullopt;
    }
    Bytes header(kSealedJsonHeaderSize);
    stream.read(reinterpret_cast<char*>(header.data()),
                static_cast<std::streamsize>(header.size()));
    if (stream.gcount() != static_cast<std::streamsize>(kSealedJsonHeaderSize)) {
        return std::nullopt;
    }
    if (!std::equal(magic.begin(), magic.end(), header.begin())) {
        return std::nullopt;
    }
    return header;
}

}  // namespace

Bytes derive_sealed_profile_key(ByteView identity_key, std::string_view domain) {
    const Bytes prk = crypto::hkdf_extract(as_bytes(domain), identity_key);
    const std::string info = std::string(domain) + "|profile-key";
    return crypto::hkdf_expand(ByteView(prk), as_bytes(info), 32);
}

Bytes derive_sealed_file_key(ByteView identity_key, ByteView salt,
                             std::string_view domain, std::string_view scope) {
    const Bytes profile_key = derive_sealed_profile_key(identity_key, domain);
    const Bytes prk = crypto::hkdf_extract(salt, ByteView(profile_key));
    std::string info = std::string(domain) + "|file-key";
    if (!scope.empty()) {
        info += "|";
        info += scope;
    }
    return crypto::hkdf_expand(ByteView(prk), as_bytes(info), 32);
}

bool is_sealed_json_file(const std::filesystem::path& path, std::string_view magic) {
    return read_header(path, magic).has_value();
}

std::string serialize_sealed_payload(const nlohmann::json& payload) {
    return payload.dump(-1, ' ', /*ensure_ascii=*/true);
}

nlohmann::json read_sealed_json(const std::filesystem::path& path,
                                std::optional<ByteView> identity_key,
                                const SealedJsonFormat& format) {
    const Bytes raw = read_file(path);
    const std::string_view magic = format.magic;

    const bool sealed = raw.size() >= magic.size() &&
                        std::equal(magic.begin(), magic.end(), raw.begin());
    if (!sealed) {
        // Legacy plaintext, written before at-rest encryption existed.
        return nlohmann::json::parse(to_string(ByteView(raw)));
    }
    if (raw.size() < kSealedJsonHeaderSize) {
        throw SealedJsonError("Sealed JSON record truncated");
    }
    if (!identity_key.has_value()) {
        throw SealedJsonError("Record is encrypted but no identity key is available");
    }

    const std::uint16_t version = read_u16_be(ByteView(raw).subspan(4, 2));
    if (version != format.version) {
        throw SealedJsonError("Unsupported sealed JSON version " +
                              std::to_string(version));
    }

    const ByteView salt = ByteView(raw).subspan(6, kSealedJsonSaltSize);
    const ByteView ciphertext = ByteView(raw).subspan(kSealedJsonHeaderSize);
    const Bytes file_key =
        derive_sealed_file_key(*identity_key, salt, format.domain, format.scope);

    const std::optional<Bytes> plaintext =
        crypto::decrypt_message(ByteView(file_key), ciphertext);
    if (!plaintext.has_value()) {
        throw SealedJsonError("Sealed JSON decryption failed (wrong key or tampered)");
    }

    nlohmann::json payload = nlohmann::json::parse(to_string(ByteView(*plaintext)));
    if (!payload.is_object()) {
        throw SealedJsonError("Sealed JSON payload must be an object");
    }
    return payload;
}

void write_sealed_json(const std::filesystem::path& path, const nlohmann::json& payload,
                       std::optional<ByteView> identity_key,
                       const SealedJsonFormat& format) {
    const std::optional<Bytes> existing_header = read_header(path, format.magic);

    if (!identity_key.has_value()) {
        if (existing_header.has_value()) {
            // Overwriting sealed data with plaintext would quietly strip a
            // user's at-rest encryption, so refuse instead.
            throw SealedJsonError(
                "Refusing to overwrite an encrypted record without an identity key");
        }
        atomic_write_json(path, payload);
        return;
    }

    // Reuse the file's salt when it already has one: the reference
    // implementation keeps it stable for the life of the file.
    Bytes salt;
    if (existing_header.has_value()) {
        salt.assign(existing_header->begin() + 6, existing_header->end());
    } else {
        salt = crypto::random_bytes(kSealedJsonSaltSize);
    }

    const Bytes file_key = derive_sealed_file_key(*identity_key, ByteView(salt),
                                                  format.domain, format.scope);
    const std::string plaintext = serialize_sealed_payload(payload);
    const Bytes ciphertext =
        crypto::encrypt_message(ByteView(file_key), as_bytes(plaintext));

    Bytes blob;
    blob.reserve(kSealedJsonHeaderSize + ciphertext.size());
    append(blob, format.magic);
    append_u16_be(blob, format.version);
    append(blob, ByteView(salt));
    append(blob, ByteView(ciphertext));
    atomic_write_bytes(path, ByteView(blob));
}

}  // namespace i2pchat::storage
