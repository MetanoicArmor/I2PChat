#include "i2pchat/sam/destination.hpp"

#include <algorithm>
#include <cctype>
#include <regex>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

namespace i2pchat::sam {
namespace {

std::size_t read_cert_len(ByteView blob) {
    if (blob.size() < kPublicPrefixLen) {
        throw DestinationError("Private destination blob is too short");
    }
    return read_u16_be(blob.subspan(kCertLenOffset, 2));
}

}  // namespace

Destination Destination::from_public_base64(std::string_view base64) {
    const std::string trimmed(base64);
    const std::optional<Bytes> decoded = encoding::i2p_base64_decode(trimmed);
    if (!decoded.has_value()) {
        throw DestinationError("Destination is not valid I2P base64");
    }
    if (decoded->empty()) {
        throw DestinationError("Destination is empty");
    }
    Destination dest;
    dest.data_ = *decoded;
    dest.base64_ = trimmed;
    return dest;
}

Destination Destination::from_private_blob(ByteView blob) {
    const std::size_t cert_len = read_cert_len(blob);
    const std::size_t public_len = kPublicPrefixLen + cert_len;
    // An inflated cert_len would otherwise publish private key bytes as part of
    // the destination.
    if (public_len > blob.size()) {
        throw DestinationError("Destination certificate length exceeds blob size");
    }
    if (public_len >= blob.size()) {
        throw DestinationError("Private destination blob is missing private-key bytes");
    }

    Destination dest;
    dest.private_key_ = Bytes(blob.begin(), blob.end());
    dest.data_ = Bytes(blob.begin(), blob.begin() + static_cast<std::ptrdiff_t>(public_len));
    dest.base64_ = encoding::i2p_base64_encode(ByteView(dest.data_));
    return dest;
}

Destination Destination::from_private_base64(std::string_view base64) {
    const std::optional<Bytes> decoded = encoding::i2p_base64_decode(base64);
    if (!decoded.has_value()) {
        throw DestinationError("Private destination is not valid I2P base64");
    }
    return from_private_blob(ByteView(*decoded));
}

std::string Destination::base32() const {
    const Bytes digest = crypto::sha256(ByteView(data_));
    std::string encoded = encoding::base32_encode_lower(ByteView(digest));
    encoded.resize(std::min<std::size_t>(encoded.size(), 52));
    return encoded;
}

const Bytes& Destination::private_key() const {
    if (!private_key_.has_value()) {
        throw DestinationError("Destination carries no private key");
    }
    return *private_key_;
}

std::string Destination::private_key_base64() const {
    return encoding::i2p_base64_encode(ByteView(private_key()));
}

std::string normalize_peer_address(std::string_view raw) {
    std::string lower;
    lower.reserve(raw.size());
    for (const char ch : raw) {
        lower.push_back(static_cast<char>(
            std::tolower(static_cast<unsigned char>(ch))));
    }
    // Trim surrounding whitespace before matching so a bare host still works.
    const auto first = lower.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }
    const auto last = lower.find_last_not_of(" \t\r\n");
    lower = lower.substr(first, last - first + 1);

    // Prefer an embedded ".b32.i2p" host, ignoring any prefix such as
    // "My Addr: ...".
    static const std::regex kSuffixed(R"(([a-z2-7]{40,80})\.b32\.i2p)");
    std::smatch match;
    if (std::regex_search(lower, match, kSuffixed)) {
        return match[1].str();
    }
    // Otherwise accept a bare base32 host.
    static const std::regex kBare(R"(^[a-z2-7]{40,80}$)");
    if (std::regex_match(lower, kBare)) {
        return lower;
    }
    return "";
}

}  // namespace i2pchat::sam
