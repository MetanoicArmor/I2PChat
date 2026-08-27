#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/groups/invite.hpp"
#include "i2pchat/groups/models.hpp"
#include "i2pchat/groups/wire.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "vectors.hpp"

using namespace i2pchat;
using i2pchat::testing::hex_field;
using i2pchat::testing::load_vector;

namespace {

const std::string kAlice = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm";
const std::string kBob = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz";

/// The state and envelope the group wire vectors were generated from.
std::pair<groups::GroupState, groups::GroupEnvelope> vector_input() {
    const nlohmann::json input = load_vector("group_wire").at("input");
    groups::GroupState state(input.at("group_id").get<std::string>(),
                             input.at("epoch").get<std::uint64_t>(),
                             input.at("members").get<std::vector<std::string>>(),
                             input.at("group_title").get<std::string>());

    groups::GroupEnvelope envelope;
    envelope.group_id = state.group_id();
    envelope.epoch = state.epoch();
    envelope.msg_id = input.at("msg_id").get<std::string>();
    envelope.sender_id = input.at("sender_id").get<std::string>();
    envelope.group_seq = input.at("group_seq").get<std::uint64_t>();
    envelope.content_type = groups::ContentType::GroupText;
    envelope.payload = input.at("payload");
    envelope.created_at = input.at("created_at").get<std::string>();
    return {state, envelope};
}

groups::GroupEnvelope text_envelope(std::string sender, std::uint64_t seq,
                                    std::string text) {
    groups::GroupEnvelope envelope;
    envelope.group_id = "group-alpha";
    envelope.epoch = 2;
    envelope.msg_id = "msg-" + std::to_string(seq);
    envelope.sender_id = std::move(sender);
    envelope.group_seq = seq;
    envelope.content_type = groups::ContentType::GroupText;
    envelope.payload = std::move(text);
    envelope.created_at = "2026-01-01T12:00:00+00:00";
    return envelope;
}

}  // namespace

TEST_CASE("member ids are canonicalised", "[groups]") {
    CHECK(groups::normalize_member_id(kBob + ".b32.i2p") == kBob);
    CHECK(groups::normalize_member_id("  " + kBob + "  ") == kBob);
    // A non-address member id is kept as it is: control payloads and fixtures
    // use plain names, and rejecting them would break both.
    CHECK(groups::normalize_member_id(" Group-Admin ") == "group-admin");
    CHECK(groups::normalize_member_id("").empty());
}

TEST_CASE("group state deduplicates members but keeps their order", "[groups]") {
    // The order is inside the signed payload, so it cannot be sorted for
    // convenience.
    const groups::GroupState state("  group-alpha ", 2,
                                   {kBob, kAlice, kBob + ".b32.i2p", "", kAlice}, " Тест ");
    CHECK(state.group_id() == "group-alpha");
    CHECK(state.title() == "Тест");
    CHECK(state.members() == std::vector<std::string>{kBob, kAlice});
    CHECK(state.has_member(kAlice + ".b32.i2p"));
    CHECK_FALSE(state.has_member("someone-else"));
}

TEST_CASE("the v1 transport encoding matches the reference byte for byte",
          "[groups][vectors]") {
    const nlohmann::json document = load_vector("group_wire");
    const nlohmann::json input = document.at("input");
    const auto [state, envelope] = vector_input();

    const groups::RecipientDelivery delivery{input.at("recipient_id").get<std::string>(),
                                             input.at("delivery_id").get<std::string>()};
    CHECK(groups::encode_transport_v1(state, envelope, delivery) ==
          document.at("v1_recipient_scope").at("encoded").get<std::string>());
}

TEST_CASE("the v3 signature payload matches the reference byte for byte",
          "[groups][vectors]") {
    // A difference of one byte here silently breaks every signature check
    // between the two implementations, which is why it is compared literally.
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    const auto [state, envelope] = vector_input();
    const Bytes signer_key = hex_field(v3, "signer_pub_hex");

    CHECK(to_string(ByteView(groups::v3_signature_payload(state, envelope,
                                                          ByteView(signer_key)))) ==
          v3.at("signature_payload_utf8").get<std::string>());
}

TEST_CASE("the v3 transport encoding matches the reference", "[groups][vectors]") {
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    const auto [state, envelope] = vector_input();

    CHECK(groups::encode_transport_v3(state, envelope,
                                      ByteView(hex_field(v3, "signer_pub_hex")),
                                      ByteView(hex_field(v3, "signature_hex"))) ==
          v3.at("encoded").get<std::string>());
}

TEST_CASE("a reference v1 message decodes", "[groups][vectors]") {
    const nlohmann::json document = load_vector("group_wire");
    const auto decoded = groups::decode_transport(
        document.at("v1_recipient_scope").at("encoded").get<std::string>());

    REQUIRE(decoded.has_value());
    CHECK(decoded->version == groups::kTransportVersionV1);
    CHECK(decoded->delivery_scope == groups::kDeliveryScopeRecipient);
    CHECK(decoded->state.group_id() == "group-alpha");
    CHECK(decoded->state.title() == document.at("input").at("group_title").get<std::string>());
    CHECK(decoded->envelope.sender_id == kAlice);
    CHECK(decoded->envelope.group_seq == 7);
    CHECK(decoded->envelope.payload == "привет группа");
    CHECK(decoded->recipient_id == kBob);
    CHECK(decoded->delivery_id == "delivery-0001");
    CHECK_FALSE(decoded->signature.has_value());
}

TEST_CASE("a reference v3 message decodes and its signature verifies",
          "[groups][vectors]") {
    crypto::init();
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    const auto decoded =
        groups::decode_transport(v3.at("encoded").get<std::string>());

    REQUIRE(decoded.has_value());
    CHECK(decoded->version == groups::kTransportVersionV3);
    CHECK(decoded->delivery_scope == groups::kDeliveryScopeGroupBlindBox);
    CHECK(decoded->signer_key == hex_field(v3, "signer_pub_hex"));
    CHECK(decoded->signature == hex_field(v3, "signature_hex"));
    // A v3 message names no recipient; the whole group reads the same blob.
    CHECK_FALSE(decoded->recipient_id.has_value());
}

TEST_CASE("a v3 message with a broken signature is refused", "[groups]") {
    crypto::init();
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    std::string encoded = v3.at("encoded").get<std::string>();

    // Flip one hex digit of the signature.
    const std::string signature = encoding::hex_encode(ByteView(hex_field(v3, "signature_hex")));
    const auto position = encoded.find(signature);
    REQUIRE(position != std::string::npos);
    encoded[position] = encoded[position] == 'a' ? 'b' : 'a';

    CHECK_THROWS_AS(groups::decode_transport(encoded), groups::WireError);
}

TEST_CASE("a v3 message whose body was edited is refused", "[groups]") {
    // The point of signing the canonical payload is that changing the roster or
    // the text invalidates it, even though the signature field is untouched.
    crypto::init();
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    std::string encoded = v3.at("encoded").get<std::string>();

    const auto position = encoded.find("\"group_seq\":7");
    REQUIRE(position != std::string::npos);
    encoded.replace(position, std::string("\"group_seq\":7").size(), "\"group_seq\":8");

    CHECK_THROWS_AS(groups::decode_transport(encoded), groups::WireError);
}

TEST_CASE("a signed v3 message round trips through our own encoder", "[groups]") {
    crypto::init();
    const crypto::SigningKeyPair keys = crypto::generate_signing_keypair();
    const groups::GroupState state("group-alpha", 2, {kAlice, kBob}, "Тест");
    const groups::GroupEnvelope envelope = text_envelope(kAlice, 4, "привет");

    const Bytes signed_payload =
        groups::v3_signature_payload(state, envelope, ByteView(keys.public_key));
    const Bytes signature =
        crypto::sign_data(ByteView(keys.seed), ByteView(signed_payload));

    const auto decoded = groups::decode_transport(groups::encode_transport_v3(
        state, envelope, ByteView(keys.public_key), ByteView(signature)));

    REQUIRE(decoded.has_value());
    CHECK(decoded->envelope.payload == "привет");
    CHECK(decoded->envelope.group_seq == 4);
    CHECK(decoded->signer_key == keys.public_key);
}

TEST_CASE("unsigned v2 group messages are no longer accepted", "[groups]") {
    // v2 was v3 without a signature, which let anyone holding the group root
    // forge a message from any member.
    const nlohmann::json payload = {
        {"transport", "group"},
        {"version", 2},
        {"delivery_scope", "group_blindbox"},
        {"group_id", "group-alpha"},
        {"members", {kAlice, kBob}},
        {"epoch", 2},
        {"msg_id", "msg-1"},
        {"sender_id", kAlice},
        {"group_seq", 1},
        {"content_type", "GROUP_TEXT"},
        {"payload", "hi"},
        {"created_at", "2026-01-01T12:00:00+00:00"},
    };
    CHECK_THROWS_AS(
        groups::decode_transport(std::string(groups::kTransportPrefix) + payload.dump()),
        groups::WireError);
}

TEST_CASE("text that is not a group message is left alone", "[groups]") {
    CHECK_FALSE(groups::decode_transport("just a chat message").has_value());
    CHECK_FALSE(groups::decode_transport("").has_value());
}

TEST_CASE("malformed group messages are rejected with a reason", "[groups]") {
    const auto prefixed = [](const nlohmann::json& payload) {
        return std::string(groups::kTransportPrefix) + payload.dump();
    };
    const nlohmann::json base = {
        {"transport", "group"},
        {"version", 1},
        {"group_id", "group-alpha"},
        {"members", {kAlice, kBob}},
        {"epoch", 2},
        {"msg_id", "msg-1"},
        {"sender_id", kAlice},
        {"group_seq", 1},
        {"content_type", "GROUP_TEXT"},
        {"payload", "hi"},
        {"created_at", "2026-01-01T12:00:00+00:00"},
        {"recipient_id", kBob},
        {"delivery_id", "d-1"},
    };

    CHECK(groups::decode_transport(prefixed(base)).has_value());

    SECTION("not JSON") {
        CHECK_THROWS_AS(
            groups::decode_transport(std::string(groups::kTransportPrefix) + "{oops"),
            groups::WireError);
    }
    SECTION("another transport") {
        nlohmann::json payload = base;
        payload["transport"] = "direct";
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("an unknown version") {
        nlohmann::json payload = base;
        payload["version"] = 9;
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a sender outside the group") {
        // Otherwise a group member could relay a message attributed to anyone.
        nlohmann::json payload = base;
        payload["sender_id"] = "zzzzyyyyxxxxwwwwvvvvuuuuttttssssrrrrqqqqppppoooonnnn";
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a recipient outside the group") {
        nlohmann::json payload = base;
        payload["recipient_id"] = "zzzzyyyyxxxxwwwwvvvvuuuuttttssssrrrrqqqqppppoooonnnn";
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a text message carrying an object") {
        nlohmann::json payload = base;
        payload["payload"] = nlohmann::json::object();
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a control message carrying a string") {
        nlohmann::json payload = base;
        payload["content_type"] = "GROUP_CONTROL";
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a sequence number of zero") {
        nlohmann::json payload = base;
        payload["group_seq"] = 0;
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("an empty member list") {
        nlohmann::json payload = base;
        payload["members"] = nlohmann::json::array();
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
    SECTION("a missing delivery id") {
        nlohmann::json payload = base;
        payload.erase("delivery_id");
        CHECK_THROWS_AS(groups::decode_transport(prefixed(payload)), groups::WireError);
    }
}

TEST_CASE("a v3 message naming a recipient is refused", "[groups]") {
    crypto::init();
    const nlohmann::json v3 = load_vector("group_wire").at("v3_group_blindbox_scope");
    nlohmann::json payload = nlohmann::json::parse(
        v3.at("encoded").get<std::string>().substr(groups::kTransportPrefix.size()));
    payload["recipient_id"] = kBob;

    CHECK_THROWS_AS(
        groups::decode_transport(std::string(groups::kTransportPrefix) + payload.dump()),
        groups::WireError);
}

TEST_CASE("an oversized group message is refused before parsing", "[groups]") {
    const std::string huge = std::string(groups::kTransportPrefix) +
                             std::string(groups::kMaxTransportBytes + 1, 'x');
    CHECK_THROWS_AS(groups::decode_transport(huge), groups::WireError);
}

TEST_CASE("the invite signature payload matches the reference byte for byte",
          "[groups][vectors]") {
    const nlohmann::json fixture = load_vector("groups").at("invite");

    groups::GroupInvite invite;
    invite.invite_id = fixture.at("invite_id").get<std::string>();
    invite.group_id = fixture.at("group_id").get<std::string>();
    invite.title = fixture.at("title").get<std::string>();
    invite.members = fixture.at("members").get<std::vector<std::string>>();
    invite.epoch = fixture.at("epoch").get<std::uint64_t>();
    invite.inviter_id = fixture.at("inviter_id").get<std::string>();
    invite.created_at = fixture.at("created_at").get<std::string>();
    invite.inviter_signing_pub = fixture.at("inviter_signing_pub_hex").get<std::string>();

    CHECK(to_string(ByteView(groups::invite_signature_payload(invite))) ==
          fixture.at("canonical_signed_bytes_utf8").get<std::string>());
}

TEST_CASE("the reference invite token opens and verifies", "[groups][vectors]") {
    crypto::init();
    const nlohmann::json fixture = load_vector("groups").at("invite");
    const groups::GroupInvite invite =
        groups::decode_invite(fixture.at("token").get<std::string>());

    CHECK(invite.invite_id == fixture.at("invite_id").get<std::string>());
    CHECK(invite.group_id == fixture.at("group_id").get<std::string>());
    CHECK(invite.title == fixture.at("title").get<std::string>());
    CHECK(invite.epoch == fixture.at("epoch").get<std::uint64_t>());
    CHECK(invite.inviter_id == kAlice);
    CHECK(invite.members == std::vector<std::string>{kAlice, kBob});
    CHECK(invite.created_at == fixture.at("created_at").get<std::string>());
    CHECK(invite.expires_at.empty());
    CHECK(invite.inviter_signing_pub ==
          fixture.at("inviter_signing_pub_hex").get<std::string>());
    CHECK(invite.signature == fixture.at("signature_hex").get<std::string>());
}

TEST_CASE("a sealed invite token shows nothing on sight", "[groups][vectors]") {
    // The seal is not a secret — the wrap key is in the token — but a shared
    // invite must not display the group title or its members' addresses.
    const nlohmann::json fixture = load_vector("groups").at("invite");
    const std::string token = fixture.at("token").get<std::string>();

    CHECK(token.find(kAlice) == std::string::npos);
    CHECK(token.find("group-alpha") == std::string::npos);
    CHECK(token.find(std::string(groups::kInvitePrefix)) == std::string::npos);
    CHECK(groups::looks_like_invite(token));
}

TEST_CASE("an invite round trips through our own encoder", "[groups]") {
    crypto::init();
    const crypto::SigningKeyPair keys = crypto::generate_signing_keypair();

    groups::GroupInvite invite;
    invite.invite_id = "abcdef0123456789";
    invite.group_id = "group-alpha";
    invite.title = "Группа 🎯";
    invite.members = {kBob};
    invite.epoch = 2;
    invite.inviter_id = kAlice;
    invite.created_at = "2026-01-01T12:00:00+00:00";

    const groups::GroupInvite decoded =
        groups::decode_invite(groups::encode_invite(invite, ByteView(keys.seed)));

    CHECK(decoded.title == "Группа 🎯");
    // The inviter is added to the roster, since they are in the group by
    // construction.
    CHECK(decoded.members == std::vector<std::string>{kBob, kAlice});
    CHECK(decoded.inviter_signing_pub == encoding::hex_encode(ByteView(keys.public_key)));
}

TEST_CASE("an invite whose roster was edited fails verification", "[groups]") {
    // The signature covers the member list, which is the entire security value
    // of a signed invite: nobody can add themselves to a group on the way.
    crypto::init();
    const nlohmann::json fixture = load_vector("groups").at("invite");
    const groups::GroupInvite original =
        groups::decode_invite(fixture.at("token").get<std::string>());

    groups::GroupInvite tampered = original;
    tampered.members.push_back("zzzzyyyyxxxxwwwwvvvvuuuuttttssssrrrrqqqqppppoooonnnn");

    nlohmann::json payload = nlohmann::json::object();
    payload["v"] = tampered.version;
    payload["invite_id"] = tampered.invite_id;
    payload["group_id"] = tampered.group_id;
    payload["title"] = tampered.title;
    payload["members"] = tampered.members;
    payload["epoch"] = tampered.epoch;
    payload["inviter_id"] = tampered.inviter_id;
    payload["inviter_signing_pub"] = tampered.inviter_signing_pub;
    payload["created_at"] = tampered.created_at;
    payload["expires_at"] = nlohmann::json();
    payload["signature"] = tampered.signature;

    CHECK_THROWS_AS(
        groups::decode_invite(std::string(groups::kInvitePrefix) + payload.dump()),
        groups::WireError);
}

TEST_CASE("an expired invite is refused", "[groups]") {
    crypto::init();
    const crypto::SigningKeyPair keys = crypto::generate_signing_keypair();

    groups::GroupInvite invite;
    invite.invite_id = "abcdef0123456789";
    invite.group_id = "group-alpha";
    invite.members = {kBob};
    invite.epoch = 1;
    invite.inviter_id = kAlice;
    invite.created_at = "2026-01-01T12:00:00+00:00";
    invite.expires_at = "2026-01-02T12:00:00+00:00";

    const std::string token = groups::encode_invite(invite, ByteView(keys.seed));

    const auto before = storage::parse_iso8601_utc("2026-01-02T11:00:00+00:00");
    const auto after = storage::parse_iso8601_utc("2026-01-03T00:00:00+00:00");
    REQUIRE(before.has_value());
    REQUIRE(after.has_value());

    CHECK(groups::decode_invite(token, before).expires_at == invite.expires_at);
    CHECK_THROWS_AS(groups::decode_invite(token, after), groups::WireError);
}

TEST_CASE("unsigned v1 invites are refused", "[groups]") {
    const nlohmann::json payload = {
        {"v", 1},
        {"invite_id", "abcdef0123456789"},
        {"group_id", "group-alpha"},
        {"members", {kAlice, kBob}},
        {"epoch", 1},
        {"inviter_id", kAlice},
    };
    CHECK_THROWS_AS(
        groups::decode_invite(std::string(groups::kInvitePrefix) + payload.dump()),
        groups::WireError);
}

TEST_CASE("text that is not an invite is not mistaken for one", "[groups]") {
    CHECK_FALSE(groups::looks_like_invite(""));
    CHECK_FALSE(groups::looks_like_invite("   "));
    CHECK_FALSE(groups::looks_like_invite("just a chat message with spaces"));
    CHECK_FALSE(groups::looks_like_invite("notbase64url!!!"));
    CHECK_THROWS_AS(groups::decode_invite("notbase64url!!!"), groups::WireError);
}

TEST_CASE("an invite token survives being wrapped across lines",
          "[groups][vectors]") {
    // Tokens get pasted back out of mail clients with line breaks in them.
    crypto::init();
    const std::string token =
        load_vector("groups").at("invite").at("token").get<std::string>();

    std::string wrapped;
    for (std::size_t i = 0; i < token.size(); i += 64) {
        wrapped += token.substr(i, 64);
        wrapped += "\n";
    }
    CHECK(groups::decode_invite(wrapped).group_id == "group-alpha");
}

TEST_CASE("the invite encoder rejects what it cannot sign", "[groups]") {
    crypto::init();
    const crypto::SigningKeyPair keys = crypto::generate_signing_keypair();

    groups::GroupInvite invite;
    invite.invite_id = "abcdef0123456789";
    invite.group_id = "group-alpha";
    invite.members = {kBob};
    invite.inviter_id = kAlice;

    CHECK_THROWS_AS(groups::encode_invite(invite, ByteView(Bytes{})), groups::WireError);

    groups::GroupInvite no_group = invite;
    no_group.group_id = "  ";
    CHECK_THROWS_AS(groups::encode_invite(no_group, ByteView(keys.seed)),
                    groups::WireError);

    groups::GroupInvite no_inviter = invite;
    no_inviter.inviter_id = "";
    CHECK_THROWS_AS(groups::encode_invite(no_inviter, ByteView(keys.seed)),
                    groups::WireError);

    groups::GroupInvite no_members = invite;
    no_members.members = {};
    CHECK_THROWS_AS(groups::encode_invite(no_members, ByteView(keys.seed)),
                    groups::WireError);
}
