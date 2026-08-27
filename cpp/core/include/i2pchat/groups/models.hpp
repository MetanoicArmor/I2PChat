#pragma once

#include <cstdint>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

/// The group conversation model, kept free of transport and storage concerns.
namespace i2pchat::groups {

enum class ContentType { GroupText, GroupControl };

[[nodiscard]] std::string_view content_type_name(ContentType type);
[[nodiscard]] std::optional<ContentType> parse_content_type(std::string_view text);

enum class DeliveryStatus { DeliveredLive, QueuedOffline, Failed };
[[nodiscard]] std::string_view delivery_status_name(DeliveryStatus status);

/// Canonical member id: the strict contact form when the value is an address,
/// otherwise the trimmed lowercase text. Group ids of members that are not
/// addresses do occur in test fixtures and control payloads, so this must not
/// reject them.
[[nodiscard]] std::string normalize_member_id(std::string_view value);

/// The shared view of a group at one epoch.
///
/// `members` is deduplicated and normalised on construction, preserving the
/// order given, because the order is part of the signed payload.
class GroupState {
public:
    GroupState() = default;
    GroupState(std::string group_id, std::uint64_t epoch, std::vector<std::string> members,
               std::string title = {});

    [[nodiscard]] const std::string& group_id() const noexcept { return group_id_; }
    [[nodiscard]] std::uint64_t epoch() const noexcept { return epoch_; }
    [[nodiscard]] const std::vector<std::string>& members() const noexcept {
        return members_;
    }
    /// Empty when the group has no title; the wire format writes `null` then.
    [[nodiscard]] const std::string& title() const noexcept { return title_; }

    [[nodiscard]] bool has_member(std::string_view member_id) const;

private:
    std::string group_id_;
    std::uint64_t epoch_ = 0;
    std::vector<std::string> members_;
    std::string title_;
};

struct GroupEnvelope {
    std::string group_id;
    std::uint64_t epoch = 0;
    std::string msg_id;
    std::string sender_id;
    /// Per-group monotonic counter, starting at 1.
    std::uint64_t group_seq = 0;
    ContentType content_type = ContentType::GroupText;
    /// A string for `GROUP_TEXT`, an object for `GROUP_CONTROL`.
    nlohmann::json payload;
    /// ISO-8601 UTC, as it appears in the signed payload.
    std::string created_at;
};

struct RecipientDelivery {
    std::string recipient_id;
    std::string delivery_id;
};

}  // namespace i2pchat::groups
