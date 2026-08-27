#pragma once

#include <nlohmann/json.hpp>
#include <string>

#include "i2pchat/bytes.hpp"

/// Canonical JSON for signature-critical payloads.
///
/// Signed payloads in I2PChat are serialized by Python as
/// `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
/// Any divergence — a stray space, a different key order, a literal non-ASCII
/// character — silently breaks Ed25519 verification against real peers, so this
/// wrapper exists to give the requirement a name and a single implementation
/// that the golden-vector tests pin down.
namespace i2pchat::json_canonical {

using Json = nlohmann::json;

/// Compact, key-sorted, ASCII-escaped serialization.
///
/// nlohmann's default object type keeps keys in a std::map<std::string>, whose
/// byte-wise ordering matches Python's code-point ordering for valid UTF-8, and
/// its `ensure_ascii` escaping emits UTF-16 surrogate pairs for code points
/// outside the BMP exactly as Python does.
std::string dump(const Json& value);

/// Serialization of `dump` as bytes, ready for signing.
Bytes dump_bytes(const Json& value);

}  // namespace i2pchat::json_canonical
