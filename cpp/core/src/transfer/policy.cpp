#include "i2pchat/transfer/policy.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>

namespace i2pchat::transfer {
namespace {

struct ReasonEntry {
    FailureReason reason;
    std::string_view key;
    bool retryable;
    std::string_view message;
};

constexpr std::array<ReasonEntry, 7> kReasons{{
    {FailureReason::ConnectionLost, "connection_lost", true,
     "Connection lost — will retry"},
    {FailureReason::Timeout, "timeout", true, "Transfer timed out — will retry"},
    {FailureReason::PeerBusy, "peer_busy", true, "Peer is busy — will retry shortly"},
    {FailureReason::PeerRejected, "peer_rejected", false,
     "Recipient declined the transfer"},
    {FailureReason::FileNotFound, "file_not_found", false,
     "File no longer exists on disk"},
    {FailureReason::SizeExceeded, "size_exceeded", false,
     "File exceeds the maximum allowed size"},
    {FailureReason::UserCancelled, "user_cancelled", false, "Transfer cancelled"},
}};

const ReasonEntry* entry_for(FailureReason reason) {
    for (const ReasonEntry& entry : kReasons) {
        if (entry.reason == reason) {
            return &entry;
        }
    }
    return nullptr;
}

std::string format_number(double value, int decimals) {
    std::array<char, 64> buffer{};
    std::snprintf(buffer.data(), buffer.size(), "%.*f", decimals, value);
    return std::string(buffer.data());
}

}  // namespace

RetryDecision should_retry(int attempt, FailureReason reason, const RetryPolicy& policy) {
    const ReasonEntry* const entry = entry_for(reason);
    if (entry == nullptr || !entry->retryable) {
        return {};
    }
    if (attempt > policy.max_retries) {
        return {};
    }
    // Doubling from the base, so a peer that is down is asked less and less
    // often, up to the cap.
    const auto exponent = std::max(0, attempt - 1);
    const auto uncapped = policy.backoff_base * (1LL << exponent);
    return RetryDecision{true, std::min(uncapped, policy.max_backoff)};
}

std::string_view reason_key(FailureReason reason) {
    const ReasonEntry* const entry = entry_for(reason);
    return entry == nullptr ? std::string_view{} : entry->key;
}

FailureReason parse_reason(std::string_view key) {
    for (const ReasonEntry& entry : kReasons) {
        if (entry.key == key) {
            return entry.reason;
        }
    }
    return FailureReason::Unknown;
}

std::string failure_message(std::string_view reason_key) {
    for (const ReasonEntry& entry : kReasons) {
        if (entry.key == reason_key) {
            return std::string(entry.message);
        }
    }
    return "Transfer failed: " + std::string(reason_key);
}

std::string_view state_label(State state) {
    switch (state) {
        case State::Preparing:
            return "Preparing";
        case State::Sending:
            return "Sending";
        case State::Paused:
            return "Paused";
        case State::Failed:
            return "Failed";
        case State::Completed:
            return "Completed";
    }
    return {};
}

std::string_view state_key(State state) {
    switch (state) {
        case State::Preparing:
            return "preparing";
        case State::Sending:
            return "sending";
        case State::Paused:
            return "paused";
        case State::Failed:
            return "failed";
        case State::Completed:
            return "completed";
    }
    return {};
}

double progress_percent(std::uint64_t received, std::uint64_t total) {
    if (total == 0) {
        return 0.0;
    }
    const double percent =
        static_cast<double>(received) / static_cast<double>(total) * 100.0;
    return std::clamp(percent, 0.0, 100.0);
}

bool should_emit_progress(std::uint64_t sent, std::uint64_t chunk_length,
                          std::uint64_t total) {
    if (total == 0) {
        return true;
    }
    const std::uint64_t step = total <= 65536 ? 4096 : 65536;
    const bool first_chunk_done = sent > 0 && sent <= 4096;
    return first_chunk_done || sent % step < chunk_length || sent == total;
}

std::string speed_label(double bytes_per_second) {
    if (bytes_per_second < 0.0) {
        return {};
    }
    if (bytes_per_second < 1024.0) {
        return format_number(bytes_per_second, 0) + " B/s";
    }
    if (bytes_per_second < 1024.0 * 1024.0) {
        return format_number(bytes_per_second / 1024.0, 1) + " KB/s";
    }
    return format_number(bytes_per_second / (1024.0 * 1024.0), 1) + " MB/s";
}

bool timed_out(std::chrono::milliseconds elapsed, std::uint64_t received,
               std::chrono::milliseconds timeout) {
    return elapsed >= timeout && received == 0;
}

}  // namespace i2pchat::transfer
