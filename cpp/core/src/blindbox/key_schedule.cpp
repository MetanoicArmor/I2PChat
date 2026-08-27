#include "i2pchat/blindbox/key_schedule.hpp"

#include <algorithm>
#include <cctype>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

namespace i2pchat::blindbox {
namespace {

constexpr std::string_view kWhitespace = " \t\r\n\f\v";
constexpr std::string_view kB32Suffix = ".b32.i2p";

constexpr std::string_view kLookupLabel = "BLINDBOX_LOOKUP_V1";
constexpr std::string_view kBlobLabel = "BLINDBOX_BLOB_V1";
constexpr std::string_view kStateLabel = "BLINDBOX_STATE_V1";
constexpr std::string_view kGroupLookupLabel = "BLINDBOX_GROUP_LOOKUP_V1";
constexpr std::string_view kGroupBlobLabel = "BLINDBOX_GROUP_BLOB_V1";
constexpr std::string_view kGroupStateLabel = "BLINDBOX_GROUP_STATE_V1";

constexpr std::size_t kMinRootSecretSize = 16;

std::string trim_lower(std::string_view text) {
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    std::string value(text.substr(first, last - first + 1));
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

/// The index appears in the context as the hex of its 8-byte big-endian form,
/// not as decimal digits.
std::string index_hex(std::uint64_t index) {
    Bytes raw;
    append_u64_be(raw, index);
    return encoding::hex_encode(ByteView(raw));
}

void require_root_secret(ByteView root_secret) {
    if (root_secret.size() < kMinRootSecretSize) {
        throw BlindBoxError("root_secret must be at least 16 bytes");
    }
}

Bytes expand(ByteView prk, std::string_view label, const std::string& context,
             std::size_t length) {
    const std::string info = std::string(label) + "|" + context;
    return crypto::hkdf_expand(prk, as_bytes(info), length);
}

std::string join(const std::vector<std::string>& parts) {
    std::string out;
    for (std::size_t i = 0; i < parts.size(); ++i) {
        if (i > 0) {
            out += "|";
        }
        out += parts[i];
    }
    return out;
}

}  // namespace

std::string_view direction_name(Direction direction) {
    return direction == Direction::Send ? "send" : "recv";
}

Direction parse_direction(std::string_view text) {
    const std::string value = trim_lower(text);
    if (value == "send") {
        return Direction::Send;
    }
    if (value == "recv") {
        return Direction::Recv;
    }
    throw BlindBoxError("direction must be 'send' or 'recv'");
}

std::string normalize_blindbox_peer_id(std::string_view peer_id) {
    std::string value = trim_lower(peer_id);
    if (value.empty()) {
        throw BlindBoxError("Peer id cannot be empty");
    }
    if (value.size() > kB32Suffix.size() &&
        value.compare(value.size() - kB32Suffix.size(), kB32Suffix.size(), kB32Suffix) ==
            0) {
        value.resize(value.size() - kB32Suffix.size());
    }
    return value;
}

std::pair<std::string, std::string> canonical_pair(std::string_view local_peer_id,
                                                   std::string_view remote_peer_id) {
    const std::string local = normalize_blindbox_peer_id(local_peer_id);
    const std::string remote = normalize_blindbox_peer_id(remote_peer_id);
    if (local == remote) {
        throw BlindBoxError("Local and remote peer ids must differ");
    }
    return local < remote ? std::pair{local, remote} : std::pair{remote, local};
}

MessageKeys derive_message_keys(ByteView root_secret, std::string_view local_peer_id,
                                std::string_view remote_peer_id, Direction direction,
                                std::uint64_t index, std::uint64_t epoch) {
    require_root_secret(root_secret);

    const auto [low_id, high_id] = canonical_pair(local_peer_id, remote_peer_id);
    const std::string local = normalize_blindbox_peer_id(local_peer_id);

    // The label describes the flow between the ordered pair, so the sender and
    // the receiver of one message agree on it without exchanging anything.
    const bool local_is_low = local == low_id;
    const bool low_to_high = (direction == Direction::Send) == local_is_low;
    const std::string direction_label = low_to_high ? "LOW_TO_HIGH" : "HIGH_TO_LOW";

    const Bytes salt =
        crypto::sha256(as_bytes("BLINDBOX-SALT-V1|" + low_id + "|" + high_id));
    const Bytes prk = crypto::hkdf_extract(ByteView(salt), root_secret);

    const std::string context =
        join({low_id, high_id, direction_label, "epoch=" + std::to_string(epoch),
              index_hex(index)});

    MessageKeys keys;
    keys.lookup_key = expand(ByteView(prk), kLookupLabel, context, 32);
    keys.blob_key = expand(ByteView(prk), kBlobLabel, context, 32);
    keys.state_tag = expand(ByteView(prk), kStateLabel, context, 16);
    keys.lookup_token =
        encoding::hex_encode(ByteView(crypto::sha256(ByteView(keys.lookup_key))));
    keys.direction_label = direction_label;
    keys.index = index;
    keys.epoch = epoch;
    return keys;
}

GroupMessageKeys derive_group_message_keys(ByteView root_secret, std::string_view group_id,
                                           Direction direction, std::uint64_t index,
                                           std::uint64_t group_epoch,
                                           std::uint64_t root_epoch,
                                           std::string_view sender_id) {
    require_root_secret(root_secret);

    const std::string group = trim_lower(group_id);
    if (group.empty()) {
        throw BlindBoxError("Channel id cannot be empty");
    }
    const std::string sender = normalize_blindbox_peer_id(sender_id);
    const std::string direction_label =
        direction == Direction::Send ? "GROUP_SEND" : "GROUP_RECV";

    const Bytes salt =
        crypto::sha256(as_bytes("BLINDBOX-GROUP-SALT-V2|" + group + "|" + sender));
    const Bytes prk = crypto::hkdf_extract(ByteView(salt), root_secret);

    const std::string context =
        join({group, sender, direction_label, index_hex(index),
              "group_epoch=" + std::to_string(group_epoch),
              "root_epoch=" + std::to_string(root_epoch)});

    GroupMessageKeys keys;
    keys.lookup_key = expand(ByteView(prk), kGroupLookupLabel, context, 32);
    keys.blob_key = expand(ByteView(prk), kGroupBlobLabel, context, 32);
    keys.state_tag = expand(ByteView(prk), kGroupStateLabel, context, 16);
    keys.lookup_token =
        encoding::hex_encode(ByteView(crypto::sha256(ByteView(keys.lookup_key))));
    keys.direction_label = direction_label;
    keys.index = index;
    keys.group_epoch = group_epoch;
    keys.root_epoch = root_epoch;
    keys.sender_id = sender;
    return keys;
}

}  // namespace i2pchat::blindbox
