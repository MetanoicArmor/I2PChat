#include "emoji_picker.hpp"
#include "emoji_chars.hpp"

#include <algorithm>
#include <cmath>
#include <QApplication>
#include <QColor>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QGridLayout>
#include <QIcon>
#include <QPainter>
#include <QPixmap>
#include <QScreen>
#include <QScrollArea>
#include <QToolButton>
#include <QVBoxLayout>
#include <fstream>

#include <nlohmann/json.hpp>

namespace i2pchat::gui {
namespace {

constexpr int kCols = 8;
constexpr int kCell = 36;

QIcon pixmap_icon(const QPixmap& src, int logical) {
    if (src.isNull()) {
        return {};
    }
    qreal dpr = 1.0;
    if (QScreen* screen = QApplication::primaryScreen()) {
        dpr = std::max(1.0, std::min(3.0, screen->devicePixelRatio()));
    }
    const int phys = std::max(1, static_cast<int>(std::lround(logical * dpr)));
    QPixmap canvas(phys, phys);
    canvas.fill(Qt::transparent);
    const QPixmap scaled =
        src.scaled(phys, phys, Qt::KeepAspectRatio, Qt::SmoothTransformation);
    QPainter p(&canvas);
    p.drawPixmap((phys - scaled.width()) / 2, (phys - scaled.height()) / 2, scaled);
    p.end();
    canvas.setDevicePixelRatio(dpr);
    return QIcon(canvas);
}

QPixmap tint_alpha(const QPixmap& source, const QColor& color) {
    if (source.isNull()) {
        return source;
    }
    QPixmap work = source;
    work.setDevicePixelRatio(1.0);
    QPixmap out(work.size());
    out.fill(Qt::transparent);
    QPainter p(&out);
    p.fillRect(out.rect(), color);
    p.setCompositionMode(QPainter::CompositionMode_DestinationIn);
    p.drawPixmap(0, 0, work);
    p.end();
    out.setDevicePixelRatio(source.devicePixelRatio());
    return out;
}

}  // namespace

std::filesystem::path find_fluent_emoji_root() {
    const QDir exe(QCoreApplication::applicationDirPath());
    const QStringList rels = {
        QStringLiteral("../Resources/fluent_emoji"),
        QStringLiteral("../../Resources/fluent_emoji"),
        QStringLiteral("../../../../i2pchat/gui/fluent_emoji"),
        QStringLiteral("../../../i2pchat/gui/fluent_emoji"),
        QStringLiteral("../../../../../i2pchat/gui/fluent_emoji"),
        QStringLiteral("../i2pchat/gui/fluent_emoji"),
    };
    for (const QString& rel : rels) {
        const QDir dir(QDir::cleanPath(exe.absoluteFilePath(rel)));
        if (QFile::exists(dir.filePath(QStringLiteral("manifest.json")))) {
            return dir.absolutePath().toStdString();
        }
    }
    return {};
}

QIcon tinted_face_icon(bool dark) {
    const QDir exe(QCoreApplication::applicationDirPath());
    const QStringList rels = {
        QStringLiteral(":/i2pchat/icons/face.dashed.png"),
        exe.absoluteFilePath(QStringLiteral("../Resources/icons/face.dashed.png")),
        exe.absoluteFilePath(QStringLiteral("../../../../i2pchat/gui/icons/face.dashed.png")),
        exe.absoluteFilePath(QStringLiteral("../../../i2pchat/gui/icons/face.dashed.png")),
        exe.absoluteFilePath(QStringLiteral("../../../../../i2pchat/gui/icons/face.dashed.png")),
    };
    QPixmap pm;
    for (const QString& path : rels) {
        pm = QPixmap(path);
        if (!pm.isNull()) {
            break;
        }
    }
    if (pm.isNull()) {
        return {};
    }
    const QColor color = dark ? QColor(245, 245, 247, 140) : QColor(60, 60, 67, 140);
    return QIcon(tint_alpha(pm, color));
}

EmojiPickerPopup::EmojiPickerPopup(QWidget* parent) : QFrame(parent) {
    setObjectName("EmojiPickerPopupWindow");
    setWindowFlags(Qt::Popup | Qt::FramelessWindowHint);
    setAttribute(Qt::WA_TranslucentBackground, true);
    setFocusPolicy(Qt::StrongFocus);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    auto* surface = new QFrame(this);
    surface->setObjectName("EmojiPickerPopupSurface");
    root->addWidget(surface);
    auto* lay = new QVBoxLayout(surface);
    lay->setContentsMargins(6, 6, 6, 6);
    scroll_ = new QScrollArea(surface);
    scroll_->setObjectName("EmojiPickerScroll");
    scroll_->setWidgetResizable(true);
    scroll_->setFrameShape(QFrame::NoFrame);
    scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scroll_->setFixedHeight(260);
    inner_ = new QWidget();
    inner_->setObjectName("EmojiPickerGridHost");
    scroll_->setWidget(inner_);
    lay->addWidget(scroll_);
    setFixedWidth(kCols * 44 + 24);
    root_ = find_fluent_emoji_root();
    if (!root_.empty()) {
        std::ifstream in(root_ / "manifest.json");
        nlohmann::json doc = nlohmann::json::parse(in, nullptr, false);
        if (doc.is_object()) {
            for (auto it = doc.begin(); it != doc.end(); ++it) {
                if (it.value().is_string()) {
                    png_by_glyph_.insert(QString::fromStdString(it.key()),
                                         QString::fromStdString(it.value().get<std::string>()));
                }
            }
        }
    }
    rebuild();
}

void EmojiPickerPopup::rebuild() {
    auto* grid = new QGridLayout(inner_);
    grid->setSpacing(4);
    grid->setContentsMargins(8, 6, 8, 6);
    int i = 0;
    for (const std::string_view raw : kEmojiChars) {
        const QString glyph = QString::fromUtf8(raw.data(), static_cast<int>(raw.size()));
        auto* btn = new QToolButton(inner_);
        btn->setObjectName("EmojiCell");
        btn->setAutoRaise(true);
        btn->setFocusPolicy(Qt::NoFocus);
        btn->setFixedSize(kCell, kCell);
        btn->setToolTip(glyph);
        QIcon icon;
        const auto it = png_by_glyph_.constFind(glyph);
        if (it != png_by_glyph_.cend() && !root_.empty()) {
            const QPixmap pm(QString::fromStdString((root_ / it->toStdString()).string()));
            icon = pixmap_icon(pm, 28);
        }
        if (!icon.isNull()) {
            btn->setIcon(icon);
            btn->setIconSize(QSize(28, 28));
        } else {
            btn->setText(glyph);
        }
        connect(btn, &QToolButton::clicked, this, [this, glyph] {
            emit emoji_chosen(glyph);
            hide();
        });
        grid->addWidget(btn, i / kCols, i % kCols);
        ++i;
    }
}

void EmojiPickerPopup::set_night(bool night) { night_ = night; }

void EmojiPickerPopup::show_above(QWidget* anchor) {
    adjustSize();
    QPoint pos = anchor->mapToGlobal(QPoint(0, -height() - 6));
    if (QScreen* screen = QApplication::screenAt(pos)) {
        const QRect avail = screen->availableGeometry();
        pos.setX(std::min(pos.x(), avail.right() - width() + 1));
        pos.setY(std::max(pos.y(), avail.top()));
        pos.setX(std::max(pos.x(), avail.left()));
    }
    move(pos);
    show();
    raise();
}

}  // namespace i2pchat::gui
