#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <string_view>

/// Retry policy and progress reporting for file and image transfers.
///
/// Pure logic, deliberately free of any transport or UI dependency: the same
/// rules have to hold in the terminal client, the Qt client and the tests.
namespace i2pchat::transfer {

enum class State {
    Preparing,
    Sending,
    Paused,
    Failed,
    Completed,
};

/// Why a transfer stopped. The distinction that matters is whether trying again
/// could plausibly succeed: a dropped connection is worth another attempt, a
/// refusal from the recipient is not, and retrying it would be pestering.
enum class FailureReason {
    ConnectionLost,
    Timeout,
    PeerBusy,
    PeerRejected,
    FileNotFound,
    SizeExceeded,
    UserCancelled,
    /// Anything not in the two known sets, which is treated as permanent: an
    /// unrecognised failure is not evidence that retrying helps.
    Unknown,
};

struct RetryPolicy {
    int max_retries = 3;
    std::chrono::milliseconds backoff_base{2000};
    std::chrono::milliseconds max_backoff{30000};
};

struct RetryDecision {
    bool retry = false;
    std::chrono::milliseconds delay{0};
};

/// `attempt` is one-based: the first failure is attempt 1.
[[nodiscard]] RetryDecision should_retry(int attempt, FailureReason reason,
                                         const RetryPolicy& policy = {});

/// The wire and log spelling of a reason, as used by the reference.
[[nodiscard]] std::string_view reason_key(FailureReason reason);
[[nodiscard]] FailureReason parse_reason(std::string_view key);

/// A message for the user. Unknown reasons are reported verbatim rather than
/// swallowed, since the alternative is a transfer that failed for no stated
/// cause.
[[nodiscard]] std::string failure_message(std::string_view reason_key);

/// Empty for a state this version does not know.
[[nodiscard]] std::string_view state_label(State state);
[[nodiscard]] std::string_view state_key(State state);

[[nodiscard]] double progress_percent(std::uint64_t received, std::uint64_t total);

/// Whether a progress update is worth emitting after `chunk_length` bytes
/// brought the total to `sent`.
///
/// Small transfers report every few kilobytes so the bar moves at all; large
/// ones report roughly every 64 KiB, because a repaint per 4 KiB chunk of a
/// gigabyte costs more than the transfer itself.
[[nodiscard]] bool should_emit_progress(std::uint64_t sent, std::uint64_t chunk_length,
                                        std::uint64_t total);

[[nodiscard]] std::string speed_label(double bytes_per_second);

/// A transfer that has produced nothing at all within the timeout is stuck.
[[nodiscard]] bool timed_out(std::chrono::milliseconds elapsed, std::uint64_t received,
                             std::chrono::milliseconds timeout =
                                 std::chrono::milliseconds(60000));

}  // namespace i2pchat::transfer
