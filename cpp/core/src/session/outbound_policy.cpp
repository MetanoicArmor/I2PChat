#include "i2pchat/session/outbound_policy.hpp"

#include <algorithm>
#include <string>

namespace i2pchat::session {
namespace {

std::string trimmed_lower(std::string_view text) {
    constexpr std::string_view kWhitespace = " \t\r\n\f\v";
    const auto first = text.find_first_not_of(kWhitespace);
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(kWhitespace);
    std::string result(text.substr(first, last - first + 1));
    std::transform(result.begin(), result.end(), result.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return result;
}

}  // namespace

OutboundRoute parse_outbound_route(std::string_view text) {
    const std::string value = trimmed_lower(text);
    if (value == "live") {
        return OutboundRoute::Live;
    }
    if (value == "offline") {
        return OutboundRoute::Offline;
    }
    return OutboundRoute::Auto;
}

std::string_view outbound_route_name(OutboundRoute route) {
    switch (route) {
        case OutboundRoute::Live:
            return "live";
        case OutboundRoute::Offline:
            return "offline";
        case OutboundRoute::Auto:
            break;
    }
    return "auto";
}

std::string_view outbound_policy_name(OutboundPolicy policy) {
    switch (policy) {
        case OutboundPolicy::LiveOnly:
            return "LIVE_ONLY";
        case OutboundPolicy::PreferLiveFallbackBlindBox:
            return "PREFER_LIVE_FALLBACK_BLINDBOX";
        case OutboundPolicy::QueueThenRetryLive:
            return "QUEUE_THEN_RETRY_LIVE";
        case OutboundPolicy::BlindBoxOnly:
            break;
    }
    return "BLINDBOX_ONLY";
}

OutboundPolicy select_outbound_policy(OutboundRoute route, bool live_alive) {
    switch (route) {
        case OutboundRoute::Live:
            return OutboundPolicy::LiveOnly;
        case OutboundRoute::Offline:
            return OutboundPolicy::BlindBoxOnly;
        case OutboundRoute::Auto:
            break;
    }
    return live_alive ? OutboundPolicy::PreferLiveFallbackBlindBox
                      : OutboundPolicy::QueueThenRetryLive;
}

}  // namespace i2pchat::session
