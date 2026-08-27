#include <catch2/catch_test_macros.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/protocol/signals.hpp"

using namespace i2pchat;
using protocol::SignalKind;

TEST_CASE("a signal body is recognised and its payload extracted", "[signals]") {
    CHECK(protocol::signal_body("QUIT") == "__SIGNAL__:QUIT");
    CHECK(protocol::signal_payload("__SIGNAL__:QUIT") == "QUIT");
    CHECK(protocol::signal_payload("__SIGNAL__:  MSG_ACK|7  ") == "MSG_ACK|7");
    CHECK_FALSE(protocol::signal_payload("a plain chat message").has_value());
}

TEST_CASE("a body that is not a signal is not mistaken for one", "[signals]") {
    // An `S` frame also carries the peer's destination, which must not be
    // parsed as a control signal.
    const protocol::Signal signal = protocol::parse_signal("some-destination-base64");
    CHECK(signal.kind == SignalKind::Unknown);
    CHECK_FALSE(signal.well_formed);
}

TEST_CASE("delivery acknowledgements round trip", "[signals]") {
    const protocol::Signal message =
        protocol::parse_signal(protocol::signal_body(protocol::build_msg_ack(42)));
    CHECK(message.kind == SignalKind::MsgAck);
    CHECK(message.well_formed);
    CHECK(message.message_id == 42);

    const protocol::Signal file = protocol::parse_signal(
        protocol::signal_body(protocol::build_file_ack("report.pdf", 7)));
    CHECK(file.kind == SignalKind::FileAck);
    CHECK(file.well_formed);
    CHECK(file.name == "report.pdf");
    CHECK(file.message_id == 7);

    const protocol::Signal image = protocol::parse_signal(
        protocol::signal_body(protocol::build_image_ack("кот.png", 9)));
    CHECK(image.kind == SignalKind::ImgAck);
    CHECK(image.well_formed);
    CHECK(image.name == "кот.png");
    CHECK(image.message_id == 9);
}

TEST_CASE("an acknowledgement without an id is not acted on", "[signals]") {
    // Matching on the filename alone would let one transfer confirm another,
    // and a stale ACK confirm a message that is still in flight.
    const protocol::Signal file = protocol::parse_signal("__SIGNAL__:FILE_ACK|report.pdf");
    CHECK(file.kind == SignalKind::FileAck);
    CHECK_FALSE(file.well_formed);
    CHECK(file.name == "report.pdf");

    const protocol::Signal broken =
        protocol::parse_signal("__SIGNAL__:FILE_ACK|report.pdf|not-a-number");
    CHECK(broken.kind == SignalKind::FileAck);
    CHECK_FALSE(broken.well_formed);

    const protocol::Signal message = protocol::parse_signal("__SIGNAL__:MSG_ACK|");
    CHECK(message.kind == SignalKind::MsgAck);
    CHECK_FALSE(message.well_formed);
}

TEST_CASE("transfer control signals round trip", "[signals]") {
    const protocol::Signal reject = protocol::parse_signal(
        protocol::signal_body(protocol::build_reject_file("report.pdf")));
    CHECK(reject.kind == SignalKind::RejectFile);
    CHECK(reject.well_formed);
    CHECK(reject.name == "report.pdf");

    const protocol::Signal abort =
        protocol::parse_signal(protocol::signal_body(protocol::build_abort_file()));
    CHECK(abort.kind == SignalKind::AbortFile);
    CHECK(abort.well_formed);

    const protocol::Signal quit =
        protocol::parse_signal(protocol::signal_body(protocol::build_quit()));
    CHECK(quit.kind == SignalKind::Quit);
    CHECK(quit.well_formed);
}

TEST_CASE("only a graceful disconnect is honoured before the handshake",
          "[signals]") {
    // Every other signal moves protocol state, so accepting one unauthenticated
    // would let an on-path attacker forge ACKs, abort transfers, or replace a
    // BlindBox root.
    const auto honoured = [](const std::string& payload) {
        return protocol::honoured_before_handshake(
            protocol::parse_signal(protocol::signal_body(payload)));
    };
    CHECK(honoured(protocol::build_quit()));
    CHECK_FALSE(honoured(protocol::build_msg_ack(1)));
    CHECK_FALSE(honoured(protocol::build_file_ack("f", 1)));
    CHECK_FALSE(honoured(protocol::build_abort_file()));
    CHECK_FALSE(honoured(protocol::build_blindbox_root_ack(2)));
}

TEST_CASE("BlindBox root signals round trip", "[signals]") {
    crypto::init();
    const Bytes root = crypto::random_bytes(32);

    const protocol::Signal pairwise = protocol::parse_signal(
        protocol::signal_body(protocol::build_blindbox_root(3, ByteView(root))));
    CHECK(pairwise.kind == SignalKind::BlindBoxRoot);
    CHECK(pairwise.well_formed);
    CHECK(pairwise.epoch == 3);
    CHECK(pairwise.root_secret == root);

    const protocol::Signal acknowledged =
        protocol::parse_signal(protocol::signal_body(protocol::build_blindbox_root_ack(3)));
    CHECK(acknowledged.kind == SignalKind::BlindBoxRootAck);
    CHECK(acknowledged.well_formed);
    CHECK(acknowledged.epoch == 3);

    const protocol::Signal group = protocol::parse_signal(protocol::signal_body(
        protocol::build_group_blindbox_root("group-alpha", 2, 5, ByteView(root))));
    CHECK(group.kind == SignalKind::GroupBlindBoxRoot);
    CHECK(group.well_formed);
    CHECK(group.group_id == "group-alpha");
    CHECK(group.epoch == 2);
    CHECK(group.root_epoch == 5);
    CHECK(group.root_secret == root);

    const protocol::Signal group_ack = protocol::parse_signal(protocol::signal_body(
        protocol::build_group_blindbox_root_ack("group-alpha", 2, 5)));
    CHECK(group_ack.kind == SignalKind::GroupBlindBoxRootAck);
    CHECK(group_ack.well_formed);
    CHECK(group_ack.group_id == "group-alpha");
    CHECK(group_ack.epoch == 2);
    CHECK(group_ack.root_epoch == 5);
}

TEST_CASE("a group root signal is not read as a pairwise one", "[signals]") {
    // "BLINDBOX_ROOT|" is a substring of "GROUP_BLINDBOX_ROOT|", so the order
    // the markers are tested in decides which handler sees the signal.
    crypto::init();
    const Bytes root = crypto::random_bytes(32);
    const protocol::Signal group = protocol::parse_signal(protocol::signal_body(
        protocol::build_group_blindbox_root("group-alpha", 2, 5, ByteView(root))));
    CHECK(group.kind == SignalKind::GroupBlindBoxRoot);

    const protocol::Signal group_ack = protocol::parse_signal(protocol::signal_body(
        protocol::build_group_blindbox_root_ack("group-alpha", 2, 5)));
    CHECK(group_ack.kind == SignalKind::GroupBlindBoxRootAck);
}

TEST_CASE("a root of the wrong length is rejected", "[signals]") {
    // A short root would still derive keys, just weaker ones, and the mismatch
    // would only surface as messages that cannot be read.
    crypto::init();
    const std::string short_root = encoding::hex_encode(ByteView(crypto::random_bytes(16)));
    const protocol::Signal signal =
        protocol::parse_signal("__SIGNAL__:BLINDBOX_ROOT|3|" + short_root);
    CHECK(signal.kind == SignalKind::BlindBoxRoot);
    CHECK_FALSE(signal.well_formed);

    const protocol::Signal not_hex =
        protocol::parse_signal("__SIGNAL__:BLINDBOX_ROOT|3|nothexatall");
    CHECK_FALSE(not_hex.well_formed);

    const protocol::Signal no_epoch = protocol::parse_signal(
        "__SIGNAL__:BLINDBOX_ROOT|later|" +
        encoding::hex_encode(ByteView(crypto::random_bytes(32))));
    CHECK_FALSE(no_epoch.well_formed);
}

TEST_CASE("a group root signal missing a field is rejected", "[signals]") {
    crypto::init();
    const std::string root = encoding::hex_encode(ByteView(crypto::random_bytes(32)));
    CHECK_FALSE(
        protocol::parse_signal("__SIGNAL__:GROUP_BLINDBOX_ROOT|group-alpha|2|" + root)
            .well_formed);
    CHECK_FALSE(protocol::parse_signal("__SIGNAL__:GROUP_BLINDBOX_ROOT||2|5|" + root)
                    .well_formed);
    CHECK_FALSE(
        protocol::parse_signal("__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|group-alpha|2")
            .well_formed);
}
