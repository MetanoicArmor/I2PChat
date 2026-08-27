#include <catch2/catch_test_macros.hpp>

#include "i2pchat/sam/protocol.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::load_vector;

TEST_CASE("SAM command builders reproduce the reference bytes", "[sam][vectors]") {
    const auto document = load_vector("sam");

    for (const auto& entry : document.at("builders")) {
        const auto name = entry.at("name").get<std::string>();
        const auto expected = entry.at("payload").get<std::string>();
        if (name == "hello") {
            CHECK(sam::build_hello() == expected);
        } else if (name == "dest_generate") {
            CHECK(sam::build_dest_generate() == expected);
        }
    }
}

TEST_CASE("SESSION CREATE keeps the unconditional space before options",
          "[sam]") {
    // The reference builder always emits a space after DESTINATION, so with no
    // options the line ends with a trailing space. Routers accept it; matching
    // it keeps captured traffic identical between implementations.
    CHECK(sam::build_session_create("STREAM", "sess-1", "TRANSIENT") ==
          "SESSION CREATE STYLE=STREAM ID=sess-1 DESTINATION=TRANSIENT \n");

    CHECK(sam::build_session_create("STREAM", "sess-1", "TRANSIENT", {},
                                    sam::kSigTypeEd25519) ==
          "SESSION CREATE STYLE=STREAM ID=sess-1 DESTINATION=TRANSIENT "
          "SIGNATURE_TYPE=7\n");

    CHECK(sam::build_session_create("STREAM", "sess-1", "TRANSIENT",
                                    {{"inbound.length", "3"}}, sam::kSigTypeEd25519) ==
          "SESSION CREATE STYLE=STREAM ID=sess-1 DESTINATION=TRANSIENT "
          "SIGNATURE_TYPE=7 inbound.length=3\n");
}

TEST_CASE("stream commands are built as the router expects", "[sam]") {
    CHECK(sam::build_stream_connect("sess-1", "dest-b64") ==
          "STREAM CONNECT ID=sess-1 DESTINATION=dest-b64 SILENT=false\n");
    CHECK(sam::build_stream_accept("sess-1") ==
          "STREAM ACCEPT ID=sess-1 SILENT=false\n");
    CHECK(sam::build_naming_lookup("example.i2p") ==
          "NAMING LOOKUP NAME=example.i2p\n");
    // STREAM FORWARD carries a trailing space in the reference builder too.
    CHECK(sam::build_stream_forward("sess-1", 1234) ==
          "STREAM FORWARD ID=sess-1 PORT=1234 \n");
}

TEST_CASE("builders reject injected control characters", "[sam]") {
    // Without this a caller-supplied name could smuggle an extra SAM command.
    CHECK_THROWS_AS(sam::build_naming_lookup("host\nSESSION CREATE"), sam::SamError);
    CHECK_THROWS_AS(sam::build_stream_connect("sess\r\n1", "dest"), sam::SamError);
    CHECK_THROWS_AS(sam::build_session_create("STREAM", "id with space", "TRANSIENT"),
                    sam::SamError);
    CHECK_THROWS_AS(sam::build_session_create("GOSSIP", "id", "TRANSIENT"),
                    sam::SamError);
    CHECK_THROWS_AS(sam::build_hello("three", "3.2"), sam::SamError);
    CHECK_THROWS_AS(sam::build_stream_forward("sess-1", 0), sam::SamError);
    CHECK_THROWS_AS(sam::build_stream_forward("sess-1", 70000), sam::SamError);
}

TEST_CASE("reply parsing upper-cases the command, topic and keys", "[sam]") {
    const sam::SamReply reply =
        sam::parse_reply_line("session status result=OK destination=abc\n");
    CHECK(reply.command == "SESSION");
    CHECK(reply.topic == "STATUS");
    CHECK(reply.field("RESULT") == "OK");
    // Values keep their case; only keys are normalized.
    CHECK(reply.field("destination") == "abc");
}

TEST_CASE("reply parsing rejects empty and malformed lines", "[sam]") {
    CHECK_THROWS_AS(sam::parse_reply_line(""), sam::SamError);
    CHECK_THROWS_AS(sam::parse_reply_line("   \n"), sam::SamError);
    CHECK_THROWS_AS(sam::parse_reply_line("HELLO"), sam::SamError);
}

TEST_CASE("private key material is redacted from reply lines", "[sam]") {
    // A DEST REPLY carries the profile's private identity key; it must never
    // survive into a log line or an exception message.
    const sam::SamReply reply =
        sam::parse_reply_line("DEST REPLY PUB=publicvalue PRIV=secretkeymaterial");
    CHECK(reply.raw_line.find("secretkeymaterial") == std::string::npos);
    CHECK(reply.raw_line.find("<redacted:") != std::string::npos);
    // The parsed field is still available to the caller that needs it.
    CHECK(reply.field("PRIV") == "secretkeymaterial");

    const sam::SamReply session =
        sam::parse_reply_line("SESSION STATUS RESULT=OK DESTINATION=longdestination");
    CHECK(session.raw_line.find("longdestination") == std::string::npos);
}

TEST_CASE("expect_ok accepts i2pd's implicit successes", "[sam]") {
    // i2pd omits RESULT=OK on a successful DEST GENERATE.
    const sam::SamReply dest = sam::parse_reply_line("DEST REPLY PUB=pub PRIV=priv");
    CHECK_NOTHROW(sam::expect_ok(dest));

    // And often on SESSION CREATE.
    const sam::SamReply session = sam::parse_reply_line("SESSION STATUS DESTINATION=d");
    CHECK_NOTHROW(sam::expect_ok(session));

    const sam::SamReply ok = sam::parse_reply_line("HELLO REPLY RESULT=OK VERSION=3.1");
    CHECK_NOTHROW(sam::expect_ok(ok));
}

TEST_CASE("expect_ok maps SAM result codes to error kinds", "[sam]") {
    const auto check_kind = [](const std::string& line, sam::SamErrorKind expected) {
        const sam::SamReply reply = sam::parse_reply_line(line);
        try {
            sam::expect_ok(reply);
            FAIL("expected expect_ok to throw for: " + line);
        } catch (const sam::SamError& error) {
            CHECK(error.kind() == expected);
        }
    };

    check_kind("STREAM STATUS RESULT=CANT_REACH_PEER", sam::SamErrorKind::CantReachPeer);
    check_kind("SESSION STATUS RESULT=DUPLICATED_ID", sam::SamErrorKind::DuplicatedId);
    check_kind("SESSION STATUS RESULT=DUPLICATED_DEST", sam::SamErrorKind::DuplicatedDest);
    check_kind("STREAM STATUS RESULT=I2P_ERROR", sam::SamErrorKind::I2pError);
    check_kind("STREAM STATUS RESULT=INVALID_KEY", sam::SamErrorKind::InvalidKey);
    check_kind("NAMING REPLY RESULT=KEY_NOT_FOUND", sam::SamErrorKind::KeyNotFound);
    check_kind("STREAM STATUS RESULT=PEER_NOT_FOUND", sam::SamErrorKind::PeerNotFound);
    check_kind("STREAM STATUS RESULT=TIMEOUT", sam::SamErrorKind::Timeout);
    check_kind("STREAM STATUS RESULT=SOMETHING_NEW", sam::SamErrorKind::Unknown);

    // A reply with no RESULT and no implicit-success payload is a protocol error.
    check_kind("STREAM STATUS FOO=bar", sam::SamErrorKind::Protocol);
}
