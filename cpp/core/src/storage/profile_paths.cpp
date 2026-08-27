#include "i2pchat/storage/profile_paths.hpp"

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include <cctype>

namespace i2pchat::storage {
namespace {

std::string sha256_hex(std::string_view text) {
    return encoding::hex_encode(ByteView(crypto::sha256(as_bytes(text))));
}

}  // namespace

std::string history_peer_key(std::string_view peer_addr) {
    const auto first = peer_addr.find_first_not_of(" \t\r\n\f\v");
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = peer_addr.find_last_not_of(" \t\r\n\f\v");
    std::string key(peer_addr.substr(first, last - first + 1));
    for (char& ch : key) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return key;
}

std::string peer_file_id(std::string_view peer_addr) {
    // Deliberately *not* the strict address normaliser: the reference
    // implementation hashes the address after nothing but trim and lowercase, so
    // a `.b32.i2p` suffix produces a different history file there. Canonicalising
    // harder here would make us look in the wrong place for a file Python wrote.
    return sha256_hex(history_peer_key(peer_addr));
}

std::string legacy_peer_file_id(std::string_view peer_addr) {
    return peer_file_id(peer_addr).substr(0, kLegacyPeerIdHexLength);
}

std::string group_token(std::string_view group_id) { return sha256_hex(group_id); }

ProfilePaths::ProfilePaths(std::filesystem::path data_dir, std::string profile)
    : data_dir_(std::move(data_dir)), profile_(std::move(profile)) {}

std::filesystem::path ProfilePaths::identity_dat() const {
    return data_dir_ / (profile_ + ".dat");
}

std::filesystem::path ProfilePaths::identity_dat_wrap() const {
    return data_dir_ / (profile_ + ".dat.wrap");
}

std::filesystem::path ProfilePaths::trust_store() const {
    return data_dir_ / (profile_ + ".trust.json");
}

std::filesystem::path ProfilePaths::contacts() const {
    return data_dir_ / (profile_ + ".contacts.json");
}

std::filesystem::path ProfilePaths::compose_drafts() const {
    return data_dir_ / (profile_ + ".compose_drafts.json");
}

std::filesystem::path ProfilePaths::blindbox_replicas() const {
    return data_dir_ / (profile_ + ".blindbox_replicas.json");
}

std::filesystem::path ProfilePaths::chat_history(std::string_view peer_addr) const {
    return data_dir_ / (profile_ + ".history." + peer_file_id(peer_addr) + ".enc");
}

std::filesystem::path ProfilePaths::legacy_chat_history(
    std::string_view peer_addr) const {
    return data_dir_ / (profile_ + ".history." + legacy_peer_file_id(peer_addr) + ".enc");
}

std::filesystem::path ProfilePaths::group_store(std::string_view group_id) const {
    return data_dir_ / (profile_ + ".group." + group_token(group_id) + ".json");
}

SealedJsonFormat chat_history_format(std::string_view peer_addr) {
    // The scope is the address itself, not its digest: the digest names the file,
    // the address keys it.
    return SealedJsonFormat{"I2CH", "I2PCHAT-HISTORY", 2, history_peer_key(peer_addr)};
}

SealedJsonFormat group_store_format(std::string_view group_id) {
    return group_store_format_for_token(group_token(group_id));
}

SealedJsonFormat group_store_format_for_token(std::string_view token) {
    return SealedJsonFormat{"I2GS", "I2PCHAT-GROUPSTORE", 1, std::string(token)};
}

}  // namespace i2pchat::storage
