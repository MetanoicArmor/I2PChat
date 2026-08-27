#pragma once

#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/crypto.hpp"

/// The HS4 handshake, as a pure state machine.
///
/// Deliberately free of I/O: it consumes handshake frame bodies and produces
/// frame bodies to send. The transport, the SAM session and the UI are all
/// somebody else's problem, which makes every branch — including the failure
/// branches that matter most for security — reachable from a unit test.
///
/// Message formats (all carried in plaintext `H` frames):
///
///   INIT:<nonce_hex>:<eph_pub_hex>:<sign_pub_hex>:<signature_hex>
///   RESP:<nonce_hex>:<eph_pub_hex>:<sign_pub_hex>:<signature_hex>
///   FINISHED:<hmac_hex>
namespace i2pchat::session {

class HandshakeError : public std::runtime_error {
public:
    explicit HandshakeError(const std::string& message)
        : std::runtime_error(message) {}
};

enum class HandshakeState {
    Idle,
    /// Local INIT sent; waiting for RESP.
    InitSent,
    /// Directional keys derived; waiting for the peer's FINISHED.
    AwaitingFinished,
    /// Both FINISHED exchanged. The channel is secure.
    Established,
    Failed,
};

enum class HandshakeRole { Unknown, Initiator, Responder };

/// Directional keys, named from the local endpoint's point of view.
struct SessionKeys {
    Bytes send_enc;
    Bytes send_mac;
    Bytes recv_enc;
    Bytes recv_mac;

    [[nodiscard]] bool installed() const noexcept { return send_enc.size() == 32; }
};

/// Decision returned by the trust callback.
enum class TrustDecision {
    /// Key matches a pin, or was newly pinned. Continue.
    Accept,
    /// Key contradicts an existing pin, or the user refused. Abort.
    Reject,
};

/// Verifies a peer's Ed25519 signing key against the TOFU store. Called after
/// the signature checks out but before any key material is installed, so a key
/// change cannot be used to establish a session.
using TrustVerifier =
    std::function<TrustDecision(const std::string& peer_addr, ByteView signing_key)>;

struct HandshakeConfig {
    /// Normalized local base32 address (no `.b32.i2p` suffix).
    std::string local_addr;
    /// Normalized peer base32 address.
    std::string peer_addr;
    /// Local Ed25519 signing seed and its public key.
    Bytes signing_seed;
    Bytes signing_public;
    TrustVerifier trust_verifier;
};

/// What the caller should do after feeding the machine an input.
struct HandshakeOutput {
    /// Plaintext `H` frame bodies to send, in order.
    std::vector<std::string> frames;
    /// True on the transition into Established.
    bool established = false;
};

class HandshakeMachine {
public:
    explicit HandshakeMachine(HandshakeConfig config);

    /// Produce the INIT message. Only valid from Idle.
    HandshakeOutput start_as_initiator();

    /// Feed one handshake frame body. Throws HandshakeError on any violation;
    /// the caller must then tear the connection down.
    HandshakeOutput on_message(std::string_view body);

    [[nodiscard]] HandshakeState state() const noexcept { return state_; }
    [[nodiscard]] HandshakeRole role() const noexcept { return role_; }
    [[nodiscard]] const SessionKeys& keys() const noexcept { return keys_; }
    [[nodiscard]] const Bytes& peer_signing_key() const noexcept {
        return peer_signing_public_;
    }

    /// Ephemeral keys may be injected before starting, for deterministic tests.
    void set_ephemeral_for_test(Bytes private_key, Bytes public_key);
    void set_nonce_for_test(Bytes nonce);

private:
    HandshakeOutput handle_init(std::string_view payload);
    HandshakeOutput handle_resp(std::string_view payload);
    HandshakeOutput handle_finished(std::string_view payload);

    void ensure_ephemeral();
    void install_keys(bool is_initiator);
    void fail(const std::string& reason);

    HandshakeConfig config_;
    HandshakeState state_ = HandshakeState::Idle;
    HandshakeRole role_ = HandshakeRole::Unknown;

    Bytes my_ephemeral_private_;
    Bytes my_ephemeral_public_;
    Bytes my_nonce_;
    Bytes peer_ephemeral_public_;
    Bytes peer_nonce_;
    Bytes peer_signing_public_;

    SessionKeys keys_;
    Bytes transcript_hash_;
    bool finished_sent_ = false;
    bool peer_finished_ = false;
};

/// Build the byte string signed by the initiator.
///
/// Note the `HS3` domain label: the signed transcript uses HS3 while key
/// derivation uses HS4. That inconsistency is part of the deployed protocol and
/// must not be "fixed".
Bytes build_init_sig_payload(std::string_view signer_addr, std::string_view remote_addr,
                            std::string_view nonce_hex, std::string_view eph_hex,
                            std::string_view sign_pub_hex);

/// Build the byte string signed by the responder.
Bytes build_resp_sig_payload(std::string_view signer_addr, std::string_view remote_addr,
                            std::string_view init_nonce_hex,
                            std::string_view init_eph_hex,
                            std::string_view init_sign_pub_hex,
                            std::string_view resp_nonce_hex,
                            std::string_view resp_eph_hex,
                            std::string_view resp_sign_pub_hex);

}  // namespace i2pchat::session
