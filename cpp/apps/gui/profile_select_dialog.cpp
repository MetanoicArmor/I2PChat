#include "profile_select_dialog.hpp"

#include <algorithm>

#include <QComboBox>
#include <QDesktopServices>
#include <QEvent>
#include <QHBoxLayout>
#include <QLabel>
#include <QMessageBox>
#include <QMouseEvent>
#include <QPushButton>
#include <QUrl>
#include <QVBoxLayout>

#include "i2pchat/runtime/identity.hpp"
#include "dialog_theme.hpp"

namespace i2pchat::gui {

std::vector<std::string> list_profile_names(const std::filesystem::path& app_root) {
    std::vector<std::string> names;
    const std::filesystem::path dir = app_root / "profiles";
    std::error_code ec;
    if (!std::filesystem::exists(dir, ec)) {
        return names;
    }
    for (const auto& entry : std::filesystem::directory_iterator(dir, ec)) {
        if (!entry.is_directory()) {
            continue;
        }
        const std::string name = entry.path().filename().string();
        if (name.empty() || name == runtime::kTransientProfile) {
            continue;
        }
        std::error_code dat_ec;
        const auto dat = entry.path() / (name + ".dat");
        if (!std::filesystem::is_regular_file(dat, dat_ec)) {
            continue;
        }
        names.push_back(name);
    }
    std::sort(names.begin(), names.end());
    return names;
}

ProfileSelectDialog::ProfileSelectDialog(const std::filesystem::path& app_root, bool dark,
                                         QWidget* parent)
    : QDialog(parent) {
    setWindowTitle(QStringLiteral("I2PChat"));
    setMinimumSize(420, 300);
    setMaximumWidth(480);
    setObjectName("ProfileSelectDialog");

    auto* layout = new QVBoxLayout(this);
    layout->setSpacing(16);
    layout->setContentsMargins(28, 28, 28, 28);

    auto* title = new QLabel(QStringLiteral("I2PChat"), this);
    QFont title_font = title->font();
    title_font.setPointSize(18);
    title_font.setWeight(QFont::DemiBold);
    title->setFont(title_font);
    title->setAlignment(Qt::AlignCenter);
    layout->addWidget(title);

    auto* subtitle = new QLabel(tr("Choose profile"), this);
    subtitle->setAlignment(Qt::AlignCenter);
    layout->addWidget(subtitle);

    auto* hint = new QLabel(
        tr("Use <b>%1</b> for a one-time session, or enter a name to save your identity.<br>"
           "<b>Security note:</b> in <b>%1</b> mode, TOFU trust is not persisted between app "
           "restarts.")
            .arg(QString::fromUtf8(runtime::kTransientProfile.data(),
                                   static_cast<int>(runtime::kTransientProfile.size()))),
        this);
    hint->setWordWrap(true);
    hint->setAlignment(Qt::AlignCenter);
    hint->setTextFormat(Qt::RichText);
    layout->addWidget(hint);

    layout->addWidget(new QLabel(tr("Profile:"), this));
    combo_ = new QComboBox(this);
    combo_->setEditable(true);
    combo_->setInsertPolicy(QComboBox::NoInsert);
    combo_->addItem(QString::fromUtf8(runtime::kTransientProfile.data(),
                                      static_cast<int>(runtime::kTransientProfile.size())));
    for (const std::string& name : list_profile_names(app_root)) {
        combo_->addItem(QString::fromStdString(name));
    }
    combo_->setCurrentIndex(0);
    layout->addWidget(combo_);

    auto* combo_hint = new QLabel(
        tr("Click the list on the right to pick an existing profile, or type a new name above."),
        this);
    combo_hint->setWordWrap(true);
    layout->addWidget(combo_hint);

    const QString folder = QString::fromStdString((app_root / "profiles").string());
    auto* path_hint = new QLabel(tr("Data folder: %1 (each profile: profiles/<name>/)").arg(folder),
                                 this);
    path_hint->setWordWrap(true);
    path_hint->setCursor(Qt::PointingHandCursor);
    path_hint->setToolTip(tr("Click to open folder"));
    QObject::connect(path_hint, &QLabel::linkActivated, this, [](const QString&) {});
    path_hint->installEventFilter(this);
    path_hint->setProperty("folder", folder);
    layout->addWidget(path_hint);

    auto* buttons = new QHBoxLayout();
    buttons->addStretch();
    auto* cancel = new QPushButton(tr("Cancel"), this);
    cancel->setObjectName("SecondaryButton");
    cancel->setMinimumWidth(120);
    connect(cancel, &QPushButton::clicked, this, &QDialog::reject);
    auto* cont = new QPushButton(tr("Continue"), this);
    cont->setObjectName("PrimaryButton");
    cont->setDefault(true);
    cont->setMinimumWidth(120);
    connect(cont, &QPushButton::clicked, this, &QDialog::accept);
    buttons->addWidget(cancel);
    buttons->addWidget(cont);
    buttons->addStretch();
    layout->addLayout(buttons);

    apply_dialog_theme(this, dark);
}

QString ProfileSelectDialog::selected_profile() const {
    return combo_->currentText().trimmed();
}

bool ProfileSelectDialog::eventFilter(QObject* watched, QEvent* event) {
    if (event->type() == QEvent::MouseButtonRelease) {
        const QString folder = watched->property("folder").toString();
        if (!folder.isEmpty()) {
            QDesktopServices::openUrl(QUrl::fromLocalFile(folder));
            return true;
        }
    }
    return QDialog::eventFilter(watched, event);
}

void ProfileSelectDialog::accept() {
    const QString name = selected_profile();
    if (name.isEmpty()) {
        return;
    }
    if (name == QString::fromUtf8(runtime::kTransientProfile.data(),
                                  static_cast<int>(runtime::kTransientProfile.size()))) {
        const auto answer = QMessageBox::question(
            this, tr("Transient profile warning"),
            tr("You selected the transient profile '%1'.\n\n"
               "TOFU trust pins are not persisted between app restarts in this mode.\n"
               "For persistent trust, use a named profile.\n\n"
               "Continue with '%1' anyway?")
                .arg(name),
            QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
        if (answer != QMessageBox::Yes) {
            return;
        }
    }
    QDialog::accept();
}

}  // namespace i2pchat::gui
