#include <QCoreApplication>
#include <QApplication>
#include <QPalette>
#include <iostream>
#include <string>
#include <vector>

#include "chat_window.hpp"
#include "options.hpp"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName("I2PChat");
    app.setOrganizationName("I2PChat");
    app.setApplicationVersion(I2PCHAT_VERSION);
    app.setQuitOnLastWindowClosed(false);

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

    i2pchat::gui::ChatWindow window(std::move(options));
    window.show();
    return app.exec();
}
