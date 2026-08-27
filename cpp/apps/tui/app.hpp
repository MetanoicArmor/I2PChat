#pragma once

#include <boost/asio/io_context.hpp>
#include <condition_variable>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "i2pchat/router/i2pd.hpp"

#include <ftxui/component/component.hpp>
#include <ftxui/component/screen_interactive.hpp>

#include "i2pchat/presentation/chat_view.hpp"
#include "i2pchat/presentation/commands.hpp"
#include "i2pchat/runtime/chat_service.hpp"
#include "i2pchat/session/trust_store.hpp"
#include "options.hpp"

namespace i2pchat::tui {

/// The screens `/contacts`, `/group`, `/router`, `/backups`, `/diagnostics`
/// and `/settings` switch to. Chat is the default.
enum class Screen {
    Chat,
    Contacts,
    Groups,
    Router,
    Backups,
    Diagnostics,
    Settings,
    Help,
};

/// Terminal client: FTXUI on the main thread, ChatService on a private
/// `io_context`. Events cross the boundary through `App::Post`.
class TuiApp {
public:
    explicit TuiApp(Options options);
    ~TuiApp();

    TuiApp(const TuiApp&) = delete;
    TuiApp& operator=(const TuiApp&) = delete;

    int run();

private:
    void start_core();
    void stop_core();
    void post_ui(std::function<void()> work);
    void post_core(std::function<boost::asio::awaitable<void>()> work);

    void on_submit();
    void handle_line(std::string line);
    void handle_command(const presentation::Command& command);
    void select_peer(std::string addr);
    [[nodiscard]] std::string resolve_peer(std::string_view token) const;

    void append_local(presentation::LineKind kind, std::string text);
    void refresh_rows();
    [[nodiscard]] presentation::StatusInput status_input() const;
    [[nodiscard]] ftxui::Element render_chat() const;
    [[nodiscard]] ftxui::Element render_info_screen() const;
    [[nodiscard]] ftxui::Element render_trust_modal() const;

    session::TrustDecision on_trust_prompt(session::TrustPrompt prompt,
                                           const std::string& peer_addr,
                                           const std::string& new_key_hex,
                                           const std::string& old_key_hex);

    Options options_;
    ftxui::ScreenInteractive screen_ = ftxui::ScreenInteractive::Fullscreen();
    boost::asio::io_context core_;
    std::unique_ptr<runtime::ChatService> service_;
    std::unique_ptr<router::I2pdManager> router_;
    std::thread core_thread_;

    mutable std::mutex mutex_;
    Screen screen_kind_ = Screen::Chat;
    std::string selected_;
    std::string draft_;
    std::string status_reason_;
    session::TransportState transport_ = session::TransportState::Stopped;
    std::vector<presentation::ChatLine> lines_;
    std::vector<presentation::ContactRow> contacts_;
    std::vector<transfer::Progress> transfers_;
    std::map<std::string, unsigned> unread_;
    std::string local_addr_;
    bool running_ = false;

    bool trust_open_ = false;
    presentation::TrustPromptView trust_view_;
    std::mutex trust_mutex_;
    std::condition_variable trust_cv_;
    std::optional<session::TrustDecision> trust_decision_;
};

}  // namespace i2pchat::tui
