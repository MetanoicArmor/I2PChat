/// A thin command line over the storage layer, used by the Python interop tests
/// to prove that both implementations read and write the same files.
///
/// Every subcommand takes the profile directory, the profile name and the
/// identity key as hex, prints JSON on stdout and reads JSON from stdin where a
/// payload is needed. Nothing here is used by the application itself.

#include <iostream>
#include <map>
#include <nlohmann/json.hpp>
#include <string>
#include <vector>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"
#include "i2pchat/storage/chat_history.hpp"
#include "i2pchat/storage/compose_drafts.hpp"
#include "i2pchat/storage/contacts.hpp"
#include "i2pchat/storage/group_record.hpp"
#include "i2pchat/storage/keyring.hpp"
#include "i2pchat/storage/profile_dat.hpp"
#include "i2pchat/storage/profile_paths.hpp"

namespace {

using namespace i2pchat;

nlohmann::json read_stdin_json() {
    return nlohmann::json::parse(std::string(std::istreambuf_iterator<char>(std::cin),
                                             std::istreambuf_iterator<char>()));
}

nlohmann::json contacts_to_json(const storage::ContactBook& book) {
    nlohmann::json contacts = nlohmann::json::array();
    for (const storage::ContactRecord& record : book.contacts()) {
        contacts.push_back({{"addr", record.addr},
                            {"display_name", record.display_name},
                            {"note", record.note},
                            {"last_preview", record.last_preview},
                            {"last_activity_ts", record.last_activity_ts}});
    }
    nlohmann::json out;
    out["contacts"] = std::move(contacts);
    out["last_active_peer"] = book.last_active_peer().has_value()
                                  ? nlohmann::json(*book.last_active_peer())
                                  : nlohmann::json();
    return out;
}

storage::ContactBook contacts_from_json(const nlohmann::json& data) {
    storage::ContactBook book;
    for (const auto& entry : data.at("contacts")) {
        const std::string addr = entry.at("addr").get<std::string>();
        book.remember_peer(addr);
        book.set_peer_profile(addr, entry.value("display_name", ""),
                              entry.value("note", ""));
        book.touch_peer_message_meta(addr, entry.value("last_preview", ""),
                                     entry.value("last_activity_ts", ""));
    }
    // Inserting at the front reverses the order, so restore what was asked for.
    std::reverse(book.contacts().begin(), book.contacts().end());
    const auto last_active = data.find("last_active_peer");
    if (last_active != data.end() && last_active->is_string()) {
        book.set_last_active_peer(last_active->get<std::string>());
    }
    return book;
}

nlohmann::json history_to_json_list(const std::vector<storage::HistoryEntry>& entries) {
    nlohmann::json out = nlohmann::json::array();
    for (const storage::HistoryEntry& entry : entries) {
        out.push_back({{"kind", entry.kind},
                       {"text", entry.text},
                       {"ts", entry.ts},
                       {"message_id", entry.message_id.has_value()
                                          ? nlohmann::json(*entry.message_id)
                                          : nlohmann::json()},
                       {"delivery_state", entry.delivery_state.has_value()
                                              ? nlohmann::json(*entry.delivery_state)
                                              : nlohmann::json()},
                       {"delivery_route", entry.delivery_route.has_value()
                                              ? nlohmann::json(*entry.delivery_route)
                                              : nlohmann::json()},
                       {"delivery_hint", entry.delivery_hint},
                       {"delivery_reason", entry.delivery_reason},
                       {"retryable", entry.retryable}});
    }
    return out;
}

std::vector<storage::HistoryEntry> history_from_json(const nlohmann::json& data) {
    std::vector<storage::HistoryEntry> entries;
    for (const auto& item : data) {
        storage::HistoryEntry entry;
        entry.kind = item.value("kind", "in");
        entry.text = item.value("text", "");
        entry.ts = item.value("ts", "");
        if (item.contains("message_id") && item.at("message_id").is_string()) {
            entry.message_id = item.at("message_id").get<std::string>();
        }
        if (item.contains("delivery_state") && item.at("delivery_state").is_string()) {
            entry.delivery_state = item.at("delivery_state").get<std::string>();
        }
        if (item.contains("delivery_route") && item.at("delivery_route").is_string()) {
            entry.delivery_route = item.at("delivery_route").get<std::string>();
        }
        entry.delivery_hint = item.value("delivery_hint", "");
        entry.delivery_reason = item.value("delivery_reason", "");
        entry.retryable = item.value("retryable", false);
        entries.push_back(std::move(entry));
    }
    return entries;
}

int usage() {
    std::cerr << "usage: interop_storage <command> <dir> <profile> [args]\n"
                 "commands: read-contacts write-contacts read-drafts write-drafts\n"
                 "          read-history write-history read-group write-group\n"
                 "          read-dat write-dat\n";
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 4) {
        return usage();
    }
    crypto::init();
    // The credential store is off: these runs must not touch the developer's
    // keychain, and the sidecar is what the interop test compares.
    storage::keyring::set_enabled(false);

    const std::string command = argv[1];
    const std::filesystem::path dir = argv[2];
    const std::string profile = argv[3];
    const storage::ProfilePaths paths(dir, profile);

    const auto identity_hex = [argc, argv]() -> std::string {
        return argc > 4 ? argv[4] : "";
    };
    const auto identity = [&identity_hex]() -> Bytes {
        const std::optional<Bytes> decoded = encoding::hex_decode(identity_hex());
        if (!decoded.has_value() || decoded->size() != 32) {
            throw std::runtime_error("identity key must be 32 bytes of hex");
        }
        return *decoded;
    };

    try {
        if (command == "read-contacts") {
            const Bytes key = identity();
            std::cout << contacts_to_json(
                             storage::load_contact_book(paths.contacts(), ByteView(key)))
                             .dump()
                      << "\n";
            return 0;
        }
        if (command == "write-contacts") {
            const Bytes key = identity();
            storage::save_contact_book(paths.contacts(), contacts_from_json(read_stdin_json()),
                                       ByteView(key));
            return 0;
        }
        if (command == "read-drafts") {
            const Bytes key = identity();
            const storage::ComposeDrafts drafts =
                storage::load_compose_drafts(paths.compose_drafts(), ByteView(key));
            std::cout << nlohmann::json(drafts).dump() << "\n";
            return 0;
        }
        if (command == "write-drafts") {
            const Bytes key = identity();
            const nlohmann::json data = read_stdin_json();
            storage::ComposeDrafts drafts;
            for (const auto& [conversation, text] : data.items()) {
                drafts.emplace(conversation, text.get<std::string>());
            }
            storage::save_compose_drafts(paths.compose_drafts(), drafts, ByteView(key));
            return 0;
        }
        if (command == "read-history") {
            if (argc < 6) {
                return usage();
            }
            const Bytes key = identity();
            std::cout << history_to_json_list(
                             storage::load_history(paths, argv[5], ByteView(key)))
                             .dump()
                      << "\n";
            return 0;
        }
        if (command == "write-history") {
            if (argc < 6) {
                return usage();
            }
            const Bytes key = identity();
            storage::save_history(paths, argv[5], history_from_json(read_stdin_json()),
                                  ByteView(key));
            return 0;
        }
        if (command == "read-group") {
            if (argc < 6) {
                return usage();
            }
            const Bytes key = identity();
            std::cout << storage::read_group_record(paths.group_store(argv[5]), argv[5],
                                                    ByteView(key))
                             .dump()
                      << "\n";
            return 0;
        }
        if (command == "write-group") {
            if (argc < 6) {
                return usage();
            }
            const Bytes key = identity();
            storage::write_group_record(paths.group_store(argv[5]), argv[5],
                                        read_stdin_json(), ByteView(key));
            return 0;
        }
        if (command == "read-dat") {
            const storage::ProfileDatContents contents = storage::read_profile_dat_file(
                paths.identity_dat(), profile, dir, {}, /*create_wrap_key=*/false);
            nlohmann::json out;
            out["private_key_base64"] = contents.private_key_base64.has_value()
                                            ? nlohmann::json(*contents.private_key_base64)
                                            : nlohmann::json();
            out["was_plaintext"] = contents.was_plaintext;
            std::cout << out.dump() << "\n";
            return 0;
        }
        if (command == "write-dat") {
            if (argc < 5) {
                return usage();
            }
            storage::write_encrypted_profile_dat(paths.identity_dat(), argv[4], profile,
                                                 dir);
            return 0;
        }
    } catch (const std::exception& error) {
        std::cerr << "interop_storage: " << error.what() << "\n";
        return 1;
    }
    return usage();
}
