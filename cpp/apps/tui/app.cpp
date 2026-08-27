#include "app.hpp"

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/executor_work_guard.hpp>
#include <algorithm>
#include <cctype>
#include <sstream>

#include "i2pchat/bytes.hpp"

#include <ftxui/dom/elements.hpp>

#include "i2pchat/groups/store.hpp"
#include "i2pchat/presentation/commands.hpp"
#include "i2pchat/protocol/signals.hpp"
#include "i2pchat/router/i2pd.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/storage/chat_history.hpp"

namespace i2pchat::tui {
namespace asio = boost::asio;
using namespace ftxui;
namespace {

std::string help_text() {
    std::ostringstream out;
    out << "Slash commands. Tab completes. F1–F8 switch screens.\n\n";
    for (const presentation::CommandHelp& entry : presentation::command_help()) {
        out << "  " << entry.usage << "\n      " << entry.summary << "\n";
    }
    return out.str();
}

Color color_for(presentation::LineKind kind) {
    switch (kind) {
        case presentation::LineKind::Incoming:
            return Color::GreenLight;
        case presentation::LineKind::Outgoing:
            return Color::Cyan;
        case presentation::LineKind::Error:
            return Color::RedLight;
        case presentation::LineKind::System:
            return Color::Yellow;
    }
    return Color::White;
}

}  // namespace

TuiApp::TuiApp(Options options) : options_(std::move(options)) {}

TuiApp::~TuiApp() { stop_core(); }

void TuiApp::post_ui(std::function<void()> work) {
    screen_.Post(Task{std::move(work)});
}

void TuiApp::post_core(std::function<asio::awaitable<void>()> work) {
    asio::co_spawn(core_, std::move(work), asio::detached);
}

void TuiApp::append_local(presentation::LineKind kind, std::string text) {
    presentation::ChatLine line;
    line.kind = kind;
    line.time = presentation::format_clock(storage::now_iso8601_utc());
    line.text = std::move(text);
    std::lock_guard lock(mutex_);
    lines_.push_back(std::move(line));
}

void TuiApp::refresh_rows() {
    if (!service_) {
        return;
    }
    std::vector<std::pair<std::string, unsigned>> unread;
    {
        std::lock_guard lock(mutex_);
        unread.assign(unread_.begin(), unread_.end());
    }
    const std::vector<presentation::ContactRow> rows = presentation::contact_rows(
        service_->contacts(), service_->connected_peers(), selected_, unread);
    std::lock_guard lock(mutex_);
    contacts_ = rows;
}

presentation::StatusInput TuiApp::status_input() const {
    presentation::StatusInput input;
    input.profile = options_.profile;
    input.local_addr = local_addr_;
    input.transport = transport_;
    input.transport_reason = status_reason_;
    if (service_) {
        input.live_peers = service_->connected_peers();
        input.contacts = service_->contacts().contacts().size();
        input.blindbox_ready = service_->blindbox_ready();
    }
    input.selected = selected_;
    return input;
}

session::TrustDecision TuiApp::on_trust_prompt(session::TrustPrompt prompt,
                                               const std::string& peer_addr,
                                               const std::string& new_key_hex,
                                               const std::string& old_key_hex) {
    {
        std::lock_guard lock(trust_mutex_);
        trust_decision_.reset();
        trust_view_ = presentation::trust_prompt_view(prompt, peer_addr, new_key_hex,
                                                      old_key_hex);
        trust_open_ = true;
    }
    post_ui([] {});
    std::unique_lock lock(trust_mutex_);
    trust_cv_.wait(lock, [this] { return trust_decision_.has_value(); });
    const session::TrustDecision decision = *trust_decision_;
    trust_open_ = false;
    return decision;
}

void TuiApp::select_peer(std::string addr) {
    selected_ = std::move(addr);
    unread_.erase(selected_);
    if (service_) {
        lines_ = presentation::lines_from_history(service_->history(selected_),
                                                  service_->contacts(), selected_);
        service_->contacts().set_last_active_peer(selected_);
        service_->save_contacts();
    }
    refresh_rows();
}

std::string TuiApp::resolve_peer(std::string_view token) const {
    if (token.empty()) {
        return selected_;
    }
    bool digits = !token.empty();
    for (const char ch : token) {
        if (std::isdigit(static_cast<unsigned char>(ch)) == 0) {
            digits = false;
            break;
        }
    }
    if (digits) {
        const std::size_t index = static_cast<std::size_t>(std::stoul(std::string(token)));
        for (const presentation::ContactRow& row : contacts_) {
            if (row.index == index) {
                return row.addr;
            }
        }
    }
    const std::string normalized(sam::normalize_peer_address(std::string(token)));
    if (service_) {
        if (const storage::ContactRecord* record = service_->contacts().get(normalized);
            record != nullptr) {
            return record->addr;
        }
    }
    return normalized.empty() ? std::string(token) : normalized;
}

void TuiApp::start_core() {
    runtime::ChatServiceConfig config;
    config.app_root = options_.app_root;
    config.profile = options_.profile;
    config.sam.host = options_.sam_host;
    config.sam.port = options_.sam_port;
    config.blindbox_over_sam = options_.blindbox_over_sam;
    config.blindbox_poll_interval = options_.blindbox_poll;
    if (!options_.replicas.empty()) {
        storage::ReplicaSettings replicas;
        replicas.endpoints = options_.replicas;
        config.replicas = replicas;
        config.blindbox_enabled = true;
    }

    if (!options_.bundled_router.empty()) {
        router::I2pdManager::Config router_config;
        router_config.binary = options_.bundled_router;
        router_config.data_dir = options_.app_root / "i2pd";
        router_config.runtime.sam_host = options_.sam_host;
        router_config.runtime.sam_port = options_.sam_port;
        router_ = std::make_unique<router::I2pdManager>(std::move(router_config));
        router_->prepare_data_dir();
        router_->start();
        config.sam.port = router_->runtime().sam_port;
    }

    runtime::ChatEvents events;
    events.on_system = [this](const std::string& message) {
        post_ui([this, message] {
            append_local(presentation::LineKind::System, message);
            screen_.RequestAnimationFrame();
        });
    };
    events.on_error = [this](const std::string& message) {
        post_ui([this, message] {
            append_local(presentation::LineKind::Error, message);
            screen_.RequestAnimationFrame();
        });
    };
    events.on_history = [this](const std::string& peer, const storage::HistoryEntry& entry) {
        const storage::ContactBook contacts =
            service_ ? service_->contacts() : storage::ContactBook{};
        post_ui([this, peer, entry, contacts] {
            {
                std::lock_guard lock(mutex_);
                if (peer == selected_) {
                    lines_.push_back(presentation::line_from_history(entry, contacts, peer));
                } else if (entry.kind == "in") {
                    unread_[peer] += 1;
                }
            }
            refresh_rows();
            screen_.RequestAnimationFrame();
        });
    };
    events.on_delivery = [this](const runtime::DeliveryReport& report) {
        post_ui([this, report] {
            if (report.peer != selected_ || !service_) {
                return;
            }
            lines_ = presentation::lines_from_history(service_->history(selected_),
                                                      service_->contacts(), selected_);
            screen_.RequestAnimationFrame();
        });
    };
    events.on_transport_state = [this](session::TransportState state,
                                       const std::string& reason) {
        post_ui([this, state, reason] {
            transport_ = state;
            status_reason_ = reason;
            screen_.RequestAnimationFrame();
        });
    };
    events.on_peer_state = [this](const std::string&, session::PeerState,
                                  const std::string&) {
        post_ui([this] {
            refresh_rows();
            screen_.RequestAnimationFrame();
        });
    };
    events.on_transfer = [this](const std::string&, const transfer::Progress& progress) {
        post_ui([this, progress] {
            transfers_.push_back(progress);
            if (transfers_.size() > 32) {
                transfers_.erase(transfers_.begin());
            }
            screen_.RequestAnimationFrame();
        });
    };
    events.on_local_address = [this](const std::string& addr) {
        post_ui([this, addr] {
            local_addr_ = addr;
            screen_.RequestAnimationFrame();
        });
    };
    events.on_trust_prompt = [this](session::TrustPrompt prompt, const std::string& peer,
                                    const std::string& neu, const std::string& old) {
        return on_trust_prompt(prompt, peer, neu, old);
    };

    service_ = std::make_unique<runtime::ChatService>(core_.get_executor(), std::move(config),
                                                      std::move(events));
    running_ = true;
    post_core([this]() -> asio::awaitable<void> {
        try {
            co_await service_->start();
            if (!options_.connect.empty()) {
                const bool ok = co_await service_->connect_peer(options_.connect);
                post_ui([this, ok] {
                    if (ok) {
                        select_peer(std::string(sam::normalize_peer_address(options_.connect)));
                    }
                });
            }
        } catch (const std::exception& error) {
            post_ui([this, message = std::string(error.what())] {
                append_local(presentation::LineKind::Error,
                             "failed to start: " + message);
            });
        }
    });
    core_thread_ = std::thread([this] {
        auto work = asio::make_work_guard(core_);
        core_.run();
    });
}

void TuiApp::stop_core() {
    if (!running_) {
        return;
    }
    running_ = false;
    {
        std::lock_guard lock(trust_mutex_);
        if (!trust_decision_) {
            trust_decision_ = session::TrustDecision::Reject;
        }
        trust_cv_.notify_all();
    }
    asio::co_spawn(
        core_,
        [this]() -> asio::awaitable<void> {
            if (service_) {
                co_await service_->stop();
            }
            core_.stop();
        },
        asio::detached);
    if (core_thread_.joinable()) {
        core_thread_.join();
    }
    if (router_) {
        router_->stop();
        router_.reset();
    }
}

void TuiApp::handle_line(std::string line) {
    const presentation::Command command = presentation::parse_command(line);
    if (command.kind == presentation::CommandKind::Text) {
        if (selected_.empty()) {
            append_local(presentation::LineKind::Error,
                         "Select a contact first (/contacts or /connect).");
            return;
        }
        const std::string peer = selected_;
        const std::string text = command.rest;
        post_core([this, peer, text]() -> asio::awaitable<void> {
            co_await service_->send_text(peer, text);
        });
        return;
    }
    handle_command(command);
}

void TuiApp::handle_command(const presentation::Command& command) {
    using Kind = presentation::CommandKind;
    switch (command.kind) {
        case Kind::Unknown:
            append_local(presentation::LineKind::Error,
                         "Unknown command /" + command.name + ". Try /help.");
            return;
        case Kind::Help:
            screen_kind_ = Screen::Help;
            return;
        case Kind::Quit:
            post_core([this]() -> asio::awaitable<void> {
                if (service_) {
                    co_await service_->stop();
                }
                post_ui([this] { screen_.Exit(); });
                co_return;
            });
            return;
        case Kind::Status:
            screen_kind_ = Screen::Diagnostics;
            return;
        case Kind::Connect: {
            if (!command.has_arg(0)) {
                append_local(presentation::LineKind::Error, "Usage: /connect <address>");
                return;
            }
            const std::string peer = resolve_peer(command.arg(0));
            post_core([this, peer]() -> asio::awaitable<void> {
                const bool ok = co_await service_->connect_peer(peer);
                post_ui([this, peer, ok] {
                    if (ok) {
                        select_peer(peer);
                    }
                });
            });
            return;
        }
        case Kind::Disconnect: {
            const std::string peer =
                command.has_arg(0) ? resolve_peer(command.arg(0)) : selected_;
            if (peer.empty()) {
                append_local(presentation::LineKind::Error, "No peer selected.");
                return;
            }
            service_->disconnect_peer(peer);
            return;
        }
        case Kind::Reply: {
            if (command.args.size() < 2) {
                append_local(presentation::LineKind::Error,
                             "Usage: /reply <address> <text>");
                return;
            }
            const std::string peer = resolve_peer(command.arg(0));
            const std::string text = command.rest.substr(command.arg(0).size());
            const auto begin = text.find_first_not_of(" \t");
            const std::string body =
                begin == std::string::npos ? std::string{} : text.substr(begin);
            post_core([this, peer, body]() -> asio::awaitable<void> {
                co_await service_->send_text(peer, body);
            });
            return;
        }
        case Kind::Contacts:
            screen_kind_ = Screen::Contacts;
            refresh_rows();
            return;
        case Kind::ContactAdd: {
            if (!command.has_arg(0)) {
                append_local(presentation::LineKind::Error,
                             "Usage: /contact-add <address> [name] [note]");
                return;
            }
            const std::string addr = resolve_peer(command.arg(0));
            const std::string name = command.arg(1);
            const std::string note = command.arg(2);
            service_->contacts().remember_peer(addr);
            service_->contacts().set_peer_profile(addr, name, note);
            service_->save_contacts();
            refresh_rows();
            append_local(presentation::LineKind::System, "Remembered " + addr);
            return;
        }
        case Kind::ContactUse: {
            if (!command.has_arg(0)) {
                append_local(presentation::LineKind::Error,
                             "Usage: /contact-use <index|address>");
                return;
            }
            select_peer(resolve_peer(command.arg(0)));
            screen_kind_ = Screen::Chat;
            return;
        }
        case Kind::ContactEdit: {
            if (command.args.size() < 2) {
                append_local(presentation::LineKind::Error,
                             "Usage: /contact-edit <index|address> <name> [note]");
                return;
            }
            const std::string addr = resolve_peer(command.arg(0));
            service_->contacts().set_peer_profile(addr, command.arg(1), command.arg(2));
            service_->save_contacts();
            refresh_rows();
            return;
        }
        case Kind::ContactRemove: {
            if (!command.has_arg(0)) {
                append_local(presentation::LineKind::Error,
                             "Usage: /contact-remove <index|address>");
                return;
            }
            const std::string addr = resolve_peer(command.arg(0));
            service_->contacts().remove_peer(addr);
            service_->save_contacts();
            if (selected_ == addr) {
                selected_.clear();
                lines_.clear();
            }
            refresh_rows();
            return;
        }
        case Kind::ContactInfo: {
            const std::string addr =
                command.has_arg(0) ? resolve_peer(command.arg(0)) : selected_;
            const storage::ContactRecord* record =
                service_ ? service_->contacts().get(addr) : nullptr;
            if (record == nullptr) {
                append_local(presentation::LineKind::Error, "No such contact.");
                return;
            }
            append_local(presentation::LineKind::System,
                         record->addr + "  name=" + record->display_name +
                             "  note=" + record->note);
            return;
        }
        case Kind::Recent:
            screen_kind_ = Screen::Contacts;
            refresh_rows();
            return;
        case Kind::SendFile:
        case Kind::SendPicture: {
            if (selected_.empty() || !command.has_arg(0)) {
                append_local(presentation::LineKind::Error,
                             "Usage: /sendfile|/sendpic <path> (with a peer selected)");
                return;
            }
            const std::string peer = selected_;
            const std::filesystem::path path(command.arg(0));
            const bool image = command.kind == Kind::SendPicture;
            post_core([this, peer, path, image]() -> asio::awaitable<void> {
                if (image) {
                    co_await service_->send_image(peer, path);
                } else {
                    co_await service_->send_file(peer, path);
                }
            });
            return;
        }
        case Kind::Transfers:
            screen_kind_ = Screen::Diagnostics;
            return;
        case Kind::History: {
            if (selected_.empty()) {
                append_local(presentation::LineKind::Error, "No peer selected.");
                return;
            }
            select_peer(selected_);
            screen_kind_ = Screen::Chat;
            return;
        }
        case Kind::HistoryClear: {
            const std::string addr =
                command.has_arg(0) ? resolve_peer(command.arg(0)) : selected_;
            if (addr.empty()) {
                append_local(presentation::LineKind::Error, "No peer selected.");
                return;
            }
            storage::delete_history(service_->paths(), addr);
            if (addr == selected_) {
                lines_.clear();
            }
            append_local(presentation::LineKind::System, "History cleared.");
            return;
        }
        case Kind::HistoryRetention:
            append_local(presentation::LineKind::System,
                         "Retention is applied on save. Current default: 1000 messages.");
            return;
        case Kind::BlindBox:
            append_local(presentation::LineKind::System,
                         service_ && service_->blindbox_ready()
                             ? "Offline delivery is configured."
                             : "No BlindBox replicas configured.");
            return;
        case Kind::BlindBoxPoll:
            post_core([this]() -> asio::awaitable<void> {
                const std::size_t count = co_await service_->poll_blindbox();
                post_ui([this, count] {
                    append_local(presentation::LineKind::System,
                                 "Collected " + std::to_string(count) + " offline message(s).");
                });
            });
            return;
        case Kind::TrustInfo: {
            const std::string addr =
                command.has_arg(0) ? resolve_peer(command.arg(0)) : selected_;
            const auto pin = service_->trust().pin_for(addr);
            if (!pin) {
                append_local(presentation::LineKind::System, "No pin for " + addr);
                return;
            }
            append_local(presentation::LineKind::System,
                         addr + "  " + presentation::group_fingerprint(pin->signing_key_hex) +
                             (pin->oob_verified ? "  oob-verified" : ""));
            return;
        }
        case Kind::ForgetPin: {
            if (!command.has_arg(0)) {
                append_local(presentation::LineKind::Error, "Usage: /forget-pin <address>");
                return;
            }
            const std::string addr = resolve_peer(command.arg(0));
            if (service_->trust().forget(addr)) {
                append_local(presentation::LineKind::System, "Pin forgotten.");
            } else {
                append_local(presentation::LineKind::Error, "No pin for that peer.");
            }
            return;
        }
        case Kind::CopyAddress:
            append_local(presentation::LineKind::System,
                         local_addr_.empty() ? "(address not yet known)" : local_addr_);
            return;
        case Kind::AppDir:
            append_local(presentation::LineKind::System, options_.app_root.string());
            return;
        case Kind::Router:
            screen_kind_ = Screen::Router;
            return;
        case Kind::Diagnostics:
            screen_kind_ = Screen::Diagnostics;
            return;
        case Kind::Settings:
            screen_kind_ = Screen::Settings;
            return;
        case Kind::Profiles: {
            const std::filesystem::path root = options_.app_root / "profiles";
            std::string listed = "Profiles in " + root.string() + ":";
            if (std::filesystem::exists(root)) {
                for (const auto& entry : std::filesystem::directory_iterator(root)) {
                    if (entry.is_directory()) {
                        listed += "\n  " + entry.path().filename().string();
                    }
                }
            }
            append_local(presentation::LineKind::System, listed);
            screen_kind_ = Screen::Settings;
            return;
        }
        case Kind::Group:
            screen_kind_ = Screen::Groups;
            return;
        case Kind::Text:
            break;
    }
}

void TuiApp::on_submit() {
    std::string line = draft_;
    draft_.clear();
    if (line.empty()) {
        return;
    }
    handle_line(std::move(line));
}

Element TuiApp::render_chat() const {
    Elements body;
    body.reserve(lines_.size());
    for (const presentation::ChatLine& line : lines_) {
        Elements parts;
        if (!line.time.empty()) {
            parts.push_back(text(line.time) | dim);
        }
        if (!line.author.empty()) {
            parts.push_back(text(" " + line.author) | bold | color(color_for(line.kind)));
        }
        if (!line.marker.empty()) {
            parts.push_back(text(" " + line.marker));
        }
        parts.push_back(text(" " + line.text) | color(color_for(line.kind)));
        if (!line.detail.empty()) {
            parts.push_back(text("  (" + line.detail + ")") | dim);
        }
        body.push_back(hbox(std::move(parts)));
    }
    if (body.empty()) {
        body.push_back(text("No messages yet. /connect <address> or /contacts.") | dim);
    }
    return vbox(std::move(body)) | yframe | yflex;
}

Element TuiApp::render_info_screen() const {
    Elements rows;
    auto add_heading = [&](std::string_view title) {
        rows.push_back(text(std::string(title)) | bold | color(Color::Cyan));
    };
    switch (screen_kind_) {
        case Screen::Contacts:
            add_heading("Contacts  (Enter /contact-use <n>)");
            for (const presentation::ContactRow& row : contacts_) {
                std::string label = std::to_string(row.index) + ". " + row.label;
                if (row.live) {
                    label += "  live";
                }
                if (row.unread > 0) {
                    label += "  [" + std::to_string(row.unread) + "]";
                }
                if (!row.preview.empty()) {
                    label += "  — " + row.preview;
                }
                auto line = text(label);
                if (row.selected) {
                    line |= inverted;
                }
                rows.push_back(line);
            }
            if (contacts_.empty()) {
                rows.push_back(text("(empty)") | dim);
            }
            break;
        case Screen::Groups: {
            add_heading("Groups");
            if (service_) {
                const auto states = groups::list_states(
                    service_->paths(), ByteView(service_->identity().identity_key),
                    ByteView(service_->identity().signing_seed));
                if (states.empty()) {
                    rows.push_back(
                        text("No groups yet. Invites are accepted from a live peer.") | dim);
                }
                for (const groups::GroupState& state : states) {
                    rows.push_back(text((state.title().empty() ? state.group_id()
                                                               : state.title()) +
                                        "  members=" +
                                        std::to_string(state.members().size())));
                }
            }
            rows.push_back(separator());
            rows.push_back(text("/group is the entry point; membership changes travel "
                                "as signed control envelopes.") |
                           dim);
            break;
        }
        case Screen::Router:
            add_heading("Router");
            for (const presentation::InfoRow& row : presentation::status_rows(status_input())) {
                rows.push_back(hbox({text(row.name + ": ") | dim, text(row.value)}));
            }
            if (router_) {
                rows.push_back(text(std::string("bundled i2pd: ") +
                                    (router_->is_running() ? "running" : "stopped") +
                                    (router_->adopted() ? " (adopted)" : "")));
            } else {
                rows.push_back(text("Using an external SAM router at " + options_.sam_host +
                                    ":" + std::to_string(options_.sam_port)));
            }
            break;
        case Screen::Backups:
            add_heading("Backups");
            rows.push_back(text("Profile data: " + options_.app_root.string()));
            rows.push_back(text("Export/import of I2CP/I2HX archives is available from "
                                "the Qt client; the TUI points at the same files.") |
                           dim);
            break;
        case Screen::Diagnostics: {
            add_heading("Diagnostics");
            for (const presentation::InfoRow& row : presentation::status_rows(status_input())) {
                rows.push_back(hbox({text(row.name + ": ") | dim, text(row.value)}));
            }
            rows.push_back(separator());
            add_heading("Transfers");
            if (transfers_.empty()) {
                rows.push_back(text("(none)") | dim);
            }
            for (const transfer::Progress& progress : transfers_) {
                rows.push_back(text(presentation::transfer_row(selected_, progress)));
            }
            break;
        }
        case Screen::Settings:
            add_heading("Settings");
            rows.push_back(text("profile: " + options_.profile));
            rows.push_back(text("app-root: " + options_.app_root.string()));
            rows.push_back(text("SAM: " + options_.sam_host + ":" +
                                std::to_string(options_.sam_port)));
            rows.push_back(text(std::string("replicas: ") +
                                (options_.blindbox_over_sam ? "over I2P" : "direct TCP")));
            break;
        case Screen::Help:
            add_heading("Help");
            rows.push_back(paragraph(help_text()));
            break;
        case Screen::Chat:
            break;
    }
    return vbox(std::move(rows)) | yframe | yflex;
}

Element TuiApp::render_trust_modal() const {
    Elements body = {
        text(trust_view_.title) | bold |
            color(trust_view_.dangerous ? Color::RedLight : Color::Yellow),
        separator(),
        paragraph(trust_view_.body),
        text("New key: " + trust_view_.fingerprint) | color(Color::Cyan),
    };
    if (!trust_view_.previous_fingerprint.empty()) {
        body.push_back(text("Pinned: " + trust_view_.previous_fingerprint) | dim);
    }
    body.push_back(separator());
    body.push_back(text("y accept   n reject") | bold);
    return vbox(std::move(body)) | border | size(WIDTH, LESS_THAN, 80) | center;
}

int TuiApp::run() {
    start_core();

    InputOption input_opt = InputOption::Default();
    input_opt.multiline = false;
    input_opt.placeholder = "message or /command";
    input_opt.on_enter = [this] { on_submit(); };
    Component input = Input(&draft_, input_opt);

    Component layout = Renderer(input, [this, input] {
        std::lock_guard lock(mutex_);
        const std::string header = presentation::status_line(status_input());
        Element main = screen_kind_ == Screen::Chat ? render_chat() : render_info_screen();
        Element page = vbox({
            text("I2PChat  " + header) | bold | inverted,
            separator(),
            main,
            separator(),
            hbox({text("> ") | bold, input->Render()}),
            text("F1 chat  F2 contacts  F3 groups  F4 router  F5 backups  "
                 "F6 diagnostics  F7 settings  F8 help  /quit") |
                dim,
        });
        if (trust_open_) {
            page = dbox({page, render_trust_modal() | clear_under});
        }
        return page;
    });

    layout |= CatchEvent([this](const Event& event) {
        if (trust_open_) {
            if (event == Event::y || event == Event::Y || event == Event::Return) {
                std::lock_guard lock(trust_mutex_);
                trust_decision_ = session::TrustDecision::Accept;
                trust_cv_.notify_all();
                return true;
            }
            if (event == Event::n || event == Event::N || event == Event::Escape) {
                std::lock_guard lock(trust_mutex_);
                trust_decision_ = session::TrustDecision::Reject;
                trust_cv_.notify_all();
                return true;
            }
            return true;
        }
        if (event == Event::F1) {
            screen_kind_ = Screen::Chat;
            return true;
        }
        if (event == Event::F2) {
            screen_kind_ = Screen::Contacts;
            refresh_rows();
            return true;
        }
        if (event == Event::F3) {
            screen_kind_ = Screen::Groups;
            return true;
        }
        if (event == Event::F4) {
            screen_kind_ = Screen::Router;
            return true;
        }
        if (event == Event::F5) {
            screen_kind_ = Screen::Backups;
            return true;
        }
        if (event == Event::F6) {
            screen_kind_ = Screen::Diagnostics;
            return true;
        }
        if (event == Event::F7) {
            screen_kind_ = Screen::Settings;
            return true;
        }
        if (event == Event::F8) {
            screen_kind_ = Screen::Help;
            return true;
        }
        if (event == Event::Tab) {
            const presentation::Command command = presentation::parse_command(draft_);
            if (draft_.empty() || draft_.front() != '/') {
                return false;
            }
            const std::string prefix = command.name.empty() && draft_ == "/"
                                           ? std::string{}
                                           : (command.name.empty() ? draft_.substr(1)
                                                                   : command.name);
            const std::vector<std::string> matches =
                presentation::complete_command(prefix);
            if (matches.size() == 1) {
                draft_ = "/" + matches.front() + " ";
                return true;
            }
            if (!matches.empty()) {
                append_local(presentation::LineKind::System,
                             "Matches: " + [&] {
                                 std::string joined;
                                 for (const std::string& name : matches) {
                                     if (!joined.empty()) {
                                         joined += " ";
                                     }
                                     joined += "/" + name;
                                 }
                                 return joined;
                             }());
                return true;
            }
            return true;
        }
        if (event == Event::Escape && screen_kind_ != Screen::Chat) {
            screen_kind_ = Screen::Chat;
            return true;
        }
        return false;
    });

    screen_.Loop(layout);
    stop_core();
    return 0;
}

}  // namespace i2pchat::tui
