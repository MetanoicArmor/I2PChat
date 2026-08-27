#pragma once

#include <QStyledItemDelegate>

namespace i2pchat::gui {

/// Bubble painter for chat rows: incoming on the left, outgoing on the right,
/// system lines centred and unadorned. The Python client spent ~900 lines on
/// inline images and braille art; this covers the text path that every
/// conversation actually uses.
class ChatItemDelegate : public QStyledItemDelegate {
    Q_OBJECT
public:
    explicit ChatItemDelegate(QObject* parent = nullptr);

    void paint(QPainter* painter, const QStyleOptionViewItem& option,
               const QModelIndex& index) const override;
    [[nodiscard]] QSize sizeHint(const QStyleOptionViewItem& option,
                                 const QModelIndex& index) const override;

    void set_dark(bool dark) { dark_ = dark; }

private:
    bool dark_ = false;
};

}  // namespace i2pchat::gui
