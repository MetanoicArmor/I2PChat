#include "i2pchat/groups/invite.hpp"

#include <algorithm>
#include <cctype>
#include <nlohmann/json.hpp>
#include <set>

#include "i2pchat/canonical_json.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/chat_history.hpp"

namespace i2pchat::groups {
namespace {

constexpr std::string_view kWhitespace = " \t\r\n\f\v";
constexpr std::size_t kWrapKeySize = 32;
constexpr std::string_view kSealSalt = "I2PCHAT-GROUP-INVITE-SEAL";
constexpr std::string_view kSealInfo = "I2PCHAT-GROUP-INVITE-SEAL|v3";
/// wrap key + SecretBox overhead (24-byte nonce, 16-byte tag) + a minimal body.
constexpr std::size_t kMinSealedBytes = kWrapKeySize + 24 + 16 + 16;

std::string trim(std::string_view text) {
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

/// Whitespace anywhere is dropped: tokens get wrapped by mail clients and
/// pasted back with line breaks in them.
std::string strip_all_whitespace(std::string_view text) {
    std::string out;
    out.reserve(text.size());
    for (const char ch : text) {
        if (kWhitespace.find(ch) == std::string_view::npos) {
            out.push_back(ch);
        }
    }
    return out;
}

bool is_base64url_token(std::string_view text) {
    return !text.empty() && std::all_of(text.begin(), text.end(), [](const char ch) {
               return std::isalnum(static_cast<unsigned char>(ch)) != 0 || ch == '_' ||
                      ch == '-';
           });
}

std::vector<std::string> normalize_members(const nlohmann::json& raw) {
    if (!raw.is_array()) {
        throw WireError("Group invite members must be a list");
    }
    std::vector<std::string> members;
    std::set<std::string> seen;
    for (const nlohmann::json& item : raw) {
        const std::string text = item.is_string() ? item.get<std::string>() : item.dump();
        const std::string member = normalize_member_id(text);
        if (member.empty() || !seen.insert(member).second) {
            continue;
        }
        members.push_back(member);
    }
    if (members.empty()) {
        throw WireError("Group invite must include at least one member");
    }
    return members;
}

Bytes derive_seal_key(ByteView wrap_key) {
    const Bytes prk = crypto::hkdf_extract(as_bytes(kSealSalt), wrap_key);
    return crypto::hkdf_expand(ByteView(prk), as_bytes(kSealInfo), 32);
}

std::string seal(const std::string& plaintext) {
    const Bytes wrap_key = crypto::random_bytes(kWrapKeySize);
    const Bytes file_key = derive_seal_key(ByteView(wrap_key));
    const Bytes ciphertext = crypto::encrypt_message(ByteView(file_key), as_bytes(plaintext));

    Bytes blob;
    blob.reserve(wrap_key.size() + ciphertext.size());
    append(blob, ByteView(wrap_key));
    append(blob, ByteView(ciphertext));
    return encoding::base64url_encode_nopad(ByteView(blob));
}

std::string unseal(std::string_view token) {
    const std::string compact = strip_all_whitespace(token);
    if (!is_base64url_token(compact)) {
        throw WireError("Not a group invite string");
    }
    const std::optional<Bytes> raw = encoding::base64url_decode(compact);
    if (!raw.has_value()) {
        throw WireError("Invalid group invite encoding");
    }
    if (raw->size() < kMinSealedBytes) {
        throw WireError("Group invite payload too small");
    }
    const ByteView wrap_key = ByteView(*raw).subspan(0, kWrapKeySize);
    const ByteView ciphertext = ByteView(*raw).subspan(kWrapKeySize);
    const Bytes file_key = derive_seal_key(wrap_key);

    const std::optional<Bytes> plaintext =
        crypto::decrypt_message(ByteView(file_key), ciphertext);
    if (!plaintext.has_value()) {
        throw WireError("Group invite decryption failed");
    }
    return to_string(ByteView(*plaintext));
}

nlohmann::json load_payload(std::string_view text) {
    const std::string raw = trim(text);
    if (raw.empty()) {
        throw WireError("Empty group invite payload");
    }
    if (raw.size() > kMaxInviteBytes) {
        throw WireError("Group invite payload too large");
    }

    std::string body;
    if (raw.starts_with(kInvitePrefix)) {
        body = trim(std::string_view(raw).substr(kInvitePrefix.size()));
        if (body.empty()) {
            throw WireError("Empty group invite payload");
        }
    } else {
        body = unseal(raw);
    }

    nlohmann::json payload;
    try {
        payload = nlohmann::json::parse(body);
    } catch (const nlohmann::json::exception&) {
        throw WireError("Invalid group invite payload");
    }
    if (!payload.is_object()) {
        throw WireError("Group invite payload must be an object");
    }
    return payload;
}

std::string text_field(const nlohmann::json& payload, const char* key) {
    const auto it = payload.find(key);
    if (it == payload.end() || !it->is_string()) {
        return "";
    }
    return trim(it->get<std::string>());
}

/// The signed form. `expires_at` is `null` rather than absent when unset,
/// because the signature covers the field either way.
nlohmann::json signed_body(const GroupInvite& invite) {
    nlohmann::json payload = nlohmann::json::object();
    payload["created_at"] = invite.created_at;
    payload["epoch"] = invite.epoch;
    payload["expires_at"] =
        invite.expires_at.empty() ? nlohmann::json() : nlohmann::json(invite.expires_at);
    payload["group_id"] = invite.group_id;
    payload["invite_id"] = invite.invite_id;
    payload["inviter_id"] = invite.inviter_id;
    payload["inviter_signing_pub"] = invite.inviter_signing_pub;
    payload["members"] = invite.members;
    payload["title"] = invite.title.empty() ? nlohmann::json() : nlohmann::json(invite.title);
    payload["v"] = invite.version;
    return payload;
}

/// Members with the inviter appended when missing. The inviter is part of the
/// group by construction, and the signature is computed over this form.
std::vector<std::string> members_with_inviter(std::vector<std::string> members,
                                             const std::string& inviter_id) {
    if (std::find(members.begin(), members.end(), inviter_id) == members.end()) {
        members.push_back(inviter_id);
    }
    return members;
}

}  // namespace

Bytes invite_signature_payload(const GroupInvite& invite) {
    return to_bytes(std::string(kInviteSignatureDomain) + "|" +
                    json_canonical::dump(signed_body(invite)));
}

std::string encode_invite(const GroupInvite& invite, ByteView signing_seed) {
    if (signing_seed.empty()) {
        throw WireError("A signing seed is required to sign a group invite");
    }
    GroupInvite signed_invite = invite;
    signed_invite.invite_id = trim(invite.invite_id);
    signed_invite.group_id = trim(invite.group_id);
    signed_invite.inviter_id = normalize_member_id(invite.inviter_id);
    signed_invite.title = trim(invite.title);
    signed_invite.version = kInviteVersion;

    if (signed_invite.invite_id.empty()) {
        throw WireError("Group invite invite_id is required");
    }
    if (signed_invite.group_id.empty()) {
        throw WireError("Group invite group_id is required");
    }
    if (signed_invite.inviter_id.empty()) {
        throw WireError("Group invite inviter_id is required");
    }

    std::vector<std::string> members;
    std::set<std::string> seen;
    for (const std::string& raw : invite.members) {
        const std::string member = normalize_member_id(raw);
        if (member.empty() || !seen.insert(member).second) {
            continue;
        }
        members.push_back(member);
    }
    if (members.empty()) {
        throw WireError("Group invite must include at least one member");
    }
    signed_invite.members = members_with_inviter(std::move(members), signed_invite.inviter_id);

    signed_invite.inviter_signing_pub =
        encoding::hex_encode(ByteView(crypto::get_verify_key_from_seed(signing_seed)));
    signed_invite.signature = encoding::hex_encode(ByteView(crypto::sign_data(
        signing_seed, ByteView(invite_signature_payload(signed_invite)))));

    nlohmann::json payload = signed_body(signed_invite);
    payload["signature"] = signed_invite.signature;
    return seal(json_canonical::dump(payload));
}

GroupInvite decode_invite(std::string_view text,
                          std::optional<std::chrono::system_clock::time_point> now) {
    const nlohmann::json payload = load_payload(text);

    const auto version_field = payload.find("v");
    const int version = version_field != payload.end() && version_field->is_number_integer()
                            ? version_field->get<int>()
                            : 0;
    if (version != kInviteVersion) {
        throw WireError("Unsupported group invite version: " + std::to_string(version) +
                        " (v1 unsigned invites are rejected)");
    }

    GroupInvite invite;
    invite.version = version;
    invite.invite_id = text_field(payload, "invite_id");
    invite.group_id = text_field(payload, "group_id");
    invite.title = text_field(payload, "title");
    invite.created_at = text_field(payload, "created_at");
    invite.expires_at = text_field(payload, "expires_at");
    invite.inviter_signing_pub = trim_lower(text_field(payload, "inviter_signing_pub"));
    invite.signature = trim_lower(text_field(payload, "signature"));

    if (invite.invite_id.empty()) {
        throw WireError("Missing required group invite field: invite_id");
    }
    if (invite.group_id.empty()) {
        throw WireError("Missing required group invite field: group_id");
    }
    const std::string inviter_raw = text_field(payload, "inviter_id");
    if (inviter_raw.empty()) {
        throw WireError("Missing required group invite field: inviter_id");
    }
    invite.inviter_id = normalize_member_id(inviter_raw);
    if (invite.inviter_id.empty()) {
        throw WireError("Missing required group invite field: inviter_id");
    }
    if (invite.inviter_signing_pub.size() != 64) {
        throw WireError("Missing or invalid inviter signing public key");
    }
    if (invite.signature.size() != 128) {
        throw WireError("Missing or invalid group invite signature");
    }

    const auto epoch_field = payload.find("epoch");
    if (epoch_field == payload.end() || !epoch_field->is_number_integer() ||
        epoch_field->get<std::int64_t>() < 0) {
        throw WireError("Invalid group invite epoch");
    }
    invite.epoch = epoch_field->get<std::uint64_t>();

    if (!invite.created_at.empty() &&
        !storage::parse_iso8601_utc(invite.created_at).has_value()) {
        throw WireError("Invalid group invite created_at");
    }
    std::optional<std::chrono::system_clock::time_point> expires;
    if (!invite.expires_at.empty()) {
        expires = storage::parse_iso8601_utc(invite.expires_at);
        if (!expires.has_value()) {
            throw WireError("Invalid group invite expires_at");
        }
    }

    const auto members_field = payload.find("members");
    invite.members = normalize_members(members_field == payload.end() ? nlohmann::json()
                                                                     : *members_field);

    const std::optional<Bytes> signing_pub = encoding::hex_decode(invite.inviter_signing_pub);
    const std::optional<Bytes> signature = encoding::hex_decode(invite.signature);
    if (!signing_pub.has_value() || !signature.has_value()) {
        throw WireError("Malformed group invite signature encoding");
    }

    // Verify over the roster as the encoder signed it, with the inviter present,
    // rather than over what the payload happens to list. That is what proves the
    // roster itself was not edited in transit.
    GroupInvite signed_form = invite;
    signed_form.members = members_with_inviter(invite.members, invite.inviter_id);
    if (!crypto::verify_signature(ByteView(*signing_pub),
                                  ByteView(invite_signature_payload(signed_form)),
                                  ByteView(*signature))) {
        throw WireError("Group invite signature verification failed");
    }

    if (expires.has_value() && *expires < now.value_or(std::chrono::system_clock::now())) {
        throw WireError("Group invite has expired");
    }

    invite.members = std::move(signed_form.members);
    return invite;
}

bool looks_like_invite(std::string_view text) {
    const std::string raw = trim(text);
    if (raw.empty()) {
        return false;
    }
    if (raw.starts_with(kInvitePrefix)) {
        return true;
    }
    try {
        const nlohmann::json payload = load_payload(raw);
        return !text_field(payload, "invite_id").empty() &&
               !text_field(payload, "signature").empty();
    } catch (const std::exception&) {
        return false;
    }
}

}  // namespace i2pchat::groups
