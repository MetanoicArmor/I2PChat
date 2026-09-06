#include "group_topology_map.hpp"

#include <QEvent>
#include <QFontMetricsF>
#include <QLinearGradient>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QStringList>
#include <QToolTip>
#include <algorithm>
#include <cmath>

namespace i2pchat::gui {
namespace {

QString elide(QPainter& painter, const QString& text, qreal width) {
    return QFontMetricsF(painter.font()).elidedText(text, Qt::ElideRight, std::max(16.0, width));
}

std::vector<qreal> column_positions(int count, qreal top, qreal bottom, qreal item_height) {
    std::vector<qreal> out;
    if (count <= 0) {
        return out;
    }
    const qreal usable = std::max(item_height, bottom - top);
    if (count == 1) {
        out.push_back(top + usable / 2.0);
        return out;
    }
    const qreal gap = std::max(12.0, (usable - item_height * count) / static_cast<qreal>(count - 1));
    const qreal start = top + item_height / 2.0;
    for (int i = 0; i < count; ++i) {
        out.push_back(start + i * (item_height + gap));
    }
    return out;
}

}  // namespace

GroupTopologyMapWidget::GroupTopologyMapWidget(groups::TopologySnapshot snapshot, bool night,
                                               QWidget* parent)
    : QWidget(parent), snapshot_(std::move(snapshot)), night_(night) {
    setObjectName("GroupTopologyMapWidget");
    setMinimumSize(640, 420);
    setMouseTracking(true);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
}

void GroupTopologyMapWidget::set_snapshot(groups::TopologySnapshot snapshot) {
    snapshot_ = std::move(snapshot);
    update();
}

void GroupTopologyMapWidget::set_night(bool night) {
    night_ = night;
    update();
}

GroupTopologyMapWidget::Palette GroupTopologyMapWidget::palette() const {
    Palette pal;
    pal.bg = QColor(night_ ? "#13161d" : "#f4f7fb");
    pal.panel = QColor(night_ ? "#1c212b" : "#ffffff");
    pal.text = QColor(night_ ? "#f5f7fb" : "#17212d");
    pal.muted = QColor(night_ ? "#b5bdca" : "#64748b");
    pal.border = QColor(night_ ? "#394457" : "#d6dfeb");
    pal.orbit = QColor(night_ ? "#2d3950" : "#d9e4f2");
    pal.accent = QColor(night_ ? "#4ca1ff" : "#2076e6");
    pal.live = QColor("#2fb36c");
    pal.handshaking = QColor("#f0a128");
    pal.await_root = QColor("#f0a128");
    pal.blindbox = QColor("#6c63ff");
    pal.degraded = QColor("#d87a2f");
    pal.failed = QColor("#de4f4f");
    pal.idle = QColor("#8b93a7");
    return pal;
}

QColor GroupTopologyMapWidget::edge_color(const QString& state) const {
    const Palette pal = palette();
    if (state == QLatin1String("live")) {
        return pal.live;
    }
    if (state == QLatin1String("handshaking")) {
        return pal.handshaking;
    }
    if (state == QLatin1String("await-root")) {
        return pal.await_root;
    }
    if (state == QLatin1String("blindbox")) {
        return pal.blindbox;
    }
    if (state == QLatin1String("degraded")) {
        return pal.degraded;
    }
    if (state == QLatin1String("failed")) {
        return pal.failed;
    }
    return pal.idle;
}

QString GroupTopologyMapWidget::status_key(const groups::TopologyNode& node,
                                           const groups::TopologyEdge* edge) const {
    if (edge != nullptr) {
        const QString raw = QString::fromUtf8(groups::link_state_name(edge->state).data());
        if (!raw.isEmpty()) {
            return raw;
        }
    }
    if (node.live_ready) {
        return QStringLiteral("live");
    }
    const QString peer = QString::fromStdString(node.peer_state).toLower();
    if (peer == QLatin1String("connecting") || peer == QLatin1String("handshaking")) {
        return QStringLiteral("handshaking");
    }
    if (snapshot_.await_group_root && !node.blindbox_ready) {
        return QStringLiteral("await-root");
    }
    if (peer == QLatin1String("stale")) {
        return QStringLiteral("degraded");
    }
    if (peer == QLatin1String("failed")) {
        return QStringLiteral("failed");
    }
    if (node.blindbox_ready) {
        return QStringLiteral("blindbox");
    }
    return QStringLiteral("idle");
}

QString GroupTopologyMapWidget::status_text(const QString& key) const {
    if (key == QLatin1String("live")) {
        return QStringLiteral("Live secure");
    }
    if (key == QLatin1String("handshaking")) {
        return QStringLiteral("Secure intro in progress");
    }
    if (key == QLatin1String("await-root")) {
        return QStringLiteral("Awaiting BlindBox root");
    }
    if (key == QLatin1String("blindbox")) {
        return QStringLiteral("Offline route ready");
    }
    if (key == QLatin1String("degraded")) {
        return QStringLiteral("Needs refresh");
    }
    if (key == QLatin1String("failed")) {
        return QStringLiteral("Last connect failed");
    }
    return QStringLiteral("Idle");
}

QString GroupTopologyMapWidget::status_chip(const QString& key) const {
    if (key == QLatin1String("live")) {
        return QStringLiteral("LIVE");
    }
    if (key == QLatin1String("handshaking")) {
        return QStringLiteral("INTRO");
    }
    if (key == QLatin1String("await-root")) {
        return QStringLiteral("ROOT");
    }
    if (key == QLatin1String("blindbox")) {
        return QStringLiteral("BLINDBOX");
    }
    if (key == QLatin1String("degraded")) {
        return QStringLiteral("STALE");
    }
    if (key == QLatin1String("failed")) {
        return QStringLiteral("FAILED");
    }
    return QStringLiteral("IDLE");
}

std::vector<groups::TopologyNode> GroupTopologyMapWidget::remotes() const {
    std::vector<groups::TopologyNode> out;
    for (const groups::TopologyNode& node : snapshot_.nodes) {
        if (!node.is_local) {
            out.push_back(node);
        }
    }
    return out;
}

void GroupTopologyMapWidget::layout_node_rects(const QRectF& map_rect, QRectF& local_rect,
                                               std::map<std::string, QRectF>& node_rects) const {
    const auto remote = remotes();
    const qreal circle_d = std::max(132.0, std::min(188.0, map_rect.height() * 0.34));
    const qreal card_w = std::max(188.0, std::min(248.0, map_rect.width() * 0.26));
    const qreal card_h = std::max(88.0, std::min(108.0, map_rect.height() * 0.20));
    const qreal center_x = map_rect.center().x();
    const qreal center_y = map_rect.center().y() + 10.0;
    local_rect = QRectF(center_x - circle_d / 2.0, center_y - circle_d / 2.0, circle_d, circle_d);

    std::vector<groups::TopologyNode> left;
    std::vector<groups::TopologyNode> right;
    for (std::size_t i = 0; i < remote.size(); ++i) {
        (i % 2 == 0 ? left : right).push_back(remote[i]);
    }
    const qreal top = map_rect.top() + 30.0;
    const qreal bottom = map_rect.bottom() - 12.0;
    const qreal left_x = map_rect.left() + 12.0;
    const qreal right_x = map_rect.right() - 12.0 - card_w;
    const std::vector<qreal> left_ys =
        column_positions(static_cast<int>(left.size()), top, bottom, card_h);
    const std::vector<qreal> right_ys =
        column_positions(static_cast<int>(right.size()), top, bottom, card_h);
    for (std::size_t i = 0; i < left.size(); ++i) {
        node_rects[left[i].member_id] =
            QRectF(left_x, left_ys[i] - card_h / 2.0, card_w, card_h);
    }
    for (std::size_t i = 0; i < right.size(); ++i) {
        node_rects[right[i].member_id] =
            QRectF(right_x, right_ys[i] - card_h / 2.0, card_w, card_h);
    }
}

std::optional<std::string> GroupTopologyMapWidget::hit_test(const QPointF& pos) const {
    const QRectF outer = QRectF(rect()).adjusted(14, 14, -14, -14);
    const QRectF inner = outer.adjusted(18, 18, -18, -18);
    const QRectF map_rect = inner.adjusted(4, 4, -4, -4);
    QRectF local;
    std::map<std::string, QRectF> nodes;
    layout_node_rects(map_rect, local, nodes);
    for (const auto& [id, box] : nodes) {
        if (box.contains(pos)) {
            return id;
        }
    }
    return std::nullopt;
}

QString GroupTopologyMapWidget::tooltip_for(const std::string& peer_id) const {
    const groups::TopologyNode* node = nullptr;
    for (const groups::TopologyNode& candidate : snapshot_.nodes) {
        if (candidate.member_id == peer_id) {
            node = &candidate;
            break;
        }
    }
    if (node == nullptr) {
        return {};
    }
    const groups::TopologyEdge* edge = nullptr;
    for (const groups::TopologyEdge& candidate : snapshot_.edges) {
        if (candidate.target_id == peer_id) {
            edge = &candidate;
            break;
        }
    }
    const QString key = status_key(*node, edge);
    QStringList lines;
    lines << QString::fromStdString(node->label);
    lines << QString::fromStdString(peer_id);
    lines << QStringLiteral("Route: %1").arg(status_text(key));
    if (!node->peer_state.empty()) {
        lines << QStringLiteral("Peer state: %1").arg(QString::fromStdString(node->peer_state));
    }
    if (snapshot_.await_group_root && !node->blindbox_ready) {
        lines << QStringLiteral("BlindBox: awaiting pairwise root");
    } else if (node->blindbox_ready) {
        lines << QStringLiteral("BlindBox: ready");
    } else {
        lines << QStringLiteral("BlindBox: not ready");
    }
    if (!node->last_delivery_status.empty()) {
        lines << QStringLiteral("Last delivery: %1")
                     .arg(QString::fromStdString(node->last_delivery_status));
    }
    if (!node->last_delivery_reason.empty()) {
        lines << QStringLiteral("Details: %1").arg(QString::fromStdString(node->last_delivery_reason));
    }
    lines << QStringLiteral("Click to open direct chat.");
    return lines.join('\n');
}

void GroupTopologyMapWidget::draw_connection(QPainter& painter, const QRectF& local_rect,
                                             const QRectF& target, const QString& state,
                                             bool dashed) const {
    const QColor base = edge_color(state);
    QPen pen(base, 2.6);
    if (dashed) {
        pen.setStyle(Qt::DashLine);
    }
    pen.setCapStyle(Qt::RoundCap);
    QPointF source(local_rect.center());
    QPointF dest;
    if (target.center().x() < local_rect.center().x()) {
        source.setX(local_rect.left() + 8.0);
        dest = QPointF(target.right() - 2.0, target.center().y());
    } else {
        source.setX(local_rect.right() - 8.0);
        dest = QPointF(target.left() + 2.0, target.center().y());
    }
    const qreal spread = std::abs(dest.x() - source.x()) * 0.42;
    const QPointF ctrl1(source.x() + (dest.x() > source.x() ? spread : -spread), source.y());
    const QPointF ctrl2(dest.x() - (dest.x() > source.x() ? spread : -spread), dest.y());
    QPainterPath path(source);
    path.cubicTo(ctrl1, ctrl2, dest);
    QColor glow_color = base;
    glow_color.setAlpha(night_ ? 48 : 36);
    QPen glow(glow_color, 7.0);
    if (dashed) {
        glow.setStyle(Qt::DashLine);
    }
    glow.setCapStyle(Qt::RoundCap);
    painter.save();
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setPen(glow);
    painter.drawPath(path);
    painter.setPen(pen);
    painter.drawPath(path);
    painter.setPen(Qt::NoPen);
    painter.setBrush(base);
    painter.drawEllipse(dest, 4.0, 4.0);
    painter.restore();
}

void GroupTopologyMapWidget::draw_local(QPainter& painter, const QRectF& rect,
                                        const Palette& pal) const {
    painter.save();
    painter.setRenderHint(QPainter::Antialiasing, true);
    QLinearGradient grad(rect.topLeft(), rect.bottomRight());
    grad.setColorAt(0.0, pal.accent);
    QColor light = pal.accent.lighter(118);
    grad.setColorAt(1.0, light);
    painter.setPen(QPen(pal.accent.darker(120), 1.4));
    painter.setBrush(grad);
    painter.drawEllipse(rect);
    painter.setPen(QPen(QColor(255, 255, 255, 44), 1.2));
    painter.setBrush(Qt::NoBrush);
    painter.drawEllipse(rect.adjusted(11, 11, -11, -11));
    QFont title = font();
    title.setPointSize(std::max(14, title.pointSize() + 2));
    title.setBold(true);
    painter.setFont(title);
    painter.setPen(QColor("#ffffff"));
    painter.drawText(rect.adjusted(10, 10, -10, -10), Qt::AlignCenter, QStringLiteral("You"));
    painter.restore();
}

void GroupTopologyMapWidget::draw_remote(QPainter& painter, const groups::TopologyNode& node,
                                         const groups::TopologyEdge* edge, const QRectF& rect,
                                         const Palette& pal) const {
    const QString key = status_key(node, edge);
    const QColor status = edge_color(key);
    QColor accent_fill = status;
    accent_fill.setAlpha(night_ ? 32 : 24);
    painter.save();
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setPen(QPen(pal.border, 1.0));
    painter.setBrush(pal.panel);
    painter.drawRoundedRect(rect, 18, 18);
    painter.setPen(Qt::NoPen);
    painter.setBrush(status);
    painter.drawRoundedRect(QRectF(rect.left(), rect.top(), 10.0, rect.height()), 18, 18);
    painter.setBrush(accent_fill);
    painter.drawRoundedRect(rect.adjusted(10, 0, 0, 0), 16, 16);

    QFont name_font = font();
    name_font.setPointSize(std::max(10, name_font.pointSize()));
    name_font.setWeight(QFont::DemiBold);
    painter.setFont(name_font);
    painter.setPen(pal.text);
    const QRectF name_rect = rect.adjusted(22, 10, -92, -52);
    painter.drawText(name_rect, Qt::AlignLeft | Qt::AlignTop,
                     elide(painter, QString::fromStdString(node.label), name_rect.width()));

    QFont chip_font = font();
    chip_font.setPointSize(std::max(8, chip_font.pointSize() - 1));
    chip_font.setBold(true);
    const QRectF chip_rect(rect.right() - 86.0, rect.top() + 10.0, 72.0, 22.0);
    painter.setPen(QPen(status.darker(118), 1.0));
    painter.setBrush(status);
    painter.drawRoundedRect(chip_rect, 11, 11);
    painter.setFont(chip_font);
    painter.setPen(QColor("#ffffff"));
    painter.drawText(chip_rect, Qt::AlignCenter, status_chip(key));

    QFont subtitle = font();
    subtitle.setPointSize(std::max(9, subtitle.pointSize() - 1));
    painter.setFont(subtitle);
    painter.setPen(pal.muted);
    const QRectF subtitle_rect = rect.adjusted(22, 34, -14, -34);
    painter.drawText(subtitle_rect, Qt::AlignLeft | Qt::AlignTop,
                     elide(painter, status_text(key), subtitle_rect.width()));

    QStringList bits;
    if (node.blindbox_ready && key != QLatin1String("blindbox")) {
        bits << QStringLiteral("BlindBox ready");
    }
    if (!node.last_delivery_status.empty()) {
        bits << QStringLiteral("Last: %1").arg(QString::fromStdString(node.last_delivery_status));
    }
    if (!node.last_delivery_reason.empty()) {
        bits << QString::fromStdString(node.last_delivery_reason);
    }
    if (!bits.isEmpty()) {
        painter.setPen(pal.text);
        const QRectF footer = rect.adjusted(22, 56, -14, -10);
        painter.drawText(footer, Qt::AlignLeft | Qt::AlignTop,
                         elide(painter, bits.join(QStringLiteral(" · ")), footer.width()));
    }
    painter.restore();
}

void GroupTopologyMapWidget::paintEvent(QPaintEvent*) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const Palette pal = palette();
    const QRectF outer = QRectF(rect()).adjusted(14, 14, -14, -14);
    painter.setPen(QPen(pal.border, 1.0));
    painter.setBrush(pal.bg);
    painter.drawRoundedRect(outer, 20, 20);

    const QRectF inner = outer.adjusted(18, 18, -18, -18);
    const QRectF map_rect = inner.adjusted(4, 4, -4, -4);
    const auto remote = remotes();
    if (remote.empty()) {
        painter.setPen(pal.muted);
        painter.drawText(map_rect, Qt::AlignCenter, tr("No remote members in this group yet."));
        return;
    }

    QRectF local_rect;
    std::map<std::string, QRectF> node_rects;
    layout_node_rects(map_rect, local_rect, node_rects);
    const QPointF center = local_rect.center();
    const qreal span = std::max(local_rect.width(), local_rect.height());
    painter.save();
    painter.setBrush(Qt::NoBrush);
    QPen orbit(pal.orbit, 1.1);
    orbit.setStyle(Qt::DotLine);
    painter.setPen(orbit);
    painter.drawEllipse(center, span * 0.88, span * 0.88);
    painter.drawEllipse(center, span * 0.64, span * 0.64);
    painter.drawEllipse(center, span * 0.46, span * 0.46);
    QColor guide_color = pal.orbit;
    guide_color.setAlpha(night_ ? 58 : 66);
    QPen guide(guide_color, 1.0, Qt::DashLine);
    painter.setPen(guide);
    painter.drawLine(QPointF(map_rect.left() + 18.0, center.y()),
                     QPointF(map_rect.right() - 18.0, center.y()));
    painter.drawLine(QPointF(center.x(), map_rect.top() + 22.0),
                     QPointF(center.x(), map_rect.bottom() - 22.0));
    painter.restore();

    std::map<std::string, const groups::TopologyEdge*> edges;
    for (const groups::TopologyEdge& edge : snapshot_.edges) {
        edges[edge.target_id] = &edge;
    }
    for (const groups::TopologyNode& node : remote) {
        const auto found = node_rects.find(node.member_id);
        if (found == node_rects.end()) {
            continue;
        }
        const groups::TopologyEdge* edge = edges.count(node.member_id) ? edges[node.member_id] : nullptr;
        const bool dashed = edge != nullptr && edge->blindbox_ready && !edge->live_ready;
        draw_connection(painter, local_rect, found->second, status_key(node, edge), dashed);
    }
    draw_local(painter, local_rect, pal);
    for (const groups::TopologyNode& node : remote) {
        const auto found = node_rects.find(node.member_id);
        if (found == node_rects.end()) {
            continue;
        }
        draw_remote(painter, node, edges.count(node.member_id) ? edges[node.member_id] : nullptr,
                    found->second, pal);
    }
}

void GroupTopologyMapWidget::mouseMoveEvent(QMouseEvent* event) {
    const auto hit = hit_test(event->position());
    if (hit) {
        setCursor(Qt::PointingHandCursor);
        QToolTip::showText(event->globalPosition().toPoint(), tooltip_for(*hit), this);
    } else {
        setCursor(Qt::ArrowCursor);
        QToolTip::hideText();
    }
    QWidget::mouseMoveEvent(event);
}

void GroupTopologyMapWidget::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        if (const auto hit = hit_test(event->position())) {
            emit peerActivated(QString::fromStdString(*hit));
            event->accept();
            return;
        }
    }
    QWidget::mousePressEvent(event);
}

void GroupTopologyMapWidget::leaveEvent(QEvent* event) {
    setCursor(Qt::ArrowCursor);
    QToolTip::hideText();
    QWidget::leaveEvent(event);
}

}  // namespace i2pchat::gui
