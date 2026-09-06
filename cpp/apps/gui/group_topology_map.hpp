#pragma once

#include <QWidget>
#include <map>
#include <optional>
#include <string>

#include "i2pchat/groups/coordinator.hpp"

namespace i2pchat::gui {

class GroupTopologyMapWidget : public QWidget {
    Q_OBJECT
public:
    explicit GroupTopologyMapWidget(groups::TopologySnapshot snapshot, bool night,
                                    QWidget* parent = nullptr);

    void set_snapshot(groups::TopologySnapshot snapshot);
    void set_night(bool night);

signals:
    void peerActivated(const QString& peer_id);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void leaveEvent(QEvent* event) override;

private:
    struct Palette {
        QColor bg;
        QColor panel;
        QColor text;
        QColor muted;
        QColor border;
        QColor orbit;
        QColor accent;
        QColor live;
        QColor handshaking;
        QColor await_root;
        QColor blindbox;
        QColor degraded;
        QColor failed;
        QColor idle;
    };

    [[nodiscard]] Palette palette() const;
    [[nodiscard]] QColor edge_color(const QString& state) const;
    [[nodiscard]] QString status_key(const groups::TopologyNode& node,
                                     const groups::TopologyEdge* edge) const;
    [[nodiscard]] QString status_text(const QString& key) const;
    [[nodiscard]] QString status_chip(const QString& key) const;
    [[nodiscard]] std::vector<groups::TopologyNode> remotes() const;
    void layout_node_rects(const QRectF& map_rect, QRectF& local_rect,
                           std::map<std::string, QRectF>& node_rects) const;
    [[nodiscard]] std::optional<std::string> hit_test(const QPointF& pos) const;
    [[nodiscard]] QString tooltip_for(const std::string& peer_id) const;
    void draw_connection(QPainter& painter, const QRectF& local_rect, const QRectF& target,
                         const QString& state, bool dashed) const;
    void draw_local(QPainter& painter, const QRectF& rect, const Palette& pal) const;
    void draw_remote(QPainter& painter, const groups::TopologyNode& node,
                     const groups::TopologyEdge* edge, const QRectF& rect,
                     const Palette& pal) const;

    groups::TopologySnapshot snapshot_;
    bool night_ = false;
};

}  // namespace i2pchat::gui
