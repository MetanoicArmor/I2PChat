#include "dialog_theme.hpp"

#include <QApplication>
#include <QAbstractSpinBox>
#include <QDialogButtonBox>
#include <QEvent>
#include <QFile>
#include <QFrame>
#include <QHBoxLayout>
#include <QIODevice>
#include <QLabel>
#include <QObject>
#include <QStyle>
#include <QSizePolicy>
#include <QSpinBox>
#include <QToolButton>
#include <QVariant>
#include <QVBoxLayout>
#include <QWidget>

namespace i2pchat::gui {
namespace {

class HistorySpinFocusFilter : public QObject {
public:
    explicit HistorySpinFocusFilter(QFrame* row) : QObject(row), row_(row) {}

    bool eventFilter(QObject*, QEvent* event) override {
        if (event->type() == QEvent::FocusIn) {
            row_->setProperty("focused", true);
        } else if (event->type() == QEvent::FocusOut) {
            row_->setProperty("focused", false);
        } else {
            return false;
        }
        row_->style()->unpolish(row_);
        row_->style()->polish(row_);
        row_->update();
        return false;
    }

private:
    QFrame* row_ = nullptr;
};

}  // namespace

void apply_dialog_theme(QWidget* widget) {
    const QVariant night = qApp != nullptr ? qApp->property("i2pchatNight") : QVariant();
    apply_dialog_theme(widget, night.isValid() && night.toBool());
}

void apply_dialog_theme(QWidget* widget, bool night) {
    if (widget == nullptr) {
        return;
    }
    const QString path = night ? QStringLiteral(":/i2pchat/qss/dialog_dark.qss")
                               : QStringLiteral(":/i2pchat/qss/dialog_light.qss");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return;
    }
    widget->setAttribute(Qt::WA_StyledBackground, true);
    widget->setStyleSheet(QString::fromUtf8(file.readAll()));
}

void add_centered_dialog_buttons(QVBoxLayout* layout, QDialogButtonBox* buttons) {
    buttons->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
    auto* row = new QHBoxLayout();
    row->addStretch(1);
    row->addWidget(buttons, 0, Qt::AlignHCenter);
    row->addStretch(1);
    layout->addLayout(row);
}

QFrame* wrap_history_numeric_row(QSpinBox* spin) {
    spin->setButtonSymbols(QAbstractSpinBox::NoButtons);
    auto* frame = new QFrame(spin->parentWidget());
    frame->setObjectName("HistoryNumericRow");
    frame->setFrameShape(QFrame::NoFrame);
    frame->setProperty("focused", false);
    frame->setMinimumWidth(260);
    auto* h = new QHBoxLayout(frame);
    h->setContentsMargins(0, 0, 0, 0);
    h->setSpacing(0);
    h->addWidget(spin, 1);

    auto* step_col = new QWidget(frame);
    step_col->setObjectName("HistorySpinStepColumn");
    auto* v = new QVBoxLayout(step_col);
    v->setContentsMargins(0, 0, 0, 0);
    v->setSpacing(0);

    auto* up = new QToolButton(step_col);
    up->setObjectName("HistorySpinStepUp");
    up->setText(QStringLiteral("▲"));
    up->setCursor(Qt::PointingHandCursor);
    up->setAutoRepeat(true);
    up->setAutoRepeatDelay(400);
    up->setAutoRepeatInterval(120);

    auto* down = new QToolButton(step_col);
    down->setObjectName("HistorySpinStepDown");
    down->setText(QStringLiteral("▼"));
    down->setCursor(Qt::PointingHandCursor);
    down->setAutoRepeat(true);
    down->setAutoRepeatDelay(400);
    down->setAutoRepeatInterval(120);

    QObject::connect(up, &QToolButton::clicked, spin, [spin] {
        spin->stepUp();
        spin->setFocus(Qt::OtherFocusReason);
    });
    QObject::connect(down, &QToolButton::clicked, spin, [spin] {
        spin->stepDown();
        spin->setFocus(Qt::OtherFocusReason);
    });
    v->addWidget(up);
    v->addWidget(down);
    h->addWidget(step_col, 0);
    spin->installEventFilter(new HistorySpinFocusFilter(frame));
    return frame;
}

QWidget* history_field_label_block(const QString& title, const QString& hint, QWidget* parent) {
    auto* wrap = new QWidget(parent);
    wrap->setObjectName("HistoryFieldLabelBlock");
    auto* lay = new QVBoxLayout(wrap);
    lay->setContentsMargins(0, 0, 0, 0);
    lay->setSpacing(4);
    lay->setAlignment(Qt::AlignTop | Qt::AlignLeft);
    auto* head = new QLabel(title, wrap);
    head->setObjectName("HistoryFieldTitle");
    head->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    auto* sub = new QLabel(hint, wrap);
    sub->setObjectName("HistoryFieldHint");
    sub->setWordWrap(true);
    sub->setAlignment(Qt::AlignLeft | Qt::AlignTop);
    lay->addWidget(head);
    lay->addWidget(sub);
    return wrap;
}

}  // namespace i2pchat::gui
