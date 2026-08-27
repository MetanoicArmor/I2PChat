#include "i2pchat/runtime/identity.hpp"

#include <fstream>
#include <string>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/keyring.hpp"
#include "i2pchat/storage/profile_dat.hpp"

namespace asio = boost::asio;

namespace i2pchat::runtime {
namespace {

std::filesystem::path signing_seed_path(const storage::ProfilePaths& paths) {
    return paths.data_dir() / (paths.profile() + ".signing");
}

std::optional<Bytes> parse_seed(std::string_view text) {
    std::string trimmed(text);
    while (!trimmed.empty() && (trimmed.back() == '\n' || trimmed.back() == '\r' ||
                               trimmed.back() == ' ' || trimmed.back() == '\t')) {
        trimmed.pop_back();
    }
    const std::optional<Bytes> decoded = encoding::hex_decode(trimmed);
    if (!decoded.has_value() || decoded->size() != 32) {
        return std::nullopt;
    }
    return decoded;
}

std::optional<std::string> read_text_file(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return std::nullopt;
    }
    return std::string{std::istreambuf_iterator<char>(stream),
                       std::istreambuf_iterator<char>()};
}

}  // namespace

std::string signing_keyring_account(std::string_view profile) {
    return std::string(profile) + std::string(kSigningKeyringSuffix);
}

Bytes load_or_create_signing_seed(const storage::ProfilePaths& paths) {
    crypto::init();
    if (paths.profile() == kTransientProfile) {
        return crypto::random_bytes(32);
    }

    const std::string account = signing_keyring_account(paths.profile());
    if (const std::optional<std::string> stored =
            storage::keyring::get(storage::kKeyringService, account)) {
        if (const std::optional<Bytes> seed = parse_seed(*stored)) {
            return *seed;
        }
        // A malformed entry is left in place rather than deleted: it may be
        // another application's, and overwriting it would be worse than
        // falling through to the file.
    }

    const std::filesystem::path path = signing_seed_path(paths);
    if (const std::optional<std::string> text = read_text_file(path)) {
        if (const std::optional<Bytes> seed = parse_seed(*text)) {
            return *seed;
        }
    }

    const Bytes seed = crypto::random_bytes(32);
    const std::string hex = encoding::hex_encode(ByteView(seed));
    if (!storage::keyring::set(storage::kKeyringService, account, hex)) {
        storage::atomic_write_text(path, hex);
    }
    return seed;
}

std::optional<sam::Destination> load_destination(const storage::ProfilePaths& paths) {
    const std::filesystem::path path = paths.identity_dat();
    if (!std::filesystem::exists(path)) {
        return std::nullopt;
    }

    const storage::ProfileDatContents contents = storage::read_profile_dat_file(
        path, paths.profile(), paths.data_dir(),
        // Legacy files stored a peer address on one of the lines; a private key
        // is far longer than any address, which is how the two are told apart.
        [](const std::string& line) { return line.size() < 600; });
    if (!contents.private_key_base64.has_value()) {
        return std::nullopt;
    }

    sam::Destination destination =
        sam::Destination::from_private_base64(*contents.private_key_base64);
    if (contents.was_plaintext) {
        storage::write_encrypted_profile_dat(path, *contents.private_key_base64,
                                             paths.profile(), paths.data_dir());
    }
    return destination;
}

asio::awaitable<sam::Destination> create_destination(sam::SamSession& session,
                                                     const storage::ProfilePaths& paths,
                                                     bool persist) {
    const sam::Destination destination = co_await session.generate_destination();
    if (persist && paths.profile() != kTransientProfile) {
        std::filesystem::create_directories(paths.data_dir());
        storage::write_encrypted_profile_dat(paths.identity_dat(),
                                             destination.private_key_base64(),
                                             paths.profile(), paths.data_dir());
    }
    co_return destination;
}

ProfileIdentity identity_from(std::string profile, const sam::Destination& destination,
                              ByteView signing_seed) {
    crypto::init();
    ProfileIdentity identity;
    identity.profile = std::move(profile);
    identity.destination_base64 = destination.private_key_base64();
    identity.public_destination_base64 = destination.base64();
    identity.local_addr = destination.base32();
    identity.signing_seed = Bytes(signing_seed.begin(), signing_seed.end());
    identity.signing_public = crypto::get_verify_key_from_seed(signing_seed);
    identity.identity_key = destination.private_key();
    return identity;
}

asio::awaitable<ProfileIdentity> load_identity(sam::SamSession& session,
                                               const storage::ProfilePaths& paths) {
    const Bytes seed = load_or_create_signing_seed(paths);
    std::optional<sam::Destination> destination = load_destination(paths);
    if (!destination.has_value()) {
        destination = co_await create_destination(session, paths);
    }
    co_return identity_from(paths.profile(), *destination, ByteView(seed));
}

}  // namespace i2pchat::runtime
