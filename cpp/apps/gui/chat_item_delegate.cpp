#include "chat_item_delegate.hpp"

#include <QPainter>
#include <QPainterPath>
#include <QTextLayout>
#include <algorithm>

#include "chat_model.hpp"
#include "i2pchat/presentation/chat_view.hpp"

namespace i2pchat::gui {
namespace {

constexpr int kMargin = 12;
constexpr int kBubblePad = 10;
constexpr int kMaxFraction = 72;  // percent of the view width

QColor bubble_color(presentation::LineKind kind, bool dark) {
    switch (kind) {
        case presentation::LineKind::Outgoing:
            return dark ? QColor(10, 132, 255) : QColor(10, 132, 255);
        case presentation::LineKind::Incoming:
            return dark ? QColor(58, 58, 60) : QColor(255, 255, 255);
        case presentation::LineKind::Error:
            return dark ? QColor(90, 30, 30) : QColor(255, 230, 230);
        case presentation::LineKind::System:
            return Qt::transparent;
    }
    return Qt::transparent;
}

QColor text_color(presentation::LineKind kind, bool dark) {
    if (kind == presentation::LineKind::Outgoing) {
        return Qt::white;
    }
    if (kind == presentation::LineKind::System) {
        return dark ? QColor(142, 142, 147) : QColor(98, 104, 117);
    }
    return dark ? QColor(245, 245, 247) : QColor(29, 29, 31);
}

int bubble_width(int view_width, const QString& text, const QFont& font) {
    const int cap = std::max(120, view_width * kMaxFraction / 100);
    QFontMetrics metrics(font);
    return std::min(cap, metrics.horizontalAdvance(text) + 2 * kBubblePad + 8);
}

}  // namespace

ChatItemDelegate::ChatItemDelegate(QObject* parent) : QStyledItemDelegate(parent) {}

QSize ChatItemDelegate::sizeHint(const QStyleOptionViewItem& option,
                                 const QModelIndex& index) const {
    const QString text = index.data(ChatModel::TextRole).toString();
    const int kind = index.data(ChatModel::KindRole).toInt();
    const int view_width = option.rect.width() > 0 ? option.rect.width() : 400;
    if (kind == static_cast<int>(presentation::LineKind::System) ||
        kind == static_cast<int>(presentation::LineKind::Error)) {
        return {view_width, option.fontMetrics.lineSpacing() + kMargin};
    }
    const int width = bubble_width(view_width, text, option.font);
    QTextLayout layout(text, option.font);
    layout.beginLayout();
    qreal height = 0;
    const int inner = width - 2 * kBubblePad;
    while (true) {
        QTextLine line = layout.createLine();
        if (!line.isValid()) {
            break;
        }
        line.setLineWidth(inner);
        height += line.height();
    }
    layout.endLayout();
    return {view_width, static_cast<int>(height) + 2 * kBubblePad + kMargin +
                            option.fontMetrics.lineSpacing()};
}

void ChatItemDelegate::paint(QPainter* painter, const QStyleOptionViewItem& option,
                             const QModelIndex& index) const {
    painter->save();
    painter->setRenderHint(QPainter::Antialiasing);
    const auto kind =
        static_cast<presentation::LineKind>(index.data(ChatModel::KindRole).toInt());
    const QString text = index.data(ChatModel::TextRole).toString();
    const QString time = index.data(ChatModel::TimeRole).toString();
    const QString marker = index.data(ChatModel::MarkerRole).toString();
    const QString author = index.data(ChatModel::AuthorRole).toString();

    if (kind == presentation::LineKind::System || kind == presentation::LineKind::Error) {
        painter->setPen(text_color(kind, dark_));
        painter->drawText(option.rect.adjusted(kMargin, 0, -kMargin, 0),
                          Qt::AlignCenter | Qt::TextWordWrap, text);
        painter->restore();
        return;
    }

    const int width = bubble_width(option.rect.width(), text, option.font);
    const int x = kind == presentation::LineKind::Outgoing
                      ? option.rect.right() - width - kMargin
                      : option.rect.left() + kMargin;
    const QRect bubble(x, option.rect.top() + 4, width,
                       option.rect.height() - kMargin);

    QPainterPath path;
    path.addRoundedRect(bubble, 14, 14);
    painter->fillPath(path, bubble_color(kind, dark_));

    painter->setPen(text_color(kind, dark_));
    const QRect inner = bubble.adjusted(kBubblePad, kBubblePad, -kBubblePad, -kBubblePad);
    QString body = text;
    if (!author.isEmpty() && kind == presentation::LineKind::Incoming) {
        body = author + "\n" + text;
    }
    painter->drawText(inner, Qt::AlignLeft | Qt::TextWordWrap, body);

    QString meta = time;
    if (!marker.isEmpty()) {
        meta += "  " + marker;
    }
    painter->setPen(text_color(presentation::LineKind::System, dark_));
    painter->drawText(inner, Qt::AlignBottom | Qt::AlignRight, meta);
    painter->restore();
}

}  // namespace i2pchat::gui
