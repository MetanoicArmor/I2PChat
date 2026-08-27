#include <catch2/catch_test_macros.hpp>

#include <string>
#include <vector>

#include "i2pchat/encoding.hpp"
#include "i2pchat/sam/destination.hpp"
#include "i2pchat/session/peer_session.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;

namespace {

/// A valid public destination, taken from the golden vectors so the base32 we
/// derive from it is the same one the reference implementation would derive.
struct Identity {
    sam::Destination destination;
    crypto::SigningKeyPair signing;

    [[nodiscard]] std::string addr() const { return destination.base32(); }
    [[nodiscard]] const std::string& dest_b64() const { return destination.base64(); }
};

sam::Destination load_destination() {
    const auto document = load_vector("sam");
    return sam::Destination::from_public_base64(
        document.at("destination").at("public_base64").get<std::string>());
}

/// A second distinct destination, produced by perturbing the first. Only the
/// bytes matter here: nothing in the session layer validates the key material,
/// it only needs two destinations that hash differently.
sam::Destination other_destination(const sam::Destination& source) {
    Bytes data = source.data();
    data[0] = static_cast<Byte>(data[0] ^ 0xFF);
    return sam::Destination::from_public_base64(encoding::i2p_base64_encode(ByteView(data)));
}

session::PeerSessionConfig make_config(const Identity& local, const std::string& peer_addr,
                                       session::ConnectionDirection direction) {
    session::PeerSessionConfig config;
    config.local_dest_base64 = local.dest_b64();
    config.direction = direction;
    config.handshake.local_addr = local.addr();
    config.handshake.peer_addr = peer_addr;
    config.handshake.signing_seed = local.signing.seed;
    config.handshake.signing_public = local.signing.public_key;
    return config;
}

std::vector<Bytes> sends(const session::SessionActions& actions) {
    std::vector<Bytes> out;
    for (const auto& action : actions) {
        if (action.kind == session::SessionAction::Kind::Send) {
            out.push_back(action.bytes);
        }
    }
    return out;
}

bool has_disconnect(const session::SessionActions& actions) {
    for (const auto& action : actions) {
        if (action.kind == session::SessionAction::Kind::Disconnect) {
            return true;
        }
    }
    return false;
}

bool has_established(const session::SessionActions& actions) {
    for (const auto& action : actions) {
        if (action.kind == session::SessionAction::Kind::Established) {
            return true;
        }
    }
    return false;
}

Bytes concat(const std::vector<Bytes>& parts) {
    Bytes out;
    for (const auto& part : parts) {
        append(out, ByteView(part));
    }
    return out;
}

/// Two sessions wired to each other, exchanging bytes until both are secure.
struct Pair {
    Identity alice;
    Identity bob;
    session::PeerSession outbound;
    session::PeerSession inbound;
};

Pair make_pair() {
    crypto::init();
    const sam::Destination first = load_destination();
    Identity alice{first, crypto::generate_signing_keypair()};
    Identity bob{other_destination(first), crypto::generate_signing_keypair()};

    return Pair{
        alice, bob,
        session::PeerSession(
            make_config(alice, bob.addr(), session::ConnectionDirection::Outbound)),
        session::PeerSession(
            make_config(bob, "", session::ConnectionDirection::Inbound)),
    };
}

/// Run the handshake to completion, returning the actions each side saw last.
void establish(Pair& pair) {
    Bytes to_inbound = concat(sends(pair.outbound.on_stream_open()));
    Bytes to_outbound = concat(sends(pair.inbound.on_stream_open()));

    for (int round = 0; round < 8 && !(pair.outbound.secure() && pair.inbound.secure());
         ++round) {
        const auto inbound_actions = pair.inbound.on_bytes(ByteView(to_inbound));
        REQUIRE_FALSE(has_disconnect(inbound_actions));
        Bytes next_to_outbound = concat(sends(inbound_actions));

        const auto outbound_actions = pair.outbound.on_bytes(ByteView(to_outbound));
        REQUIRE_FALSE(has_disconnect(outbound_actions));

        to_inbound = concat(sends(outbound_actions));
        to_outbound = std::move(next_to_outbound);
    }
}

}  // namespace

TEST_CASE("an outbound session opens with a bare identity line", "[session]") {
    // Older peers read the calling destination with a plain readline before they
    // parse any frames, so the line must come first and on its own.
    Pair pair = make_pair();
    const auto actions = pair.outbound.on_stream_open();
    const auto outgoing = sends(actions);
    REQUIRE(outgoing.size() >= 3);  // line, S frame, INIT frame

    const std::string line = to_string(ByteView(outgoing[0]));
    CHECK(line == pair.alice.dest_b64() + "\n");

    protocol::FrameReader reader;
    reader.feed(ByteView(outgoing[1]));
    const auto identity_frame = reader.next();
    REQUIRE(identity_frame.has_value());
    CHECK(identity_frame->msg_type == 'S');
    CHECK_FALSE(identity_frame->encrypted());
    CHECK(to_string(ByteView(identity_frame->payload)) == pair.alice.dest_b64());

    reader.feed(ByteView(outgoing[2]));
    const auto handshake_frame = reader.next();
    REQUIRE(handshake_frame.has_value());
    CHECK(handshake_frame->msg_type == 'H');
    CHECK(to_string(ByteView(handshake_frame->payload)).rfind("INIT:", 0) == 0);
}

TEST_CASE("an inbound session sends nothing before the preface arrives",
          "[session]") {
    // The accepting side does not know who called until it reads the preface,
    // and announcing itself twice would put an extra `S` frame on the wire that
    // the caller is not expecting.
    Pair pair = make_pair();
    CHECK(sends(pair.inbound.on_stream_open()).empty());

    const Bytes preface = to_bytes(pair.alice.dest_b64() + "\n");
    const auto outgoing = sends(pair.inbound.on_bytes(ByteView(preface)));
    REQUIRE(outgoing.size() == 1);

    protocol::FrameReader reader;
    reader.feed(ByteView(outgoing[0]));
    const auto frame = reader.next();
    REQUIRE(frame.has_value());
    CHECK(frame->msg_type == 'S');
    CHECK(to_string(ByteView(frame->payload)) == pair.bob.dest_b64());
}

TEST_CASE("two sessions complete a handshake and exchange text", "[session]") {
    Pair pair = make_pair();
    establish(pair);

    REQUIRE(pair.outbound.secure());
    REQUIRE(pair.inbound.secure());
    CHECK(pair.outbound.state() == session::PeerState::Secure);

    // The inbound side learned who called it from the preface.
    CHECK(pair.inbound.peer_addr() == pair.alice.addr());
    CHECK(pair.outbound.peer_addr() == pair.bob.addr());

    const Bytes message = pair.outbound.send_message('U', as_bytes("привет"), 1);
    const auto delivered = pair.inbound.on_bytes(ByteView(message));
    REQUIRE(delivered.size() == 1);
    CHECK(delivered[0].kind == session::SessionAction::Kind::Deliver);
    CHECK(delivered[0].msg_type == 'U');
    CHECK(delivered[0].msg_id == 1);
    CHECK(to_string(ByteView(delivered[0].bytes)) == "привет");

    // And the same in the other direction.
    const Bytes reply = pair.inbound.send_message('U', as_bytes("и тебе"), 2);
    const auto reply_actions = pair.outbound.on_bytes(ByteView(reply));
    REQUIRE(reply_actions.size() == 1);
    CHECK(to_string(ByteView(reply_actions[0].bytes)) == "и тебе");
}

TEST_CASE("establishment is announced exactly once", "[session]") {
    Pair pair = make_pair();
    Bytes to_inbound = concat(sends(pair.outbound.on_stream_open()));
    Bytes to_outbound = concat(sends(pair.inbound.on_stream_open()));

    int announcements = 0;
    for (int round = 0; round < 8; ++round) {
        const auto inbound_actions = pair.inbound.on_bytes(ByteView(to_inbound));
        announcements += has_established(inbound_actions) ? 1 : 0;
        Bytes next = concat(sends(inbound_actions));

        const auto outbound_actions = pair.outbound.on_bytes(ByteView(to_outbound));
        announcements += has_established(outbound_actions) ? 1 : 0;

        to_inbound = concat(sends(outbound_actions));
        to_outbound = std::move(next);
    }
    CHECK(announcements == 2);  // once per side
}

TEST_CASE("bytes arriving in arbitrary fragments are handled", "[session]") {
    // I2P streams deliver whatever the router happens to have; the preface line
    // and the frames after it can be split anywhere.
    Pair pair = make_pair();
    const Bytes opening = concat(sends(pair.outbound.on_stream_open()));
    pair.inbound.on_stream_open();

    session::SessionActions collected;
    for (Byte byte : opening) {
        const Bytes single{byte};
        auto actions = pair.inbound.on_bytes(ByteView(single));
        for (auto& action : actions) {
            collected.push_back(std::move(action));
        }
    }
    CHECK_FALSE(has_disconnect(collected));
    // The inbound side answered with its own S frame plus RESP and FINISHED.
    CHECK(sends(collected).size() == 3);
}

TEST_CASE("a garbage identity preface is refused", "[session]") {
    Pair pair = make_pair();
    pair.inbound.on_stream_open();
    const auto actions = pair.inbound.on_bytes(as_bytes("not-a-destination\n"));
    CHECK(has_disconnect(actions));
    CHECK(pair.inbound.state() == session::PeerState::Failed);
}

TEST_CASE("an unbounded identity preface is refused", "[session]") {
    // Without a bound, an unauthenticated peer could make us buffer forever.
    Pair pair = make_pair();
    pair.inbound.on_stream_open();
    const Bytes flood(9000, 'A');
    const auto actions = pair.inbound.on_bytes(ByteView(flood));
    CHECK(has_disconnect(actions));
}

TEST_CASE("an unverifiable identity binding is refused", "[session]") {
    // A peer may claim any base64 destination; only a SAM lookup proves that the
    // address it maps to is really the one calling.
    crypto::init();
    const sam::Destination first = load_destination();
    Identity alice{first, crypto::generate_signing_keypair()};
    Identity bob{other_destination(first), crypto::generate_signing_keypair()};

    auto config = make_config(bob, "", session::ConnectionDirection::Inbound);
    bool consulted = false;
    config.identity_verifier = [&consulted](const std::string& addr,
                                            const std::string& dest) {
        consulted = true;
        CHECK_FALSE(addr.empty());
        CHECK_FALSE(dest.empty());
        return false;
    };
    session::PeerSession inbound(config);
    inbound.on_stream_open();

    session::PeerSession outbound(
        make_config(alice, bob.addr(), session::ConnectionDirection::Outbound));
    const Bytes opening = concat(sends(outbound.on_stream_open()));

    const auto actions = inbound.on_bytes(ByteView(opening));
    CHECK(consulted);
    CHECK(has_disconnect(actions));
    CHECK(inbound.state() == session::PeerState::Failed);
}

TEST_CASE("data before the handshake is refused", "[session]") {
    Pair pair = make_pair();
    pair.outbound.on_stream_open();

    // A `U` frame is application data and has no business arriving unencrypted
    // before there is a channel to protect it.
    const Bytes premature = protocol::encode_frame('U', as_bytes("early"), 1, 0);
    const auto actions = pair.outbound.on_bytes(ByteView(premature));
    CHECK(has_disconnect(actions));
    CHECK(pair.outbound.state() == session::PeerState::Failed);
}

TEST_CASE("an encrypted frame before the handshake is refused", "[session]") {
    Pair pair = make_pair();
    pair.outbound.on_stream_open();

    Bytes body(protocol::kEncryptedTrailerSize + 16, 0);
    const Bytes premature =
        protocol::encode_frame('U', ByteView(body), 1, protocol::kFlagEncrypted);
    const auto actions = pair.outbound.on_bytes(ByteView(premature));
    CHECK(has_disconnect(actions));
}

TEST_CASE("a plaintext frame after the handshake is refused", "[session]") {
    // This is the downgrade case: once keys exist, cleartext must never be
    // accepted, however well-formed the frame is.
    Pair pair = make_pair();
    establish(pair);

    const Bytes plain = protocol::encode_frame('U', as_bytes("cleartext"), 9, 0);
    const auto actions = pair.inbound.on_bytes(ByteView(plain));
    CHECK(has_disconnect(actions));
    CHECK(pair.inbound.state() == session::PeerState::Failed);
}

TEST_CASE("a handshake frame after the handshake is refused", "[session]") {
    // Renegotiation is not part of the protocol, so an `H` frame here can only
    // be an attempt to reset the keys on an authenticated channel.
    Pair pair = make_pair();
    establish(pair);

    const Bytes rehandshake =
        protocol::encode_frame('H', as_bytes("INIT:aa:bb:cc:dd"), 0, 0);
    const auto actions = pair.inbound.on_bytes(ByteView(rehandshake));
    CHECK(has_disconnect(actions));
}

TEST_CASE("an inbound session reads nothing until the preface arrives",
          "[session]") {
    // Everything an accepted stream sends is the preface line until a newline
    // shows up, so a peer cannot slip a handshake in ahead of its identity.
    Pair pair = make_pair();
    pair.inbound.on_stream_open();

    const Bytes init = protocol::encode_frame('H', as_bytes("INIT:aa:bb:cc:dd"), 0, 0);
    const auto buffered = pair.inbound.on_bytes(ByteView(init));
    CHECK(buffered.empty());
    CHECK(pair.inbound.state() == session::PeerState::Handshaking);
    CHECK_FALSE(pair.inbound.handshake().has_value());

    // Terminating the line reveals it is not a destination.
    const auto actions = pair.inbound.on_bytes(as_bytes("\n"));
    CHECK(has_disconnect(actions));
    CHECK(pair.inbound.state() == session::PeerState::Failed);
}

TEST_CASE("an S frame may not change the peer identity", "[session]") {
    // A peer that authenticated as one address must not be able to act as
    // another by sending a second identity frame.
    Pair pair = make_pair();
    pair.outbound.on_stream_open();

    const sam::Destination impostor = other_destination(load_destination());
    const sam::Destination third = other_destination(impostor);
    const Bytes mismatch = protocol::encode_frame('S', as_bytes(third.base64()), 0, 0);

    const auto actions = pair.outbound.on_bytes(ByteView(mismatch));
    CHECK(has_disconnect(actions));
    CHECK(pair.outbound.state() == session::PeerState::Failed);
}

TEST_CASE("a matching S frame is accepted", "[session]") {
    Pair pair = make_pair();
    pair.outbound.on_stream_open();
    const Bytes matching =
        protocol::encode_frame('S', as_bytes(pair.bob.dest_b64()), 0, 0);
    const auto actions = pair.outbound.on_bytes(ByteView(matching));
    CHECK_FALSE(has_disconnect(actions));
    CHECK(pair.outbound.state() == session::PeerState::Handshaking);
}

TEST_CASE("keepalive pings are answered in the current mode", "[session]") {
    Pair pair = make_pair();

    SECTION("in plaintext before the handshake") {
        pair.outbound.on_stream_open();
        const Bytes ping = protocol::encode_frame('P', ByteView(Bytes{}), 5, 0);
        const auto actions = pair.outbound.on_bytes(ByteView(ping));
        REQUIRE(sends(actions).size() == 1);

        protocol::FrameReader reader;
        reader.feed(ByteView(sends(actions)[0]));
        const auto pong = reader.next();
        REQUIRE(pong.has_value());
        CHECK(pong->msg_type == 'O');
        CHECK(pong->msg_id == 5);
        CHECK_FALSE(pong->encrypted());
    }

    SECTION("encrypted once the channel is secure") {
        establish(pair);
        const Bytes ping = pair.outbound.build_keepalive(7);
        const auto actions = pair.inbound.on_bytes(ByteView(ping));
        REQUIRE(sends(actions).size() == 1);

        const auto pong_actions = pair.outbound.on_bytes(ByteView(sends(actions)[0]));
        // `O` is consumed as a liveness signal, not delivered upwards.
        CHECK(pong_actions.empty());
    }
}

TEST_CASE("sending before the channel is secure is refused", "[session]") {
    Pair pair = make_pair();
    pair.outbound.on_stream_open();
    CHECK_THROWS_AS(pair.outbound.send_message('U', as_bytes("too early"), 1),
                    protocol::ProtocolError);
}

TEST_CASE("an inbound session may not be given a peer address up front",
          "[session]") {
    // Trusting a caller-supplied address on an accepted stream would skip the
    // preface verification entirely.
    crypto::init();
    const sam::Destination first = load_destination();
    Identity bob{other_destination(first), crypto::generate_signing_keypair()};
    auto config = make_config(bob, first.base32(), session::ConnectionDirection::Inbound);
    CHECK_THROWS_AS(session::PeerSession(config), session::HandshakeError);
}

TEST_CASE("a failed session ignores further bytes", "[session]") {
    Pair pair = make_pair();
    pair.outbound.on_stream_open();
    pair.outbound.on_bytes(ByteView(protocol::encode_frame('U', as_bytes("x"), 1, 0)));
    REQUIRE(pair.outbound.state() == session::PeerState::Failed);
    CHECK(pair.outbound.on_bytes(as_bytes("anything")).empty());
}

TEST_CASE("frame types allowed before the handshake match the protocol",
          "[session]") {
    for (char allowed : {'S', 'H', 'P', 'O'}) {
        CHECK(session::PeerSession::allowed_before_handshake(allowed));
    }
    for (char refused : {'U', 'F', 'D', 'E', 'I', 'G'}) {
        CHECK_FALSE(session::PeerSession::allowed_before_handshake(refused));
    }
}
