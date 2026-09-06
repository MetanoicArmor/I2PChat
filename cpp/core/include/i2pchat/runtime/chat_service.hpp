#pragma once

#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/blindbox/coordinator.hpp"
#include "i2pchat/protocol/secure_frame.hpp"
#include "i2pchat/runtime/identity.hpp"
#include "i2pchat/runtime/peer_link.hpp"
#include "i2pchat/sam/client.hpp"
#include "i2pchat/session/manager.hpp"
#include "i2pchat/session/trust_store.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/contacts.hpp"
#include "i2pchat/storage/profile_paths.hpp"
#include "i2pchat/storage/replica_settings.hpp"
#include "i2pchat/transfer/manager.hpp"
#include "i2pchat/groups/coordinator.hpp"
#include "i2pchat/groups/invite.hpp"
#include "i2pchat/groups/store.hpp"
#include "i2pchat/groups/wire.hpp"

/// The core, as a UI sees it.
///
/// One object owns the SAM session, the peer links, the trust store, the
/// on-disk profile and the offline path, and reports everything through a
/// callback struct. A front end — the TUI, the Qt client, a test — drives it
/// with a handful of coroutines and never touches a socket or a cipher.
///
/// This is what the reference implementation's 11,500-line `I2PChatCore` was,
/// minus the parts that belong to the layers underneath: framing, key
/// derivation, replica quorums, group fan-out and transfer state machines all
/// live in their own modules and are testable without a network. What is left
/// here is orchestration, and only orchestration.
///
/// Threading: everything runs on the executor handed to the constructor.
/// Callbacks fire on that executor, so a UI thread must marshal them itself.
namespace i2pchat::runtime {

struct ChatServiceConfig {
    /// Application root that holds `profiles/`.
    std::filesystem::path app_root;
    std::string profile = "default";
    sam::SamEndpoint sam;
    /// Where received files and images land. Defaults are placed under the
    /// profile directory when left empty.
    std::filesystem::path downloads_dir;
    std::filesystem::path images_dir;
    storage::RetentionPolicy retention;
    session::SessionManagerConfig sessions;
    protocol::PaddingProfile padding = protocol::PaddingProfile::Balanced;
    /// I2CP tunnel options passed to SESSION CREATE.
    std::vector<std::pair<std::string, std::string>> sam_options{
        {"inbound.length", "2"},
        {"outbound.length", "2"},
        {"inbound.quantity", "3"},
        {"outbound.quantity", "3"},
    };
    /// Offline delivery through BlindBox replicas. Off when the profile
    /// configures no replicas, regardless of this flag.
    bool blindbox_enabled = true;
    blindbox::CoordinatorConfig blindbox;
    /// Endpoints and tokens. Loaded from the profile when left empty.
    std::optional<storage::ReplicaSettings> replicas;
    /// Whether to reach replicas through I2P rather than dialling them
    /// directly. Direct is right for a replica on the local machine only.
    bool blindbox_over_sam = true;
    std::chrono::seconds blindbox_poll_interval{25};
};

/// What happened to one outgoing message.
enum class DeliveryState {
    /// Handed to the transport, no confirmation yet.
    Sent,
    /// The peer acknowledged it.
    Delivered,
    /// Stored on the replicas for the peer to collect.
    Queued,
    Failed,
};

[[nodiscard]] std::string_view delivery_state_name(DeliveryState state);

struct DeliveryReport {
    std::string peer;
    std::uint64_t msg_id = 0;
    DeliveryState state = DeliveryState::Sent;
    /// "live" or "blindbox".
    std::string route;
    std::string reason;
};

struct ChatEvents {
    std::function<void(const std::string& message)> on_system;
    std::function<void(const std::string& message)> on_error;
    /// A history entry was appended for `peer`, incoming or outgoing. The entry
    /// is already persisted.
    std::function<void(const std::string& peer, const storage::HistoryEntry& entry)>
        on_history;
    std::function<void(const DeliveryReport& report)> on_delivery;
    std::function<void(const std::string& peer, session::PeerState state,
                       const std::string& reason)>
        on_peer_state;
    std::function<void(session::TransportState state, const std::string& reason)>
        on_transport_state;
    std::function<void(const std::string& peer, const transfer::Progress& progress)>
        on_transfer;
    std::function<void(const std::string& peer, const std::filesystem::path& path)>
        on_file_received;
    std::function<void(const std::string& peer, const std::filesystem::path& path)>
        on_image_received;
    /// A peer sent a text-rendered image, lines joined by newlines.
    std::function<void(const std::string& peer, const std::string& text)> on_image_text;
    /// Whether to accept an offered file. Unset means accept.
    std::function<bool(const std::string& peer, const std::string& name,
                       std::uint64_t size)>
        accept_file;
    /// First sighting or key change. Unset means reject a changed key and
    /// accept a first sighting, which is what trust on first use means.
    session::TrustPromptHandler on_trust_prompt;
    std::function<void()> on_contacts_changed;
    /// Group list or an open group's history changed.
    std::function<void(const std::string& group_id)> on_group_message;
    /// The local address became known, once the SAM session is up.
    std::function<void(const std::string& local_addr)> on_local_address;
};

class ChatService {
public:
    ChatService(asio::any_io_executor executor, ChatServiceConfig config,
                ChatEvents events = {});
    ~ChatService();

    ChatService(const ChatService&) = delete;
    ChatService& operator=(const ChatService&) = delete;

    /// Open the profile, create the SAM session and start accepting peers.
    ///
    /// Throws on a failure that leaves the service unusable — no router, or a
    /// profile that cannot be opened. A missing replica configuration is not
    /// such a failure: the live path works without one.
    asio::awaitable<void> start();

    /// Close every peer link and the SAM session, and flush state to disk.
    asio::awaitable<void> stop();

    /// Dial a peer and complete the handshake. `peer` may be a base32 address,
    /// a `.b32.i2p` host or a full I2P-base64 destination.
    ///
    /// Returns false when the peer could not be reached or the handshake was
    /// refused; the reason is reported through `on_error`.
    asio::awaitable<bool> connect_peer(std::string peer);
    void disconnect_peer(std::string_view peer);

    /// Send a chat message, splitting it into protocol-sized parts.
    ///
    /// Takes the live channel when the peer has one, and the offline path
    /// otherwise. Returns the message id of each part, in order; empty when
    /// nothing could be sent.
    asio::awaitable<std::vector<std::uint64_t>> send_text(std::string peer,
                                                          std::string text);

    /// Send a file over the live channel. Offline file transfer is not part of
    /// the protocol: the peer has to be connected.
    asio::awaitable<bool> send_file(std::string peer, std::filesystem::path path);
    /// Send an image, inline when the peer can display it.
    asio::awaitable<bool> send_image(std::string peer, std::filesystem::path path);

    /// Collect whatever the replicas are holding for every known peer. Called
    /// on a timer while the service is running; exposed for the UI's manual
    /// "check now" and for tests.
    asio::awaitable<std::size_t> poll_blindbox();

    [[nodiscard]] const ProfileIdentity& identity() const { return identity_; }
    [[nodiscard]] const std::string& local_addr() const { return identity_.local_addr; }
    [[nodiscard]] const storage::ProfilePaths& paths() const { return paths_; }
    [[nodiscard]] session::SessionManager& sessions() { return sessions_; }
    [[nodiscard]] session::TrustStore& trust() { return trust_; }
    [[nodiscard]] storage::ContactBook& contacts() { return contacts_; }
    /// Persist the contact book. The service saves it itself on every change it
    /// makes; a UI that edits it directly must call this.
    void save_contacts();

    [[nodiscard]] std::vector<storage::HistoryEntry> history(std::string_view peer) const;
    [[nodiscard]] bool live(std::string_view peer);
    [[nodiscard]] bool peer_offline_ready(std::string_view peer) const;
    [[nodiscard]] std::vector<std::string> connected_peers() const;
    /// True when the offline path is configured and usable.
    [[nodiscard]] bool blindbox_ready() const;
    [[nodiscard]] bool blindbox_enabled() const noexcept { return config_.blindbox_enabled; }
    [[nodiscard]] const storage::ReplicaSettings& replica_settings() const {
        return replica_settings_;
    }
    void save_replica_settings(storage::ReplicaSettings settings);
    void set_retention(storage::RetentionPolicy policy) { config_.retention = policy; }
    [[nodiscard]] bool running() const noexcept { return running_; }

    [[nodiscard]] std::vector<groups::GroupState> list_groups() const;
    [[nodiscard]] std::optional<groups::StoredConversation> load_group(
        std::string_view group_id) const;
    groups::GroupState create_group(std::string title, std::vector<std::string> members);
    groups::GroupState update_group(const std::string& group_id, std::string title,
                                    std::vector<std::string> members);
    bool delete_group(const std::string& group_id);
    groups::GroupState join_group_invite(std::string_view token);
    void append_group_text(const std::string& group_id, std::string text);
    asio::awaitable<void> send_group_text(std::string group_id, std::string text);
    [[nodiscard]] std::string encode_group_invite(const std::string& group_id);
    [[nodiscard]] std::optional<groups::TopologySnapshot> group_topology(
        const std::string& group_id);

private:
    struct Peer;

    Peer& ensure_peer(const std::string& peer_addr);
    Peer* find_peer(std::string_view peer_addr);

    void attach_link(Peer& peer, std::shared_ptr<PeerLink> link);
    void wire_transfers(Peer& peer);
    [[nodiscard]] PeerLinkConfig link_config(const std::string& peer_addr,
                                             session::ConnectionDirection direction);

    void on_frame(const std::string& peer_addr, const PeerFrame& frame);
    void on_signal(const std::string& peer_addr, const protocol::Signal& signal);
    void on_link_closed(const std::string& peer_addr, const std::string& reason);
    void on_text(const std::string& peer_addr, const std::string& text,
                 std::uint64_t msg_id);
    bool ingest_group_transport(const std::string& peer_addr, const std::string& text);
    void ensure_group_coordinator();
    void emit_group_message(const std::string& group_id) const;
    void on_established(PeerLink& link);
    /// Offer the peer a BlindBox root if this side is the one that offers and
    /// the channel has none, or the current one is due for replacement.
    void offer_blindbox_root(const std::string& peer_addr);

    asio::awaitable<void> accept_loop();
    asio::awaitable<void> blindbox_loop();
    asio::awaitable<bool> send_offline(const std::string& peer_addr, std::string text,
                                       std::uint64_t msg_id);
    asio::awaitable<std::size_t> collect_from_peer(const std::string& peer_addr);
    asio::awaitable<std::size_t> collect_from_groups();

    /// Resolve a user-supplied peer string to a full destination.
    asio::awaitable<std::string> resolve_destination(const std::string& peer);

    void append_history(const std::string& peer_addr, storage::HistoryEntry entry);
    void update_delivery(const std::string& peer_addr, std::uint64_t msg_id,
                         DeliveryState state, const std::string& route,
                         const std::string& reason = {});
    void remember_contact(const std::string& peer_addr, const std::string& preview);
    void emit_system(const std::string& message) const;
    void emit_error(const std::string& message) const;
    [[nodiscard]] std::uint64_t next_msg_id();
    [[nodiscard]] blindbox::PeerSnapshot& blindbox_snapshot(const std::string& peer_addr);
    void save_blindbox_snapshot(const std::string& peer_addr);
    [[nodiscard]] bool ensure_replica_client();

    asio::any_io_executor executor_;
    ChatServiceConfig config_;
    ChatEvents events_;
    storage::ProfilePaths paths_;
    ProfileIdentity identity_;
    session::TrustStore trust_;
    storage::ContactBook contacts_;
    session::SessionManager sessions_;
    std::shared_ptr<sam::SamSession> sam_;
    std::shared_ptr<blindbox::ReplicaClient> replicas_;
    storage::ReplicaSettings replica_settings_;
    std::map<std::string, std::unique_ptr<Peer>> peers_;
    /// Loaded lazily and kept in memory: every send and poll touches it.
    std::map<std::string, blindbox::PeerSnapshot> blindbox_snapshots_;
    /// Conversations opened this run. Held in memory because a history file is
    /// rewritten whole on every append.
    std::map<std::string, std::vector<storage::HistoryEntry>> history_;
    std::uint64_t next_msg_id_ = 1;
    bool running_ = false;
    bool stopping_ = false;
    std::unique_ptr<groups::GroupCoordinator> group_coordinator_;
    std::string last_group_bb_msg_id_;
};

}  // namespace i2pchat::runtime
