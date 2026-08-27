#include "i2pchat/blindbox/state.hpp"

#include <algorithm>
#include <chrono>
#include <fstream>

#include "i2pchat/blindbox/key_schedule.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"

namespace i2pchat::blindbox {
namespace {

std::int64_t system_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::string trim_lower(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    std::string result(text.substr(begin, end - begin + 1));
    std::transform(result.begin(), result.end(), result.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return result;
}

std::uint64_t json_uint(const nlohmann::json& value, const char* key,
                        std::uint64_t fallback = 0) {
    const auto found = value.find(key);
    if (found == value.end() || !found->is_number()) {
        return fallback;
    }
    // A negative index in a state file is corruption, not a value to preserve:
    // it would derive keys for a slot that cannot exist.
    if (found->is_number_integer() && found->get<std::int64_t>() < 0) {
        throw BlindBoxError(std::string("negative value for ") + key);
    }
    return found->get<std::uint64_t>();
}

std::int64_t json_int(const nlohmann::json& value, const char* key,
                      std::int64_t fallback = 0) {
    const auto found = value.find(key);
    if (found == value.end() || !found->is_number()) {
        return fallback;
    }
    return found->get<std::int64_t>();
}

std::string json_text(const nlohmann::json& value, const char* key) {
    const auto found = value.find(key);
    if (found == value.end() || !found->is_string()) {
        return {};
    }
    return found->get<std::string>();
}

Bytes as_owned(ByteView view) { return Bytes(view.begin(), view.end()); }

}  // namespace

void BlindBoxState::advance_recv_base() {
    // Settled indexes are dropped as the base passes them, where the reference
    // implementation keeps them forever. Nothing below the base is ever polled,
    // so the two behave identically — but a long-lived channel here does not
    // accumulate a state file that grows without bound.
    while (consumed_recv.contains(recv_base)) {
        consumed_recv.erase(recv_base);
        ++recv_base;
    }
}

void BlindBoxState::mark_consumed(std::uint64_t index, std::int64_t now) {
    consumed_recv.insert(index);
    advance_recv_base();
    updated_at = now != 0 ? now : system_seconds();
}

std::vector<std::uint64_t> BlindBoxState::pending_recv_indexes() const {
    std::vector<std::uint64_t> indexes;
    indexes.reserve(recv_window);
    for (std::size_t offset = 0; offset < recv_window; ++offset) {
        const std::uint64_t index = recv_base + offset;
        if (!consumed_recv.contains(index)) {
            indexes.push_back(index);
        }
    }
    return indexes;
}

nlohmann::json BlindBoxState::to_json() const {
    nlohmann::json payload = nlohmann::json::object();
    payload["version"] = std::string(kStateVersion);
    payload["send_index"] = send_index;
    payload["recv_base"] = recv_base;
    payload["recv_window"] = recv_window;
    // Sorted, which `std::set` gives for free, and which keeps the file stable
    // across writes so a diff means a real change.
    payload["consumed_recv"] = nlohmann::json::array();
    for (const std::uint64_t index : consumed_recv) {
        payload["consumed_recv"].push_back(index);
    }
    payload["updated_at"] = updated_at != 0 ? updated_at : system_seconds();
    return payload;
}

BlindBoxState BlindBoxState::from_json(const nlohmann::json& value) {
    if (!value.is_object()) {
        throw BlindBoxError("BlindBox state must be a JSON object");
    }
    if (json_text(value, "version") != std::string(kStateVersion)) {
        throw BlindBoxError("Unsupported BlindBox state version");
    }

    BlindBoxState state;
    state.send_index = json_uint(value, "send_index");
    state.recv_base = json_uint(value, "recv_base");
    state.recv_window = static_cast<std::size_t>(
        json_uint(value, "recv_window", kDefaultRecvWindow));
    if (state.recv_window < 1 || state.recv_window > kMaxRecvWindow) {
        throw BlindBoxError("recv_window must be in range 1..4096");
    }
    const auto consumed = value.find("consumed_recv");
    if (consumed != value.end() && consumed->is_array()) {
        for (const nlohmann::json& item : *consumed) {
            if (!item.is_number()) {
                continue;
            }
            if (item.is_number_integer() && item.get<std::int64_t>() < 0) {
                throw BlindBoxError("consumed_recv contains negative indexes");
            }
            state.consumed_recv.insert(item.get<std::uint64_t>());
        }
    }
    state.updated_at = json_int(value, "updated_at", system_seconds());
    state.advance_recv_base();
    return state;
}

std::string wrap_scope_for_peer(std::string_view peer_id) {
    std::string scope = trim_lower(peer_id);
    if (scope.empty()) {
        throw BlindBoxError("BlindBox peer id is not available");
    }
    static constexpr std::string_view kSuffix = ".b32.i2p";
    if (scope.size() > kSuffix.size() && scope.ends_with(kSuffix)) {
        scope.resize(scope.size() - kSuffix.size());
    }
    return scope;
}

std::string wrap_scope_for_group(std::string_view group_id) {
    const std::string trimmed = std::string(group_id);
    const auto begin = trimmed.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        throw BlindBoxError("Group id is required");
    }
    const auto end = trimmed.find_last_not_of(" \t\r\n");
    return "group:" + trimmed.substr(begin, end - begin + 1);
}

Bytes local_wrap_key(std::string_view profile, std::string_view scope,
                     ByteView signing_seed, int wrap_version) {
    crypto::init();
    // The scope is lowercased and stripped here too, so a caller that passes a
    // raw peer address gets the same key as one that normalised first.
    const std::string scope_norm =
        scope.starts_with("group:") ? std::string(scope) : wrap_scope_for_peer(scope);

    if (wrap_version == kLocalWrapVersionLegacy) {
        // v1 derives from the profile and scope alone. Anyone with the file can
        // recompute it, which is exactly why v2 exists; this path is kept only
        // to open state written before the change.
        Bytes salt_input = as_owned(as_bytes("BLINDBOX-LOCAL-WRAP-SALT|"));
        const Bytes profile_bytes = as_owned(as_bytes(profile));
        salt_input.insert(salt_input.end(), profile_bytes.begin(), profile_bytes.end());
        salt_input.push_back(static_cast<unsigned char>('|'));
        const Bytes scope_bytes = as_owned(as_bytes(scope_norm));
        salt_input.insert(salt_input.end(), scope_bytes.begin(), scope_bytes.end());

        const Bytes digest = crypto::sha256(ByteView(salt_input));
        const Bytes prk = crypto::hkdf_extract({}, ByteView(digest));
        return crypto::hkdf_expand(ByteView(prk), as_bytes("BLINDBOX-LOCAL-WRAP-KEY"), 32);
    }
    if (wrap_version != kLocalWrapVersionCurrent) {
        throw BlindBoxError("Unsupported BlindBox local wrap version: " +
                            std::to_string(wrap_version));
    }
    if (signing_seed.empty()) {
        throw BlindBoxError("Local signing seed is not initialized");
    }

    Bytes salt_input = as_owned(as_bytes("BLINDBOX-LOCAL-WRAP-SALT-V2|"));
    const Bytes profile_bytes = as_owned(as_bytes(profile));
    salt_input.insert(salt_input.end(), profile_bytes.begin(), profile_bytes.end());
    salt_input.push_back(static_cast<unsigned char>('|'));
    const Bytes scope_bytes = as_owned(as_bytes(scope_norm));
    salt_input.insert(salt_input.end(), scope_bytes.begin(), scope_bytes.end());

    const Bytes salt = crypto::sha256(ByteView(salt_input));
    const Bytes prk = crypto::hkdf_extract(ByteView(salt), signing_seed);

    Bytes info = as_owned(as_bytes("BLINDBOX-LOCAL-WRAP-KEY-V2|"));
    info.insert(info.end(), profile_bytes.begin(), profile_bytes.end());
    info.push_back(static_cast<unsigned char>('|'));
    info.insert(info.end(), scope_bytes.begin(), scope_bytes.end());
    return crypto::hkdf_expand(ByteView(prk), ByteView(info), 32);
}

std::string encrypt_root_secret(ByteView root_secret, std::string_view profile,
                                std::string_view scope, ByteView signing_seed) {
    const Bytes key =
        local_wrap_key(profile, scope, signing_seed, kLocalWrapVersionCurrent);
    return encoding::hex_encode(ByteView(crypto::encrypt_message(ByteView(key), root_secret)));
}

std::pair<Bytes, int> decrypt_root_secret(std::string_view encrypted_hex,
                                          std::string_view profile,
                                          std::string_view scope, ByteView signing_seed,
                                          std::optional<int> wrap_version) {
    const std::optional<Bytes> encrypted = encoding::hex_decode(encrypted_hex);
    if (!encrypted) {
        throw BlindBoxError("Wrapped BlindBox root secret is not valid hex");
    }

    std::vector<int> versions;
    if (wrap_version) {
        versions.push_back(*wrap_version);
    }
    versions.push_back(kLocalWrapVersionCurrent);
    versions.push_back(kLocalWrapVersionLegacy);

    std::vector<int> tried;
    for (const int version : versions) {
        if (std::find(tried.begin(), tried.end(), version) != tried.end()) {
            continue;
        }
        tried.push_back(version);
        Bytes key;
        try {
            key = local_wrap_key(profile, scope, signing_seed, version);
        } catch (const std::exception&) {
            // An unusable version — no signing seed for v2, say — is skipped so
            // the other one still gets its chance.
            continue;
        }
        if (const std::optional<Bytes> decrypted =
                crypto::decrypt_message(ByteView(key), ByteView(*encrypted))) {
            return {*decrypted, version};
        }
    }
    throw BlindBoxError("Failed to decrypt BlindBox root secret");
}

std::string peer_state_filename(std::string_view profile, std::string_view peer_id) {
    const std::string lowered = trim_lower(peer_id);
    if (lowered.empty()) {
        throw BlindBoxError("BlindBox peer id is not available");
    }
    std::string safe;
    safe.reserve(lowered.size());
    for (const char character : lowered) {
        const bool allowed = (character >= 'a' && character <= 'z') ||
                             (character >= '0' && character <= '9') ||
                             character == '.' || character == '_' || character == '-';
        safe.push_back(allowed ? character : '_');
    }
    return std::string(profile) + ".blindbox." + safe + ".json";
}

namespace {

template <typename Root>
std::vector<Root> prune_roots_impl(std::vector<Root> roots, std::size_t max_roots,
                                   std::int64_t now) {
    const std::int64_t moment = now != 0 ? now : system_seconds();
    std::vector<Root> kept;
    kept.reserve(roots.size());
    for (Root& root : roots) {
        if (root.secret.size() != 32) {
            continue;
        }
        if (root.expires_at != 0 && root.expires_at <= moment) {
            continue;
        }
        kept.push_back(std::move(root));
    }
    // Newest first, then truncate: an old root is only useful for messages
    // still in flight, and the newer ones are the likelier candidates.
    std::stable_sort(kept.begin(), kept.end(), [](const Root& left, const Root& right) {
        return left.expires_at > right.expires_at;
    });
    if (kept.size() > max_roots) {
        kept.resize(max_roots);
    }
    return kept;
}

nlohmann::json roots_to_json(const std::vector<PreviousRoot>& roots,
                             std::string_view profile, std::string_view scope,
                             ByteView signing_seed) {
    nlohmann::json array = nlohmann::json::array();
    for (const PreviousRoot& root : roots) {
        if (root.secret.size() != 32) {
            continue;
        }
        nlohmann::json item = nlohmann::json::object();
        item["epoch"] = root.epoch;
        item["expires_at"] = root.expires_at;
        item["secret_enc"] =
            encrypt_root_secret(ByteView(root.secret), profile, scope, signing_seed);
        array.push_back(std::move(item));
    }
    return array;
}

std::vector<PreviousRoot> roots_from_json(const nlohmann::json& value,
                                          std::string_view profile,
                                          std::string_view scope, ByteView signing_seed,
                                          int wrap_version) {
    std::vector<PreviousRoot> roots;
    if (!value.is_array()) {
        return roots;
    }
    for (const nlohmann::json& item : value) {
        if (!item.is_object()) {
            continue;
        }
        const std::string encrypted = json_text(item, "secret_enc");
        if (encrypted.empty()) {
            continue;
        }
        try {
            const auto [secret, _] = decrypt_root_secret(encrypted, profile, scope,
                                                         signing_seed, wrap_version);
            if (secret.size() != 32) {
                continue;
            }
            roots.push_back(PreviousRoot{json_uint(item, "epoch"), secret,
                                         json_int(item, "expires_at")});
        } catch (const std::exception&) {
            // One unreadable previous root must not cost the whole file: the
            // current root is what the channel needs to keep working.
            continue;
        }
    }
    return roots;
}

}  // namespace

std::vector<PreviousRoot> prune_previous_roots(std::vector<PreviousRoot> roots,
                                               std::size_t max_roots, std::int64_t now) {
    return prune_roots_impl(std::move(roots), max_roots, now);
}

std::vector<GroupPreviousRoot> prune_previous_roots(std::vector<GroupPreviousRoot> roots,
                                                    std::size_t max_roots,
                                                    std::int64_t now) {
    return prune_roots_impl(std::move(roots), max_roots, now);
}

nlohmann::json peer_snapshot_to_json(const PeerSnapshot& snapshot,
                                     std::string_view profile, ByteView signing_seed) {
    const std::string scope = wrap_scope_for_peer(snapshot.peer_id);
    nlohmann::json payload = snapshot.state.to_json();
    payload["blindbox_wrap_version"] = kLocalWrapVersionCurrent;
    if (snapshot.root_secret) {
        payload["blindbox_root_secret_enc"] = encrypt_root_secret(
            ByteView(*snapshot.root_secret), profile, scope, signing_seed);
    }
    payload["blindbox_root_epoch"] = snapshot.root_epoch;
    payload["blindbox_root_created_at"] = snapshot.root_created_at;
    payload["blindbox_root_send_index_base"] = snapshot.root_send_index_base;
    if (snapshot.pending_root_secret) {
        payload["blindbox_pending_root_secret_enc"] = encrypt_root_secret(
            ByteView(*snapshot.pending_root_secret), profile, scope, signing_seed);
    }
    payload["blindbox_pending_root_epoch"] = snapshot.pending_root_epoch;
    payload["blindbox_pending_root_created_at"] = snapshot.pending_root_created_at;
    payload["blindbox_pending_root_send_index_base"] =
        snapshot.pending_root_send_index_base;
    payload["blindbox_prev_roots"] =
        roots_to_json(snapshot.prev_roots, profile, scope, signing_seed);
    return payload;
}

PeerSnapshot peer_snapshot_from_json(const nlohmann::json& value,
                                     std::string_view peer_id, std::string_view profile,
                                     ByteView signing_seed) {
    if (!value.is_object()) {
        throw BlindBoxError("BlindBox state must be a JSON object");
    }
    const std::string scope = wrap_scope_for_peer(peer_id);

    PeerSnapshot snapshot;
    snapshot.peer_id = wrap_scope_for_peer(peer_id);
    snapshot.state = BlindBoxState::from_json(value);
    // An absent version means a file from before versioning, which is v1.
    snapshot.wrap_version = static_cast<int>(
        json_uint(value, "blindbox_wrap_version", kLocalWrapVersionLegacy));

    const std::string root_enc = json_text(value, "blindbox_root_secret_enc");
    if (!root_enc.empty()) {
        const auto [secret, used] = decrypt_root_secret(root_enc, profile, scope,
                                                        signing_seed, snapshot.wrap_version);
        snapshot.root_secret = secret;
        snapshot.wrap_version = used;
    }
    snapshot.root_epoch = json_uint(value, "blindbox_root_epoch");
    snapshot.root_created_at =
        json_int(value, "blindbox_root_created_at", system_seconds());
    snapshot.root_send_index_base =
        json_uint(value, "blindbox_root_send_index_base", snapshot.state.send_index);

    const std::string pending_enc = json_text(value, "blindbox_pending_root_secret_enc");
    if (!pending_enc.empty()) {
        const auto [secret, _] = decrypt_root_secret(pending_enc, profile, scope,
                                                     signing_seed, snapshot.wrap_version);
        snapshot.pending_root_secret = secret;
    }
    snapshot.pending_root_epoch = json_uint(value, "blindbox_pending_root_epoch");
    snapshot.pending_root_created_at =
        json_int(value, "blindbox_pending_root_created_at", system_seconds());
    snapshot.pending_root_send_index_base = json_uint(
        value, "blindbox_pending_root_send_index_base", snapshot.state.send_index);

    const auto prev = value.find("blindbox_prev_roots");
    if (prev != value.end()) {
        snapshot.prev_roots =
            roots_from_json(*prev, profile, scope, signing_seed, snapshot.wrap_version);
    }
    return snapshot;
}

PeerSnapshot load_peer_snapshot(const std::filesystem::path& path,
                                std::string_view peer_id, std::string_view profile,
                                ByteView signing_seed) {
    PeerSnapshot empty;
    empty.peer_id = wrap_scope_for_peer(peer_id);

    std::error_code error;
    if (!std::filesystem::exists(path, error) || error) {
        return empty;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open()) {
        return empty;
    }
    const std::string contents{std::istreambuf_iterator<char>(in),
                               std::istreambuf_iterator<char>()};
    if (contents.empty()) {
        return empty;
    }
    return peer_snapshot_from_json(nlohmann::json::parse(contents), peer_id, profile,
                                   signing_seed);
}

void save_peer_snapshot(const std::filesystem::path& path, const PeerSnapshot& snapshot,
                        std::string_view profile, ByteView signing_seed) {
    if (!snapshot.root_secret && !snapshot.pending_root_secret) {
        // Nothing to protect yet. Writing here would replace "no channel" with
        // "a channel with no root", which reads as an established peer.
        return;
    }
    storage::atomic_write_json(path,
                               peer_snapshot_to_json(snapshot, profile, signing_seed));
}

nlohmann::json group_snapshot_to_json(const GroupSnapshot& snapshot,
                                      std::string_view profile, ByteView signing_seed) {
    const std::string scope = wrap_scope_for_group(snapshot.group_id);
    nlohmann::json payload = nlohmann::json::object();
    payload["channel_id"] = snapshot.channel_id;
    payload["group_epoch"] = snapshot.group_epoch;
    payload["state"] = snapshot.state.to_json();
    payload["root_secret_enc"] =
        snapshot.root_secret
            ? nlohmann::json(encrypt_root_secret(ByteView(*snapshot.root_secret), profile,
                                                 scope, signing_seed))
            : nlohmann::json(nullptr);
    payload["root_epoch"] = snapshot.root_epoch;
    payload["root_created_at"] = snapshot.root_created_at;
    payload["root_send_index_base"] = snapshot.root_send_index_base;
    payload["pending_root_secret_enc"] =
        snapshot.pending_root_secret
            ? nlohmann::json(encrypt_root_secret(ByteView(*snapshot.pending_root_secret),
                                                 profile, scope, signing_seed))
            : nlohmann::json(nullptr);
    payload["pending_root_epoch"] = snapshot.pending_root_epoch;
    payload["pending_root_created_at"] = snapshot.pending_root_created_at;
    payload["pending_root_send_index_base"] = snapshot.pending_root_send_index_base;
    payload["pending_root_target_members"] = snapshot.pending_root_target_members;
    payload["pending_root_acked_members"] = nlohmann::json::array();
    for (const std::string& member : snapshot.pending_root_acked_members) {
        payload["pending_root_acked_members"].push_back(member);
    }
    payload["prev_roots"] = nlohmann::json::array();
    for (const GroupPreviousRoot& root : snapshot.prev_roots) {
        if (root.secret.size() != 32) {
            continue;
        }
        nlohmann::json item = nlohmann::json::object();
        item["group_epoch"] = root.group_epoch;
        item["root_epoch"] = root.root_epoch;
        item["expires_at"] = root.expires_at;
        item["secret_enc"] =
            encrypt_root_secret(ByteView(root.secret), profile, scope, signing_seed);
        payload["prev_roots"].push_back(std::move(item));
    }
    return payload;
}

GroupSnapshot group_snapshot_from_json(const nlohmann::json& value,
                                       std::string_view group_id,
                                       std::string_view profile, ByteView signing_seed) {
    if (!value.is_object()) {
        throw BlindBoxError("Group BlindBox channel must be a JSON object");
    }
    const std::string scope = wrap_scope_for_group(group_id);

    GroupSnapshot snapshot;
    snapshot.group_id = std::string(group_id);
    snapshot.channel_id = json_text(value, "channel_id");
    snapshot.group_epoch = json_uint(value, "group_epoch");

    const auto state = value.find("state");
    if (state != value.end() && state->is_object()) {
        snapshot.state = BlindBoxState::from_json(*state);
    }

    const std::string root_enc = json_text(value, "root_secret_enc");
    if (!root_enc.empty()) {
        const auto [secret, used] =
            decrypt_root_secret(root_enc, profile, scope, signing_seed);
        snapshot.root_secret = secret;
        snapshot.wrap_version = used;
    }
    snapshot.root_epoch = json_uint(value, "root_epoch");
    snapshot.root_created_at = json_int(value, "root_created_at");
    snapshot.root_send_index_base = json_uint(value, "root_send_index_base");

    const std::string pending_enc = json_text(value, "pending_root_secret_enc");
    if (!pending_enc.empty()) {
        const auto [secret, _] =
            decrypt_root_secret(pending_enc, profile, scope, signing_seed);
        snapshot.pending_root_secret = secret;
    }
    snapshot.pending_root_epoch = json_uint(value, "pending_root_epoch");
    snapshot.pending_root_created_at = json_int(value, "pending_root_created_at");
    snapshot.pending_root_send_index_base =
        json_uint(value, "pending_root_send_index_base");

    const auto targets = value.find("pending_root_target_members");
    if (targets != value.end() && targets->is_array()) {
        for (const nlohmann::json& member : *targets) {
            if (member.is_string()) {
                snapshot.pending_root_target_members.push_back(member.get<std::string>());
            }
        }
    }
    const auto acked = value.find("pending_root_acked_members");
    if (acked != value.end() && acked->is_array()) {
        for (const nlohmann::json& member : *acked) {
            if (member.is_string()) {
                snapshot.pending_root_acked_members.insert(member.get<std::string>());
            }
        }
    }

    const auto prev = value.find("prev_roots");
    if (prev != value.end() && prev->is_array()) {
        for (const nlohmann::json& item : *prev) {
            if (!item.is_object()) {
                continue;
            }
            const std::string encrypted = json_text(item, "secret_enc");
            if (encrypted.empty()) {
                continue;
            }
            try {
                const auto [secret, _] =
                    decrypt_root_secret(encrypted, profile, scope, signing_seed);
                if (secret.size() != 32) {
                    continue;
                }
                snapshot.prev_roots.push_back(
                    GroupPreviousRoot{json_uint(item, "group_epoch"),
                                      json_uint(item, "root_epoch"), secret,
                                      json_int(item, "expires_at")});
            } catch (const std::exception&) {
                continue;
            }
        }
    }
    return snapshot;
}

}  // namespace i2pchat::blindbox
