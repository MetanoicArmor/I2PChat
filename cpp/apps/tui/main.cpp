#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

#include "app.hpp"
#include "options.hpp"

int main(int argc, char** argv) {
    std::vector<std::string> args;
    args.reserve(static_cast<std::size_t>(std::max(0, argc - 1)));
    for (int index = 1; index < argc; ++index) {
        args.emplace_back(argv[index]);
    }

    const i2pchat::tui::ParseResult parsed = i2pchat::tui::parse_options(args);
    if (!parsed.error.empty()) {
        std::cerr << parsed.error << "\n\n" << i2pchat::tui::usage_text();
        return 2;
    }
    if (parsed.options.help) {
        std::cout << i2pchat::tui::usage_text();
        return 0;
    }
    if (parsed.options.version) {
        std::cout << "i2pchat-tui " << I2PCHAT_VERSION << "\n";
        return 0;
    }

    try {
        i2pchat::tui::TuiApp app(parsed.options);
        return app.run();
    } catch (const std::exception& error) {
        std::cerr << "i2pchat-tui: " << error.what() << "\n";
        return 1;
    }
}
