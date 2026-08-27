#include "i2pchat/storage/profile_dat.hpp"

#include <algorithm>
#include <cctype>
#include <sstream>
#include <vector>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/keyring.hpp"

namespace i2pchat::storage {
namespace {

std::string trim(std::string_view text) {
    std::size_t begin = 0;
    while (begin < text.size() &&
           std::isspace(static_cast<unsigned char>(text[begin])) != 0) {
        ++begin;
    }
    std::size_t end = text.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
        --end;
    }
    return std::string(text.substr(begin, end - begin));
}

std::vector<std::string> non_empty_lines(std::string_view text) {
    std::vector<std::string> lines;
    std::istringstream stream{std::string(text)};
    std::string line;
    while (std::getline(stream, line)) {
        const std::string trimmed = trim(line);
        if (!trimmed.empty()) {
            lines.push_back(trimmed);
        }
    }
    return lines;
}

/// The wrap key travels as standard base64 of 32 raw bytes, in both the keyring
/// and the sidecar, because that is what Python's implementation writes.
std::optional<Bytes> decode_wrap_key(std::string_view token) {
    const std::optional<Bytes> decoded = encoding::base64_decode(trim(token));
    if (!decoded.has_value() || decoded->size() != 32) {
        return std::nullopt;
    }
    return decoded;
}

std::optional<Bytes> keyring_wrap_key(std::string_view profile) {
    const std::optional<std::string> stored =
        keyring::get(kKeyringService, dat_wrap_keyring_account(profile));
    if (!stored.has_value()) {
        return std::nullopt;
    }
    return decode_wrap_key(*stored);
}

bool store_keyring_wrap_key(std::string_view profile, ByteView wrap_key) {
    return keyring::set(kKeyringService, dat_wrap_keyring_account(profile),
                        encoding::base64_encode(wrap_key));
}

std::optional<Bytes> read_wrap_sidecar(const std::filesystem::path& path) {
    if (!std::filesystem::is_regular_file(path)) {
        return std::nullopt;
    }
    try {
        return decode_wrap_key(to_string(ByteView(read_file(path))));
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

void write_wrap_sidecar(const std::filesystem::path& path, ByteView wrap_key) {
    atomic_write_text(path, encoding::base64_encode(wrap_key) + "\n");
}

}  // namespace

bool is_encrypted_profile_dat(ByteView raw) {
    return raw.size() >= kProfileDatHeaderSize &&
           std::equal(kProfileDatMagic.begin(), kProfileDatMagic.end(), raw.begin());
}

std::filesystem::path profile_dat_wrap_path(const std::filesystem::path& profile_data_dir,
                                            std::string_view profile) {
    return profile_data_dir / (std::string(profile) + ".dat.wrap");
}

std::string dat_wrap_keyring_account(std::string_view profile) {
    return std::string(profile) + std::string(kDatWrapKeyringSuffix);
}

Bytes get_or_create_dat_wrap_key(std::string_view profile,
                                 const std::filesystem::path& profile_data_dir) {
    if (const std::optional<Bytes> existing = keyring_wrap_key(profile)) {
        return *existing;
    }

    const std::filesystem::path sidecar = profile_dat_wrap_path(profile_data_dir, profile);
    if (const std::optional<Bytes> existing = read_wrap_sidecar(sidecar)) {
        // Promote the sidecar into the keyring, where the OS can protect it.
        (void)store_keyring_wrap_key(profile, ByteView(*existing));
        return *existing;
    }

    const Bytes wrap_key = crypto::random_bytes(32);
    (void)store_keyring_wrap_key(profile, ByteView(wrap_key));
    write_wrap_sidecar(sidecar, ByteView(wrap_key));
    return wrap_key;
}

std::optional<Bytes> load_dat_wrap_key(std::string_view profile,
                                       const std::filesystem::path& profile_data_dir) {
    if (const std::optional<Bytes> existing = keyring_wrap_key(profile)) {
        return existing;
    }
    return read_wrap_sidecar(profile_dat_wrap_path(profile_data_dir, profile));
}

Bytes derive_profile_dat_file_key(ByteView wrap_key, ByteView salt) {
    const Bytes prk = crypto::hkdf_extract(as_bytes(kProfileDatDomain), wrap_key);
    const Bytes profile_key = crypto::hkdf_expand(
        ByteView(prk), as_bytes(std::string(kProfileDatDomain) + "|profile-key"), 32);
    const Bytes prk2 = crypto::hkdf_extract(salt, ByteView(profile_key));
    return crypto::hkdf_expand(
        ByteView(prk2), as_bytes(std::string(kProfileDatDomain) + "|file-key"), 32);
}

Bytes encrypt_profile_dat(std::string_view private_key_base64, ByteView wrap_key) {
    const std::string key = trim(private_key_base64);
    if (key.empty()) {
        throw ProfileDatError("private_key_base64 is empty");
    }
    const Bytes salt = crypto::random_bytes(kProfileDatSaltSize);
    const Bytes file_key = derive_profile_dat_file_key(wrap_key, ByteView(salt));
    const Bytes ciphertext =
        crypto::encrypt_message(ByteView(file_key), as_bytes(key + "\n"));

    Bytes blob;
    blob.reserve(kProfileDatHeaderSize + ciphertext.size());
    append(blob, kProfileDatMagic);
    append_u16_be(blob, kProfileDatVersion);
    append(blob, ByteView(salt));
    append(blob, ByteView(ciphertext));
    return blob;
}

std::string decrypt_profile_dat(ByteView raw, ByteView wrap_key) {
    if (!is_encrypted_profile_dat(raw)) {
        throw ProfileDatError("Not an encrypted profile .dat");
    }
    const std::uint16_t version = read_u16_be(raw.subspan(4, 2));
    if (version != kProfileDatVersion) {
        throw ProfileDatError("Unsupported encrypted profile .dat version " +
                              std::to_string(version));
    }

    const ByteView salt = raw.subspan(6, kProfileDatSaltSize);
    const ByteView ciphertext = raw.subspan(kProfileDatHeaderSize);
    const Bytes file_key = derive_profile_dat_file_key(wrap_key, salt);

    const std::optional<Bytes> plaintext =
        crypto::decrypt_message(ByteView(file_key), ciphertext);
    if (!plaintext.has_value()) {
        throw ProfileDatError(
            "Profile .dat decryption failed (wrong wrap key or tampered)");
    }

    const std::vector<std::string> lines =
        non_empty_lines(to_string(ByteView(*plaintext)));
    if (lines.empty()) {
        throw ProfileDatError("Decrypted profile .dat is empty");
    }
    return lines.front();
}

ProfileDatContents parse_plaintext_profile_dat(std::string_view text,
                                               const PeerAddressPredicate& is_peer) {
    ProfileDatContents contents;
    const std::vector<std::string> lines = non_empty_lines(text);
    if (lines.empty()) {
        return contents;
    }

    const auto peer_check = [&is_peer](const std::string& line) {
        return is_peer ? is_peer(line) : false;
    };

    if (!peer_check(lines[0])) {
        contents.private_key_base64 = lines[0];
        if (lines.size() > 1 && peer_check(lines[1])) {
            contents.legacy_peer = lines[1];
        }
    } else {
        // A keyring-only profile: the file held nothing but the locked peer.
        contents.legacy_peer = lines[0];
    }
    return contents;
}

ProfileDatContents read_profile_dat_file(const std::filesystem::path& path,
                                         std::string_view profile,
                                         const std::filesystem::path& profile_data_dir,
                                         const PeerAddressPredicate& is_peer,
                                         bool create_wrap_key) {
    ProfileDatContents contents;
    if (!std::filesystem::is_regular_file(path)) {
        return contents;
    }
    const Bytes raw = read_file(path);
    if (raw.empty()) {
        return contents;
    }

    if (is_encrypted_profile_dat(ByteView(raw))) {
        const std::optional<Bytes> wrap =
            create_wrap_key
                ? std::optional<Bytes>(get_or_create_dat_wrap_key(profile, profile_data_dir))
                : load_dat_wrap_key(profile, profile_data_dir);
        if (!wrap.has_value()) {
            throw ProfileDatError(
                "Encrypted profile .dat at " + path.string() +
                " but no wrap key is available (no keyring entry and no .dat.wrap)");
        }
        contents.private_key_base64 =
            decrypt_profile_dat(ByteView(raw), ByteView(*wrap));
        return contents;
    }

    contents = parse_plaintext_profile_dat(to_string(ByteView(raw)), is_peer);
    contents.was_plaintext = true;
    return contents;
}

void write_encrypted_profile_dat(const std::filesystem::path& path,
                                 std::string_view private_key_base64,
                                 std::string_view profile,
                                 const std::filesystem::path& profile_data_dir) {
    const Bytes wrap = get_or_create_dat_wrap_key(profile, profile_data_dir);

    // Keep the sidecar in step with the key actually in use, so a copied profile
    // directory stays openable on a machine with no keyring.
    const std::filesystem::path sidecar = profile_dat_wrap_path(profile_data_dir, profile);
    if (read_wrap_sidecar(sidecar) != std::optional<Bytes>(wrap)) {
        write_wrap_sidecar(sidecar, ByteView(wrap));
    }

    atomic_write_bytes(path,
                       ByteView(encrypt_profile_dat(private_key_base64, ByteView(wrap))));
}

}  // namespace i2pchat::storage
