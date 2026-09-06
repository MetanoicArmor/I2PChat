#include "actions_popup.hpp"

#include <algorithm>
#include <QApplication>
#include <QEnterEvent>
#include <QEvent>
#include <QHBoxLayout>
#include <QLabel>
#include <QMouseEvent>
#include <QScreen>
#include <QStyle>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

namespace i2pchat::gui {

ActionsPopupItem::ActionsPopupItem(const QString& title, const QString& shortcut,
                                   const QString& tooltip, QWidget* parent)
    : QFrame(parent) {
    setObjectName("ActionsPopupItem");
    setAttribute(Qt::WA_Hover, true);
    setCursor(Qt::PointingHandCursor);
    if (!tooltip.isEmpty()) {
        setToolTip(tooltip);
    }
    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(10, 2, 10, 2);
    layout->setSpacing(8);
    title_label_ = new QLabel(title, this);
    title_label_->setObjectName("ActionsPopupItemTitle");
    title_label_->setAttribute(Qt::WA_TransparentForMouseEvents, true);
    layout->addWidget(title_label_, 1);
    auto* shortcut_label = new QLabel(shortcut, this);
    shortcut_label->setObjectName("ActionsPopupItemShortcut");
    shortcut_label->setAttribute(Qt::WA_TransparentForMouseEvents, true);
    shortcut_label->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    shortcut_label->setVisible(!shortcut.isEmpty());
    layout->addWidget(shortcut_label, 0);
}

void ActionsPopupItem::set_title(const QString& title) {
    if (title_label_ != nullptr) {
        title_label_->setText(title);
    }
}

void ActionsPopupItem::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        emit clicked();
        event->accept();
        return;
    }
    QFrame::mousePressEvent(event);
}

void ActionsPopupItem::mouseReleaseEvent(QMouseEvent* event) {
    QFrame::mouseReleaseEvent(event);
}

void ActionsPopupItem::enterEvent(QEnterEvent*) { setProperty("hover", true); style()->unpolish(this); style()->polish(this); }
void ActionsPopupItem::leaveEvent(QEvent*) { setProperty("hover", false); style()->unpolish(this); style()->polish(this); }

ActionsPopup::ActionsPopup(QWidget* parent) : QFrame(parent) {
    setObjectName("ActionsPopupWindow");
    setWindowFlags(Qt::Popup | Qt::FramelessWindowHint);
    setAttribute(Qt::WA_TranslucentBackground, true);
    setMinimumWidth(236);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);
    surface_ = new QFrame(this);
    surface_->setObjectName("ActionsPopupSurface");
    root->addWidget(surface_);
    surface_layout_ = new QVBoxLayout(surface_);
    surface_layout_->setContentsMargins(6, 6, 6, 6);
    surface_layout_->setSpacing(0);
}

void ActionsPopup::clear_actions() {
    while (surface_layout_->count() > 0) {
        QLayoutItem* item = surface_layout_->takeAt(0);
        if (item->widget() != nullptr) {
            item->widget()->deleteLater();
        }
        delete item;
    }
}

ActionsPopupItem* ActionsPopup::add_action(const QString& title, const QString& shortcut,
                              const std::function<void()>& callback, const QString& tooltip) {
    auto* item = new ActionsPopupItem(title, shortcut, tooltip, surface_);
    connect(item, &ActionsPopupItem::clicked, this, [this, callback] {
        hide();
        if (callback) {
            QTimer::singleShot(0, this, [callback] { callback(); });
        }
    });
    surface_layout_->addWidget(item);
    return item;
}

void ActionsPopup::add_separator() {
    auto* sep = new QFrame(surface_);
    sep->setObjectName("ActionsPopupSeparator");
    sep->setFrameShape(QFrame::HLine);
    sep->setFixedHeight(1);
    surface_layout_->addWidget(sep);
}

void ActionsPopup::show_below(QWidget* anchor) {
    show_at(anchor->mapToGlobal(QPoint(anchor->width() - sizeHint().width(), anchor->height() + 4)));
}

void ActionsPopup::show_at(const QPoint& global_pos) {
    adjustSize();
    QPoint pos = global_pos;
    if (QScreen* screen = QApplication::screenAt(pos)) {
        const QRect avail = screen->availableGeometry();
        if (height() > avail.height() - 12) {
            setMaximumHeight(avail.height() - 12);
            adjustSize();
        } else {
            setMaximumHeight(QWIDGETSIZE_MAX);
        }
        pos.setX(std::min(pos.x(), avail.right() - width() + 1));
        pos.setY(std::min(pos.y(), avail.bottom() - height() + 1));
        pos.setX(std::max(pos.x(), avail.left()));
        pos.setY(std::max(pos.y(), avail.top()));
    }
    move(pos);
    show();
    raise();
}

void ActionsPopup::set_night(bool night) { night_ = night; }

}  // namespace i2pchat::gui
