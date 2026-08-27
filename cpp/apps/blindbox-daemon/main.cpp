#include <boost/asio/io_context.hpp>
#include <boost/asio/signal_set.hpp>

#include <exception>
#include <iostream>
#include <memory>
#include <string>

#include "i2pchat/blindbox/replica_server.hpp"

/// The BlindBox replica daemon.
///
/// A standalone store-and-forward box: it holds sealed blobs for peers that are
/// offline and hands them over when they come back. It is configured entirely
/// through the environment and `.env` files, so a packaged unit needs no
/// arguments and no token on a command line where `ps` would show it.
///
/// Refusals go to stderr and to `audit.log` in a form fail2ban can act on.
int main(int argc, char** argv) {
    using namespace i2pchat;

    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (argument == "--help" || argument == "-h") {
            std::cout
                << "Usage: i2pchat-blindbox-daemon\n\n"
                   "Configured through the environment, or a .env file in the working\n"
                   "directory or in ~/.i2pchat-blindbox:\n"
                   "  BLINDBOX_HOST, BLINDBOX_PORT        listen address (127.0.0.1:19444)\n"
                   "  BLINDBOX_AUTH_TOKEN                 required for PUT and GET\n"
                   "  BLINDBOX_ADMIN_TOKEN                required for STATUS and METRICS\n"
                   "  BLINDBOX_MAX_BLOB                   largest accepted blob\n"
                   "  BLINDBOX_TTL_SEC                    how long a blob is kept\n"
                   "  BLINDBOX_MAX_FILES,       BLINDBOX_MAX_TOTAL_BYTES\n"
                   "  BLINDBOX_MAX_PREFIX_FILES BLINDBOX_MAX_PREFIX_BYTES\n"
                   "  BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE\n"
                   "  BLINDBOX_RATE_LIMIT_BYTES_PER_MINUTE\n"
                   "  BLINDBOX_GC_INTERVAL_SEC            sweep interval\n"
                   "  BLINDBOX_LOG_JSON                   JSON audit lines (default on)\n"
                   "  BLINDBOX_AUDIT_LOG_MAX_BYTES, BLINDBOX_AUDIT_LOG_BACKUPS\n"
                   "  BLINDBOX_HTTP_STATUS, BLINDBOX_HTTP_HOST, BLINDBOX_HTTP_PORT\n"
                   "  BLINDBOX_METRICS_JSON_PATH, BLINDBOX_METRICS_PROM_PATH\n";
            return 0;
        }
        std::cerr << "Unexpected argument: " << argument << "\n";
        return 2;
    }

    try {
        const blindbox::ReplicaServerConfig config = blindbox::config_from_environment();
        auto service = std::make_shared<blindbox::ReplicaService>(config);
        service->collect_garbage();

        boost::asio::io_context context;
        blindbox::ReplicaServer server(context.get_executor(), service);
        server.start();

        std::cout << "BlindBox listening on " << config.host << ":" << server.port()
                  << (config.auth_token.empty() ? " (open, no auth token)"
                                                : " (auth token configured)")
                  << "; limits: " << config.max_files << " files / "
                  << config.max_total_bytes << " bytes"
                  << ", prefix: " << config.max_prefix_files << " files / "
                  << config.max_prefix_bytes << " bytes"
                  << ", rate: " << config.rate_limit_puts_per_minute << " puts/min / "
                  << config.rate_limit_bytes_per_minute << " bytes/min\n";
        if (config.http_status) {
            std::cout << "BlindBox HTTP status on " << config.http_host << ":"
                      << server.status_port() << " (admin auth "
                      << (config.admin_token.empty() ? "off" : "on") << ")\n";
        }
        std::cout << std::flush;

        // SIGTERM is how systemd stops the unit; without handling it the store
        // would be left with whatever temporary files a PUT was mid-way through.
        boost::asio::signal_set signals(context, SIGINT, SIGTERM);
        signals.async_wait([&](const boost::system::error_code&, int) {
            server.stop();
            context.stop();
        });

        context.run();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "BlindBox failed to start: " << error.what() << "\n";
        return 1;
    }
}
