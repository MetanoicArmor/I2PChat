#include "actions_popup.hpp"
#include "chat_window.hpp"
#include "dialog_theme.hpp"
#include "emoji_picker.hpp"
#include "group_topology_map.hpp"
#include "router_settings_dialog.hpp"

#include <algorithm>
#include <fstream>
#include <functional>
#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QClipboard>
#include <QCoreApplication>
#include <QCloseEvent>
#include <QDesktopServices>
#include <QDir>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFontMetrics>
#include <QFormLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QCheckBox>
#include <QDialogButtonBox>
#include <QListWidget>
#include <QInputDialog>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QListView>
#include <QMenu>
#include <QMessageBox>
#include <QMimeData>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QGuiApplication>
#include <QImage>
#include <QStyleHints>
#include <QCursor>
#include <QDateTime>
#include <QElapsedTimer>
#include <QEvent>
#include <QKeyEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPlainTextEdit>
#include <QScrollArea>
#include <QPushButton>
#include <QProcess>
#include <QRegularExpression>
#include <QSettings>
#include <QSignalBlocker>
#include <QSpinBox>
#include <QShortcut>
#include <QSizePolicy>
#include <QSplitter>
#include <QStackedLayout>
#include <QTabWidget>
#include <QTcpSocket>
#include <QTextCursor>
#include <QThread>
#include <QTimer>
#include <QToolButton>
#include <QUrl>
#include <QVBoxLayout>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/executor_work_guard.hpp>

#include "i2pchat/bytes.hpp"
#include "i2pchat/blindbox/state.hpp"
#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/groups/invite.hpp"
#include "i2pchat/groups/store.hpp"
#include "i2pchat/groups/wire.hpp"
#include "i2pchat/presentation/chat_view.hpp"
#include "i2pchat/router/i2pd.hpp"
#include "i2pchat/runtime/identity.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/storage/atomic_write.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/compose_drafts.hpp"
#include "i2pchat/storage/profile_backup.hpp"
#include "i2pchat/storage/replica_settings.hpp"
#include "i2pchat/updates/release_index.hpp"
#include "tofu_dialog.hpp"

#include <nlohmann/json.hpp>
#include <optional>
#include <tuple>
#include <QAbstractButton>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>

namespace i2pchat::gui {
namespace asio = boost::asio;

namespace {

QString friendly_error(const std::string& raw) {
    const QString text = QString::fromStdString(raw);
    if (text.contains(QStringLiteral("Connection refused"), Qt::CaseInsensitive) ||
        text.contains(QStringLiteral("system:61"))) {
        return QObject::tr("I2P router is not reachable (SAM refused the connection). Start i2pd.");
    }
    const qsizetype cut = text.indexOf(QStringLiteral(" ["));
    QString short_text = cut > 0 ? text.left(cut) : text;
    if (short_text.size() > 220) {
        short_text = short_text.left(217) + QStringLiteral("…");
    }
    return short_text;
}

nlohmann::json load_ui_prefs(const std::filesystem::path& app_root) {
    std::ifstream stream(app_root / "ui_prefs.json");
    if (!stream) {
        return nlohmann::json::object();
    }
    try {
        nlohmann::json data;
        stream >> data;
        return data.is_object() ? data : nlohmann::json::object();
    } catch (...) {
        return nlohmann::json::object();
    }
}

void save_ui_prefs(const std::filesystem::path& app_root, nlohmann::json data) {
    storage::atomic_write_json(app_root / "ui_prefs.json", data);
}

storage::RetentionPolicy load_retention_policy(const std::filesystem::path& app_root) {
    storage::RetentionPolicy policy;
    const nlohmann::json data = load_ui_prefs(app_root);
    if (data.contains("history_max_messages") && data["history_max_messages"].is_number_integer()) {
        const int value = data["history_max_messages"].get<int>();
        if (value > 0) {
            policy.max_messages = static_cast<std::size_t>(value);
        }
    }
    if (data.contains("history_retention_days") && data["history_retention_days"].is_number_integer()) {
        const int value = data["history_retention_days"].get<int>();
        policy.max_age_days = static_cast<unsigned>(std::max(0, value));
    }
    return policy;
}

constexpr int kComposeDraftsMaxKeys = 100;
constexpr int kComposeDraftsDebounceMs = 1500;

std::tuple<std::optional<std::string>, std::string, std::map<std::string, std::string>>
apply_compose_draft_peer_switch(const std::optional<std::string>& old_key,
                                const std::optional<std::string>& new_key,
                                const std::string& input_plain,
                                std::map<std::string, std::string> drafts) {
    if (new_key == old_key) {
        return {old_key, input_plain, std::move(drafts)};
    }
    std::string orphan;
    if (!old_key && new_key) {
        orphan = input_plain;
    }
    if (old_key) {
        drafts[*old_key] = input_plain;
    }
    std::string text;
    if (!new_key) {
        text.clear();
    } else {
        const auto found = drafts.find(*new_key);
        text = found == drafts.end() ? std::string{} : found->second;
        if (text.find_first_not_of(" \t\r\n") == std::string::npos && !orphan.empty()) {
            text = orphan;
        }
    }
    return {new_key, text, std::move(drafts)};
}

bool valid_profile_name(const QString& name) {
    static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9._-]{1,64}$"));
    return re.match(name).hasMatch();
}

std::optional<QString> prompt_backup_passphrase(QWidget* parent, const QString& title,
                                                bool confirm) {
    QDialog dialog(parent);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(title);
    dialog.setModal(true);
    auto* v = new QVBoxLayout(&dialog);
    v->setContentsMargins(20, 16, 20, 16);
    v->setSpacing(12);
    auto* pw1 = new QLineEdit(&dialog);
    pw1->setEchoMode(QLineEdit::Password);
    auto* f1 = new QFormLayout();
    f1->addRow(QObject::tr("Backup passphrase:"), pw1);
    v->addLayout(f1);
    QLineEdit* pw2 = nullptr;
    if (confirm) {
        pw2 = new QLineEdit(&dialog);
        pw2->setEchoMode(QLineEdit::Password);
        auto* f2 = new QFormLayout();
        f2->addRow(QObject::tr("Confirm passphrase:"), pw2);
        v->addLayout(f2);
    }
    auto* bb = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    bb->button(QDialogButtonBox::Ok)->setObjectName("PrimaryButton");
    bb->button(QDialogButtonBox::Cancel)->setObjectName("SecondaryButton");
    QObject::connect(bb, &QDialogButtonBox::accepted, &dialog, [&] {
        if (pw1->text().trimmed().isEmpty()) {
            QMessageBox::warning(&dialog, title, QObject::tr("Passphrase must not be empty."));
            return;
        }
        if (pw2 != nullptr && pw1->text().trimmed() != pw2->text().trimmed()) {
            QMessageBox::warning(&dialog, title, QObject::tr("Passphrases do not match."));
            return;
        }
        dialog.accept();
    });
    QObject::connect(bb, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    auto* row = new QHBoxLayout();
    row->addStretch(1);
    row->addWidget(bb);
    row->addStretch(1);
    v->addLayout(row);
    if (dialog.exec() != QDialog::Accepted) {
        return std::nullopt;
    }
    return pw1->text().trimmed();
}

QString i2p_friendly(session::TransportState state) {
    switch (state) {
        case session::TransportState::Ready:
            return QObject::tr("Online");
        case session::TransportState::WarmingTunnels:
        case session::TransportState::SamConnected:
            return QObject::tr("Pending");
        case session::TransportState::Failed:
        case session::TransportState::Stopped:
            return QObject::tr("Offline");
        case session::TransportState::Degraded:
            return QObject::tr("Degraded");
        default:
            return QObject::tr("Starting");
    }
}

QString i2p_net_tag(session::TransportState state) {
    switch (state) {
        case session::TransportState::Ready:
            return QStringLiteral("visible");
        case session::TransportState::WarmingTunnels:
        case session::TransportState::SamConnected:
            return QStringLiteral("pending");
        case session::TransportState::Failed:
        case session::TransportState::Stopped:
            return QStringLiteral("offline");
        default:
            return QStringLiteral("starting");
    }
}

class SidebarResizeGrip : public QWidget {
public:
    explicit SidebarResizeGrip(QSplitter* splitter, std::function<void(int)> persist,
                               QWidget* parent = nullptr)
        : QWidget(parent), splitter_(splitter), persist_(std::move(persist)) {
        setObjectName("ContactsResizeGrip");
        setFixedWidth(4);
        setCursor(Qt::SplitHCursor);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
    }

protected:
    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton && splitter_ != nullptr) {
            drag_origin_ = event->globalPosition().toPoint().x();
            drag_sizes_ = splitter_->sizes();
            dragging_ = true;
        }
    }
    void mouseMoveEvent(QMouseEvent* event) override {
        if (!dragging_ || splitter_ == nullptr || drag_sizes_.size() < 2) {
            return;
        }
        const int delta = event->globalPosition().toPoint().x() - drag_origin_;
        int left = std::clamp(drag_sizes_[0] + delta, 160, 520);
        const int total = std::max(400, drag_sizes_[0] + drag_sizes_[1]);
        splitter_->setSizes({left, std::max(200, total - left)});
        if (persist_) {
            persist_(left);
        }
    }
    void mouseReleaseEvent(QMouseEvent*) override {
        dragging_ = false;
        if (persist_ && splitter_ != nullptr) {
            const auto sizes = splitter_->sizes();
            if (!sizes.isEmpty() && sizes[0] >= 160) {
                persist_(sizes[0]);
            }
        }
    }

private:
    QSplitter* splitter_ = nullptr;
    std::function<void(int)> persist_;
    int drag_origin_ = 0;
    QList<int> drag_sizes_;
    bool dragging_ = false;
};

class ComposeResizeGrip : public QWidget {
public:
    explicit ComposeResizeGrip(QSplitter* splitter, std::function<void(int)> persist,
                               QWidget* parent = nullptr)
        : QWidget(parent), splitter_(splitter), persist_(std::move(persist)) {
        setObjectName("ComposeResizeGrip");
        setFixedHeight(4);
        setCursor(Qt::SizeVerCursor);
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        setToolTip(QObject::tr("Drag to resize the message field"));
    }

protected:
    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton && splitter_ != nullptr) {
            drag_origin_ = event->globalPosition().toPoint().y();
            drag_sizes_ = splitter_->sizes();
            dragging_ = true;
        }
    }
    void mouseMoveEvent(QMouseEvent* event) override {
        if (!dragging_ || splitter_ == nullptr || drag_sizes_.size() < 2) {
            return;
        }
        const int delta = drag_origin_ - event->globalPosition().toPoint().y();
        const int total = std::max(200, drag_sizes_[0] + drag_sizes_[1]);
        const int max_bottom = std::min(280, std::max(72, total / 2));
        const int bottom = std::clamp(drag_sizes_[1] + delta, 72, max_bottom);
        splitter_->setSizes({std::max(120, total - bottom), bottom});
    }
    void mouseReleaseEvent(QMouseEvent*) override {
        dragging_ = false;
        if (persist_ && splitter_ != nullptr) {
            const auto sizes = splitter_->sizes();
            if (sizes.size() > 1) {
                persist_(sizes[1]);
            }
        }
    }

private:
    QSplitter* splitter_ = nullptr;
    std::function<void(int)> persist_;
    int drag_origin_ = 0;
    QList<int> drag_sizes_;
    bool dragging_ = false;
};

class ComposerEdit : public QPlainTextEdit {
public:
    explicit ComposerEdit(QWidget* parent = nullptr) : QPlainTextEdit(parent) {
        setViewportMargins(0, 4, 42, 0);
    }
};

class SearchHitsConsole : public QFrame {
public:
    explicit SearchHitsConsole(QWidget* parent = nullptr) : QFrame(parent) {
        setObjectName("ChatSearchHitsConsole");
        setFrameShape(QFrame::NoFrame);
        setAttribute(Qt::WA_TranslucentBackground, true);
        setAutoFillBackground(false);
        setProperty("night", true);
    }

protected:
    void paintEvent(QPaintEvent*) override {
        const int w = width();
        const int h = height();
        if (w < 3 || h < 3) {
            return;
        }
        const bool night = property("night").toBool();
        QPainterPath path;
        path.addRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5), 10, 10);
        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing, true);
        painter.fillPath(path, night ? QColor(18, 22, 28, 115) : QColor(232, 236, 244, 130));
        QPen pen(night ? QColor(255, 255, 255, 82) : QColor(60, 60, 67, 110));
        pen.setWidthF(1.35);
        painter.setPen(pen);
        painter.setBrush(Qt::NoBrush);
        painter.drawPath(path);
    }
};

}  // namespace

ChatWindow::ChatWindow(GuiOptions options, QWidget* parent)
    : QMainWindow(parent), options_(std::move(options)) {
    setWindowTitle(QString("I2PChat @ %1").arg(QString::fromStdString(options_.profile)));
    setAcceptDrops(true);
    resize(980, 640);
    QSettings settings;
    history_enabled_ = settings.value(QStringLiteral("historyEnabled"), true).toBool();
    privacy_mode_ = settings.value(QStringLiteral("privacyMode"), false).toBool();
    enter_sends_ = settings.value(QStringLiteral("enterSends"), true).toBool();
    notify_sound_ = settings.value(QStringLiteral("notifySound"), true).toBool();
    theme_pref_ = settings.value(QStringLiteral("themePref"), QStringLiteral("auto")).toString();
    sidebar_width_saved_ = settings.value(QStringLiteral("contactsSidebarWidth"), 0).toInt();
    compose_bottom_saved_ = settings.value(QStringLiteral("composeSplitBottom"), 0).toInt();
    if (theme_pref_ != QStringLiteral("auto") && theme_pref_ != QStringLiteral("light") &&
        theme_pref_ != QStringLiteral("dark")) {
        theme_pref_ = QStringLiteral("auto");
    }
    build_ui();
    compose_drafts_timer_ = new QTimer(this);
    compose_drafts_timer_->setSingleShot(true);
    compose_drafts_timer_->setInterval(kComposeDraftsDebounceMs);
    connect(compose_drafts_timer_, &QTimer::timeout, this, [this] { flush_compose_drafts(); });
    apply_theme();
    start_core();
}

ChatWindow::~ChatWindow() {
    flush_compose_drafts();
    stop_core();
    bundled_router_.reset();
}

void ChatWindow::build_ui() {
    auto* central = new QWidget(this);
    setCentralWidget(central);
    auto* root = new QVBoxLayout(central);
    root->setContentsMargins(8, 8, 8, 8);
    root->setSpacing(8);

    status_label_ = new QLabel(tr("Status: starting"), this);
    status_label_->setObjectName("StatusLabel");
    status_label_->setMinimumWidth(0);
    status_label_->setWordWrap(false);
    status_label_->setIndent(0);
    status_label_->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);
    status_label_->setFixedHeight(30);
    theme_button_ = new QToolButton(this);
    theme_button_->setObjectName("ThemeSwitchButton");
    theme_button_->setAutoRaise(false);
    theme_button_->setFixedSize(30, 30);
    theme_button_->setCursor(Qt::PointingHandCursor);
    connect(theme_button_, &QToolButton::clicked, this, &ChatWindow::show_theme_menu);
    if (qApp->styleHints() != nullptr) {
        connect(qApp->styleHints(), &QStyleHints::colorSchemeChanged, this, [this] {
            if (theme_pref_ == QStringLiteral("auto")) {
                apply_theme();
            }
        });
    }
    auto* status_row_w = new QWidget(this);
    auto* status_row = new QHBoxLayout(status_row_w);
    status_row->setContentsMargins(0, 0, 0, 0);
    status_row->setSpacing(8);
    status_row->addWidget(status_label_, 1);
    status_row->addWidget(theme_button_, 0, Qt::AlignRight | Qt::AlignVCenter);

    sidebar_ = new QWidget(this);
    sidebar_->setObjectName("ContactsSidebar");
    sidebar_->setMinimumWidth(0);
    sidebar_->setMaximumWidth(520);
    auto* side_layout = new QVBoxLayout(sidebar_);
    side_layout->setContentsMargins(8, 8, 0, 8);
    side_layout->setSpacing(8);
    auto* groups_header = new QWidget(sidebar_);
    auto* groups_row = new QHBoxLayout(groups_header);
    groups_row->setContentsMargins(0, 0, 4, 0);
    groups_row->setSpacing(4);
    auto* groups_title = new QLabel(tr("Groups"), sidebar_);
    groups_title->setObjectName("ContactsSidebarTitle");
    auto* new_group = new QPushButton(tr("New"), sidebar_);
    new_group->setObjectName("GroupsCreateButton");
    new_group->setCursor(Qt::PointingHandCursor);
    new_group->setFixedHeight(34);
    connect(new_group, &QPushButton::clicked, this, &ChatWindow::new_group_hint);
    groups_row->addWidget(groups_title, 1);
    groups_row->addWidget(new_group, 0);

    contacts_model_ = new ContactListModel(this);
    contact_delegate_ = new ContactItemDelegate(this);
    contact_view_ = new QListView(sidebar_);
    contact_view_->setObjectName("ContactsList");
    contact_view_->setModel(contacts_model_);
    contact_view_->setItemDelegate(contact_delegate_);
    contact_view_->setFrameShape(QFrame::NoFrame);
    contact_view_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    contact_view_->setSelectionMode(QAbstractItemView::SingleSelection);
    contact_view_->setFocusPolicy(Qt::NoFocus);
    contact_view_->setContextMenuPolicy(Qt::CustomContextMenu);
    connect(contact_view_, &QListView::clicked, this, &ChatWindow::contact_activated);
    connect(contact_view_, &QListView::customContextMenuRequested, this,
            &ChatWindow::sidebar_context_menu);

    side_layout->addWidget(groups_header);
    side_layout->addWidget(contact_view_, 1);

    chat_ = new ChatModel(this);
    chat_delegate_ = new ChatItemDelegate(this);
    auto* chat_surface = new QWidget(this);
    chat_surface->setObjectName("ChatSurface");
    auto* chat_layout = new QVBoxLayout(chat_surface);
    chat_layout->setContentsMargins(4, 8, 0, 8);
    chat_layout->setSpacing(0);

    search_edit_ = new QLineEdit(chat_surface);
    search_edit_->setObjectName("ChatSearchLineEdit");
    search_edit_->setPlaceholderText(tr("Search in this chat…"));
    search_edit_->setFixedHeight(34);
    connect(search_edit_, &QLineEdit::textChanged, this, &ChatWindow::search_changed);
    search_status_ = new QLabel(chat_surface);
    search_status_->setObjectName("ChatSearchStatusInline");
    search_status_->hide();
    auto* search_prev = new QPushButton(QStringLiteral("◀"), chat_surface);
    search_prev->setObjectName("ChatSearchStepButton");
    search_prev->setFixedSize(36, 34);
    search_prev->setToolTip(tr("Previous match"));
    auto* search_next = new QPushButton(QStringLiteral("▶"), chat_surface);
    search_next->setObjectName("ChatSearchStepButton");
    search_next->setFixedSize(36, 34);
    search_next->setToolTip(tr("Next match"));
    connect(search_prev, &QPushButton::clicked, this, [this] { search_step(-1); });
    connect(search_next, &QPushButton::clicked, this, [this] { search_step(1); });
    auto* search_header = new QWidget(chat_surface);
    auto* search_header_lay = new QVBoxLayout(search_header);
    search_header_lay->setContentsMargins(0, 0, 8, 5);
    search_header_lay->setSpacing(0);
    auto* search_row_w = new QWidget(search_header);
    auto* search_row = new QHBoxLayout(search_row_w);
    search_row->setContentsMargins(4, 0, 0, 4);
    search_row->setSpacing(8);
    search_row->addWidget(search_edit_, 1);
    search_row->addWidget(search_status_);
    search_row->addWidget(search_prev);
    search_row->addWidget(search_next);
    search_header_lay->addWidget(search_row_w);

    auto* search_console = new SearchHitsConsole(chat_surface);
    search_console_ = search_console;
    search_console_->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    search_console_->setMaximumHeight(0);
    search_console_->hide();
    auto* console_lay = new QVBoxLayout(search_console_);
    console_lay->setContentsMargins(8, 5, 8, 7);
    console_lay->setSpacing(0);
    auto* scroll = new QScrollArea(search_console_);
    scroll->setObjectName("ChatSearchHitsScroll");
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setWidgetResizable(true);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scroll->setMaximumHeight(108);
    auto* hits_inner = new QWidget();
    hits_inner->setObjectName("ChatSearchHitsInner");
    search_hits_layout_ = new QVBoxLayout(hits_inner);
    search_hits_layout_->setContentsMargins(2, 2, 2, 2);
    search_hits_layout_->setSpacing(3);
    search_hits_layout_->addStretch(1);
    scroll->setWidget(hits_inner);
    console_lay->addWidget(scroll);

    chat_view_ = new QListView(chat_surface);
    chat_view_->setObjectName("ChatView");
    chat_view_->setModel(chat_);
    chat_view_->setItemDelegate(chat_delegate_);
    chat_view_->setUniformItemSizes(false);
    chat_view_->setSpacing(0);
    chat_view_->setWordWrap(false);
    chat_view_->setSelectionMode(QAbstractItemView::NoSelection);
    chat_view_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    chat_view_->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
    chat_view_->setFrameShape(QFrame::NoFrame);
    chat_view_->setContextMenuPolicy(Qt::CustomContextMenu);
    connect(chat_view_, &QWidget::customContextMenuRequested, this,
            &ChatWindow::show_chat_context_menu);

    empty_hint_ = new QLabel(
        tr("Select a saved peer, or paste a base32 address and press Connect."), chat_surface);
    empty_hint_->setObjectName("ChatEmptyHint");
    empty_hint_->setAlignment(Qt::AlignCenter);
    empty_hint_->setWordWrap(true);

    auto* chat_stack = new QWidget(chat_surface);
    auto* stack = new QStackedLayout(chat_stack);
    stack->setContentsMargins(0, 0, 0, 0);
    stack->addWidget(chat_view_);
    stack->addWidget(empty_hint_);
    stack->setCurrentWidget(empty_hint_);
    chat_stack->setProperty("stack", QVariant::fromValue(static_cast<void*>(stack)));

    chat_layout->addWidget(search_header);
    chat_layout->addWidget(search_console_);
    chat_layout->addWidget(chat_stack, 1);

    composer_ = new ComposerEdit(this);
    composer_->setObjectName("Composer");
    composer_->installEventFilter(this);
    composer_->setPlaceholderText(tr("Message or /command…  Enter to send, Shift+Enter for a new line"));
    QFont compose_font = composer_->font();
    compose_font.setPointSize(compose_font.pointSize() + 1);
    composer_->setFont(compose_font);
    const int compose_min_h = std::max(48, composer_->fontMetrics().lineSpacing() + 20);
    composer_->setMinimumHeight(compose_min_h);
    composer_->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    emoji_button_ = new QToolButton(this);
    emoji_button_->setObjectName("EmojiPickerButton");
    emoji_button_->setAutoRaise(true);
    emoji_button_->setFixedSize(28, 28);
    emoji_button_->setIconSize(QSize(17, 17));
    emoji_button_->setCursor(Qt::PointingHandCursor);
    emoji_button_->setToolTip(
        tr("Open emoji panel.\nIn the panel: click to insert; Esc to close.\n\nShortcut: %1")
            .arg(QKeySequence(QStringLiteral("Ctrl+;"))
                     .toString(QKeySequence::NativeText)));
    emoji_popup_ = new EmojiPickerPopup(this);
    connect(emoji_button_, &QToolButton::clicked, this, &ChatWindow::toggle_emoji_picker);
    connect(emoji_popup_, &EmojiPickerPopup::emoji_chosen, this, &ChatWindow::insert_emoji);
    connect(emoji_popup_, &EmojiPickerPopup::picker_hidden, this, [this] {
        emoji_picker_suppress_until_ms_ = QDateTime::currentMSecsSinceEpoch() + 180;
        if (composer_ != nullptr) {
            composer_->setFocus();
        }
    });
    emoji_button_->installEventFilter(this);
    emoji_popup_->installEventFilter(this);
    emoji_hover_open_ = new QTimer(this);
    emoji_hover_open_->setSingleShot(true);
    connect(emoji_hover_open_, &QTimer::timeout, this, [this] {
        if (cursor_over_emoji_area()) {
            show_emoji_picker();
        }
    });
    emoji_hover_close_ = new QTimer(this);
    emoji_hover_close_->setSingleShot(true);
    connect(emoji_hover_close_, &QTimer::timeout, this, [this] {
        if (emoji_popup_ != nullptr && emoji_popup_->isVisible() && !cursor_over_emoji_area()) {
            emoji_popup_->hide();
        }
    });
    send_button_ = new QPushButton(tr("Send"), this);
    send_button_->setObjectName("PrimaryActionButton");
    send_button_->setMinimumHeight(compose_min_h);
    send_button_->setSizePolicy(QSizePolicy::Minimum, QSizePolicy::Expanding);
    connect(send_button_, &QPushButton::clicked, this, &ChatWindow::send_current);
    connect(composer_, &QPlainTextEdit::textChanged, this, [this] { schedule_compose_drafts_persist(); });
    auto* compose = new QWidget(this);
    compose->setObjectName("ComposeBar");
    auto* compose_row = new QHBoxLayout(compose);
    compose_row->setContentsMargins(8, 8, 8, 8);
    compose_row->setSpacing(8);
    auto* compose_wrap = new QWidget(compose);
    compose_wrap->setObjectName("ComposeInputWrap");
    auto* wrap_lay = new QVBoxLayout(compose_wrap);
    wrap_lay->setContentsMargins(0, 0, 0, 0);
    wrap_lay->setSpacing(0);
    wrap_lay->addWidget(composer_);
    emoji_button_->setParent(compose_wrap);
    compose_wrap->installEventFilter(this);
    compose_row->addWidget(compose_wrap, 1);
    compose_row->addWidget(send_button_);

    addr_edit_ = new QLineEdit(this);
    addr_edit_->setObjectName("PeerAddressEdit");
    addr_edit_->setPlaceholderText(tr("Peer base32 address (optional .b32.i2p when pasting)"));
    addr_edit_->setFixedHeight(30);
    connect(addr_edit_, &QLineEdit::textChanged, this, [this](const QString& text) {
        selected_ = sam::normalize_peer_address(text.toStdString());
        refresh_connection_buttons();
        apply_empty_state();
    });
    connect(addr_edit_, &QLineEdit::returnPressed, this, &ChatWindow::connect_selected);
    connect_button_ = new QPushButton(tr("Connect"), this);
    connect_button_->setObjectName("ConnectPeerButton");
    connect_button_->setFixedHeight(30);
    disconnect_button_ = new QPushButton(tr("Disconnect"), this);
    disconnect_button_->setObjectName("DisconnectPeerButton");
    disconnect_button_->setFixedHeight(30);
    connect(connect_button_, &QPushButton::clicked, this, &ChatWindow::connect_selected);
    connect(disconnect_button_, &QPushButton::clicked, this, &ChatWindow::disconnect_selected);
    more_button_ = new QToolButton(this);
    more_button_->setObjectName("MoreActionsButton");
    more_button_->setText(QStringLiteral("⋯"));
    more_button_->setFixedHeight(30);
    more_button_->setToolTip(
        tr("Open the ⋯ menu: load profile, send picture or file, backups, BlindBox, lock, "
           "history, privacy, notifications, and more."));
    connect(more_button_, &QToolButton::clicked, this, &ChatWindow::show_more_menu);
    auto* actions = new QWidget(this);
    actions->setObjectName("ActionToolbar");
    auto* actions_row = new QHBoxLayout(actions);
    actions_row->setContentsMargins(4, 8, 8, 8);
    actions_row->setSpacing(8);
    actions_row->addWidget(addr_edit_, 1);
    actions_row->addWidget(connect_button_);
    actions_row->addWidget(disconnect_button_);
    actions_row->addWidget(more_button_);

    auto* right_column = new QWidget(this);
    auto* right_layout = new QVBoxLayout(right_column);
    right_layout->setContentsMargins(0, 0, 0, 0);
    right_layout->setSpacing(8);

    auto* compose_bottom = new QWidget(this);
    auto* compose_col = new QVBoxLayout(compose_bottom);
    compose_col->setContentsMargins(0, 0, 0, 0);
    compose_col->setSpacing(0);
    auto* compose_splitter = new QSplitter(Qt::Vertical, right_column);
    compose_splitter_ = compose_splitter;
    compose_splitter->setHandleWidth(0);
    compose_splitter->setChildrenCollapsible(false);
    compose_splitter->setStretchFactor(0, 1);
    compose_splitter->setStretchFactor(1, 0);
    auto* compose_grip = new ComposeResizeGrip(
        compose_splitter, [this](int h) { persist_compose_bottom(h); }, compose_bottom);
    compose_col->addWidget(compose_grip);
    compose_col->addWidget(compose, 1);
    compose_splitter->addWidget(chat_surface);
    compose_splitter->addWidget(compose_bottom);
    compose_splitter->setStretchFactor(0, 1);
    compose_splitter->setStretchFactor(1, 0);
    const int compose_default = compose_min_h + 20;
    const int compose_bottom_h =
        compose_bottom_saved_ > 0 ? compose_bottom_saved_ : compose_default;
    compose_splitter->setSizes({400, compose_bottom_h});

    right_layout->addWidget(compose_splitter, 1);
    right_layout->addWidget(actions);

    sidebar_toggle_ = new QPushButton(QStringLiteral("◀"), this);
    sidebar_toggle_->setObjectName("ContactsSidebarToggle");
    sidebar_toggle_->setFlat(true);
    sidebar_toggle_->setFocusPolicy(Qt::NoFocus);
    sidebar_toggle_->setFixedWidth(22);
    sidebar_toggle_->setMinimumHeight(30);
    sidebar_toggle_->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
    sidebar_toggle_->setCursor(Qt::PointingHandCursor);
    sidebar_toggle_->setToolTip(tr("Show or hide saved peers"));
    connect(sidebar_toggle_, &QPushButton::clicked, this, &ChatWindow::toggle_sidebar);

    splitter_ = new QSplitter(Qt::Horizontal, this);
    splitter_->setHandleWidth(0);
    splitter_->setChildrenCollapsible(false);
    splitter_->setOpaqueResize(true);

    sidebar_grip_ = new SidebarResizeGrip(
        splitter_, [this](int w) { persist_sidebar_width(w); }, this);

    auto* right_pack = new QWidget(this);
    right_pack_layout_ = new QHBoxLayout(right_pack);
    right_pack_layout_->setContentsMargins(4, 0, 0, 0);
    right_pack_layout_->setSpacing(0);
    right_pack_layout_->addWidget(sidebar_toggle_);
    right_pack_layout_->addWidget(sidebar_grip_);
    right_pack_layout_->addWidget(right_column, 1);

    splitter_->addWidget(sidebar_);
    splitter_->addWidget(right_pack);
    splitter_->setStretchFactor(0, 0);
    splitter_->setStretchFactor(1, 1);
    splitter_->setSizes({240, 740});

    root->addWidget(status_row_w);
    root->addWidget(splitter_, 1);

    more_popup_ = new ActionsPopup(this);
    more_popup_->set_night(options_.dark);
    sidebar_popup_ = new ActionsPopup(this);
    sidebar_popup_->set_night(options_.dark);
    chat_popup_ = new ActionsPopup(this);
    chat_popup_->set_night(options_.dark);
    theme_popup_ = new ActionsPopup(this);
    theme_popup_->set_night(options_.dark);

    auto bind_shortcut = [this](const QString& keys, auto slot) {
        new QShortcut(QKeySequence(keys), this, slot);
    };
    bind_shortcut(QStringLiteral("Ctrl+O"), [this] { load_profile_dat(); });
    bind_shortcut(QStringLiteral("Ctrl+P"), [this] { choose_file(true); });
    bind_shortcut(QStringLiteral("Ctrl+F"), [this] { choose_file(false); });
    bind_shortcut(QStringLiteral("Ctrl+G"), [this] { new_group_hint(); });
    bind_shortcut(QStringLiteral("Ctrl+J"), [this] { join_group_hint(); });
    bind_shortcut(QStringLiteral("Ctrl+D"), [this] { show_blindbox_diagnostics(); });
    bind_shortcut(QStringLiteral("Ctrl+E"), [this] { export_profile_backup(); });
    bind_shortcut(QStringLiteral("Ctrl+I"), [this] { import_profile_backup(); });
    bind_shortcut(QStringLiteral("Ctrl+Shift+E"), [this] { export_history_backup(); });
    bind_shortcut(QStringLiteral("Ctrl+Shift+I"), [this] { import_history_backup(); });
    bind_shortcut(QStringLiteral("Ctrl+U"), [this] { check_for_updates(); });
    bind_shortcut(QStringLiteral("Ctrl+Shift+A"), [this] { open_app_dir(); });
    bind_shortcut(QStringLiteral("Ctrl+R"), [this] { router_settings(); });
    bind_shortcut(QStringLiteral("Ctrl+Shift+C"), [this] { copy_address(); });
    bind_shortcut(QStringLiteral("Ctrl+Shift+G"), [this] { copy_group_invite(); });
    new QShortcut(QKeySequence(QStringLiteral("Ctrl+Return")), this, SLOT(send_current()));
    new QShortcut(QKeySequence(QStringLiteral("Ctrl+;")), this, SLOT(toggle_emoji_picker()));

    new QShortcut(QKeySequence(QStringLiteral("Ctrl+B")), this, SLOT(toggle_sidebar()));

    sync_sidebar_toggle_margin();
    QTimer::singleShot(0, this, [this] {
        position_emoji_button();
        balance_sidebar_splitter();
    });

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
    refresh_connection_buttons();
}

void ChatWindow::apply_theme() {
    options_.dark = resolved_dark();
    qApp->setProperty("i2pchatNight", options_.dark);
    const QString path = options_.dark ? ":/i2pchat/qss/dark.qss" : ":/i2pchat/qss/light.qss";
    QFile file(path);
    if (file.open(QIODevice::ReadOnly)) {
        qApp->setStyleSheet(QString::fromUtf8(file.readAll()));
    }
    theme_button_->setText(options_.dark ? QStringLiteral("☾") : QStringLiteral("☀"));
    theme_button_->setToolTip(tr("Theme: System / Light / Dark"));
    if (chat_delegate_) {
        chat_delegate_->set_dark(options_.dark);
    }
    if (search_console_) {
        search_console_->setProperty("night", options_.dark);
        search_console_->update();
    }
    if (contact_delegate_) {
        contact_delegate_->set_dark(options_.dark);
    }
    if (more_popup_) {
        more_popup_->set_night(options_.dark);
    }
    if (sidebar_popup_) {
        sidebar_popup_->set_night(options_.dark);
    }
    if (chat_popup_) {
        chat_popup_->set_night(options_.dark);
    }
    if (theme_popup_) {
        theme_popup_->set_night(options_.dark);
    }
    if (emoji_popup_) {
        emoji_popup_->set_night(options_.dark);
    }
    if (emoji_button_) {
        emoji_button_->setIcon(tinted_face_icon(options_.dark));
        emoji_button_->setIconSize(QSize(17, 17));
    }
    chat_view_->viewport()->update();
    contact_view_->viewport()->update();
}

void ChatWindow::toggle_theme() { show_theme_menu(); }

bool ChatWindow::resolved_dark() const {
    if (theme_pref_ == QStringLiteral("light")) {
        return false;
    }
    if (theme_pref_ == QStringLiteral("dark")) {
        return true;
    }
    if (qApp->styleHints() != nullptr) {
        return qApp->styleHints()->colorScheme() == Qt::ColorScheme::Dark;
    }
    return options_.dark;
}

void ChatWindow::show_theme_menu() {
    if (theme_popup_ == nullptr) {
        return;
    }
    theme_popup_->clear_actions();
    theme_popup_->set_night(options_.dark);
    auto pick = [this](const QString& pref) {
        theme_pref_ = pref;
        QSettings().setValue(QStringLiteral("themePref"), theme_pref_);
        apply_theme();
    };
    theme_popup_->add_action(tr("System"), {}, [pick] { pick(QStringLiteral("auto")); },
                             tr("Follow the desktop light/dark setting."));
    theme_popup_->add_action(tr("Light"), {}, [pick] { pick(QStringLiteral("light")); });
    theme_popup_->add_action(tr("Dark"), {}, [pick] { pick(QStringLiteral("dark")); });
    theme_popup_->show_below(theme_button_);
}

void ChatWindow::show_chat_context_menu(const QPoint& pos) {
    if (chat_popup_ == nullptr || chat_ == nullptr) {
        return;
    }
    const QModelIndex index = chat_view_->indexAt(pos);
    if (!index.isValid()) {
        return;
    }
    const QString text = index.data(ChatModel::TextRole).toString();
    const QString time = index.data(ChatModel::TimeRole).toString();
    const QString author = index.data(ChatModel::AuthorRole).toString();
    chat_popup_->clear_actions();
    chat_popup_->set_night(options_.dark);
    chat_popup_->add_action(tr("Copy text"), {}, [text] { qApp->clipboard()->setText(text); },
                            tr("Copy the message body."));
    chat_popup_->add_action(
        tr("Copy with timestamp"), {},
        [text, time, author] {
            QString line;
            if (!time.isEmpty()) {
                line += time + QStringLiteral(" ");
            }
            if (!author.isEmpty()) {
                line += author + QStringLiteral(": ");
            }
            line += text;
            qApp->clipboard()->setText(line);
        },
        tr("Copy the message with time and author."));
    QString path;
    if (text.startsWith(QStringLiteral("[image] "))) {
        path = text.mid(8).trimmed();
    } else if (text.startsWith(QStringLiteral("[file] "))) {
        path = text.mid(7).trimmed();
    }
    if (!path.isEmpty()) {
        chat_popup_->add_action(tr("Open"), {}, [path] {
            QDesktopServices::openUrl(QUrl::fromLocalFile(path));
        });
        chat_popup_->add_action(tr("Copy path"), {}, [path] { qApp->clipboard()->setText(path); });
        const QFileInfo info(path);
        if (info.exists()) {
            const QString folder = info.absolutePath();
            chat_popup_->add_action(tr("Open folder"), {}, [folder] {
                QDesktopServices::openUrl(QUrl::fromLocalFile(folder));
            });
        }
    }
    chat_popup_->add_separator();
    chat_popup_->add_action(
        tr("Reply"), {},
        [this, text] {
            const QString cur = composer_->toPlainText();
            const QString quote = QStringLiteral("> ") + text;
            composer_->setPlainText(cur.trimmed().isEmpty() ? quote
                                                            : cur + QStringLiteral("\n\n") + quote);
            composer_->setFocus();
        },
        tr("Insert a quoted copy of this message into the compose field."));
    const auto kind = static_cast<presentation::LineKind>(index.data(ChatModel::KindRole).toInt());
    const QString marker = index.data(ChatModel::MarkerRole).toString();
    if (kind == presentation::LineKind::Outgoing && marker == QStringLiteral("✗")) {
        chat_popup_->add_action(
            tr("Retry"), {},
            [this, text] {
                if (!service_) {
                    return;
                }
                if (text.startsWith(QStringLiteral("[image] "))) {
                    const std::string media = text.mid(8).trimmed().toStdString();
                    const std::string peer = selected_;
                    post_core([this, peer, media]() -> asio::awaitable<void> {
                        co_await service_->send_image(peer, media);
                    });
                } else if (text.startsWith(QStringLiteral("[file] "))) {
                    const std::string media = text.mid(7).trimmed().toStdString();
                    const std::string peer = selected_;
                    post_core([this, peer, media]() -> asio::awaitable<void> {
                        co_await service_->send_file(peer, media);
                    });
                } else if (!selected_.empty()) {
                    const std::string peer = selected_;
                    post_core([this, peer, body = text.toStdString()]() -> asio::awaitable<void> {
                        co_await service_->send_text(peer, body);
                    });
                }
            },
            tr("Send this outgoing message again."));
    }
    chat_popup_->show_at(chat_view_->viewport()->mapToGlobal(pos));
}

void ChatWindow::note_unread(const std::string& conversation_id) {
    if (conversation_id.empty()) {
        return;
    }
    unread_[conversation_id] += 1;
    update_tray_unread();
}

void ChatWindow::clear_unread(const std::string& conversation_id) {
    unread_.erase(conversation_id);
    update_tray_unread();
}

void ChatWindow::update_tray_unread() {
    unsigned total = 0;
    for (const auto& [_, count] : unread_) {
        total += count;
    }
    if (tray_ == nullptr) {
        return;
    }
    tray_->setToolTip(total == 0 ? QStringLiteral("I2PChat")
                                 : tr("I2PChat (%1 unread)").arg(total));
}

void ChatWindow::toggle_sidebar() {
    sidebar_collapsed_ = !sidebar_collapsed_;
    if (sidebar_collapsed_) {
        if (splitter_ != nullptr) {
            const auto sizes = splitter_->sizes();
            if (sizes.size() >= 1 && sizes[0] > 0) {
                sidebar_width_saved_ = sizes[0];
            }
        }
        sidebar_->hide();
    } else {
        sidebar_->show();
    }
    sidebar_toggle_->setText(sidebar_collapsed_ ? QStringLiteral("▶") : QStringLiteral("◀"));
    sync_sidebar_toggle_margin();
    balance_sidebar_splitter();
    QTimer::singleShot(0, this, [this] { balance_sidebar_splitter(); });
}

void ChatWindow::persist_sidebar_width(int width_px) {
    sidebar_width_saved_ = std::clamp(width_px, 160, 520);
    QSettings().setValue(QStringLiteral("contactsSidebarWidth"), sidebar_width_saved_);
}

void ChatWindow::persist_compose_bottom(int height_px) {
    compose_bottom_saved_ = std::max(72, height_px);
    QSettings().setValue(QStringLiteral("composeSplitBottom"), compose_bottom_saved_);
}

int ChatWindow::sidebar_open_target_px(int total) const {
    const int avail = std::max(160, total - 200);
    if (sidebar_width_saved_ <= 0) {
        const int quarter = std::max(0, total / 4);
        return std::min({std::max(160, std::min(quarter, 240)), 520, avail});
    }
    return std::min({std::max(160, sidebar_width_saved_), 520, avail});
}

void ChatWindow::balance_sidebar_splitter() {
    if (splitter_ == nullptr) {
        return;
    }
    const int total = std::max(400, splitter_->width());
    if (sidebar_collapsed_ || (sidebar_ != nullptr && !sidebar_->isVisible())) {
        splitter_->setSizes({0, total});
        return;
    }
    const int left = sidebar_open_target_px(total);
    splitter_->setSizes({left, std::max(200, total - left)});
}

void ChatWindow::position_emoji_button() {
    if (emoji_button_ == nullptr) {
        return;
    }
    auto* wrap = qobject_cast<QWidget*>(emoji_button_->parent());
    if (wrap == nullptr) {
        return;
    }
    constexpr int margin = 6;
    emoji_button_->move(wrap->width() - emoji_button_->width() - margin, margin);
    emoji_button_->raise();
}

void ChatWindow::toggle_emoji_picker() {
    if (emoji_popup_ == nullptr || emoji_button_ == nullptr) {
        return;
    }
    if (QDateTime::currentMSecsSinceEpoch() < emoji_picker_suppress_until_ms_) {
        return;
    }
    if (emoji_popup_->isVisible()) {
        emoji_popup_->hide();
        return;
    }
    show_emoji_picker();
}

bool ChatWindow::cursor_over_emoji_area() const {
    if (emoji_button_ == nullptr) {
        return false;
    }
    const QPoint gp = QCursor::pos();
    const QRect btn(emoji_button_->mapToGlobal(QPoint(0, 0)), emoji_button_->size());
    if (btn.contains(gp)) {
        return true;
    }
    return emoji_popup_ != nullptr && emoji_popup_->isVisible() &&
           emoji_popup_->frameGeometry().contains(gp);
}

void ChatWindow::show_emoji_picker() {
    if (emoji_popup_ == nullptr || emoji_button_ == nullptr) {
        return;
    }
    if (emoji_hover_close_ != nullptr) {
        emoji_hover_close_->stop();
    }
    emoji_popup_->set_night(options_.dark);
    emoji_popup_->show_above(emoji_button_);
}

void ChatWindow::insert_emoji(const QString& glyph) {
    if (composer_ == nullptr || glyph.isEmpty()) {
        return;
    }
    QTextCursor cursor = composer_->textCursor();
    cursor.insertText(glyph);
    composer_->setTextCursor(cursor);
    composer_->setFocus();
}

void ChatWindow::show_more_menu() {
    if (emoji_popup_ != nullptr && emoji_popup_->isVisible()) {
        emoji_popup_->hide();
    }
    more_popup_->clear_actions();
    more_popup_->set_night(options_.dark);
    more_popup_->add_action(tr("Load profile (.dat)"), QStringLiteral("Ctrl+O"),
                            [this] { load_profile_dat(); },
                            tr("Open a file dialog to load a profile from a .dat file."));
    more_popup_->add_action(tr("Send picture"), QStringLiteral("Ctrl+P"),
                            [this] { choose_file(true); },
                            tr("Send an image file to the connected peer; images appear inline."));
    more_popup_->add_action(tr("Send file"), QStringLiteral("Ctrl+F"),
                            [this] { choose_file(false); },
                            tr("Send any file to the connected peer via the file picker."));
    more_popup_->add_action(tr("New text group…"), QStringLiteral("Ctrl+G"),
                            [this] { new_group_hint(); },
                            tr("Create a text-only group with a local title and member addresses."));
    more_popup_->add_action(tr("Join group via invite…"), QStringLiteral("Ctrl+J"),
                            [this] { join_group_hint(); },
                            tr("Paste a copied group invite string to join that text group."));
    if (!active_group_id_.empty()) {
        more_popup_->add_action(tr("Copy group invite"), QStringLiteral("Ctrl+Shift+G"),
                                [this] { copy_group_invite(); },
                                tr("Copy a shareable invite string for this group to the clipboard."));
    }
    more_popup_->add_action(tr("BlindBox diagnostics"), QStringLiteral("Ctrl+D"),
                            [this] { show_blindbox_diagnostics(); },
                            tr("Show offline-delivery status and edit BlindBox replica endpoints."));
    more_popup_->add_action(tr("Export profile backup…"), QStringLiteral("Ctrl+E"),
                            [this] { export_profile_backup(); },
                            tr("Export an encrypted backup of this profile."));
    more_popup_->add_action(tr("Import profile backup…"), QStringLiteral("Ctrl+I"),
                            [this] { import_profile_backup(); },
                            tr("Import an encrypted profile backup."));
    more_popup_->add_action(tr("Export history backup…"), QStringLiteral("Ctrl+Shift+E"),
                            [this] { export_history_backup(); },
                            tr("Export an encrypted backup of chat history."));
    more_popup_->add_action(tr("Import history backup…"), QStringLiteral("Ctrl+Shift+I"),
                            [this] { import_history_backup(); },
                            tr("Import an encrypted history backup."));
    more_popup_->add_action(tr("Check for updates…"), QStringLiteral("Ctrl+U"),
                            [this] { check_for_updates(); },
                            tr("Look up the latest I2PChat release."));
    more_popup_->add_action(tr("Open App dir"), QStringLiteral("Ctrl+Shift+A"),
                            [this] { open_app_dir(); },
                            tr("Open the application data folder in the file manager."));
    more_popup_->add_action(tr("I2P router…"), QStringLiteral("Ctrl+R"),
                            [this] { router_settings(); },
                            tr("Choose bundled or system i2pd and SAM ports."));
    more_popup_->add_separator();
    more_popup_->add_action(tr("Forget pinned peer key"), {}, [this] { forget_pin(); });
    more_popup_->add_action(tr("Copy my address"), QStringLiteral("Ctrl+Shift+C"),
                            [this] { copy_address(); });
    more_popup_->add_separator();
    more_popup_->add_action(history_enabled_ ? tr("History: on") : tr("History: off"), {}, [this] {
        history_enabled_ = !history_enabled_;
        QSettings().setValue(QStringLiteral("historyEnabled"), history_enabled_);
        if (!history_enabled_) {
            chat_->clear();
            apply_empty_state();
        } else {
            reload_selected();
        }
    });
    more_popup_->add_action(tr("Clear history"), {}, [this] { clear_history(); });
    more_popup_->add_action(tr("History retention…"), {},
                            [this] { configure_history_retention(); });
    more_popup_->add_action(privacy_mode_ ? tr("Privacy mode: on") : tr("Privacy mode: off"), {},
                            [this] {
                                privacy_mode_ = !privacy_mode_;
                                QSettings().setValue(QStringLiteral("privacyMode"), privacy_mode_);
                                status_label_->setText(
                                    privacy_mode_
                                        ? tr("Privacy mode ON: tray hides message text; while this "
                                             "window is focused, no tray toasts or notification "
                                             "sounds.")
                                        : tr("Privacy mode OFF."));
                            });
    more_popup_->add_action(enter_sends_ ? tr("Enter sends: on") : tr("Enter sends: off"), {},
                            [this] {
                                enter_sends_ = !enter_sends_;
                                QSettings().setValue(QStringLiteral("enterSends"), enter_sends_);
                                composer_->setPlaceholderText(
                                    enter_sends_ ? tr("Message or /command…  Enter to send, "
                                                      "Shift+Enter for a new line")
                                                 : tr("Message or /command…  Ctrl+Enter to send, "
                                                      "Enter for a new line"));
                            });
    more_popup_->add_separator();
    more_popup_->add_action(
        notify_sound_ ? tr("Notification sound: on") : tr("Notification sound: off"), {}, [this] {
            notify_sound_ = !notify_sound_;
            QSettings().setValue(QStringLiteral("notifySound"), notify_sound_);
        });
    more_popup_->show_below(more_button_);
}

void ChatWindow::post_core(std::function<asio::awaitable<void>()> work) {
    asio::co_spawn(core_, std::move(work), asio::detached);
}

void ChatWindow::apply_router_settings_to_options() {
    const GuiRouterSettings rs = load_gui_router_settings(options_.app_root);
    if (rs.backend == "bundled") {
        options_.sam_host = rs.bundled_sam_host;
        options_.sam_port = rs.bundled_sam_port;
    } else {
        options_.sam_host = rs.system_sam_host;
        options_.sam_port = rs.system_sam_port;
    }
}

QString ChatWindow::bundled_router_status() const {
    const auto binary = find_bundled_i2pd_binary();
    const auto data_dir = router_runtime_dir(options_.app_root);
    if (bundled_router_ && bundled_router_->is_running()) {
        const auto& runtime = bundled_router_->runtime();
        return tr("Bundled i2pd is running (SAM %1:%2). Data dir: %3")
            .arg(QString::fromStdString(runtime.sam_host))
            .arg(runtime.sam_port)
            .arg(QString::fromStdString(data_dir.string()));
    }
    if (binary) {
        return tr("Bundled i2pd binary is available. Data dir: %1")
            .arg(QString::fromStdString(data_dir.string()));
    }
    return tr("No bundled i2pd binary in this app. Use system i2pd, or place i2pd under "
              "Resources/vendor/i2pd.");
}

void ChatWindow::ensure_bundled_router() {
    const GuiRouterSettings rs = load_gui_router_settings(options_.app_root);
    if (rs.backend != "bundled" || !bundled_i2pd_allowed()) {
        return;
    }
    if (bundled_router_ && bundled_router_->is_running()) {
        return;
    }
    const auto binary = find_bundled_i2pd_binary();
    if (!binary) {
        return;
    }
    router::I2pdManager::Config cfg;
    cfg.binary = *binary;
    cfg.data_dir = router_runtime_dir(options_.app_root);
    cfg.runtime.sam_host = rs.bundled_sam_host;
    cfg.runtime.sam_port = rs.bundled_sam_port;
    cfg.runtime.http_proxy_port = rs.bundled_http_proxy_port;
    cfg.runtime.socks_proxy_port = rs.bundled_socks_proxy_port;
    cfg.runtime.control_http_port = rs.bundled_control_http_port;
    cfg.runtime.data_dir = cfg.data_dir;
    cfg.runtime.conf_path = cfg.data_dir / "i2pd.conf";
    cfg.runtime.log_path = cfg.data_dir / "i2pd.log";
    bundled_router_ = std::make_unique<router::I2pdManager>(std::move(cfg));
    bundled_router_->prepare_data_dir();
    bundled_router_->start();
}

bool ChatWindow::wait_for_sam_ready(int timeout_ms) {
    QElapsedTimer timer;
    timer.start();
    while (timer.elapsed() < timeout_ms) {
        QTcpSocket sock;
        sock.connectToHost(QString::fromStdString(options_.sam_host), options_.sam_port);
        if (sock.waitForConnected(400)) {
            sock.write("HELLO VERSION MIN=3.1 MAX=3.3\n");
            if (sock.waitForReadyRead(800)) {
                if (sock.readAll().contains("HELLO REPLY")) {
                    return true;
                }
            }
        }
        QThread::msleep(250);
        QCoreApplication::processEvents();
    }
    return false;
}

void ChatWindow::play_notify_sound() {
    const QDir exe(QCoreApplication::applicationDirPath());
    const QStringList rels = {
        exe.absoluteFilePath(QStringLiteral("../Resources/sounds/notify.wav")),
        exe.absoluteFilePath(QStringLiteral("../../Resources/sounds/notify.wav")),
        exe.absoluteFilePath(QStringLiteral("../../../../assets/sounds/notify.wav")),
        exe.absoluteFilePath(QStringLiteral("../../../assets/sounds/notify.wav")),
        QStringLiteral(":/i2pchat/sounds/notify.wav"),
    };
    QString path;
    for (const QString& candidate : rels) {
        if (candidate.startsWith(QLatin1Char(':')) && QFile::exists(candidate)) {
            QFile in(candidate);
            if (in.open(QIODevice::ReadOnly)) {
                const QString tmp =
                    QDir::temp().filePath(QStringLiteral("i2pchat-notify.wav"));
                QFile out(tmp);
                if (out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
                    out.write(in.readAll());
                    out.close();
                    path = tmp;
                    break;
                }
            }
        } else if (QFile::exists(candidate)) {
            path = candidate;
            break;
        }
    }
    if (!path.isEmpty()) {
#ifdef Q_OS_MAC
        if (QProcess::startDetached(QStringLiteral("afplay"), {path})) {
            return;
        }
#endif
        if (QProcess::startDetached(QStringLiteral("paplay"), {path}) ||
            QProcess::startDetached(QStringLiteral("aplay"), {path})) {
            return;
        }
    }
    QApplication::beep();
}

void ChatWindow::start_core() {
    apply_router_settings_to_options();
    ensure_bundled_router();
    wait_for_sam_ready(bundled_router_ ? 45000 : 8000);
    runtime::ChatServiceConfig config;
    config.app_root = options_.app_root;
    config.profile = options_.profile;
    config.sam.host = options_.sam_host;
    config.sam.port = options_.sam_port;
    config.retention = load_retention_policy(options_.app_root);

    runtime::ChatEvents events;
    events.on_system = [this](const std::string& message) {
        QMetaObject::invokeMethod(this, [this, message] {
            presentation::ChatLine line;
            line.kind = presentation::LineKind::System;
            line.text = message;
            if (!selected_.empty()) {
                chat_->append(std::move(line));
            }
            refresh_status();
        });
    };
    events.on_error = [this](const std::string& message) {
        QMetaObject::invokeMethod(this, [this, message] {
            const QString text = friendly_error(message);
            presentation::ChatLine line;
            line.kind = presentation::LineKind::Error;
            line.text = text.toStdString();
            if (!selected_.empty()) {
                chat_->append(std::move(line));
            }
            refresh_status();
        });
    };
    events.on_history = [this](const std::string& peer, const storage::HistoryEntry& entry) {
        QMetaObject::invokeMethod(this, [this, peer, entry] {
            const std::string kind = entry.kind;
            if (kind == "in" || kind == "peer") {
                notify_incoming(peer, QString::fromStdString(entry.text));
            }
            if (!history_enabled_) {
                refresh_contacts();
                return;
            }
            if (peer == selected_) {
                chat_->append(presentation::line_from_history(
                    entry, service_ ? service_->contacts() : storage::ContactBook{}, peer));
                chat_view_->scrollToBottom();
                apply_empty_state();
            }
            refresh_contacts();
        });
    };
    events.on_local_address = [this](const std::string& addr) {
        QMetaObject::invokeMethod(this, [this, addr] {
            setWindowTitle(QString("I2PChat @ %1").arg(QString::fromStdString(options_.profile)));
            refresh_status();
        });
        (void)addr;
    };
    events.on_transport_state = [this](session::TransportState state, const std::string& reason) {
        QMetaObject::invokeMethod(this, [this, state, reason] {
            transport_ = state;
            transport_reason_ = reason;
            refresh_status();
            refresh_connection_buttons();
        });
    };
    events.on_peer_state = [this](const std::string&, session::PeerState, const std::string&) {
        QMetaObject::invokeMethod(this, [this] {
            refresh_contacts();
            refresh_status();
            refresh_connection_buttons();
        });
    };
    events.on_contacts_changed = [this] {
        QMetaObject::invokeMethod(this, [this] { refresh_contacts(); });
    };
    events.on_group_message = [this](const std::string& group_id) {
        QMetaObject::invokeMethod(this, [this, group_id] {
            if (group_id != active_group_id_) {
                note_unread(group_id);
            }
            refresh_contacts();
            if (group_id == active_group_id_) {
                reload_selected();
            }
        });
    };
    events.on_trust_prompt = [this](session::TrustPrompt prompt, const std::string& peer,
                                    const std::string& neu, const std::string& old) {
        return on_trust(prompt, peer, neu, old);
    };
    events.on_file_received = [this](const std::string& peer, const std::filesystem::path& path) {
        QMetaObject::invokeMethod(this, [this, peer, path] {
            notify_incoming(peer, tr("File received: %1").arg(QFileInfo(QString::fromStdString(path.string())).fileName()));
        });
    };

    service_ = std::make_unique<runtime::ChatService>(core_.get_executor(), std::move(config),
                                                      std::move(events));
    running_ = true;
    post_core([this]() -> asio::awaitable<void> {
        try {
            co_await service_->start();
            QMetaObject::invokeMethod(this, [this] {
                sync_media_dirs();
                load_compose_drafts();
                sync_compose_draft(compose_draft_key());
                refresh_contacts();
                refresh_status();
                refresh_connection_buttons();
            });
        } catch (const std::exception& error) {
            QMetaObject::invokeMethod(this, [this, message = std::string(error.what())] {
                status_label_->setText(friendly_error(message));
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
    QVector<SidebarRow> groups;
    for (const auto& state : service_->list_groups()) {
        SidebarRow row;
        row.kind = SidebarKind::Group;
        row.addr = QString::fromStdString(state.group_id());
        row.title = state.title().empty() ? row.addr : QString::fromStdString(state.title());
        const int peers = std::max(0, static_cast<int>(state.members().size()) - 1);
        QString preview =
            peers == 0 ? tr("Only you are in this group.")
                       : tr("%1 peer%2").arg(peers).arg(peers == 1 ? QString() : QStringLiteral("s"));
        if (const auto conv = service_->load_group(state.group_id()); conv && !conv->history.empty()) {
            const auto& last = conv->history.back();
            if (!last.text.empty()) {
                preview += QStringLiteral(" · ") + QString::fromStdString(last.text);
            }
        }
        row.subtitle = preview;
        row.selected = state.group_id() == active_group_id_;
        row.unread = unread_[state.group_id()];
        groups.push_back(std::move(row));
    }
    std::vector<std::pair<std::string, unsigned>> unread_pairs(unread_.begin(), unread_.end());
    contacts_model_->set_rows(sidebar_from_contacts(
        presentation::contact_rows(service_->contacts(), service_->connected_peers(), selected_,
                                   unread_pairs),
        groups));
}

void ChatWindow::refresh_status() {
    const bool transient = options_.profile == runtime::kTransientProfile;
    const QString mode = transient ? QStringLiteral("T") : QStringLiteral("P");
    const QString my = service_ && !service_->local_addr().empty()
                           ? QString::fromStdString(presentation::short_address(service_->local_addr()))
                           : QStringLiteral("—");
    const QString peer = selected_.empty()
                             ? QStringLiteral("none")
                             : QString::fromStdString(presentation::short_address(selected_));
    const bool live = service_ && !selected_.empty() && service_->live(selected_);
    const QString chat = live ? tr("Online") : tr("Disconnected");
    const QString delivery = live ? tr("Live") : tr("Will deliver later");
    const QString i2p = i2p_friendly(transport_);
    const QString net = i2p_net_tag(transport_);
    const QString bb = (service_ && service_->blindbox_ready()) ? tr("BlindBox: ready")
                                                               : tr("BlindBox: off");
    const QString send_prefix;
    const QString compact =
        QStringLiteral("%1%2 · %3 · I2P %4 · My:%5").arg(send_prefix, chat, delivery, i2p, my);
    const QString full = QStringLiteral("%1Prof:%2 (%3) | Chat:%4 | Delivery:%5 | My:%6 | Peer:%7 | I2P:%8 | %9")
                             .arg(send_prefix, QString::fromStdString(options_.profile), mode, chat,
                                  delivery, my, peer, net, bb);
    status_full_ = full;
    status_compact_ = compact;
    const int label_w = status_label_->width();
    const bool use_compact = label_w > 0 && label_w < 700;
    const QString raw = use_compact ? compact : full;
    const int available = std::max(40, label_w - 14);
    status_label_->setText(status_label_->fontMetrics().elidedText(raw, Qt::ElideRight, available));
    status_label_->setToolTip(QString());
}

void ChatWindow::refresh_connection_buttons() {
    const bool has_addr = !selected_.empty();
    const bool live = service_ && service_->live(selected_);
    const bool router_up = transport_ == session::TransportState::Ready ||
                           transport_ == session::TransportState::WarmingTunnels ||
                           transport_ == session::TransportState::SamConnected ||
                           transport_ == session::TransportState::Degraded;
    connect_button_->setEnabled(has_addr && !live && router_up);
    disconnect_button_->setEnabled(has_addr && live);
    send_button_->setEnabled(has_addr || !active_group_id_.empty());
}

void ChatWindow::apply_empty_state() {
    auto* stack_host = chat_view_->parentWidget();
    auto* stack = stack_host ? stack_host->findChild<QStackedLayout*>() : nullptr;
    if (stack == nullptr) {
        if (auto* layout = stack_host ? qobject_cast<QStackedLayout*>(stack_host->layout())
                                      : nullptr) {
            stack = layout;
        }
    }
    const bool show_chat =
        (!selected_.empty() || !active_group_id_.empty()) && chat_->rowCount() > 0;
    if (stack != nullptr) {
        stack->setCurrentWidget(show_chat ? static_cast<QWidget*>(chat_view_)
                                          : static_cast<QWidget*>(empty_hint_));
    }
    empty_hint_->setVisible(!show_chat);
}

void ChatWindow::reload_selected() {
    if (!service_) {
        chat_->clear();
        apply_empty_state();
        return;
    }
    if (!active_group_id_.empty()) {
        std::vector<presentation::ChatLine> lines;
        if (const auto conv = service_->load_group(active_group_id_)) {
            for (const auto& entry : conv->history) {
                presentation::ChatLine line;
                line.text = entry.text.empty() && entry.payload.is_string()
                                ? entry.payload.get<std::string>()
                                : entry.text;
                line.time = presentation::format_clock(entry.created_at);
                if (entry.kind == "me") {
                    line.kind = presentation::LineKind::Outgoing;
                    line.author = "you";
                } else if (entry.content_type == groups::ContentType::GroupControl) {
                    line.kind = presentation::LineKind::System;
                } else {
                    line.kind = presentation::LineKind::Incoming;
                    line.author = presentation::short_address(entry.sender_id);
                }
                if (!line.text.empty() || line.kind == presentation::LineKind::System) {
                    lines.push_back(std::move(line));
                }
            }
        }
        chat_->set_lines(std::move(lines));
        chat_view_->scrollToBottom();
        highlight_search();
        apply_empty_state();
        return;
    }
    if (selected_.empty()) {
        chat_->clear();
        apply_empty_state();
        return;
    }
    chat_->set_lines(presentation::lines_from_history(service_->history(selected_),
                                                      service_->contacts(), selected_));
    chat_view_->scrollToBottom();
    highlight_search();
    apply_empty_state();
}

void ChatWindow::contact_activated(const QModelIndex& index) {
    if (!index.isValid() || !contacts_model_->is_conversation(index.row())) {
        return;
    }
    if (contacts_model_->is_group(index.row())) {
        sync_compose_draft(contacts_model_->addr_at(index.row()).toStdString());
        active_group_id_ = contacts_model_->addr_at(index.row()).toStdString();
        clear_unread(active_group_id_);
        selected_.clear();
        addr_edit_->clear();
        reload_selected();
        refresh_status();
        refresh_connection_buttons();
        refresh_contacts();
        return;
    }
    const std::string peer = contacts_model_->addr_at(index.row()).toStdString();
    sync_compose_draft(peer);
    clear_unread(peer);
    active_group_id_.clear();
    selected_ = peer;
    addr_edit_->setText(QString::fromStdString(selected_));
    reload_selected();
    refresh_status();
    refresh_connection_buttons();
    refresh_contacts();
}

void ChatWindow::send_current() {
    const QString text = composer_->toPlainText().trimmed();
    if (text.isEmpty()) {
        return;
    }
    composer_->clear();
    if (compose_draft_active_key_) {
        compose_drafts_.erase(*compose_draft_active_key_);
        schedule_compose_drafts_persist();
    }
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
            addr_edit_->setText(text.mid(9).trimmed());
            connect_selected();
            return;
        }
    }
    if (!active_group_id_.empty()) {
        const std::string group = active_group_id_;
        post_core([this, group, body = text.toStdString()]() -> asio::awaitable<void> {
            co_await service_->send_group_text(group, body);
        });
        return;
    }
    if (selected_.empty()) {
        status_label_->setText(tr("Select a contact or paste an address first."));
        return;
    }
    const std::string peer = selected_;
    post_core([this, peer, body = text.toStdString()]() -> asio::awaitable<void> {
        co_await service_->send_text(peer, body);
    });
}

void ChatWindow::connect_selected() {
    const std::string peer = sam::normalize_peer_address(addr_edit_->text().toStdString());
    if (peer.empty()) {
        return;
    }
    selected_ = peer;
    post_core([this, peer]() -> asio::awaitable<void> {
        const bool ok = co_await service_->connect_peer(peer);
        QMetaObject::invokeMethod(this, [this, peer, ok] {
            if (ok) {
                selected_ = peer;
                addr_edit_->setText(QString::fromStdString(peer));
                refresh_contacts();
                reload_selected();
            }
            refresh_status();
            refresh_connection_buttons();
        });
    });
}

void ChatWindow::disconnect_selected() {
    if (selected_.empty() || !service_) {
        return;
    }
    const std::string peer = selected_;
    post_core([this, peer]() -> asio::awaitable<void> {
        service_->disconnect_peer(peer);
        QMetaObject::invokeMethod(this, [this] {
            refresh_contacts();
            refresh_connection_buttons();
            refresh_status();
        });
        co_return;
    });
}

void ChatWindow::poll_offline() {
    post_core([this]() -> asio::awaitable<void> {
        const std::size_t count = co_await service_->poll_blindbox();
        QMetaObject::invokeMethod(this, [this, count] {
            status_label_->setText(tr("Collected %1 offline message(s).").arg(count));
        });
    });
}

void ChatWindow::copy_address() {
    if (!service_ || service_->local_addr().empty()) {
        status_label_->setText(tr("Address is not ready yet."));
        return;
    }
    qApp->clipboard()->setText(QString::fromStdString(service_->local_addr()));
    status_label_->setText(tr("Address copied."));
}

void ChatWindow::copy_group_invite() { copy_group_invite_of(active_group_id_); }

void ChatWindow::copy_group_invite_of(const std::string& group_id) {
    if (group_id.empty() || service_ == nullptr) {
        status_label_->setText(tr("Select a group first."));
        return;
    }
    post_core([this, group_id]() -> asio::awaitable<void> {
        try {
            const std::string token = service_->encode_group_invite(group_id);
            QMetaObject::invokeMethod(this, [this, token] {
                qApp->clipboard()->setText(QString::fromStdString(token));
                status_label_->setText(tr("Group invite copied."));
            });
        } catch (const std::exception& error) {
            const std::string message = error.what();
            QMetaObject::invokeMethod(this, [this, message] {
                status_label_->setText(QString::fromStdString(message));
            });
        }
        co_return;
    });
}

void ChatWindow::sync_sidebar_toggle_margin() {
    if (right_pack_layout_ == nullptr) {
        return;
    }
    right_pack_layout_->setContentsMargins(sidebar_collapsed_ ? 0 : 4, 0, 0, 0);
}

void ChatWindow::sidebar_context_menu(const QPoint& pos) {
    if (contacts_model_ == nullptr || sidebar_popup_ == nullptr) {
        return;
    }
    const QModelIndex index = contact_view_->indexAt(pos);
    if (!index.isValid() || !contacts_model_->is_conversation(index.row())) {
        return;
    }
    const std::string id = contacts_model_->addr_at(index.row()).toStdString();
    sidebar_popup_->clear_actions();
    sidebar_popup_->set_night(options_.dark);
    if (contacts_model_->is_group(index.row())) {
        sidebar_popup_->add_action(tr("Edit title & members…"), {},
                                   [this, id] { edit_group_dialog(id); },
                                   tr("Change this group's title and members."));
        sidebar_popup_->add_action(tr("Copy invite"), {},
                                   [this, id] { copy_group_invite_of(id); },
                                   tr("Copy a shareable invite string for this group."));
        sidebar_popup_->add_action(tr("Map…"), {}, [this, id] { show_group_map(id); },
                                   tr("Show who is connected live versus waiting on BlindBox."));
        sidebar_popup_->add_action(tr("Delete group…"), {},
                                   [this, id] { confirm_delete_group(id); },
                                   tr("Delete this group and its local history."));
    } else {
        sidebar_popup_->add_action(tr("Edit name & note…"), {},
                                   [this, id] { edit_saved_peer(id); },
                                   tr("Set a local display name and note for this peer."));
        sidebar_popup_->add_action(tr("Contact details…"), {},
                                   [this, id] { show_contact_details(id); },
                                   tr("Show address, TOFU pin, fingerprint, and safety number."));
        sidebar_popup_->add_separator();
        sidebar_popup_->add_action(tr("Remove from saved peers…"), {},
                                   [this, id] { remove_saved_peer(id); },
                                   tr("Remove this peer from Saved peers, with optional history cleanup."));
    }
    sidebar_popup_->show_at(contact_view_->viewport()->mapToGlobal(pos));
}

void ChatWindow::edit_saved_peer(const std::string& addr) {
    if (!service_) {
        return;
    }
    const storage::ContactRecord* rec = service_->contacts().get(addr);
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(tr("Edit name & note"));
    dialog.setMinimumWidth(380);
    auto* form = new QFormLayout(&dialog);
    auto* name = new QLineEdit(&dialog);
    auto* note = new QLineEdit(&dialog);
    name->setText(rec ? QString::fromStdString(rec->display_name) : QString());
    note->setText(rec ? QString::fromStdString(rec->note) : QString());
    name->setPlaceholderText(tr("Optional label in Saved peers list"));
    note->setPlaceholderText(tr("Short note (shown under title when no preview)"));
    form->addRow(tr("Display name"), name);
    form->addRow(tr("Note"), note);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    form->addRow(buttons);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    service_->contacts().remember_peer(addr);
    service_->contacts().set_peer_profile(addr, name->text().toStdString(), note->text().toStdString());
    service_->save_contacts();
    refresh_contacts();
}

void ChatWindow::show_contact_details(const std::string& addr) {
    if (!service_) {
        return;
    }
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(tr("Contact details"));
    dialog.setMinimumWidth(420);
    auto* layout = new QVBoxLayout(&dialog);
    auto* body = new QLabel(&dialog);
    body->setTextInteractionFlags(Qt::TextSelectableByMouse);
    body->setTextFormat(Qt::RichText);
    body->setWordWrap(true);
    QString html = QStringLiteral("<b>Address</b><br>%1").arg(QString::fromStdString(addr).toHtmlEscaped());
    const std::optional<session::TrustPin> pin = service_->trust().pin_for(addr);
    if (pin) {
        const QString key = QString::fromStdString(pin->signing_key_hex);
        const QString short_key =
            key.size() > 48 ? key.left(24) + QStringLiteral("…") + key.right(16) : key;
        const std::string fp_full = presentation::fingerprint_of_signing_key(pin->signing_key_hex);
        const QString fp_short =
            QString::fromStdString(fp_full).left(16);
        const std::string grouped = presentation::group_fingerprint(fp_full);
        QString safety;
        if (const auto peer_key = encoding::hex_decode(pin->signing_key_hex);
            peer_key && !service_->identity().signing_public.empty()) {
            safety = QString::fromStdString(presentation::format_safety_number(
                ByteView(service_->identity().signing_public), ByteView(*peer_key)));
        }
        html += QStringLiteral(
                    "<br><b>TOFU</b>: pinned<br><b>OOB verified:</b> %1<br>"
                    "<b>Fingerprint (short):</b> %2<br>"
                    "<b>Fingerprint (full):</b><br><pre style='margin:4px 0'>%3</pre>")
                    .arg(pin->oob_verified ? tr("yes (confirmed)") : tr("no — compare out-of-band"),
                         fp_short.toHtmlEscaped(),
                         QString::fromStdString(grouped).toHtmlEscaped());
        if (!safety.isEmpty()) {
            html += QStringLiteral("<br><b>Safety number:</b><br><pre style='margin:4px 0'>%1</pre>")
                        .arg(safety.toHtmlEscaped());
        }
        html += QStringLiteral("<b>Signing key (hex, truncated):</b> %1").arg(short_key.toHtmlEscaped());
        body->setText(html);
        layout->addWidget(body);
        auto* row = new QHBoxLayout();
        auto* copy_addr = new QPushButton(tr("Copy address"), &dialog);
        connect(copy_addr, &QPushButton::clicked, this, [addr] {
            qApp->clipboard()->setText(QString::fromStdString(addr));
        });
        row->addWidget(copy_addr);
        if (!fp_full.empty()) {
            auto* copy_fp = new QPushButton(tr("Copy fingerprint"), &dialog);
            connect(copy_fp, &QPushButton::clicked, this, [fp_full] {
                qApp->clipboard()->setText(QString::fromStdString(fp_full));
            });
            row->addWidget(copy_fp);
        }
        if (!safety.isEmpty()) {
            auto* copy_sn = new QPushButton(tr("Copy safety number"), &dialog);
            connect(copy_sn, &QPushButton::clicked, this, [safety] {
                qApp->clipboard()->setText(safety);
            });
            row->addWidget(copy_sn);
        }
        auto* forget = new QPushButton(tr("Remove pin…"), &dialog);
        connect(forget, &QPushButton::clicked, this, [this, addr, &dialog] {
            dialog.accept();
            if (service_->trust().forget(addr)) {
                service_->trust().save();
                status_label_->setText(tr("Forgotten pinned key for this peer."));
            }
        });
        row->addWidget(forget);
        row->addStretch(1);
        auto* close = new QPushButton(tr("Close"), &dialog);
        connect(close, &QPushButton::clicked, &dialog, &QDialog::accept);
        row->addWidget(close);
        layout->addLayout(row);
        dialog.exec();
        return;
    }
    html += QStringLiteral("<br>No TOFU pin stored for this peer.");
    body->setText(html);
    layout->addWidget(body);
    auto* row = new QHBoxLayout();
    auto* copy_addr = new QPushButton(tr("Copy address"), &dialog);
    connect(copy_addr, &QPushButton::clicked, this, [addr] {
        qApp->clipboard()->setText(QString::fromStdString(addr));
    });
    row->addWidget(copy_addr);
    if (pin) {
        auto* forget = new QPushButton(tr("Remove pin…"), &dialog);
        connect(forget, &QPushButton::clicked, this, [this, addr, &dialog] {
            dialog.accept();
            if (service_->trust().forget(addr)) {
                service_->trust().save();
                status_label_->setText(tr("Forgotten pinned key for this peer."));
            }
        });
        row->addWidget(forget);
    }
    row->addStretch(1);
    auto* close = new QPushButton(tr("Close"), &dialog);
    connect(close, &QPushButton::clicked, &dialog, &QDialog::accept);
    row->addWidget(close);
    layout->addLayout(row);
    dialog.exec();
}

void ChatWindow::remove_saved_peer(const std::string& addr) {
    if (!service_) {
        return;
    }
    const auto bb_path = service_->paths().data_dir() /
                         blindbox::peer_state_filename(options_.profile, addr);
    std::error_code exists_ec;
    const bool has_bb = std::filesystem::is_regular_file(bb_path, exists_ec);
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(tr("Remove from saved peers"));
    auto* v = new QVBoxLayout(&dialog);
    v->addWidget(new QLabel(tr("Remove this peer from Saved peers?\n\n%1")
                                .arg(QString::fromStdString(addr)),
                            &dialog));
    auto* cb_history = new QCheckBox(tr("Also delete encrypted chat history for this peer"), &dialog);
    auto* cb_pin = new QCheckBox(tr("Also remove TOFU pin for this peer"), &dialog);
    auto* cb_bb = new QCheckBox(tr("Also remove BlindBox local state file for this peer"), &dialog);
    cb_bb->setVisible(has_bb);
    v->addWidget(cb_history);
    v->addWidget(cb_pin);
    v->addWidget(cb_bb);
    auto* bb = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    bb->button(QDialogButtonBox::Ok)->setText(tr("Remove"));
    bb->button(QDialogButtonBox::Ok)->setObjectName("PrimaryButton");
    bb->button(QDialogButtonBox::Cancel)->setObjectName("SecondaryButton");
    QObject::connect(bb, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(bb, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    add_centered_dialog_buttons(v, bb);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    if (cb_history->isChecked()) {
        storage::delete_history(service_->paths(), addr);
    }
    if (cb_pin->isChecked()) {
        if (service_->trust().forget(addr)) {
            service_->trust().save();
        }
    }
    if (cb_bb->isChecked() && has_bb) {
        std::error_code rm_ec;
        std::filesystem::remove(bb_path, rm_ec);
    }
    service_->contacts().remove_peer(addr);
    service_->save_contacts();
    unread_.erase(addr);
    if (selected_ == addr) {
        selected_.clear();
        addr_edit_->clear();
        reload_selected();
    }
    refresh_contacts();
    refresh_connection_buttons();
    update_tray_unread();
}

void ChatWindow::confirm_delete_group(const std::string& group_id) {
    if (!service_) {
        return;
    }
    if (QMessageBox::question(this, tr("Delete group"),
                              tr("Delete this group and its local history?")) != QMessageBox::Yes) {
        return;
    }
    if (service_->delete_group(group_id)) {
        if (active_group_id_ == group_id) {
            active_group_id_.clear();
            chat_->clear();
            apply_empty_state();
        }
        refresh_contacts();
        refresh_connection_buttons();
    }
}

void ChatWindow::show_group_map(const std::string& group_id) {
    if (!service_) {
        QMessageBox::warning(this, tr("Group map"),
                             tr("Wait for the local I2P session to finish starting, then try again."));
        return;
    }
    const std::optional<groups::StoredConversation> conv = service_->load_group(group_id);
    if (!conv) {
        QMessageBox::warning(this, tr("Group map"), tr("This group no longer exists."));
        refresh_contacts();
        return;
    }
    const std::optional<groups::TopologySnapshot> snapshot = service_->group_topology(group_id);
    if (!snapshot) {
        QMessageBox::warning(this, tr("Group map"), tr("Could not build the local group map."));
        return;
    }
    const QString title = conv->state.title().empty()
                              ? QString::fromStdString(conv->state.group_id())
                              : QString::fromStdString(conv->state.title());
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(tr("Group map: %1").arg(title));
    dialog.resize(760, 620);
    auto* layout = new QVBoxLayout(&dialog);
    layout->setContentsMargins(0, 0, 0, 0);
    auto* visual = new GroupTopologyMapWidget(*snapshot, options_.dark, &dialog);
    layout->addWidget(visual, 1);
    connect(visual, &GroupTopologyMapWidget::peerActivated, this,
            [this, &dialog](const QString& peer) {
                dialog.accept();
                active_group_id_.clear();
                selected_ = peer.toStdString();
                addr_edit_->setText(peer);
                reload_selected();
                refresh_status();
                refresh_connection_buttons();
                refresh_contacts();
            });
    dialog.exec();
}

void ChatWindow::choose_file(bool image) {
    if (selected_.empty()) {
        status_label_->setText(tr("Select a peer first."));
        return;
    }
    const QString path = image ? QFileDialog::getOpenFileName(this, tr("Send picture"))
                               : QFileDialog::getOpenFileName(this, tr("Send file"));
    if (path.isEmpty()) {
        return;
    }
    const std::string peer = selected_;
    post_core([this, peer, path, image]() -> asio::awaitable<void> {
        if (image) {
            co_await service_->send_image(peer, path.toStdString());
        } else {
            co_await service_->send_file(peer, path.toStdString());
        }
    });
}

void ChatWindow::forget_pin() {
    if (!service_ || selected_.empty()) {
        return;
    }
    if (service_->trust().forget(selected_)) {
        service_->trust().save();
        status_label_->setText(tr("Forgotten pinned key for this peer."));
    } else {
        status_label_->setText(tr("No pinned key for this peer."));
    }
}

void ChatWindow::open_app_dir() {
    const QUrl url = QUrl::fromLocalFile(QString::fromStdString(options_.app_root.string()));
    QDesktopServices::openUrl(url);
}

void ChatWindow::restart_i2p_session() {
    flush_compose_drafts();
    stop_core();
    core_.restart();
    service_.reset();
    start_core();
}

void ChatWindow::router_settings() {
    GuiRouterSettings current = load_gui_router_settings(options_.app_root);
    RouterSettingsDialog dialog(
        this, current, bundled_router_status(), options_.dark,
        [this] {
            QDesktopServices::openUrl(QUrl::fromLocalFile(
                QString::fromStdString(router_runtime_dir(options_.app_root).string())));
        },
        [this] {
            const auto data_dir = router_runtime_dir(options_.app_root);
            const auto log = data_dir / "router.log";
            const auto fallback = data_dir / "i2pd.log";
            std::error_code ec;
            const auto path = std::filesystem::is_regular_file(log, ec)
                                  ? log
                                  : (std::filesystem::is_regular_file(fallback, ec) ? fallback
                                                                                    : data_dir);
            QDesktopServices::openUrl(QUrl::fromLocalFile(QString::fromStdString(path.string())));
        },
        [this] {
            bundled_router_.reset();
            const GuiRouterSettings rs = load_gui_router_settings(options_.app_root);
            if (rs.backend != "bundled") {
                return;
            }
            save_gui_router_settings(options_.app_root, rs);
            apply_router_settings_to_options();
            try {
                ensure_bundled_router();
                wait_for_sam_ready(45000);
                restart_i2p_session();
            } catch (const std::exception& error) {
                QMessageBox::warning(this, tr("I2P router"), QString::fromStdString(error.what()));
            }
        });
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    const GuiRouterSettings next = dialog.settings();
    save_gui_router_settings(options_.app_root, next);
    apply_router_settings_to_options();
    bundled_router_.reset();
    if (next.backend == "bundled") {
        try {
            ensure_bundled_router();
        } catch (const std::exception& error) {
            QMessageBox::warning(this, tr("I2P router"), QString::fromStdString(error.what()));
        }
    }
    wait_for_sam_ready(next.backend == "bundled" ? 45000 : 8000);
    restart_i2p_session();
    status_label_->setText(
        tr("I2P router backend applied: %1 (SAM %2:%3)")
            .arg(QString::fromStdString(next.backend),
                 QString::fromStdString(options_.sam_host))
            .arg(options_.sam_port));
}

void ChatWindow::new_group_hint() { edit_group_dialog({}); }

void ChatWindow::edit_group_dialog(const std::string& existing_group_id) {
    if (!service_) {
        return;
    }
    std::optional<groups::StoredConversation> existing;
    if (!existing_group_id.empty()) {
        existing = service_->load_group(existing_group_id);
        if (!existing) {
            status_label_->setText(tr("Unknown group."));
            return;
        }
    }
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(existing ? tr("Edit title & members") : tr("New text group"));
    dialog.setMinimumWidth(420);
    auto* layout = new QVBoxLayout(&dialog);
    layout->addWidget(new QLabel(tr("Title:"), &dialog));
    auto* title = new QLineEdit(&dialog);
    title->setPlaceholderText(tr("Group name"));
    if (existing) {
        title->setText(QString::fromStdString(existing->state.title()));
    }
    layout->addWidget(title);
    layout->addWidget(new QLabel(tr("Members (saved peers):"), &dialog));
    auto* list = new QListWidget(&dialog);
    list->setSelectionMode(QAbstractItemView::NoSelection);
    const auto member_checked = [&](const std::string& addr) {
        if (!existing) {
            return false;
        }
        return existing->state.has_member(addr);
    };
    for (const auto& contact : service_->contacts().contacts()) {
        auto* item = new QListWidgetItem(QString::fromStdString(
            contact.display_name.empty() ? contact.addr
                                         : contact.display_name + " (" + contact.addr + ")"));
        item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
        item->setCheckState(member_checked(contact.addr) ? Qt::Checked : Qt::Unchecked);
        item->setData(Qt::UserRole, QString::fromStdString(contact.addr));
        list->addItem(item);
    }
    layout->addWidget(list, 1);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    std::vector<std::string> members;
    for (int row = 0; row < list->count(); ++row) {
        auto* item = list->item(row);
        if (item->checkState() == Qt::Checked) {
            members.push_back(item->data(Qt::UserRole).toString().toStdString());
        }
    }
    if (members.empty()) {
        QMessageBox::information(&dialog, dialog.windowTitle(),
                                 tr("Add at least one member address."));
        return;
    }
    try {
        const groups::GroupState state =
            existing ? service_->update_group(existing_group_id, title->text().trimmed().toStdString(),
                                              members)
                     : service_->create_group(title->text().trimmed().toStdString(), members);
        active_group_id_ = state.group_id();
        selected_.clear();
        refresh_contacts();
        reload_selected();
        refresh_connection_buttons();
    } catch (const std::exception& error) {
        QMessageBox::warning(this, dialog.windowTitle(), QString::fromStdString(error.what()));
    }
}

void ChatWindow::join_group_hint() {
    if (!service_) {
        QMessageBox::warning(
            this, tr("Join group"),
            tr("Wait for the local I2P session to finish starting, then try again."));
        return;
    }
    QDialog dialog(this);
    apply_dialog_theme(&dialog);
    dialog.setWindowTitle(tr("Join group via invite"));
    dialog.resize(520, 280);
    auto* layout = new QVBoxLayout(&dialog);
    layout->setContentsMargins(16, 16, 16, 16);
    layout->setSpacing(10);
    auto* help = new QLabel(
        tr("Paste a copied group invite token. The string is opaque: "
           "it does not show the group title or member addresses."),
        &dialog);
    help->setWordWrap(true);
    auto* invite_edit = new QPlainTextEdit(&dialog);
    invite_edit->setObjectName("GroupInvitePasteEdit");
    invite_edit->setPlaceholderText(tr("Paste invite token"));
    const QString clip = QApplication::clipboard()->text().trimmed();
    if (groups::looks_like_invite(clip.toStdString())) {
        invite_edit->setPlainText(clip);
    }
    layout->addWidget(help);
    layout->addWidget(invite_edit, 1);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    buttons->button(QDialogButtonBox::Ok)->setText(tr("Join"));
    layout->addWidget(buttons);
    QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, [&] {
        if (invite_edit->toPlainText().trimmed().isEmpty()) {
            QMessageBox::warning(&dialog, tr("Join group"),
                                 tr("Paste a group invite string first."));
            return;
        }
        dialog.accept();
    });
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    invite_edit->setFocus();
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    try {
        const auto state =
            service_->join_group_invite(invite_edit->toPlainText().trimmed().toStdString());
        active_group_id_ = state.group_id();
        selected_.clear();
        refresh_contacts();
        reload_selected();
        refresh_connection_buttons();
        status_label_->setText(
            tr("Joined group: %1")
                .arg(QString::fromStdString(state.title().empty() ? state.group_id()
                                                                  : state.title())));
    } catch (const std::exception& error) {
        QMessageBox::warning(this, tr("Join group"), QString::fromStdString(error.what()));
    }
}

void ChatWindow::load_profile_dat() {
    const QString path = QFileDialog::getOpenFileName(
        this, tr("Select profile (.dat)"), QString::fromStdString(options_.app_root.string()),
        tr("Profile files (*.dat)"));
    if (path.isEmpty()) {
        return;
    }
    const QFileInfo info(path);
    QString target = info.completeBaseName();
    if (!valid_profile_name(target)) {
        QMessageBox::warning(
            this, tr("Load .dat"),
            tr("Invalid profile name in selected file.\nAllowed: a-z A-Z 0-9 . _ - (1..64 chars)."));
        return;
    }
    auto dest_for = [&](const QString& name) {
        return options_.app_root / "profiles" / name.toStdString() / (name.toStdString() + ".dat");
    };
    std::filesystem::path dest = dest_for(target);
    std::error_code ec;
    const auto src_abs = std::filesystem::weakly_canonical(path.toStdString(), ec);
    auto dest_abs = std::filesystem::weakly_canonical(dest, ec);
    if (src_abs != dest_abs && std::filesystem::exists(dest, ec)) {
        int suffix = 2;
        QString renamed = target;
        while (std::filesystem::exists(dest_for(renamed), ec) && suffix < 100) {
            renamed = QString("%1-%2").arg(target).arg(suffix++);
        }
        QMessageBox::information(this, tr("Load .dat"),
                                 tr("Profile '%1' already exists.\nImported as '%2'.")
                                     .arg(target, renamed));
        target = renamed;
        dest = dest_for(target);
    }
    try {
        std::filesystem::create_directories(dest.parent_path());
        dest_abs = std::filesystem::weakly_canonical(dest, ec);
        if (src_abs != dest_abs) {
            std::filesystem::copy_file(path.toStdString(), dest,
                                       std::filesystem::copy_options::overwrite_existing);
        }
    } catch (const std::exception& error) {
        QMessageBox::critical(this, tr("Load .dat"),
                              tr("Could not copy the profile:\n%1").arg(error.what()));
        return;
    }
    flush_compose_drafts();
    compose_drafts_.clear();
    compose_draft_active_key_.reset();
    stop_core();
    core_.restart();
    service_.reset();
    options_.profile = target.toStdString();
    setWindowTitle(QString("I2PChat @ %1").arg(target));
    start_core();
}

void ChatWindow::switch_to_profile(const std::string& name) {
    flush_compose_drafts();
    compose_drafts_.clear();
    compose_draft_active_key_.reset();
    stop_core();
    core_.restart();
    service_.reset();
    options_.profile = name;
    setWindowTitle(QString("I2PChat @ %1").arg(QString::fromStdString(name)));
    start_core();
}

std::optional<std::string> ChatWindow::compose_draft_key() const {
    if (!active_group_id_.empty()) {
        return active_group_id_;
    }
    if (!selected_.empty()) {
        return selected_;
    }
    return std::nullopt;
}

void ChatWindow::load_compose_drafts() {
    compose_drafts_.clear();
    if (!service_ || options_.profile == runtime::kTransientProfile) {
        return;
    }
    compose_drafts_ = storage::load_compose_drafts(service_->paths().compose_drafts(),
                                                   ByteView(service_->identity().identity_key));
}

void ChatWindow::flush_compose_drafts() {
    if (compose_drafts_timer_ != nullptr) {
        compose_drafts_timer_->stop();
    }
    if (compose_draft_active_key_ && composer_ != nullptr) {
        compose_drafts_[*compose_draft_active_key_] = composer_->toPlainText().toStdString();
    }
    if (!service_ || options_.profile == runtime::kTransientProfile) {
        return;
    }
    while (static_cast<int>(compose_drafts_.size()) > kComposeDraftsMaxKeys) {
        compose_drafts_.erase(compose_drafts_.begin());
    }
    try {
        storage::save_compose_drafts(service_->paths().compose_drafts(), compose_drafts_,
                                     ByteView(service_->identity().identity_key));
    } catch (const std::exception&) {
    }
}

void ChatWindow::schedule_compose_drafts_persist() {
    if (compose_drafts_timer_ != nullptr) {
        compose_drafts_timer_->start();
    }
}

void ChatWindow::sync_compose_draft(const std::optional<std::string>& new_key) {
    if (composer_ == nullptr) {
        return;
    }
    auto [active, text, out] = apply_compose_draft_peer_switch(
        compose_draft_active_key_, new_key, composer_->toPlainText().toStdString(),
        compose_drafts_);
    compose_drafts_ = std::move(out);
    compose_draft_active_key_ = std::move(active);
    const QSignalBlocker blocker(composer_);
    composer_->setPlainText(QString::fromStdString(text));
    schedule_compose_drafts_persist();
}

void ChatWindow::show_blindbox_diagnostics() {
    QDialog dlg(this);
    apply_dialog_theme(&dlg);
    dlg.setWindowTitle(tr("BlindBox diagnostics"));
    dlg.resize(720, 580);
    auto* layout = new QVBoxLayout(&dlg);
    const bool bb_on = service_ && service_->blindbox_enabled();
    const bool locked = service_ && service_->replica_settings().auth_locked;
    auto* intro = new QLabel(
        tr("Diagnostics for offline / delayed delivery. ") +
            (locked ? tr("Replica endpoints are read-only until replica tokens can be decrypted.")
                    : (bb_on ? tr("You can edit Blind Box endpoints below when BlindBox is enabled "
                                  "for this profile.")
                             : tr("BlindBox is off for this profile; endpoint list is shown for "
                                  "reference only."))),
        &dlg);
    intro->setWordWrap(true);
    layout->addWidget(intro);
    auto* summary = new QPlainTextEdit(&dlg);
    summary->setObjectName("BlindBoxDiagnosticsSummary");
    summary->setReadOnly(true);
    QString text;
    const QString peer_label =
        !selected_.empty() ? QString::fromStdString(selected_)
                           : (!active_group_id_.empty() ? QString::fromStdString(active_group_id_)
                                                        : QStringLiteral("—"));
    text += tr("Profile: %1\n").arg(QString::fromStdString(options_.profile));
    text += tr("Selected peer: %1\n").arg(peer_label);
    text += tr("BlindBox enabled: %1\n").arg(bb_on ? tr("yes") : tr("no"));
    text += tr("BlindBox ready: %1\n")
                .arg(service_ && service_->blindbox_ready() ? tr("yes") : tr("no"));
    QString delivery_state = QStringLiteral("unknown");
    QString status_title = tr("Status unknown");
    QStringList details;
    QStringList actions;
    if (!bb_on) {
        delivery_state = QStringLiteral("blindbox-disabled");
        status_title = tr("Offline queue is disabled by configuration");
        details << tr("BlindBox is currently turned off for this profile or deployment.");
        actions << tr("Enable BlindBox or use a live connection instead.");
    } else if (service_ && service_->replica_settings().endpoints.empty()) {
        delivery_state = QStringLiteral("blindbox-needs-boxes");
        status_title = tr("No BlindBox replicas are configured");
        details << tr("Delayed delivery cannot work until at least one replica endpoint is configured.");
        actions << tr("Add replicas in this dialog.");
    } else if (!selected_.empty() && service_ && service_->live(selected_)) {
        delivery_state = QStringLiteral("online-live");
        status_title = tr("Live secure session is active");
        details << tr("Messages can be delivered immediately.")
                << tr("Offline delivery remains available as a fallback when configured.");
        actions << tr("Send text, images, or files normally.") << tr("No fix is required.");
    } else if (!selected_.empty() && service_ && service_->peer_offline_ready(selected_)) {
        delivery_state = QStringLiteral("offline-ready");
        status_title = tr("Offline queue is ready");
        details << tr("You can send text now without a live secure session.")
                << tr("Live connect is optional right now.");
        actions << tr("Send text now — it will be queued for delayed delivery.")
                << tr("No fix is required.");
    } else if (!selected_.empty() && service_) {
        delivery_state = QStringLiteral("await-live-root");
        status_title = tr("Offline queue is not ready yet");
        details << tr("BlindBox is configured, but the first offline key exchange is still missing.")
                << tr("One successful live secure chat is required before delayed delivery can start.");
        actions << tr("Press Connect once and complete one secure live session with this peer.");
    }
    text += tr("Delivery state: %1\n").arg(delivery_state);
    text += tr("\n%1\n").arg(status_title);
    if (!details.isEmpty()) {
        text += tr("\nDetails:\n");
        for (const QString& line : details) {
            text += QStringLiteral("- %1\n").arg(line);
        }
    }
    if (!actions.isEmpty()) {
        text += tr("\nWhat to do:\n");
        for (const QString& line : actions) {
            text += QStringLiteral("- %1\n").arg(line);
        }
    }
    if (service_) {
        text += tr("Replicas file:\n%1\n")
                    .arg(QString::fromStdString(service_->paths().blindbox_replicas().string()));
        text += tr("Endpoint count: %1\n")
                    .arg(static_cast<int>(service_->replica_settings().endpoints.size()));
        if (locked) {
            text += tr("Auth tokens: locked (could not decrypt)\n");
        } else {
            text += tr("Auth tokens: %1\n")
                        .arg(static_cast<int>(service_->replica_settings().auth.size()));
        }
    }
    summary->setPlainText(text);
    layout->addWidget(summary, 1);
    layout->addWidget(new QLabel(tr("Blind Box endpoints (one per line, e.g. *.b32.i2p:19444):"),
                                 &dlg));
    auto* replica_edit = new QPlainTextEdit(&dlg);
    replica_edit->setObjectName("BlindBoxReplicaEndpointsEdit");
    QStringList lines;
    if (service_) {
        for (const auto& ep : service_->replica_settings().endpoints) {
            lines.push_back(QString::fromStdString(ep));
        }
    }
    replica_edit->setPlainText(lines.join('\n'));
    const bool can_edit = bb_on && !locked && service_;
    replica_edit->setReadOnly(!can_edit);
    const QFontMetrics fm = replica_edit->fontMetrics();
    replica_edit->setMinimumHeight(fm.lineSpacing() * 2 + 20);
    replica_edit->setMaximumHeight(fm.lineSpacing() * 5 + 24);
    layout->addWidget(replica_edit);
    layout->addWidget(new QLabel(tr("Replica auth (optional): one line per replica — endpoint, then "
                                    "Tab, then token."),
                                 &dlg));
    auto* auth_edit = new QPlainTextEdit(&dlg);
    QStringList auth_lines;
    if (service_ && !locked) {
        for (const auto& [ep, tok] : service_->replica_settings().auth) {
            auth_lines.push_back(QString::fromStdString(ep) + QChar('\t') +
                                 QString::fromStdString(tok));
        }
    }
    auth_edit->setPlainText(auth_lines.join('\n'));
    auth_edit->setReadOnly(!can_edit);
    auth_edit->setMaximumHeight(fm.lineSpacing() * 4 + 20);
    layout->addWidget(auth_edit);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dlg);
    auto* example = buttons->addButton(tr("Example server…"), QDialogButtonBox::ActionRole);
    example->setToolTip(tr("Show install.sh and i2pd tunnel snippets for a BlindBox replica."));
    auto* save = buttons->addButton(tr("Save endpoints"), QDialogButtonBox::AcceptRole);
    save->setObjectName("PrimaryButton");
    save->setEnabled(can_edit);
    buttons->button(QDialogButtonBox::Close)->setObjectName("SecondaryButton");
    layout->addWidget(buttons);
    QObject::connect(example, &QPushButton::clicked, &dlg, [this, &dlg] {
        show_blindbox_setup_examples(&dlg);
    });
    QObject::connect(save, &QPushButton::clicked, &dlg, [&] {
        if (!service_ || !can_edit) {
            return;
        }
        storage::ReplicaSettings next;
        for (const QString& line : replica_edit->toPlainText().split('\n')) {
            const QString trimmed = line.trimmed();
            if (trimmed.isEmpty() || trimmed.startsWith('#')) {
                continue;
            }
            next.endpoints.push_back(trimmed.toStdString());
        }
        for (const QString& line : auth_edit->toPlainText().split('\n')) {
            const QString trimmed = line.trimmed();
            if (trimmed.isEmpty() || trimmed.startsWith('#') || !trimmed.contains('\t')) {
                continue;
            }
            const auto tab = trimmed.indexOf('\t');
            if (tab < 0) {
                continue;
            }
            const std::string ep = trimmed.left(tab).trimmed().toStdString();
            const std::string tok = trimmed.mid(tab + 1).trimmed().toStdString();
            if (!ep.empty() && !tok.empty()) {
                next.auth[ep] = tok;
            }
        }
        try {
            service_->save_replica_settings(std::move(next));
            dlg.accept();
            status_label_->setText(tr("BlindBox replica endpoints saved."));
        } catch (const std::exception& error) {
            QMessageBox::warning(&dlg, tr("BlindBox diagnostics"),
                                 QString::fromStdString(error.what()));
        }
    });
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dlg, &QDialog::reject);
    dlg.exec();
}

void ChatWindow::show_blindbox_setup_examples(QWidget* parent) {
    constexpr const char* kCurl =
        "curl -fsSL https://raw.githubusercontent.com/MetanoicArmor/I2PChat/main/"
        "i2pchat/blindbox/daemon/install/install.sh -o install.sh && sudo bash install.sh";
    constexpr const char* kI2pd = "# /etc/i2pd/tunnels.conf (merge + restart i2pd; set keys= to your .dat)\n"
                                  "\n"
                                  "[blindbox]\n"
                                  "type = server\n"
                                  "host = 127.0.0.1\n"
                                  "port = 19444\n"
                                  "keys = blindbox.dat\n"
                                  "inport = 19444\n";
    QFile install_file(QStringLiteral(":/i2pchat/blindbox/install.sh"));
    QString install_text;
    if (install_file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        install_text = QString::fromUtf8(install_file.readAll());
    } else {
        install_text = tr("# Example file install.sh not found in the application bundle.\n");
    }

    QDialog sub(parent != nullptr ? parent : this);
    apply_dialog_theme(&sub);
    sub.setWindowTitle(tr("Blind Box setup examples"));
    sub.resize(680, 520);
    auto* v = new QVBoxLayout(&sub);
    auto* tabs = new QTabWidget(&sub);
    tabs->setObjectName("BlindBoxExampleTabWidget");
    tabs->setDocumentMode(true);
    tabs->tabBar()->setUsesScrollButtons(false);
    tabs->tabBar()->setExpanding(false);
    tabs->tabBar()->setDrawBase(false);

    auto make_page = [&](const QString& note, const QString& body) {
        auto* page = new QWidget(&sub);
        auto* pl = new QVBoxLayout(page);
        pl->setContentsMargins(8, 8, 8, 8);
        auto* hl = new QLabel(note, page);
        hl->setTextFormat(Qt::RichText);
        hl->setWordWrap(true);
        hl->setOpenExternalLinks(false);
        pl->addWidget(hl);
        auto* te = new QPlainTextEdit(page);
        te->setObjectName("BlindBoxExampleSourceEdit");
        te->setReadOnly(true);
        te->setPlainText(body);
        te->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
        pl->addWidget(te, 1);
        return std::pair<QWidget*, QPlainTextEdit*>{page, te};
    };

    const auto install_page = make_page(
        tr("<b>Production daemon package:</b> this is the supported package-local deployment path. "
           "Use <code>python3 -m i2pchat.blindbox.daemon</code> or point <code>systemd</code> to the "
           "same module. The package bundles a dedicated <code>systemd</code> unit, env example, and "
           "matching fail2ban assets, plus install/package helper scripts. "
           "If you want a single downloaded server installer, use <code>install.sh</code>."),
        install_text);
    tabs->addTab(install_page.first, QStringLiteral("install.sh"));
    const auto i2p_page = make_page(
        tr("<b>Merge into <code>tunnels.conf</code>, restart i2pd.</b> "
           "I2P traffic hits <code>127.0.0.1:19444</code> where the replica listens. "
           "Use this tunnel&apos;s <code>*.b32.i2p:19444</code> in I2PChat (Blind Box diagnostics)."),
        QString::fromUtf8(kI2pd));
    tabs->addTab(i2p_page.first, QStringLiteral("I2pd"));
    v->addWidget(tabs, 1);

    auto* brow = new QHBoxLayout();
    brow->setContentsMargins(8, 0, 8, 0);
    auto* get_install = new QPushButton(tr("Get install"), &sub);
    get_install->setToolTip(
        tr("Save the one-shot install.sh locally so you can copy it to a server and run it there."));
    QObject::connect(get_install, &QPushButton::clicked, &sub, [&] {
        const QString path = QFileDialog::getSaveFileName(
            &sub, tr("Save BlindBox install.sh"),
            QDir::homePath() + QStringLiteral("/install.sh"),
            tr("Shell script (*.sh);;All Files (*)"));
        if (path.isEmpty()) {
            return;
        }
        QFile out(path);
        if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
            QMessageBox::critical(&sub, tr("Get install"), out.errorString());
            return;
        }
        out.write(install_text.toUtf8());
        out.close();
        out.setPermissions(out.permissions() | QFileDevice::ExeOwner | QFileDevice::ExeUser |
                           QFileDevice::ExeGroup | QFileDevice::ExeOther);
        QMessageBox::information(
            &sub, tr("Get install"),
            tr("Saved install.sh to:\n%1\n\nCopy it to your server and run:\n\nsudo bash install.sh")
                .arg(path));
    });
    auto* copy_curl = new QPushButton(tr("Copy curl"), &sub);
    copy_curl->setToolTip(
        tr("Copy the one-liner: download install.sh from GitHub and run it on the server."));
    QObject::connect(copy_curl, &QPushButton::clicked, &sub, [kCurl] {
        qApp->clipboard()->setText(QString::fromUtf8(kCurl));
    });
    auto* copy_all = new QPushButton(tr("Copy all"), &sub);
    QObject::connect(copy_all, &QPushButton::clicked, &sub, [tabs, install_page, i2p_page] {
        const auto* edit = tabs->currentIndex() == 0 ? install_page.second : i2p_page.second;
        qApp->clipboard()->setText(edit->toPlainText());
    });
    auto* close_sub = new QPushButton(tr("Close"), &sub);
    QObject::connect(close_sub, &QPushButton::clicked, &sub, &QDialog::accept);
    brow->addStretch(1);
    brow->addWidget(get_install);
    brow->addWidget(copy_curl);
    brow->addWidget(copy_all);
    brow->addWidget(close_sub);
    v->addLayout(brow);
    sub.exec();
}

void ChatWindow::export_profile_backup() {
    if (!service_) {
        return;
    }
    const QString dest = QFileDialog::getSaveFileName(
        this, tr("Export profile backup"),
        QString::fromStdString(
            (options_.app_root / (options_.profile + ".i2pchat-profile-backup")).string()),
        tr("I2PChat backup (*.i2pchat-profile-backup);;All Files (*)"));
    if (dest.isEmpty()) {
        return;
    }
    const std::optional<QString> passphrase =
        prompt_backup_passphrase(this, tr("Export profile backup"), true);
    if (!passphrase) {
        return;
    }
    try {
        const auto summary = storage::export_profile_bundle(
            dest.toStdString(), options_.app_root, options_.profile, passphrase->toStdString(),
            true);
        status_label_->setText(tr("Profile backup exported: %1 file(s), %2 history file(s).")
                                   .arg(summary.file_count)
                                   .arg(summary.history_files));
    } catch (const std::exception& error) {
        QMessageBox::critical(this, tr("Export profile backup"),
                              QString::fromStdString(error.what()));
    }
}

void ChatWindow::import_profile_backup() {
    const QString src = QFileDialog::getOpenFileName(
        this, tr("Import profile backup"), QString::fromStdString(options_.app_root.string()),
        tr("I2PChat backup (*.i2pchat-profile-backup);;All Files (*)"));
    if (src.isEmpty()) {
        return;
    }
    const std::optional<QString> passphrase =
        prompt_backup_passphrase(this, tr("Import profile backup"), false);
    if (!passphrase) {
        return;
    }
    try {
        const auto summary = storage::import_profile_bundle(
            src.toStdString(), options_.app_root, passphrase->toStdString());
        status_label_->setText(tr("Profile backup imported as '%1' (%2 file(s)).")
                                   .arg(QString::fromStdString(summary.target_profile))
                                   .arg(summary.restored_files));
        switch_to_profile(summary.target_profile);
    } catch (const std::exception& error) {
        QMessageBox::critical(this, tr("Import profile backup"),
                              QString::fromStdString(error.what()));
    }
}

void ChatWindow::export_history_backup() {
    if (!service_) {
        return;
    }
    if (storage::list_history_files(service_->paths()).empty()) {
        QMessageBox::information(this, tr("Export history backup"),
                                 tr("No saved history files were found for the current profile."));
        return;
    }
    const QString dest = QFileDialog::getSaveFileName(
        this, tr("Export history backup"),
        QString::fromStdString(
            (options_.app_root / (options_.profile + ".i2pchat-history-backup")).string()),
        tr("I2PChat history backup (*.i2pchat-history-backup);;All Files (*)"));
    if (dest.isEmpty()) {
        return;
    }
    const std::optional<QString> passphrase =
        prompt_backup_passphrase(this, tr("Export history backup"), true);
    if (!passphrase) {
        return;
    }
    try {
        const auto summary = storage::export_history_bundle(
            dest.toStdString(), options_.app_root, options_.profile, passphrase->toStdString());
        status_label_->setText(
            tr("History backup exported: %1 history file(s).").arg(summary.history_files));
    } catch (const std::exception& error) {
        QMessageBox::critical(this, tr("Export history backup"),
                              QString::fromStdString(error.what()));
    }
}

void ChatWindow::import_history_backup() {
    const QString src = QFileDialog::getOpenFileName(
        this, tr("Import history backup"), QString::fromStdString(options_.app_root.string()),
        tr("I2PChat history backup (*.i2pchat-history-backup);;All Files (*)"));
    if (src.isEmpty()) {
        return;
    }
    const std::optional<QString> passphrase =
        prompt_backup_passphrase(this, tr("Import history backup"), false);
    if (!passphrase) {
        return;
    }
    const auto overwrite = QMessageBox::question(
        this, tr("Import history backup"),
        tr("Overwrite existing history files for matching peers?\n\n"
           "Choose Yes to overwrite, No to keep existing files and import only missing ones."),
        QMessageBox::Yes | QMessageBox::No | QMessageBox::Cancel, QMessageBox::No);
    if (overwrite == QMessageBox::Cancel) {
        return;
    }
    try {
        const auto summary = storage::import_history_bundle(
            src.toStdString(), options_.app_root, options_.profile, passphrase->toStdString(),
            overwrite == QMessageBox::Yes);
        status_label_->setText(tr("History backup imported: %1 restored, %2 skipped.")
                                   .arg(summary.restored_files)
                                   .arg(summary.skipped_files));
        reload_selected();
    } catch (const std::exception& error) {
        QMessageBox::critical(this, tr("Import history backup"),
                              QString::fromStdString(error.what()));
    }
}

void ChatWindow::check_for_updates() {
    if (update_reply_ != nullptr) {
        return;
    }
    const QString custom_url = qEnvironmentVariable("I2PCHAT_RELEASES_PAGE_URL").trimmed();
    const QString custom_proxy = qEnvironmentVariable("I2PCHAT_UPDATE_HTTP_PROXY").trimmed();
    QSettings settings;
    const bool need_url_ack =
        !custom_url.isEmpty() && !settings.value(QStringLiteral("releasesCustomUrlAck")).toBool();
    const bool need_proxy_ack =
        !custom_proxy.isEmpty() && !settings.value(QStringLiteral("releasesCustomProxyAck")).toBool();
    if (need_url_ack || need_proxy_ack) {
        QStringList parts;
        if (need_url_ack) {
            parts << tr("I2PCHAT_RELEASES_PAGE_URL is set. The update check trusts whatever that "
                        "server returns over HTTP. Only use URLs you fully trust.");
        }
        if (need_proxy_ack) {
            parts << tr("I2PCHAT_UPDATE_HTTP_PROXY is set. Update requests go through that proxy; "
                        "use only proxies you trust.");
        }
        parts << tr("See the user manual §4.12 (Verifying downloads).");
        if (QMessageBox::warning(this, tr("Update check overrides"), parts.join("\n\n"),
                                 QMessageBox::Ok | QMessageBox::Cancel,
                                 QMessageBox::Ok) != QMessageBox::Ok) {
            return;
        }
        if (need_url_ack) {
            settings.setValue(QStringLiteral("releasesCustomUrlAck"), true);
        }
        if (need_proxy_ack) {
            settings.setValue(QStringLiteral("releasesCustomProxyAck"), true);
        }
    }

    const QUrl url(QString::fromStdString(updates::releases_page_url()));
    if (update_nam_ == nullptr) {
        update_nam_ = new QNetworkAccessManager(this);
    }
    QNetworkProxy proxy = QNetworkProxy::NoProxy;
    const QString low = custom_proxy.toLower();
    const bool i2p_host = url.host().endsWith(QStringLiteral(".i2p"));
    if (low == "0" || low == "none" || low == "off" || low == "direct" || low == "false") {
        proxy = QNetworkProxy::NoProxy;
    } else if (!custom_proxy.isEmpty()) {
        const QUrl proxy_url(custom_proxy);
        proxy = QNetworkProxy(QNetworkProxy::HttpProxy, proxy_url.host(),
                              static_cast<quint16>(proxy_url.port(4444)));
        if (i2p_host && proxy.hostName() != "127.0.0.1" && proxy.hostName() != "localhost" &&
            proxy.hostName() != "::1") {
            QMessageBox::warning(this, tr("Check for updates"),
                                 tr("Refusing to fetch a .i2p update page through a non-loopback "
                                    "proxy (would leak the lookup to the clearnet)."));
            return;
        }
    } else if (i2p_host) {
        proxy = QNetworkProxy(QNetworkProxy::HttpProxy, QStringLiteral("127.0.0.1"), 4444);
    }
    update_nam_->setProxy(proxy);
    QNetworkRequest request(url);
    request.setRawHeader("User-Agent", "I2PChat-update-check/1.0");
    request.setTransferTimeout(45000);
    update_reply_ = update_nam_->get(request);
    connect(update_reply_, &QNetworkReply::finished, this, &ChatWindow::on_update_check_finished);
    status_label_->setText(tr("Checking for updates…"));
}

void ChatWindow::on_update_check_finished() {
    QNetworkReply* reply = update_reply_;
    update_reply_ = nullptr;
    if (reply == nullptr) {
        return;
    }
    reply->deleteLater();
    updates::UpdateCheckResult result;
    if (reply->error() != QNetworkReply::NoError) {
        result.ok = false;
        result.kind = "network";
        result.message =
            "Could not reach the release page (" + reply->errorString().toStdString() +
            "). Ensure I2P is running. If the HTTP proxy is not on 127.0.0.1:4444, set "
            "http_proxy or I2PCHAT_UPDATE_HTTP_PROXY to your I2P HTTP proxy URL.";
    } else {
        const QByteArray body = reply->readAll();
        result = updates::check_for_updates_from_html(
            QCoreApplication::applicationVersion().toStdString(), body.toStdString());
    }
    QString display = QString::fromStdString(result.message);
    if (result.ok && result.kind == "update_available") {
        display += tr("\n\nBefore installing a build you download: verify SHA256SUMS and the GPG "
                      "detached signature. The in-app check only compares version numbers parsed "
                      "from the release page HTML (see manual §4.12).");
    } else if (result.ok && result.kind == "no_artifact") {
        display += tr("\n\nIf you download a build manually, verify SHA256SUMS and GPG "
                      "(manual §4.12).");
    }
    QMessageBox mb(this);
    mb.setWindowTitle(tr("Check for updates"));
    mb.setText(display);
    mb.setIcon(result.ok ? QMessageBox::Information : QMessageBox::Warning);
    QAbstractButton* open_btn = nullptr;
    if (result.kind == "update_available" || result.kind == "no_artifact" || !result.ok) {
        open_btn = mb.addButton(tr("Open downloads page"), QMessageBox::ActionRole);
    }
    mb.addButton(QMessageBox::Ok);
    mb.exec();
    if (open_btn != nullptr && mb.clickedButton() == open_btn) {
        QDesktopServices::openUrl(QUrl(QString::fromStdString(updates::downloads_page_url())));
    }
}

void ChatWindow::clear_history() {
    if (!service_ || selected_.empty()) {
        return;
    }
    if (QMessageBox::question(this, tr("Clear history"),
                              tr("Delete stored history for this peer?")) != QMessageBox::Yes) {
        return;
    }
    storage::delete_history(service_->paths(), selected_);
    chat_->clear();
    apply_empty_state();
}

void ChatWindow::configure_history_retention() {
    const storage::RetentionPolicy shown = load_retention_policy(options_.app_root);
    QDialog dialog(this);
    apply_dialog_theme(&dialog, options_.dark);
    dialog.setWindowTitle(tr("History retention"));
    dialog.setModal(true);
    auto* v = new QVBoxLayout(&dialog);
    v->setContentsMargins(20, 16, 20, 16);
    v->setSpacing(14);
    auto* form = new QFormLayout();
    form->setHorizontalSpacing(14);
    form->setVerticalSpacing(12);
    form->setRowWrapPolicy(QFormLayout::DontWrapRows);
    form->setFieldGrowthPolicy(QFormLayout::FieldsStayAtSizeHint);
    form->setLabelAlignment(Qt::AlignLeft | Qt::AlignTop);
    auto* sp_messages = new QSpinBox(&dialog);
    sp_messages->setRange(1, 100000);
    sp_messages->setSingleStep(50);
    sp_messages->setValue(static_cast<int>(std::min<std::size_t>(shown.max_messages, 100000)));
    auto* sp_days = new QSpinBox(&dialog);
    sp_days->setRange(0, 3650);
    sp_days->setSingleStep(1);
    sp_days->setValue(static_cast<int>(shown.max_age_days));
    form->addRow(history_field_label_block(tr("Max saved messages per peer"),
                                           tr("Older entries are dropped when this count is exceeded."),
                                           &dialog),
                 wrap_history_numeric_row(sp_messages));
    form->addRow(history_field_label_block(tr("Max age in days"),
                                           tr("0 = keep only by message count above (ignore age)."),
                                           &dialog),
                 wrap_history_numeric_row(sp_days));
    v->addLayout(form);
    auto* bb = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    bb->button(QDialogButtonBox::Ok)->setObjectName("PrimaryButton");
    bb->button(QDialogButtonBox::Cancel)->setObjectName("SecondaryButton");
    QObject::connect(bb, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(bb, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    add_centered_dialog_buttons(v, bb);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    nlohmann::json data = load_ui_prefs(options_.app_root);
    data["history_max_messages"] = sp_messages->value();
    data["history_retention_days"] = sp_days->value();
    save_ui_prefs(options_.app_root, data);
    storage::RetentionPolicy policy;
    policy.max_messages = static_cast<std::size_t>(sp_messages->value());
    policy.max_age_days = static_cast<unsigned>(sp_days->value());
    if (service_) {
        service_->set_retention(policy);
    }
    status_label_->setText(tr("History retention updated: %1 messages per peer, %2 day(s) max age.")
                               .arg(sp_messages->value())
                               .arg(sp_days->value()));
}

void ChatWindow::search_changed(const QString& text) {
    search_hits_ = chat_->match_rows(text);
    search_cur_ = search_hits_.isEmpty() ? -1 : 0;
    rebuild_search_console();
    highlight_search();
}

void ChatWindow::rebuild_search_console() {
    if (search_console_ == nullptr || search_hits_layout_ == nullptr) {
        return;
    }
    while (QLayoutItem* item = search_hits_layout_->takeAt(0)) {
        if (item->widget()) {
            item->widget()->deleteLater();
        }
        delete item;
    }
    const QString query = search_edit_->text().trimmed();
    if (query.isEmpty() || search_hits_.isEmpty()) {
        search_console_->hide();
        search_console_->setMaximumHeight(0);
        return;
    }
    for (int i = 0; i < search_hits_.size(); ++i) {
        const int row = search_hits_.at(i);
        const QString text = chat_->index(row, 0).data(ChatModel::TextRole).toString().simplified();
        auto* btn = new QPushButton(text.left(120), search_console_);
        btn->setObjectName("ChatSearchHitButton");
        btn->setCursor(Qt::PointingHandCursor);
        btn->setFlat(true);
        QObject::connect(btn, &QPushButton::clicked, this, [this, i] {
            search_cur_ = i;
            highlight_search();
        });
        search_hits_layout_->addWidget(btn);
    }
    search_hits_layout_->addStretch(1);
    search_console_->show();
    search_console_->setMaximumHeight(128);
}

void ChatWindow::notify_incoming(const std::string& peer, const QString& preview) {
    if (peer != selected_) {
        note_unread(peer);
        refresh_contacts();
    }
    const bool focused = isActiveWindow() && peer == selected_;
    if (privacy_mode_ && isActiveWindow()) {
        return;
    }
    if (focused) {
        return;
    }
    if (tray_) {
        const QString body = privacy_mode_ ? tr("New message") : preview.left(180);
        tray_->showMessage(tr("I2PChat"), body);
    }
    if (notify_sound_) {
        play_notify_sound();
    }
}

void ChatWindow::sync_media_dirs() {
    if (!chat_delegate_ || !service_) {
        return;
    }
    chat_delegate_->set_media_dirs(
        QString::fromStdString((service_->paths().data_dir() / "images").string()),
        QString::fromStdString((service_->paths().data_dir() / "downloads").string()));
}

bool ChatWindow::eventFilter(QObject* watched, QEvent* event) {
    if (emoji_button_ != nullptr && watched == emoji_button_->parent() &&
        event->type() == QEvent::Resize) {
        position_emoji_button();
    }
    if (watched == emoji_button_) {
        if (event->type() == QEvent::Enter && emoji_hover_open_ != nullptr) {
            if (emoji_hover_close_ != nullptr) {
                emoji_hover_close_->stop();
            }
            if (emoji_popup_ == nullptr || !emoji_popup_->isVisible()) {
                emoji_hover_open_->start(120);
            }
        } else if (event->type() == QEvent::Leave && emoji_hover_close_ != nullptr) {
            if (emoji_hover_open_ != nullptr) {
                emoji_hover_open_->stop();
            }
            if (emoji_popup_ != nullptr && emoji_popup_->isVisible()) {
                emoji_hover_close_->start(180);
            }
        }
    }
    if (watched == emoji_popup_) {
        if (event->type() == QEvent::Enter && emoji_hover_close_ != nullptr) {
            emoji_hover_close_->stop();
        } else if (event->type() == QEvent::Leave && emoji_hover_close_ != nullptr) {
            emoji_hover_close_->start(180);
        } else if (event->type() == QEvent::Hide) {
            if (emoji_hover_open_ != nullptr) {
                emoji_hover_open_->stop();
            }
            if (emoji_hover_close_ != nullptr) {
                emoji_hover_close_->stop();
            }
        }
    }
    if (watched == composer_ && event->type() == QEvent::KeyPress) {
        const auto* key = static_cast<QKeyEvent*>(event);
        if (key->key() == Qt::Key_Return || key->key() == Qt::Key_Enter) {
            const Qt::KeyboardModifiers mods = key->modifiers();
            const bool shift = mods.testFlag(Qt::ShiftModifier);
            const bool command_like =
                mods.testFlag(Qt::ControlModifier) || mods.testFlag(Qt::MetaModifier);
            const bool wants_send = enter_sends_ ? !shift : command_like;
            if (wants_send) {
                send_current();
                return true;
            }
            return QMainWindow::eventFilter(watched, event);
        }
        if (key->matches(QKeySequence::Paste) && QApplication::clipboard()->mimeData() &&
            QApplication::clipboard()->mimeData()->hasImage() && service_ && !selected_.empty()) {
            const QImage image = qvariant_cast<QImage>(QApplication::clipboard()->mimeData()->imageData());
            if (!image.isNull()) {
                const auto dir = service_->paths().data_dir() / "images";
                std::error_code ec;
                std::filesystem::create_directories(dir, ec);
                const auto path =
                    dir / ("paste-" + std::to_string(QDateTime::currentMSecsSinceEpoch()) + ".png");
                if (image.save(QString::fromStdString(path.string()), "PNG")) {
                    const std::string peer = selected_;
                    const std::string saved = path.string();
                    post_core([this, peer, saved]() -> asio::awaitable<void> {
                        co_await service_->send_image(peer, saved);
                    });
                    return true;
                }
            }
        }
    }
    return QMainWindow::eventFilter(watched, event);
}

void ChatWindow::search_step(int delta) {
    if (search_hits_.isEmpty()) {
        return;
    }
    const int count = static_cast<int>(search_hits_.size());
    search_cur_ = (search_cur_ + delta + count) % count;
    highlight_search();
}

void ChatWindow::highlight_search() {
    if (search_edit_->text().trimmed().isEmpty()) {
        search_status_->hide();
        return;
    }
    if (search_hits_.isEmpty()) {
        search_status_->setText(tr("0"));
        search_status_->show();
        return;
    }
    search_status_->setText(QString("%1/%2").arg(search_cur_ + 1).arg(search_hits_.size()));
    search_status_->show();
    const QModelIndex index = chat_->index(search_hits_.at(search_cur_), 0);
    chat_view_->scrollTo(index, QAbstractItemView::PositionAtCenter);
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
    flush_compose_drafts();
    if (tray_ && tray_->isVisible()) {
        hide();
        event->ignore();
        return;
    }
    event->accept();
}

void ChatWindow::resizeEvent(QResizeEvent* event) {
    QMainWindow::resizeEvent(event);
    refresh_status();
    position_emoji_button();
}

}  // namespace i2pchat::gui
