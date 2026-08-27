#include "i2pchat/groups/wire.hpp"

#include <algorithm>
#include <cctype>

#include "i2pchat/canonical_json.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

namespace i2pchat::groups {
namespace {

std::string trim(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    return std::string(text.substr(first, last - first + 1));
}

std::string trim_lower(std::string_view text) {
    std::string value = trim(text);
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

/// The title is written as `null` rather than `""` when unset, because the
/// reference model stores it as an optional and the difference is inside the
/// signed bytes.
nlohmann::json title_field(const GroupState& state) {
    return state.title().empty() ? nlohmann::json() : nlohmann::json(state.title());
}

/// The fields both versions share, in the shape they are serialised in.
nlohmann::json common_payload(const GroupState& state, const GroupEnvelope& envelope) {
    nlohmann::json payload = nlohmann::json::object();
    payload["transport"] = "group";
    payload["group_id"] = state.group_id();
    payload["group_title"] = title_field(state);
    payload["members"] = state.members();
    payload["epoch"] = envelope.epoch;
    payload["msg_id"] = envelope.msg_id;
    payload["sender_id"] = envelope.sender_id;
    payload["group_seq"] = envelope.group_seq;
    payload["content_type"] = content_type_name(envelope.content_type);
    payload["payload"] = envelope.payload;
    payload["created_at"] = envelope.created_at;
    return payload;
}

std::string required_text(const nlohmann::json& payload, const char* key) {
    const auto it = payload.find(key);
    std::string value;
    if (it != payload.end()) {
        value = it->is_string() ? trim(it->get<std::string>())
                                : (it->is_null() ? "" : trim(it->dump()));
    }
    if (value.empty()) {
        throw WireError(std::string("Missing required group transport field: ") + key);
    }
    return value;
}

std::uint64_t required_uint(const nlohmann::json& payload, const char* key,
                            std::uint64_t minimum) {
    const auto it = payload.find(key);
    if (it == payload.end()) {
        throw WireError(std::string("Missing required group transport field: ") + key);
    }
    if (!it->is_number_integer()) {
        throw WireError(std::string("Invalid group transport integer field: ") + key);
    }
    const std::int64_t value = it->get<std::int64_t>();
    if (value < 0 || static_cast<std::uint64_t>(value) < minimum) {
        throw WireError(std::string("Invalid group transport integer field: ") + key);
    }
    return static_cast<std::uint64_t>(value);
}

std::vector<std::string> required_members(const nlohmann::json& payload) {
    const auto it = payload.find("members");
    if (it == payload.end() || !it->is_array()) {
        throw WireError("Invalid group transport field: members");
    }
    std::vector<std::string> members;
    for (const nlohmann::json& member : *it) {
        members.push_back(member.is_string() ? member.get<std::string>() : member.dump());
    }
    return members;
}

ContentType required_content_type(const nlohmann::json& payload) {
    const std::optional<ContentType> type =
        parse_content_type(required_text(payload, "content_type"));
    if (!type.has_value()) {
        throw WireError("Unsupported group content type");
    }
    return *type;
}

/// A text message must carry a string and a control message an object. Checking
/// here means the rest of the code can trust the shape.
void check_payload_shape(ContentType type, const nlohmann::json& payload) {
    const auto it = payload.find("payload");
    const nlohmann::json value = it == payload.end() ? nlohmann::json() : *it;
    if (type == ContentType::GroupText && !value.is_string()) {
        throw WireError("GROUP_TEXT payload must be a string");
    }
    if (type == ContentType::GroupControl && !value.is_object()) {
        throw WireError("GROUP_CONTROL payload must be an object");
    }
}

GroupState state_from_payload(const nlohmann::json& payload, const std::string& group_id,
                             const std::string& created_at) {
    const auto title = payload.find("group_title");
    const std::string title_text =
        title != payload.end() && title->is_string() ? trim(title->get<std::string>()) : "";

    GroupState state(group_id, required_uint(payload, "epoch", 0),
                     required_members(payload), title_text);
    if (state.members().empty()) {
        throw WireError("Group transport must include at least one member");
    }
    (void)created_at;
    return state;
}

GroupEnvelope envelope_from_payload(const nlohmann::json& payload, const GroupState& state,
                                   ContentType type, const std::string& msg_id,
                                   const std::string& sender_id,
                                   const std::string& created_at) {
    GroupEnvelope envelope;
    envelope.group_id = state.group_id();
    envelope.epoch = state.epoch();
    envelope.msg_id = msg_id;
    envelope.sender_id = sender_id;
    envelope.group_seq = required_uint(payload, "group_seq", 1);
    envelope.content_type = type;
    const auto it = payload.find("payload");
    envelope.payload = it == payload.end() ? nlohmann::json() : *it;
    envelope.created_at = created_at;
    return envelope;
}

Bytes required_hex(const nlohmann::json& payload, const char* key, std::size_t size) {
    const std::optional<Bytes> decoded =
        encoding::hex_decode(required_text(payload, key));
    if (!decoded.has_value()) {
        throw WireError("Invalid group blindbox signature encoding");
    }
    if (decoded->size() != size) {
        throw WireError(std::string("Group blindbox ") + key + " has the wrong length");
    }
    return *decoded;
}

DecodedTransportMessage decode_v1(const nlohmann::json& payload) {
    const ContentType type = required_content_type(payload);
    check_payload_shape(type, payload);

    const std::string group_id = required_text(payload, "group_id");
    const std::string msg_id = required_text(payload, "msg_id");
    const std::string sender_id =
        normalize_member_id(required_text(payload, "sender_id"));
    const std::string recipient_id =
        normalize_member_id(required_text(payload, "recipient_id"));
    if (sender_id.empty()) {
        throw WireError("Missing required group transport field: sender_id");
    }
    if (recipient_id.empty()) {
        throw WireError("Missing required group transport field: recipient_id");
    }
    const std::string delivery_id = required_text(payload, "delivery_id");
    const std::string created_at = required_text(payload, "created_at");

    const GroupState state = state_from_payload(payload, group_id, created_at);
    if (!state.has_member(sender_id)) {
        throw WireError("Group transport sender is not a group member");
    }
    if (!state.has_member(recipient_id)) {
        throw WireError("Group transport recipient is not a group member");
    }

    DecodedTransportMessage decoded;
    decoded.state = state;
    decoded.envelope =
        envelope_from_payload(payload, state, type, msg_id, sender_id, created_at);
    decoded.recipient_id = recipient_id;
    decoded.delivery_id = delivery_id;
    decoded.version = kTransportVersionV1;
    decoded.delivery_scope = std::string(kDeliveryScopeRecipient);
    return decoded;
}

DecodedTransportMessage decode_v3(const nlohmann::json& payload) {
    const auto scope = payload.find("delivery_scope");
    if (scope == payload.end() || !scope->is_string() ||
        trim_lower(scope->get<std::string>()) != kDeliveryScopeGroupBlindBox) {
        throw WireError("Unsupported group transport delivery scope");
    }
    // A group-wide blob has no single recipient. Naming one would mean the
    // sender is trying to have it both ways.
    if (payload.contains("recipient_id") || payload.contains("delivery_id")) {
        throw WireError("Group blindbox transport must not include recipient metadata");
    }

    const ContentType type = required_content_type(payload);
    check_payload_shape(type, payload);

    const std::string group_id = required_text(payload, "group_id");
    const std::string msg_id = required_text(payload, "msg_id");
    const std::string sender_id =
        normalize_member_id(required_text(payload, "sender_id"));
    if (sender_id.empty()) {
        throw WireError("Missing required group transport field: sender_id");
    }
    const Bytes signer_key = required_hex(payload, "signer_key", 32);
    const Bytes signature = required_hex(payload, "signature", 64);
    const std::string created_at = required_text(payload, "created_at");

    const GroupState state = state_from_payload(payload, group_id, created_at);
    if (!state.has_member(sender_id)) {
        throw WireError("Group transport sender is not a group member");
    }

    GroupEnvelope envelope =
        envelope_from_payload(payload, state, type, msg_id, sender_id, created_at);

    // Verify here rather than leaving it to the caller: a decoded v3 message
    // that has not been checked against its own signature is a trap.
    const Bytes signed_payload = v3_signature_payload(state, envelope, ByteView(signer_key));
    if (!crypto::verify_signature(ByteView(signer_key), ByteView(signed_payload),
                                  ByteView(signature))) {
        throw WireError("Invalid group blindbox sender signature");
    }

    DecodedTransportMessage decoded;
    decoded.state = state;
    decoded.envelope = std::move(envelope);
    decoded.signer_key = signer_key;
    decoded.signature = signature;
    decoded.version = kTransportVersionV3;
    decoded.delivery_scope = std::string(kDeliveryScopeGroupBlindBox);
    return decoded;
}

}  // namespace

std::string encode_transport_v1(const GroupState& state, const GroupEnvelope& envelope,
                                const RecipientDelivery& delivery) {
    nlohmann::json payload = common_payload(state, envelope);
    payload["version"] = kTransportVersionV1;
    payload["recipient_id"] = delivery.recipient_id;
    payload["delivery_id"] = delivery.delivery_id;
    return std::string(kTransportPrefix) + json_canonical::dump(payload);
}

Bytes v3_signature_payload(const GroupState& state, const GroupEnvelope& envelope,
                           ByteView signer_key) {
    if (signer_key.size() != 32) {
        throw WireError("Group blindbox signer key must be 32 bytes");
    }
    nlohmann::json payload = common_payload(state, envelope);
    payload["version"] = kTransportVersionV3;
    payload["delivery_scope"] = kDeliveryScopeGroupBlindBox;
    payload["signer_key"] = encoding::hex_encode(signer_key);
    return to_bytes(json_canonical::dump(payload));
}

std::string encode_transport_v3(const GroupState& state, const GroupEnvelope& envelope,
                                ByteView signer_key, ByteView signature) {
    if (signature.size() != 64) {
        throw WireError("Group blindbox signature must be 64 bytes");
    }
    nlohmann::json payload =
        nlohmann::json::parse(to_string(ByteView(v3_signature_payload(state, envelope, signer_key))));
    payload["signature"] = encoding::hex_encode(signature);
    return std::string(kTransportPrefix) + json_canonical::dump(payload);
}

std::optional<DecodedTransportMessage> decode_transport(std::string_view text) {
    if (!text.starts_with(kTransportPrefix)) {
        return std::nullopt;
    }
    // Bound the input before parsing: an oversized document is a cheap way to
    // burn a peer's CPU and memory.
    if (text.size() > kMaxTransportBytes) {
        throw WireError("Group transport payload too large");
    }

    nlohmann::json payload;
    try {
        payload = nlohmann::json::parse(text.substr(kTransportPrefix.size()));
    } catch (const nlohmann::json::exception&) {
        throw WireError("Group transport payload is not valid JSON");
    }
    if (!payload.is_object()) {
        throw WireError("Group transport payload must be a JSON object");
    }
    const auto transport = payload.find("transport");
    if (transport == payload.end() || *transport != "group") {
        throw WireError("Unsupported group transport payload");
    }

    const auto version = payload.find("version");
    const int value =
        version != payload.end() && version->is_number_integer() ? version->get<int>() : 0;
    if (value == kTransportVersionV1) {
        return decode_v1(payload);
    }
    if (value == kTransportVersionV2) {
        throw WireError("Unsigned group blindbox transport is no longer accepted");
    }
    if (value == kTransportVersionV3) {
        return decode_v3(payload);
    }
    throw WireError("Unsupported group transport version");
}

}  // namespace i2pchat::groups
