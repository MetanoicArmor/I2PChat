#pragma once

#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"

/// SAM v3 control protocol: a plain-text, newline-terminated line protocol of
/// space-separated KEY=VALUE tokens.
///
/// Source of truth: i2pchat/sam/protocol.py and i2pchat/sam/errors.py.
namespace i2pchat::sam {

inline constexpr std::string_view kTransientDestination = "TRANSIENT";
inline constexpr int kSigTypeEd25519 = 7;

/// Token values that must never reach a log: a DEST REPLY carries the profile's
/// private identity key.
bool is_sensitive_key(std::string_view key);

/// Replace the value of every sensitive token with a length placeholder.
std::string redact_sam_line(std::string_view raw);

enum class SamErrorKind {
    Protocol,
    CantReachPeer,
    DuplicatedDest,
    DuplicatedId,
    I2pError,
    InvalidId,
    InvalidKey,
    KeyNotFound,
    PeerNotFound,
    Timeout,
    Unknown,
};

class SamError : public std::runtime_error {
public:
    SamError(SamErrorKind kind, const std::string& message, std::string raw_line)
        : std::runtime_error(message), kind_(kind), raw_line_(std::move(raw_line)) {}

    [[nodiscard]] SamErrorKind kind() const noexcept { return kind_; }
    [[nodiscard]] const std::string& raw_line() const noexcept { return raw_line_; }

private:
    SamErrorKind kind_;
    std::string raw_line_;
};

SamErrorKind map_result_to_error_kind(std::string_view result);

struct SamReply {
    std::string command;                        // upper case, e.g. "SESSION"
    std::string topic;                          // upper case, e.g. "STATUS"
    std::map<std::string, std::string> fields;  // keys upper case
    std::string raw_line;                       // already redacted

    [[nodiscard]] std::optional<std::string> field(std::string_view key) const;
};

/// Command builders. Each returns the exact bytes the reference implementation
/// sends, including its trailing-space quirks, so a router that tolerates one
/// implementation tolerates both.
std::string build_hello(std::string_view min_version = "3.0",
                        std::string_view max_version = "3.2");
std::string build_dest_generate(int sig_type = kSigTypeEd25519);
std::string build_naming_lookup(std::string_view name);
std::string build_session_create(std::string_view style, std::string_view session_id,
                                 std::string_view destination,
                                 const std::vector<std::pair<std::string, std::string>>&
                                     options = {},
                                 std::optional<int> sig_type = std::nullopt);
std::string build_stream_connect(std::string_view session_id,
                                 std::string_view destination,
                                 std::string_view silent = "false");
std::string build_stream_accept(std::string_view session_id);
std::string build_stream_forward(std::string_view session_id, int port);

/// Parse one reply line. Throws SamError for empty or malformed input.
SamReply parse_reply_line(std::string_view line);

/// Enforce RESULT=OK, accepting the two implicit-success replies i2pd sends:
/// a DEST REPLY carrying PUB and PRIV, and a SESSION STATUS carrying
/// DESTINATION, both without a RESULT token.
const SamReply& expect_ok(const SamReply& reply, std::string_view result_key = "RESULT");

}  // namespace i2pchat::sam
