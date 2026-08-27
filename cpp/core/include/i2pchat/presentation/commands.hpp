#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

/// Slash commands, parsed.
///
/// A pure function over a line of text: no core, no terminal, no side effects.
/// The reference implementation parses commands inside its TUI and duplicates
/// most of the ladder in its Qt client; here both front ends share this, and the
/// awkward parts — quoted paths, an address pasted with its `.b32.i2p` suffix,
/// a command that needs an argument it did not get — are unit-tested once.
namespace i2pchat::presentation {

enum class CommandKind {
    /// Not a command: ordinary chat text.
    Text,
    /// A leading slash with a word this version does not know.
    Unknown,

    Help,
    Status,
    Quit,

    Connect,
    Disconnect,
    /// Send to a specific peer regardless of which one is selected.
    Reply,

    Contacts,
    ContactAdd,
    ContactUse,
    ContactEdit,
    ContactRemove,
    ContactInfo,
    Recent,

    SendFile,
    SendPicture,
    Transfers,

    History,
    HistoryClear,
    HistoryRetention,

    BlindBox,
    /// Force a collection now rather than waiting for the poll.
    BlindBoxPoll,

    TrustInfo,
    ForgetPin,

    CopyAddress,
    AppDir,
    Router,
    Diagnostics,
    Settings,
    Profiles,
    Group,
};

struct Command {
    CommandKind kind = CommandKind::Text;
    /// The command word as typed, without the slash. Empty for plain text.
    std::string name;
    /// Positional arguments, with quoted runs kept together.
    std::vector<std::string> args;
    /// The whole line for `Text`, and everything after the command word
    /// otherwise — what a command that takes free-form text should use.
    std::string rest;

    [[nodiscard]] const std::string& arg(std::size_t index) const;
    [[nodiscard]] bool has_arg(std::size_t index) const {
        return index < args.size();
    }
};

/// Split on whitespace, keeping `"quoted runs"` together and honouring
/// backslash escapes. Needed because file paths have spaces in them.
[[nodiscard]] std::vector<std::string> tokenize(std::string_view line);

/// Parse one input line.
///
/// A line is a command only when it starts with `/` and holds no newline: a
/// multi-line paste that happens to begin with a slash is chat text, which is
/// how the reference implementation behaves and what users expect when pasting
/// code.
[[nodiscard]] Command parse_command(std::string_view line);

/// The command word for a kind, without the slash. Empty for `Text` and
/// `Unknown`.
[[nodiscard]] std::string_view command_name(CommandKind kind);

struct CommandHelp {
    std::string_view usage;
    std::string_view summary;
};

/// Every command with its usage line, for `/help` and for completion.
[[nodiscard]] const std::vector<CommandHelp>& command_help();

/// Commands whose name starts with `prefix` (without the slash), for tab
/// completion. Sorted, and empty for a prefix nothing matches.
[[nodiscard]] std::vector<std::string> complete_command(std::string_view prefix);

}  // namespace i2pchat::presentation
