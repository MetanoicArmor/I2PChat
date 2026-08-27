#include "i2pchat/runtime/chat_service.hpp"

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <algorithm>
#include <chrono>
#include <utility>

#include "i2pchat/blindbox/state.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/protocol/codec.hpp"
#include "i2pchat/protocol/signals.hpp"
#include "i2pchat/protocol/text_chunking.hpp"
#include "i2pchat/sam/destination.hpp"

namespace i2pchat::runtime {
namespace {

/// A destination in I2P-base64 is 516 characters or more; a base32 host is 52.
/// Nothing in between is a valid peer identifier, so the length decides.
constexpr std::size_t kMinDestinationLength = 300;

std::int64_t unix_now() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::string preview_of(const std::string& text) {
    // Measured in code points, like the contact book's own limit.
    std::string out;
    std::size_t points = 0;
    for (std::size_t index = 0; index < text.size() && points < storage::kPreviewMaxLength;) {
        const unsigned char lead = static_cast<unsigned char>(text[index]);
        std::size_t width = 1;
        if ((lead & 0xE0) == 0xC0) {
            width = 2;
        } else if ((lead & 0xF0) == 0xE0) {
            width = 3;
        } else if ((lead & 0xF8) == 0xF0) {
            width = 4;
        }
        width = std::min(width, text.size() - index);
        out.append(text, index, width);
        index += width;
        ++points;
    }
    return out;
}

std::string session_nickname(const std::string& profile) {
    const Bytes suffix = crypto::random_bytes(4);
    return "chat_" + profile + "_" + std::to_string(unix_now()) + "_" +
           encoding::hex_encode(ByteView(suffix));
}

}  // namespace

/// Everything the service keeps for one peer while the process runs.
struct ChatService::Peer {
    std::string addr;
    std::shared_ptr<PeerLink> link;
    std::unique_ptr<transfer::IncomingTransfers> incoming;
    /// Cancelled to wake a `connect_peer` that is waiting on the handshake.
    std::shared_ptr<asio::steady_timer> connect_waiter;
    bool connect_succeeded = false;
};

std::string_view delivery_state_name(DeliveryState state) {
    switch (state) {
        case DeliveryState::Sent:
            return "sent";
        case DeliveryState::Delivered:
            return "delivered";
        case DeliveryState::Queued:
            return "queued";
        case DeliveryState::Failed:
            return "failed";
    }
    return "sent";
}

ChatService::ChatService(asio::any_io_executor executor, ChatServiceConfig config,
                         ChatEvents events)
    : executor_(std::move(executor)),
      config_(std::move(config)),
      events_(std::move(events)),
      paths_(config_.app_root / "profiles" / config_.profile, config_.profile),
      sessions_(config_.sessions) {
    crypto::init();
    if (config_.downloads_dir.empty()) {
        config_.downloads_dir = paths_.data_dir() / "downloads";
    }
    if (config_.images_dir.empty()) {
        config_.images_dir = paths_.data_dir() / "images";
    }

    session::SessionManager::Callbacks callbacks;
    callbacks.on_transport_state = [this](session::TransportState, session::TransportState to,
                                         const std::string& reason) {
        if (events_.on_transport_state) {
            events_.on_transport_state(to, reason);
        }
    };
    callbacks.on_peer_state = [this](const std::string& peer_id, session::PeerState,
                                     session::PeerState to, const std::string& reason) {
        if (events_.on_peer_state) {
            events_.on_peer_state(peer_id, to, reason);
        }
    };
    sessions_ = session::SessionManager(config_.sessions, std::move(callbacks));
}

ChatService::~ChatService() = default;

asio::awaitable<void> ChatService::start() {
    if (running_) {
        co_return;
    }
    std::filesystem::create_directories(paths_.data_dir());
    std::filesystem::create_directories(config_.downloads_dir);
    std::filesystem::create_directories(config_.images_dir);

    if (config_.profile != kTransientProfile) {
        trust_ = session::TrustStore(paths_.trust_store());
        trust_.load();
    }
    if (events_.on_trust_prompt) {
        trust_.set_prompt_handler(events_.on_trust_prompt);
    }

    sessions_.set_transport_state(session::TransportState::Starting, "start");
    sam_ = std::make_shared<sam::SamSession>(executor_, config_.sam);
    identity_ = co_await load_identity(*sam_, paths_);

    contacts_ = storage::load_contact_book(paths_.contacts(), ByteView(identity_.identity_key));

    sam::SessionOptions options;
    options.session_id = session_nickname(config_.profile);
    options.destination = identity_.destination_base64;
    options.options = config_.sam_options;
    co_await sam_->open(options);

    sessions_.set_transport_state(session::TransportState::WarmingTunnels,
                                  "sam-session-created");
    running_ = true;
    stopping_ = false;

    if (config_.blindbox_enabled) {
        if (!config_.replicas.has_value()) {
            config_.replicas = storage::load_replica_settings(
                paths_.blindbox_replicas(), ByteView(identity_.identity_key));
        }
        replica_settings_ = *config_.replicas;
        if (replica_settings_.auth_locked) {
            emit_error(
                "BlindBox replica tokens could not be decrypted; the offline path "
                "will be attempted without them.");
        }
    }

    if (events_.on_local_address) {
        events_.on_local_address(identity_.local_addr);
    }
    emit_system("I2P session ready at " + identity_.local_addr);

    asio::co_spawn(executor_, [this] { return accept_loop(); }, asio::detached);
    if (config_.blindbox_enabled) {
        asio::co_spawn(executor_, [this] { return blindbox_loop(); }, asio::detached);
    }
}

asio::awaitable<void> ChatService::stop() {
    if (!running_) {
        co_return;
    }
    stopping_ = true;
    running_ = false;
    sessions_.set_transport_state(session::TransportState::ShuttingDown, "stop");

    for (auto& [addr, peer] : peers_) {
        if (peer->link && !peer->link->closed()) {
            peer->link->close_gracefully();
        }
    }
    peers_.clear();

    for (const auto& [addr, snapshot] : blindbox_snapshots_) {
        save_blindbox_snapshot(addr);
    }
    save_contacts();
    if (trust_.persistent()) {
        trust_.save();
    }
    if (sam_) {
        sam_->close();
        sam_.reset();
    }
    replicas_.reset();
    sessions_.reset();
    sessions_.set_transport_state(session::TransportState::Stopped, "stopped");
    co_return;
}

ChatService::Peer& ChatService::ensure_peer(const std::string& peer_addr) {
    auto found = peers_.find(peer_addr);
    if (found == peers_.end()) {
        auto peer = std::make_unique<Peer>();
        peer->addr = peer_addr;
        found = peers_.emplace(peer_addr, std::move(peer)).first;
    }
    return *found->second;
}

ChatService::Peer* ChatService::find_peer(std::string_view peer_addr) {
    const auto found = peers_.find(std::string(peer_addr));
    return found == peers_.end() ? nullptr : found->second.get();
}

PeerLinkConfig ChatService::link_config(const std::string& peer_addr,
                                        session::ConnectionDirection direction) {
    PeerLinkConfig config;
    config.session.local_dest_base64 = identity_.public_destination_base64;
    config.session.direction = direction;
    config.session.padding = config_.padding;
    config.session.handshake.local_addr = identity_.local_addr;
    config.session.handshake.peer_addr = peer_addr;
    config.session.handshake.signing_seed = identity_.signing_seed;
    config.session.handshake.signing_public = identity_.signing_public;
    config.session.handshake.trust_verifier = [this](const std::string& addr,
                                                     ByteView signing_key) {
        return trust_.verify_or_pin(addr, signing_key);
    };
    return config;
}

void ChatService::wire_transfers(Peer& peer) {
    transfer::IncomingTransfers::Config config;
    config.downloads_dir = config_.downloads_dir;
    config.images_dir = config_.images_dir;

    const std::string addr = peer.addr;
    transfer::IncomingTransfers::Callbacks callbacks;
    callbacks.send = [this, addr](const transfer::Frame& frame) {
        if (Peer* target = find_peer(addr); target != nullptr && target->link) {
            target->link->send_text(frame.type, frame.body, next_msg_id());
        }
    };
    callbacks.accept_file = [this, addr](const std::string& name, std::uint64_t size) {
        return events_.accept_file ? events_.accept_file(addr, name, size) : true;
    };
    callbacks.on_progress = [this, addr](const transfer::Progress& progress) {
        if (events_.on_transfer) {
            events_.on_transfer(addr, progress);
        }
    };
    callbacks.on_system = [this](const std::string& message) { emit_system(message); };
    callbacks.on_error = [this](const std::string& message) { emit_error(message); };
    callbacks.on_file_received = [this, addr](const std::filesystem::path& path) {
        storage::HistoryEntry entry;
        entry.kind = "in";
        entry.text = "[file] " + path.filename().string();
        entry.ts = storage::now_iso8601_utc();
        append_history(addr, entry);
        if (events_.on_file_received) {
            events_.on_file_received(addr, path);
        }
    };
    callbacks.on_image_received = [this, addr](const std::filesystem::path& path) {
        storage::HistoryEntry entry;
        entry.kind = "in";
        entry.text = "[image] " + path.filename().string();
        entry.ts = storage::now_iso8601_utc();
        append_history(addr, entry);
        if (events_.on_image_received) {
            events_.on_image_received(addr, path);
        }
    };
    callbacks.on_image_lines_received = [this, addr](const std::string& text) {
        if (events_.on_image_text) {
            events_.on_image_text(addr, text);
        }
    };
    peer.incoming =
        std::make_unique<transfer::IncomingTransfers>(std::move(config), std::move(callbacks));
}

void ChatService::attach_link(Peer& peer, std::shared_ptr<PeerLink> link) {
    // Two sides dialling at once leaves two streams; the newer one wins, which
    // matches the reference implementation and avoids a split channel.
    if (peer.link && !peer.link->closed()) {
        peer.link->close("replaced by a newer stream");
    }
    peer.link = std::move(link);
    wire_transfers(peer);
}

asio::awaitable<void> ChatService::accept_loop() {
    while (running_) {
        sam::SamStream stream{asio::ip::tcp::socket(executor_), {}, {}};
        bool accepted = false;
        try {
            stream = co_await sam_->accept_stream();
            accepted = true;
        } catch (const std::exception& error) {
            if (running_) {
                emit_error(std::string("accepting a peer failed: ") + error.what());
            }
        }
        if (!accepted) {
            if (!running_) {
                co_return;
            }
            // A failed ACCEPT usually means the session is gone; without a
            // pause this would spin.
            asio::steady_timer pause(executor_);
            pause.expires_after(std::chrono::seconds(2));
            boost::system::error_code ignored;
            co_await pause.async_wait(asio::redirect_error(asio::use_awaitable, ignored));
            continue;
        }

        // An inbound session must learn who is calling from the identity
        // preface rather than from the router: the address SAM reports is not
        // authenticated, and the session layer refuses to be told it in
        // advance. So the link is created anonymously and registered against a
        // peer once the handshake names one.
        PeerLinkConfig config = link_config({}, session::ConnectionDirection::Inbound);
        PeerLink::Callbacks callbacks;
        callbacks.on_established = [this](PeerLink& link) { on_established(link); };
        callbacks.on_frame = [this](PeerLink& link, const PeerFrame& frame) {
            on_frame(link.peer_addr(), frame);
        };
        callbacks.on_closed = [this](PeerLink& link, const std::string& reason) {
            on_link_closed(link.peer_addr(), reason);
        };

        PeerLink::create(std::move(stream.socket), std::move(stream.prebuffered), config,
                         std::move(callbacks))
            ->start();
    }
}

asio::awaitable<std::string> ChatService::resolve_destination(const std::string& peer) {
    const std::string trimmed = std::string(sam::normalize_peer_address(peer));
    if (peer.size() >= kMinDestinationLength) {
        co_return peer;
    }
    if (trimmed.empty()) {
        throw std::invalid_argument("not an I2P address: " + peer);
    }
    co_return co_await sam_->naming_lookup(trimmed + ".b32.i2p");
}

asio::awaitable<bool> ChatService::connect_peer(std::string peer) {
    if (!running_) {
        emit_error("Not connected to a router yet.");
        co_return false;
    }

    std::string destination;
    try {
        destination = co_await resolve_destination(peer);
    } catch (const std::exception& error) {
        emit_error(std::string("could not resolve ") + peer + ": " + error.what());
        co_return false;
    }
    const std::string peer_addr =
        sam::Destination::from_public_base64(destination).base32();
    if (peer_addr == identity_.local_addr) {
        emit_error("That is this client's own address.");
        co_return false;
    }

    Peer& peer_entry = ensure_peer(peer_addr);
    if (peer_entry.link && peer_entry.link->secure()) {
        co_return true;
    }

    sessions_.on_connecting(peer_addr);
    sam::SamStream stream{asio::ip::tcp::socket(executor_), {}, {}};
    try {
        stream = co_await sam_->connect_stream(destination);
    } catch (const std::exception& error) {
        const double delay = sessions_.schedule_reconnect(peer_addr, error.what());
        sessions_.mark_live_failure(peer_addr, error.what());
        emit_error(std::string("connecting to ") + peer_addr.substr(0, 16) + "… failed: " +
                   error.what() + " (retry in " + std::to_string(static_cast<int>(delay)) +
                   "s)");
        co_return false;
    }

    peer_entry.connect_succeeded = false;
    peer_entry.connect_waiter = std::make_shared<asio::steady_timer>(executor_);
    peer_entry.connect_waiter->expires_after(kHandshakeTimeout + std::chrono::seconds(5));

    PeerLinkConfig config = link_config(peer_addr, session::ConnectionDirection::Outbound);
    PeerLink::Callbacks callbacks;
    callbacks.on_established = [this](PeerLink& link) { on_established(link); };
    callbacks.on_frame = [this](PeerLink& link, const PeerFrame& frame) {
        on_frame(link.peer_addr(), frame);
    };
    callbacks.on_closed = [this](PeerLink& link, const std::string& reason) {
        on_link_closed(link.peer_addr(), reason);
    };

    auto link = PeerLink::create(std::move(stream.socket), std::move(stream.prebuffered),
                                config, std::move(callbacks));
    attach_link(peer_entry, link);
    sessions_.on_stream_open(peer_addr, destination);
    sessions_.on_handshaking(peer_addr);
    link->start();

    boost::system::error_code ignored;
    co_await peer_entry.connect_waiter->async_wait(
        asio::redirect_error(asio::use_awaitable, ignored));

    Peer* settled = find_peer(peer_addr);
    if (settled == nullptr || !settled->connect_succeeded) {
        sessions_.mark_live_failure(peer_addr, "handshake did not complete");
        co_return false;
    }
    settled->connect_waiter.reset();
    co_return true;
}

void ChatService::disconnect_peer(std::string_view peer) {
    if (Peer* entry = find_peer(peer); entry != nullptr && entry->link) {
        entry->link->close_gracefully();
    }
}

void ChatService::on_established(PeerLink& link) {
    const std::string addr = link.peer_addr();
    if (addr.empty()) {
        return;
    }
    sessions_.on_secure(addr);
    sessions_.mark_live_ok(addr);

    Peer& peer = ensure_peer(addr);
    if (!peer.link) {
        // An inbound link only learns its address from the identity preface, so
        // this is where it gets registered.
        peer.link = link.shared_from_this();
        wire_transfers(peer);
    }
    peer.connect_succeeded = true;
    if (peer.connect_waiter) {
        peer.connect_waiter->cancel();
    }
    remember_contact(addr, {});
    emit_system("Secure channel with " + addr.substr(0, 16) + "… established");
    offer_blindbox_root(addr);
}

void ChatService::offer_blindbox_root(const std::string& peer_addr) {
    if (!config_.blindbox_enabled ||
        !blindbox::initiates_root_exchange(identity_.local_addr, peer_addr)) {
        return;
    }
    Peer* peer = find_peer(peer_addr);
    if (peer == nullptr || peer->link == nullptr || !peer->link->secure()) {
        return;
    }
    blindbox::PeerSnapshot& snapshot = blindbox_snapshot(peer_addr);
    const std::optional<blindbox::PendingRoot> pending =
        blindbox::ensure_pending_root(snapshot, config_.blindbox, unix_now());
    if (!pending.has_value()) {
        return;
    }
    if (pending->created) {
        save_blindbox_snapshot(peer_addr);
    }
    peer->link->send_signal(
        protocol::build_blindbox_root(pending->epoch, ByteView(pending->secret)),
        next_msg_id());
}

void ChatService::on_link_closed(const std::string& peer_addr, const std::string& reason) {
    if (peer_addr.empty()) {
        return;
    }
    sessions_.on_disconnected(peer_addr, reason);
    sessions_.clear_inflight(peer_addr);
    Peer* entry = find_peer(peer_addr);
    if (entry == nullptr) {
        return;
    }
    if (entry->incoming) {
        entry->incoming->reset();
    }
    if (entry->connect_waiter) {
        entry->connect_waiter->cancel();
    }
    emit_system("Disconnected from " + peer_addr.substr(0, 16) + "…: " + reason);
}

void ChatService::on_frame(const std::string& peer_addr, const PeerFrame& frame) {
    if (peer_addr.empty()) {
        return;
    }
    sessions_.touch(peer_addr);

    switch (frame.msg_type) {
        case 'U':
            on_text(peer_addr, frame.text(), frame.msg_id);
            return;
        case 'S':
            on_signal(peer_addr, protocol::parse_signal(frame.text()));
            return;
        case 'F':
        case 'D':
        case 'E':
        case 'G':
        case 'I':
            if (Peer* peer = find_peer(peer_addr); peer != nullptr && peer->incoming) {
                peer->incoming->on_frame(frame.msg_type, frame.text(), frame.msg_id);
            }
            return;
        default:
            return;
    }
}

void ChatService::on_text(const std::string& peer_addr, const std::string& text,
                          std::uint64_t msg_id) {
    storage::HistoryEntry entry;
    entry.kind = "in";
    entry.text = text;
    entry.ts = storage::now_iso8601_utc();
    if (msg_id != 0) {
        entry.message_id = std::to_string(msg_id);
    }
    append_history(peer_addr, entry);
    remember_contact(peer_addr, text);

    if (Peer* peer = find_peer(peer_addr); peer != nullptr && peer->link && msg_id != 0) {
        peer->link->send_signal(protocol::build_msg_ack(msg_id), next_msg_id());
    }
}

void ChatService::on_signal(const std::string& peer_addr, const protocol::Signal& signal) {
    if (!signal.well_formed && signal.kind != protocol::SignalKind::Unknown) {
        return;
    }
    Peer* peer = find_peer(peer_addr);
    switch (signal.kind) {
        case protocol::SignalKind::MsgAck:
            if (sessions_.acknowledge_inflight(peer_addr, signal.message_id)) {
                update_delivery(peer_addr, signal.message_id, DeliveryState::Delivered,
                                "live", "peer-ack");
            }
            return;
        case protocol::SignalKind::FileAck:
        case protocol::SignalKind::ImgAck:
            emit_system(signal.name + " delivered to " + peer_addr.substr(0, 16) + "…");
            return;
        case protocol::SignalKind::RejectFile:
            emit_system(peer_addr.substr(0, 16) + "… declined " + signal.name);
            return;
        case protocol::SignalKind::AbortFile:
            if (peer != nullptr && peer->incoming) {
                peer->incoming->on_signal(signal);
            }
            return;
        case protocol::SignalKind::Quit:
            if (peer != nullptr && peer->link) {
                peer->link->close("peer sent QUIT");
            }
            return;
        case protocol::SignalKind::BlindBoxRoot: {
            blindbox::PeerSnapshot& snapshot = blindbox_snapshot(peer_addr);
            if (signal.epoch < snapshot.root_epoch) {
                // An older epoch than the one in use is either a replay or a
                // peer that has not caught up; adopting it would send into a
                // slot the peer is not watching.
                return;
            }
            if (snapshot.root_secret.has_value() && snapshot.root_epoch != signal.epoch) {
                blindbox::PreviousRoot previous;
                previous.epoch = snapshot.root_epoch;
                previous.secret = *snapshot.root_secret;
                previous.expires_at = unix_now() + config_.blindbox.privacy.previous_grace_seconds;
                snapshot.prev_roots.insert(snapshot.prev_roots.begin(), previous);
                snapshot.prev_roots = blindbox::prune_previous_roots(
                    std::move(snapshot.prev_roots),
                    config_.blindbox.privacy.max_previous_roots, unix_now());
            }
            snapshot.root_secret = signal.root_secret;
            snapshot.root_epoch = signal.epoch;
            snapshot.root_created_at = unix_now();
            snapshot.root_send_index_base = snapshot.state.send_index;
            save_blindbox_snapshot(peer_addr);
            if (peer != nullptr && peer->link) {
                peer->link->send_signal(protocol::build_blindbox_root_ack(signal.epoch),
                                        next_msg_id());
            }
            emit_system("Offline delivery enabled with " + peer_addr.substr(0, 16) + "…");
            return;
        }
        case protocol::SignalKind::BlindBoxRootAck: {
            blindbox::PeerSnapshot& snapshot = blindbox_snapshot(peer_addr);
            if (blindbox::commit_pending_root(snapshot, signal.epoch, config_.blindbox,
                                              unix_now())) {
                save_blindbox_snapshot(peer_addr);
                emit_system("Offline delivery enabled with " + peer_addr.substr(0, 16) + "…");
            }
            return;
        }
        default:
            return;
    }
}

asio::awaitable<std::vector<std::uint64_t>> ChatService::send_text(std::string peer,
                                                                   std::string text) {
    std::vector<std::uint64_t> ids;
    const std::string peer_addr = std::string(sam::normalize_peer_address(peer));
    if (peer_addr.empty()) {
        emit_error("not an I2P address: " + peer);
        co_return ids;
    }

    const std::vector<std::string> chunks = protocol::split_long_chat_text(text);
    for (const std::string& chunk : chunks) {
        const std::uint64_t msg_id = next_msg_id();
        storage::HistoryEntry entry;
        entry.kind = "out";
        entry.text = chunk;
        entry.ts = storage::now_iso8601_utc();
        entry.message_id = std::to_string(msg_id);

        Peer* target = find_peer(peer_addr);
        const bool live_path =
            target != nullptr && target->link != nullptr && target->link->secure() &&
            sessions_.live_ready(peer_addr);
        if (live_path) {
            entry.delivery_state = "sent";
            entry.delivery_route = "live";
            append_history(peer_addr, entry);
            target->link->send_text('U', chunk, msg_id);
            sessions_.register_inflight(peer_addr, msg_id);
            if (events_.on_delivery) {
                events_.on_delivery(DeliveryReport{peer_addr, msg_id, DeliveryState::Sent,
                                                   "live", "sent"});
            }
            ids.push_back(msg_id);
            remember_contact(peer_addr, chunk);
            continue;
        }

        entry.delivery_state = "sending";
        entry.delivery_route = "blindbox";
        append_history(peer_addr, entry);
        const bool queued = co_await send_offline(peer_addr, chunk, msg_id);
        if (queued) {
            ids.push_back(msg_id);
            remember_contact(peer_addr, chunk);
        }
    }
    co_return ids;
}

bool ChatService::ensure_replica_client() {
    if (replicas_) {
        return true;
    }
    if (!config_.blindbox_enabled || replica_settings_.endpoints.empty()) {
        return false;
    }
    blindbox::ReplicaClientConfig config;
    config.endpoints = replica_settings_.endpoints;
    config.replica_auth = replica_settings_.auth;
    try {
        blindbox::StreamFactory factory =
            config_.blindbox_over_sam && sam_
                ? blindbox::sam_stream_factory(sam_)
                : blindbox::direct_stream_factory(executor_);
        replicas_ = std::make_shared<blindbox::ReplicaClient>(executor_, std::move(config),
                                                              std::move(factory));
    } catch (const std::exception& error) {
        emit_error(std::string("BlindBox replicas are misconfigured: ") + error.what());
        return false;
    }
    return true;
}

asio::awaitable<bool> ChatService::send_offline(const std::string& peer_addr,
                                               std::string text, std::uint64_t msg_id) {
    if (!ensure_replica_client()) {
        update_delivery(peer_addr, msg_id, DeliveryState::Failed, "blindbox",
                        "no-replicas");
        emit_error("Peer is offline and no BlindBox replicas are configured.");
        co_return false;
    }
    blindbox::PeerSnapshot& snapshot = blindbox_snapshot(peer_addr);
    if (!snapshot.root_secret.has_value()) {
        update_delivery(peer_addr, msg_id, DeliveryState::Failed, "blindbox", "no-root");
        emit_error(
            "Peer is offline and no offline channel has been agreed yet — connect once "
            "while both clients are online.");
        co_return false;
    }

    const Bytes frame =
        protocol::encode_frame('U', as_bytes(text), msg_id, 0);
    try {
        const blindbox::SendOutcome outcome = co_await blindbox::send_pairwise(
            *replicas_, snapshot, identity_.local_addr, frame, config_.blindbox, unix_now());
        save_blindbox_snapshot(peer_addr);
        update_delivery(peer_addr, msg_id, DeliveryState::Queued, "blindbox",
                        "slot " + std::to_string(outcome.index));
        co_return true;
    } catch (const std::exception& error) {
        update_delivery(peer_addr, msg_id, DeliveryState::Failed, "blindbox", error.what());
        emit_error(std::string("BlindBox send failed: ") + error.what());
        co_return false;
    }
}

asio::awaitable<bool> ChatService::send_file(std::string peer, std::filesystem::path path) {
    const std::string peer_addr = std::string(sam::normalize_peer_address(peer));
    Peer* target = find_peer(peer_addr);
    if (target == nullptr || target->link == nullptr || !target->link->secure()) {
        emit_error("Files can only be sent over a live connection.");
        co_return false;
    }

    std::optional<transfer::OutgoingTransfer> outgoing;
    try {
        outgoing.emplace(std::move(path), false);
    } catch (const std::exception& error) {
        emit_error(std::string("cannot send that file: ") + error.what());
        co_return false;
    }

    const std::uint64_t msg_id = next_msg_id();
    const transfer::Frame header = outgoing->header();
    target->link->send_text(header.type, header.body, msg_id);
    while (std::optional<transfer::Frame> next = outgoing->next()) {
        Peer* still = find_peer(peer_addr);
        if (still == nullptr || still->link == nullptr || still->link->closed()) {
            emit_error("Connection dropped mid-transfer.");
            co_return false;
        }
        still->link->send_text(next->type, next->body, next_msg_id());
        if (events_.on_transfer) {
            events_.on_transfer(peer_addr, outgoing->progress());
        }
        // Yield so keepalives and incoming frames are not starved by a large
        // file's chunk loop.
        asio::steady_timer breath(executor_);
        breath.expires_after(std::chrono::milliseconds(0));
        boost::system::error_code ignored;
        co_await breath.async_wait(asio::redirect_error(asio::use_awaitable, ignored));
    }
    if (events_.on_transfer) {
        events_.on_transfer(peer_addr, outgoing->progress(transfer::Outcome::Completed));
    }
    storage::HistoryEntry entry;
    entry.kind = "out";
    entry.text = "[file] " + outgoing->name();
    entry.ts = storage::now_iso8601_utc();
    append_history(peer_addr, entry);
    co_return true;
}

asio::awaitable<bool> ChatService::send_image(std::string peer, std::filesystem::path path) {
    const std::string peer_addr = std::string(sam::normalize_peer_address(peer));
    Peer* target = find_peer(peer_addr);
    if (target == nullptr || target->link == nullptr || !target->link->secure()) {
        emit_error("Images can only be sent over a live connection.");
        co_return false;
    }

    std::optional<transfer::OutgoingTransfer> outgoing;
    try {
        outgoing.emplace(std::move(path), true);
    } catch (const std::exception& error) {
        emit_error(std::string("cannot send that image: ") + error.what());
        co_return false;
    }

    const std::uint64_t msg_id = next_msg_id();
    const transfer::Frame header = outgoing->header();
    target->link->send_text(header.type, header.body, msg_id);
    while (std::optional<transfer::Frame> next = outgoing->next()) {
        Peer* still = find_peer(peer_addr);
        if (still == nullptr || still->link == nullptr || still->link->closed()) {
            co_return false;
        }
        still->link->send_text(next->type, next->body, next_msg_id());
        asio::steady_timer breath(executor_);
        breath.expires_after(std::chrono::milliseconds(0));
        boost::system::error_code ignored;
        co_await breath.async_wait(asio::redirect_error(asio::use_awaitable, ignored));
    }
    if (events_.on_transfer) {
        events_.on_transfer(peer_addr, outgoing->progress(transfer::Outcome::Completed));
    }
    storage::HistoryEntry entry;
    entry.kind = "out";
    entry.text = "[image] " + outgoing->name();
    entry.ts = storage::now_iso8601_utc();
    append_history(peer_addr, entry);
    co_return true;
}

asio::awaitable<void> ChatService::blindbox_loop() {
    asio::steady_timer timer(executor_);
    while (running_) {
        timer.expires_after(config_.blindbox_poll_interval);
        boost::system::error_code error;
        co_await timer.async_wait(asio::redirect_error(asio::use_awaitable, error));
        if (error || !running_) {
            co_return;
        }
        try {
            co_await poll_blindbox();
        } catch (const std::exception& failure) {
            emit_error(std::string("BlindBox poll failed: ") + failure.what());
        }
    }
}

asio::awaitable<std::size_t> ChatService::poll_blindbox() {
    if (!ensure_replica_client()) {
        co_return 0;
    }
    std::size_t delivered = 0;
    // Snapshot the addresses first: collecting mutates the map.
    std::vector<std::string> addrs;
    for (const auto& [addr, snapshot] : blindbox_snapshots_) {
        if (snapshot.root_secret.has_value() || !snapshot.prev_roots.empty()) {
            addrs.push_back(addr);
        }
    }
    for (const std::string& addr : addrs) {
        delivered += co_await collect_from_peer(addr);
    }
    if (config_.blindbox.privacy.cover_gets > 0) {
        co_await blindbox::emit_cover_gets(*replicas_, config_.blindbox.privacy.cover_gets);
    }
    co_return delivered;
}

asio::awaitable<std::size_t> ChatService::collect_from_peer(const std::string& peer_addr) {
    blindbox::PeerSnapshot& snapshot = blindbox_snapshot(peer_addr);
    std::vector<blindbox::ReceivedMessage> messages;
    try {
        messages = co_await blindbox::poll_pairwise(*replicas_, snapshot,
                                                    identity_.local_addr, config_.blindbox,
                                                    unix_now());
    } catch (const std::exception& error) {
        emit_error(std::string("BlindBox collection failed: ") + error.what());
        co_return 0;
    }
    if (messages.empty()) {
        co_return 0;
    }
    save_blindbox_snapshot(peer_addr);

    std::size_t delivered = 0;
    for (const blindbox::ReceivedMessage& message : messages) {
        // A collected blob holds one plaintext vNext frame, the same bytes the
        // live path would have carried.
        protocol::FrameReader reader;
        reader.feed(ByteView(message.frame));
        try {
            while (const std::optional<protocol::Frame> frame = reader.next()) {
                if (frame->msg_type != 'U') {
                    continue;
                }
                storage::HistoryEntry entry;
                entry.kind = "in";
                entry.text = to_string(ByteView(frame->payload));
                entry.ts = storage::now_iso8601_utc();
                entry.delivery_route = "blindbox";
                if (frame->msg_id != 0) {
                    entry.message_id = std::to_string(frame->msg_id);
                }
                append_history(peer_addr, entry);
                remember_contact(peer_addr, entry.text);
                ++delivered;
            }
        } catch (const protocol::ProtocolError& error) {
            emit_error(std::string("a collected BlindBox message was malformed: ") +
                       error.what());
        }
    }
    co_return delivered;
}

blindbox::PeerSnapshot& ChatService::blindbox_snapshot(const std::string& peer_addr) {
    const auto found = blindbox_snapshots_.find(peer_addr);
    if (found != blindbox_snapshots_.end()) {
        return found->second;
    }
    const std::filesystem::path path =
        paths_.data_dir() /
        blindbox::peer_state_filename(config_.profile, peer_addr);
    blindbox::PeerSnapshot snapshot = blindbox::load_peer_snapshot(
        path, peer_addr, config_.profile, ByteView(identity_.signing_seed));
    return blindbox_snapshots_.emplace(peer_addr, std::move(snapshot)).first->second;
}

void ChatService::save_blindbox_snapshot(const std::string& peer_addr) {
    const auto found = blindbox_snapshots_.find(peer_addr);
    if (found == blindbox_snapshots_.end() || config_.profile == kTransientProfile) {
        return;
    }
    const std::filesystem::path path =
        paths_.data_dir() / blindbox::peer_state_filename(config_.profile, peer_addr);
    try {
        blindbox::save_peer_snapshot(path, found->second, config_.profile,
                                     ByteView(identity_.signing_seed));
    } catch (const std::exception& error) {
        emit_error(std::string("could not save BlindBox state: ") + error.what());
    }
}

void ChatService::append_history(const std::string& peer_addr, storage::HistoryEntry entry) {
    std::vector<storage::HistoryEntry>& entries = history_[peer_addr];
    if (entries.empty()) {
        entries = storage::load_history(paths_, peer_addr, ByteView(identity_.identity_key));
    }
    entries.push_back(entry);
    if (config_.profile != kTransientProfile) {
        try {
            storage::save_history(paths_, peer_addr, entries,
                                  ByteView(identity_.identity_key), config_.retention);
        } catch (const std::exception& error) {
            emit_error(std::string("could not save history: ") + error.what());
        }
    }
    if (events_.on_history) {
        events_.on_history(peer_addr, entry);
    }
}

void ChatService::update_delivery(const std::string& peer_addr, std::uint64_t msg_id,
                                  DeliveryState state, const std::string& route,
                                  const std::string& reason) {
    const std::string id = std::to_string(msg_id);
    std::vector<storage::HistoryEntry>& entries = history_[peer_addr];
    for (auto entry = entries.rbegin(); entry != entries.rend(); ++entry) {
        if (entry->message_id.has_value() && *entry->message_id == id) {
            entry->delivery_state = std::string(delivery_state_name(state));
            entry->delivery_route = route;
            entry->delivery_reason = reason;
            break;
        }
    }
    if (config_.profile != kTransientProfile && !entries.empty()) {
        try {
            storage::save_history(paths_, peer_addr, entries,
                                  ByteView(identity_.identity_key), config_.retention);
        } catch (const std::exception&) {
            // A delivery marker is not worth failing a send over.
        }
    }
    if (events_.on_delivery) {
        events_.on_delivery(DeliveryReport{peer_addr, msg_id, state, route, reason});
    }
}

void ChatService::remember_contact(const std::string& peer_addr, const std::string& preview) {
    bool changed = contacts_.remember_peer(peer_addr);
    changed = contacts_.set_last_active_peer(peer_addr) || changed;
    if (!preview.empty()) {
        changed = contacts_.touch_peer_message_meta(peer_addr, preview_of(preview),
                                                   storage::now_iso8601_utc()) ||
                  changed;
    }
    if (!changed) {
        return;
    }
    save_contacts();
    if (events_.on_contacts_changed) {
        events_.on_contacts_changed();
    }
}

void ChatService::save_contacts() {
    if (config_.profile == kTransientProfile) {
        return;
    }
    try {
        storage::save_contact_book(paths_.contacts(), contacts_,
                                   ByteView(identity_.identity_key));
    } catch (const std::exception& error) {
        emit_error(std::string("could not save contacts: ") + error.what());
    }
}

std::vector<storage::HistoryEntry> ChatService::history(std::string_view peer) const {
    const std::string addr(peer);
    const auto found = history_.find(addr);
    if (found != history_.end()) {
        return found->second;
    }
    return storage::load_history(paths_, addr, ByteView(identity_.identity_key));
}

bool ChatService::live(std::string_view peer) {
    const std::string addr(peer);
    const auto found = peers_.find(addr);
    if (found == peers_.end() || !found->second->link || !found->second->link->secure()) {
        return false;
    }
    return sessions_.live_ready(addr);
}

std::vector<std::string> ChatService::connected_peers() const {
    std::vector<std::string> out;
    for (const auto& [addr, peer] : peers_) {
        if (peer->link && peer->link->secure()) {
            out.push_back(addr);
        }
    }
    return out;
}

bool ChatService::blindbox_ready() const {
    return config_.blindbox_enabled && !replica_settings_.endpoints.empty();
}

std::uint64_t ChatService::next_msg_id() {
    const std::uint64_t id = next_msg_id_;
    next_msg_id_ = next_msg_id_ == 0xFFFFFFFFFFFFFFFFULL ? 1 : next_msg_id_ + 1;
    return id;
}

void ChatService::emit_system(const std::string& message) const {
    if (events_.on_system) {
        events_.on_system(message);
    }
}

void ChatService::emit_error(const std::string& message) const {
    if (events_.on_error) {
        events_.on_error(message);
    }
}

}  // namespace i2pchat::runtime
