#include "i2pchat/transfer/manager.hpp"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <stdexcept>
#include <system_error>

#include "i2pchat/crypto.hpp"
#include "i2pchat/encoding.hpp"

namespace i2pchat::transfer {
namespace {

constexpr std::string_view kFileEndBody = "";
constexpr std::string_view kImageEndBody = "__IMG_END__";
constexpr std::string_view kImageLinesEndBody = "__END__";
constexpr std::uint64_t kImageProgressStep = 65536;

std::string trim(std::string_view text) {
    const auto begin = text.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = text.find_last_not_of(" \t\r\n");
    return std::string(text.substr(begin, end - begin + 1));
}

/// `name|size`, as both the F and G offers spell it.
std::optional<std::pair<std::string, std::uint64_t>> parse_offer(std::string_view body) {
    const auto separator = body.find('|');
    if (separator == std::string_view::npos) {
        return std::nullopt;
    }
    const std::string_view name = body.substr(0, separator);
    const std::string size_text = trim(body.substr(separator + 1));
    // A second separator means the name contained one, which cannot be told
    // apart from a malformed header, so the offer is refused.
    if (name.find('|') != std::string_view::npos ||
        size_text.find('|') != std::string::npos) {
        return std::nullopt;
    }
    std::uint64_t size = 0;
    const auto* const begin = size_text.data();
    const auto* const end = begin + size_text.size();
    const auto parsed = std::from_chars(begin, end, size);
    if (parsed.ec != std::errc{} || parsed.ptr != end) {
        return std::nullopt;
    }
    return std::make_pair(std::string(name), size);
}

std::int64_t system_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

}  // namespace

OutgoingTransfer::OutgoingTransfer(std::filesystem::path path, bool inline_image,
                                   std::size_t chunk_bytes)
    : path_(std::move(path)),
      inline_image_(inline_image),
      chunk_bytes_(std::clamp<std::size_t>(chunk_bytes, 1024, 512 * 1024)) {
    std::error_code error;
    const auto size = std::filesystem::file_size(path_, error);
    if (error) {
        throw std::filesystem::filesystem_error("Cannot read the file to send", path_,
                                                error);
    }
    const std::uint64_t limit = inline_image_ ? kMaxImageSize : kMaxFileSize;
    if (size > limit) {
        throw std::length_error("File exceeds the maximum allowed size");
    }

    size_ = size;
    name_ = sanitize_filename(path_.filename().string());
    stream_ = std::make_shared<std::ifstream>(path_, std::ios::binary);
    if (!stream_->is_open()) {
        throw std::filesystem::filesystem_error(
            "Cannot open the file to send", path_,
            std::make_error_code(std::errc::no_such_file_or_directory));
    }
}

Frame OutgoingTransfer::header() const {
    return Frame{inline_image_ ? 'G' : 'F', name_ + "|" + std::to_string(size_)};
}

std::optional<Frame> OutgoingTransfer::next() {
    if (finished_) {
        return std::nullopt;
    }
    if (cancelled_) {
        finished_ = true;
        return std::nullopt;
    }

    if (!terminator_sent_) {
        Bytes chunk(chunk_bytes_);
        stream_->read(reinterpret_cast<char*>(chunk.data()),
                      static_cast<std::streamsize>(chunk.size()));
        const auto read = static_cast<std::size_t>(stream_->gcount());
        if (read > 0) {
            chunk.resize(read);
            sent_ += read;
            return Frame{inline_image_ ? 'G' : 'D',
                         encoding::base64_encode(ByteView(chunk))};
        }
        terminator_sent_ = true;
        return Frame{inline_image_ ? 'G' : 'E',
                     std::string(inline_image_ ? kImageEndBody : kFileEndBody)};
    }

    finished_ = true;
    return std::nullopt;
}

void OutgoingTransfer::cancel() { cancelled_ = true; }

Progress OutgoingTransfer::progress(Outcome outcome) const {
    Progress progress;
    progress.name = name_;
    progress.size = size_;
    progress.transferred = sent_;
    progress.direction = Direction::Outgoing;
    progress.inline_image = inline_image_;
    progress.outcome = outcome;
    return progress;
}

IncomingTransfers::IncomingTransfers(Config config, Callbacks callbacks)
    : config_(std::move(config)), callbacks_(std::move(callbacks)) {}

void IncomingTransfers::on_frame(char type, std::string_view body,
                                 std::uint64_t message_id) {
    switch (type) {
        case 'F':
            begin_file(body, message_id);
            return;
        case 'D':
            append_file_chunk(body);
            return;
        case 'E':
            finish_file();
            return;
        case 'G':
            if (body == kImageEndBody) {
                finish_image();
            } else if (!image_) {
                begin_image(body, message_id);
            } else {
                append_image_chunk(body);
            }
            return;
        case 'I':
            if (body == kImageLinesEndBody) {
                finish_image_lines();
            } else {
                append_image_line(body);
            }
            return;
        default:
            return;
    }
}

void IncomingTransfers::on_signal(const protocol::Signal& signal) {
    if (signal.kind != protocol::SignalKind::AbortFile || !file_) {
        return;
    }
    // The reference leaves the partial file behind here. Removing it is
    // deliberate: a truncated file with the right name looks complete, and the
    // sender has already said it will not finish.
    fail_file("Sender cancelled the transfer", /*remove_partial=*/true);
}

void IncomingTransfers::reset() {
    if (file_) {
        file_->stream.close();
        std::error_code ignored;
        std::filesystem::remove(file_->path, ignored);
        file_.reset();
    }
    image_.reset();
    image_lines_.clear();
    image_lines_truncated_ = false;
}

void IncomingTransfers::begin_file(std::string_view body, std::uint64_t message_id) {
    if (file_) {
        // A second offer while one is in flight would otherwise silently
        // abandon a half-written file under a name the user already saw.
        fail_file("A new file offer arrived mid-transfer", /*remove_partial=*/true);
    }

    const auto offer = parse_offer(body);
    if (!offer) {
        emit_error("Invalid file header");
        return;
    }
    const auto& [raw_name, size] = *offer;
    if (size > config_.max_file_size) {
        emit_error("File too large: " + std::to_string(size) + " bytes (max " +
                   std::to_string(config_.max_file_size / (1024 * 1024)) + " MB)");
        return;
    }

    const std::string safe_name = sanitize_filename(raw_name, now());
    std::filesystem::path target;
    try {
        std::error_code ignored;
        std::filesystem::create_directories(config_.downloads_dir, ignored);
        target = allocate_unique_filename(config_.downloads_dir, safe_name);
    } catch (const std::exception& exc) {
        emit_error(std::string("Cannot save the incoming file: ") + exc.what());
        return;
    }
    const std::string final_name = target.filename().string();

    if (callbacks_.accept_file && !callbacks_.accept_file(final_name, size)) {
        send(Frame{'S', protocol::signal_body(protocol::build_reject_file(final_name))});
        emit_system("Incoming file rejected by user: " + final_name);
        return;
    }

    IncomingFile incoming;
    incoming.path = target;
    incoming.name = final_name;
    incoming.size = size;
    incoming.message_id = message_id;
    // Exclusive creation: between allocating the name and opening it, something
    // else may have taken it.
    incoming.stream.open(target, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!incoming.stream.is_open()) {
        emit_error("Cannot open the incoming file for writing: " + final_name);
        return;
    }

    if (final_name != safe_name) {
        emit_system("Filename collision detected: saved as " + final_name);
    }
    emit_system("Receiving file: " + final_name + " (" + std::to_string(size) +
                " bytes)");

    file_ = std::move(incoming);
    report(Progress{final_name, target, size, 0, Direction::Incoming, false,
                    Outcome::Active});
}

void IncomingTransfers::append_file_chunk(std::string_view body) {
    if (!file_) {
        // A chunk with no offer in front of it is not an error the user needs
        // to hear about: it is what a stale frame from an aborted transfer
        // looks like.
        return;
    }

    const std::uint64_t remaining = file_->size - file_->received;
    if (remaining == 0) {
        fail_file("File chunk exceeds declared size", /*remove_partial=*/true);
        return;
    }
    // Checked before decoding, because decoding is where the memory goes.
    if (body.size() > max_base64_chars_for_bytes(remaining)) {
        fail_file("File chunk is too large for the remaining size",
                  /*remove_partial=*/true);
        return;
    }
    const std::optional<Bytes> chunk = encoding::base64_decode(body);
    if (!chunk) {
        fail_file("File chunk is not valid base64", /*remove_partial=*/true);
        return;
    }
    if (chunk->size() > remaining) {
        fail_file("Decoded file chunk exceeds the remaining size",
                  /*remove_partial=*/true);
        return;
    }

    file_->stream.write(reinterpret_cast<const char*>(chunk->data()),
                        static_cast<std::streamsize>(chunk->size()));
    if (!file_->stream) {
        fail_file("Cannot write the incoming file", /*remove_partial=*/true);
        return;
    }
    file_->received += chunk->size();

    if (should_emit_progress(file_->received, chunk->size(), file_->size)) {
        report(Progress{file_->name, file_->path, file_->size, file_->received,
                        Direction::Incoming, false, Outcome::Active});
    }
}

void IncomingTransfers::finish_file() {
    if (!file_) {
        return;
    }
    file_->stream.flush();
    file_->stream.close();

    if (file_->received != file_->size) {
        fail_file("File transfer incomplete: expected " + std::to_string(file_->size) +
                      " bytes, got " + std::to_string(file_->received),
                  /*remove_partial=*/true);
        return;
    }

    const std::filesystem::path path = file_->path;
    const std::string name = file_->name;
    const std::uint64_t size = file_->size;
    const std::uint64_t message_id = file_->message_id;
    file_.reset();

    report(Progress{name, path, size, size, Direction::Incoming, false,
                    Outcome::Completed});
    if (callbacks_.on_file_received) {
        callbacks_.on_file_received(path);
    }
    // The ACK quotes the base name so it matches what the sender offered, not
    // the collision-resolved name it was saved under.
    send(Frame{'S', protocol::signal_body(protocol::build_file_ack(name, message_id))});
}

void IncomingTransfers::fail_file(const std::string& message, bool remove_partial) {
    if (!file_) {
        return;
    }
    file_->stream.close();
    const Progress failed{file_->name,           file_->path, file_->size,
                          file_->received,       Direction::Incoming,
                          false,                 Outcome::Failed};
    if (remove_partial) {
        std::error_code ignored;
        std::filesystem::remove(file_->path, ignored);
    }
    file_.reset();
    emit_error(message);
    report(failed);
}

void IncomingTransfers::begin_image(std::string_view body, std::uint64_t message_id) {
    const auto offer = parse_offer(body);
    if (!offer) {
        emit_error("Invalid image header");
        return;
    }
    const auto& [raw_name, size] = *offer;
    if (size > config_.max_image_size) {
        emit_error("Incoming image too large: " + std::to_string(size) + " bytes (max " +
                   std::to_string(config_.max_image_size / (1024 * 1024)) + " MB)");
        return;
    }

    IncomingImage incoming;
    incoming.name = sanitize_filename(raw_name, now());
    incoming.size = size;
    incoming.message_id = message_id;
    image_ = std::move(incoming);

    emit_system("Receiving image: " + image_->name + " (" + std::to_string(size) +
                " bytes)");
    report(Progress{image_->name, {}, size, 0, Direction::Incoming, true,
                    Outcome::Active});
}

void IncomingTransfers::append_image_chunk(std::string_view body) {
    if (!image_) {
        return;
    }
    const std::uint64_t remaining = image_->size - image_->buffer.size();
    if (remaining == 0) {
        fail_image("Image chunk exceeds declared size");
        return;
    }
    if (body.size() > max_base64_chars_for_bytes(remaining)) {
        fail_image("Image chunk is too large for the remaining size");
        return;
    }
    const std::optional<Bytes> chunk = encoding::base64_decode(body);
    if (!chunk) {
        fail_image("Image chunk is not valid base64");
        return;
    }
    if (chunk->size() > remaining) {
        fail_image("Decoded image chunk exceeds the remaining size");
        return;
    }

    append(image_->buffer, ByteView(*chunk));
    const std::uint64_t received = image_->buffer.size();
    if (received - image_->last_reported >= kImageProgressStep ||
        received == image_->size) {
        image_->last_reported = received;
        report(Progress{image_->name, {}, image_->size, received, Direction::Incoming,
                        true, Outcome::Active});
    }
}

void IncomingTransfers::finish_image() {
    if (!image_) {
        return;
    }
    if (image_->buffer.size() != image_->size) {
        fail_image("Image transfer incomplete: received " +
                   std::to_string(image_->buffer.size()) + " of " +
                   std::to_string(image_->size) + " bytes");
        return;
    }

    // The declared name means nothing: the format is decided by the magic
    // bytes, so a peer cannot get an arbitrary extension written to disk.
    const std::optional<std::string> extension =
        detect_inline_image_format(ByteView(image_->buffer));
    if (!extension) {
        fail_image("Received image has an invalid format");
        return;
    }

    crypto::init();
    const std::string digest =
        encoding::hex_encode(ByteView(crypto::sha256(ByteView(image_->buffer))))
            .substr(0, 8);
    const std::string filename =
        "img_" + std::to_string(now()) + "_" + digest + "." + *extension;
    const std::filesystem::path target = config_.images_dir / filename;

    std::error_code ignored;
    std::filesystem::create_directories(config_.images_dir, ignored);
    std::ofstream out(target, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!out.is_open()) {
        fail_image("Cannot save the received image");
        return;
    }
    out.write(reinterpret_cast<const char*>(image_->buffer.data()),
              static_cast<std::streamsize>(image_->buffer.size()));
    out.flush();
    if (!out) {
        out.close();
        std::filesystem::remove(target, ignored);
        fail_image("Cannot save the received image");
        return;
    }
    out.close();

    const std::string name = image_->name;
    const std::uint64_t size = image_->size;
    const std::uint64_t message_id = image_->message_id;
    image_.reset();

    report(Progress{name, target, size, size, Direction::Incoming, true,
                    Outcome::Completed});
    if (callbacks_.on_image_received) {
        callbacks_.on_image_received(target);
    }
    send(Frame{'S', protocol::signal_body(protocol::build_image_ack(name, message_id))});
}

void IncomingTransfers::fail_image(const std::string& message) {
    if (!image_) {
        return;
    }
    const Progress failed{image_->name,          {},   image_->size,
                          image_->buffer.size(), Direction::Incoming,
                          true,                  Outcome::Failed};
    image_.reset();
    emit_error(message);
    report(failed);
}

void IncomingTransfers::append_image_line(std::string_view body) {
    if (image_lines_.size() < config_.max_image_lines) {
        image_lines_.emplace_back(body);
        return;
    }
    // One truncation notice, not one per surplus line: a peer sending ten
    // thousand lines would otherwise flood the log through the error callback.
    if (!image_lines_truncated_) {
        image_lines_truncated_ = true;
        image_lines_.emplace_back("[Image truncated - too large]");
        emit_error("Image too large, truncating");
    }
}

void IncomingTransfers::finish_image_lines() {
    std::string text;
    for (std::size_t i = 0; i < image_lines_.size(); ++i) {
        if (i > 0) {
            text += "\n";
        }
        text += image_lines_[i];
    }
    image_lines_.clear();
    image_lines_truncated_ = false;
    if (callbacks_.on_image_lines_received) {
        callbacks_.on_image_lines_received(text);
    }
}

void IncomingTransfers::report(const Progress& progress) const {
    if (callbacks_.on_progress) {
        callbacks_.on_progress(progress);
    }
}

void IncomingTransfers::emit_error(const std::string& message) const {
    if (callbacks_.on_error) {
        callbacks_.on_error(message);
    }
}

void IncomingTransfers::emit_system(const std::string& message) const {
    if (callbacks_.on_system) {
        callbacks_.on_system(message);
    }
}

void IncomingTransfers::send(const Frame& frame) const {
    if (callbacks_.send) {
        callbacks_.send(frame);
    }
}

std::int64_t IncomingTransfers::now() const {
    return config_.now_seconds ? config_.now_seconds() : system_seconds();
}

}  // namespace i2pchat::transfer
