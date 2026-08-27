#include "i2pchat/blindbox/replica_client.hpp"

#include <boost/asio/as_tuple.hpp>
#include <boost/asio/bind_cancellation_slot.hpp>
#include <boost/asio/cancel_after.hpp>
#include <boost/asio/cancellation_signal.hpp>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/experimental/concurrent_channel.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/write.hpp>

#include <algorithm>
#include <cctype>
#include <charconv>
#include <deque>
#include <utility>

#include "i2pchat/crypto.hpp"
#include "net/line_reader.hpp"

namespace i2pchat::blindbox {
namespace {

using asio::ip::tcp;
using Clock = std::chrono::steady_clock;

std::string trim(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

std::string to_lower(std::string_view text) {
    std::string lowered(text);
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return lowered;
}

std::optional<std::size_t> parse_size(std::string_view text) {
    std::size_t value = 0;
    const auto* const begin = text.data();
    const auto* const end = begin + text.size();
    const auto result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{} || result.ptr != end) {
        return std::nullopt;
    }
    return value;
}

std::string describe(std::exception_ptr error) {
    try {
        std::rethrow_exception(error);
    } catch (const std::exception& exc) {
        const std::string message = exc.what();
        return message.empty() ? "failed" : message;
    } catch (...) {
        return "unknown error";
    }
}

/// One replica's answer, or the reason it did not give one.
template <typename R>
struct Outcome {
    std::size_t index = 0;
    std::optional<R> value;
    std::string error;
};

/// Runs one operation per replica concurrently and hands back answers as they
/// arrive.
///
/// Quorums mean the caller usually stops caring about the remaining replicas
/// partway through, so the outstanding operations have to be cancellable and,
/// more importantly, have to be *finished* before the caller's frame goes away:
/// each of them holds a reference to this channel. `shutdown` is what makes
/// that safe, and every exit path goes through it.
template <typename R>
class Fanout {
public:
    Fanout(asio::any_io_executor executor, std::size_t capacity)
        : executor_(std::move(executor)), channel_(executor_, std::max<std::size_t>(capacity, 1)) {}

    Fanout(const Fanout&) = delete;
    Fanout& operator=(const Fanout&) = delete;

    template <typename Op>
    void launch(std::size_t index, Op operation) {
        asio::cancellation_signal& signal = signals_.emplace_back();
        ++outstanding_;
        asio::co_spawn(
            executor_, std::move(operation),
            asio::bind_cancellation_slot(
                signal.slot(), [this, index](std::exception_ptr error, R value) {
                    Outcome<R> outcome;
                    outcome.index = index;
                    if (error) {
                        outcome.error = describe(error);
                    } else {
                        outcome.value = std::move(value);
                    }
                    channel_.try_send(boost::system::error_code(), std::move(outcome));
                }));
    }

    /// The next answer, or nothing when `timeout` elapsed first or nothing is
    /// outstanding. `outstanding()` tells those apart.
    asio::awaitable<std::optional<Outcome<R>>> next(
        std::optional<std::chrono::milliseconds> timeout = std::nullopt) {
        if (outstanding_ == 0) {
            co_return std::nullopt;
        }
        if (timeout) {
            auto [error, outcome] = co_await channel_.async_receive(
                asio::cancel_after(*timeout, asio::as_tuple(asio::use_awaitable)));
            if (error) {
                co_return std::nullopt;
            }
            --outstanding_;
            co_return outcome;
        }
        auto [error, outcome] =
            co_await channel_.async_receive(asio::as_tuple(asio::use_awaitable));
        if (error) {
            co_return std::nullopt;
        }
        --outstanding_;
        co_return outcome;
    }

    /// Cancel whatever is still running and wait for it to unwind.
    asio::awaitable<std::vector<std::size_t>> shutdown() {
        std::vector<std::size_t> cancelled;
        for (asio::cancellation_signal& signal : signals_) {
            signal.emit(asio::cancellation_type::all);
        }
        while (outstanding_ > 0) {
            auto [error, outcome] =
                co_await channel_.async_receive(asio::as_tuple(asio::use_awaitable));
            if (error) {
                break;
            }
            --outstanding_;
            cancelled.push_back(outcome.index);
        }
        co_return cancelled;
    }

    [[nodiscard]] std::size_t outstanding() const noexcept { return outstanding_; }

private:
    using Channel =
        asio::experimental::concurrent_channel<void(boost::system::error_code, Outcome<R>)>;

    asio::any_io_executor executor_;
    Channel channel_;
    /// A deque because `asio::cancellation_signal` is neither copyable nor
    /// movable, and the slots handed to co_spawn must not be reseated.
    std::deque<asio::cancellation_signal> signals_;
    std::size_t outstanding_ = 0;
};

}  // namespace

std::string validate_lookup_key(std::string_view key) {
    const std::string token = trim(key);
    if (token.empty()) {
        throw ReplicaError("BlindBox key is required");
    }
    // A space would be read by the replica as the start of an auth token, and a
    // newline as the start of the next command.
    for (const char ch : token) {
        if (ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' || ch == '\0') {
            throw ReplicaError("BlindBox key contains forbidden characters");
        }
    }
    return token;
}

bool is_loopback_endpoint(std::string_view address) {
    std::string host = trim(address);
    const auto colon = host.rfind(':');
    if (colon != std::string::npos) {
        host = host.substr(0, colon);
    }
    host = to_lower(trim(host));
    if (!host.empty() && host.front() == '[' && host.back() == ']') {
        host = host.substr(1, host.size() - 2);
    }
    return host == "127.0.0.1" || host == "localhost" || host == "::1";
}

std::string sam_destination_from_endpoint(std::string_view address) {
    const std::string value = trim(address);
    const auto colon = value.rfind(':');
    if (colon == std::string::npos) {
        return value;
    }
    const std::string host = value.substr(0, colon);
    const std::string port = value.substr(colon + 1);
    const bool numeric_port =
        !port.empty() && std::all_of(port.begin(), port.end(), [](unsigned char ch) {
            return std::isdigit(ch) != 0;
        });
    if (numeric_port && host.size() >= 4 && host.compare(host.size() - 4, 4, ".i2p") == 0) {
        return host;
    }
    return value;
}

StreamFactory direct_stream_factory(asio::any_io_executor executor,
                                    std::chrono::milliseconds timeout) {
    return [executor, timeout](std::string address) -> asio::awaitable<sam::SamStream> {
        const std::string trimmed = trim(address);
        const auto colon = trimmed.rfind(':');
        if (colon == std::string::npos) {
            throw ReplicaError("Direct replica address must be host:port: " + trimmed);
        }
        const std::string host = trim(trimmed.substr(0, colon));
        const std::optional<std::size_t> port = parse_size(trimmed.substr(colon + 1));
        if (host.empty() || !port || *port == 0 || *port > 65535) {
            throw ReplicaError("Direct replica address must be host:port: " + trimmed);
        }

        tcp::socket socket(executor);
        boost::system::error_code parse_error;
        const auto parsed = asio::ip::make_address(host, parse_error);
        if (!parse_error) {
            co_await socket.async_connect(
                tcp::endpoint(parsed, static_cast<std::uint16_t>(*port)),
                asio::cancel_after(timeout, asio::use_awaitable));
        } else {
            tcp::resolver resolver(executor);
            const auto endpoints = co_await resolver.async_resolve(
                host, std::to_string(*port),
                asio::cancel_after(timeout, asio::use_awaitable));
            co_await asio::async_connect(socket, endpoints,
                                         asio::cancel_after(timeout, asio::use_awaitable));
        }
        boost::system::error_code ignored;
        socket.set_option(tcp::no_delay(true), ignored);

        sam::SamStream stream{std::move(socket), {}, {}};
        co_return stream;
    };
}

StreamFactory sam_stream_factory(std::shared_ptr<sam::SamSession> session) {
    return [session = std::move(session)](
               std::string address) -> asio::awaitable<sam::SamStream> {
        if (!session) {
            throw ReplicaError("No SAM session for the BlindBox replica");
        }
        const std::string destination = sam_destination_from_endpoint(address);
        if (destination.empty()) {
            throw ReplicaError("Empty replica destination");
        }

        // A b32 address is resolved first because a router that already knows
        // the lease set connects faster by full destination; when it does not
        // know it yet the lookup fails and the raw b32 is still worth a try.
        std::vector<std::string> candidates;
        const bool is_b32 = destination.size() > 8 &&
                            destination.compare(destination.size() - 8, 8, ".b32.i2p") == 0;
        const bool is_name = destination.size() > 4 &&
                             destination.compare(destination.size() - 4, 4, ".i2p") == 0;
        if (is_name) {
            try {
                candidates.push_back(co_await session->naming_lookup(destination));
            } catch (const std::exception&) {
                if (!is_b32) {
                    throw;
                }
            }
        }
        if (candidates.empty() || is_b32) {
            candidates.push_back(destination);
        }

        std::string errors;
        for (const std::string& candidate : candidates) {
            try {
                co_return co_await session->connect_stream(candidate);
            } catch (const std::exception& exc) {
                if (!errors.empty()) {
                    errors += "; ";
                }
                errors += exc.what();
            }
        }
        throw ReplicaError("Replica STREAM CONNECT failed: " + errors);
    };
}

struct ReplicaClient::Impl {
    Impl(asio::any_io_executor exec, ReplicaClientConfig cfg, StreamFactory factory)
        : executor(std::move(exec)),
          config(std::move(cfg)),
          stream_factory(std::move(factory)) {}

    asio::any_io_executor executor;
    ReplicaClientConfig config;
    StreamFactory stream_factory;

    [[nodiscard]] std::string auth_suffix(const std::string& address) const {
        const std::string token = token_for(address);
        return token.empty() ? std::string() : " " + token;
    }

    [[nodiscard]] std::string token_for(const std::string& address) const {
        const std::string key = trim(address);
        const auto mapped = config.replica_auth.find(key);
        if (mapped != config.replica_auth.end()) {
            const std::string token = trim(mapped->second);
            if (!token.empty()) {
                return token;
            }
        }
        const std::string local = trim(config.local_auth_token);
        if (!local.empty() && is_loopback_endpoint(key)) {
            return local;
        }
        return {};
    }

    asio::awaitable<void> backoff(int attempt) {
        const auto delay = config.retry_backoff_base * (1 << (attempt - 1));
        asio::steady_timer timer(executor);
        timer.expires_after(delay);
        co_await timer.async_wait(asio::use_awaitable);
    }

    asio::awaitable<PutStatus> put_once(std::string address, std::string key, Bytes blob) {
        sam::SamStream stream = co_await stream_factory(address);
        const std::string header =
            "PUT " + key + " " + std::to_string(blob.size()) + auth_suffix(address) + "\n";

        co_await asio::async_write(stream.socket, asio::buffer(header),
                                   asio::cancel_after(config.io_timeout,
                                                      asio::use_awaitable));
        co_await asio::async_write(stream.socket, asio::buffer(blob),
                                   asio::cancel_after(config.io_timeout,
                                                      asio::use_awaitable));

        net::LineReader reader(stream.socket, std::move(stream.prebuffered),
                               config.io_timeout);
        const std::optional<std::string> line = co_await reader.read_line();
        const std::string status = line ? trim(*line) : std::string();
        if (status == "OK") {
            co_return PutStatus::Ok;
        }
        if (status == "EXISTS") {
            co_return PutStatus::Exists;
        }
        throw ReplicaError("Unexpected PUT response: '" + status + "'");
    }

    asio::awaitable<std::optional<Bytes>> get_once(std::string address, std::string key) {
        sam::SamStream stream = co_await stream_factory(address);
        const std::string command = "GET " + key + auth_suffix(address) + "\n";
        co_await asio::async_write(stream.socket, asio::buffer(command),
                                   asio::cancel_after(config.io_timeout,
                                                      asio::use_awaitable));

        net::LineReader reader(stream.socket, std::move(stream.prebuffered),
                               config.io_timeout);
        const std::optional<std::string> line = co_await reader.read_line();
        const std::string header = line ? trim(*line) : std::string();
        if (header == "MISS") {
            co_return std::nullopt;
        }
        if (!header.starts_with("OK ")) {
            throw ReplicaError("Unexpected GET response: '" + header + "'");
        }
        const std::optional<std::size_t> size = parse_size(trim(header.substr(3)));
        if (!size) {
            throw ReplicaError("Malformed GET header: '" + header + "'");
        }
        if (*size == 0) {
            throw ReplicaError("Invalid blob size in GET response");
        }
        // A replica is untrusted, so its declared size is a claim, not a
        // promise: refuse it before allocating.
        if (*size > config.max_blob_size) {
            throw ReplicaError("GET blob size " + std::to_string(*size) +
                               " exceeds limit " + std::to_string(config.max_blob_size));
        }

        std::optional<Bytes> blob = co_await reader.read_exactly(*size);
        if (!blob) {
            throw ReplicaError("Replica closed the connection mid-blob");
        }
        co_return blob;
    }

    asio::awaitable<PutStatus> put_with_retries(std::string address, std::string key,
                                                Bytes blob, int attempts) {
        std::string last_error;
        for (int attempt = 1; attempt <= attempts; ++attempt) {
            try {
                co_return co_await put_once(address, key, blob);
            } catch (const std::exception& exc) {
                last_error = exc.what();
            }
            if (attempt < attempts) {
                co_await backoff(attempt);
            }
        }
        throw ReplicaError(last_error.empty() ? "PUT failed" : last_error);
    }

    asio::awaitable<std::optional<Bytes>> get_with_retries(std::string address,
                                                           std::string key, int attempts) {
        std::string last_error;
        for (int attempt = 1; attempt <= attempts; ++attempt) {
            try {
                co_return co_await get_once(address, key);
            } catch (const std::exception& exc) {
                last_error = exc.what();
            }
            if (attempt < attempts) {
                co_await backoff(attempt);
            }
        }
        throw ReplicaError(last_error.empty() ? "GET failed" : last_error);
    }

    /// A replica answering EXISTS has *something* in the slot. That counts as
    /// delivered only if it is this exact blob: a slot holding a different blob
    /// means this message was never stored, and reporting success would drop it
    /// silently.
    asio::awaitable<bool> exists_holds_our_blob(std::string address, std::string key,
                                                const Bytes& blob) {
        const std::optional<Bytes> stored = co_await get_once(address, key);
        co_return stored.has_value() && *stored == blob;
    }
};

ReplicaClient::ReplicaClient(asio::any_io_executor executor, ReplicaClientConfig config,
                             StreamFactory stream_factory) {
    if (config.endpoints.empty()) {
        throw ReplicaError("At least one BlindBox replica is required");
    }
    if (config.put_quorum < 1 || config.put_quorum > config.endpoints.size()) {
        throw ReplicaError("put_quorum must be between 1 and the number of replicas");
    }
    if (config.get_quorum < 1 || config.get_quorum > config.endpoints.size()) {
        throw ReplicaError("get_quorum must be between 1 and the number of replicas");
    }
    if (config.retry_attempts < 1) {
        throw ReplicaError("retry_attempts must be at least 1");
    }
    if (config.io_timeout <= std::chrono::milliseconds::zero()) {
        throw ReplicaError("io_timeout must be positive");
    }
    if (config.get_first_accept_grace < std::chrono::milliseconds::zero()) {
        throw ReplicaError("get_first_accept_grace must not be negative");
    }
    if (config.max_blob_size == 0) {
        throw ReplicaError("max_blob_size must be positive");
    }
    if (!stream_factory) {
        throw ReplicaError("A stream factory is required");
    }

    config_ = config;
    impl_ = std::make_unique<Impl>(std::move(executor), std::move(config),
                                   std::move(stream_factory));
}

ReplicaClient::~ReplicaClient() = default;

std::string ReplicaClient::auth_token_for(const std::string& address) const {
    return impl_->token_for(address);
}

asio::awaitable<std::vector<PutResult>> ReplicaClient::put(std::string key, Bytes blob) {
    key = validate_lookup_key(key);
    if (blob.empty()) {
        throw ReplicaError("Refusing to store an empty blob");
    }

    Impl& impl = *impl_;
    std::vector<PutResult> answered;
    std::vector<std::string> failures;
    std::size_t stored = 0;

    if (impl.config.put_quorum == 1) {
        // One healthy replica is enough, so a fanout across every replica would
        // only add latency and traffic. Two are tried in case the first is down.
        const std::size_t targets = std::min<std::size_t>(2, impl.config.endpoints.size());
        for (std::size_t i = 0; i < targets && stored < 1; ++i) {
            const std::string& address = impl.config.endpoints[i];
            PutStatus status = PutStatus::Ok;
            try {
                status = co_await impl.put_with_retries(address, key, blob, 1);
            } catch (const std::exception& exc) {
                failures.emplace_back(std::string(exc.what()));
                continue;
            }
            answered.push_back(PutResult{address, status});
            if (status == PutStatus::Ok) {
                ++stored;
                continue;
            }
            try {
                if (co_await impl.exists_holds_our_blob(address, key, blob)) {
                    ++stored;
                } else {
                    failures.emplace_back("PUT EXISTS verification mismatch");
                }
            } catch (const std::exception& exc) {
                failures.emplace_back(std::string("PUT EXISTS verification failed: ") +
                                      exc.what());
            }
        }
    } else {
        Fanout<PutStatus> fanout(impl.executor, impl.config.endpoints.size());
        for (std::size_t i = 0; i < impl.config.endpoints.size(); ++i) {
            fanout.launch(i, impl.put_with_retries(impl.config.endpoints[i], key, blob,
                                                   impl.config.retry_attempts));
        }

        while (stored < impl.config.put_quorum && fanout.outstanding() > 0) {
            const std::optional<Outcome<PutStatus>> outcome = co_await fanout.next();
            if (!outcome) {
                break;
            }
            const std::string& address = impl.config.endpoints[outcome->index];
            if (!outcome->value) {
                failures.push_back(outcome->error);
                continue;
            }
            answered.push_back(PutResult{address, *outcome->value});
            if (*outcome->value == PutStatus::Ok) {
                ++stored;
                continue;
            }
            try {
                if (co_await impl.exists_holds_our_blob(address, key, blob)) {
                    ++stored;
                } else {
                    failures.emplace_back("PUT EXISTS verification mismatch");
                }
            } catch (const std::exception& exc) {
                failures.emplace_back(std::string("PUT EXISTS verification failed: ") +
                                      exc.what());
            }
        }
        co_await fanout.shutdown();
    }

    if (stored < impl.config.put_quorum) {
        std::string detail;
        if (!failures.empty()) {
            detail = " (first failure: " + failures.front() + ")";
        }
        throw ReplicaError("BlindBox PUT quorum not reached: " + std::to_string(stored) +
                           "/" + std::to_string(impl.config.put_quorum) + detail);
    }
    co_return answered;
}

asio::awaitable<std::vector<Bytes>> ReplicaClient::get(std::string key,
                                                       bool require_quorum) {
    key = validate_lookup_key(key);
    Impl& impl = *impl_;

    Fanout<std::optional<Bytes>> fanout(impl.executor, impl.config.endpoints.size());
    for (std::size_t i = 0; i < impl.config.endpoints.size(); ++i) {
        fanout.launch(i, impl.get_with_retries(impl.config.endpoints[i], key,
                                               impl.config.retry_attempts));
    }

    std::vector<Bytes> blobs;
    std::size_t reachable = 0;
    while (fanout.outstanding() > 0) {
        const std::optional<Outcome<std::optional<Bytes>>> outcome = co_await fanout.next();
        if (!outcome) {
            break;
        }
        if (!outcome->value) {
            continue;
        }
        // An empty slot is an answer: the replica was reachable and holds
        // nothing. Only an unreachable replica is a failure.
        ++reachable;
        if (outcome->value->has_value()) {
            blobs.push_back(**outcome->value);
        }
    }
    co_await fanout.shutdown();

    if (require_quorum && reachable < impl.config.get_quorum) {
        throw ReplicaError("BlindBox GET quorum not reached: " +
                           std::to_string(reachable) + "/" +
                           std::to_string(impl.config.get_quorum));
    }

    // Replicas are expected to hold the same blob, so the caller is spared the
    // duplicates. Comparing digests rather than bytes keeps this cheap for the
    // megabyte-scale frames a file transfer produces.
    std::vector<Bytes> unique;
    std::vector<Bytes> seen;
    for (Bytes& blob : blobs) {
        Bytes digest = crypto::sha256(ByteView(blob));
        if (std::find(seen.begin(), seen.end(), digest) != seen.end()) {
            continue;
        }
        seen.push_back(std::move(digest));
        unique.push_back(std::move(blob));
    }
    co_return unique;
}

asio::awaitable<std::optional<Bytes>> ReplicaClient::get_first_accepted(
    std::string key, BlobAcceptor accept, std::optional<std::chrono::milliseconds> grace,
    GetFirstDiagnostics* diagnostics) {
    key = validate_lookup_key(key);
    if (!accept) {
        throw ReplicaError("An acceptor is required");
    }
    Impl& impl = *impl_;
    const auto started = Clock::now();
    const std::chrono::milliseconds patience =
        grace.value_or(impl.config.get_first_accept_grace);

    if (diagnostics != nullptr) {
        *diagnostics = GetFirstDiagnostics{};
    }
    const auto record_elapsed = [&] {
        if (diagnostics != nullptr) {
            diagnostics->elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                Clock::now() - started);
        }
    };

    // A single attempt per replica: the poll loop will come round again shortly,
    // and retrying here only delays the answer a healthy replica already gave.
    Fanout<std::optional<Bytes>> fanout(impl.executor, impl.config.endpoints.size());
    for (std::size_t i = 0; i < impl.config.endpoints.size(); ++i) {
        fanout.launch(i, impl.get_with_retries(impl.config.endpoints[i], key, 1));
    }

    std::optional<Clock::time_point> soft_deadline;
    std::optional<Bytes> accepted;
    while (fanout.outstanding() > 0 && !accepted) {
        std::optional<std::chrono::milliseconds> timeout;
        if (soft_deadline) {
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                *soft_deadline - Clock::now());
            if (remaining <= std::chrono::milliseconds::zero()) {
                break;
            }
            timeout = remaining;
        }

        const std::optional<Outcome<std::optional<Bytes>>> outcome =
            co_await fanout.next(timeout);
        if (!outcome) {
            if (diagnostics != nullptr && diagnostics->first_result_kind.empty()) {
                diagnostics->first_result_kind = "timeout";
            }
            break;
        }

        const std::string& address = impl.config.endpoints[outcome->index];
        if (diagnostics != nullptr) {
            diagnostics->completed.push_back(address);
        }

        const auto note_first = [&](std::string kind) {
            if (diagnostics != nullptr && diagnostics->first_result_kind.empty()) {
                diagnostics->first_result_kind = std::move(kind);
                diagnostics->first_result_address = address;
            }
        };

        if (!outcome->value) {
            note_first("error");
        } else if (!outcome->value->has_value()) {
            note_first("miss");
        } else {
            note_first("blob");
            const Bytes& blob = **outcome->value;
            if (accept(ByteView(blob))) {
                accepted = blob;
                if (diagnostics != nullptr) {
                    diagnostics->accepted_address = address;
                }
                break;
            }
        }

        // The first answer starts the clock on the rest: a replica that has not
        // spoken by now is either slow or down, and either way the poll should
        // not stall on it.
        if (!soft_deadline && fanout.outstanding() > 0) {
            soft_deadline = Clock::now() + patience;
        }
    }

    const std::vector<std::size_t> cancelled = co_await fanout.shutdown();
    if (diagnostics != nullptr) {
        for (const std::size_t index : cancelled) {
            diagnostics->cancelled.push_back(impl.config.endpoints[index]);
        }
        std::sort(diagnostics->cancelled.begin(), diagnostics->cancelled.end());
    }
    record_elapsed();
    co_return accepted;
}

}  // namespace i2pchat::blindbox
