#include "i2pchat/sam/protocol.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <regex>
#include <set>
#include <sstream>

namespace i2pchat::sam {
namespace {

std::string to_upper(std::string_view text) {
    std::string out(text);
    std::transform(out.begin(), out.end(), out.begin(), [](unsigned char ch) {
        return static_cast<char>(std::toupper(ch));
    });
    return out;
}

std::string trim(std::string_view text) {
    const auto is_space = [](unsigned char ch) { return std::isspace(ch) != 0; };
    std::size_t begin = 0;
    while (begin < text.size() && is_space(static_cast<unsigned char>(text[begin]))) {
        ++begin;
    }
    std::size_t end = text.size();
    while (end > begin && is_space(static_cast<unsigned char>(text[end - 1]))) {
        --end;
    }
    return std::string(text.substr(begin, end - begin));
}

/// Reject characters that would let a caller inject extra SAM commands.
std::string validate_token(std::string_view value, std::string_view field_name,
                           bool allow_equals) {
    const std::string token = trim(value);
    if (token.empty()) {
        throw SamError(SamErrorKind::Protocol,
                       "SAM " + std::string(field_name) + " is empty", "");
    }
    static constexpr std::array<char, 5> kForbidden = {'\r', '\n', '\0', ' ', '\t'};
    for (const char ch : kForbidden) {
        if (token.find(ch) != std::string::npos) {
            throw SamError(SamErrorKind::Protocol,
                           "SAM " + std::string(field_name) +
                               " contains forbidden characters",
                           "");
        }
    }
    if (!allow_equals && token.find('=') != std::string::npos) {
        throw SamError(SamErrorKind::Protocol,
                       "SAM " + std::string(field_name) +
                           " contains forbidden characters",
                       "");
    }
    return token;
}

std::string validate_version(std::string_view value, std::string_view field_name) {
    const std::string token = validate_token(value, field_name, false);
    static const std::regex kPattern(R"(\d+\.\d+)");
    if (!std::regex_match(token, kPattern)) {
        throw SamError(SamErrorKind::Protocol,
                       "SAM " + std::string(field_name) + " version is invalid", "");
    }
    return token;
}

std::string validate_style(std::string_view value) {
    const std::string token = to_upper(validate_token(value, "STYLE", false));
    if (token != "STREAM" && token != "DATAGRAM" && token != "RAW") {
        throw SamError(SamErrorKind::Protocol, "SAM STYLE is invalid", "");
    }
    return token;
}

std::string validate_boolish(std::string_view value, std::string_view field_name) {
    std::string token = validate_token(value, field_name, false);
    std::transform(token.begin(), token.end(), token.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    if (token != "true" && token != "false") {
        throw SamError(SamErrorKind::Protocol,
                       "SAM " + std::string(field_name) + " is invalid", "");
    }
    return token;
}

std::vector<std::string> split_whitespace(std::string_view text) {
    std::vector<std::string> parts;
    std::istringstream stream{std::string(text)};
    std::string token;
    while (stream >> token) {
        parts.push_back(token);
    }
    return parts;
}

}  // namespace

bool is_sensitive_key(std::string_view key) {
    static const std::set<std::string> kSensitive{"PRIV", "PRIVATE", "DESTINATION",
                                                  "SIGNING_PRIVATE_KEY"};
    return kSensitive.count(to_upper(key)) > 0;
}

std::string redact_sam_line(std::string_view raw) {
    if (raw.empty()) {
        return std::string(raw);
    }
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start <= raw.size()) {
        const std::size_t end = raw.find(' ', start);
        const std::string token(raw.substr(
            start, end == std::string_view::npos ? std::string_view::npos : end - start));
        const std::size_t eq = token.find('=');
        if (eq != std::string::npos) {
            const std::string key = token.substr(0, eq);
            const std::string value = token.substr(eq + 1);
            if (is_sensitive_key(key) && !value.empty()) {
                out.push_back(key + "=<redacted:" + std::to_string(value.size()) + "b>");
            } else {
                out.push_back(token);
            }
        } else {
            out.push_back(token);
        }
        if (end == std::string_view::npos) {
            break;
        }
        start = end + 1;
    }
    std::string joined;
    for (std::size_t i = 0; i < out.size(); ++i) {
        if (i > 0) {
            joined.push_back(' ');
        }
        joined += out[i];
    }
    return joined;
}

SamErrorKind map_result_to_error_kind(std::string_view result) {
    const std::string upper = to_upper(result);
    if (upper == "CANT_REACH_PEER") return SamErrorKind::CantReachPeer;
    if (upper == "DUPLICATED_DEST") return SamErrorKind::DuplicatedDest;
    if (upper == "DUPLICATED_ID") return SamErrorKind::DuplicatedId;
    if (upper == "I2P_ERROR") return SamErrorKind::I2pError;
    if (upper == "INVALID_ID") return SamErrorKind::InvalidId;
    if (upper == "INVALID_KEY") return SamErrorKind::InvalidKey;
    if (upper == "KEY_NOT_FOUND") return SamErrorKind::KeyNotFound;
    if (upper == "PEER_NOT_FOUND") return SamErrorKind::PeerNotFound;
    if (upper == "TIMEOUT") return SamErrorKind::Timeout;
    return SamErrorKind::Unknown;
}

std::optional<std::string> SamReply::field(std::string_view key) const {
    const auto it = fields.find(to_upper(key));
    if (it == fields.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::string build_hello(std::string_view min_version, std::string_view max_version) {
    const std::string min_v = validate_version(min_version, "MIN");
    const std::string max_v = validate_version(max_version, "MAX");
    return "HELLO VERSION MIN=" + min_v + " MAX=" + max_v + "\n";
}

std::string build_dest_generate(int sig_type) {
    return "DEST GENERATE SIGNATURE_TYPE=" + std::to_string(sig_type) + "\n";
}

std::string build_naming_lookup(std::string_view name) {
    return "NAMING LOOKUP NAME=" + validate_token(name, "NAME", false) + "\n";
}

std::string build_session_create(
    std::string_view style, std::string_view session_id, std::string_view destination,
    const std::vector<std::pair<std::string, std::string>>& options,
    std::optional<int> sig_type) {
    // I2CP and streaming options are plain name=value tokens after DESTINATION;
    // SAM routers reject a standalone "OPTION" keyword.
    std::string option_string;
    if (sig_type.has_value()) {
        option_string = "SIGNATURE_TYPE=" + std::to_string(*sig_type);
    }
    for (const auto& [key, value] : options) {
        const std::string key_s = validate_token(key, "OPTION_KEY", false);
        const std::string val_s = validate_token(value, "OPTION_VALUE", false);
        if (!option_string.empty()) {
            option_string.push_back(' ');
        }
        option_string += key_s + "=" + val_s;
    }

    const std::string style_s = validate_style(style);
    const std::string session_s = validate_token(session_id, "ID", false);
    const std::string dest_s = validate_token(destination, "DESTINATION", true);

    // The space before the options is unconditional, matching the reference
    // implementation: with no options the line carries a trailing space.
    return "SESSION CREATE STYLE=" + style_s + " ID=" + session_s + " DESTINATION=" +
           dest_s + " " + option_string + "\n";
}

std::string build_stream_connect(std::string_view session_id,
                                 std::string_view destination,
                                 std::string_view silent) {
    const std::string sid = validate_token(session_id, "ID", false);
    const std::string dest = validate_token(destination, "DESTINATION", true);
    const std::string silent_flag = validate_boolish(silent, "SILENT");
    return "STREAM CONNECT ID=" + sid + " DESTINATION=" + dest + " SILENT=" +
           silent_flag + "\n";
}

std::string build_stream_accept(std::string_view session_id) {
    return "STREAM ACCEPT ID=" + validate_token(session_id, "ID", false) +
           " SILENT=false\n";
}

std::string build_stream_forward(std::string_view session_id, int port) {
    if (port < 1 || port > 65535) {
        throw SamError(SamErrorKind::Protocol, "SAM PORT is out of range", "");
    }
    // Trailing space before the newline is present in the reference builder.
    return "STREAM FORWARD ID=" + validate_token(session_id, "ID", false) + " PORT=" +
           std::to_string(port) + " \n";
}

SamReply parse_reply_line(std::string_view line) {
    const std::string raw = trim(line);
    if (raw.empty()) {
        throw SamError(SamErrorKind::Protocol, "Empty SAM reply", "");
    }
    const std::vector<std::string> parts = split_whitespace(raw);
    if (parts.size() < 2) {
        throw SamError(SamErrorKind::Protocol, "Malformed SAM reply",
                       redact_sam_line(raw));
    }

    SamReply reply;
    reply.command = to_upper(parts[0]);
    reply.topic = to_upper(parts[1]);
    for (std::size_t i = 2; i < parts.size(); ++i) {
        const std::size_t eq = parts[i].find('=');
        if (eq == std::string::npos) {
            continue;
        }
        reply.fields[to_upper(parts[i].substr(0, eq))] = parts[i].substr(eq + 1);
    }
    reply.raw_line = redact_sam_line(raw);
    return reply;
}

const SamReply& expect_ok(const SamReply& reply, std::string_view result_key) {
    const std::optional<std::string> raw_result = reply.field(result_key);
    const std::string result = raw_result.has_value() ? to_upper(*raw_result) : "";
    if (result == "OK") {
        return reply;
    }
    if (result.empty()) {
        // i2pd omits RESULT=OK on a successful DEST GENERATE and often on
        // SESSION CREATE; treat the payload-bearing replies as implicit OK.
        if (reply.command == "DEST" && reply.topic == "REPLY" &&
            reply.field("PUB").has_value() && reply.field("PRIV").has_value()) {
            return reply;
        }
        if (reply.command == "SESSION" && reply.topic == "STATUS" &&
            reply.field("DESTINATION").has_value()) {
            return reply;
        }
        throw SamError(SamErrorKind::Protocol, "SAM reply missing RESULT",
                       reply.raw_line);
    }
    const std::string message =
        reply.field("MESSAGE").value_or(reply.command + " " + reply.topic + " failed");
    throw SamError(map_result_to_error_kind(result), message, reply.raw_line);
}

}  // namespace i2pchat::sam
