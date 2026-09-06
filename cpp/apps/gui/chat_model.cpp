#include "chat_model.hpp"

#include <QString>

namespace i2pchat::gui {

ChatModel::ChatModel(QObject* parent) : QAbstractListModel(parent) {}

int ChatModel::rowCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : static_cast<int>(lines_.size());
}

QVariant ChatModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= lines_.size()) {
        return {};
    }
    const presentation::ChatLine& line = lines_.at(index.row());
    switch (role) {
        case Qt::DisplayRole:
        case TextRole:
            return QString::fromStdString(line.text);
        case KindRole:
            return static_cast<int>(line.kind);
        case TimeRole:
            return QString::fromStdString(line.time);
        case AuthorRole:
            return QString::fromStdString(line.author);
        case MarkerRole:
            return QString::fromStdString(line.marker);
        case DetailRole:
            return QString::fromStdString(line.detail);
        default:
            return {};
    }
}

QHash<int, QByteArray> ChatModel::roleNames() const {
    return {{KindRole, "kind"},     {TimeRole, "time"},     {AuthorRole, "author"},
            {TextRole, "text"},     {MarkerRole, "marker"}, {DetailRole, "detail"}};
}

void ChatModel::set_lines(std::vector<presentation::ChatLine> lines) {
    beginResetModel();
    lines_.clear();
    lines_.reserve(static_cast<int>(lines.size()));
    for (auto& line : lines) {
        lines_.push_back(std::move(line));
    }
    endResetModel();
}

void ChatModel::append(presentation::ChatLine line) {
    const int row = static_cast<int>(lines_.size());
    beginInsertRows({}, row, row);
    lines_.push_back(std::move(line));
    endInsertRows();
}

void ChatModel::clear() {
    beginResetModel();
    lines_.clear();
    endResetModel();
}

QVector<int> ChatModel::match_rows(const QString& query) const {
    QVector<int> hits;
    const QString needle = query.trimmed();
    if (needle.isEmpty()) {
        return hits;
    }
    for (int row = 0; row < lines_.size(); ++row) {
        const QString hay = QString::fromStdString(lines_.at(row).text);
        if (hay.contains(needle, Qt::CaseInsensitive)) {
            hits.push_back(row);
        }
    }
    return hits;
}

}  // namespace i2pchat::gui
