#include <catch2/catch_test_macros.hpp>

#include <fstream>
#include <string>
#include <vector>

#include "i2pchat/encoding.hpp"
#include "i2pchat/protocol/signals.hpp"
#include "i2pchat/transfer/files.hpp"
#include "i2pchat/transfer/manager.hpp"
#include "i2pchat/transfer/policy.hpp"
#include "temp_dir.hpp"

using namespace i2pchat;
using i2pchat::testing::TempDir;
using transfer::Direction;
using transfer::FailureReason;
using transfer::Outcome;

namespace {

Bytes pattern(std::size_t size) {
    Bytes data(size);
    for (std::size_t i = 0; i < size; ++i) {
        data[i] = static_cast<Byte>((i * 31 + 7) & 0xFF);
    }
    return data;
}

std::filesystem::path write_file(const TempDir& dir, const std::string& name,
                                 ByteView contents) {
    const std::filesystem::path path = dir.file(name);
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(contents.data()),
              static_cast<std::streamsize>(contents.size()));
    return path;
}

Bytes read_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    return Bytes(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

/// Records everything a receiver reports, so a test can assert on the sequence
/// rather than on one final value.
struct Recorder {
    std::vector<transfer::Frame> sent;
    std::vector<transfer::Progress> progress;
    std::vector<std::string> systems;
    std::vector<std::string> errors;
    std::vector<std::filesystem::path> files;
    std::vector<std::filesystem::path> images;
    std::vector<std::string> image_texts;

    [[nodiscard]] transfer::IncomingTransfers::Callbacks callbacks() {
        transfer::IncomingTransfers::Callbacks callbacks;
        callbacks.send = [this](const transfer::Frame& frame) { sent.push_back(frame); };
        callbacks.on_progress = [this](const transfer::Progress& value) {
            progress.push_back(value);
        };
        callbacks.on_system = [this](const std::string& message) {
            systems.push_back(message);
        };
        callbacks.on_error = [this](const std::string& message) {
            errors.push_back(message);
        };
        callbacks.on_file_received = [this](const std::filesystem::path& path) {
            files.push_back(path);
        };
        callbacks.on_image_received = [this](const std::filesystem::path& path) {
            images.push_back(path);
        };
        callbacks.on_image_lines_received = [this](const std::string& text) {
            image_texts.push_back(text);
        };
        return callbacks;
    }

    [[nodiscard]] bool has_outcome(Outcome outcome) const {
        for (const transfer::Progress& value : progress) {
            if (value.outcome == outcome) {
                return true;
            }
        }
        return false;
    }
};

transfer::IncomingTransfers::Config config_for(const TempDir& dir) {
    transfer::IncomingTransfers::Config config;
    config.downloads_dir = dir.path() / "downloads";
    config.images_dir = dir.path() / "images";
    config.now_seconds = [] { return std::int64_t{1767225600}; };
    return config;
}

/// Hand every frame one side produces to the other, which is the whole transfer
/// flow minus the secure channel.
void deliver(transfer::OutgoingTransfer& sender, transfer::IncomingTransfers& receiver,
             std::uint64_t header_message_id = 11) {
    const transfer::Frame header = sender.header();
    receiver.on_frame(header.type, header.body, header_message_id);
    while (const std::optional<transfer::Frame> frame = sender.next()) {
        receiver.on_frame(frame->type, frame->body);
    }
}

}  // namespace

TEST_CASE("a file is offered, streamed and acknowledged", "[transfer]") {
    TempDir dir;
    const Bytes contents = pattern(9000);
    const std::filesystem::path source = write_file(dir, "report.pdf", ByteView(contents));

    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());
    transfer::OutgoingTransfer sender(source, /*inline_image=*/false, 4096);

    CHECK(sender.header().type == 'F');
    CHECK(sender.header().body == "report.pdf|9000");

    deliver(sender, receiver);

    CHECK(sender.sent() == contents.size());
    REQUIRE(recorder.files.size() == 1);
    CHECK(read_file(recorder.files.front()) == contents);
    CHECK(recorder.files.front().filename() == "report.pdf");
    CHECK(recorder.errors.empty());
    CHECK(recorder.has_outcome(Outcome::Completed));

    REQUIRE(recorder.sent.size() == 1);
    CHECK(recorder.sent.front().type == 'S');
    const protocol::Signal ack = protocol::parse_signal(recorder.sent.front().body);
    CHECK(ack.kind == protocol::SignalKind::FileAck);
    CHECK(ack.name == "report.pdf");
    CHECK(ack.message_id == 11);
}

TEST_CASE("an empty file still completes", "[transfer]") {
    // The E frame arrives with no D frames in front of it, which must not be
    // read as a truncated transfer.
    TempDir dir;
    const std::filesystem::path source = write_file(dir, "empty.txt", ByteView(Bytes{}));

    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());
    transfer::OutgoingTransfer sender(source);
    deliver(sender, receiver);

    REQUIRE(recorder.files.size() == 1);
    CHECK(std::filesystem::file_size(recorder.files.front()) == 0);
    CHECK(recorder.errors.empty());
}

TEST_CASE("a declined offer is answered and nothing is written", "[transfer]") {
    TempDir dir;
    const std::filesystem::path source = write_file(dir, "report.pdf", ByteView(pattern(100)));

    Recorder recorder;
    auto callbacks = recorder.callbacks();
    callbacks.accept_file = [](const std::string&, std::uint64_t) { return false; };
    transfer::IncomingTransfers receiver(config_for(dir), std::move(callbacks));
    transfer::OutgoingTransfer sender(source);

    deliver(sender, receiver);

    CHECK_FALSE(receiver.receiving_file());
    CHECK(recorder.files.empty());
    REQUIRE(recorder.sent.size() == 1);
    const protocol::Signal reject = protocol::parse_signal(recorder.sent.front().body);
    CHECK(reject.kind == protocol::SignalKind::RejectFile);
    CHECK(reject.name == "report.pdf");
    CHECK_FALSE(std::filesystem::exists(dir.path() / "downloads" / "report.pdf"));
}

TEST_CASE("an incoming file never overwrites one already there", "[transfer]") {
    // Otherwise a peer could replace a file the user has simply by naming a
    // transfer after it.
    TempDir dir;
    const auto config = config_for(dir);
    std::filesystem::create_directories(config.downloads_dir);
    const Bytes existing = pattern(50);
    std::ofstream(config.downloads_dir / "report.pdf", std::ios::binary)
        .write(reinterpret_cast<const char*>(existing.data()),
               static_cast<std::streamsize>(existing.size()));

    const Bytes contents = pattern(200);
    const std::filesystem::path source = write_file(dir, "report.pdf", ByteView(contents));

    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());
    transfer::OutgoingTransfer sender(source);
    deliver(sender, receiver);

    REQUIRE(recorder.files.size() == 1);
    CHECK(recorder.files.front().filename() == "report (1).pdf");
    CHECK(read_file(config.downloads_dir / "report.pdf") == existing);
    CHECK(read_file(recorder.files.front()) == contents);

    bool announced = false;
    for (const std::string& message : recorder.systems) {
        announced = announced || message.find("collision") != std::string::npos;
    }
    CHECK(announced);
}

TEST_CASE("a peer-supplied path cannot escape the downloads directory",
          "[transfer]") {
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "../../etc/passwd|4", 1);
    receiver.on_frame('D', encoding::base64_encode(as_bytes("data")));
    receiver.on_frame('E', "");

    REQUIRE(recorder.files.size() == 1);
    CHECK(recorder.files.front().parent_path() == config.downloads_dir);
    CHECK(recorder.files.front().filename() == "passwd");
    CHECK_FALSE(std::filesystem::exists(dir.path() / "etc" / "passwd"));
}

TEST_CASE("a file larger than the limit is refused before anything is written",
          "[transfer]") {
    TempDir dir;
    auto config = config_for(dir);
    config.max_file_size = 1024;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "huge.bin|4096", 1);
    CHECK_FALSE(receiver.receiving_file());
    REQUIRE(recorder.errors.size() == 1);
    CHECK(recorder.errors.front().find("too large") != std::string::npos);
}

TEST_CASE("a malformed offer is reported and ignored", "[transfer]") {
    TempDir dir;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());

    receiver.on_frame('F', "no-size-here", 1);
    receiver.on_frame('F', "name|notanumber", 2);
    receiver.on_frame('F', "name|10|extra", 3);
    CHECK_FALSE(receiver.receiving_file());
    CHECK(recorder.errors.size() == 3);
}

TEST_CASE("a sender that claims less than it sends is cut off", "[transfer]") {
    // The declared size bounds how much is written to disk; without that a peer
    // could fill the user's disk from a small-looking offer.
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "small.bin|4", 1);
    receiver.on_frame('D', encoding::base64_encode(ByteView(pattern(4096))));

    CHECK_FALSE(receiver.receiving_file());
    CHECK(recorder.has_outcome(Outcome::Failed));
    REQUIRE_FALSE(recorder.errors.empty());
    // The partial file is removed rather than left looking like a real one.
    CHECK(std::filesystem::is_empty(config.downloads_dir));
}

TEST_CASE("a chunk that is not base64 aborts the transfer", "[transfer]") {
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "small.bin|64", 1);
    receiver.on_frame('D', "!!! not base64 !!!");

    CHECK_FALSE(receiver.receiving_file());
    CHECK(recorder.has_outcome(Outcome::Failed));
    CHECK(std::filesystem::is_empty(config.downloads_dir));
}

TEST_CASE("a transfer that ends short of its declared size fails", "[transfer]") {
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "truncated.bin|1000", 1);
    receiver.on_frame('D', encoding::base64_encode(ByteView(pattern(100))));
    receiver.on_frame('E', "");

    CHECK(recorder.files.empty());
    CHECK(recorder.has_outcome(Outcome::Failed));
    CHECK(std::filesystem::is_empty(config.downloads_dir));
    CHECK(recorder.sent.empty());
}

TEST_CASE("a sender abandoning a transfer discards the partial file",
          "[transfer]") {
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('F', "aborted.bin|1000", 1);
    receiver.on_frame('D', encoding::base64_encode(ByteView(pattern(100))));
    receiver.on_signal(protocol::parse_signal(
        protocol::signal_body(protocol::build_abort_file())));

    CHECK_FALSE(receiver.receiving_file());
    CHECK(recorder.has_outcome(Outcome::Failed));
    CHECK(std::filesystem::is_empty(config.downloads_dir));
}

TEST_CASE("chunks arriving with no offer are ignored", "[transfer]") {
    // These are what the tail of an aborted transfer looks like; reporting each
    // one would flood the user with errors for something already handled.
    TempDir dir;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());

    receiver.on_frame('D', encoding::base64_encode(as_bytes("orphan")));
    receiver.on_frame('E', "");
    CHECK(recorder.errors.empty());
    CHECK(recorder.progress.empty());
}

TEST_CASE("a sender can stop midway", "[transfer]") {
    TempDir dir;
    const std::filesystem::path source = write_file(dir, "big.bin", ByteView(pattern(20000)));
    transfer::OutgoingTransfer sender(source, false, 4096);

    CHECK(sender.next().has_value());
    sender.cancel();
    CHECK_FALSE(sender.next().has_value());
    CHECK(sender.finished());
    CHECK(sender.cancelled());
    CHECK(sender.sent() == 4096);
}

TEST_CASE("an inline image is transferred, sniffed and saved", "[transfer]") {
    TempDir dir;
    // A minimal PNG signature followed by filler: the format is decided by the
    // magic bytes, not by the name the peer chose.
    Bytes png{0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    append(png, ByteView(pattern(500)));
    const std::filesystem::path source = write_file(dir, "photo.png", ByteView(png));

    Recorder recorder;
    const auto config = config_for(dir);
    transfer::IncomingTransfers receiver(config, recorder.callbacks());
    transfer::OutgoingTransfer sender(source, /*inline_image=*/true, 4096);

    CHECK(sender.header().type == 'G');
    deliver(sender, receiver, /*header_message_id=*/21);

    REQUIRE(recorder.images.size() == 1);
    CHECK(read_file(recorder.images.front()) == png);
    CHECK(recorder.images.front().parent_path() == config.images_dir);
    // The saved name is generated from the content, not taken from the peer.
    CHECK(recorder.images.front().filename().string().starts_with("img_1767225600_"));
    CHECK(recorder.images.front().extension() == ".png");

    REQUIRE(recorder.sent.size() == 1);
    const protocol::Signal ack = protocol::parse_signal(recorder.sent.front().body);
    CHECK(ack.kind == protocol::SignalKind::ImgAck);
    CHECK(ack.name == "photo.png");
    CHECK(ack.message_id == 21);
}

TEST_CASE("an image that is not an image is discarded", "[transfer]") {
    TempDir dir;
    const auto config = config_for(dir);
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    const Bytes payload = to_bytes("MZ\x90 this is an executable, not a picture");
    receiver.on_frame('G', "photo.png|" + std::to_string(payload.size()), 1);
    receiver.on_frame('G', encoding::base64_encode(ByteView(payload)));
    receiver.on_frame('G', "__IMG_END__");

    CHECK(recorder.images.empty());
    CHECK(recorder.has_outcome(Outcome::Failed));
    REQUIRE_FALSE(recorder.errors.empty());
    CHECK(recorder.errors.back().find("invalid format") != std::string::npos);
}

TEST_CASE("an image larger than the limit is refused", "[transfer]") {
    TempDir dir;
    auto config = config_for(dir);
    config.max_image_size = 1024;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    receiver.on_frame('G', "photo.png|4096", 1);
    CHECK_FALSE(receiver.receiving_image());
    REQUIRE(recorder.errors.size() == 1);
    CHECK(recorder.errors.front().find("too large") != std::string::npos);
}

TEST_CASE("an image sender that overruns its declared size is cut off",
          "[transfer]") {
    TempDir dir;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());

    receiver.on_frame('G', "photo.png|4", 1);
    receiver.on_frame('G', encoding::base64_encode(ByteView(pattern(4096))));

    CHECK_FALSE(receiver.receiving_image());
    CHECK(recorder.has_outcome(Outcome::Failed));
    CHECK(recorder.images.empty());
}

TEST_CASE("a rendered image is reassembled line by line", "[transfer]") {
    TempDir dir;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());

    receiver.on_frame('I', "⠁⠂⠄");
    receiver.on_frame('I', "⠈⠐⠠");
    receiver.on_frame('I', "__END__");

    REQUIRE(recorder.image_texts.size() == 1);
    CHECK(recorder.image_texts.front() == "⠁⠂⠄\n⠈⠐⠠");
}

TEST_CASE("a rendered image is truncated instead of buffered without bound",
          "[transfer]") {
    TempDir dir;
    auto config = config_for(dir);
    config.max_image_lines = 3;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config, recorder.callbacks());

    for (int i = 0; i < 10; ++i) {
        receiver.on_frame('I', "line " + std::to_string(i));
    }
    receiver.on_frame('I', "__END__");

    REQUIRE(recorder.image_texts.size() == 1);
    const std::string& text = recorder.image_texts.front();
    CHECK(text.starts_with("line 0\nline 1\nline 2\n"));
    CHECK(text.find("[Image truncated - too large]") != std::string::npos);
    // One notice, not one per surplus line.
    CHECK(recorder.errors.size() == 1);
}

TEST_CASE("a second rendered image starts from an empty buffer", "[transfer]") {
    TempDir dir;
    Recorder recorder;
    transfer::IncomingTransfers receiver(config_for(dir), recorder.callbacks());

    receiver.on_frame('I', "first");
    receiver.on_frame('I', "__END__");
    receiver.on_frame('I', "second");
    receiver.on_frame('I', "__END__");

    REQUIRE(recorder.image_texts.size() == 2);
    CHECK(recorder.image_texts[1] == "second");
}

TEST_CASE("progress is reported often for small files and sparsely for large ones",
          "[transfer]") {
    // A repaint per 4 KiB chunk of a gigabyte costs more than the transfer.
    CHECK(transfer::should_emit_progress(4096, 4096, 8192));
    CHECK(transfer::should_emit_progress(8192, 4096, 8192));
    CHECK(transfer::should_emit_progress(4096, 4096, 10 * 1024 * 1024));
    CHECK_FALSE(transfer::should_emit_progress(12288, 4096, 10 * 1024 * 1024));
    CHECK(transfer::should_emit_progress(65536, 4096, 10 * 1024 * 1024));
    // Nothing is known about the total, so every step is worth reporting.
    CHECK(transfer::should_emit_progress(1, 1, 0));
}

TEST_CASE("only transient failures are retried", "[transfer]") {
    const transfer::RetryPolicy policy;

    CHECK(transfer::should_retry(1, FailureReason::ConnectionLost, policy).retry);
    CHECK(transfer::should_retry(1, FailureReason::ConnectionLost, policy).delay ==
          std::chrono::milliseconds(2000));
    CHECK(transfer::should_retry(2, FailureReason::Timeout, policy).delay ==
          std::chrono::milliseconds(4000));
    CHECK(transfer::should_retry(3, FailureReason::PeerBusy, policy).delay ==
          std::chrono::milliseconds(8000));
    // Past the allowance, the transfer is left failed rather than retried
    // forever.
    CHECK_FALSE(transfer::should_retry(4, FailureReason::ConnectionLost, policy).retry);

    // Retrying these would be pestering: the answer will not change.
    CHECK_FALSE(transfer::should_retry(1, FailureReason::PeerRejected, policy).retry);
    CHECK_FALSE(transfer::should_retry(1, FailureReason::FileNotFound, policy).retry);
    CHECK_FALSE(transfer::should_retry(1, FailureReason::SizeExceeded, policy).retry);
    CHECK_FALSE(transfer::should_retry(1, FailureReason::UserCancelled, policy).retry);
    CHECK_FALSE(transfer::should_retry(1, FailureReason::Unknown, policy).retry);
}

TEST_CASE("the backoff is capped", "[transfer]") {
    transfer::RetryPolicy policy;
    policy.max_retries = 10;
    policy.max_backoff = std::chrono::milliseconds(30000);
    CHECK(transfer::should_retry(8, FailureReason::Timeout, policy).delay ==
          std::chrono::milliseconds(30000));
}

TEST_CASE("failure reasons keep their wire spelling", "[transfer]") {
    CHECK(transfer::reason_key(FailureReason::ConnectionLost) == "connection_lost");
    CHECK(transfer::parse_reason("peer_rejected") == FailureReason::PeerRejected);
    CHECK(transfer::parse_reason("something_new") == FailureReason::Unknown);
    CHECK(transfer::failure_message("timeout") == "Transfer timed out — will retry");
    CHECK(transfer::failure_message("disk_on_fire") == "Transfer failed: disk_on_fire");
}

TEST_CASE("transfer states and speeds are labelled", "[transfer]") {
    CHECK(transfer::state_label(transfer::State::Sending) == "Sending");
    CHECK(transfer::state_key(transfer::State::Completed) == "completed");
    CHECK(transfer::progress_percent(50, 200) == 25.0);
    CHECK(transfer::progress_percent(1, 0) == 0.0);
    CHECK(transfer::progress_percent(300, 200) == 100.0);
    CHECK(transfer::speed_label(512) == "512 B/s");
    CHECK(transfer::speed_label(2048) == "2.0 KB/s");
    CHECK(transfer::speed_label(3 * 1024 * 1024) == "3.0 MB/s");
    CHECK(transfer::speed_label(-1).empty());
    CHECK(transfer::timed_out(std::chrono::milliseconds(60000), 0));
    CHECK_FALSE(transfer::timed_out(std::chrono::milliseconds(60000), 1));
    CHECK_FALSE(transfer::timed_out(std::chrono::milliseconds(5000), 0));
}

TEST_CASE("peer-supplied filenames are reduced to something safe", "[transfer]") {
    const std::int64_t stamp = 1767225600;
    CHECK(transfer::sanitize_filename("report.pdf", stamp) == "report.pdf");
    CHECK(transfer::sanitize_filename("/etc/passwd", stamp) == "passwd");
    CHECK(transfer::sanitize_filename("C:\\Windows\\evil.exe", stamp) == "evil.exe");
    CHECK(transfer::sanitize_filename("a<b>c:d\"e|f?g*h", stamp) == "a_b_c_d_e_f_g_h");
    CHECK(transfer::sanitize_filename(std::string("bell\x07.txt"), stamp) == "bell_.txt");
    // Unicode is not a security problem, and mangling it would be a bug.
    CHECK(transfer::sanitize_filename("отчёт 漢字.pdf", stamp) == "отчёт 漢字.pdf");
    // A hidden name is replaced rather than unhidden.
    CHECK(transfer::sanitize_filename(".bashrc", stamp) == "file_1767225600");
    CHECK(transfer::sanitize_filename("   ", stamp) == "file_1767225600");
    CHECK(transfer::sanitize_filename(std::string(300, 'x') + ".txt", stamp) ==
          "file_1767225600.txt");
}

TEST_CASE("a unique name is allocated without overwriting", "[transfer]") {
    TempDir dir;
    CHECK(transfer::allocate_unique_filename(dir.path(), "a.txt") == dir.file("a.txt"));
    std::ofstream(dir.file("a.txt")) << "x";
    CHECK(transfer::allocate_unique_filename(dir.path(), "a.txt") ==
          dir.file("a (1).txt"));
    std::ofstream(dir.file("a (1).txt")) << "x";
    CHECK(transfer::allocate_unique_filename(dir.path(), "a.txt") ==
          dir.file("a (2).txt"));

    std::ofstream(dir.file("noext")) << "x";
    CHECK(transfer::allocate_unique_filename(dir.path(), "noext") ==
          dir.file("noext (1)"));
}

TEST_CASE("the base64 bound is tight enough to reject an oversized chunk",
          "[transfer]") {
    CHECK(transfer::max_base64_chars_for_bytes(0) == 0);
    CHECK(transfer::max_base64_chars_for_bytes(1) == 4);
    CHECK(transfer::max_base64_chars_for_bytes(3) == 4);
    CHECK(transfer::max_base64_chars_for_bytes(4) == 8);
    CHECK(transfer::max_base64_chars_for_bytes(4096) ==
          encoding::base64_encode(ByteView(pattern(4096))).size());
}

TEST_CASE("image formats are recognised from their magic bytes", "[transfer]") {
    const Bytes png{0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    CHECK(transfer::detect_inline_image_format(ByteView(png)) == "png");

    const Bytes jpeg{0xFF, 0xD8, 0xFF, 0xE0};
    CHECK(transfer::detect_inline_image_format(ByteView(jpeg)) == "jpeg");

    Bytes webp = to_bytes("RIFF");
    append(webp, ByteView(Bytes{0x10, 0x00, 0x00, 0x00}));
    append(webp, as_bytes("WEBP"));
    CHECK(transfer::detect_inline_image_format(ByteView(webp)) == "webp");

    CHECK_FALSE(transfer::detect_inline_image_format(ByteView(Bytes{})).has_value());
    CHECK_FALSE(
        transfer::detect_inline_image_format(ByteView(to_bytes("GIF89a"))).has_value());
    // A prefix that is too short to be conclusive is not accepted.
    CHECK_FALSE(transfer::detect_inline_image_format(ByteView(Bytes{0x89, 'P'})).has_value());
}
