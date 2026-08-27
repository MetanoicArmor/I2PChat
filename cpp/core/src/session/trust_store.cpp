#include "i2pchat/session/trust_store.hpp"

#include <algorithm>
#include <cctype>
#include <nlohmann/json.hpp>

#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/atomic_write.hpp"

namespace i2pchat::session {
namespace {

std::string to_lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return text;
}

}  // namespace

TrustStore::TrustStore(std::filesystem::path path) : path_(std::move(path)) {}

void TrustStore::load() {
    pins_.clear();
    if (path_.empty() || !std::filesystem::exists(path_)) {
        return;
    }

    nlohmann::json document;
    try {
        const Bytes raw = storage::read_file(path_);
        document = nlohmann::json::parse(to_string(ByteView(raw)));
    } catch (const std::exception&) {
        // A corrupt trust store must not prevent startup. Losing pins downgrades
        // to first-sighting prompts, which is visible to the user, whereas
        // refusing to launch is not recoverable without a shell.
        return;
    }
    if (!document.is_object()) {
        return;
    }

    const int version = document.value("version", 1);
    if (version >= 2 && document.contains("pins") && document.at("pins").is_object()) {
        for (const auto& [peer, entry] : document.at("pins").items()) {
            if (entry.is_object()) {
                const std::string key =
                    to_lower(entry.value("signing_key_hex", std::string{}));
                if (key.empty()) {
                    continue;
                }
                pins_[peer] = TrustPin{key, entry.value("oob_verified", false)};
            } else if (entry.is_string()) {
                // Tolerated shorthand seen in hand-edited stores.
                const std::string key = to_lower(entry.get<std::string>());
                if (!key.empty()) {
                    pins_[peer] = TrustPin{key, false};
                }
            }
        }
        return;
    }

    // Version 1: a flat {peer: hex} map.
    for (const auto& [peer, entry] : document.items()) {
        if (entry.is_string()) {
            pins_[peer] = TrustPin{to_lower(entry.get<std::string>()), false};
        }
    }
}

void TrustStore::save() const {
    if (path_.empty()) {
        return;
    }
    nlohmann::json pins = nlohmann::json::object();
    for (const auto& [peer, pin] : pins_) {
        pins[peer] = {{"signing_key_hex", pin.signing_key_hex},
                      {"oob_verified", pin.oob_verified}};
    }
    storage::atomic_write_json(path_, {{"version", 2}, {"pins", pins}});
}

std::optional<TrustPin> TrustStore::pin_for(const std::string& peer_addr) const {
    const auto it = pins_.find(peer_addr);
    if (it == pins_.end()) {
        return std::nullopt;
    }
    return it->second;
}

TrustDecision TrustStore::verify_or_pin(const std::string& peer_addr,
                                        ByteView signing_key) {
    if (peer_addr.empty() || signing_key.size() != crypto::kEd25519PublicSize) {
        return TrustDecision::Reject;
    }
    const std::string key_hex = encoding::hex_encode(signing_key);

    const auto existing = pins_.find(peer_addr);
    if (existing == pins_.end()) {
        // First sighting. Without a prompt handler the key is pinned silently,
        // which is the trust-on-first-use bargain the protocol is built on.
        if (prompt_ && prompt_(TrustPrompt::FirstSighting, peer_addr, key_hex, "") !=
                           TrustDecision::Accept) {
            return TrustDecision::Reject;
        }
        pins_[peer_addr] = TrustPin{key_hex, false};
        save();
        return TrustDecision::Accept;
    }

    if (existing->second.signing_key_hex == key_hex) {
        return TrustDecision::Accept;
    }

    // The pinned key changed. This is either a reinstalled peer or an
    // impersonation attempt, and the two are indistinguishable from here, so
    // the decision belongs to the user and the default is no.
    if (!prompt_) {
        return TrustDecision::Reject;
    }
    if (prompt_(TrustPrompt::KeyChanged, peer_addr, key_hex,
                existing->second.signing_key_hex) != TrustDecision::Accept) {
        return TrustDecision::Reject;
    }
    existing->second = TrustPin{key_hex, false};
    save();
    return TrustDecision::Accept;
}

bool TrustStore::mark_oob_verified(const std::string& peer_addr, bool verified) {
    const auto it = pins_.find(peer_addr);
    if (it == pins_.end()) {
        return false;
    }
    it->second.oob_verified = verified;
    save();
    return true;
}

bool TrustStore::forget(const std::string& peer_addr) {
    if (pins_.erase(peer_addr) == 0) {
        return false;
    }
    save();
    return true;
}

}  // namespace i2pchat::session
