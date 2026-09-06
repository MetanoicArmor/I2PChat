#pragma once

#include <QFrame>
#include <QHash>
#include <QIcon>
#include <filesystem>
#include <string>

class QGridLayout;
class QScrollArea;
class QToolButton;

namespace i2pchat::gui {

[[nodiscard]] std::filesystem::path find_fluent_emoji_root();
[[nodiscard]] QIcon tinted_face_icon(bool dark);

class EmojiPickerPopup : public QFrame {
    Q_OBJECT
public:
    explicit EmojiPickerPopup(QWidget* parent = nullptr);

    void set_night(bool night);
    void show_above(QWidget* anchor);

signals:
    void emoji_chosen(const QString& glyph);

private:
    void rebuild();
    QHash<QString, QString> png_by_glyph_;
    std::filesystem::path root_;
    QScrollArea* scroll_ = nullptr;
    QWidget* inner_ = nullptr;
    bool night_ = false;
};

}  // namespace i2pchat::gui
