#include "contact_list_model.hpp"

namespace i2pchat::gui {

ContactListModel::ContactListModel(QObject* parent) : QAbstractListModel(parent) {}

int ContactListModel::rowCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : static_cast<int>(rows_.size());
}

QVariant ContactListModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= rows_.size()) {
        return {};
    }
    const SidebarRow& row = rows_.at(index.row());
    switch (role) {
        case Qt::DisplayRole:
        case TitleRole:
            return row.title;
        case KindRole:
            return static_cast<int>(row.kind);
        case SubtitleRole:
            return row.subtitle;
        case AddrRole:
            return row.addr;
        case LiveRole:
            return row.live;
        case UnreadRole:
            return row.unread;
        case SelectedRole:
            return row.selected;
        default:
            return {};
    }
}

Qt::ItemFlags ContactListModel::flags(const QModelIndex& index) const {
    if (!index.isValid() || !is_conversation(index.row())) {
        return Qt::ItemIsEnabled;
    }
    return Qt::ItemIsEnabled | Qt::ItemIsSelectable;
}

void ContactListModel::set_rows(QVector<SidebarRow> rows) {
    beginResetModel();
    rows_ = std::move(rows);
    endResetModel();
}

QString ContactListModel::addr_at(int row) const {
    if (row < 0 || row >= rows_.size()) {
        return {};
    }
    return rows_.at(row).addr;
}

bool ContactListModel::is_peer(int row) const {
    return row >= 0 && row < rows_.size() && rows_.at(row).kind == SidebarKind::Peer;
}

bool ContactListModel::is_group(int row) const {
    return row >= 0 && row < rows_.size() && rows_.at(row).kind == SidebarKind::Group;
}

bool ContactListModel::is_conversation(int row) const {
    return is_peer(row) || is_group(row);
}

QVector<SidebarRow> sidebar_from_contacts(const std::vector<presentation::ContactRow>& peers,
                                          const QVector<SidebarRow>& groups) {
    QVector<SidebarRow> rows;
    if (groups.isEmpty()) {
        SidebarRow empty_groups;
        empty_groups.kind = SidebarKind::Section;
        empty_groups.title = QStringLiteral("No groups yet");
        empty_groups.subtitle = QStringLiteral("Create one with New or ⋯ → New text group");
        rows.push_back(std::move(empty_groups));
    } else {
        for (const SidebarRow& group : groups) {
            rows.push_back(group);
        }
    }

    SidebarRow saved;
    saved.kind = SidebarKind::Section;
    saved.title = QStringLiteral("Saved peers");
    rows.push_back(std::move(saved));

    for (const auto& peer : peers) {
        SidebarRow row;
        row.kind = SidebarKind::Peer;
        row.title = QString::fromStdString(peer.label);
        row.subtitle = QString::fromStdString(peer.preview);
        if (row.subtitle.isEmpty()) {
            row.subtitle = QString::fromStdString(presentation::short_address(peer.addr));
        }
        row.addr = QString::fromStdString(peer.addr);
        row.live = peer.live;
        row.unread = peer.unread;
        row.selected = peer.selected;
        rows.push_back(std::move(row));
    }
    return rows;
}

}  // namespace i2pchat::gui
