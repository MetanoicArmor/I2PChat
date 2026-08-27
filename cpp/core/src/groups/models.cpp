#include "i2pchat/groups/models.hpp"

#include <algorithm>
#include <cctype>
#include <set>

#include "i2pchat/storage/contacts.hpp"

namespace i2pchat::groups {
namespace {

std::string trim_lower(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
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

std::string trim(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    return std::string(text.substr(first, last - first + 1));
}

}  // namespace

std::string_view content_type_name(ContentType type) {
    return type == ContentType::GroupText ? "GROUP_TEXT" : "GROUP_CONTROL";
}

std::optional<ContentType> parse_content_type(std::string_view text) {
    if (text == "GROUP_TEXT") {
        return ContentType::GroupText;
    }
    if (text == "GROUP_CONTROL") {
        return ContentType::GroupControl;
    }
    return std::nullopt;
}

std::string_view delivery_status_name(DeliveryStatus status) {
    switch (status) {
        case DeliveryStatus::DeliveredLive:
            return "delivered_live";
        case DeliveryStatus::QueuedOffline:
            return "queued_offline";
        case DeliveryStatus::Failed:
            return "failed";
    }
    return "failed";
}

std::string normalize_member_id(std::string_view value) {
    const std::string lowered = trim_lower(value);
    if (lowered.empty()) {
        return "";
    }
    const std::string canonical = storage::normalize_contact_address(lowered);
    return canonical.empty() ? lowered : canonical;
}

GroupState::GroupState(std::string group_id, std::uint64_t epoch,
                       std::vector<std::string> members, std::string title)
    : group_id_(trim(group_id)), epoch_(epoch), title_(trim(title)) {
    std::set<std::string> seen;
    for (const std::string& raw : members) {
        const std::string member = normalize_member_id(raw);
        if (member.empty() || !seen.insert(member).second) {
            continue;
        }
        members_.push_back(member);
    }
}

bool GroupState::has_member(std::string_view member_id) const {
    const std::string normalized = normalize_member_id(member_id);
    if (normalized.empty()) {
        return false;
    }
    return std::any_of(members_.begin(), members_.end(),
                       [&normalized](const std::string& member) {
                           return storage::same_i2p_destination(normalized, member);
                       });
}

}  // namespace i2pchat::groups
