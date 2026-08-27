#include "i2pchat/storage/compose_drafts.hpp"

#include <nlohmann/json.hpp>

#include "i2pchat/storage/profile_paths.hpp"
#include "i2pchat/storage/sealed_json.hpp"

namespace i2pchat::storage {

ComposeDrafts load_compose_drafts(const std::filesystem::path& path,
                                  std::optional<ByteView> identity_key) {
    ComposeDrafts drafts;
    nlohmann::json data;
    try {
        data = read_sealed_json(path, identity_key, kComposeDraftsFormat);
    } catch (const std::exception&) {
        return drafts;
    }
    if (!data.is_object()) {
        return drafts;
    }
    const auto raw = data.find("drafts");
    if (raw == data.end() || !raw->is_object()) {
        return drafts;
    }
    for (const auto& [key, value] : raw->items()) {
        if (value.is_string()) {
            drafts.emplace(key, value.get<std::string>());
        }
    }
    return drafts;
}

void save_compose_drafts(const std::filesystem::path& path, const ComposeDrafts& drafts,
                         std::optional<ByteView> identity_key) {
    nlohmann::json payload = nlohmann::json::object();
    payload["version"] = kComposeDraftsVersion;
    payload["drafts"] = nlohmann::json(drafts);
    write_sealed_json(path, payload, identity_key, kComposeDraftsFormat);
}

}  // namespace i2pchat::storage
