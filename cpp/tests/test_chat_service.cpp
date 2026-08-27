#include <catch2/catch_test_macros.hpp>

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>
#include <algorithm>
#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "fake_replica.hpp"
#include "fake_sam_router.hpp"
#include "i2pchat/protocol/text_chunking.hpp"
#include "i2pchat/runtime/chat_service.hpp"
#include "i2pchat/storage/keyring.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using i2pchat::testing::FakeReplica;
using i2pchat::testing::FakeSamRouter;
using i2pchat::testing::TempDir;
namespace asio = boost::asio;

namespace {

/// Runs the context until `predicate` holds or the budget runs out.
///
/// The two services and the router keep timers and accept loops outstanding
/// forever, so `run()` would never return; polling is what lets a test observe
/// intermediate states.
bool run_until(asio::io_context& context, const std::function<bool()>& predicate,
               std::chrono::milliseconds budget = std::chrono::seconds(10)) {
    const auto deadline = std::chrono::steady_clock::now() + budget;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        if (context.poll() == 0) {
            // Nothing runnable right now: the pending work is waiting on the
            // router's thread, so yield rather than spin.
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (context.stopped()) {
            context.restart();
        }
    }
    return predicate();
}

/// Keeps the tests out of the developer's own credential store: a profile
/// created here must not leave a Keychain entry behind, and on a machine that
/// prompts for access the run would block.
struct NoKeyring {
    NoKeyring() { storage::keyring::set_enabled(false); }
    ~NoKeyring() { storage::keyring::set_enabled(true); }
};

/// One service plus the events it reported.
struct Client {
    NoKeyring no_keyring;
    TempDir root;
    std::vector<std::string> systems;
    std::vector<std::string> errors;
    std::vector<std::pair<std::string, storage::HistoryEntry>> history;
    std::vector<runtime::DeliveryReport> deliveries;
    std::string local_addr;
    bool started = false;
    std::unique_ptr<runtime::ChatService> service;

    Client(asio::io_context& context, std::uint16_t sam_port, std::string profile,
           std::vector<std::string> replicas = {}) {
        runtime::ChatServiceConfig config;
        config.app_root = root.path();
        config.profile = std::move(profile);
        config.sam.port = sam_port;
        config.blindbox_enabled = !replicas.empty();
        config.blindbox_over_sam = false;
        config.blindbox_poll_interval = std::chrono::seconds(3600);
        if (!replicas.empty()) {
            storage::ReplicaSettings settings;
            settings.endpoints = std::move(replicas);
            config.replicas = settings;
        }

        runtime::ChatEvents events;
        events.on_system = [this](const std::string& message) {
            systems.push_back(message);
        };
        events.on_error = [this](const std::string& message) { errors.push_back(message); };
        events.on_history = [this](const std::string& peer,
                                   const storage::HistoryEntry& entry) {
            history.emplace_back(peer, entry);
        };
        events.on_delivery = [this](const runtime::DeliveryReport& report) {
            deliveries.push_back(report);
        };
        events.on_local_address = [this](const std::string& addr) {
            local_addr = addr;
            started = true;
        };
        service = std::make_unique<runtime::ChatService>(context.get_executor(),
                                                        std::move(config),
                                                        std::move(events));
    }

    [[nodiscard]] std::vector<std::string> received_from(const std::string& peer) const {
        std::vector<std::string> out;
        for (const auto& [addr, entry] : history) {
            if (addr == peer && entry.kind == "in") {
                out.push_back(entry.text);
            }
        }
        return out;
    }

    /// Every reported error on one line. A failing assertion about a message
    /// that did not arrive is far easier to read next to the reason.
    [[nodiscard]] std::string joined_errors() const {
        std::string out;
        for (const std::string& error : errors) {
            if (!out.empty()) {
                out += " | ";
            }
            out += error;
        }
        return out;
    }

    [[nodiscard]] const runtime::DeliveryReport* delivery(std::uint64_t msg_id) const {
        const runtime::DeliveryReport* latest = nullptr;
        for (const auto& report : deliveries) {
            if (report.msg_id == msg_id) {
                latest = &report;
            }
        }
        return latest;
    }
};

/// Starts both services and waits until each has a local address.
void start_both(asio::io_context& context, Client& first, Client& second) {
    asio::co_spawn(context, first.service->start(), asio::detached);
    asio::co_spawn(context, second.service->start(), asio::detached);
    REQUIRE(run_until(context, [&] { return first.started && second.started; }));
    REQUIRE(first.errors.empty());
    REQUIRE(second.errors.empty());
}

}  // namespace

TEST_CASE("two clients exchange text through the router") {
    FakeSamRouter router;
    asio::io_context context;

    Client alice(context, router.port(), "alice");
    Client bob(context, router.port(), "bob");
    start_both(context, alice, bob);
    CHECK(alice.local_addr.size() == 52);
    CHECK(alice.local_addr != bob.local_addr);

    bool connected = false;
    bool result = false;
    asio::co_spawn(context, alice.service->connect_peer(bob.local_addr),
                   [&](std::exception_ptr error, bool ok) {
                       REQUIRE_FALSE(error);
                       result = ok;
                       connected = true;
                   });
    REQUIRE(run_until(context, [&] { return connected; }));
    CHECK(result);
    CHECK(alice.service->live(bob.local_addr));
    REQUIRE(run_until(context, [&] { return bob.service->live(alice.local_addr); }));

    SECTION("a message arrives, is acknowledged and is remembered") {
        std::vector<std::uint64_t> ids;
        bool sent = false;
        asio::co_spawn(context, alice.service->send_text(bob.local_addr, "привет"),
                       [&](std::exception_ptr error, std::vector<std::uint64_t> result) {
                           REQUIRE_FALSE(error);
                           ids = std::move(result);
                           sent = true;
                       });
        REQUIRE(run_until(context, [&] { return sent; }));
        REQUIRE(ids.size() == 1);

        REQUIRE(run_until(
            context, [&] { return !bob.received_from(alice.local_addr).empty(); }));
        CHECK(bob.received_from(alice.local_addr) == std::vector<std::string>{"привет"});

        // The peer's MSG_ACK turns the outgoing entry from sent to delivered.
        REQUIRE(run_until(context, [&] {
            const runtime::DeliveryReport* report = alice.delivery(ids.front());
            return report != nullptr && report->state == runtime::DeliveryState::Delivered;
        }));

        const std::vector<storage::HistoryEntry> stored =
            alice.service->history(bob.local_addr);
        REQUIRE(stored.size() == 1);
        CHECK(stored.front().kind == "out");
        CHECK(stored.front().text == "привет");
        CHECK(stored.front().delivery_state == "delivered");
        CHECK(stored.front().delivery_route == "live");

        // Both sides now know each other as contacts.
        CHECK(alice.service->contacts().has_peer(bob.local_addr));
        CHECK(bob.service->contacts().has_peer(alice.local_addr));
    }

    SECTION("a long message is split into protocol-sized parts") {
        const std::string long_text(protocol::kMaxChatMessageChars + 10, 'a');
        std::vector<std::uint64_t> ids;
        bool sent = false;
        asio::co_spawn(context, alice.service->send_text(bob.local_addr, long_text),
                       [&](std::exception_ptr, std::vector<std::uint64_t> result) {
                           ids = std::move(result);
                           sent = true;
                       });
        REQUIRE(run_until(context, [&] { return sent; }));
        CHECK(ids.size() == 2);
        REQUIRE(run_until(context, [&] {
            return bob.received_from(alice.local_addr).size() == 2;
        }));
    }

    SECTION("a disconnect is reported to both ends") {
        alice.service->disconnect_peer(bob.local_addr);
        REQUIRE(run_until(context, [&] { return !bob.service->live(alice.local_addr); }));
        CHECK_FALSE(alice.service->live(bob.local_addr));
    }

    bool stopped = false;
    asio::co_spawn(context, alice.service->stop(), [&](std::exception_ptr) { stopped = true; });
    run_until(context, [&] { return stopped; });
}

TEST_CASE("a peer that is not listening cannot be dialled") {
    FakeSamRouter router;
    asio::io_context context;

    Client alice(context, router.port(), "alice");
    Client bob(context, router.port(), "bob");
    start_both(context, alice, bob);

    const std::string unknown = std::string(52, 'a');
    bool finished = false;
    bool result = true;
    asio::co_spawn(context, alice.service->connect_peer(unknown),
                   [&](std::exception_ptr, bool ok) {
                       result = ok;
                       finished = true;
                   });
    REQUIRE(run_until(context, [&] { return finished; }));
    CHECK_FALSE(result);
    CHECK_FALSE(alice.errors.empty());
}

TEST_CASE("an offline message is queued on a replica and collected later") {
    FakeReplica replica;
    FakeSamRouter router;
    asio::io_context context;

    Client alice(context, router.port(), "alice", {replica.address()});
    Client bob(context, router.port(), "bob", {replica.address()});
    start_both(context, alice, bob);

    // The offline channel needs a root, and a root is agreed over the live
    // channel — which is the whole point of connecting once while both are up.
    bool connected = false;
    asio::co_spawn(context, alice.service->connect_peer(bob.local_addr),
                   [&](std::exception_ptr, bool ok) { connected = ok; });
    REQUIRE(run_until(context, [&] { return connected; }));
    REQUIRE(run_until(context, [&] { return bob.service->live(alice.local_addr); }));

    // Wait for the root exchange to settle before taking the link down.
    //
    // Both sides have to report it, not just the receiver: the side that offers
    // the root only adopts it once the peer acknowledges the epoch, and which of
    // the two offers depends on the addresses, which are freshly generated every
    // run. Waiting on one side alone is a coin flip.
    const auto has_offline_channel = [](const Client& client) {
        return std::any_of(client.systems.begin(), client.systems.end(),
                           [](const std::string& message) {
                               return message.find("Offline delivery enabled") !=
                                      std::string::npos;
                           });
    };
    REQUIRE(run_until(context, [&] {
        return has_offline_channel(alice) && has_offline_channel(bob);
    }));

    alice.service->disconnect_peer(bob.local_addr);
    REQUIRE(run_until(context, [&] { return !alice.service->live(bob.local_addr); }));

    std::vector<std::uint64_t> ids;
    bool sent = false;
    asio::co_spawn(context, alice.service->send_text(bob.local_addr, "offline hello"),
                   [&](std::exception_ptr error, std::vector<std::uint64_t> result) {
                       REQUIRE_FALSE(error);
                       ids = std::move(result);
                       sent = true;
                   });
    REQUIRE(run_until(context, [&] { return sent; }));
    INFO("alice errors: " << alice.joined_errors());
    REQUIRE(ids.size() == 1);
    const runtime::DeliveryReport* report = alice.delivery(ids.front());
    REQUIRE(report != nullptr);
    CHECK(report->state == runtime::DeliveryState::Queued);
    CHECK(report->route == "blindbox");

    std::size_t collected = 0;
    bool polled = false;
    asio::co_spawn(context, bob.service->poll_blindbox(),
                   [&](std::exception_ptr error, std::size_t count) {
                       REQUIRE_FALSE(error);
                       collected = count;
                       polled = true;
                   });
    REQUIRE(run_until(context, [&] { return polled; }));
    CHECK(collected == 1);
    CHECK(bob.received_from(alice.local_addr) ==
          std::vector<std::string>{"offline hello"});

    SECTION("the same message is not collected twice") {
        std::size_t again = 1;
        bool second = false;
        asio::co_spawn(context, bob.service->poll_blindbox(),
                       [&](std::exception_ptr, std::size_t count) {
                           again = count;
                           second = true;
                       });
        REQUIRE(run_until(context, [&] { return second; }));
        CHECK(again == 0);
    }
}

TEST_CASE("an offline message with no replicas configured fails loudly") {
    FakeSamRouter router;
    asio::io_context context;

    Client alice(context, router.port(), "alice");
    Client bob(context, router.port(), "bob");
    start_both(context, alice, bob);

    std::vector<std::uint64_t> ids{1};
    bool sent = false;
    asio::co_spawn(context, alice.service->send_text(bob.local_addr, "into the void"),
                   [&](std::exception_ptr, std::vector<std::uint64_t> result) {
                       ids = std::move(result);
                       sent = true;
                   });
    REQUIRE(run_until(context, [&] { return sent; }));
    CHECK(ids.empty());
    CHECK_FALSE(alice.errors.empty());
}

TEST_CASE("a profile written by one run is reopened by the next") {
    const NoKeyring no_keyring;
    FakeSamRouter router;
    asio::io_context context;

    TempDir root;
    std::string first_addr;
    {
        runtime::ChatServiceConfig config;
        config.app_root = root.path();
        config.profile = "persistent";
        config.sam.port = router.port();
        config.blindbox_enabled = false;

        runtime::ChatEvents events;
        bool ready = false;
        events.on_local_address = [&](const std::string& addr) {
            first_addr = addr;
            ready = true;
        };
        runtime::ChatService service(context.get_executor(), config, events);
        asio::co_spawn(context, service.start(), asio::detached);
        REQUIRE(run_until(context, [&] { return ready; }));

        bool stopped = false;
        asio::co_spawn(context, service.stop(), [&](std::exception_ptr) { stopped = true; });
        REQUIRE(run_until(context, [&] { return stopped; }));
    }

    runtime::ChatServiceConfig config;
    config.app_root = root.path();
    config.profile = "persistent";
    config.sam.port = router.port();
    config.blindbox_enabled = false;

    std::string second_addr;
    bool ready = false;
    runtime::ChatEvents events;
    events.on_local_address = [&](const std::string& addr) {
        second_addr = addr;
        ready = true;
    };
    runtime::ChatService service(context.get_executor(), config, events);
    asio::co_spawn(context, service.start(), asio::detached);
    REQUIRE(run_until(context, [&] { return ready; }));

    // The destination is loaded from `{profile}.dat` rather than regenerated,
    // so the address a user has published keeps working.
    CHECK(second_addr == first_addr);
    CHECK(service.identity().signing_seed.size() == 32);
}
