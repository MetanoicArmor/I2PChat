#pragma once

#include <QAbstractListModel>
#include <QVector>

#include "i2pchat/presentation/chat_view.hpp"

namespace i2pchat::gui {

enum class SidebarKind {
    Section,
    Peer,
    Group,
};

struct SidebarRow {
    SidebarKind kind = SidebarKind::Peer;
    QString title;
    QString subtitle;
    QString addr;
    bool live = false;
    unsigned unread = 0;
    bool selected = false;
};

class ContactListModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Role {
        KindRole = Qt::UserRole + 1,
        TitleRole,
        SubtitleRole,
        AddrRole,
        LiveRole,
        UnreadRole,
        SelectedRole,
    };

    explicit ContactListModel(QObject* parent = nullptr);

    [[nodiscard]] int rowCount(const QModelIndex& parent = {}) const override;
    [[nodiscard]] QVariant data(const QModelIndex& index, int role) const override;
    [[nodiscard]] Qt::ItemFlags flags(const QModelIndex& index) const override;

    void set_rows(QVector<SidebarRow> rows);
    [[nodiscard]] QString addr_at(int row) const;
    [[nodiscard]] bool is_peer(int row) const;
    [[nodiscard]] bool is_group(int row) const;
    [[nodiscard]] bool is_conversation(int row) const;

private:
    QVector<SidebarRow> rows_;
};

QVector<SidebarRow> sidebar_from_contacts(
    const std::vector<presentation::ContactRow>& peers,
    const QVector<SidebarRow>& groups = {});

}  // namespace i2pchat::gui
