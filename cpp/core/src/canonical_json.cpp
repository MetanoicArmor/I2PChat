#include "i2pchat/canonical_json.hpp"

namespace i2pchat::json_canonical {

std::string dump(const Json& value) {
    // indent = -1 selects the compact form with "," and ":" separators;
    // ensure_ascii = true escapes every non-ASCII code point.
    return value.dump(-1, ' ', /*ensure_ascii=*/true);
}

Bytes dump_bytes(const Json& value) {
    const std::string text = dump(value);
    return to_bytes(text);
}

}  // namespace i2pchat::json_canonical
