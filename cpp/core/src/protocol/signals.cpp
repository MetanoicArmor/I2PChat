#include "i2pchat/protocol/signals.hpp"

#include <charconv>
#include <vector>

#include "i2pchat/encoding.hpp"

namespace i2pchat::protocol {
namespace {

constexpr std::size_t kRootSecretSize = 32;

std::string trim(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

std::vector<std::string> split(std::string_view text, char separator) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (true) {
        const std::size_t position = text.find(separator, start);
        if (position == std::string_view::npos) {
            parts.emplace_back(text.substr(start));
            return parts;
        }
        parts.emplace_back(text.substr(start, position - start));
        start = position + 1;
    }
}

std::optional<std::uint64_t> parse_uint(std::string_view text) {
    const std::string trimmed = trim(text);
    if (trimmed.empty()) {
        return std::nullopt;
    }
    std::uint64_t value = 0;
    const auto* const begin = trimmed.data();
    const auto* const end = begin + trimmed.size();
    const auto result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{} || result.ptr != end) {
        return std::nullopt;
    }
    return value;
}

/// The tail of `payload` after `marker`, or nothing when the marker is absent.
///
/// The reference dispatches on substring containment rather than a prefix
/// match, so a signal is recognised wherever its marker appears in the body.
/// That is reproduced here because the two implementations have to agree on
/// which signals they honour.
std::optional<std::string> tail_after(std::string_view payload, std::string_view marker) {
    const std::size_t position = payload.find(marker);
    if (position == std::string_view::npos) {
        return std::nullopt;
    }
    return trim(payload.substr(position + marker.size()));
}

}  // namespace

std::string signal_body(std::string_view payload) {
    return std::string(kSignalPrefix) + std::string(payload);
}

std::optional<std::string> signal_payload(std::string_view body) {
    const std::size_t position = body.find(kSignalPrefix);
    if (position == std::string_view::npos) {
        return std::nullopt;
    }
    return trim(body.substr(position + kSignalPrefix.size()));
}

Signal parse_signal(std::string_view body) {
    Signal signal;
    const std::optional<std::string> payload = signal_payload(body);
    if (!payload) {
        return signal;
    }
    signal.payload = *payload;

    // The order matters: "BLINDBOX_ROOT|" is a substring of
    // "GROUP_BLINDBOX_ROOT|", so the group forms have to be tested first.
    if (const auto tail = tail_after(signal.payload, "GROUP_BLINDBOX_ROOT|")) {
        signal.kind = SignalKind::GroupBlindBoxRoot;
        const std::vector<std::string> parts = split(*tail, '|');
        if (parts.size() == 4) {
            const auto group_epoch = parse_uint(parts[1]);
            const auto root_epoch = parse_uint(parts[2]);
            const auto secret = encoding::hex_decode(trim(parts[3]));
            signal.group_id = trim(parts[0]);
            if (!signal.group_id.empty() && group_epoch && root_epoch && secret &&
                secret->size() == kRootSecretSize) {
                signal.epoch = *group_epoch;
                signal.root_epoch = *root_epoch;
                signal.root_secret = *secret;
                signal.well_formed = true;
            }
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "GROUP_BLINDBOX_ROOT_ACK|")) {
        signal.kind = SignalKind::GroupBlindBoxRootAck;
        const std::vector<std::string> parts = split(*tail, '|');
        if (parts.size() == 3) {
            const auto group_epoch = parse_uint(parts[1]);
            const auto root_epoch = parse_uint(parts[2]);
            signal.group_id = trim(parts[0]);
            if (!signal.group_id.empty() && group_epoch && root_epoch) {
                signal.epoch = *group_epoch;
                signal.root_epoch = *root_epoch;
                signal.well_formed = true;
            }
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "BLINDBOX_ROOT|")) {
        signal.kind = SignalKind::BlindBoxRoot;
        const std::vector<std::string> parts = split(*tail, '|');
        if (parts.size() == 2) {
            const auto epoch = parse_uint(parts[0]);
            const auto secret = encoding::hex_decode(trim(parts[1]));
            if (epoch && secret && secret->size() == kRootSecretSize) {
                signal.epoch = *epoch;
                signal.root_secret = *secret;
                signal.well_formed = true;
            }
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "BLINDBOX_ROOT_ACK|")) {
        signal.kind = SignalKind::BlindBoxRootAck;
        if (const auto epoch = parse_uint(*tail)) {
            signal.epoch = *epoch;
            signal.well_formed = true;
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "MSG_ACK|")) {
        signal.kind = SignalKind::MsgAck;
        const std::vector<std::string> parts = split(*tail, '|');
        if (const auto id = parse_uint(parts.front())) {
            signal.message_id = *id;
            signal.well_formed = true;
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "IMG_ACK|")) {
        signal.kind = SignalKind::ImgAck;
        const std::vector<std::string> parts = split(*tail, '|');
        signal.name = trim(parts.front());
        // An ACK without an id cannot be matched to a message, and acting on
        // the name alone would let one transfer confirm another.
        if (parts.size() > 1) {
            if (const auto id = parse_uint(parts[1])) {
                signal.message_id = *id;
                signal.well_formed = !signal.name.empty();
            }
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "FILE_ACK|")) {
        signal.kind = SignalKind::FileAck;
        const std::vector<std::string> parts = split(*tail, '|');
        signal.name = trim(parts.front());
        if (parts.size() > 1) {
            if (const auto id = parse_uint(parts[1])) {
                signal.message_id = *id;
                signal.well_formed = !signal.name.empty();
            }
        }
        return signal;
    }
    if (const auto tail = tail_after(signal.payload, "REJECT_FILE|")) {
        signal.kind = SignalKind::RejectFile;
        signal.name = trim(split(*tail, '|').front());
        signal.well_formed = true;
        return signal;
    }
    if (signal.payload.find("QUIT") != std::string::npos) {
        signal.kind = SignalKind::Quit;
        signal.well_formed = true;
        return signal;
    }
    if (signal.payload.find("ABORT_FILE") != std::string::npos) {
        signal.kind = SignalKind::AbortFile;
        signal.well_formed = true;
        return signal;
    }
    return signal;
}

bool honoured_before_handshake(const Signal& signal) {
    return signal.kind == SignalKind::Quit;
}

std::string build_msg_ack(std::uint64_t message_id) {
    return "MSG_ACK|" + std::to_string(message_id);
}

std::string build_file_ack(std::string_view filename, std::uint64_t message_id) {
    return "FILE_ACK|" + std::string(filename) + "|" + std::to_string(message_id);
}

std::string build_image_ack(std::string_view filename, std::uint64_t message_id) {
    return "IMG_ACK|" + std::string(filename) + "|" + std::to_string(message_id);
}

std::string build_reject_file(std::string_view filename) {
    return "REJECT_FILE|" + std::string(filename);
}

std::string build_abort_file() { return "ABORT_FILE"; }

std::string build_quit() { return "QUIT"; }

std::string build_blindbox_root(std::uint64_t epoch, ByteView root_secret) {
    return "BLINDBOX_ROOT|" + std::to_string(epoch) + "|" +
           encoding::hex_encode(root_secret);
}

std::string build_blindbox_root_ack(std::uint64_t epoch) {
    return "BLINDBOX_ROOT_ACK|" + std::to_string(epoch);
}

std::string build_group_blindbox_root(std::string_view group_id, std::uint64_t group_epoch,
                                      std::uint64_t root_epoch, ByteView root_secret) {
    return "GROUP_BLINDBOX_ROOT|" + std::string(group_id) + "|" +
           std::to_string(group_epoch) + "|" + std::to_string(root_epoch) + "|" +
           encoding::hex_encode(root_secret);
}

std::string build_group_blindbox_root_ack(std::string_view group_id,
                                          std::uint64_t group_epoch,
                                          std::uint64_t root_epoch) {
    return "GROUP_BLINDBOX_ROOT_ACK|" + std::string(group_id) + "|" +
           std::to_string(group_epoch) + "|" + std::to_string(root_epoch);
}

}  // namespace i2pchat::protocol
