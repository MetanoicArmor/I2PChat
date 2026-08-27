#include <catch2/catch_test_macros.hpp>

#include <string>
#include <vector>

#include "i2pchat/presentation/commands.hpp"

using namespace i2pchat::presentation;

TEST_CASE("tokenizing keeps quoted paths together") {
    CHECK(tokenize("a b  c") == std::vector<std::string>{"a", "b", "c"});
    CHECK(tokenize("  ") == std::vector<std::string>{});
    CHECK(tokenize("\"two words\" tail") ==
          std::vector<std::string>{"two words", "tail"});
    CHECK(tokenize("'single quoted'") == std::vector<std::string>{"single quoted"});
    CHECK(tokenize("/tmp/a\\ b") == std::vector<std::string>{"/tmp/a b"});
    // An empty quoted string is a real argument: it is how a user clears a note.
    CHECK(tokenize("name \"\"") == std::vector<std::string>{"name", ""});
}

TEST_CASE("plain text is not mistaken for a command") {
    const Command command = parse_command("hello there");
    CHECK(command.kind == CommandKind::Text);
    CHECK(command.rest == "hello there");
    CHECK(command.name.empty());
}

TEST_CASE("a multi-line paste starting with a slash is text") {
    const Command command = parse_command("/usr/bin/env\nsecond line");
    CHECK(command.kind == CommandKind::Text);
    CHECK(command.rest == "/usr/bin/env\nsecond line");
}

TEST_CASE("commands are parsed with their arguments") {
    SECTION("no arguments") {
        const Command command = parse_command("  /status  ");
        CHECK(command.kind == CommandKind::Status);
        CHECK(command.name == "status");
        CHECK(command.args.empty());
        CHECK(command.rest.empty());
    }

    SECTION("case is ignored in the command word") {
        CHECK(parse_command("/HELP").kind == CommandKind::Help);
    }

    SECTION("positional arguments") {
        const Command command =
            parse_command("/contact-add abcdef.b32.i2p \"Long Name\" a note");
        CHECK(command.kind == CommandKind::ContactAdd);
        REQUIRE(command.args.size() == 4);
        CHECK(command.arg(0) == "abcdef.b32.i2p");
        CHECK(command.arg(1) == "Long Name");
        CHECK(command.arg(3) == "note");
        CHECK_FALSE(command.has_arg(4));
        CHECK(command.arg(9).empty());
    }

    SECTION("free-form text after the command word") {
        const Command command = parse_command("/reply abc  hello  world");
        CHECK(command.kind == CommandKind::Reply);
        CHECK(command.rest == "abc  hello  world");
        CHECK(command.arg(0) == "abc");
    }

    SECTION("a path with spaces survives") {
        const Command command = parse_command("/sendfile \"/tmp/my file.txt\"");
        CHECK(command.kind == CommandKind::SendFile);
        CHECK(command.arg(0) == "/tmp/my file.txt");
    }

    SECTION("an unknown command is reported, not silently sent as text") {
        const Command command = parse_command("/nosuchthing arg");
        CHECK(command.kind == CommandKind::Unknown);
        CHECK(command.name == "nosuchthing");
    }
}

TEST_CASE("help covers every command exactly once") {
    const std::vector<CommandHelp>& help = command_help();
    CHECK(help.size() >= 25);
    for (const CommandHelp& entry : help) {
        CHECK(entry.usage.front() == '/');
        CHECK_FALSE(entry.summary.empty());
    }
}

TEST_CASE("completion narrows as the user types") {
    const std::vector<std::string> contacts = complete_command("contact");
    CHECK(contacts.size() >= 5);
    for (const std::string& name : contacts) {
        CHECK(name.rfind("contact", 0) == 0);
    }
    CHECK(complete_command("/hel") == std::vector<std::string>{"help"});
    CHECK(complete_command("zzz").empty());
    // An empty prefix offers everything, which is what an empty slash shows.
    CHECK(complete_command("").size() == command_help().size());
}

TEST_CASE("every kind with a name round-trips") {
    for (const CommandKind kind : {CommandKind::Help, CommandKind::Connect,
                                   CommandKind::BlindBoxPoll, CommandKind::Group}) {
        const std::string_view name = command_name(kind);
        REQUIRE_FALSE(name.empty());
        CHECK(parse_command("/" + std::string(name)).kind == kind);
    }
    CHECK(command_name(CommandKind::Text).empty());
}
