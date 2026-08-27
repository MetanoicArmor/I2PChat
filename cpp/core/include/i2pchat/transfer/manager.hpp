#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "i2pchat/bytes.hpp"
#include "i2pchat/protocol/signals.hpp"
#include "i2pchat/transfer/files.hpp"
#include "i2pchat/transfer/policy.hpp"

/// File and image transfers over the live secure channel.
///
/// The wire flow is unchanged from the reference:
///
///   file:  F "<name>|<size>"  then D "<base64 chunk>"…  then E ""
///          the receiver answers with the FILE_ACK signal
///   image: G "<name>|<size>"  then G "<base64 chunk>"…  then G "__IMG_END__"
///          the receiver answers with the IMG_ACK signal
///   text-rendered image: I "<line>"… then I "__END__"
///
/// Both directions are expressed as plain state machines over frames rather
/// than as coroutines that own a socket. The transport already exists in
/// `PeerSession`, and keeping transfers independent of it means the awkward
/// parts — a chunk larger than the declared size, a peer aborting mid-file, a
/// filename that collides — are testable without a network.
namespace i2pchat::transfer {

/// A frame to write on the secure channel: the vNext type letter and the body.
struct Frame {
    char type = 'D';
    std::string body;
};

enum class Direction { Incoming, Outgoing };

enum class Outcome {
    /// Bytes are still moving.
    Active,
    Completed,
    Failed,
};

struct Progress {
    std::string name;
    /// Where an incoming file is being written. Empty for outgoing transfers
    /// and for images, which are only named once validated.
    std::filesystem::path path;
    std::uint64_t size = 0;
    std::uint64_t transferred = 0;
    Direction direction = Direction::Incoming;
    bool inline_image = false;
    Outcome outcome = Outcome::Active;
};

/// Streams one local file out as frames.
///
/// The caller decides when to write and when to flush, which is what lets a
/// sender interleave keepalives and honour an abort from the receiver without
/// this class knowing anything about sockets.
class OutgoingTransfer {
public:
    /// `inline_image` selects the image flow: G frames throughout and an
    /// `__IMG_END__` terminator instead of an E frame.
    ///
    /// Throws `std::filesystem::filesystem_error` when the file cannot be read
    /// and `std::length_error` when it is over the size limit for its kind.
    OutgoingTransfer(std::filesystem::path path, bool inline_image = false,
                     std::size_t chunk_bytes = 4096);

    /// The offer frame. Must be sent before any chunk, and its message id is
    /// what the peer's ACK will refer to.
    [[nodiscard]] Frame header() const;

    /// The next chunk or terminator frame, or nothing once the terminator has
    /// been produced.
    [[nodiscard]] std::optional<Frame> next();

    /// Stop producing chunks. The caller is expected to send an ABORT_FILE
    /// signal; `next` will report the transfer as finished from here on.
    void cancel();

    [[nodiscard]] const std::string& name() const noexcept { return name_; }
    [[nodiscard]] std::uint64_t size() const noexcept { return size_; }
    [[nodiscard]] std::uint64_t sent() const noexcept { return sent_; }
    [[nodiscard]] bool finished() const noexcept { return finished_; }
    [[nodiscard]] bool cancelled() const noexcept { return cancelled_; }
    [[nodiscard]] Progress progress(Outcome outcome = Outcome::Active) const;

private:
    std::filesystem::path path_;
    std::string name_;
    bool inline_image_ = false;
    std::size_t chunk_bytes_ = 4096;
    std::uint64_t size_ = 0;
    std::uint64_t sent_ = 0;
    bool finished_ = false;
    bool cancelled_ = false;
    bool terminator_sent_ = false;
    std::shared_ptr<std::ifstream> stream_;
};

/// Receives the file, image and rendered-image flows for one peer.
class IncomingTransfers {
public:
    struct Config {
        std::filesystem::path downloads_dir;
        std::filesystem::path images_dir;
        std::uint64_t max_file_size = kMaxFileSize;
        std::uint64_t max_image_size = kMaxImageSize;
        std::size_t max_image_lines = kMaxImageLines;
        /// Injectable so generated image names are reproducible in tests.
        std::function<std::int64_t()> now_seconds;
    };

    struct Callbacks {
        /// Decides whether to accept an offered file. `name` is the name it
        /// would be saved under, after sanitising and collision resolution.
        /// Declining sends REJECT_FILE. Unset means accept.
        std::function<bool(const std::string& name, std::uint64_t size)> accept_file;
        std::function<void(const Frame&)> send;
        std::function<void(const Progress&)> on_progress;
        std::function<void(const std::string& message)> on_system;
        std::function<void(const std::string& message)> on_error;
        std::function<void(const std::filesystem::path& path)> on_file_received;
        std::function<void(const std::filesystem::path& path)> on_image_received;
        /// A text-rendered image, lines already joined by newlines.
        std::function<void(const std::string& text)> on_image_lines_received;
    };

    IncomingTransfers(Config config, Callbacks callbacks);

    /// Feed one decrypted frame. `message_id` is the frame's id, which the
    /// completion ACK has to quote so the sender can match it to its offer.
    ///
    /// Frame types this class does not handle are ignored, so a caller can pass
    /// everything it receives.
    void on_frame(char type, std::string_view body, std::uint64_t message_id = 0);

    /// Apply a control signal. Only ABORT_FILE is acted on; the rest belong to
    /// the sending side.
    void on_signal(const protocol::Signal& signal);

    /// Abandon whatever is in flight, for a connection that dropped.
    void reset();

    [[nodiscard]] bool receiving_file() const noexcept { return file_.has_value(); }
    [[nodiscard]] bool receiving_image() const noexcept { return image_.has_value(); }

private:
    struct IncomingFile {
        std::filesystem::path path;
        std::string name;
        std::uint64_t size = 0;
        std::uint64_t received = 0;
        std::uint64_t message_id = 0;
        std::ofstream stream;
    };

    struct IncomingImage {
        std::string name;
        std::uint64_t size = 0;
        std::uint64_t message_id = 0;
        Bytes buffer;
        std::uint64_t last_reported = 0;
    };

    void begin_file(std::string_view body, std::uint64_t message_id);
    void append_file_chunk(std::string_view body);
    void finish_file();
    void fail_file(const std::string& message, bool remove_partial);

    void begin_image(std::string_view body, std::uint64_t message_id);
    void append_image_chunk(std::string_view body);
    void finish_image();
    void fail_image(const std::string& message);

    void append_image_line(std::string_view body);
    void finish_image_lines();

    void report(const Progress& progress) const;
    void emit_error(const std::string& message) const;
    void emit_system(const std::string& message) const;
    void send(const Frame& frame) const;
    [[nodiscard]] std::int64_t now() const;

    Config config_;
    Callbacks callbacks_;
    std::optional<IncomingFile> file_;
    std::optional<IncomingImage> image_;
    std::vector<std::string> image_lines_;
    bool image_lines_truncated_ = false;
};

}  // namespace i2pchat::transfer
