#include "i2pchat/groups/store.hpp"

#include <algorithm>
#include <set>
#include <utility>

#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/group_record.hpp"
#include "i2pchat/storage/sealed_json.hpp"

namespace i2pchat::groups {
namespace {

std::string trimmed(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    return std::string(text.substr(first, last - first + 1));
}

/// An empty string is written as JSON null, which is how the reference stores an
/// absent title, message id or source peer.
nlohmann::json text_or_null(const std::string& value) {
    return value.empty() ? nlohmann::json() : nlohmann::json(value);
}

std::string text_field(const nlohmann::json& object, const char* key) {
    const auto it = object.find(key);
    if (it == object.end() || !it->is_string()) {
        return "";
    }
    return trimmed(it->get<std::string>());
}

std::uint64_t uint_field(const nlohmann::json& object, const char* key) {
    const auto it = object.find(key);
    if (it == object.end() || !it->is_number()) {
        return 0;
    }
    const auto value = it->get<std::int64_t>();
    return value > 0 ? static_cast<std::uint64_t>(value) : 0;
}

ContentType content_type_field(const nlohmann::json& object) {
    const auto it = object.find("content_type");
    if (it == object.end() || !it->is_string()) {
        throw storage::SealedJsonError("Group record entry has no content type");
    }
    const std::optional<ContentType> parsed = parse_content_type(it->get<std::string>());
    if (!parsed.has_value()) {
        throw storage::SealedJsonError("Unsupported group content type: " +
                                       it->get<std::string>());
    }
    return *parsed;
}

std::vector<std::string> member_list(const nlohmann::json& object, const char* key) {
    std::vector<std::string> members;
    const auto it = object.find(key);
    if (it == object.end() || !it->is_array()) {
        return members;
    }
    for (const nlohmann::json& item : *it) {
        if (item.is_string()) {
            members.push_back(item.get<std::string>());
        }
    }
    return members;
}

/// Members go through `GroupState` so a stored list is normalised and
/// deduplicated the same way a live one is.
std::vector<std::string> normalized_members(const std::string& group_id,
                                            std::uint64_t epoch,
                                            const std::vector<std::string>& members) {
    return GroupState(group_id.empty() ? "__pending__" : group_id, epoch, members).members();
}

std::map<std::string, std::string> string_map(const nlohmann::json& object,
                                             const char* key,
                                             bool drop_empty_values) {
    std::map<std::string, std::string> result;
    const auto it = object.find(key);
    if (it == object.end() || !it->is_object()) {
        return result;
    }
    for (const auto& [raw_key, raw_value] : it->items()) {
        const std::string member = normalize_member_id(raw_key);
        if (member.empty() || !raw_value.is_string()) {
            continue;
        }
        const std::string value = trimmed(raw_value.get<std::string>());
        if (drop_empty_values && value.empty()) {
            continue;
        }
        result.emplace(member, value);
    }
    return result;
}

nlohmann::json payload_for(ContentType type, const nlohmann::json& payload,
                           const std::string& text) {
    if (type == ContentType::GroupText) {
        if (!text.empty()) {
            return text;
        }
        return payload.is_string() ? payload : nlohmann::json("");
    }
    return payload.is_object() ? payload : nlohmann::json::object();
}

nlohmann::json entry_to_json(const HistoryEntry& entry) {
    nlohmann::json item = nlohmann::json::object();
    item["kind"] = entry.kind;
    item["sender_id"] = entry.sender_id;
    item["content_type"] = content_type_name(entry.content_type);
    item["text"] = entry.text;
    item["payload"] = payload_for(entry.content_type, entry.payload, entry.text);
    item["msg_id"] = text_or_null(entry.msg_id);
    item["group_seq"] = entry.group_seq;
    item["epoch"] = entry.epoch;
    item["created_at"] = entry.created_at;
    item["source_peer"] = text_or_null(entry.source_peer);
    item["delivery_results"] = entry.delivery_results;
    item["delivery_reasons"] = entry.delivery_reasons;
    return item;
}

HistoryEntry entry_from_json(const nlohmann::json& item) {
    HistoryEntry entry;
    entry.kind = text_field(item, "kind") == "me" ? "me" : "peer";
    entry.sender_id = normalize_member_id(text_field(item, "sender_id"));
    entry.content_type = content_type_field(item);
    entry.text = item.value("text", std::string{});
    const auto payload = item.find("payload");
    entry.payload = payload != item.end() ? *payload : nlohmann::json();
    if (entry.content_type == ContentType::GroupText && entry.text.empty() &&
        entry.payload.is_string()) {
        entry.text = entry.payload.get<std::string>();
    }
    entry.payload = payload_for(entry.content_type, entry.payload, entry.text);
    entry.msg_id = text_field(item, "msg_id");
    entry.group_seq = uint_field(item, "group_seq");
    entry.epoch = uint_field(item, "epoch");
    entry.created_at = text_field(item, "created_at");
    entry.source_peer = text_field(item, "source_peer");
    entry.delivery_results = string_map(item, "delivery_results", false);
    entry.delivery_reasons = string_map(item, "delivery_reasons", true);
    return entry;
}

nlohmann::json pending_delivery_to_json(const PendingDelivery& pending) {
    nlohmann::json item = nlohmann::json::object();
    item["group_id"] = pending.group_id;
    item["group_title"] = text_or_null(pending.group_title);
    item["group_members"] = pending.group_members;
    item["sender_id"] = pending.sender_id;
    item["recipient_id"] = pending.recipient_id;
    item["delivery_id"] = pending.delivery_id;
    item["msg_id"] = pending.msg_id;
    item["group_seq"] = pending.group_seq;
    item["epoch"] = pending.epoch;
    item["content_type"] = content_type_name(pending.content_type);
    item["payload"] = payload_for(pending.content_type, pending.payload, {});
    item["created_at"] = pending.created_at;
    return item;
}

PendingDelivery pending_delivery_from_json(const nlohmann::json& item) {
    PendingDelivery pending;
    pending.group_id = text_field(item, "group_id");
    pending.group_title = text_field(item, "group_title");
    pending.epoch = uint_field(item, "epoch");
    pending.group_members =
        normalized_members(pending.group_id, pending.epoch, member_list(item, "group_members"));
    pending.sender_id = normalize_member_id(text_field(item, "sender_id"));
    pending.recipient_id = normalize_member_id(text_field(item, "recipient_id"));
    pending.delivery_id = text_field(item, "delivery_id");
    pending.msg_id = text_field(item, "msg_id");
    pending.group_seq = uint_field(item, "group_seq");
    pending.content_type = content_type_field(item);
    const auto payload = item.find("payload");
    pending.payload = payload_for(pending.content_type,
                                  payload != item.end() ? *payload : nlohmann::json(), {});
    pending.created_at = text_field(item, "created_at");
    return pending;
}

nlohmann::json pending_blindbox_to_json(const PendingBlindBoxMessage& pending) {
    nlohmann::json item = nlohmann::json::object();
    item["group_id"] = pending.group_id;
    item["group_title"] = text_or_null(pending.group_title);
    item["group_members"] = pending.group_members;
    item["sender_id"] = pending.sender_id;
    item["msg_id"] = pending.msg_id;
    item["group_seq"] = pending.group_seq;
    item["epoch"] = pending.epoch;
    item["content_type"] = content_type_name(pending.content_type);
    item["payload"] = payload_for(pending.content_type, pending.payload, {});
    item["created_at"] = pending.created_at;
    return item;
}

PendingBlindBoxMessage pending_blindbox_from_json(const nlohmann::json& item) {
    PendingBlindBoxMessage pending;
    pending.group_id = text_field(item, "group_id");
    pending.group_title = text_field(item, "group_title");
    pending.epoch = uint_field(item, "epoch");
    pending.group_members =
        normalized_members(pending.group_id, pending.epoch, member_list(item, "group_members"));
    pending.sender_id = normalize_member_id(text_field(item, "sender_id"));
    pending.msg_id = text_field(item, "msg_id");
    pending.group_seq = uint_field(item, "group_seq");
    pending.content_type = content_type_field(item);
    const auto payload = item.find("payload");
    pending.payload = payload_for(pending.content_type,
                                  payload != item.end() ? *payload : nlohmann::json(), {});
    pending.created_at = text_field(item, "created_at");
    return pending;
}

nlohmann::json state_to_json(const StoredConversation& conversation) {
    nlohmann::json state = nlohmann::json::object();
    state["group_id"] = conversation.state.group_id();
    state["title"] = text_or_null(conversation.state.title());
    state["epoch"] = conversation.state.epoch();
    state["members"] = conversation.state.members();
    state["created_at"] = conversation.created_at;
    state["updated_at"] = conversation.updated_at;
    return state;
}

GroupState state_from_json(const nlohmann::json& state) {
    if (!state.is_object() || !state.contains("group_id")) {
        throw storage::SealedJsonError("Group record has no group state");
    }
    return GroupState(text_field(state, "group_id"), uint_field(state, "epoch"),
                      member_list(state, "members"), text_field(state, "title"));
}

/// Read one record straight from its path, for the listing walk where the group
/// id — and so the file key scope — comes from the file name.
std::optional<nlohmann::json> read_record_by_token(const std::filesystem::path& path,
                                                   const std::string& profile,
                                                   std::optional<ByteView> identity_key) {
    const std::string name = path.filename().string();
    const std::string prefix = profile + ".group.";
    constexpr std::string_view kSuffix = ".json";
    if (!name.starts_with(prefix) || !name.ends_with(kSuffix)) {
        return std::nullopt;
    }
    const std::string token =
        name.substr(prefix.size(), name.size() - prefix.size() - kSuffix.size());
    if (token.empty()) {
        return std::nullopt;
    }
    return storage::read_sealed_json(path, identity_key,
                                     storage::group_store_format_for_token(token));
}

}  // namespace

GroupState PendingDelivery::as_group_state() const {
    return GroupState(group_id, epoch, group_members, group_title);
}

GroupEnvelope PendingDelivery::as_envelope() const {
    GroupEnvelope envelope;
    envelope.group_id = group_id;
    envelope.epoch = epoch;
    envelope.msg_id = msg_id;
    envelope.sender_id = sender_id;
    envelope.group_seq = group_seq;
    envelope.content_type = content_type;
    envelope.payload = payload;
    envelope.created_at = created_at;
    return envelope;
}

RecipientDelivery PendingDelivery::as_delivery() const {
    return RecipientDelivery{recipient_id, delivery_id};
}

GroupState PendingBlindBoxMessage::as_group_state() const {
    return GroupState(group_id, epoch, group_members, group_title);
}

GroupEnvelope PendingBlindBoxMessage::as_envelope() const {
    GroupEnvelope envelope;
    envelope.group_id = group_id;
    envelope.epoch = epoch;
    envelope.msg_id = msg_id;
    envelope.sender_id = sender_id;
    envelope.group_seq = group_seq;
    envelope.content_type = content_type;
    envelope.payload = payload;
    envelope.created_at = created_at;
    return envelope;
}

void StoredConversation::normalize() {
    std::uint64_t highest_seq = 0;
    for (const HistoryEntry& entry : history) {
        highest_seq = std::max(highest_seq, entry.group_seq);
    }
    next_group_seq = std::max<std::uint64_t>({1, next_group_seq, highest_seq + 1});

    // Ids already in the file come first, then those the history implies, so a
    // record whose duplicate filter was trimmed still suppresses what it holds.
    std::vector<std::string> resolved;
    std::set<std::string> seen;
    const auto remember = [&](const std::string& raw) {
        const std::string msg_id = trimmed(raw);
        if (msg_id.empty() || !seen.insert(msg_id).second) {
            return;
        }
        resolved.push_back(msg_id);
    };
    for (const std::string& msg_id : seen_msg_ids) {
        remember(msg_id);
    }
    for (const HistoryEntry& entry : history) {
        remember(entry.msg_id);
    }
    if (resolved.size() > kMaxSeenMessageIds) {
        resolved.erase(resolved.begin(),
                       resolved.begin() +
                           static_cast<std::ptrdiff_t>(resolved.size() - kMaxSeenMessageIds));
    }
    seen_msg_ids = std::move(resolved);

    std::vector<PendingDelivery> deliveries;
    std::set<std::string> delivery_ids;
    for (PendingDelivery& pending : pending_deliveries) {
        if (pending.delivery_id.empty() || !delivery_ids.insert(pending.delivery_id).second) {
            continue;
        }
        deliveries.push_back(std::move(pending));
    }
    pending_deliveries = std::move(deliveries);

    std::vector<PendingBlindBoxMessage> messages;
    std::set<std::string> message_ids;
    for (PendingBlindBoxMessage& pending : pending_blindbox_messages) {
        if (pending.msg_id.empty() || !message_ids.insert(pending.msg_id).second) {
            continue;
        }
        messages.push_back(std::move(pending));
    }
    pending_blindbox_messages = std::move(messages);
}

nlohmann::json conversation_to_json(const StoredConversation& conversation,
                                    std::string_view profile, ByteView signing_seed) {
    nlohmann::json payload = nlohmann::json::object();
    payload["version"] = kGroupRecordVersion;
    payload["state"] = state_to_json(conversation);
    payload["next_group_seq"] = std::max<std::uint64_t>(1, conversation.next_group_seq);

    payload["history"] = nlohmann::json::array();
    for (const HistoryEntry& entry : conversation.history) {
        payload["history"].push_back(entry_to_json(entry));
    }
    payload["seen_msg_ids"] = conversation.seen_msg_ids;

    payload["pending_deliveries"] = nlohmann::json::array();
    for (const PendingDelivery& pending : conversation.pending_deliveries) {
        payload["pending_deliveries"].push_back(pending_delivery_to_json(pending));
    }

    payload["blindbox_channel"] =
        conversation.blindbox_channel
            ? blindbox::group_snapshot_to_json(*conversation.blindbox_channel, profile,
                                               signing_seed)
            : nlohmann::json();

    payload["pending_group_blindbox_messages"] = nlohmann::json::array();
    for (const PendingBlindBoxMessage& pending : conversation.pending_blindbox_messages) {
        payload["pending_group_blindbox_messages"].push_back(
            pending_blindbox_to_json(pending));
    }
    return payload;
}

StoredConversation conversation_from_json(const nlohmann::json& payload,
                                          std::string_view profile,
                                          ByteView signing_seed) {
    if (!payload.is_object()) {
        throw storage::SealedJsonError("Group record must be a JSON object");
    }
    if (payload.value("version", 0) != kGroupRecordVersion) {
        throw storage::SealedJsonError("Unsupported group store version");
    }

    StoredConversation conversation;
    const auto state = payload.find("state");
    if (state == payload.end()) {
        throw storage::SealedJsonError("Group record has no group state");
    }
    conversation.state = state_from_json(*state);
    conversation.created_at = text_field(*state, "created_at");
    conversation.updated_at = text_field(*state, "updated_at");
    conversation.next_group_seq = uint_field(payload, "next_group_seq");

    const auto history = payload.find("history");
    if (history != payload.end() && history->is_array()) {
        for (const nlohmann::json& item : *history) {
            if (item.is_object()) {
                conversation.history.push_back(entry_from_json(item));
            }
        }
    }

    const auto seen = payload.find("seen_msg_ids");
    if (seen != payload.end() && seen->is_array()) {
        for (const nlohmann::json& item : *seen) {
            if (item.is_string()) {
                conversation.seen_msg_ids.push_back(item.get<std::string>());
            }
        }
    }

    const auto deliveries = payload.find("pending_deliveries");
    if (deliveries != payload.end() && deliveries->is_array()) {
        for (const nlohmann::json& item : *deliveries) {
            if (item.is_object()) {
                conversation.pending_deliveries.push_back(pending_delivery_from_json(item));
            }
        }
    }

    const auto channel = payload.find("blindbox_channel");
    if (channel != payload.end() && channel->is_object() && !channel->empty()) {
        conversation.blindbox_channel = blindbox::group_snapshot_from_json(
            *channel, conversation.state.group_id(), profile, signing_seed);
    }

    const auto messages = payload.find("pending_group_blindbox_messages");
    if (messages != payload.end() && messages->is_array()) {
        for (const nlohmann::json& item : *messages) {
            if (item.is_object()) {
                conversation.pending_blindbox_messages.push_back(
                    pending_blindbox_from_json(item));
            }
        }
    }

    conversation.normalize();
    return conversation;
}

std::optional<StoredConversation> load_conversation(const storage::ProfilePaths& paths,
                                                    std::string_view group_id,
                                                    std::optional<ByteView> identity_key,
                                                    ByteView signing_seed) {
    const std::filesystem::path path = paths.group_store(group_id);
    if (!std::filesystem::exists(path)) {
        return std::nullopt;
    }
    return conversation_from_json(
        storage::read_group_record(path, group_id, identity_key), paths.profile(),
        signing_seed);
}

void save_conversation(const storage::ProfilePaths& paths,
                       const StoredConversation& conversation,
                       std::optional<ByteView> identity_key, ByteView signing_seed) {
    const std::string& group_id = conversation.state.group_id();
    storage::write_group_record(
        paths.group_store(group_id), group_id,
        conversation_to_json(conversation, paths.profile(), signing_seed), identity_key);
}

StoredConversation upsert_state(const storage::ProfilePaths& paths, const GroupState& state,
                                std::optional<ByteView> identity_key,
                                ByteView signing_seed,
                                std::optional<std::uint64_t> next_group_seq) {
    std::optional<StoredConversation> existing =
        load_conversation(paths, state.group_id(), identity_key, signing_seed);

    StoredConversation conversation =
        existing.has_value() ? std::move(*existing) : StoredConversation{};
    conversation.state = state;
    if (conversation.created_at.empty()) {
        conversation.created_at = storage::now_iso8601_utc();
    }
    conversation.updated_at = storage::now_iso8601_utc();
    if (next_group_seq.has_value()) {
        conversation.next_group_seq =
            std::max<std::uint64_t>(conversation.next_group_seq, *next_group_seq);
    }
    conversation.normalize();
    save_conversation(paths, conversation, identity_key, signing_seed);
    return conversation;
}

std::pair<StoredConversation, bool> append_history(
    const storage::ProfilePaths& paths, const GroupState& state, const HistoryEntry& entry,
    std::optional<ByteView> identity_key, ByteView signing_seed,
    std::optional<std::uint64_t> next_group_seq) {
    std::optional<StoredConversation> existing =
        load_conversation(paths, state.group_id(), identity_key, signing_seed);

    StoredConversation conversation =
        existing.has_value() ? std::move(*existing) : StoredConversation{};
    conversation.state = state;
    if (conversation.created_at.empty()) {
        conversation.created_at = storage::now_iso8601_utc();
    }
    conversation.updated_at = storage::now_iso8601_utc();
    if (next_group_seq.has_value()) {
        conversation.next_group_seq =
            std::max<std::uint64_t>(conversation.next_group_seq, *next_group_seq);
    }

    const std::string msg_id = trimmed(entry.msg_id);
    const bool duplicate =
        !msg_id.empty() && std::find(conversation.seen_msg_ids.begin(),
                                     conversation.seen_msg_ids.end(),
                                     msg_id) != conversation.seen_msg_ids.end();
    if (!duplicate) {
        conversation.history.push_back(entry);
        if (!msg_id.empty()) {
            conversation.seen_msg_ids.push_back(msg_id);
        }
    }
    conversation.normalize();
    save_conversation(paths, conversation, identity_key, signing_seed);
    return {std::move(conversation), !duplicate};
}

std::vector<GroupState> list_states(const storage::ProfilePaths& paths,
                                    std::optional<ByteView> identity_key,
                                    ByteView signing_seed) {
    std::vector<std::pair<std::string, GroupState>> found;
    for (const std::filesystem::path& path : storage::list_group_record_files(paths)) {
        try {
            const std::optional<nlohmann::json> payload =
                read_record_by_token(path, paths.profile(), identity_key);
            if (!payload.has_value()) {
                continue;
            }
            const StoredConversation conversation =
                conversation_from_json(*payload, paths.profile(), signing_seed);
            found.emplace_back(conversation.updated_at, conversation.state);
        } catch (const std::exception&) {
            // One unreadable record must not hide the rest: a group whose file
            // was corrupted is simply missing from the list.
            continue;
        }
    }

    std::stable_sort(found.begin(), found.end(),
                     [](const auto& left, const auto& right) {
                         return left.first > right.first;
                     });

    std::vector<GroupState> states;
    states.reserve(found.size());
    for (auto& [updated_at, state] : found) {
        states.push_back(std::move(state));
    }
    return states;
}

bool delete_record(const storage::ProfilePaths& paths, std::string_view group_id) {
    const std::string id = trimmed(group_id);
    if (id.empty()) {
        return false;
    }
    std::error_code error;
    return std::filesystem::remove(paths.group_store(id), error) && !error;
}

}  // namespace i2pchat::groups
