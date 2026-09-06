#pragma once

#include <QAbstractListModel>
#include <QVector>

#include "i2pchat/presentation/chat_view.hpp"

namespace i2pchat::gui {

class ChatModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Role {
        KindRole = Qt::UserRole + 1,
        TimeRole,
        AuthorRole,
        TextRole,
        MarkerRole,
        DetailRole,
    };

    explicit ChatModel(QObject* parent = nullptr);

    [[nodiscard]] int rowCount(const QModelIndex& parent = {}) const override;
    [[nodiscard]] QVariant data(const QModelIndex& index, int role) const override;
    [[nodiscard]] QHash<int, QByteArray> roleNames() const override;

    void set_lines(std::vector<presentation::ChatLine> lines);
    void append(presentation::ChatLine line);
    void clear();
    [[nodiscard]] QVector<int> match_rows(const QString& query) const;

private:
    QVector<presentation::ChatLine> lines_;
};

}  // namespace i2pchat::gui
