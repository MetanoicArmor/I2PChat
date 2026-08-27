#pragma once

#include <filesystem>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"

/// The per-profile contact list: most-recently-used peers with display names,
/// notes and a preview of the last message.
///
/// Sealed as `I2CB` under domain `I2PCHAT-CONTACTS`. Version 1 of the payload
/// stored a plain list of address strings; it is still read and upgraded to
/// version 2 on the next save.
namespace i2pchat::storage {

inline constexpr std::size_t kMaxContacts = 500;
inline constexpr std::uint32_t kContactBookVersion = 2;
/// Measured in Unicode code points, matching the reference implementation.
inline constexpr std::size_t kPreviewMaxLength = 80;

/// Canonical contact id: the lowercase base32 host, `.b32.i2p` suffix removed.
///
/// Stricter than `sam::normalize_peer_address`: the whole string has to be an
/// address, because anything else in a contact file is corruption rather than
/// something a user pasted.
[[nodiscard]] std::string normalize_contact_address(std::string_view raw);

/// True when both strings denote the same destination. Falls back to a
/// case-insensitive comparison when neither side is a valid address, so callers
/// can compare identifiers this module does not recognise.
[[nodiscard]] bool same_i2p_destination(std::string_view left, std::string_view right);

struct ContactRecord {
    std::string addr;
    std::string display_name;
    std::string note;
    std::string last_preview;
    std::string last_activity_ts;
};

class ContactBook {
public:
    [[nodiscard]] const std::vector<ContactRecord>& contacts() const noexcept {
        return contacts_;
    }
    [[nodiscard]] std::vector<ContactRecord>& contacts() noexcept { return contacts_; }

    [[nodiscard]] const std::optional<std::string>& last_active_peer() const noexcept {
        return last_active_peer_;
    }

    /// Index of `addr` in most-recently-used order, or -1.
    [[nodiscard]] std::ptrdiff_t peer_index(std::string_view addr) const;
    [[nodiscard]] const ContactRecord* get(std::string_view addr) const;
    [[nodiscard]] ContactRecord* get(std::string_view addr);
    [[nodiscard]] bool has_peer(std::string_view addr) const;
    [[nodiscard]] std::vector<std::string> ordered_peer_addrs() const;

    /// Each mutator returns whether the book actually changed, so a caller can
    /// skip a save — these files are rewritten on almost every message.
    bool remember_peer(std::string_view addr);
    bool set_last_active_peer(std::optional<std::string_view> addr);
    bool set_peer_profile(std::string_view addr, std::string_view display_name,
                          std::string_view note);
    bool touch_peer_message_meta(std::string_view addr, std::string_view preview,
                                 std::string_view ts_iso);
    bool remove_peer(std::string_view addr);

    /// Drop everything past `kMaxContacts`.
    void trim();

private:
    void set_last_active_raw(std::optional<std::string> value) {
        last_active_peer_ = std::move(value);
    }

    std::vector<ContactRecord> contacts_;
    std::optional<std::string> last_active_peer_;
};

/// Parse a decoded payload. Unreadable entries are dropped rather than fatal:
/// losing one malformed contact beats refusing to open the profile.
[[nodiscard]] ContactBook parse_contact_book(const nlohmann::json& data);
[[nodiscard]] nlohmann::json contact_book_to_json(const ContactBook& book);

/// Read the contact book, returning an empty one if the file is missing or
/// unreadable — the reference implementation does the same, and a contact list
/// is not worth blocking startup over.
[[nodiscard]] ContactBook load_contact_book(const std::filesystem::path& path,
                                            std::optional<ByteView> identity_key);

void save_contact_book(const std::filesystem::path& path, const ContactBook& book,
                       std::optional<ByteView> identity_key);

}  // namespace i2pchat::storage
