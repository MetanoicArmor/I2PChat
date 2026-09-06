#pragma once

#include <QStyledItemDelegate>

namespace i2pchat::gui {

class ContactItemDelegate : public QStyledItemDelegate {
    Q_OBJECT
public:
    explicit ContactItemDelegate(QObject* parent = nullptr);

    void paint(QPainter* painter, const QStyleOptionViewItem& option,
               const QModelIndex& index) const override;
    [[nodiscard]] QSize sizeHint(const QStyleOptionViewItem& option,
                                 const QModelIndex& index) const override;

    void set_dark(bool dark) { dark_ = dark; }

private:
    bool dark_ = false;
};

}  // namespace i2pchat::gui
