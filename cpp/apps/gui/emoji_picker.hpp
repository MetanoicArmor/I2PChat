#pragma once

#include <QFrame>
#include <QHash>
#include <QIcon>
#include <QVector>
#include <filesystem>
#include <string>

class QGridLayout;
class QHideEvent;
class QKeyEvent;
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

protected:
    void keyPressEvent(QKeyEvent* event) override;
    void hideEvent(QHideEvent* event) override;

signals:
    void emoji_chosen(const QString& glyph);
    void picker_hidden();

private:
    void rebuild();
    void sync_focus_visual();
    void pick_focused();
    QHash<QString, QString> png_by_glyph_;
    std::filesystem::path root_;
    QScrollArea* scroll_ = nullptr;
    QWidget* inner_ = nullptr;
    QVector<QToolButton*> buttons_;
    int focus_idx_ = 0;
    bool night_ = false;
};

}  // namespace i2pchat::gui
