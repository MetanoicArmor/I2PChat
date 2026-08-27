#pragma once

#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <string>

#include "i2pchat/bytes.hpp"
#include "i2pchat/session/handshake.hpp"

namespace i2pchat::session {

/// One pinned peer identity.
struct TrustPin {
    /// Lowercase hex of the peer's Ed25519 signing public key.
    std::string signing_key_hex;
    /// The user confirmed this key through a channel other than I2PChat.
    bool oob_verified = false;
};

/// How the user (or a policy) resolves a first sighting or a key change.
enum class TrustPrompt { FirstSighting, KeyChanged };
using TrustPromptHandler =
    std::function<TrustDecision(TrustPrompt prompt, const std::string& peer_addr,
                                const std::string& new_key_hex,
                                const std::string& old_key_hex)>;

/// Trust-on-first-use store for peer signing keys.
///
/// Persisted as plaintext JSON in `{profile}.trust.json` — it holds public keys
/// only, and keeping it readable lets a user inspect and edit their own pins.
///
///   {"version": 2, "pins": {"<peer>": {"signing_key_hex": "...",
///                                      "oob_verified": false}}}
///
/// Version 1 was a flat `{peer: hex}` map and is still read.
class TrustStore {
public:
    /// A store with no backing file. Used for the transient profile, which by
    /// design forgets its pins.
    TrustStore() = default;
    explicit TrustStore(std::filesystem::path path);

    void load();
    void save() const;

    [[nodiscard]] std::optional<TrustPin> pin_for(const std::string& peer_addr) const;
    [[nodiscard]] const std::map<std::string, TrustPin>& pins() const noexcept {
        return pins_;
    }

    /// Check a peer's key against the store, pinning it on first sighting.
    ///
    /// A key that contradicts an existing pin is never accepted silently: with
    /// no prompt handler installed the answer is Reject, because the safe
    /// default for an unexplained identity change is to refuse the session.
    TrustDecision verify_or_pin(const std::string& peer_addr, ByteView signing_key);

    void set_prompt_handler(TrustPromptHandler handler) {
        prompt_ = std::move(handler);
    }

    /// Mark a pin as verified out of band. Returns false when unknown.
    bool mark_oob_verified(const std::string& peer_addr, bool verified = true);

    /// Drop a pin so the next connection is treated as a first sighting.
    bool forget(const std::string& peer_addr);

    /// A store without a path never persists anything.
    [[nodiscard]] bool persistent() const noexcept { return !path_.empty(); }

private:
    std::filesystem::path path_;
    std::map<std::string, TrustPin> pins_;
    TrustPromptHandler prompt_;
};

}  // namespace i2pchat::session
