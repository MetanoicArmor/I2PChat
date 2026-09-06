#include "router_settings_dialog.hpp"

#include <cstdlib>
#include <fstream>

#include <QButtonGroup>
#include <QCoreApplication>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QVBoxLayout>

#include <nlohmann/json.hpp>

#include "i2pchat/storage/atomic_write.hpp"

namespace i2pchat::gui {
namespace {

bool env_truthy(const char* name) {
    const char* raw = std::getenv(name);
    if (raw == nullptr) {
        return false;
    }
    const std::string text = raw;
    return text == "1" || text == "true" || text == "TRUE" || text == "yes" || text == "on";
}

bool is_loopback_host(std::string host) {
    for (char& ch : host) {
        if (ch >= 'A' && ch <= 'Z') {
            ch = static_cast<char>(ch - 'A' + 'a');
        }
    }
    return host == "127.0.0.1" || host == "::1" || host == "localhost";
}

std::uint16_t coerce_port(const nlohmann::json& value, std::uint16_t fallback) {
    try {
        int raw = 0;
        if (value.is_number_integer()) {
            raw = value.get<int>();
        } else if (value.is_string()) {
            raw = std::stoi(value.get<std::string>());
        } else {
            return fallback;
        }
        if (raw < 1 || raw > 65535) {
            return fallback;
        }
        return static_cast<std::uint16_t>(raw);
    } catch (...) {
        return fallback;
    }
}

QWidget* wrap_spin(QSpinBox* box) {
    auto* wrap = new QWidget(box->parentWidget());
    auto* row = new QHBoxLayout(wrap);
    row->setContentsMargins(0, 0, 0, 0);
    row->addWidget(box);
    row->addStretch(1);
    box->setMinimumWidth(96);
    return wrap;
}

QLabel* section_label(const QString& text, bool secondary, QWidget* parent) {
    auto* lab = new QLabel(text, parent);
    lab->setObjectName(secondary ? QStringLiteral("RouterSectionSecondaryTitle")
                                 : QStringLiteral("RouterSectionTitle"));
    lab->setWordWrap(true);
    return lab;
}

}  // namespace

bool bundled_i2pd_allowed() {
    if (env_truthy("I2PCHAT_DISABLE_BUNDLED_I2PD")) {
        return false;
    }
    return true;
}

std::filesystem::path router_prefs_path(const std::filesystem::path& app_root) {
    return app_root / "router_prefs.json";
}

std::filesystem::path router_runtime_dir(const std::filesystem::path& app_root) {
    const auto path = app_root / "router";
    std::error_code ec;
    std::filesystem::create_directories(path, ec);
    return path;
}

GuiRouterSettings normalize_gui_router_settings(GuiRouterSettings settings) {
    if (settings.backend != "bundled" && settings.backend != "system") {
        settings.backend = "system";
    }
    if (!is_loopback_host(settings.bundled_sam_host)) {
        settings.bundled_sam_host = "127.0.0.1";
    }
    if (!env_truthy("I2PCHAT_ALLOW_REMOTE_SAM") && !is_loopback_host(settings.system_sam_host)) {
        settings.system_sam_host = "127.0.0.1";
    }
    if (!bundled_i2pd_allowed()) {
        settings.backend = "system";
        settings.bundled_auto_start = false;
    }
    settings.bundled_auto_start = settings.backend == "bundled";
    return settings;
}

GuiRouterSettings load_gui_router_settings(const std::filesystem::path& app_root) {
    GuiRouterSettings settings;
    std::ifstream stream(router_prefs_path(app_root));
    if (!stream) {
        return normalize_gui_router_settings(settings);
    }
    try {
        nlohmann::json raw;
        stream >> raw;
        if (!raw.is_object()) {
            return normalize_gui_router_settings(settings);
        }
        if (raw.contains("backend") && raw["backend"].is_string()) {
            settings.backend = raw["backend"].get<std::string>();
        }
        if (raw.contains("system_sam_host") && raw["system_sam_host"].is_string()) {
            settings.system_sam_host = raw["system_sam_host"].get<std::string>();
        }
        settings.system_sam_port = coerce_port(raw.value("system_sam_port", nlohmann::json{}),
                                              settings.system_sam_port);
        if (raw.contains("bundled_sam_host") && raw["bundled_sam_host"].is_string()) {
            settings.bundled_sam_host = raw["bundled_sam_host"].get<std::string>();
        }
        settings.bundled_sam_port = coerce_port(raw.value("bundled_sam_port", nlohmann::json{}),
                                               settings.bundled_sam_port);
        settings.bundled_http_proxy_port =
            coerce_port(raw.value("bundled_http_proxy_port", nlohmann::json{}),
                        settings.bundled_http_proxy_port);
        settings.bundled_socks_proxy_port =
            coerce_port(raw.value("bundled_socks_proxy_port", nlohmann::json{}),
                        settings.bundled_socks_proxy_port);
        settings.bundled_control_http_port =
            coerce_port(raw.value("bundled_control_http_port", nlohmann::json{}),
                        settings.bundled_control_http_port);
        if (raw.contains("bundled_auto_start") && raw["bundled_auto_start"].is_boolean()) {
            settings.bundled_auto_start = raw["bundled_auto_start"].get<bool>();
        }
    } catch (...) {
    }
    return normalize_gui_router_settings(settings);
}

void save_gui_router_settings(const std::filesystem::path& app_root, GuiRouterSettings settings) {
    settings = normalize_gui_router_settings(std::move(settings));
    nlohmann::json document = {
        {"backend", settings.backend},
        {"system_sam_host", settings.system_sam_host},
        {"system_sam_port", settings.system_sam_port},
        {"bundled_sam_host", settings.bundled_sam_host},
        {"bundled_sam_port", settings.bundled_sam_port},
        {"bundled_http_proxy_port", settings.bundled_http_proxy_port},
        {"bundled_socks_proxy_port", settings.bundled_socks_proxy_port},
        {"bundled_control_http_port", settings.bundled_control_http_port},
        {"bundled_auto_start", settings.bundled_auto_start},
    };
    storage::atomic_write_json(router_prefs_path(app_root), document);
}

std::optional<std::filesystem::path> find_bundled_i2pd_binary() {
    const QString mac = QCoreApplication::applicationDirPath() +
                        QStringLiteral("/../Resources/vendor/i2pd");
    const std::filesystem::path vendor = mac.toStdString();
    const char* names[] = {"darwin-arm64/i2pd", "darwin-x64/i2pd", "macos-arm64/i2pd",
                           "macos-x64/i2pd", "linux-x64/i2pd", "linux-aarch64/i2pd"};
    for (const char* name : names) {
        const auto candidate = vendor / name;
        std::error_code ec;
        if (std::filesystem::is_regular_file(candidate, ec)) {
            return candidate;
        }
    }
    return std::nullopt;
}

RouterSettingsDialog::RouterSettingsDialog(QWidget* parent, GuiRouterSettings settings,
                                           const QString& bundled_status,
                                           std::function<void()> open_data_dir,
                                           std::function<void()> open_log,
                                           std::function<void()> restart_bundled)
    : QDialog(parent) {
    setWindowTitle(tr("I2P router"));
    setModal(true);
    setMinimumWidth(560);

    auto* v = new QVBoxLayout(this);
    v->setContentsMargins(20, 16, 20, 16);
    v->setSpacing(14);

    auto* form = new QFormLayout();
    form->setHorizontalSpacing(14);
    form->setVerticalSpacing(12);
    form->setRowWrapPolicy(QFormLayout::DontWrapRows);
    form->setFieldGrowthPolicy(QFormLayout::FieldsStayAtSizeHint);
    form->setLabelAlignment(Qt::AlignLeft | Qt::AlignTop);

    system_host_ = new QLineEdit(QString::fromStdString(settings.system_sam_host), this);
    system_host_->setMinimumWidth(260);
    system_port_ = new QSpinBox(this);
    system_port_->setRange(1, 65535);
    system_port_->setValue(settings.system_sam_port);
    bundled_sam_port_ = new QSpinBox(this);
    bundled_sam_port_->setRange(1, 65535);
    bundled_sam_port_->setValue(settings.bundled_sam_port);
    bundled_http_proxy_port_ = new QSpinBox(this);
    bundled_http_proxy_port_->setRange(1, 65535);
    bundled_http_proxy_port_->setValue(settings.bundled_http_proxy_port);
    bundled_socks_proxy_port_ = new QSpinBox(this);
    bundled_socks_proxy_port_->setRange(1, 65535);
    bundled_socks_proxy_port_->setValue(settings.bundled_socks_proxy_port);
    bundled_control_http_port_ = new QSpinBox(this);
    bundled_control_http_port_->setRange(1, 65535);
    bundled_control_http_port_->setValue(settings.bundled_control_http_port);

    form->addRow(section_label(tr("Built-in router (Bundled i2pd)"), false, this));
    form->addRow(tr("SAM port"), wrap_spin(bundled_sam_port_));
    form->addRow(tr("HTTP proxy"), wrap_spin(bundled_http_proxy_port_));
    form->addRow(tr("SOCKS proxy"), wrap_spin(bundled_socks_proxy_port_));
    form->addRow(tr("Control HTTP"), wrap_spin(bundled_control_http_port_));
    form->addRow(section_label(tr("External router (System i2pd)"), true, this));
    form->addRow(tr("SAM host"), system_host_);
    form->addRow(tr("SAM port"), wrap_spin(system_port_));

    auto* form_wrap = new QWidget(this);
    auto* form_outer = new QVBoxLayout(form_wrap);
    form_outer->setContentsMargins(0, 0, 0, 0);
    form_outer->addLayout(form);

    auto* backend_panel = new QFrame(this);
    backend_panel->setObjectName("RouterBackendPanel");
    backend_panel->setFixedWidth(212);
    auto* bp_lay = new QVBoxLayout(backend_panel);
    bp_lay->setContentsMargins(12, 12, 12, 12);
    bp_lay->setSpacing(10);
    auto* pick_title = new QLabel(tr("Router source"), backend_panel);
    pick_title->setObjectName("RouterBackendPickTitle");
    pick_title->setWordWrap(true);
    bp_lay->addWidget(pick_title);

    opt_bundled_ = new QPushButton(tr("Bundled i2pd\nIncluded with I2PChat"), backend_panel);
    opt_bundled_->setObjectName("RouterBackendOption");
    opt_bundled_->setCheckable(true);
    opt_bundled_->setAutoDefault(false);
    opt_bundled_->setDefault(false);
    opt_system_ = new QPushButton(tr("System i2pd\nExisting install"), backend_panel);
    opt_system_->setObjectName("RouterBackendOption");
    opt_system_->setCheckable(true);
    opt_system_->setAutoDefault(false);
    opt_system_->setDefault(false);

    auto* group = new QButtonGroup(this);
    group->setExclusive(true);
    group->addButton(opt_bundled_, 0);
    group->addButton(opt_system_, 1);
    if (settings.backend == "bundled" && bundled_i2pd_allowed()) {
        opt_bundled_->setChecked(true);
    } else {
        opt_system_->setChecked(true);
    }
    if (!bundled_i2pd_allowed()) {
        opt_bundled_->setEnabled(false);
    }
    bp_lay->addWidget(opt_bundled_);
    bp_lay->addWidget(opt_system_);

    auto* router_pick_column = new QWidget(this);
    auto* router_pick_lay = new QVBoxLayout(router_pick_column);
    router_pick_lay->setContentsMargins(0, 0, 0, 0);
    router_pick_lay->setSpacing(0);
    router_pick_lay->addStretch(2);
    router_pick_lay->addWidget(backend_panel, 0, Qt::AlignHCenter);
    router_pick_lay->addStretch(1);

    auto* body_row = new QHBoxLayout();
    body_row->setSpacing(18);
    body_row->addWidget(form_wrap, 1);
    body_row->addWidget(router_pick_column, 0);
    v->addLayout(body_row);

    status_label_ = new QLabel(bundled_status, this);
    status_label_->setWordWrap(true);
    status_label_->setObjectName("RouterStatusLabel");
    v->addWidget(status_label_);

    auto* actions_inner = new QHBoxLayout();
    actions_inner->setContentsMargins(0, 0, 0, 0);
    actions_inner->setSpacing(8);
    auto* btn_open_data = new QPushButton(tr("Open data dir"), this);
    btn_open_data->setObjectName("SecondaryButton");
    auto* btn_open_log = new QPushButton(tr("Open log"), this);
    btn_open_log->setObjectName("SecondaryButton");
    btn_restart_ = new QPushButton(tr("Restart bundled router"), this);
    btn_restart_->setObjectName("SecondaryButton");
    actions_inner->addWidget(btn_open_data);
    actions_inner->addWidget(btn_open_log);
    actions_inner->addWidget(btn_restart_);
    auto* actions_bar = new QWidget(this);
    actions_bar->setLayout(actions_inner);
    auto* actions_row = new QHBoxLayout();
    actions_row->addStretch(1);
    actions_row->addWidget(actions_bar, 0, Qt::AlignHCenter);
    actions_row->addStretch(1);
    v->addLayout(actions_row);

    auto* bb = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    bb->button(QDialogButtonBox::Ok)->setText(tr("Save and apply"));
    bb->button(QDialogButtonBox::Ok)->setObjectName("PrimaryButton");
    bb->button(QDialogButtonBox::Cancel)->setObjectName("SecondaryButton");
    connect(bb, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(bb, &QDialogButtonBox::rejected, this, &QDialog::reject);
    auto* buttons_row = new QHBoxLayout();
    buttons_row->addStretch(1);
    buttons_row->addWidget(bb);
    buttons_row->addStretch(1);
    v->addLayout(buttons_row);

    connect(group, &QButtonGroup::idClicked, this, [this](int) { sync_enabled(); });
    connect(btn_open_data, &QPushButton::clicked, this, [open_data_dir] {
        if (open_data_dir) {
            open_data_dir();
        }
    });
    connect(btn_open_log, &QPushButton::clicked, this, [open_log] {
        if (open_log) {
            open_log();
        }
    });
    connect(btn_restart_, &QPushButton::clicked, this, [restart_bundled] {
        if (restart_bundled) {
            restart_bundled();
        }
    });
    sync_enabled();
}

void RouterSettingsDialog::sync_enabled() {
    if (!bundled_i2pd_allowed()) {
        opt_system_->setChecked(true);
    }
    const bool use_system = opt_system_->isChecked();
    system_host_->setEnabled(use_system);
    system_port_->setEnabled(use_system);
    const bool bundled_enabled = (!use_system) && bundled_i2pd_allowed();
    bundled_sam_port_->setEnabled(bundled_enabled);
    bundled_http_proxy_port_->setEnabled(bundled_enabled);
    bundled_socks_proxy_port_->setEnabled(bundled_enabled);
    bundled_control_http_port_->setEnabled(bundled_enabled);
    btn_restart_->setEnabled(bundled_enabled);
    if (!bundled_i2pd_allowed()) {
        status_label_->setText(tr(
            "Bundled router is disabled in this build. Configure a system i2pd SAM endpoint."));
    }
}

void RouterSettingsDialog::set_status(const QString& text) { status_label_->setText(text); }

GuiRouterSettings RouterSettingsDialog::settings() const {
    GuiRouterSettings out;
    out.backend = (opt_bundled_->isChecked() && bundled_i2pd_allowed()) ? "bundled" : "system";
    out.system_sam_host = system_host_->text().trimmed().toStdString();
    if (out.system_sam_host.empty()) {
        out.system_sam_host = "127.0.0.1";
    }
    out.system_sam_port = static_cast<std::uint16_t>(system_port_->value());
    out.bundled_sam_host = "127.0.0.1";
    out.bundled_sam_port = static_cast<std::uint16_t>(bundled_sam_port_->value());
    out.bundled_http_proxy_port = static_cast<std::uint16_t>(bundled_http_proxy_port_->value());
    out.bundled_socks_proxy_port = static_cast<std::uint16_t>(bundled_socks_proxy_port_->value());
    out.bundled_control_http_port = static_cast<std::uint16_t>(bundled_control_http_port_->value());
    return normalize_gui_router_settings(std::move(out));
}

}  // namespace i2pchat::gui
