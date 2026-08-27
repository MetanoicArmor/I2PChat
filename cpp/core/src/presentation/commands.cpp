#include "i2pchat/presentation/commands.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <map>

namespace i2pchat::presentation {
namespace {

struct Entry {
    CommandKind kind;
    std::string_view name;
    std::string_view usage;
    std::string_view summary;
};

/// The command table. Order is the order `/help` prints, which is grouped by
/// what a user is doing rather than alphabetically.
const std::array<Entry, 30> kCommands{{
    {CommandKind::Help, "help", "/help", "List every command"},
    {CommandKind::Status, "status", "/status", "Transport, peers and offline queue"},
    {CommandKind::Quit, "quit", "/quit", "Close the client"},

    {CommandKind::Connect, "connect", "/connect <address>",
     "Dial a peer by b32 address or destination"},
    {CommandKind::Disconnect, "disconnect", "/disconnect [address]",
     "Hang up on a peer, or the selected one"},
    {CommandKind::Reply, "reply", "/reply <address> <text>",
     "Send to a peer without selecting it"},

    {CommandKind::Contacts, "contacts", "/contacts", "Show the contact list"},
    {CommandKind::ContactAdd, "contact-add",
     "/contact-add <address> [name] [note]", "Remember a peer"},
    {CommandKind::ContactUse, "contact-use", "/contact-use <index|address>",
     "Select a contact"},
    {CommandKind::ContactEdit, "contact-edit",
     "/contact-edit <index|address> <name> [note]", "Rename a contact"},
    {CommandKind::ContactRemove, "contact-remove", "/contact-remove <index|address>",
     "Forget a contact"},
    {CommandKind::ContactInfo, "contact-info", "/contact-info [index|address]",
     "Show one contact in full"},
    {CommandKind::Recent, "recent", "/recent", "Most recently active peers"},

    {CommandKind::SendFile, "sendfile", "/sendfile <path>",
     "Send a file over the live channel"},
    {CommandKind::SendPicture, "sendpic", "/sendpic <path>", "Send an inline image"},
    {CommandKind::Transfers, "transfers", "/transfers", "Transfers in flight"},

    {CommandKind::History, "history", "/history [count]",
     "Replay the conversation from disk"},
    {CommandKind::HistoryClear, "history-clear", "/history-clear [address]",
     "Delete a conversation"},
    {CommandKind::HistoryRetention, "history-retention",
     "/history-retention <messages> [days]", "Set how much history is kept"},

    {CommandKind::BlindBox, "blindbox", "/blindbox", "Offline delivery status"},
    {CommandKind::BlindBoxPoll, "blindbox-poll", "/blindbox-poll",
     "Collect waiting offline messages now"},

    {CommandKind::TrustInfo, "trust-info", "/trust-info [address]",
     "Show the pinned signing key"},
    {CommandKind::ForgetPin, "forget-pin", "/forget-pin <address>",
     "Drop a pin so the next connection is a first sighting"},

    {CommandKind::CopyAddress, "copyaddr", "/copyaddr", "Show this client's address"},
    {CommandKind::AppDir, "appdir", "/appdir", "Where profile data lives"},
    {CommandKind::Router, "router", "/router", "Bundled router status"},
    {CommandKind::Diagnostics, "diagnostics", "/diagnostics", "Diagnostics screen"},
    {CommandKind::Settings, "settings", "/settings", "Settings screen"},
    {CommandKind::Profiles, "profiles", "/profiles", "List profiles on this machine"},
    {CommandKind::Group, "group", "/group <subcommand>", "Group conversations"},
}};

std::string lowered(std::string_view text) {
    std::string out;
    out.reserve(text.size());
    for (const char ch : text) {
        out.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }
    return out;
}

std::string_view trimmed(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return text.substr(begin, end - begin + 1);
}

}  // namespace

const std::string& Command::arg(std::size_t index) const {
    static const std::string empty;
    return index < args.size() ? args[index] : empty;
}

std::vector<std::string> tokenize(std::string_view line) {
    std::vector<std::string> tokens;
    std::string current;
    bool in_token = false;
    char quote = '\0';

    for (std::size_t index = 0; index < line.size(); ++index) {
        const char ch = line[index];
        if (ch == '\\' && index + 1 < line.size()) {
            current.push_back(line[index + 1]);
            in_token = true;
            ++index;
            continue;
        }
        if (quote != '\0') {
            if (ch == quote) {
                quote = '\0';
            } else {
                current.push_back(ch);
            }
            continue;
        }
        if (ch == '"' || ch == '\'') {
            quote = ch;
            // An empty quoted string is still a token.
            in_token = true;
            continue;
        }
        if (std::isspace(static_cast<unsigned char>(ch)) != 0) {
            if (in_token) {
                tokens.push_back(current);
                current.clear();
                in_token = false;
            }
            continue;
        }
        current.push_back(ch);
        in_token = true;
    }
    if (in_token) {
        tokens.push_back(current);
    }
    return tokens;
}

Command parse_command(std::string_view line) {
    Command command;
    const std::string_view body = trimmed(line);
    // A multi-line paste is text even when it opens with a slash.
    if (body.empty() || body.front() != '/' || line.find('\n') != std::string_view::npos) {
        command.kind = CommandKind::Text;
        command.rest = std::string(line);
        return command;
    }

    const std::string_view without_slash = body.substr(1);
    const std::size_t space = without_slash.find_first_of(" \t");
    const std::string_view word = without_slash.substr(0, space);
    command.name = lowered(word);
    command.rest = space == std::string_view::npos
                       ? std::string{}
                       : std::string(trimmed(without_slash.substr(space)));
    command.args = tokenize(command.rest);

    const auto found = std::find_if(kCommands.begin(), kCommands.end(),
                                    [&](const Entry& entry) {
                                        return entry.name == command.name;
                                    });
    command.kind = found == kCommands.end() ? CommandKind::Unknown : found->kind;
    return command;
}

std::string_view command_name(CommandKind kind) {
    const auto found = std::find_if(kCommands.begin(), kCommands.end(),
                                    [kind](const Entry& entry) {
                                        return entry.kind == kind;
                                    });
    return found == kCommands.end() ? std::string_view{} : found->name;
}

const std::vector<CommandHelp>& command_help() {
    static const std::vector<CommandHelp> help = [] {
        std::vector<CommandHelp> out;
        out.reserve(kCommands.size());
        for (const Entry& entry : kCommands) {
            out.push_back(CommandHelp{entry.usage, entry.summary});
        }
        return out;
    }();
    return help;
}

std::vector<std::string> complete_command(std::string_view prefix) {
    const std::string wanted = lowered(prefix.empty() || prefix.front() != '/'
                                           ? prefix
                                           : prefix.substr(1));
    std::vector<std::string> matches;
    for (const Entry& entry : kCommands) {
        if (entry.name.rfind(wanted, 0) == 0) {
            matches.emplace_back(entry.name);
        }
    }
    std::sort(matches.begin(), matches.end());
    return matches;
}

}  // namespace i2pchat::presentation
