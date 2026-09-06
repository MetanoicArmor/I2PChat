#include "chat_item_delegate.hpp"

#include <QApplication>
#include <QDesktopServices>
#include <QEvent>
#include <QFileInfo>
#include <QFontMetrics>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPixmap>
#include <QTextLayout>
#include <QUrl>
#include <QWidget>
#include <algorithm>
#include <cmath>

#include "chat_model.hpp"
#include "i2pchat/presentation/chat_view.hpp"

namespace i2pchat::gui {
namespace {

constexpr int kPaddingX = 12;
constexpr int kPaddingY = 6;
constexpr int kSystemMarginX = 20;
constexpr int kBubbleOuterY = 2;
constexpr int kBubbleRadius = 12;
constexpr int kMetaGapY = 2;
constexpr int kMetaAlpha = 150;
constexpr int kMetaFontDelta = 3;

int cell_width(const QStyleOptionViewItem& option) {
    int width = option.rect.width();
    if (option.widget != nullptr) {
        width = std::max(width, option.widget->contentsRect().width());
    }
    return std::max(width, 120);
}

QFont meta_font(const QFont& base) {
    QFont font = base;
    if (base.pointSize() > 0) {
        font.setPointSize(std::max(base.pointSize() - kMetaFontDelta, 8));
    } else if (base.pixelSize() > 0) {
        font.setPixelSize(std::max(base.pixelSize() - kMetaFontDelta, 9));
    }
    return font;
}

int max_line_advance(const QString& text, const QFont& font) {
    const QFontMetrics metrics(font);
    int best = 0;
    for (const QString& part : text.split(QLatin1Char('\n'))) {
        best = std::max(best, metrics.horizontalAdvance(part));
    }
    return best > 0 ? best : metrics.horizontalAdvance(QLatin1Char(' '));
}

int bubble_width_px(int cell, const QString& text, const QFont& font) {
    const int content = max_line_advance(text, font) + kPaddingX * 4;
    const int max_w = static_cast<int>(cell * 0.75);
    const int min_w = text.contains(QLatin1Char('\n'))
                          ? std::max(72, static_cast<int>(cell * 0.12))
                          : static_cast<int>(cell * 0.4);
    return std::max(min_w, std::min(max_w, content));
}

int inner_text_width(int bubble_w) { return std::max(10, bubble_w - 3 * kPaddingX); }

constexpr int kThumbMaxW = 240;
constexpr int kThumbMaxH = 180;

struct MediaHint {
    enum class Kind { None, Image, File };
    Kind kind = Kind::None;
    QString path;
    QString caption;
};

MediaHint parse_media(const QString& text) {
    MediaHint hint;
    const QString image = QStringLiteral("[image] ");
    const QString file = QStringLiteral("[file] ");
    if (text.startsWith(image)) {
        hint.kind = MediaHint::Kind::Image;
        hint.path = text.mid(image.size()).trimmed();
        hint.caption = QFileInfo(hint.path).fileName();
    } else if (text.startsWith(file)) {
        hint.kind = MediaHint::Kind::File;
        hint.path = text.mid(file.size()).trimmed();
        hint.caption = QFileInfo(hint.path).fileName();
    }
    return hint;
}

QString resolve_media_path(const MediaHint& hint, const QString& images_dir,
                           const QString& downloads_dir) {
    if (hint.path.isEmpty()) {
        return {};
    }
    QFileInfo info(hint.path);
    if (info.isAbsolute() && info.exists()) {
        return hint.path;
    }
    const QString base = hint.kind == MediaHint::Kind::Image ? images_dir : downloads_dir;
    if (!base.isEmpty()) {
        QFileInfo nested(base + QLatin1Char('/') + info.fileName());
        if (nested.exists()) {
            return nested.absoluteFilePath();
        }
    }
    return hint.path;
}

QSize thumb_size(const QPixmap& pix, int max_w) {
    if (pix.isNull()) {
        return {0, 0};
    }
    const int cap_w = std::min(max_w, kThumbMaxW);
    QSize size = pix.size();
    size.scale(cap_w, kThumbMaxH, Qt::KeepAspectRatio);
    return size;
}

qreal wrapped_height(const QString& text, const QFont& font, int inner) {
    QTextLayout layout(text.isEmpty() ? QStringLiteral(" ") : text, font);
    layout.beginLayout();
    qreal height = 0;
    while (true) {
        QTextLine line = layout.createLine();
        if (!line.isValid()) {
            break;
        }
        line.setLineWidth(inner);
        line.setPosition(QPointF(0, height));
        height += line.height();
    }
    layout.endLayout();
    return std::max(height, QFontMetricsF(font).height());
}

QColor bubble_color(presentation::LineKind kind, bool dark) {
    switch (kind) {
        case presentation::LineKind::Outgoing:
            return QColor(0x2f, 0x92, 0xf0);
        case presentation::LineKind::Incoming:
            return dark ? QColor(0x34, 0x38, 0x42) : QColor(0xe2, 0xe6, 0xef);
        case presentation::LineKind::Error:
            return dark ? QColor(0x5a, 0x35, 0x36) : QColor(0xf2, 0xd8, 0xd7);
        case presentation::LineKind::System:
            return Qt::transparent;
    }
    return Qt::transparent;
}

QColor text_color(presentation::LineKind kind, bool dark) {
    switch (kind) {
        case presentation::LineKind::Outgoing:
            return Qt::white;
        case presentation::LineKind::Incoming:
            return dark ? QColor(0xf2, 0xf2, 0xf7) : QColor(0x1c, 0x1c, 0x1e);
        case presentation::LineKind::Error:
            return dark ? QColor(0xff, 0xd9, 0xd6) : QColor(0x7c, 0x30, 0x2c);
        case presentation::LineKind::System:
            return dark ? QColor(0xa3, 0xac, 0xbc) : QColor(0x5f, 0x66, 0x73);
    }
    return dark ? QColor(0xf5, 0xf5, 0xf7) : QColor(0x1d, 0x1d, 0x1f);
}

}  // namespace

ChatItemDelegate::ChatItemDelegate(QObject* parent) : QStyledItemDelegate(parent) {}

QSize ChatItemDelegate::sizeHint(const QStyleOptionViewItem& option,
                                 const QModelIndex& index) const {
    const QString text = index.data(ChatModel::TextRole).toString();
    const QString time = index.data(ChatModel::TimeRole).toString();
    const auto kind =
        static_cast<presentation::LineKind>(index.data(ChatModel::KindRole).toInt());
    const int cell = cell_width(option);
    const QFont font = option.font;

    if (kind == presentation::LineKind::System || kind == presentation::LineKind::Error) {
        const int inner = std::max(10, cell - 2 * kSystemMarginX);
        qreal height = wrapped_height(text, font, inner);
        if (!time.isEmpty()) {
            height += QFontMetrics(meta_font(font)).height() + kMetaGapY;
        }
        return {cell, static_cast<int>(std::ceil(height)) + 4};
    }

    const MediaHint media = parse_media(text);
    const QString resolved = resolve_media_path(media, images_dir_, downloads_dir_);
    QString shown = media.kind == MediaHint::Kind::None ? text : media.caption;
    if (media.kind == MediaHint::Kind::File) {
        shown = QStringLiteral("📎  ") + shown;
    }
    const int bubble_w = bubble_width_px(cell, shown.isEmpty() ? text : shown, font);
    const int inner = inner_text_width(bubble_w);
    qreal height = wrapped_height(shown.isEmpty() ? text : shown, font, inner);
    if (media.kind == MediaHint::Kind::Image) {
        QPixmap pix(resolved);
        if (!pix.isNull()) {
            height += thumb_size(pix, inner).height() + 6;
        }
    }
    height += kPaddingY * 2 + 2 * kBubbleOuterY;
    if (!time.isEmpty()) {
        height += QFontMetrics(meta_font(font)).height() + kMetaGapY;
    }
    return {cell, static_cast<int>(std::ceil(height))};
}

void ChatItemDelegate::paint(QPainter* painter, const QStyleOptionViewItem& option,
                             const QModelIndex& index) const {
    painter->save();
    painter->setRenderHint(QPainter::Antialiasing);
    painter->setRenderHint(QPainter::TextAntialiasing);
    const auto kind =
        static_cast<presentation::LineKind>(index.data(ChatModel::KindRole).toInt());
    const QString text = index.data(ChatModel::TextRole).toString();
    const QString time = index.data(ChatModel::TimeRole).toString();
    const QString marker = index.data(ChatModel::MarkerRole).toString();
    const QFont font = option.font;
    const int cell = option.rect.width();

    if (kind == presentation::LineKind::System || kind == presentation::LineKind::Error) {
        const QColor color = text_color(kind, dark_);
        const QRect outer = option.rect.adjusted(kSystemMarginX, 0, -kSystemMarginX, 0);
        QFont sys = font;
        sys.setPixelSize(12);
        painter->setFont(sys);
        painter->setPen(color);
        const int meta_h = time.isEmpty() ? 0 : QFontMetrics(meta_font(sys)).height() + kMetaGapY;
        const QRect text_rect = outer.adjusted(0, 0, 0, -meta_h);
        painter->drawText(text_rect, Qt::AlignHCenter | Qt::AlignVCenter | Qt::TextWordWrap,
                          text);
        if (!time.isEmpty()) {
            QColor meta = color;
            meta.setAlpha(kMetaAlpha);
            painter->setPen(meta);
            painter->setFont(meta_font(sys));
            painter->drawText(QRect(outer.left(), outer.bottom() - meta_h + kMetaGapY,
                                    outer.width(), meta_h - kMetaGapY),
                              Qt::AlignHCenter | Qt::AlignVCenter, time);
        }
        painter->restore();
        return;
    }

    const bool outgoing = kind == presentation::LineKind::Outgoing;
    const MediaHint media = parse_media(text);
    const QString resolved = resolve_media_path(media, images_dir_, downloads_dir_);
    QString shown = media.kind == MediaHint::Kind::None ? text : media.caption;
    if (media.kind == MediaHint::Kind::File) {
        shown = QStringLiteral("📎  ") + shown;
    }
    const int bubble_w = bubble_width_px(cell, shown, font);
    const int x = outgoing ? option.rect.right() - bubble_w : option.rect.left();
    const QRectF bubble(x, option.rect.top() + kBubbleOuterY, bubble_w,
                        option.rect.height() - 2 * kBubbleOuterY);

    QPainterPath path;
    path.addRoundedRect(bubble, kBubbleRadius, kBubbleRadius);
    painter->fillPath(path, bubble_color(kind, dark_));

    const QColor fg = text_color(kind, dark_);
    const int meta_h = time.isEmpty() && marker.isEmpty()
                           ? 0
                           : QFontMetrics(meta_font(font)).height() + kMetaGapY;
    const QRectF inner = bubble.adjusted(kPaddingX, kPaddingY, -kPaddingX, -kPaddingY);
    qreal y = inner.top();
    if (media.kind == MediaHint::Kind::Image) {
        QPixmap pix(resolved);
        if (!pix.isNull()) {
            const QSize sz = thumb_size(pix, static_cast<int>(inner.width()));
            const QPixmap scaled = pix.scaled(sz, Qt::KeepAspectRatio, Qt::SmoothTransformation);
            const int draw_x = outgoing ? static_cast<int>(inner.right()) - sz.width()
                                        : static_cast<int>(inner.left());
            painter->drawPixmap(draw_x, static_cast<int>(y), scaled);
            y += sz.height() + 4;
        }
    }
    const QRectF text_area(inner.left(), y, inner.width(),
                           std::max(1.0, inner.bottom() - meta_h - y));

    painter->setPen(fg);
    painter->setFont(font);
    painter->drawText(text_area.toRect(), Qt::AlignLeft | Qt::AlignTop | Qt::TextWordWrap,
                      shown.isEmpty() ? QStringLiteral(" ") : shown);

    QString meta = time;
    if (!marker.isEmpty()) {
        meta = meta.isEmpty() ? marker : (meta + QStringLiteral("  ") + marker);
    }
    if (!meta.isEmpty()) {
        QColor meta_color = outgoing ? QColor(Qt::white) : fg;
        meta_color.setAlpha(kMetaAlpha);
        painter->setPen(meta_color);
        painter->setFont(meta_font(font));
        painter->drawText(QRectF(inner.left(), inner.bottom() - meta_h + kMetaGapY, inner.width(),
                                 meta_h - kMetaGapY)
                              .toRect(),
                          Qt::AlignRight | Qt::AlignVCenter, meta);
    }
    painter->restore();
}

bool ChatItemDelegate::editorEvent(QEvent* event, QAbstractItemModel*,
                                   const QStyleOptionViewItem&, const QModelIndex& index) {
    if (event->type() != QEvent::MouseButtonDblClick) {
        return false;
    }
    const auto* mouse = static_cast<QMouseEvent*>(event);
    if (mouse->button() != Qt::LeftButton) {
        return false;
    }
    const MediaHint media = parse_media(index.data(ChatModel::TextRole).toString());
    if (media.kind == MediaHint::Kind::None) {
        return false;
    }
    const QString resolved = resolve_media_path(media, images_dir_, downloads_dir_);
    if (resolved.isEmpty()) {
        return false;
    }
    QDesktopServices::openUrl(QUrl::fromLocalFile(resolved));
    return true;
}

}  // namespace i2pchat::gui
