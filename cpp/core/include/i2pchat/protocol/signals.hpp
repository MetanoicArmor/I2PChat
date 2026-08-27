#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

#include "i2pchat/bytes.hpp"

/// Control signals, which the protocol carries as text inside `S` frames
/// rather than as frame types of their own.
///
/// A signal body is the literal `__SIGNAL__:` followed by a `|`-separated
/// payload. They move protocol state — delivery confirmations, transfer aborts,
/// BlindBox root rotations — so they are only honoured on the authenticated
/// secure channel. The one exception is `QUIT`: a peer hanging up before the
/// handshake finished has nothing to gain by lying about it, and refusing to
/// hear it would leave the connection waiting for a timeout.
namespace i2pchat::protocol {

inline constexpr std::string_view kSignalPrefix = "__SIGNAL__:";

enum class SignalKind {
    /// Not a signal, or one this version does not know.
    Unknown,
    /// Text message delivered: `MSG_ACK|<msg_id>`.
    MsgAck,
    /// File received in full: `FILE_ACK|<basename>|<msg_id>`.
    FileAck,
    /// Inline image received in full: `IMG_ACK|<filename>|<msg_id>`.
    ImgAck,
    /// The receiver declined an offered file: `REJECT_FILE|<filename>`.
    RejectFile,
    /// Either side abandoned the transfer in progress: `ABORT_FILE`.
    AbortFile,
    /// Graceful disconnect: `QUIT`.
    Quit,
    /// New pairwise BlindBox root: `BLINDBOX_ROOT|<epoch>|<root_hex>`.
    BlindBoxRoot,
    /// `BLINDBOX_ROOT_ACK|<epoch>`.
    BlindBoxRootAck,
    /// `GROUP_BLINDBOX_ROOT|<group_id>|<group_epoch>|<root_epoch>|<root_hex>`.
    GroupBlindBoxRoot,
    /// `GROUP_BLINDBOX_ROOT_ACK|<group_id>|<group_epoch>|<root_epoch>`.
    GroupBlindBoxRootAck,
};

struct Signal {
    SignalKind kind = SignalKind::Unknown;
    /// The payload after the prefix, trimmed. Kept for logging and for the
    /// signals this version does not recognise.
    std::string payload;
    /// False when the kind was recognised but its fields did not parse. Such a
    /// signal must be dropped, not acted on with default values.
    bool well_formed = false;

    /// File or image name for the ACK and reject signals.
    std::string name;
    /// Group id for the group BlindBox signals.
    std::string group_id;
    /// The message id an ACK refers to.
    std::uint64_t message_id = 0;
    /// Root epoch for the pairwise signals, group epoch for the group ones.
    std::uint64_t epoch = 0;
    /// Root epoch inside a group signal.
    std::uint64_t root_epoch = 0;
    /// 32 bytes, for the root-carrying signals.
    Bytes root_secret;
};

/// The `S` frame body for `payload`.
[[nodiscard]] std::string signal_body(std::string_view payload);

/// The payload of a signal body, or nothing when the body is not a signal.
[[nodiscard]] std::optional<std::string> signal_payload(std::string_view body);

/// Classify a frame body. Bodies that are not signals come back as `Unknown`
/// with `well_formed` false, which is also how an `S` frame carrying a peer
/// destination looks.
[[nodiscard]] Signal parse_signal(std::string_view body);

/// Whether a signal may be acted on before the secure channel exists.
[[nodiscard]] bool honoured_before_handshake(const Signal& signal);

[[nodiscard]] std::string build_msg_ack(std::uint64_t message_id);
[[nodiscard]] std::string build_file_ack(std::string_view filename,
                                         std::uint64_t message_id);
[[nodiscard]] std::string build_image_ack(std::string_view filename,
                                          std::uint64_t message_id);
[[nodiscard]] std::string build_reject_file(std::string_view filename);
[[nodiscard]] std::string build_abort_file();
[[nodiscard]] std::string build_quit();
[[nodiscard]] std::string build_blindbox_root(std::uint64_t epoch, ByteView root_secret);
[[nodiscard]] std::string build_blindbox_root_ack(std::uint64_t epoch);
[[nodiscard]] std::string build_group_blindbox_root(std::string_view group_id,
                                                    std::uint64_t group_epoch,
                                                    std::uint64_t root_epoch,
                                                    ByteView root_secret);
[[nodiscard]] std::string build_group_blindbox_root_ack(std::string_view group_id,
                                                        std::uint64_t group_epoch,
                                                        std::uint64_t root_epoch);

}  // namespace i2pchat::protocol
