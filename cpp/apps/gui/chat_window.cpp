#include "chat_window.hpp"

#include <QAction>
#include <QApplication>
#include <QClipboard>
#include <QCloseEvent>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFile>
#include <QFileDialog>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QLabel>
#include <QLineEdit>
#include <QListView>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QMimeData>
#include <QPushButton>
#include <QSplitter>
#include <QStatusBar>
#include <QToolBar>
#include <QUrl>
#include <QVBoxLayout>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/executor_work_guard.hpp>

#include "i2pchat/presentation/chat_view.hpp"
#include "i2pchat/sam/destination.hpp"
#include "tofu_dialog.hpp"

namespace i2pchat::gui {
namespace asio = boost::asio;

ChatWindow::ChatWindow(GuiOptions options, QWidget* parent)
    : QMainWindow(parent), options_(std::move(options)) {
    setWindowTitle(QString("I2PChat — %1").arg(QString::fromStdString(options_.profile)));
    setAcceptDrops(true);
    resize(980, 640);

    chat_ = new ChatModel(this);
    delegate_ = new ChatItemDelegate(this);
    contacts_model_ = new QStringListModel(this);

    contact_view_ = new QListView(this);
    contact_view_->setObjectName("ContactList");
    contact_view_->setModel(contacts_model_);
    contact_view_->setMinimumWidth(220);
    connect(contact_view_, &QListView::clicked, this, &ChatWindow::contact_activated);

    chat_view_ = new QListView(this);
    chat_view_->setObjectName("ChatView");
    chat_view_->setModel(chat_);
    chat_view_->setItemDelegate(delegate_);
    chat_view_->setUniformItemSizes(false);
    chat_view_->setWordWrap(true);
    chat_view_->setSelectionMode(QAbstractItemView::NoSelection);

    composer_ = new QLineEdit(this);
    composer_->setObjectName("Composer");
    composer_->setPlaceholderText(tr("Message or /command…"));
    connect(composer_, &QLineEdit::returnPressed, this, &ChatWindow::send_current);

    auto* send = new QPushButton(tr("Send"), this);
    send->setObjectName("PrimaryButton");
    connect(send, &QPushButton::clicked, this, &ChatWindow::send_current);

    auto* compose_row = new QHBoxLayout();
    compose_row->addWidget(composer_, 1);
    compose_row->addWidget(send);

    auto* right = new QWidget(this);
    auto* right_layout = new QVBoxLayout(right);
    right_layout->setContentsMargins(0, 0, 0, 0);
    right_layout->addWidget(chat_view_, 1);
    right_layout->addLayout(compose_row);

    auto* splitter = new QSplitter(this);
    splitter->addWidget(contact_view_);
    splitter->addWidget(right);
    splitter->setStretchFactor(1, 1);
    setCentralWidget(splitter);

    status_ = new QLabel(this);
    status_->setObjectName("StatusBar");
    statusBar()->addPermanentWidget(status_, 1);

    auto* connect_action = menuBar()->addMenu(tr("Peer"))->addAction(tr("Connect…"));
    connect(connect_action, &QAction::triggered, this, &ChatWindow::connect_prompt);
    auto* file_menu = menuBar()->addMenu(tr("File"));
    connect(file_menu->addAction(tr("Send file…")), &QAction::triggered, this, [this] {
        if (selected_.empty()) {
            return;
        }
        const QString path = QFileDialog::getOpenFileName(this, tr("Send file"));
        if (path.isEmpty()) {
            return;
        }
        const std::string peer = selected_;
        post_core([this, peer, path]() -> asio::awaitable<void> {
            co_await service_->send_file(peer, path.toStdString());
        });
    });
    connect(file_menu->addAction(tr("Send image…")), &QAction::triggered, this, [this] {
        if (selected_.empty()) {
            return;
        }
        const QString path = QFileDialog::getOpenFileName(this, tr("Send image"));
        if (path.isEmpty()) {
            return;
        }
        const std::string peer = selected_;
        post_core([this, peer, path]() -> asio::awaitable<void> {
            co_await service_->send_image(peer, path.toStdString());
        });
    });
    auto* view = menuBar()->addMenu(tr("View"));
    connect(view->addAction(tr("Toggle theme")), &QAction::triggered, this,
            &ChatWindow::toggle_theme);
    auto* offline = menuBar()->addMenu(tr("Offline"));
    connect(offline->addAction(tr("Collect now")), &QAction::triggered, this,
            &ChatWindow::poll_offline);
    auto* help = menuBar()->addMenu(tr("Help"));
    connect(help->addAction(tr("Copy my address")), &QAction::triggered, this,
            &ChatWindow::copy_address);

    if (QSystemTrayIcon::isSystemTrayAvailable()) {
        tray_ = new QSystemTrayIcon(this);
        tray_->setToolTip("I2PChat");
        auto* tray_menu = new QMenu(this);
        tray_menu->addAction(tr("Show"), this, &QWidget::showNormal);
        tray_menu->addAction(tr("Quit"), qApp, &QApplication::quit);
        tray_->setContextMenu(tray_menu);
        connect(tray_, &QSystemTrayIcon::activated, this, &ChatWindow::tray_activated);
        tray_->show();
    }

    apply_theme();
    start_core();
}

ChatWindow::~ChatWindow() { stop_core(); }

void ChatWindow::apply_theme() {
    const QString path = options_.dark ? ":/i2pchat/qss/dark.qss" : ":/i2pchat/qss/light.qss";
    QFile file(path);
    if (file.open(QIODevice::ReadOnly)) {
        qApp->setStyleSheet(QString::fromUtf8(file.readAll()));
    }
    delegate_->set_dark(options_.dark);
    chat_view_->viewport()->update();
}

void ChatWindow::toggle_theme() {
    options_.dark = !options_.dark;
    apply_theme();
}

void ChatWindow::post_core(std::function<asio::awaitable<void>()> work) {
    asio::co_spawn(core_, std::move(work), asio::detached);
}

void ChatWindow::start_core() {
    runtime::ChatServiceConfig config;
    config.app_root = options_.app_root;
    config.profile = options_.profile;
    config.sam.host = options_.sam_host;
    config.sam.port = options_.sam_port;

    runtime::ChatEvents events;
    events.on_system = [this](const std::string& message) {
        QMetaObject::invokeMethod(this, [this, message] {
            presentation::ChatLine line;
            line.kind = presentation::LineKind::System;
            line.text = message;
            chat_->append(std::move(line));
            status_->setText(QString::fromStdString(message));
        });
    };
    events.on_error = [this](const std::string& message) {
        QMetaObject::invokeMethod(this, [this, message] {
            presentation::ChatLine line;
            line.kind = presentation::LineKind::Error;
            line.text = message;
            chat_->append(std::move(line));
        });
    };
    events.on_history = [this](const std::string& peer, const storage::HistoryEntry& entry) {
        QMetaObject::invokeMethod(this, [this, peer, entry] {
            if (peer == selected_) {
                chat_->append(presentation::line_from_history(
                    entry, service_ ? service_->contacts() : storage::ContactBook{}, peer));
                chat_view_->scrollToBottom();
            }
            refresh_contacts();
        });
    };
    events.on_local_address = [this](const std::string& addr) {
        QMetaObject::invokeMethod(this, [this, addr] {
            status_->setText(tr("Listening as %1").arg(QString::fromStdString(addr)));
            setWindowTitle(QString("I2PChat — %1").arg(QString::fromStdString(addr.substr(0, 8))));
        });
    };
    events.on_trust_prompt = [this](session::TrustPrompt prompt, const std::string& peer,
                                    const std::string& neu, const std::string& old) {
        return on_trust(prompt, peer, neu, old);
    };
    events.on_file_received = [this](const std::string&, const std::filesystem::path& path) {
        QMetaObject::invokeMethod(this, [this, path] {
            if (tray_) {
                tray_->showMessage(tr("File received"), QString::fromStdString(path.string()));
            }
        });
    };

    service_ = std::make_unique<runtime::ChatService>(core_.get_executor(), std::move(config),
                                                      std::move(events));
    running_ = true;
    post_core([this]() -> asio::awaitable<void> {
        try {
            co_await service_->start();
            QMetaObject::invokeMethod(this, [this] { refresh_contacts(); });
        } catch (const std::exception& error) {
            QMetaObject::invokeMethod(this, [this, message = std::string(error.what())] {
                QMessageBox::critical(this, tr("Startup failed"),
                                      QString::fromStdString(message));
            });
        }
    });
    core_thread_ = std::thread([this] {
        auto work = asio::make_work_guard(core_);
        core_.run();
    });
}

void ChatWindow::stop_core() {
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
}

session::TrustDecision ChatWindow::on_trust(session::TrustPrompt prompt,
                                            const std::string& peer, const std::string& neu,
                                            const std::string& old) {
    {
        std::lock_guard lock(trust_mutex_);
        trust_decision_.reset();
    }
    const presentation::TrustPromptView view =
        presentation::trust_prompt_view(prompt, peer, neu, old);
    QMetaObject::invokeMethod(
        this,
        [this, view] {
            TofuDialog dialog(view, this);
            dialog.exec();
            std::lock_guard lock(trust_mutex_);
            trust_decision_ = dialog.decision();
            trust_cv_.notify_all();
        },
        Qt::QueuedConnection);
    std::unique_lock lock(trust_mutex_);
    trust_cv_.wait(lock, [this] { return trust_decision_.has_value(); });
    return *trust_decision_;
}

void ChatWindow::refresh_contacts() {
    if (!service_) {
        return;
    }
    const auto rows = presentation::contact_rows(service_->contacts(),
                                                 service_->connected_peers(), selected_);
    QStringList labels;
    contact_addrs_.clear();
    for (const auto& row : rows) {
        QString label = QString::fromStdString(row.label);
        if (row.live) {
            label += " ●";
        }
        labels << label;
        contact_addrs_.push_back(row.addr);
    }
    contacts_model_->setStringList(labels);
}

void ChatWindow::reload_selected() {
    if (!service_ || selected_.empty()) {
        chat_->clear();
        return;
    }
    chat_->set_lines(presentation::lines_from_history(service_->history(selected_),
                                                      service_->contacts(), selected_));
    chat_view_->scrollToBottom();
}

void ChatWindow::contact_activated(const QModelIndex& index) {
    if (!index.isValid() || index.row() >= static_cast<int>(contact_addrs_.size())) {
        return;
    }
    selected_ = contact_addrs_[static_cast<std::size_t>(index.row())];
    reload_selected();
}

void ChatWindow::send_current() {
    const QString text = composer_->text().trimmed();
    if (text.isEmpty()) {
        return;
    }
    composer_->clear();
    if (text.startsWith('/')) {
        if (text == "/copyaddr") {
            copy_address();
            return;
        }
        if (text == "/quit") {
            close();
            return;
        }
        if (text.startsWith("/connect ")) {
            const std::string peer = text.mid(9).toStdString();
            post_core([this, peer]() -> asio::awaitable<void> {
                const bool ok = co_await service_->connect_peer(peer);
                if (ok) {
                    QMetaObject::invokeMethod(this, [this, peer] {
                        selected_ = std::string(sam::normalize_peer_address(peer));
                        refresh_contacts();
                        reload_selected();
                    });
                }
            });
            return;
        }
    }
    if (selected_.empty()) {
        status_->setText(tr("Select a contact first."));
        return;
    }
    const std::string peer = selected_;
    post_core([this, peer, body = text.toStdString()]() -> asio::awaitable<void> {
        co_await service_->send_text(peer, body);
    });
}

void ChatWindow::connect_prompt() {
    bool ok = false;
    const QString addr = QInputDialog::getText(this, tr("Connect"),
                                               tr("Peer b32 address:"), QLineEdit::Normal,
                                               {}, &ok);
    if (!ok || addr.isEmpty()) {
        return;
    }
    const std::string peer = addr.toStdString();
    post_core([this, peer]() -> asio::awaitable<void> {
        const bool ok = co_await service_->connect_peer(peer);
        if (ok) {
            QMetaObject::invokeMethod(this, [this, peer] {
                selected_ = std::string(sam::normalize_peer_address(peer));
                refresh_contacts();
                reload_selected();
            });
        }
    });
}

void ChatWindow::poll_offline() {
    post_core([this]() -> asio::awaitable<void> {
        const std::size_t count = co_await service_->poll_blindbox();
        QMetaObject::invokeMethod(this, [this, count] {
            status_->setText(tr("Collected %1 offline message(s).").arg(count));
        });
    });
}

void ChatWindow::copy_address() {
    if (!service_) {
        return;
    }
    qApp->clipboard()->setText(QString::fromStdString(service_->local_addr()));
    status_->setText(tr("Address copied."));
}

void ChatWindow::tray_activated(QSystemTrayIcon::ActivationReason reason) {
    if (reason == QSystemTrayIcon::Trigger) {
        showNormal();
        raise();
        activateWindow();
    }
}

void ChatWindow::dragEnterEvent(QDragEnterEvent* event) {
    if (event->mimeData()->hasUrls()) {
        event->acceptProposedAction();
    }
}

void ChatWindow::dropEvent(QDropEvent* event) {
    if (selected_.empty()) {
        return;
    }
    for (const QUrl& url : event->mimeData()->urls()) {
        const QString path = url.toLocalFile();
        if (path.isEmpty()) {
            continue;
        }
        const std::string peer = selected_;
        post_core([this, peer, path]() -> asio::awaitable<void> {
            const bool image = path.endsWith(".png", Qt::CaseInsensitive) ||
                               path.endsWith(".jpg", Qt::CaseInsensitive) ||
                               path.endsWith(".jpeg", Qt::CaseInsensitive) ||
                               path.endsWith(".webp", Qt::CaseInsensitive) ||
                               path.endsWith(".gif", Qt::CaseInsensitive);
            if (image) {
                co_await service_->send_image(peer, path.toStdString());
            } else {
                co_await service_->send_file(peer, path.toStdString());
            }
        });
    }
}

void ChatWindow::closeEvent(QCloseEvent* event) {
    if (tray_ && tray_->isVisible()) {
        hide();
        event->ignore();
        return;
    }
    event->accept();
}

}  // namespace i2pchat::gui
