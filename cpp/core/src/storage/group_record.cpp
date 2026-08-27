#include "i2pchat/storage/group_record.hpp"

#include <algorithm>
#include <set>

#include "i2pchat/storage/sealed_json.hpp"

namespace i2pchat::storage {

nlohmann::json read_group_record(const std::filesystem::path& path,
                                 std::string_view group_id,
                                 std::optional<ByteView> identity_key) {
    return read_sealed_json(path, identity_key, group_store_format(group_id));
}

void write_group_record(const std::filesystem::path& path, std::string_view group_id,
                        const nlohmann::json& payload,
                        std::optional<ByteView> identity_key) {
    write_sealed_json(path, payload, identity_key, group_store_format(group_id));
}

std::vector<std::string> known_group_records(
    const ProfilePaths& paths, const std::vector<std::string>& candidate_ids) {
    std::set<std::string> present;
    for (const std::filesystem::path& path : list_group_record_files(paths)) {
        const std::string name = path.filename().string();
        const std::string prefix = paths.profile() + ".group.";
        present.insert(name.substr(prefix.size(),
                                   name.size() - prefix.size() - std::string(".json").size()));
    }

    std::vector<std::string> found;
    for (const std::string& id : candidate_ids) {
        if (present.contains(group_token(id))) {
            found.push_back(id);
        }
    }
    return found;
}

std::vector<std::filesystem::path> list_group_record_files(const ProfilePaths& paths) {
    const std::string prefix = paths.profile() + ".group.";
    constexpr std::string_view kSuffix = ".json";
    std::vector<std::filesystem::path> found;

    std::error_code error;
    for (const auto& entry : std::filesystem::directory_iterator(paths.data_dir(), error)) {
        const std::string name = entry.path().filename().string();
        if (name.size() > prefix.size() + kSuffix.size() && name.starts_with(prefix) &&
            name.ends_with(kSuffix)) {
            found.push_back(entry.path());
        }
    }
    std::sort(found.begin(), found.end());
    return found;
}

}  // namespace i2pchat::storage
