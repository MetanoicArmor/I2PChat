#pragma once

#include <QMainWindow>
#include <QEvent>
#include <QVector>
#include <QSystemTrayIcon>
#include <boost/asio/io_context.hpp>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>
#include <vector>

#include "chat_item_delegate.hpp"
#include "chat_model.hpp"
#include "contact_item_delegate.hpp"
#include "contact_list_model.hpp"
#include "i2pchat/router/i2pd.hpp"
#include "i2pchat/runtime/chat_service.hpp"
#include "i2pchat/session/manager.hpp"
#include "i2pchat/session/trust_store.hpp"

class QNetworkAccessManager;
class QNetworkReply;
class QHBoxLayout;
class QLabel;
class QLineEdit;
class QListView;
class QPlainTextEdit;
class QPushButton;
class QSplitter;
class QToolButton;
class QVBoxLayout;
class QWidget;

namespace i2pchat::gui {

class ActionsPopup;

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
    void resizeEvent(QResizeEvent* event) override;

    bool eventFilter(QObject* watched, QEvent* event) override;

private slots:
    void send_current();
    void contact_activated(const QModelIndex& index);
    void connect_selected();
    void disconnect_selected();
    void poll_offline();
    void copy_address();
    void toggle_theme();
    void toggle_sidebar();
    void show_more_menu();
    void search_changed(const QString& text);
    void search_step(int delta);
    void choose_file(bool image);
    void forget_pin();
    void open_app_dir();
    void router_settings();
    void new_group_hint();
    void join_group_hint();
    void copy_group_invite();
    void sidebar_context_menu(const QPoint& pos);
    void load_profile_dat();
    void show_blindbox_diagnostics();
    void export_profile_backup();
    void import_profile_backup();
    void export_history_backup();
    void import_history_backup();
    void check_for_updates();
    void on_update_check_finished();
    void clear_history();
    void configure_history_retention();
    void tray_activated(QSystemTrayIcon::ActivationReason reason);

private:
    void build_ui();
    void start_core();
    void stop_core();
    void post_core(std::function<boost::asio::awaitable<void>()> work);
    void reload_selected();
    void refresh_contacts();
    void refresh_status();
    void refresh_connection_buttons();
    void apply_theme();
    void apply_empty_state();
    void highlight_search();
    void rebuild_search_console();
    void notify_incoming(const std::string& peer, const QString& preview);
    void sync_media_dirs();
    void sync_sidebar_toggle_margin();
    void edit_group_dialog(const std::string& existing_group_id);
    void edit_saved_peer(const std::string& addr);
    void show_contact_details(const std::string& addr);
    void remove_saved_peer(const std::string& addr);
    void confirm_delete_group(const std::string& group_id);
    void show_group_map(const std::string& group_id);
    void copy_group_invite_of(const std::string& group_id);
    void switch_to_profile(const std::string& name);
    void apply_router_settings_to_options();
    void ensure_bundled_router();
    QString bundled_router_status() const;
    session::TrustDecision on_trust(session::TrustPrompt prompt, const std::string& peer,
                                    const std::string& neu, const std::string& old);

    GuiOptions options_;
    ChatModel* chat_ = nullptr;
    ChatItemDelegate* chat_delegate_ = nullptr;
    ContactListModel* contacts_model_ = nullptr;
    ContactItemDelegate* contact_delegate_ = nullptr;

    QLabel* status_label_ = nullptr;
    QToolButton* theme_button_ = nullptr;
    QSplitter* splitter_ = nullptr;
    QWidget* sidebar_ = nullptr;
    QHBoxLayout* right_pack_layout_ = nullptr;
    QListView* contact_view_ = nullptr;
    QPushButton* sidebar_toggle_ = nullptr;
    QWidget* sidebar_grip_ = nullptr;
    QLineEdit* search_edit_ = nullptr;
    QLabel* search_status_ = nullptr;
    QWidget* search_console_ = nullptr;
    QVBoxLayout* search_hits_layout_ = nullptr;
    QListView* chat_view_ = nullptr;
    QLabel* empty_hint_ = nullptr;
    QPlainTextEdit* composer_ = nullptr;
    QPushButton* send_button_ = nullptr;
    QLineEdit* addr_edit_ = nullptr;
    QPushButton* connect_button_ = nullptr;
    QPushButton* disconnect_button_ = nullptr;
    QToolButton* more_button_ = nullptr;
    ActionsPopup* more_popup_ = nullptr;
    ActionsPopup* sidebar_popup_ = nullptr;
    QString status_full_;
    QString status_compact_;
    QSystemTrayIcon* tray_ = nullptr;
    QNetworkAccessManager* update_nam_ = nullptr;
    QNetworkReply* update_reply_ = nullptr;

    std::string selected_;
    std::string active_group_id_;
    QVector<int> search_hits_;
    int search_cur_ = -1;
    bool sidebar_collapsed_ = false;
    bool history_enabled_ = true;
    bool privacy_mode_ = false;
    bool enter_sends_ = true;
    bool notify_sound_ = true;
    session::TransportState transport_ = session::TransportState::Starting;
    std::string transport_reason_;

    boost::asio::io_context core_;
    std::unique_ptr<runtime::ChatService> service_;
    std::unique_ptr<router::I2pdManager> bundled_router_;
    std::thread core_thread_;
    bool running_ = false;

    std::mutex trust_mutex_;
    std::condition_variable trust_cv_;
    std::optional<session::TrustDecision> trust_decision_;
};

}  // namespace i2pchat::gui
