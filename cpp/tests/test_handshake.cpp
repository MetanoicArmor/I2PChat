#include <catch2/catch_test_macros.hpp>

#include "i2pchat/encoding.hpp"
#include "i2pchat/session/handshake.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::hex_of;
using i2pchat::testing::load_vector;

namespace {

const std::string kInitAddr = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kRespAddr = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

session::HandshakeConfig make_config(const std::string& local,
                                     const std::string& peer, ByteView seed) {
    session::HandshakeConfig config;
    config.local_addr = local;
    config.peer_addr = peer;
    config.signing_seed = Bytes(seed.begin(), seed.end());
    config.signing_public = crypto::get_verify_key_from_seed(seed);
    return config;
}

}  // namespace

TEST_CASE("handshake messages reproduce the reference bytes", "[handshake][vectors]") {
    crypto::init();
    const auto document = load_vector("crypto_handshake");
    const auto& parties = document.at("parties");
    const auto& messages = document.at("messages");

    const Bytes init_seed = hex_field(parties, "initiator_signing_seed_hex");
    const Bytes resp_seed = hex_field(parties, "responder_signing_seed_hex");

    SECTION("initiator produces the exact INIT frame") {
        session::HandshakeMachine initiator(
            make_config(kInitAddr, kRespAddr, ByteView(init_seed)));
        initiator.set_ephemeral_for_test(hex_field(parties, "initiator_eph_priv_hex"),
                                         hex_field(parties, "initiator_eph_pub_hex"));
        initiator.set_nonce_for_test(hex_field(parties, "nonce_init_hex"));

        const session::HandshakeOutput output = initiator.start_as_initiator();
        REQUIRE(output.frames.size() == 1);
        CHECK(output.frames[0] == messages.at("init").get<std::string>());
        CHECK(initiator.state() == session::HandshakeState::InitSent);
        CHECK(initiator.role() == session::HandshakeRole::Initiator);
    }

    SECTION("responder produces the exact RESP and FINISHED frames") {
        session::HandshakeMachine responder(
            make_config(kRespAddr, kInitAddr, ByteView(resp_seed)));
        responder.set_ephemeral_for_test(hex_field(parties, "responder_eph_priv_hex"),
                                         hex_field(parties, "responder_eph_pub_hex"));
        responder.set_nonce_for_test(hex_field(parties, "nonce_resp_hex"));

        const session::HandshakeOutput output =
            responder.on_message(messages.at("init").get<std::string>());
        REQUIRE(output.frames.size() == 2);
        CHECK(output.frames[0] == messages.at("resp").get<std::string>());
        CHECK(output.frames[1] ==
              messages.at("finished_from_responder").get<std::string>());
        CHECK(responder.state() == session::HandshakeState::AwaitingFinished);
        CHECK(responder.role() == session::HandshakeRole::Responder);
    }
}

TEST_CASE("a full handshake between two machines establishes matching keys",
          "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    const auto init_out = initiator.start_as_initiator();
    REQUIRE(init_out.frames.size() == 1);

    const auto resp_out = responder.on_message(init_out.frames[0]);
    REQUIRE(resp_out.frames.size() == 2);  // RESP + FINISHED

    const auto initiator_after_resp = initiator.on_message(resp_out.frames[0]);
    REQUIRE(initiator_after_resp.frames.size() == 1);  // FINISHED
    CHECK_FALSE(initiator_after_resp.established);

    const auto initiator_done = initiator.on_message(resp_out.frames[1]);
    CHECK(initiator_done.established);
    CHECK(initiator.state() == session::HandshakeState::Established);

    const auto responder_done = responder.on_message(initiator_after_resp.frames[0]);
    CHECK(responder_done.established);
    CHECK(responder.state() == session::HandshakeState::Established);

    // The two ends must mirror: what one sends with, the other receives with.
    const session::SessionKeys& a = initiator.keys();
    const session::SessionKeys& b = responder.keys();
    CHECK(a.send_enc == b.recv_enc);
    CHECK(a.send_mac == b.recv_mac);
    CHECK(a.recv_enc == b.send_enc);
    CHECK(a.recv_mac == b.send_mac);
    // And the directions must differ, or a reflected frame would verify.
    CHECK(a.send_enc != a.recv_enc);

    // Each side learned the other's signing key, ready for TOFU pinning.
    CHECK(initiator.peer_signing_key() == bob.public_key);
    CHECK(responder.peer_signing_key() == alice.public_key);
}

TEST_CASE("a tampered INIT signature is rejected", "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    std::string init = initiator.start_as_initiator().frames[0];
    // Flip the last hex digit of the signature.
    init.back() = (init.back() == 'a') ? 'b' : 'a';

    CHECK_THROWS_AS(responder.on_message(init), session::HandshakeError);
    CHECK(responder.state() == session::HandshakeState::Failed);
    // A failed machine must not be reusable.
    CHECK_THROWS_AS(responder.on_message("FINISHED:00"), session::HandshakeError);
}

TEST_CASE("an INIT signed for a different peer is rejected", "[handshake]") {
    // The signed transcript binds both addresses, so an INIT captured from a
    // conversation with someone else cannot be replayed at us.
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();
    const std::string other = "zzzzyyyyxxxxwwwwvvvvuuuuttttssssrrrrqqqqppppoooonnnn";

    session::HandshakeMachine initiator(
        make_config(kInitAddr, other, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    const std::string init = initiator.start_as_initiator().frames[0];
    CHECK_THROWS_AS(responder.on_message(init), session::HandshakeError);
}

TEST_CASE("simultaneous INIT is treated as a role conflict", "[handshake]") {
    // Both ends dialling at once would leave them disagreeing about which nonce
    // is nonce_init, silently deriving different keys.
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine one(make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine two(make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    one.start_as_initiator();
    const std::string other_init = two.start_as_initiator().frames[0];

    CHECK_THROWS_AS(one.on_message(other_init), session::HandshakeError);
}

TEST_CASE("RESP without a pending INIT is rejected", "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));
    session::HandshakeMachine bystander(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));

    const std::string init = initiator.start_as_initiator().frames[0];
    const std::string resp = responder.on_message(init).frames[0];

    // bystander never sent an INIT.
    CHECK_THROWS_AS(bystander.on_message(resp), session::HandshakeError);
}

TEST_CASE("FINISHED before key derivation is rejected", "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    session::HandshakeMachine machine(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    CHECK_THROWS_AS(machine.on_message("FINISHED:" + std::string(64, 'a')),
                    session::HandshakeError);
}

TEST_CASE("a wrong FINISHED tag fails key confirmation", "[handshake]") {
    // Key confirmation is what proves the peer derived the same keys; accepting
    // a bad tag would let an active attacker complete a handshake it cannot read.
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    const std::string init = initiator.start_as_initiator().frames[0];
    const auto resp_out = responder.on_message(init);
    initiator.on_message(resp_out.frames[0]);

    CHECK_THROWS_AS(initiator.on_message("FINISHED:" + std::string(64, '0')),
                    session::HandshakeError);
}

TEST_CASE("the initiator's own FINISHED is not accepted as the peer's",
          "[handshake]") {
    // Directional MAC keys mean a reflected FINISHED must fail; if it passed,
    // an attacker could complete a handshake by echoing our own frames.
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    session::HandshakeMachine responder(
        make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));

    const std::string init = initiator.start_as_initiator().frames[0];
    const auto resp_out = responder.on_message(init);
    const auto own_finished = initiator.on_message(resp_out.frames[0]).frames[0];

    CHECK_THROWS_AS(initiator.on_message(own_finished), session::HandshakeError);
}

TEST_CASE("malformed handshake payloads are rejected", "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    const auto attempt = [&bob](const std::string& body) {
        session::HandshakeMachine responder(
            make_config(kRespAddr, kInitAddr, ByteView(bob.seed)));
        return responder.on_message(body);
    };

    CHECK_THROWS_AS(attempt("INIT:"), session::HandshakeError);
    CHECK_THROWS_AS(attempt("INIT:aa:bb:cc"), session::HandshakeError);  // too few parts
    CHECK_THROWS_AS(attempt("INIT:aa:bb:cc:dd:ee"),
                    session::HandshakeError);  // too many parts
    CHECK_THROWS_AS(attempt("INIT:zz:bb:cc:dd"), session::HandshakeError);  // not hex
    // Correct shape, wrong field lengths.
    CHECK_THROWS_AS(attempt("INIT:" + std::string(64, 'a') + ":" +
                            std::string(62, 'a') + ":" + std::string(64, 'a') + ":" +
                            std::string(128, 'a')),
                    session::HandshakeError);
}

TEST_CASE("an unknown handshake verb is ignored rather than fatal", "[handshake]") {
    // Tolerating unknown verbs is what lets a future protocol version add
    // messages without breaking older peers.
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    session::HandshakeMachine machine(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));

    const auto output = machine.on_message("SOMETHING_NEW:payload");
    CHECK(output.frames.empty());
    CHECK_FALSE(output.established);
    CHECK(machine.state() == session::HandshakeState::Idle);
}

TEST_CASE("the trust verifier can veto a handshake", "[handshake][tofu]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();
    const crypto::SigningKeyPair bob = crypto::generate_signing_keypair();

    session::HandshakeMachine initiator(
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed)));
    const std::string init = initiator.start_as_initiator().frames[0];

    bool consulted = false;
    session::HandshakeConfig config = make_config(kRespAddr, kInitAddr, ByteView(bob.seed));
    config.trust_verifier = [&consulted](const std::string& peer, ByteView key) {
        consulted = true;
        CHECK(peer == kInitAddr);
        CHECK(key.size() == 32);
        return session::TrustDecision::Reject;
    };
    session::HandshakeMachine responder(config);

    CHECK_THROWS_AS(responder.on_message(init), session::HandshakeError);
    CHECK(consulted);
    // No keys may be installed when trust was refused.
    CHECK_FALSE(responder.keys().installed());
}

TEST_CASE("handshake construction requires complete configuration", "[handshake]") {
    crypto::init();
    const crypto::SigningKeyPair alice = crypto::generate_signing_keypair();

    session::HandshakeConfig missing_peer =
        make_config(kInitAddr, "", ByteView(alice.seed));
    CHECK_THROWS_AS(session::HandshakeMachine(missing_peer), session::HandshakeError);

    session::HandshakeConfig missing_key =
        make_config(kInitAddr, kRespAddr, ByteView(alice.seed));
    missing_key.signing_seed.clear();
    CHECK_THROWS_AS(session::HandshakeMachine(missing_key), session::HandshakeError);
}
