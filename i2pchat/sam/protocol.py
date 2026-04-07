from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import ProtocolError, map_result_to_error


SENSITIVE_SAM_KEYS = {"PRIV", "PRIVATE", "DESTINATION", "SIGNING_PRIVATE_KEY"}
TRANSIENT_DESTINATION = "TRANSIENT"


@dataclass(frozen=True, slots=True)
class SAMReply:
    command: str
    topic: str
    fields: dict[str, str]
    raw_line: str


def _validate_sam_token(value: object, *, field_name: str, allow_equals: bool) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"SAM {field_name} is empty")
    if any(ch in token for ch in ("\r", "\n", "\x00", " ", "\t")):
        raise ValueError(f"SAM {field_name} contains forbidden characters")
    if not allow_equals and "=" in token:
        raise ValueError(f"SAM {field_name} contains forbidden characters")
    return token


def _validate_session_id(session_id: object) -> str:
    return _validate_sam_token(session_id, field_name="ID", allow_equals=False)


def _validate_boolish_flag(value: object, *, field_name: str) -> str:
    token = _validate_sam_token(value, field_name=field_name, allow_equals=False).lower()
    if token not in {"true", "false"}:
        raise ValueError(f"SAM {field_name} is invalid")
    return token


def _validate_port(port: object) -> int:
    if type(port) is not int:
        raise ValueError("SAM PORT must be an int")
    if port < 1 or port > 65535:
        raise ValueError("SAM PORT is out of range")
    return port


def _validate_sam_options(options: object) -> str:
    text = str(options or "").strip()
    if any(ch in text for ch in ("\r", "\n", "\x00")):
        raise ValueError("SAM OPTIONS contains forbidden characters")
    return text


def _session_options_dict_to_tokens(
    options: dict[str, str],
    *,
    sig_type: int | None,
) -> str:
    """Space-joined name=value tokens for SESSION CREATE (after DESTINATION)."""
    bits: list[str] = []
    if sig_type is not None:
        bits.append(f"SIGNATURE_TYPE={int(sig_type)}")
    if options:
        for raw_key, raw_val in options.items():
            for label, raw in (("OPTION_KEY", raw_key), ("OPTION_VALUE", raw_val)):
                s = str(raw or "")
                if any(ch in s for ch in ("\r", "\n", "\x00")):
                    raise ValueError(f"SAM {label} contains forbidden characters")
            key_s = _validate_sam_token(
                raw_key, field_name="OPTION_KEY", allow_equals=False
            )
            val_s = _validate_sam_token(
                raw_val, field_name="OPTION_VALUE", allow_equals=False
            )
            bits.append(f"{key_s}={val_s}")
    return " ".join(bits)


def _validate_sam_style(style: object) -> str:
    token = _validate_sam_token(style, field_name="STYLE", allow_equals=False).upper()
    if token not in {"STREAM", "DATAGRAM", "RAW"}:
        raise ValueError("SAM STYLE is invalid")
    return token


def _validate_sam_destination_token(destination: object) -> str:
    token = _validate_sam_token(
        destination, field_name="DESTINATION", allow_equals=True
    )
    if token == TRANSIENT_DESTINATION:
        return token
    return token


def _validate_sam_version(version: object, *, field_name: str) -> str:
    token = _validate_sam_token(version, field_name=field_name, allow_equals=False)
    if not re.fullmatch(r"\d+\.\d+", token):
        raise ValueError(f"SAM {field_name} version is invalid")
    return token


def build_hello(min_version: str = "3.0", max_version: str = "3.2") -> bytes:
    min_v = _validate_sam_version(min_version, field_name="MIN")
    max_v = _validate_sam_version(max_version, field_name="MAX")
    return f"HELLO VERSION MIN={min_v} MAX={max_v}\n".encode("ascii")


def build_dest_generate(sig_type: int = 7) -> bytes:
    if type(sig_type) is not int:
        raise ValueError("SAM SIGNATURE_TYPE must be an int")
    return f"DEST GENERATE SIGNATURE_TYPE={sig_type}\n".encode("ascii")


def build_naming_lookup(name: str) -> bytes:
    safe_name = _validate_sam_token(name, field_name="NAME", allow_equals=False)
    return f"NAMING LOOKUP NAME={safe_name}\n".encode("ascii")


def build_session_create(
    style: str,
    session_id: str,
    destination: str,
    option_string: str = "",
    *,
    sig_type: int | None = None,
    options: dict[str, str] | None = None,
) -> bytes:
    if option_string:
        if sig_type is not None or options:
            raise ValueError(
                "option_string is mutually exclusive with sig_type/options"
            )
        style_s = _validate_sam_style(style)
        session_s = _validate_session_id(session_id)
        dest_s = _validate_sam_destination_token(destination)
        opts_s = _validate_sam_options(option_string)
        return (
            f"SESSION CREATE STYLE={style_s} ID={session_s} "
            f"DESTINATION={dest_s} {opts_s}\n"
        ).encode("ascii")
    # SAM v3: I2CP/streaming options are plain name=value tokens after DESTINATION
    # (see geti2p SAM spec "[option=value]*"). Do not insert a standalone "OPTION"
    # keyword — SAM routers expect space-separated name=value pairs only.
    option_string = _session_options_dict_to_tokens(options or {}, sig_type=sig_type)
    style_s = _validate_sam_style(style)
    session_s = _validate_session_id(session_id)
    dest_s = _validate_sam_destination_token(destination)
    opts_s = _validate_sam_options(option_string)
    return (
        f"SESSION CREATE STYLE={style_s} ID={session_s} "
        f"DESTINATION={dest_s} {opts_s}\n"
    ).encode("ascii")


def build_stream_connect(
    session_id: str,
    destination: str,
    *,
    silent: str = "false",
) -> bytes:
    sid = _validate_session_id(session_id)
    dest = _validate_sam_token(
        destination, field_name="DESTINATION", allow_equals=True
    )
    silent_flag = _validate_boolish_flag(silent, field_name="SILENT")
    return (
        f"STREAM CONNECT ID={sid} DESTINATION={dest} SILENT={silent_flag}\n"
    ).encode("ascii")


def build_stream_accept(session_id: str) -> bytes:
    sid = _validate_session_id(session_id)
    return f"STREAM ACCEPT ID={sid} SILENT=false\n".encode("ascii")


def build_stream_forward(session_id: str, port: int) -> bytes:
    sid = _validate_session_id(session_id)
    safe_port = _validate_port(port)
    return f"STREAM FORWARD ID={sid} PORT={safe_port} \n".encode("ascii")


def parse_reply_line(line: bytes) -> SAMReply:
    raw = line.decode("utf-8", errors="replace").strip()
    if not raw:
        raise ProtocolError(message="Empty SAM reply", raw_line="")
    parts = raw.split()
    if len(parts) < 2:
        raise ProtocolError(message="Malformed SAM reply", raw_line=raw)
    command = parts[0].upper()
    topic = parts[1].upper()
    fields: dict[str, str] = {}
    for token in parts[2:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key.upper()] = value
    return SAMReply(command=command, topic=topic, fields=fields, raw_line=raw)


def expect_ok(reply: SAMReply, *, result_key: str = "RESULT") -> SAMReply:
    result = reply.fields.get(result_key, "").upper()
    if result == "OK":
        return reply
    if not result:
        # i2pd (SAM 3.x) often omits RESULT=OK on successful DEST GENERATE: a single line
        # DEST REPLY PUB=… PRIV=… with no RESULT token. Treat PUB+PRIV as implicit OK.
        if (
            reply.command == "DEST"
            and reply.topic == "REPLY"
            and reply.fields.get("PUB")
            and reply.fields.get("PRIV")
        ):
            return reply
        # i2pd: successful SESSION CREATE may reply SESSION STATUS DESTINATION=… without RESULT=OK.
        if (
            reply.command == "SESSION"
            and reply.topic == "STATUS"
            and reply.fields.get("DESTINATION")
        ):
            return reply
        raise ProtocolError(message="SAM reply missing RESULT", raw_line=reply.raw_line)
    message = reply.fields.get("MESSAGE") or f"{reply.command} {reply.topic} failed"
    raise map_result_to_error(result, message, raw_line=reply.raw_line)


def _redact_sam_reply(raw_reply: str) -> str:
    if not raw_reply:
        return ""
    redacted_parts = []
    for token in raw_reply.split(" "):
        if "=" not in token:
            redacted_parts.append(token)
            continue
        key, value = token.split("=", 1)
        if key in SENSITIVE_SAM_KEYS and value:
            redacted_parts.append(f"{key}=<redacted>")
        else:
            redacted_parts.append(token)
    return " ".join(redacted_parts)


# Transition aliases for tests and incremental call-site migration.
hello = build_hello
dest_generate = build_dest_generate
naming_lookup = build_naming_lookup
session_create = build_session_create
stream_connect = build_stream_connect
stream_accept = build_stream_accept
stream_forward = build_stream_forward
