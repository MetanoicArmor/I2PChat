#pragma once

#include <fstream>
#include <nlohmann/json.hpp>
#include <optional>
#include <stdexcept>
#include <string>

#include "i2pchat/bytes.hpp"
#include "i2pchat/encoding.hpp"

/// Loader for the golden vectors in cpp/testdata/vectors.
///
/// These fixtures are generated from the reference Python implementation, so a
/// failure in any test that consumes them means the C++ port would not
/// interoperate with a released client — not merely that a unit test is unhappy.
namespace i2pchat::testing {

inline nlohmann::json load_vector(const std::string& name) {
    const std::string path = std::string(I2PCHAT_VECTORS_DIR) + "/" + name + ".json";
    std::ifstream stream(path);
    if (!stream) {
        throw std::runtime_error("Cannot open golden vector: " + path);
    }
    nlohmann::json document;
    stream >> document;
    return document;
}

/// Decode a hex field, failing loudly rather than silently yielding an empty
/// buffer that would make a comparison pass for the wrong reason.
inline Bytes hex_field(const nlohmann::json& node, const std::string& key) {
    if (!node.contains(key)) {
        throw std::runtime_error("Vector is missing field: " + key);
    }
    const auto text = node.at(key).get<std::string>();
    const std::optional<Bytes> decoded = encoding::hex_decode(text);
    if (!decoded.has_value()) {
        throw std::runtime_error("Vector field is not valid hex: " + key);
    }
    return *decoded;
}

inline std::string hex_of(ByteView data) { return encoding::hex_encode(data); }

}  // namespace i2pchat::testing
