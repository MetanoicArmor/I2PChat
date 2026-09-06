#include <QCoreApplication>
#include <QApplication>
#include <QFont>
#include <QPalette>
#include <QStyleFactory>
#include <iostream>
#include <string>
#include <vector>

#include "chat_window.hpp"
#include "profile_select_dialog.hpp"
#include "options.hpp"
#include "profile_select_dialog.hpp"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName("I2PChat");
    app.setOrganizationName("I2PChat");
    app.setApplicationVersion(I2PCHAT_VERSION);
    app.setQuitOnLastWindowClosed(true);
#ifdef Q_OS_MACOS
    app.setStyle(QStyleFactory::create(QStringLiteral("Fusion")));
#endif
    QFont font = app.font();
    if (font.pointSize() < 12) {
        font.setPointSize(12);
        app.setFont(font);
    }

    std::vector<std::string> args;
    const QStringList raw = QCoreApplication::arguments();
    for (int index = 1; index < raw.size(); ++index) {
        args.push_back(raw[index].toStdString());
    }
    const i2pchat::tui::ParseResult parsed = i2pchat::tui::parse_options(args);
    if (!parsed.error.empty()) {
        std::cerr << parsed.error << "\n";
        return 2;
    }
    if (parsed.options.help) {
        std::cout << i2pchat::tui::usage_text();
        return 0;
    }
    if (parsed.options.version) {
        std::cout << "i2pchat-gui " << I2PCHAT_VERSION << "\n";
        return 0;
    }

    i2pchat::gui::GuiOptions options;
    options.app_root = parsed.options.app_root;
    options.profile = parsed.options.profile;
    options.sam_host = parsed.options.sam_host;
    options.sam_port = parsed.options.sam_port;
    const QPalette palette = app.palette();
    options.dark = palette.color(QPalette::Window).lightness() < 128;

    if (!parsed.options.profile_from_cli) {
        i2pchat::gui::ProfileSelectDialog dialog(options.app_root, options.dark);
        if (dialog.exec() != QDialog::Accepted) {
            return 0;
        }
        const QString chosen = dialog.selected_profile();
        if (chosen.isEmpty()) {
            return 0;
        }
        options.profile = chosen.toStdString();
    }

    i2pchat::gui::ChatWindow window(std::move(options));
    window.show();
    return app.exec();
}
