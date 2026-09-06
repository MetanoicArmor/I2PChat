#pragma once

#include <QDialog>
#include <filesystem>
#include <string>
#include <vector>

class QComboBox;
class QEvent;

namespace i2pchat::gui {

[[nodiscard]] std::vector<std::string> list_profile_names(
    const std::filesystem::path& app_root);

class ProfileSelectDialog : public QDialog {
    Q_OBJECT
public:
    explicit ProfileSelectDialog(const std::filesystem::path& app_root, bool dark,
                                 QWidget* parent = nullptr);

    [[nodiscard]] QString selected_profile() const;

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;
    void accept() override;

private:
    QComboBox* combo_ = nullptr;
};

}  // namespace i2pchat::gui
