#pragma once

#include <QStyledItemDelegate>
#include <QString>

namespace i2pchat::gui {

/// Bubble painter for chat rows: incoming on the left, outgoing on the right,
/// system lines centred. Image and file attachments render as a thumbnail or
/// a filename chip when the history line starts with `[image]` / `[file]`.
class ChatItemDelegate : public QStyledItemDelegate {
    Q_OBJECT
public:
    explicit ChatItemDelegate(QObject* parent = nullptr);

    void paint(QPainter* painter, const QStyleOptionViewItem& option,
               const QModelIndex& index) const override;
    [[nodiscard]] QSize sizeHint(const QStyleOptionViewItem& option,
                                 const QModelIndex& index) const override;
    bool editorEvent(QEvent* event, QAbstractItemModel* model, const QStyleOptionViewItem& option,
                     const QModelIndex& index) override;

    void set_dark(bool dark) { dark_ = dark; }
    void set_media_dirs(QString images, QString downloads) {
        images_dir_ = std::move(images);
        downloads_dir_ = std::move(downloads);
    }

private:
    bool dark_ = false;
    QString images_dir_;
    QString downloads_dir_;
};

}  // namespace i2pchat::gui
