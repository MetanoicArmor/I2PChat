#pragma once

#include <QMainWindow>
#include <QStringListModel>
#include <QSystemTrayIcon>
#include <boost/asio/io_context.hpp>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>

#include "chat_item_delegate.hpp"
#include "chat_model.hpp"
#include "i2pchat/runtime/chat_service.hpp"
#include "i2pchat/session/trust_store.hpp"

class QLineEdit;
class QListView;
class QLabel;

namespace i2pchat::gui {

struct GuiOptions {
    std::filesystem::path app_root;
    std::string profile = "default";
    std::string sam_host = "127.0.0.1";
    std::uint16_t sam_port = 7656;
    bool dark = false;
};

class ChatWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit ChatWindow(GuiOptions options, QWidget* parent = nullptr);
    ~ChatWindow() override;

protected:
    void dragEnterEvent(QDragEnterEvent* event) override;
    void dropEvent(QDropEvent* event) override;
    void closeEvent(QCloseEvent* event) override;

private slots:
    void send_current();
    void contact_activated(const QModelIndex& index);
    void connect_prompt();
    void poll_offline();
    void copy_address();
    void toggle_theme();
    void tray_activated(QSystemTrayIcon::ActivationReason reason);

private:
    void start_core();
    void stop_core();
    void post_core(std::function<boost::asio::awaitable<void>()> work);
    void reload_selected();
    void refresh_contacts();
    void apply_theme();
    session::TrustDecision on_trust(session::TrustPrompt prompt, const std::string& peer,
                                    const std::string& neu, const std::string& old);

    GuiOptions options_;
    ChatModel* chat_ = nullptr;
    ChatItemDelegate* delegate_ = nullptr;
    QStringListModel* contacts_model_ = nullptr;
    QListView* chat_view_ = nullptr;
    QListView* contact_view_ = nullptr;
    QLineEdit* composer_ = nullptr;
    QLabel* status_ = nullptr;
    QSystemTrayIcon* tray_ = nullptr;

    std::string selected_;
    std::vector<std::string> contact_addrs_;

    boost::asio::io_context core_;
    std::unique_ptr<runtime::ChatService> service_;
    std::thread core_thread_;
    bool running_ = false;

    std::mutex trust_mutex_;
    std::condition_variable trust_cv_;
    std::optional<session::TrustDecision> trust_decision_;
};

}  // namespace i2pchat::gui
