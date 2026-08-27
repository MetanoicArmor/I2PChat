#include <catch2/catch_test_macros.hpp>

#include <boost/asio/connect.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>

#include <fstream>
#include <string>

#include <nlohmann/json.hpp>

#include "i2pchat/blindbox/replica_client.hpp"
#include "i2pchat/blindbox/replica_server.hpp"
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>

#include "temp_dir.hpp"

using namespace i2pchat;
using namespace i2pchat::blindbox;
using i2pchat::testing::TempDir;

namespace {

ReplicaServerConfig config_in(const TempDir& dir) {
    ReplicaServerConfig config;
    config.base_dir = dir.path();
    // A test must never bind a fixed port: a developer running the daemon
    // would make the suite fail.
    config.port = 0;
    config.http_port = 0;
    config.gc_interval = std::chrono::seconds(3600);
    return config;
}

/// A clock the test drives, so time-to-live and rate limits can be exercised
/// without sleeping.
struct FakeClock {
    std::int64_t seconds = 1'700'000'000;
    std::function<std::int64_t()> fn() {
        return [this] { return seconds; };
    }
};

/// Run one coroutine while a server is listening.
///
/// Waiting for the context to run dry never works with a server on it: its
/// accept loop and sweep timer are permanent work. This stops the context as
/// soon as the coroutine finishes and restarts it, leaving the server's pending
/// operations in place for the next step.
template <typename Awaitable>
auto run_step(boost::asio::io_context& context, Awaitable awaitable) {
    using Value = typename Awaitable::value_type;
    std::exception_ptr failure;
    std::optional<Value> result;
    boost::asio::co_spawn(
        context,
        [&]() -> boost::asio::awaitable<void> {
            try {
                result.emplace(co_await std::move(awaitable));
            } catch (...) {
                failure = std::current_exception();
            }
            context.stop();
        },
        boost::asio::detached);
    context.run();
    context.restart();
    if (failure) {
        std::rethrow_exception(failure);
    }
    REQUIRE(result.has_value());
    return std::move(*result);
}

Bytes blob_of(std::string_view text) {
    const ByteView view = as_bytes(text);
    return Bytes(view.begin(), view.end());
}

std::string read_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in),
                       std::istreambuf_iterator<char>());
}

std::string put_body(ReplicaService& service, const std::string& key,
                     std::string_view body, const std::string& token = "") {
    const std::string header =
        "PUT " + key + " " + std::to_string(body.size()) + (token.empty() ? "" : " " + token);
    ReplicaService::Plan plan = service.plan(header, PeerInfo{"198.51.100.7", 40001});
    if (!plan.pending_put) {
        return plan.reply;
    }
    return service.complete_put(*plan.pending_put, as_bytes(body),
                                PeerInfo{"198.51.100.7", 40001});
}

}  // namespace

TEST_CASE("dotenv parsing ignores comments and strips quotes") {
    const auto values = parse_dotenv(
        "# a comment\n"
        "\n"
        "BLINDBOX_PORT=19555\n"
        "  BLINDBOX_AUTH_TOKEN = \"quoted secret\"  \n"
        "BLINDBOX_ADMIN_TOKEN='single'\n"
        "malformed line without equals\n"
        "=novalue\n"
        "BLINDBOX_LOG_JSON=0\n");

    CHECK(values.at("BLINDBOX_PORT") == "19555");
    CHECK(values.at("BLINDBOX_AUTH_TOKEN") == "quoted secret");
    CHECK(values.at("BLINDBOX_ADMIN_TOKEN") == "single");
    CHECK(values.at("BLINDBOX_LOG_JSON") == "0");
    CHECK(values.count("malformed line without equals") == 0);
    CHECK(values.count("") == 0);
}

TEST_CASE("environment configuration applies defaults and overrides") {
    const std::map<std::string, std::string> environment{
        {"HOME", "/tmp/does-not-exist-i2pchat"},
        {"BLINDBOX_PORT", "19999"},
        {"BLINDBOX_HOST", "0.0.0.0"},
        {"BLINDBOX_TTL_SEC", "60"},
        {"BLINDBOX_MAX_BLOB", "2048"},
        {"BLINDBOX_AUTH_TOKEN", " padded-token "},
        {"BLINDBOX_LOG_JSON", "no"},
        {"BLINDBOX_HTTP_STATUS", "yes"},
        {"BLINDBOX_MAX_FILES", "garbage"},
    };
    const ReplicaServerConfig config = config_from_environment(
        [&](const std::string& name) -> std::optional<std::string> {
            const auto found = environment.find(name);
            if (found == environment.end()) {
                return std::nullopt;
            }
            return found->second;
        });

    CHECK(config.port == 19999);
    CHECK(config.host == "0.0.0.0");
    CHECK(config.ttl == std::chrono::seconds(60));
    CHECK(config.max_blob == 2048);
    CHECK(config.auth_token == "padded-token");
    CHECK_FALSE(config.log_json);
    CHECK(config.http_status);
    // An unparseable value falls back to the default rather than to zero, which
    // would silently disable a quota.
    CHECK(config.max_files == 4096);
    CHECK(config.base_dir ==
          std::filesystem::path("/tmp/does-not-exist-i2pchat") / ".i2pchat-blindbox");
}

TEST_CASE("store files blobs under a hashed two-level path") {
    TempDir dir;
    ReplicaStore store(config_in(dir));

    const std::filesystem::path path = store.path_for_key("token-one");
    CHECK(path.parent_path().parent_path() == store.store_dir());
    CHECK(path.parent_path().filename().string().size() == 2);
    CHECK(path.parent_path().filename() == store.prefix_for_key("token-one"));
    // The token must not appear in the path: it is chosen by the client.
    CHECK(path.filename().string().find("token-one") == std::string::npos);
    CHECK(path.filename().string().size() == 64);

    // A path traversal attempt is just another string to hash.
    const std::filesystem::path escape = store.path_for_key("../../etc/passwd");
    CHECK(escape.parent_path().parent_path() == store.store_dir());
    CHECK(escape.filename().string().size() == 64);
}

TEST_CASE("store round-trips a blob and refuses to overwrite it") {
    TempDir dir;
    ReplicaStore store(config_in(dir));

    CHECK(store.put("k", blob_of("payload")) == ReplicaStore::PutOutcome::Stored);
    const std::optional<Bytes> fetched = store.get("k");
    REQUIRE(fetched.has_value());
    CHECK(to_string(ByteView(*fetched)) == "payload");

    // A taken slot is reported rather than clobbered: the sender checks whether
    // what is there is its own blob.
    CHECK(store.put("k", blob_of("other")) == ReplicaStore::PutOutcome::Exists);
    CHECK(to_string(ByteView(*store.get("k"))) == "payload");

    CHECK_FALSE(store.get("absent").has_value());
}

TEST_CASE("store expires blobs past the time to live") {
    TempDir dir;
    FakeClock clock;
    ReplicaServerConfig config = config_in(dir);
    config.ttl = std::chrono::seconds(100);
    ReplicaStore store(config, clock.fn());

    REQUIRE(store.put("k", blob_of("payload")) == ReplicaStore::PutOutcome::Stored);
    CHECK(store.stats().files == 1);

    clock.seconds += 99;
    CHECK(store.get("k").has_value());

    clock.seconds += 2;
    CHECK_FALSE(store.get("k").has_value());
    CHECK(store.stats().files == 0);
    // The expired file is gone from disk, not merely hidden.
    CHECK_FALSE(std::filesystem::exists(store.path_for_key("k")));

    // The slot is free again, which is what lets a sender reuse a key after the
    // window has passed.
    CHECK(store.put("k", blob_of("fresh")) == ReplicaStore::PutOutcome::Stored);
}

TEST_CASE("store prunes the oldest blobs to stay within quotas") {
    TempDir dir;
    FakeClock clock;
    ReplicaServerConfig config = config_in(dir);
    config.max_files = 3;
    config.max_total_bytes = 1024;
    ReplicaStore store(config, clock.fn());

    for (const char* key : {"a", "b", "c"}) {
        REQUIRE(store.put(key, blob_of("xxxx")) == ReplicaStore::PutOutcome::Stored);
        clock.seconds += 10;
    }
    REQUIRE(store.stats().files == 3);

    REQUIRE(store.put("d", blob_of("xxxx")) == ReplicaStore::PutOutcome::Stored);
    CHECK(store.stats().files == 3);
    // Oldest first, so the blobs most likely to still be awaited survive.
    CHECK_FALSE(store.get("a").has_value());
    CHECK(store.get("d").has_value());
}

TEST_CASE("store refuses a blob larger than the total quota") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.max_total_bytes = 8;
    ReplicaStore store(config);

    CHECK(store.put("big", blob_of(std::string(16, 'x'))) ==
          ReplicaStore::PutOutcome::Full);
    CHECK(store.stats().files == 0);
}

TEST_CASE("per-prefix quotas cap one shard of the store") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.max_prefix_files = 1;
    config.max_prefix_bytes = 0;
    ReplicaStore store(config);

    // Two keys landing in different prefixes are unaffected by each other, so
    // the quota is checked against the prefix the key actually falls into.
    CHECK(store.prefix_admits("first", 4));
    REQUIRE(store.put("first", blob_of("xxxx")) == ReplicaStore::PutOutcome::Stored);
    CHECK_FALSE(store.prefix_admits("first", 4));
    CHECK(store.prefix_stats(store.prefix_for_key("first")).files == 1);
}

TEST_CASE("rate limiter slides over a one-minute window") {
    RateLimiter limiter(2, 100);

    CHECK(limiter.admit(10, 0));
    CHECK(limiter.admit(10, 1000));
    // Third operation inside the minute exceeds the count limit.
    CHECK_FALSE(limiter.admit(10, 2000));
    // Once the first two have aged out, capacity returns.
    CHECK(limiter.admit(10, 61001));

    RateLimiter byte_limiter(0, 50);
    CHECK(byte_limiter.admit(40, 0));
    CHECK_FALSE(byte_limiter.admit(20, 100));
    CHECK(byte_limiter.admit(20, 60101));

    // Zero means unlimited on both axes.
    RateLimiter open(0, 0);
    for (int i = 0; i < 100; ++i) {
        CHECK(open.admit(1'000'000, i));
    }
}

TEST_CASE("audit lines are machine-readable and cannot be forged by a peer") {
    FakeClock clock;
    AuditLog json_log({}, 0, 0, true, clock.fn());
    const std::string line =
        json_log.render_event("put_ok", {{"key", "abc"}, {"size", 12}});
    const nlohmann::json parsed = nlohmann::json::parse(line);
    CHECK(parsed.at("ts").get<std::int64_t>() == clock.seconds);
    CHECK(parsed.at("event").get<std::string>() == "put_ok");
    CHECK(parsed.at("key").get<std::string>() == "abc");
    CHECK(parsed.at("size").get<int>() == 12);

    AuditLog text_log({}, 0, 0, false, clock.fn());
    CHECK(text_log.render_event("get_miss", {{"key", "abc"}}) ==
          "ts=" + std::to_string(clock.seconds) + " event=get_miss key=abc");

    // A key carrying a newline or a quote must not be able to inject a line
    // that a log parser or a fail2ban jail would believe.
    const std::string hostile =
        text_log.render_event("get_miss", {{"key", "a\nFAIL2BAN reason=\"x\""}});
    CHECK(hostile.find('\n') == std::string::npos);
    CHECK(hostile.find('"') == std::string::npos);

    CHECK(AuditLog::render_fail2ban("BLINDBOX_AUTH_FAIL",
                                    {{"remote_host", "198.51.100.7"}}) ==
          "FAIL2BAN reason=BLINDBOX_AUTH_FAIL remote_host=198.51.100.7");
}

TEST_CASE("audit log writes to disk and rotates") {
    TempDir dir;
    const std::filesystem::path path = dir.file("audit.log");
    AuditLog log(path, 64, 2, false);
    log.set_stderr_echo(false);

    log.event("first", {{"key", "a"}});
    REQUIRE(std::filesystem::exists(path));
    CHECK(read_file(path).find("event=first") != std::string::npos);

    for (int i = 0; i < 8; ++i) {
        log.event("filler", {{"index", i}});
    }
    CHECK(std::filesystem::exists(std::filesystem::path(path.string() + ".1")));
    CHECK(std::filesystem::file_size(path) < 256);
}

TEST_CASE("the shipped fail2ban filter matches the lines the audit log emits") {
    // The jail is the only automated defence a public replica has, so drift
    // between the emitted line and the regex has to fail a test rather than go
    // unnoticed until somebody reads their ban list.
    const std::filesystem::path filter =
        std::filesystem::path(I2PCHAT_SOURCE_DIR) /
        "apps/blindbox-daemon/packaging/fail2ban-filter.conf";
    REQUIRE(std::filesystem::exists(filter));
    const std::string contents = read_file(filter);

    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "token";
    config.admin_token = "admin";
    config.rate_limit_puts_per_minute = 0;
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    const PeerInfo peer{"198.51.100.7", 40001};
    REQUIRE(service.plan("GET key wrong", peer).reply == "ERR\n");
    {
        const auto [status, type, body] =
            service.http_response("GET", "/metrics", "wrong", peer);
        REQUIRE(status == 401);
        (void)type;
        (void)body;
    }

    ReplicaServerConfig limited = config;
    limited.rate_limit_puts_per_minute = 1;
    ReplicaService limited_service(limited);
    limited_service.audit().set_stderr_echo(false);
    REQUIRE(put_body(limited_service, "one", "hello", "token") == "OK\n");
    REQUIRE(limited_service.plan("PUT two 5 token", peer).reply == "RATE\n");

    for (const std::string& reason :
         {"BLINDBOX_AUTH_FAIL", "BLINDBOX_HTTP_AUTH_FAIL", "BLINDBOX_RATE_LIMIT"}) {
        CHECK(contents.find("reason=" + reason + " .* remote_host=<HOST>") !=
              std::string::npos);
    }

    // What the emitted line has to look like for those regexes to bite: the
    // reason first, at least one field between it and the peer, and the host
    // followed by whitespace or the end of the line.
    const std::string line = AuditLog::render_fail2ban(
        "BLINDBOX_AUTH_FAIL", {{"command", "GET"},
                               {"key", "abc"},
                               {"remote_host", peer.host},
                               {"remote_port", peer.port}});
    CHECK(line.starts_with("FAIL2BAN reason=BLINDBOX_AUTH_FAIL "));
    const auto host_at = line.find(" remote_host=" + peer.host);
    REQUIRE(host_at != std::string::npos);
    CHECK(host_at > line.find("reason=") + 8);
    CHECK(line[host_at + std::string(" remote_host=").size() + peer.host.size()] == ' ');

    const std::string audit = read_file(dir.file("audit.log"));
    CHECK(audit.find("FAIL2BAN reason=BLINDBOX_AUTH_FAIL") != std::string::npos);
    CHECK(audit.find("FAIL2BAN reason=BLINDBOX_HTTP_AUTH_FAIL") != std::string::npos);
    CHECK(read_file(limited.base_dir / "audit.log")
              .find("FAIL2BAN reason=BLINDBOX_RATE_LIMIT") != std::string::npos);
}

TEST_CASE("service answers PING with its magic and keeps the connection") {
    TempDir dir;
    ReplicaService service(config_in(dir));
    service.audit().set_stderr_echo(false);

    const ReplicaService::Plan plan = service.plan("PING", PeerInfo{});
    CHECK(plan.reply == std::string(kDaemonMagic) + "\n");
    // PING and AUTH are the only commands that leave the connection open, so a
    // prober can follow up without reconnecting.
    CHECK_FALSE(plan.close_after);
}

TEST_CASE("service stores and returns a blob over the line protocol") {
    TempDir dir;
    ReplicaService service(config_in(dir));
    service.audit().set_stderr_echo(false);

    CHECK(put_body(service, "key1", "hello") == "OK\n");
    CHECK(service.metric("put_ok") == 1);

    const ReplicaService::Plan get = service.plan("GET key1", PeerInfo{});
    CHECK(get.reply == "OK 5\nhello");
    CHECK(get.close_after);

    CHECK(service.plan("GET nothing", PeerInfo{}).reply == "MISS\n");
    CHECK(service.metric("get_miss") == 1);

    // A second PUT on a taken slot is reported, never allowed to overwrite.
    CHECK(put_body(service, "key1", "other") == "EXISTS\n");
}

TEST_CASE("service rejects malformed and oversized commands") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.max_blob = 8;
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    CHECK(service.plan("", PeerInfo{}).reply == "ERR\n");
    CHECK(service.plan("NONSENSE key", PeerInfo{}).reply == "ERR\n");
    CHECK(service.plan("PUT key", PeerInfo{}).reply == "ERR\n");
    CHECK(service.plan("PUT key notanumber", PeerInfo{}).reply == "ERR\n");
    CHECK(service.plan("GET", PeerInfo{}).reply == "ERR\n");

    // A size beyond the limit is refused before any body is read.
    const ReplicaService::Plan too_big = service.plan("PUT key 9", PeerInfo{});
    CHECK(too_big.reply == "ERR\n");
    CHECK_FALSE(too_big.pending_put.has_value());
    CHECK(service.plan("PUT key 0", PeerInfo{}).reply == "ERR\n");
}

TEST_CASE("service treats a short body as a failed put") {
    TempDir dir;
    ReplicaService service(config_in(dir));
    service.audit().set_stderr_echo(false);

    ReplicaService::Plan plan = service.plan("PUT key 10", PeerInfo{});
    REQUIRE(plan.pending_put.has_value());
    // A client that vanishes mid-blob must not leave a truncated blob behind
    // that a recipient would fail to open.
    CHECK(service.complete_put(*plan.pending_put, as_bytes("short"), PeerInfo{}) ==
          "ERR\n");
    CHECK(service.plan("GET key", PeerInfo{}).reply == "MISS\n");
}

TEST_CASE("service enforces the auth token on put and get") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "s3cret";
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    CHECK(put_body(service, "key", "hello") == "ERR\n");
    CHECK(put_body(service, "key", "hello", "wrong") == "ERR\n");
    CHECK(service.plan("GET key", PeerInfo{}).reply == "ERR\n");
    CHECK(service.metric("auth_fail") == 3);

    CHECK(put_body(service, "key", "hello", "s3cret") == "OK\n");
    CHECK(service.plan("GET key s3cret", PeerInfo{}).reply == "OK 5\nhello");

    CHECK(service.plan("AUTH s3cret", PeerInfo{}).reply == "OK\n");
    CHECK(service.plan("AUTH nope", PeerInfo{}).reply == "ERR\n");
}

TEST_CASE("an open replica accepts any token") {
    TempDir dir;
    ReplicaService service(config_in(dir));
    service.audit().set_stderr_echo(false);

    // No token configured means no token required: the blobs are sealed anyway,
    // and this is the configuration a friend-run box uses.
    CHECK(put_body(service, "key", "hello", "irrelevant") == "OK\n");
    CHECK(service.plan("AUTH anything", PeerInfo{}).reply == "OK\n");
}

TEST_CASE("service rate-limits puts and records the refusal") {
    TempDir dir;
    FakeClock clock;
    ReplicaServerConfig config = config_in(dir);
    config.rate_limit_puts_per_minute = 1;
    ReplicaService service(config, clock.fn());
    service.audit().set_stderr_echo(false);

    CHECK(put_body(service, "one", "hello") == "OK\n");
    CHECK(service.plan("PUT two 5", PeerInfo{}).reply == "RATE\n");
    CHECK(service.metric("rate_limit") == 1);

    // A minute later the window has slid and the replica accepts again.
    clock.seconds += 61;
    CHECK(put_body(service, "two", "hello") == "OK\n");
}

TEST_CASE("service reports a prefix quota as full without reading a body") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.max_prefix_files = 1;
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    REQUIRE(put_body(service, "first", "hello") == "OK\n");
    const std::string prefix = service.store().prefix_for_key("first");

    // Find a second key in the same prefix, which is what a flooder would do
    // deliberately.
    std::string collide;
    for (int i = 0; i < 4096; ++i) {
        const std::string candidate = "key" + std::to_string(i);
        if (service.store().prefix_for_key(candidate) == prefix && candidate != "first") {
            collide = candidate;
            break;
        }
    }
    REQUIRE_FALSE(collide.empty());

    const ReplicaService::Plan plan = service.plan("PUT " + collide + " 5", PeerInfo{});
    CHECK(plan.reply == "FULL\n");
    CHECK_FALSE(plan.pending_put.has_value());
    CHECK(service.metric("prefix_quota") == 1);
}

TEST_CASE("status and metrics require the admin token") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "user";
    config.admin_token = "admin";
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    // The ordinary token does not open the admin surface: storage totals leak
    // how much traffic a box carries.
    CHECK(service.plan("STATUS user", PeerInfo{}).reply == "ERR\n");
    CHECK(service.plan("STATUS", PeerInfo{}).reply == "ERR\n");

    const std::string status = service.plan("STATUS admin", PeerInfo{}).reply;
    CHECK(status.starts_with("OK files=0 bytes=0 auth=1 admin_auth=1"));

    const nlohmann::json parsed =
        nlohmann::json::parse(service.plan("STATUS_JSON admin", PeerInfo{}).reply);
    CHECK(parsed.at("files").get<int>() == 0);
    CHECK(parsed.at("auth").get<int>() == 1);
    CHECK(parsed.at("max_files").get<int>() == static_cast<int>(config.max_files));

    const std::string metrics = service.plan("METRICS admin", PeerInfo{}).reply;
    CHECK(metrics.find("blindbox_files 0") != std::string::npos);
    CHECK(metrics.find("# TYPE blindbox_bytes gauge") != std::string::npos);
}

TEST_CASE("without an admin token the ordinary one governs admin commands") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "user";
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    CHECK(service.plan("STATUS user", PeerInfo{}).reply.starts_with("OK files=0"));
    CHECK(service.plan("STATUS wrong", PeerInfo{}).reply == "ERR\n");
}

TEST_CASE("status reflects stored blobs and event counters") {
    TempDir dir;
    ReplicaService service(config_in(dir));
    service.audit().set_stderr_echo(false);

    REQUIRE(put_body(service, "key", "hello") == "OK\n");
    const nlohmann::json parsed =
        nlohmann::json::parse(service.plan("STATUS_JSON", PeerInfo{}).reply);
    CHECK(parsed.at("files").get<int>() == 1);
    CHECK(parsed.at("bytes").get<int>() == 5);

    CHECK(service.prometheus_metrics().find(
              "blindbox_events_total{event=\"put_ok\"} 1") != std::string::npos);
}

TEST_CASE("http status endpoints answer only authorised GETs") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.admin_token = "admin";
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    {
        const auto [status, type, body] =
            service.http_response("POST", "/healthz", "admin", PeerInfo{});
        CHECK(status == 405);
        (void)type;
        (void)body;
    }
    {
        const auto [status, type, body] =
            service.http_response("GET", "/healthz", "wrong", PeerInfo{});
        CHECK(status == 401);
        (void)type;
        (void)body;
    }
    {
        const auto [status, type, body] =
            service.http_response("GET", "/healthz", "admin", PeerInfo{});
        CHECK(status == 200);
        CHECK(body == "ok\n");
        (void)type;
    }
    {
        const auto [status, type, body] =
            service.http_response("GET", "/status.json", "admin", PeerInfo{});
        CHECK(status == 200);
        CHECK(type.starts_with("application/json"));
        CHECK(nlohmann::json::parse(body).at("files").get<int>() == 0);
    }
    {
        const auto [status, type, body] =
            service.http_response("GET", "/metrics", "admin", PeerInfo{});
        CHECK(status == 200);
        CHECK(type.find("version=0.0.4") != std::string::npos);
        CHECK(body.find("blindbox_files") != std::string::npos);
    }
    {
        const auto [status, type, body] =
            service.http_response("GET", "/secrets", "admin", PeerInfo{});
        CHECK(status == 404);
        (void)type;
        (void)body;
    }
}

TEST_CASE("metrics exports are written to their configured paths") {
    TempDir dir;
    ReplicaServerConfig config = config_in(dir);
    config.metrics_json_path = dir.file("metrics.json");
    config.metrics_prom_path = dir.file("metrics.prom");
    ReplicaService service(config);
    service.audit().set_stderr_echo(false);

    REQUIRE(put_body(service, "key", "hello") == "OK\n");

    const nlohmann::json exported = nlohmann::json::parse(read_file(config.metrics_json_path));
    CHECK(exported.at("status").at("files").get<int>() == 1);
    CHECK(exported.at("events").at("put_ok").get<int>() == 1);
    CHECK(read_file(config.metrics_prom_path).find("blindbox_files 1") !=
          std::string::npos);
}

TEST_CASE("garbage collection drops expired blobs") {
    TempDir dir;
    FakeClock clock;
    ReplicaServerConfig config = config_in(dir);
    config.ttl = std::chrono::seconds(10);
    ReplicaService service(config, clock.fn());
    service.audit().set_stderr_echo(false);

    REQUIRE(put_body(service, "key", "hello") == "OK\n");
    clock.seconds += 11;
    service.collect_garbage();
    CHECK(service.store().stats().files == 0);
}

TEST_CASE("the real client and server interoperate over TCP") {
    TempDir dir;
    boost::asio::io_context context;

    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "token";
    auto service = std::make_shared<ReplicaService>(config);
    service->audit().set_stderr_echo(false);

    ReplicaServer server(context.get_executor(), service);
    server.start();
    const std::uint16_t port = server.port();
    REQUIRE(port != 0);

    const std::string address = "127.0.0.1:" + std::to_string(port);
    ReplicaClientConfig client_config;
    client_config.endpoints = {address};
    client_config.local_auth_token = "token";
    ReplicaClient client(context.get_executor(), client_config,
                         direct_stream_factory(context.get_executor()));

    const Bytes blob = blob_of("sealed-blob-contents");
    const std::vector<PutResult> results =
        run_step(context, client.put("lookup-token", blob));
    REQUIRE(results.size() == 1);
    CHECK(results[0].status == PutStatus::Ok);

    const std::vector<Bytes> fetched =
        run_step(context, client.get("lookup-token"));
    REQUIRE(fetched.size() == 1);
    CHECK(fetched[0] == blob);

    // Nothing stored under an unused token, and that is not an error.
    CHECK(run_step(context, client.get("other-token")).empty());

    // Re-sending the same blob is a taken slot holding the sender's own bytes,
    // which still counts as delivered.
    const std::vector<PutResult> again = run_step(context, client.put("lookup-token", blob));
    REQUIRE(again.size() == 1);
    CHECK(again[0].status == PutStatus::Exists);

    // A slot holding somebody else's blob is not a delivery, and the sender is
    // told so rather than left believing the message went out.
    CHECK_THROWS_AS(run_step(context, client.put("lookup-token", blob_of("different"))),
                    ReplicaError);

    server.stop();
}

TEST_CASE("probing distinguishes a replica from an unrelated listener") {
    TempDir dir;
    boost::asio::io_context context;

    ReplicaServerConfig config = config_in(dir);
    config.auth_token = "token";
    auto service = std::make_shared<ReplicaService>(config);
    service->audit().set_stderr_echo(false);

    ReplicaServer server(context.get_executor(), service);
    server.start();
    const std::uint16_t port = server.port();

    CHECK(run_step(context,
                        probe_replica(context.get_executor(), "127.0.0.1", port, "token")));
    // A wrong token means "not a replica I may share", which is what stops a
    // second daemon from adopting somebody else's box.
    CHECK_FALSE(run_step(
        context, probe_replica(context.get_executor(), "127.0.0.1", port, "wrong")));

    server.stop();

    // Nothing listening at all.
    CHECK_FALSE(run_step(
        context, probe_replica(context.get_executor(), "127.0.0.1", port, "token",
                               std::chrono::milliseconds(300))));
}

TEST_CASE("the server serves the http status listener") {
    TempDir dir;
    boost::asio::io_context context;

    ReplicaServerConfig config = config_in(dir);
    config.http_status = true;
    config.admin_token = "admin";
    auto service = std::make_shared<ReplicaService>(config);
    service->audit().set_stderr_echo(false);

    ReplicaServer server(context.get_executor(), service);
    server.start();
    const std::uint16_t status_port = server.status_port();
    REQUIRE(status_port != 0);

    const auto request = [&](const std::string& text) {
        return run_step(
            context,
            [&]() -> boost::asio::awaitable<std::string> {
                boost::asio::ip::tcp::socket socket(context);
                co_await socket.async_connect(
                    boost::asio::ip::tcp::endpoint(
                        boost::asio::ip::make_address("127.0.0.1"), status_port),
                    boost::asio::use_awaitable);
                co_await boost::asio::async_write(socket, boost::asio::buffer(text),
                                                  boost::asio::use_awaitable);
                std::string response;
                boost::system::error_code error;
                char buffer[1024];
                while (!error) {
                    const std::size_t read = co_await socket.async_read_some(
                        boost::asio::buffer(buffer),
                        boost::asio::redirect_error(boost::asio::use_awaitable, error));
                    response.append(buffer, read);
                }
                co_return response;
            }());
    };

    const std::string ok = request(
        "GET /healthz HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer admin\r\n\r\n");
    CHECK(ok.starts_with("HTTP/1.1 200 OK"));
    CHECK(ok.ends_with("ok\n"));

    const std::string denied =
        request("GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n");
    CHECK(denied.starts_with("HTTP/1.1 401 Unauthorized"));

    server.stop();
}
