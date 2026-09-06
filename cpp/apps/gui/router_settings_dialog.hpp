#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <optional>
#include <string>

#include <QDialog>

class QLabel;
class QLineEdit;
class QPushButton;
class QSpinBox;

namespace i2pchat::gui {

struct GuiRouterSettings {
    std::string backend = "system";
    std::string system_sam_host = "127.0.0.1";
    std::uint16_t system_sam_port = 7656;
    std::string bundled_sam_host = "127.0.0.1";
    std::uint16_t bundled_sam_port = 17656;
    std::uint16_t bundled_http_proxy_port = 14444;
    std::uint16_t bundled_socks_proxy_port = 14447;
    std::uint16_t bundled_control_http_port = 17070;
    bool bundled_auto_start = false;
};

[[nodiscard]] bool bundled_i2pd_allowed();
[[nodiscard]] std::filesystem::path router_prefs_path(const std::filesystem::path& app_root);
[[nodiscard]] std::filesystem::path router_runtime_dir(const std::filesystem::path& app_root);
[[nodiscard]] GuiRouterSettings load_gui_router_settings(const std::filesystem::path& app_root);
void save_gui_router_settings(const std::filesystem::path& app_root, GuiRouterSettings settings);
[[nodiscard]] std::optional<std::filesystem::path> find_bundled_i2pd_binary();
[[nodiscard]] GuiRouterSettings normalize_gui_router_settings(GuiRouterSettings settings);

class RouterSettingsDialog : public QDialog {
    Q_OBJECT
public:
    RouterSettingsDialog(QWidget* parent, GuiRouterSettings settings, const QString& bundled_status,
                         bool night, std::function<void()> open_data_dir,
                         std::function<void()> open_log, std::function<void()> restart_bundled);

    [[nodiscard]] GuiRouterSettings settings() const;
    void set_status(const QString& text);

private:
    void sync_enabled();

    QLineEdit* system_host_ = nullptr;
    QSpinBox* system_port_ = nullptr;
    QSpinBox* bundled_sam_port_ = nullptr;
    QSpinBox* bundled_http_proxy_port_ = nullptr;
    QSpinBox* bundled_socks_proxy_port_ = nullptr;
    QSpinBox* bundled_control_http_port_ = nullptr;
    QPushButton* opt_bundled_ = nullptr;
    QPushButton* opt_system_ = nullptr;
    QPushButton* btn_restart_ = nullptr;
    QLabel* status_label_ = nullptr;
};

}  // namespace i2pchat::gui
