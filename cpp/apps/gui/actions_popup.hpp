#pragma once

#include <QFrame>
#include <QPoint>
#include <functional>

class QLabel;
class QVBoxLayout;

namespace i2pchat::gui {

class ActionsPopupItem : public QFrame {
    Q_OBJECT
public:
    explicit ActionsPopupItem(const QString& title, const QString& shortcut, QWidget* parent);

signals:
    void clicked();

protected:
    void mouseReleaseEvent(QMouseEvent* event) override;
    void enterEvent(QEnterEvent* event) override;
    void leaveEvent(QEvent* event) override;
};

class ActionsPopup : public QFrame {
    Q_OBJECT
public:
    explicit ActionsPopup(QWidget* parent = nullptr);

    void clear_actions();
    void add_action(const QString& title, const QString& shortcut,
                    const std::function<void()>& callback);
    void add_separator();
    void show_below(QWidget* anchor);
    void show_at(const QPoint& global_pos);
    void set_night(bool night);

private:
    QFrame* surface_ = nullptr;
    QVBoxLayout* surface_layout_ = nullptr;
    bool night_ = false;
};

}  // namespace i2pchat::gui
