import asyncio
import base64
import errno
import hashlib
import inspect
import json
import logging
import os
import random
import re
import secrets
import shutil
import struct
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Literal, Mapping, Optional, Tuple

from i2pchat import sam as i2plib
from PIL import Image

from i2pchat import crypto
from i2pchat.blindbox.blindbox_blob import decrypt_blindbox_blob, encrypt_blindbox_blob
from i2pchat.blindbox.blindbox_client import BlindBoxClient
from i2pchat.blindbox.blindbox_key_schedule import (
    derive_blindbox_message_keys,
    derive_group_blindbox_message_keys,
)
from i2pchat.blindbox.blindbox_local_replica import ensure_local_blindbox_replica
from i2pchat.groups import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupEnvelope,
    GroupImportResult,
    GroupImportStatus,
    GroupMemberDeliveryResult,
    GroupMeshManager,
    GroupMeshPeerSnapshot,
    GroupManager,
    GroupRecipientDeliveryMetadata,
    GroupSendResult,
    GroupState,
    GroupTopologySnapshot,
    GroupTransportOutcome,
    build_observed_group_topology,
    render_group_topology_ascii,
    render_group_topology_mermaid,
)
from i2pchat.groups.models import normalize_member_id, utc_now
from i2pchat.groups.wire import (
    decode_group_transport_text,
    encode_group_transport_text,
    encode_group_transport_text_v2,
)
from i2pchat.storage.contact_book import (
    load_book,
    remember_peer,
    same_i2p_destination,
    save_book,
    trim_book,
)
from i2pchat.storage.blindbox_state import (
    BlindBoxState,
    atomic_write_json,
    atomic_write_text,
)
from i2pchat.storage.group_store import (
    GroupBlindBoxChannel,
    GroupHistoryEntry,
    GroupPendingBlindBoxMessage,
    GroupPendingDelivery,
    StoredGroupConversation,
    append_group_history_entry,
    delete_group_record,
    load_group_conversation,
    load_group_state as load_persisted_group_state,
    list_group_states as list_persisted_group_states,
    save_group_conversation,
    upsert_group_state,
)
from i2pchat.storage.profile_blindbox_replicas import (
    load_profile_blindbox_replicas_bundle,
    normalize_replica_endpoints,
    save_profile_blindbox_replicas_bundle,
)
from i2pchat.presentation.group_conversations import (
    render_group_control_text,
    short_member_label,
)
from i2pchat.protocol.chat_text_chunking import split_long_chat_text
from i2pchat.protocol.message_delivery import (
    DELIVERY_STATE_DELIVERED,
    DELIVERY_STATE_FAILED,
    DELIVERY_STATE_QUEUED,
    DELIVERY_STATE_SENDING,
    delivery_lifecycle_from_send_result,
)
from i2pchat.protocol.protocol_codec import (
    ENCRYPTED_TRAILER_SIZE,
    FLAG_ENCRYPTED,
    HEADER_STRUCT,
    MAGIC,
    ProtocolCodec,
)
from i2pchat.core.transient_profile import (
    LEGACY_TRANSIENT_PROFILE_NAMES,
    TRANSIENT_PROFILE_NAME,
    coalesce_profile_name,
    is_transient_profile_name,
)
from i2pchat.core.session_manager import (
    OutboundPolicy,
    PeerState,
    SessionManager,
    TransportState,
)
from i2pchat.core.live_peer_session import LivePeerSession, max_concurrent_live_sessions

logger = logging.getLogger("i2pchat")
PROTOCOL_VERSION = 4


def _exception_user_message(exc: BaseException) -> str:
    """Human-readable detail for UI/logs; many exceptions have empty str()."""
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def _sam_stream_connect_hint(exc: BaseException) -> str:
    """
    Extra context for common SAM STREAM CONNECT/ACCEPT failures (empty str() on many).
    InvalidId: router has no session with this nickname (lost session, wrong order, router restart).
    CantReachPeer: destination known but not reachable (tunnels, offline, typo in b32).
    """
    if isinstance(exc, i2plib.InvalidId):
        return (
            "Hint: SAM no longer knows this STREAM session. Restart the I2P router (i2pd/Java I2P) "
            "and I2PChat, wait until status shows Pending or Visible, then try Connect again. "
            "Do not run two I2PChat instances on the same profile simultaneously."
        )
    if isinstance(exc, i2plib.CantReachPeer):
        return (
            "Hint: peer not reachable yet. Check the full 52-character .b32.i2p address, "
            "ensure the other side is online with tunnels ready, wait 1–3 minutes, retry."
        )
    return ""


def _is_tcp_connection_refused(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionRefusedError):
        return True
    if isinstance(exc, OSError) and exc.errno in (
        errno.ECONNREFUSED,
        getattr(errno, "WSAECONNREFUSED", -1),
    ):
        return True
    return False


def _tcp_refusal_in_exception_chain(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if _is_tcp_connection_refused(cur):
            return True
        cur = cur.__cause__
    return False


def _blindbox_runtime_transport_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(
            cur,
            (
                asyncio.TimeoutError,
                ConnectionError,
                ConnectionResetError,
                BrokenPipeError,
                asyncio.IncompleteReadError,
            ),
        ):
            return True
        if _is_tcp_connection_refused(cur) or _tcp_refusal_in_exception_chain(cur):
            return True
        text = _exception_user_message(cur).lower()
        if any(
            token in text
            for token in (
                "connection lost",
                "no response / disconnected",
                "sam hello failed",
                "sam session create failed",
                "blind box sam startup failed",
                "timed out",
            )
        ):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _sam_unreachable_user_message(sam_address: Tuple[str, int]) -> str:
    host, port = sam_address
    return (
        f"The I2P SAM API is not reachable at {host}:{port} (connection refused). "
        "Start your I2P router (for example i2pd), make sure SAM is enabled on that host and port, "
        "then try again. If your router listens elsewhere, point I2PChat at the correct SAM address."
    )


DEFAULT_LOCAL_BLINDBOX_REPLICA = "127.0.0.1:19444"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

# Диагностика подвисаний при передаче файлов (см. план FILE_XFER):
#   I2PCHAT_FILE_XFER_DEBUG=1 — логи медленных drain и интервалов emit на приёме
#   I2PCHAT_FILE_SEND_DRAIN_BATCH=N — сколько D/G-фреймов подряд писать до одного drain (по умолч. 4)
#   I2PCHAT_FILE_CHUNK_BYTES=N — размер чтения с диска (1024..524288, по умолч. 4096)
#   I2PCHAT_MSG_ACK_DRAIN_EVERY=N — при исходящей передаче файла/картинки: после MSG_ACK/IMG_ACK
#     не вызывать drain каждый раз; принудительный drain каждые N сигналов (по умолч. 16, мин. 1)
# Фаза 0: при зависании зафиксируйте сторону (отправитель/получатель), размер файла, направление.


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def _file_send_drain_batch() -> int:
    raw = os.environ.get("I2PCHAT_FILE_SEND_DRAIN_BATCH", "4").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 4
    return max(1, min(n, 64))


def _file_read_chunk_bytes() -> int:
    raw = os.environ.get("I2PCHAT_FILE_CHUNK_BYTES", "4096").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 4096
    return max(1024, min(n, 512 * 1024))


def _msg_ack_soft_drain_every() -> int:
    raw = os.environ.get("I2PCHAT_MSG_ACK_DRAIN_EVERY", "16").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 16
    return max(1, min(n, 256))


def should_emit_file_progress(sent: int, chunk_len: int, total: int) -> bool:
    """Те же шаги прогресса, что и при исходящей send_file (малые файлы — чаще, крупные — ~64 KiB)."""
    if total <= 0:
        return True
    step = 4096 if total <= 65536 else 65536
    first_chunk_done = sent <= 4096 and sent > 0
    return bool(first_chunk_done or sent % step < chunk_len or sent == total)


def _finalize_inline_image_worker(
    image_bytes: bytes,
    detected_ext: str,
    images_dir: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Запись и PIL-валидация принятого inline-изображения (вызывать из executor/to_thread).
    Возвращает (safe_path, error_message); error_message не None при сбое.
    """
    import hashlib

    file_hash = hashlib.sha256(image_bytes).hexdigest()[:8]
    safe_filename = f"img_{int(time.time())}_{file_hash}.{detected_ext}"
    safe_path = os.path.join(images_dir, safe_filename)
    try:
        with open(safe_path, "wb") as f:
            f.write(image_bytes)
        is_valid, error_msg, _ = validate_image(safe_path)
        if not is_valid:
            try:
                os.remove(safe_path)
            except OSError:
                pass
            return None, error_msg or "invalid image"
        return safe_path, None
    except Exception as e:
        return None, str(e)
BLINDBOX_LOCAL_WRAP_VERSION_LEGACY = 1
BLINDBOX_LOCAL_WRAP_VERSION_CURRENT = 2

# Встроенные реплики Blind Box по умолчанию (дефолтный публичный пул проекта).
# Подставляются как источник «release-builtin», если для именованного профиля не заданы
# I2PCHAT_BLINDBOX_REPLICAS, I2PCHAT_BLINDBOX_DEFAULT_REPLICAS,
# I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE и нет файла {profile}.blindbox_replicas.json;
# отключить встроенный набор: I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS=1.
# I2PCHAT_BLINDBOX_SLOW_WARN=1 — показывать в чате предупреждение о медленном опросе реплик
#   (по умолчанию выключено). Детальная диагностика: I2PCHAT_BLINDBOX_DEBUG_UI=1.
# (Эфемерный профиль TRANSIENT_PROFILE_NAME — отдельно: BlindBox выключен, см. __init__.)
# Формат строки: <base32>.b32.i2p:19444 — порт TCP сервера Blind Box.
DEFAULT_RELEASE_BLINDBOX_ENDPOINTS: Tuple[str, ...] = (
    "tcglilyjadosrez5gu3kqvrdpu6ri622jwrzamtpburtnpge7wgq.b32.i2p:19444",
    "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444",
)


@dataclass
class ChatMessage:
    kind: str  # "me", "peer", "system", "info", "error", "success", "disconnect", "help"
    text: str
    timestamp: datetime
    # Адрес пира для kind=="peer" (как в current_peer_addr); для остальных видов — None.
    source_peer: Optional[str] = None
    message_id: Optional[str] = None
    delivery_state: Optional[str] = None
    delivery_route: Optional[str] = None
    delivery_hint: str = ""
    delivery_reason: str = ""
    retryable: bool = False
    conversation_kind: str = "direct"
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    group_sender_id: Optional[str] = None
    group_content_type: Optional[str] = None
    group_plain_text: Optional[str] = None


@dataclass
class FileTransferInfo:
    filename: str
    size: int
    received: int = 0
    is_sending: bool = False
    # True только для Send Pic (G), не для Send File (F/D)
    is_inline_image: bool = False
    rejected_by_peer: bool = False
    source_path: Optional[str] = None


@dataclass
class PeerTrustInfo:
    """Read-only TOFU trust snapshot for UI (signing key pin per peer)."""

    peer_normalized: str
    pinned: bool
    signing_key_hex: Optional[str] = None
    fingerprint_short: Optional[str] = None
    rejected_by_peer: bool = False  # True если получатель отклонил входящий файл


@dataclass
class PendingAckEntry:
    token: str
    ack_kind: str
    created_at: float
    peer_addr: str
    ack_session_epoch: int
    state: str = "awaiting_ack"


@dataclass
class SendTextResult:
    route: str
    accepted: bool
    reason: str = ""
    hint: str = ""
    message_id: Optional[str] = None
    delivery_state: Optional[str] = None
    retryable: bool = False


@dataclass
class _BlindBoxPeerSnapshot:
    peer_addr: str
    peer_id: str
    state: BlindBoxState
    root_secret: Optional[bytes] = None
    root_epoch: int = 0
    root_created_at: int = 0
    root_send_index_base: int = 0
    pending_root_secret: Optional[bytes] = None
    pending_root_epoch: int = 0
    pending_root_created_at: int = 0
    pending_root_send_index_base: int = 0
    prev_roots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _GroupBlindBoxSnapshot:
    group_id: str
    group_epoch: int
    state: BlindBoxState
    root_secret: Optional[bytes] = None
    root_epoch: int = 0
    root_created_at: int = 0
    root_send_index_base: int = 0
    pending_root_secret: Optional[bytes] = None
    pending_root_epoch: int = 0
    pending_root_created_at: int = 0
    pending_root_send_index_base: int = 0
    pending_root_target_members: tuple[str, ...] = ()
    pending_root_acked_members: set[str] = field(default_factory=set)
    prev_roots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _BlindBoxPollContext:
    channel_id: str
    state: BlindBoxState
    root_candidates: list[dict[str, Any]]
    save_state: Callable[[], None]
    source_peer: Optional[str] = None
    channel_kind: str = "peer"


@dataclass
class _BlindBoxPollGroupContext:
    group_id: str
    group_epoch: int
    state: BlindBoxState
    root_candidates: list[dict[str, Any]]
    save_state: Callable[[], None]


def _is_host_port_replica(value: str) -> bool:
    if ":" not in value:
        return False
    host, port_raw = value.rsplit(":", 1)
    if not host:
        return False
    # .i2p/.b32.i2p addresses (even with :port) should still go via SAM,
    # not via direct TCP socket resolution.
    if host.endswith(".i2p"):
        return False
    try:
        port = int(port_raw)
    except Exception:
        return False
    return 1 <= port <= 65535


def _is_loopback_replica(value: str) -> bool:
    if not _is_host_port_replica(value):
        return False
    host, _port = value.rsplit(":", 1)
    host_norm = host.strip().lower().strip("[]")
    return host_norm in {"127.0.0.1", "localhost", "::1"}


def _parse_replicas_list(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in (raw or "").split(","):
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _load_replicas_file(path: str) -> list[str]:
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    normalized: list[str] = []
    for line in content.splitlines():
        cleaned = line.split("#", 1)[0].strip()
        if cleaned:
            normalized.append(cleaned)
    return _parse_replicas_list(",".join(normalized))


def _blindbox_direct_replicas_security_issue(
    replicas: list[str],
    *,
    use_sam: bool,
    require_sam: bool,
    local_auth_token: str,
    allow_insecure_local: bool,
) -> Optional[str]:
    if require_sam and len(replicas) > 0 and not use_sam:
        return (
            "BlindBox strict SAM mode is enabled (I2PCHAT_BLINDBOX_REQUIRE_SAM=1), "
            "but replicas are configured as direct host:port endpoints. "
            "Use .i2p replicas via SAM or disable strict SAM mode."
        )
    if (
        replicas
        and not use_sam
        and any(_is_loopback_replica(item) for item in replicas)
        and not (local_auth_token or "").strip()
        and not allow_insecure_local
    ):
        return (
            "BlindBox local/direct replicas require I2PCHAT_BLINDBOX_LOCAL_TOKEN. "
            "Set a token or explicitly opt out with "
            "I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1."
        )
    return None


def _is_cant_reach_peer_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "cantreachpeer" in name or "cant reach peer" in text or "cantreachpeer" in text


StatusCallback = Callable[[str], Any]
MessageCallback = Callable[[ChatMessage], Any]
PeerChangedCallback = Callable[[Optional[str]], Any]
FileEventCallback = Callable[[FileTransferInfo], Any]
SimpleCallback = Callable[[str], Any]
TrustDecisionCallback = Callable[[str, str, str], bool | Awaitable[bool]]
TrustMismatchDecisionCallback = Callable[[str, str, str, str, str], bool | Awaitable[bool]]
FileOfferCallback = Callable[[str, int], Any]


PROFILE_DATA_SUBDIR = "profiles"


def get_profiles_dir() -> str:
    """
    Корневой каталог данных приложения I2PChat (Application Support / APPDATA / ~/.i2pchat).

    Содержит общие подпапки ``downloads/``, ``images/``, глобальные файлы вроде ``ui_prefs.json``,
    а также ``profiles/<имя>/`` с файлами каждого сохранённого профиля.
    """
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "I2PChat")
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "I2PChat")
    else:
        base = os.path.join(os.path.expanduser("~"), ".i2pchat")
    os.makedirs(base, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def get_downloads_dir() -> str:
    """
    Безопасная директория для входящих файлов (sandbox).
    Изолирована внутри profiles директории для предотвращения path traversal.
    """
    base = os.path.join(get_profiles_dir(), "downloads")
    os.makedirs(base, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def get_images_dir() -> str:
    """
    Безопасная директория для входящих изображений (sandbox).
    Изолирована внутри profiles директории для предотвращения path traversal.
    """
    base = os.path.join(get_profiles_dir(), "images")
    os.makedirs(base, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def get_profile_data_dir(
    profile: str, *, create: bool = False, app_root: Optional[str] = None
) -> str:
    """
    Каталог данных одного профиля: ``<app_root>/profiles/<profile>/``.

    Имена файлов внутри прежние (``alice.dat``, ``alice.trust.json``, …).
    ``app_root`` по умолчанию — результат ``get_profiles_dir()``.
    """
    p = ensure_valid_profile_name(profile)
    root = os.path.abspath(app_root if app_root is not None else get_profiles_dir())
    sub = os.path.join(root, PROFILE_DATA_SUBDIR, p)
    abs_sub = os.path.abspath(sub)
    if abs_sub != root and not abs_sub.startswith(root + os.sep):
        raise ValueError("Invalid profile data path")
    if create:
        os.makedirs(abs_sub, exist_ok=True)
        try:
            os.chmod(abs_sub, 0o700)
        except OSError:
            pass
    return abs_sub


def legacy_flat_profile_dat_path(app_root: str, profile: str) -> str:
    """Плоская раскладка до вложенных профилей: ``<app_root>/<profile>.dat``."""
    p = ensure_valid_profile_name(profile)
    return os.path.join(os.path.abspath(app_root), f"{p}.dat")


def nested_profile_dat_path(app_root: str, profile: str) -> str:
    """Новая раскладка: ``<app_root>/profiles/<profile>/<profile>.dat``."""
    p = ensure_valid_profile_name(profile)
    return os.path.join(
        os.path.abspath(app_root), PROFILE_DATA_SUBDIR, p, f"{p}.dat"
    )


def _legacy_profile_file_should_migrate(name: str, profile: str) -> bool:
    """Имя файла в корне app data, относящееся к профилю ``profile`` (для переноса)."""
    p = profile
    if name == f"{p}.dat":
        return True
    if name == f"{p}.trust.json":
        return True
    if name == f"{p}.signing":
        return True
    if name == f"{p}.contacts.json":
        return True
    if name == f"{p}.compose_drafts.json":
        return True
    if name == f"{p}.blindbox_replicas.json":
        return True
    prefix = f"{p}.history."
    if name.startswith(prefix) and name.endswith(".enc"):
        return True
    bb = f"{p}.blindbox."
    if name.startswith(bb) and name.endswith(".json"):
        return True
    return False


def migrate_legacy_profile_files_if_needed(
    *,
    app_root: Optional[str] = None,
    profile: str,
) -> None:
    """
    Если ``<root>/<profile>.dat`` есть, а в новой раскладке ещё нет — переносит
    все связанные файлы профиля в ``profiles/<profile>/``.
    """
    try:
        src_profile = ensure_valid_profile_name(profile)
    except ValueError:
        return
    dst_profile = (
        TRANSIENT_PROFILE_NAME
        if src_profile in LEGACY_TRANSIENT_PROFILE_NAMES
        else src_profile
    )
    root = os.path.abspath(app_root or get_profiles_dir())
    legacy_dat = legacy_flat_profile_dat_path(root, src_profile)
    new_dat = nested_profile_dat_path(root, dst_profile)
    if not os.path.isfile(legacy_dat):
        return
    if os.path.isfile(new_dat):
        return
    profiles_parent = os.path.join(root, PROFILE_DATA_SUBDIR)
    if os.path.exists(profiles_parent) and not os.path.isdir(profiles_parent):
        logger.warning(
            "Cannot migrate profile %r: %r exists and is not a directory",
            dst_profile,
            profiles_parent,
        )
        return
    dest_dir = get_profile_data_dir(dst_profile, create=True, app_root=root)

    def _migrated_basename(name: str) -> str:
        if dst_profile == src_profile:
            return name
        if name.startswith(f"{src_profile}."):
            return f"{dst_profile}{name[len(src_profile):]}"
        return name

    try:
        names = os.listdir(root)
    except OSError as e:
        logger.warning("Legacy profile migrate listdir failed (%s): %s", root, e)
        return
    to_move = [n for n in names if _legacy_profile_file_should_migrate(n, src_profile)]
    to_move.sort(key=lambda x: (x != f"{src_profile}.dat", x))
    for name in to_move:
        src = os.path.join(root, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dest_dir, _migrated_basename(name))
        if os.path.lexists(dst):
            logger.warning(
                "Skipping migrate %s → %s: destination exists", src, dst
            )
            continue
        try:
            os.replace(src, dst)
        except OSError as e:
            logger.warning("Legacy profile migrate failed %s → %s: %s", src, dst, e)


def legacy_flat_profile_dat_basenames(app_root: str) -> list[str]:
    """
    Имена профилей, у которых в корне ``app_root`` лежит плоский ``<имя>.dat``.

    Используется для одноразового прохода миграции при старте приложения.
    """
    root = os.path.abspath(app_root)
    found: set[str] = set()
    try:
        for name in os.listdir(root):
            if not name.endswith(".dat"):
                continue
            base = name[: -len(".dat")]
            if not is_valid_profile_name(base):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path):
                found.add(base)
    except OSError as e:
        logger.warning("legacy_flat_profile_dat_basenames listdir failed (%s): %s", root, e)
    return sorted(found)


def migrate_legacy_transient_profile_directory_if_needed(
    *, app_root: Optional[str] = None
) -> None:
    """
    Переименовывает ``profiles/default`` → ``profiles/<TRANSIENT_PROFILE_NAME>``,
    если новый каталог ещё не существует (миграция после смены имени эфемерного профиля).
    """
    root = os.path.abspath(app_root if app_root is not None else get_profiles_dir())
    sub = os.path.join(root, PROFILE_DATA_SUBDIR)
    old_dir = os.path.join(sub, "default")
    new_dir = os.path.join(sub, TRANSIENT_PROFILE_NAME)
    if not os.path.isdir(old_dir) or os.path.lexists(new_dir):
        return
    try:
        os.replace(old_dir, new_dir)
        logger.info(
            "Renamed transient profile directory %r -> %r",
            "default",
            TRANSIENT_PROFILE_NAME,
        )
    except OSError as e:
        logger.warning(
            "Transient profile directory migrate default -> %s failed: %s",
            TRANSIENT_PROFILE_NAME,
            e,
        )


def _migrate_transient_inner_default_prefixed_files(
    *, app_root: Optional[str] = None
) -> None:
    """
    После переименования ``profiles/default`` → ``profiles/random_address`` внутри могли
    остаться файлы ``default.dat``, ``default.contacts.json`` и т.д. — переименовываем
    в ``random_address.*``.
    """
    root = os.path.abspath(app_root if app_root is not None else get_profiles_dir())
    d = os.path.join(root, PROFILE_DATA_SUBDIR, TRANSIENT_PROFILE_NAME)
    if not os.path.isdir(d):
        return
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        if not name.startswith("default."):
            continue
        new_name = TRANSIENT_PROFILE_NAME + name[len("default") :]
        if new_name == name:
            continue
        src = os.path.join(d, name)
        dst = os.path.join(d, new_name)
        if not os.path.isfile(src) or os.path.lexists(dst):
            continue
        try:
            os.replace(src, dst)
        except OSError as e:
            logger.warning(
                "Transient inner file rename %s -> %s failed: %s", name, new_name, e
            )


def migrate_all_legacy_profiles_if_needed(app_root: Optional[str] = None) -> None:
    """
    Переносит **все** профили с плоской раскладкой в ``profiles/<имя>/`` за один проход.

    Имеет смысл вызывать при старте UI: иначе файлы профилей, которые пользователь
    давно не открывал, остаются в корне каталога данных до первого входа в профиль.
    Повторные вызовы дешёвые: для уже перенесённых профилей
    :func:`migrate_legacy_profile_files_if_needed` сразу выходит.
    """
    migrate_legacy_transient_profile_directory_if_needed(app_root=app_root)
    _migrate_transient_inner_default_prefixed_files(app_root=app_root)
    root = os.path.abspath(app_root if app_root is not None else get_profiles_dir())
    for profile in legacy_flat_profile_dat_basenames(root):
        migrate_legacy_profile_files_if_needed(app_root=root, profile=profile)


def resolve_existing_profile_file(
    app_root: str, profile: str, basename: str
) -> Optional[str]:
    """Путь к существующему файлу профиля (новая раскладка, иначе плоская)."""
    try:
        p = ensure_valid_profile_name(profile)
    except ValueError:
        return None
    if basename != f"{p}.dat" and not basename.startswith(f"{p}."):
        return None
    root = os.path.abspath(app_root)
    nested = os.path.join(root, PROFILE_DATA_SUBDIR, p, basename)
    if os.path.isfile(nested):
        return nested
    flat = os.path.join(root, basename)
    if os.path.isfile(flat):
        return flat
    return None


def list_profile_names_in_app_data(app_root: Optional[str] = None) -> list[str]:
    f"""Имена сохранённых профилей (есть ``.dat``), без эфемерного ``{TRANSIENT_PROFILE_NAME}``."""
    root = os.path.abspath(app_root or get_profiles_dir())
    seen: set[str] = set()
    sub_root = os.path.join(root, PROFILE_DATA_SUBDIR)
    if os.path.isdir(sub_root):
        try:
            for entry in os.listdir(sub_root):
                if (
                    entry == TRANSIENT_PROFILE_NAME
                    or entry in LEGACY_TRANSIENT_PROFILE_NAMES
                    or not is_valid_profile_name(entry)
                ):
                    continue
                dat_path = os.path.join(sub_root, entry, f"{entry}.dat")
                if os.path.isfile(dat_path):
                    seen.add(entry)
        except OSError:
            pass
    try:
        for name in os.listdir(root):
            if not name.endswith(".dat"):
                continue
            base = name[: -len(".dat")]
            if (
                base == TRANSIENT_PROFILE_NAME
                or base in LEGACY_TRANSIENT_PROFILE_NAMES
                or not is_valid_profile_name(base)
            ):
                continue
            if os.path.isfile(os.path.join(root, name)):
                seen.add(base)
    except OSError:
        pass
    return sorted(seen)


UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

def detect_inline_image_format(header: bytes) -> Optional[str]:
    """
    Определить формат inline-изображения по сигнатуре (PNG / JPEG / WebP).
    WebP: RIFF....WEBP (четыре байта размера chunk между RIFF и WEBP).
    """
    if not header:
        return None
    if len(header) >= 8 and header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(header) >= 3 and header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 4096  # max width/height in pixels
MAX_IMAGES_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB auto-cleanup threshold
PADDING_PROFILE_OFF = "off"
PADDING_PROFILE_BALANCED = "balanced"
SUPPORTED_PADDING_PROFILES = {PADDING_PROFILE_OFF, PADDING_PROFILE_BALANCED}
PADDING_ENVELOPE_MAGIC = b"I2PPAD1"
PADDING_BALANCED_BLOCK = 128

BLINDBOX_PRIVACY_LOW = "low"
BLINDBOX_PRIVACY_MEDIUM = "medium"
BLINDBOX_PRIVACY_HIGH = "high"
BLINDBOX_SUPPORTED_PRIVACY_PROFILES = {
    BLINDBOX_PRIVACY_LOW,
    BLINDBOX_PRIVACY_MEDIUM,
    BLINDBOX_PRIVACY_HIGH,
}
BLINDBOX_DEFAULT_PRIVACY_PROFILE = BLINDBOX_PRIVACY_HIGH
BLINDBOX_POLL_MODE_IDLE = "idle"
BLINDBOX_POLL_MODE_HOT = "hot"
BLINDBOX_POLL_MODE_COOLDOWN = "cooldown"
BLINDBOX_PRIVACY_DEFAULTS: dict[str, dict[str, float | int]] = {
    BLINDBOX_PRIVACY_LOW: {
        "poll_min_sec": 20.0,
        "poll_max_sec": 30.0,
        "cover_gets": 0,
        "padding_bucket": 256,
        "root_rotate_messages": 1024,
        "root_rotate_seconds": 24 * 60 * 60,
        "root_previous_grace_seconds": 24 * 60 * 60,
        "max_previous_roots": 1,
    },
    BLINDBOX_PRIVACY_MEDIUM: {
        "poll_min_sec": 20.0,
        "poll_max_sec": 30.0,
        "cover_gets": 1,
        "padding_bucket": 512,
        "root_rotate_messages": 512,
        "root_rotate_seconds": 12 * 60 * 60,
        "root_previous_grace_seconds": 24 * 60 * 60,
        "max_previous_roots": 2,
    },
    BLINDBOX_PRIVACY_HIGH: {
        "poll_min_sec": 20.0,
        "poll_max_sec": 30.0,
        "cover_gets": 2,
        "padding_bucket": 1024,
        "root_rotate_messages": 256,
        "root_rotate_seconds": 6 * 60 * 60,
        "root_previous_grace_seconds": 24 * 60 * 60,
        "max_previous_roots": 2,
    },
}


def is_valid_profile_name(name: str) -> bool:
    candidate = (name or "").strip()
    return bool(PROFILE_NAME_RE.fullmatch(candidate))


def ensure_valid_profile_name(name: str) -> str:
    candidate = (name or "").strip()
    if not is_valid_profile_name(candidate):
        raise ValueError(
            "Invalid profile name. Allowed characters: a-z A-Z 0-9 . _ - (1..64 chars)."
        )
    return candidate


def _peek_is_probable_peer_line(value: str) -> bool:
    """Та же эвристика, что I2PChatCore._is_probable_peer_addr (без экземпляра ядра)."""
    raw = (value or "").strip().lower()
    if not raw:
        return False
    if raw.endswith(".b32.i2p"):
        raw = raw[:-8]
    return bool(re.fullmatch(r"[a-z2-7]{40,80}", raw))


def peek_persisted_stored_peer(profile: str) -> Optional[str]:
    """
    Синхронно прочитать закреплённого пира из {profile}.dat до async-init ядра.

    В GUI `core.stored_peer` появляется только после загрузки профиля в фоне;
    для начальной вёрстки (свёрнутая боковая панель при Lock to peer) нужен этот peek.
    """
    raw = (profile or "").strip()
    if is_transient_profile_name(raw if raw else None):
        return None
    try:
        p = ensure_valid_profile_name(raw)
    except ValueError:
        return None
    app_root = get_profiles_dir()
    key_file = resolve_existing_profile_file(app_root, p, f"{p}.dat")
    if not key_file:
        return None
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except OSError:
        return None
    stored_line: Optional[str] = None
    if len(lines) > 1 and _peek_is_probable_peer_line(lines[1]):
        stored_line = lines[1]
    elif len(lines) > 0 and _peek_is_probable_peer_line(lines[0]):
        stored_line = lines[0]
    if not stored_line:
        return None
    raw = (stored_line or "").strip().lower()
    if not raw:
        return None
    if any(ch in raw for ch in ("\r", "\n", "\x00", " ", "\t", "=")):
        return None
    if raw.endswith(".b32.i2p"):
        host = raw[: -len(".b32.i2p")]
    elif "." in raw:
        return None
    else:
        host = raw
    if not re.fullmatch(r"[a-z2-7]{40,80}", host):
        return None
    return host


def _resolve_blindbox_privacy_profile(raw: str) -> str:
    candidate = (raw or "").strip().lower()
    if candidate in BLINDBOX_SUPPORTED_PRIVACY_PROFILES:
        return candidate
    return BLINDBOX_DEFAULT_PRIVACY_PROFILE


def allocate_unique_profile_name(
    base_dir: str, profile_name: str, max_attempts: int = 1000
) -> str:
    """
    Возвращает валидное уникальное имя профиля без расширения `.dat`.
    Формат коллизий: `name_1`, `name_2`, ...

    ``base_dir`` — корень данных приложения (как ``get_profiles_dir()``).
    """
    base_name = ensure_valid_profile_name(profile_name)
    app_root = os.path.abspath(base_dir)

    def _dat_exists(name: str) -> bool:
        return os.path.exists(nested_profile_dat_path(app_root, name)) or os.path.exists(
            legacy_flat_profile_dat_path(app_root, name)
        )

    if not _dat_exists(base_name):
        return base_name
    for idx in range(1, max_attempts + 1):
        suffix = f"_{idx}"
        max_base_len = 64 - len(suffix)
        if max_base_len <= 0:
            break
        candidate = f"{base_name[:max_base_len]}{suffix}"
        if not is_valid_profile_name(candidate):
            continue
        if not _dat_exists(candidate):
            return candidate
    raise FileExistsError(f"Cannot allocate unique profile name for {base_name!r}")


def import_profile_dat_atomic(
    source_path: str,
    profiles_dir: str,
    profile_name: str,
    max_attempts: int = 1000,
) -> str:
    """
    Атомарно импортирует .dat профиль в каталог данных приложения и возвращает имя профиля.

    ``profiles_dir`` — корень приложения (как ``get_profiles_dir()``); файл создаётся в
    ``profiles/<имя>/<имя>.dat``.
    Коллизии обрабатываются форматом name_1, name_2, ... без TOCTOU между check/use.
    """
    base_name = ensure_valid_profile_name(profile_name)
    src_abs = os.path.abspath(source_path)
    profiles_abs = os.path.abspath(profiles_dir)
    if os.path.islink(src_abs):
        raise ValueError("Refusing to import profile from symlink path")
    os.makedirs(profiles_abs, exist_ok=True)
    reserved_path = ""
    tmp_path = ""
    candidate = base_name
    candidate_subdir = ""
    try:
        for idx in range(0, max_attempts + 1):
            if idx == 0:
                candidate = base_name
            else:
                suffix = f"_{idx}"
                max_base_len = 64 - len(suffix)
                if max_base_len <= 0:
                    break
                candidate = f"{base_name[:max_base_len]}{suffix}"
                if not is_valid_profile_name(candidate):
                    continue
            candidate_subdir = get_profile_data_dir(
                candidate, create=True, app_root=profiles_abs
            )
            reserved_path = os.path.join(candidate_subdir, f"{candidate}.dat")
            if os.path.abspath(reserved_path) == src_abs:
                return candidate
            try:
                fd = os.open(
                    reserved_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                continue
            os.close(fd)
            break
        else:
            raise FileExistsError(f"Cannot allocate unique profile name for {base_name!r}")

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=candidate_subdir,
            prefix=f".{candidate}.",
            suffix=".tmp",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            with open(src_abs, "rb") as src:
                shutil.copyfileobj(src, tf)

        os.replace(tmp_path, reserved_path)
        tmp_path = ""
        return candidate
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if reserved_path and os.path.exists(reserved_path):
            try:
                os.unlink(reserved_path)
            except OSError:
                pass
        raise


def max_base64_chars_for_bytes(byte_count: int) -> int:
    """Maximum Base64 text length needed to encode up to byte_count bytes."""
    if byte_count <= 0:
        return 0
    return ((byte_count + 2) // 3) * 4


def validate_image(path: str) -> Tuple[bool, str, Optional[str]]:
    """
    Валидация изображения перед отправкой/отображением.
    
    Returns:
        (is_valid, error_message, detected_extension)
    """
    if not os.path.exists(path):
        return False, "File does not exist", None
    
    file_size = os.path.getsize(path)
    if file_size > MAX_IMAGE_SIZE:
        return False, f"Image too large: {file_size} bytes (max {MAX_IMAGE_SIZE // (1024*1024)} MB)", None
    
    if file_size == 0:
        return False, "Empty file", None
    
    try:
        with open(path, 'rb') as f:
            header = f.read(12)
    except IOError as e:
        return False, f"Cannot read file: {e}", None
    
    detected_ext = detect_inline_image_format(header)
    if detected_ext is None:
        return False, "Unsupported image format (PNG, JPEG, or WebP required)", None
    
    # Validate with PIL for extra safety
    try:
        with Image.open(path) as img:
            width, height = img.size
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                return False, f"Image too large: {width}x{height} (max {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION})", None
            # Force load to detect corrupted images
            img.load()
    except Exception as e:
        return False, f"Invalid or corrupted image: {e}", None
    
    return True, "", detected_ext


def cleanup_images_cache() -> None:
    """
    Автоочистка кэша изображений при превышении лимита.
    Удаляет самые старые файлы.
    """
    images_dir = get_images_dir()
    if not os.path.exists(images_dir):
        return
    
    files = []
    total_size = 0
    
    for name in os.listdir(images_dir):
        path = os.path.join(images_dir, name)
        if os.path.isfile(path):
            stat = os.stat(path)
            files.append((path, stat.st_mtime, stat.st_size))
            total_size += stat.st_size
    
    if total_size <= MAX_IMAGES_CACHE_SIZE:
        return
    
    # Sort by modification time (oldest first)
    files.sort(key=lambda x: x[1])
    
    for path, _, size in files:
        if total_size <= MAX_IMAGES_CACHE_SIZE * 0.8:  # Clean to 80%
            break
        try:
            os.remove(path)
            total_size -= size
            logger.info("Cleaned up old image: %s", os.path.basename(path))
        except OSError:
            pass


def sanitize_filename(name: str) -> str:
    """
    Очистка имени файла от потенциально опасных символов.
    Поддерживает Unicode (кириллица, иероглифы и т.д.).
    """
    name = os.path.basename(name).strip()
    name = UNSAFE_FILENAME_CHARS.sub('_', name)
    if not name or name.startswith('.'):
        return f"file_{int(time.time())}"
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        ext = ext[:10]
        name = f"file_{int(time.time())}{ext}"
    return name


def allocate_unique_filename(base_dir: str, filename: str, max_attempts: int = 1000) -> str:
    """
    Возвращает уникальный путь в base_dir без перезаписи существующего файла.
    Формат коллизий: "name (N).ext".
    """
    safe_name = sanitize_filename(filename)
    name_part, ext = os.path.splitext(safe_name)
    first_choice = os.path.join(base_dir, safe_name)
    if not os.path.exists(first_choice):
        return first_choice
    for idx in range(1, max_attempts + 1):
        candidate_name = f"{name_part} ({idx}){ext}"
        candidate = os.path.join(base_dir, candidate_name)
        if not os.path.exists(candidate):
            return candidate
    raise FileExistsError(f"Cannot allocate unique filename for {safe_name!r}")


KEYRING_SERVICE = "i2pchat"
SIGNING_KEYRING_SUFFIX = "__signing_seed__"


def _try_keyring_get(profile: str) -> Optional[str]:
    """Попытка загрузить приватный ключ из системного keyring."""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, profile)
    except ImportError:
        logger.debug("keyring not available, using file storage")
        return None
    except Exception as e:
        logger.debug("keyring get failed (%s), using file storage: %s", profile, e)
        return None


def _try_keyring_set(profile: str, private_key: str) -> bool:
    """Попытка сохранить приватный ключ в системный keyring."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, profile, private_key)
        return True
    except ImportError:
        logger.debug("keyring not available, using file storage")
        return False
    except Exception as e:
        logger.debug("keyring set failed (%s), using file storage: %s", profile, e)
        return False


class I2PChatCore:
    """
    Ядро I2P-чата без привязки к UI.

    Отвечает за:
    - инициализацию SAM-сессии и профилей
    - установление и приём соединений
    - протокол обмена сообщениями/файлами/изображениями
    - уведомление UI через колбэки
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        sam_address: Tuple[str, int] = ("127.0.0.1", 7656),
        on_status: Optional[StatusCallback] = None,
        on_message: Optional[MessageCallback] = None,
        on_peer_changed: Optional[PeerChangedCallback] = None,
        on_system: Optional[SimpleCallback] = None,
        on_error: Optional[SimpleCallback] = None,
        on_file_event: Optional[FileEventCallback] = None,
        on_file_offer: Optional[FileOfferCallback] = None,
        on_image_received: Optional[Callable[[str], Any]] = None,
        on_inline_image_received: Optional[Callable[..., Any]] = None,
        on_text_delivered: Optional[Callable[[str], Any]] = None,
        on_image_delivered: Optional[Callable[[str], Any]] = None,
        on_file_delivered: Optional[Callable[[str], Any]] = None,
        on_trust_decision: Optional[TrustDecisionCallback] = None,
        on_trust_mismatch_decision: Optional[TrustMismatchDecisionCallback] = None,
        on_saved_contacts_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        self.sam_address = sam_address
        self._sam_session_create_timeout = max(
            15.0,
            float(os.environ.get("I2PCHAT_SAM_SESSION_CREATE_TIMEOUT", "180")),
        )
        cp = coalesce_profile_name(profile)
        self.profile = (
            TRANSIENT_PROFILE_NAME
            if cp == TRANSIENT_PROFILE_NAME
            else ensure_valid_profile_name(cp)
        )

        self.on_status = on_status
        self.on_message = on_message
        self.on_peer_changed = on_peer_changed
        self.on_system = on_system
        self.on_error = on_error
        self.on_file_event = on_file_event
        self.on_file_offer = on_file_offer
        self.on_image_received = on_image_received
        self.on_inline_image_received = on_inline_image_received
        self.on_text_delivered = on_text_delivered
        self.on_image_delivered = on_image_delivered
        self.on_file_delivered = on_file_delivered
        self.on_trust_decision = on_trust_decision
        self.on_trust_mismatch_decision = on_trust_mismatch_decision
        self.on_saved_contacts_changed = on_saved_contacts_changed
        self._trust_auto = (
            os.environ.get("I2PCHAT_TRUST_AUTO", "").strip().lower() in TRUTHY_ENV_VALUES
        )

        # Include high-entropy suffix so rapid re-inits (router settings apply + rollback in
        # the same wall second) never reuse the same SAM nickname — i2pd returns DUPLICATED_ID.
        self.session_id = (
            f"chat_{self.profile}_{int(time.time())}_{secrets.token_hex(4)}"
        )
        self.network_status = "initializing"
        self.peer_b32: str = "Waiting for incoming connections..."

        self.my_dest: Optional[i2plib.Destination] = None
        self.stored_peer: Optional[str] = None
        # UI/chat selection (normalized bare id); not authoritative for routing or ACK tables.
        self.current_peer_addr: Optional[str] = None
        self.current_peer_dest_b64: Optional[str] = None
        self.peer_identity_binding_verified: bool = False
        self.proven: bool = False

        # файловый приём
        self.incoming_file = None
        self.incoming_info: Optional[FileTransferInfo] = None

        # буфер для изображений (ASCII-арт)
        self.image_buffer: list[str] = []
        
        # буфер для inline-изображений (бинарные данные)
        self.inline_image_buffer: bytearray = bytearray()
        self.inline_image_info: Optional[Tuple[str, int]] = None  # (filename, size)
        self._inline_image_last_emit: int = 0

        # криптография (устанавливается при handshake v2)
        self.shared_key: Optional[bytes] = None
        self.shared_mac_key: Optional[bytes] = None
        self.my_nonce: Optional[bytes] = None
        self.peer_nonce: Optional[bytes] = None
        self.my_ephemeral_private: Optional[bytes] = None
        self.my_ephemeral_public: Optional[bytes] = None
        self.peer_ephemeral_public: Optional[bytes] = None
        self.my_signing_seed: Optional[bytes] = None
        self.my_signing_public: Optional[bytes] = None
        self.peer_signing_public: Optional[bytes] = None
        self.peer_trusted_signing_keys: dict[str, str] = {}
        self.use_encryption: bool = False
        self.handshake_complete: bool = False
        raw_padding_profile = os.environ.get(
            "I2PCHAT_PADDING_PROFILE", PADDING_PROFILE_BALANCED
        ).strip().lower()
        self.padding_profile = (
            raw_padding_profile
            if raw_padding_profile in SUPPORTED_PADDING_PROFILES
            else PADDING_PROFILE_BALANCED
        )
        self._handshake_initiated: bool = False
        self._send_seq: int = 0
        self._recv_seq: int = 0
        self._next_msg_id: int = 1
        self._pending_text_acks: dict[int, PendingAckEntry] = {}
        self._pending_file_acks: dict[int, PendingAckEntry] = {}
        self._pending_image_acks: dict[int, PendingAckEntry] = {}
        self._incoming_file_msg_id: Optional[int] = None
        self._incoming_image_msg_id: Optional[int] = None
        self._last_ack_prune_ts: float = time.monotonic()
        self._ack_session_epoch: int = 0
        self._ack_drop_counters: dict[str, int] = {
            "unknown_id": 0,
            "context_mismatch": 0,
            "invalid_format": 0,
            "expired_or_state": 0,
        }
        self.session_manager = SessionManager()
        self.group_manager = GroupManager(
            session_manager=self.session_manager,
            send_live=self._send_group_envelope_live,
            send_offline=self._send_group_envelope_via_blindbox,
        )
        self.group_mesh_manager = GroupMeshManager(
            list_group_states=self.list_group_states,
            get_local_member_id=self._local_group_member_id,
            build_peer_snapshot=self._build_group_mesh_peer_snapshot,
            schedule_peer_intros=self._schedule_group_peer_intros,
        )
        self._live_sessions: dict[str, LivePeerSession] = {}
        self.active_live_peer_id: Optional[str] = None
        # Очередь фоновых connect_to_peer для полного mesh группы (нормализованные peer id).
        self._group_intro_backlog: set[str] = set()
        self._group_intro_task: Optional[asyncio.Task[None]] = None
        self._group_pending_flush_backlog: set[str] = set()
        self._group_pending_flush_task: Optional[asyncio.Task[None]] = None
        self._group_mesh_task: Optional[asyncio.Task[None]] = None

        # Флаг активной передачи файла (для защиты от timeout в receive_loop)
        self._file_transfer_active: bool = False
        # Счётчик MSG_ACK/IMG_ACK без drain во время исходящей передачи (см. _write_signal_frame_maybe_soft_drain)
        self._soft_signal_ack_since_drain: int = 0
        # Флаг отмены передачи (локальная отмена пользователем)
        self._cancel_transfer: bool = False
        # Получен сигнал ABORT_FILE от пира — отменить текущую отправку
        self._transfer_aborted_by_peer: bool = False
        # Получен сигнал REJECT_FILE — получатель отклонил входящий файл
        self._transfer_rejected_by_peer: bool = False
        # Флаг активного receive_loop (предотвращает запуск дублирующих корутин)
        self._recv_loop_active: bool = False
        self._file_xfer_debug: bool = _env_truthy("I2PCHAT_FILE_XFER_DEBUG")
        self._file_xfer_debug_last_recv_emit_mono: Optional[float] = None
        self._codec = ProtocolCodec(
            allowed_types={"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"},
            max_frame_body=self.MAX_FRAME_BODY,
        )
        blindbox_enabled_raw = os.environ.get("I2PCHAT_BLINDBOX_ENABLED", "").strip().lower()
        self._blindbox_enabled_source = "default"
        if self.profile == TRANSIENT_PROFILE_NAME:
            self.blindbox_enabled = False
            self._blindbox_enabled_source = "transient-disabled"
        elif blindbox_enabled_raw in {"0", "false", "no", "off"}:
            self.blindbox_enabled = False
            self._blindbox_enabled_source = "env-disabled"
        elif blindbox_enabled_raw in {"1", "true", "yes", "on"}:
            self.blindbox_enabled = True
            self._blindbox_enabled_source = "env-enabled"
        else:
            # Persistent profiles default to BlindBox-enabled mode.
            self.blindbox_enabled = True
        self._blindbox_replica_auth: dict[str, str] = {}
        replicas_from_env = _parse_replicas_list(
            os.environ.get("I2PCHAT_BLINDBOX_REPLICAS", "")
        )
        replicas_from_default_env = _parse_replicas_list(
            os.environ.get("I2PCHAT_BLINDBOX_DEFAULT_REPLICAS", "")
        )
        replicas_file_path = os.environ.get(
            "I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE", ""
        ).strip()
        replicas_from_file = _load_replicas_file(replicas_file_path)
        no_release_builtin = (
            os.environ.get("I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS", "").strip().lower()
            in TRUTHY_ENV_VALUES
        )
        local_fallback_raw = os.environ.get(
            "I2PCHAT_BLINDBOX_LOCAL_FALLBACK", ""
        ).strip().lower()
        local_fallback_enabled = local_fallback_raw in TRUTHY_ENV_VALUES
        if replicas_from_env:
            self._blindbox_replicas_source = "env"
            replicas_resolved = replicas_from_env
        elif replicas_from_default_env:
            self._blindbox_replicas_source = "env-default"
            replicas_resolved = replicas_from_default_env
        elif replicas_from_file:
            self._blindbox_replicas_source = "file-default"
            replicas_resolved = replicas_from_file
        elif self.profile != TRANSIENT_PROFILE_NAME and (
            (_bb_prof := load_profile_blindbox_replicas_bundle(
                self.get_profile_data_dir(create=False), self.profile
            ))[0]
        ):
            self._blindbox_replicas_source = "profile-file"
            replicas_resolved = _bb_prof[0]
            self._blindbox_replica_auth = dict(_bb_prof[1])
        elif not no_release_builtin and DEFAULT_RELEASE_BLINDBOX_ENDPOINTS:
            self._blindbox_replicas_source = "release-builtin"
            replicas_resolved = list(DEFAULT_RELEASE_BLINDBOX_ENDPOINTS)
        else:
            self._blindbox_replicas_source = "none"
            replicas_resolved = []
        if (
            not replicas_resolved
            and self.profile != TRANSIENT_PROFILE_NAME
            and self.blindbox_enabled
            and local_fallback_enabled
        ):
            replicas_resolved = [DEFAULT_LOCAL_BLINDBOX_REPLICA]
            self._blindbox_replicas_source = "local-auto"
        self.blindbox_replicas = replicas_resolved
        self._blindbox_use_sam = not (
            len(self.blindbox_replicas) > 0
            and all(_is_host_port_replica(item) for item in self.blindbox_replicas)
        )
        self._blindbox_require_sam = (
            os.environ.get("I2PCHAT_BLINDBOX_REQUIRE_SAM", "").strip().lower()
            in TRUTHY_ENV_VALUES
        )
        if (
            self._blindbox_require_sam
            and len(self.blindbox_replicas) > 0
            and not self._blindbox_use_sam
        ):
            raise ValueError(
                "BlindBox strict SAM mode is enabled "
                "(I2PCHAT_BLINDBOX_REQUIRE_SAM=1), "
                "but replicas are configured as direct host:port endpoints. "
                "Use .i2p replicas via SAM or disable strict SAM mode."
            )
        if self.blindbox_enabled and len(self.blindbox_replicas) > 0 and not self._blindbox_use_sam:
            logger.warning(
                "BlindBox transport is using direct TCP (non-SAM): %s. "
                "Set I2PCHAT_BLINDBOX_REQUIRE_SAM=1 to forbid this mode.",
                ", ".join(self.blindbox_replicas),
            )
        local_auth_token_env = os.environ.get("I2PCHAT_BLINDBOX_LOCAL_TOKEN", "").strip()
        self._blindbox_allow_insecure_local = (
            os.environ.get("I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL", "").strip().lower()
            in TRUTHY_ENV_VALUES
        )
        if self._blindbox_replicas_source == "local-auto":
            self._blindbox_local_auth_token = local_auth_token_env or secrets.token_hex(24)
            if not local_auth_token_env:
                logger.info(
                    "BlindBox local-auto: I2PCHAT_BLINDBOX_LOCAL_TOKEN is not set; using an "
                    "ephemeral token for this process. Set a stable secret if a separate "
                    "local replica process must authenticate to the same endpoint."
                )
        else:
            self._blindbox_local_auth_token = local_auth_token_env
        self._blindbox_local_max_entries = max(
            64,
            int(os.environ.get("I2PCHAT_BLINDBOX_LOCAL_MAX_ENTRIES", "4096")),
        )
        if (
            self.blindbox_enabled
            and not self._blindbox_use_sam
            and any(_is_loopback_replica(item) for item in self.blindbox_replicas)
            and not self._blindbox_local_auth_token
        ):
            if self._blindbox_allow_insecure_local:
                logger.warning(
                    "BlindBox local/direct replicas are enabled without auth token "
                    "because I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1 is set."
                )
            else:
                raise ValueError(
                    "BlindBox local/direct replicas require I2PCHAT_BLINDBOX_LOCAL_TOKEN. "
                    "Set a token or explicitly opt out with "
                    "I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1."
                )
        # Default 1: practical fast path for text — try one Blind Box and, on failure,
        # fallback to the next endpoint. Set I2PCHAT_BLINDBOX_PUT_QUORUM=2 to require
        # all configured boxes to ACK each offline message.
        self.blindbox_put_quorum = max(
            1,
            int(os.environ.get("I2PCHAT_BLINDBOX_PUT_QUORUM", "1")),
        )
        self.blindbox_get_quorum = max(
            1, int(os.environ.get("I2PCHAT_BLINDBOX_GET_QUORUM", "1"))
        )
        self._blindbox_client: Optional[BlindBoxClient] = None
        self._blindbox_task: Optional[asyncio.Task[Any]] = None
        self._blindbox_runtime_lock = asyncio.Lock()
        self._blindbox_runtime_last_error = ""
        self._blindbox_runtime_retry_not_before_mono = 0.0
        # Serializes encrypt+PUT+send_index bump: parallel sends reuse the same
        # lookup_token (same index) but ciphertext differs (random nonce/padding)
        # → PUT EXISTS verification mismatch on boxes.
        self._blindbox_send_lock = asyncio.Lock()
        self._blindbox_state = BlindBoxState()
        self._blindbox_root_secret: Optional[bytes] = None
        self._blindbox_root_epoch: int = 0
        self._blindbox_root_created_at: int = 0
        self._blindbox_root_send_index_base: int = 0
        self._blindbox_pending_root_secret: Optional[bytes] = None
        self._blindbox_pending_root_epoch: int = 0
        self._blindbox_pending_root_created_at: int = 0
        self._blindbox_pending_root_send_index_base: int = 0
        self._blindbox_prev_roots: list[dict[str, Any]] = []
        self._blindbox_max_seen_hashes = max(
            1,
            int(os.environ.get("I2PCHAT_BLINDBOX_MAX_SEEN_HASHES", "8192")),
        )
        self._blindbox_seen_hashes: set[str] = set()
        self._blindbox_seen_hash_order: deque[str] = deque()
        self._blindbox_rng = random.SystemRandom()
        self._blindbox_privacy_profile = _resolve_blindbox_privacy_profile(
            os.environ.get(
                "I2PCHAT_BLINDBOX_PRIVACY_PROFILE", BLINDBOX_DEFAULT_PRIVACY_PROFILE
            )
        )
        defaults = BLINDBOX_PRIVACY_DEFAULTS[self._blindbox_privacy_profile]
        poll_min_raw = float(
            os.environ.get(
                "I2PCHAT_BLINDBOX_POLL_MIN_SEC",
                str(defaults["poll_min_sec"]),
            )
        )
        poll_max_raw = float(
            os.environ.get(
                "I2PCHAT_BLINDBOX_POLL_MAX_SEC",
                str(defaults["poll_max_sec"]),
            )
        )
        self._blindbox_poll_min_sec = max(0.5, min(poll_min_raw, poll_max_raw))
        self._blindbox_poll_max_sec = max(self._blindbox_poll_min_sec, poll_max_raw)
        self._blindbox_poll_hot_sec = max(
            0.5,
            float(os.environ.get("I2PCHAT_BLINDBOX_POLL_HOT_SEC", "2.5")),
        )
        self._blindbox_poll_hot_window_sec = max(
            0.0,
            float(os.environ.get("I2PCHAT_BLINDBOX_POLL_HOT_WINDOW_SEC", "20")),
        )
        self._blindbox_poll_cooldown_sec = max(
            0.5,
            float(os.environ.get("I2PCHAT_BLINDBOX_POLL_COOLDOWN_SEC", "5")),
        )
        self._blindbox_poll_cooldown_window_sec = max(
            0.0,
            float(
                os.environ.get("I2PCHAT_BLINDBOX_POLL_COOLDOWN_WINDOW_SEC", "20")
            ),
        )
        self._blindbox_poll_hot_until_mono = 0.0
        self._blindbox_poll_cooldown_until_mono = 0.0
        self._blindbox_runtime_retry_sec = max(
            1.0,
            float(os.environ.get("I2PCHAT_BLINDBOX_RUNTIME_RETRY_SEC", "15")),
        )
        self._blindbox_poll_wakeup = asyncio.Event()
        self._blindbox_recv_scan_budget = max(
            1,
            int(os.environ.get("I2PCHAT_BLINDBOX_RECV_SCAN_BUDGET", "8")),
        )
        self._blindbox_get_first_timeout_sec = max(
            0.2,
            float(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_GET_FIRST_TIMEOUT_SEC", "2.5"
                )
            ),
        )
        self._blindbox_get_first_miss_grace_sec = max(
            0.05,
            float(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_GET_FIRST_MISS_GRACE_SEC", "1.2"
                )
            ),
        )
        self._blindbox_debug_ui = _env_truthy("I2PCHAT_BLINDBOX_DEBUG_UI")
        self._blindbox_debug_ui_interval_sec = max(
            1.0,
            float(os.environ.get("I2PCHAT_BLINDBOX_DEBUG_UI_INTERVAL_SEC", "8")),
        )
        self._blindbox_debug_ui_slow_sec = max(
            0.05,
            float(os.environ.get("I2PCHAT_BLINDBOX_DEBUG_UI_SLOW_SEC", "0.8")),
        )
        self._blindbox_debug_ui_last_emit_mono = 0.0
        self._blindbox_slow_warn_sec = max(
            0.2,
            float(os.environ.get("I2PCHAT_BLINDBOX_SLOW_WARN_SEC", "5.0")),
        )
        self._blindbox_slow_warn_interval_sec = max(
            1.0,
            float(
                os.environ.get("I2PCHAT_BLINDBOX_SLOW_WARN_INTERVAL_SEC", "30.0")
            ),
        )
        self._blindbox_slow_warn_ui = _env_truthy("I2PCHAT_BLINDBOX_SLOW_WARN")
        self._blindbox_slow_warn_last_mono = 0.0
        self._blindbox_cover_gets = max(
            0,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_COVER_GETS",
                    str(int(defaults["cover_gets"])),
                )
            ),
        )
        self._blindbox_padding_bucket = max(
            64,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_PADDING_BUCKET",
                    str(int(defaults["padding_bucket"])),
                )
            ),
        )
        self._blindbox_root_rotate_messages = max(
            1,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_ROOT_ROTATE_MESSAGES",
                    str(int(defaults["root_rotate_messages"])),
                )
            ),
        )
        self._blindbox_root_rotate_seconds = max(
            60,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_ROOT_ROTATE_SECONDS",
                    str(int(defaults["root_rotate_seconds"])),
                )
            ),
        )
        self._blindbox_previous_grace_seconds = max(
            300,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_ROOT_PREVIOUS_GRACE_SECONDS",
                    str(int(defaults["root_previous_grace_seconds"])),
                )
            ),
        )
        self._blindbox_max_previous_roots = max(
            0,
            int(
                os.environ.get(
                    "I2PCHAT_BLINDBOX_MAX_PREVIOUS_ROOTS",
                    str(int(defaults["max_previous_roots"])),
                )
            ),
        )

    # ---------- вспомогательные уведомления ----------

    def _emit_status(self, status: str) -> None:
        self.network_status = status
        if status == "visible":
            self.session_manager.transition_transport(
                TransportState.READY, reason="status-visible"
            )
        elif status == "local_ok":
            self.session_manager.transition_transport(
                TransportState.DEGRADED, reason="status-local-ok"
            )
        elif status == "initializing":
            self.session_manager.transition_transport(
                TransportState.STARTING, reason="status-initializing"
            )
        if self.on_status:
            self.on_status(status)

    def _emit_message(
        self,
        kind: str,
        text: str,
        source_peer: Optional[str] = None,
        *,
        message_id: Optional[str] = None,
        delivery_state: Optional[str] = None,
        delivery_route: Optional[str] = None,
        delivery_hint: str = "",
        delivery_reason: str = "",
        retryable: bool = False,
        conversation_kind: str = "direct",
        conversation_id: Optional[str] = None,
        conversation_title: Optional[str] = None,
        group_sender_id: Optional[str] = None,
        group_content_type: Optional[GroupContentType] = None,
        group_plain_text: Optional[str] = None,
    ) -> None:
        if self.on_message:
            msg = ChatMessage(
                kind=kind,
                text=text,
                timestamp=datetime.now(timezone.utc),
                source_peer=source_peer,
                message_id=message_id,
                delivery_state=delivery_state,
                delivery_route=delivery_route,
                delivery_hint=delivery_hint,
                delivery_reason=delivery_reason,
                retryable=retryable,
                conversation_kind=conversation_kind,
                conversation_id=conversation_id,
                conversation_title=conversation_title,
                group_sender_id=group_sender_id,
                group_content_type=(
                    str(group_content_type) if group_content_type is not None else None
                ),
                group_plain_text=group_plain_text,
            )
            self.on_message(msg)

    def _emit_outbound_delivery_update(
        self,
        message_id: str,
        *,
        delivery_state: str,
        delivery_hint: str = "",
        delivery_reason: str = "",
        retryable: bool = False,
    ) -> None:
        """UI: update an existing outbound bubble (e.g. sending → queued after BlindBox PUT)."""
        cb = getattr(self, "on_outbound_delivery_update", None)
        if cb is None:
            return
        try:
            cb(
                message_id,
                delivery_state,
                delivery_hint,
                delivery_reason,
                retryable,
            )
        except Exception:
            logger.debug("on_outbound_delivery_update callback failed", exc_info=True)

    def _emit_notify(
        self,
        kind: str,
        text: str,
        source_peer: Optional[str] = None,
        *,
        conversation_kind: str = "direct",
        conversation_id: Optional[str] = None,
        conversation_title: Optional[str] = None,
        group_sender_id: Optional[str] = None,
        group_content_type: Optional[GroupContentType] = None,
        group_plain_text: Optional[str] = None,
    ) -> None:
        """
        Уведомление UI о новом сообщении для системных нотификаций.

        Отдельный слой, чтобы ядро не зависело от конкретной реализации уведомлений.
        """
        callback = getattr(self, "on_notify", None)
        if callback is not None:
            try:
                callback(
                    ChatMessage(
                        kind=kind,
                        text=text,
                        timestamp=datetime.now(timezone.utc),
                        source_peer=source_peer,
                        conversation_kind=conversation_kind,
                        conversation_id=conversation_id,
                        conversation_title=conversation_title,
                        group_sender_id=group_sender_id,
                        group_content_type=(
                            str(group_content_type)
                            if group_content_type is not None
                            else None
                        ),
                        group_plain_text=group_plain_text,
                    )
                )
            except Exception:
                # Уведомления не должны ломать протокол even if UI callback fails.
                pass

    def _emit_system(self, text: str) -> None:
        if self.on_system:
            self.on_system(text)
        else:
            self._emit_message("system", text)

    def _emit_error(self, text: str) -> None:
        if self.on_error:
            self.on_error(text)
        else:
            self._emit_message("error", text)

    def _emit_peer_changed(self, peer: Optional[str]) -> None:
        if self.on_peer_changed:
            self.on_peer_changed(peer)

    def _emit_file_event(self, info: FileTransferInfo) -> None:
        if self.on_file_event:
            self.on_file_event(info)

    async def _request_file_offer_decision(self, filename: str, size: int) -> bool:
        """Запросить у UI согласие на входящий файл до старта записи на диск."""
        if self.on_file_offer is None:
            return True
        try:
            result = self.on_file_offer(filename, size)
            if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)
        except Exception as e:
            self._emit_error(f"Incoming file decision callback failed: {e}")
            return False

    def _emit_inline_image(self, path: str, is_from_me: bool, sent_filename: Optional[str] = None) -> None:
        if self.on_inline_image_received:
            if sent_filename is not None:
                self.on_inline_image_received(path, is_from_me, sent_filename)
            else:
                self.on_inline_image_received(path, is_from_me)

    def _require_secure_channel(self, *, outbound_peer: Optional[str] = None) -> bool:
        """Проверяет, что можно отправлять пользовательские данные."""
        if outbound_peer and str(outbound_peer).strip():
            try:
                peer_addr_norm = self._normalize_peer_addr(outbound_peer)
            except ValueError:
                self._emit_error("Invalid peer address.")
                return False
        else:
            peer_addr_norm = self._normalize_peer_addr(self.current_peer_addr or "")
        if peer_addr_norm:
            if not self._has_active_session_for_peer(peer_addr_norm):
                self._emit_error("No active connection.")
                return False
            live_kwargs: dict[str, Any] = {"peer_id": peer_addr_norm}
        else:
            if not self.any_live_stream():
                self._emit_error("No active connection.")
                return False
            live_kwargs = {
                "connected": True,
                "handshake_complete": self._handshake_complete_for_peer_route(""),
            }
        if not self.session_manager.is_live_path_alive(**live_kwargs):
            self._emit_error("Secure channel not ready yet. Wait for 'Ready'.")
            return False
        return True

    def _cancel_handshake_watchdog(self, peer_id: Optional[str] = None) -> None:
        # Не отменяем задачу напрямую внутри активной корутины: в некоторых
        # loop-интеграциях (Qt/qasync) это может вызвать re-entrant step Task.
        if peer_id is not None:
            k = self._normalize_peer_addr(peer_id)
            w = self.session_manager.handshake_watchdog_peer_id
            if w != k:
                return
        self.session_manager.invalidate_handshake_watchdog()

    def _start_handshake_watchdog(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        peer_id: Optional[str] = None,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.session_manager.invalidate_handshake_watchdog()
        self.session_manager.handshake_watchdog_peer_id = (
            self._normalize_peer_addr(peer_id)
            if peer_id
            else self._normalize_peer_addr(self.current_peer_addr or "")
        )
        generation = self.session_manager.handshake_watchdog_generation
        self.session_manager.handshake_watchdog_task = loop.create_task(
            self._handshake_watchdog(connection, generation, peer_id)
        )

    def _schedule_disconnect(self, peer_id: Optional[str] = None) -> None:
        if self.session_manager.disconnecting:
            return
        target_pid: Optional[str] = peer_id
        if target_pid is None:
            k = self._normalize_peer_addr(self.current_peer_addr or "")
            if not k or k not in self._live_sessions:
                return
            if self._live_sessions[k].conn is None:
                return
            target_pid = k
        else:
            k = self._normalize_peer_addr(target_pid)
            ls = self._live_sessions.get(k)
            if ls is None or ls.conn is None:
                return
            target_pid = k
        if (
            self.session_manager.disconnect_task is not None
            and not self.session_manager.disconnect_task.done()
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.session_manager.disconnect_task = loop.create_task(
            self.disconnect_peer(target_pid)
        )
        self.session_manager.transition_transport(
            TransportState.RECONNECTING, reason="scheduled-disconnect"
        )

    async def _handshake_watchdog(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        generation: int,
        peer_id: Optional[str] = None,
    ) -> None:
        """Закрывает соединение, если handshake не завершился вовремя."""
        await asyncio.sleep(self.HANDSHAKE_TIMEOUT)
        if generation != self.session_manager.handshake_watchdog_generation:
            return
        if peer_id is not None:
            k = self._normalize_peer_addr(peer_id)
            ls = self._live_sessions.get(k)
            if (
                ls is None
                or ls.conn is not connection
                or ls.handshake_complete
            ):
                return
            self._emit_error("Secure handshake timed out")
            self.session_manager.mark_peer_failed(
                k, reason="handshake-timeout"
            )
            self._schedule_disconnect(k)
            return

    # ---------- протокол ----------

    def _allocate_msg_id(self) -> int:
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        if self._next_msg_id > 0xFFFFFFFFFFFFFFFF:
            self._next_msg_id = 1
        return msg_id

    def _session_for_frame(self, peer_id: Optional[str]) -> Any:
        """Crypto/frame state: LivePeerSession для подключённого пира; иначе поля ядра (до connect)."""
        if peer_id:
            k = self._normalize_peer_addr(peer_id)
            s = self._live_sessions.get(k)
            if s is None:
                raise ValueError(f"No live session for peer_id={peer_id!r}")
            return s
        k = self._normalize_peer_addr(self.current_peer_addr or "")
        if k in self._live_sessions:
            return self._live_sessions[k]
        return self

    def _peer_id_for_frame(self) -> Optional[str]:
        """When sending on the active chat, use extra session crypto if this peer lives there."""
        if not self.current_peer_addr:
            return None
        k = self._normalize_peer_addr(self.current_peer_addr)
        if k in self._live_sessions:
            return k
        return None

    def _writer_frame_peer_and_text_acks(
        self, peer_for_route: str
    ) -> Tuple[Optional[asyncio.StreamWriter], Optional[str], Any]:
        """Writer, peer_id для frame_message*, и таблица pending MSG_ACK для маршрута."""
        try:
            k = self._normalize_peer_addr(peer_for_route) if peer_for_route else ""
        except ValueError:
            k = self._normalize_peer_addr("")
        if k in self._live_sessions:
            ls = self._live_sessions[k]
            if ls.conn:
                return ls.conn[1], k, ls._pending_text_acks
        return None, None, self._pending_text_acks

    def _writer_frame_peer_and_file_acks(
        self, peer_for_route: str
    ) -> Tuple[Optional[asyncio.StreamWriter], Optional[str], Any]:
        try:
            k = self._normalize_peer_addr(peer_for_route) if peer_for_route else ""
        except ValueError:
            k = self._normalize_peer_addr("")
        if k in self._live_sessions:
            ls = self._live_sessions[k]
            if ls.conn:
                return ls.conn[1], k, ls._pending_file_acks
        return None, None, self._pending_file_acks

    def _writer_frame_peer_and_image_acks(
        self, peer_for_route: str
    ) -> Tuple[Optional[asyncio.StreamWriter], Optional[str], Any]:
        try:
            k = self._normalize_peer_addr(peer_for_route) if peer_for_route else ""
        except ValueError:
            k = self._normalize_peer_addr("")
        if k in self._live_sessions:
            ls = self._live_sessions[k]
            if ls.conn:
                return ls.conn[1], k, ls._pending_image_acks
        return None, None, self._pending_image_acks

    def _session_view_for_peer_route(self, peer_for_route: str) -> Any:
        """LivePeerSession для выбранного маршрута (единственный источник live-состояния)."""
        k = self._normalize_peer_addr(peer_for_route)
        if k in self._live_sessions:
            return self._live_sessions[k]
        raise ValueError(f"No live session for peer route {peer_for_route!r}")

    def _handshake_complete_for_peer_route(self, peer_for_route: str) -> bool:
        try:
            k = self._normalize_peer_addr(peer_for_route) if peer_for_route else ""
        except ValueError:
            k = ""
        if not k:
            cur = self._normalize_peer_addr(self.current_peer_addr or "")
            if cur in self._live_sessions:
                return bool(self._live_sessions[cur].handshake_complete)
            return False
        if k in self._live_sessions:
            return bool(self._live_sessions[k].handshake_complete)
        return False

    def _live_stream_count(self) -> int:
        return sum(1 for s in self._live_sessions.values() if s.conn)

    def _has_live_session_slot_for_peer(self, peer_id: str) -> bool:
        k = self._normalize_peer_addr(peer_id)
        return k in self._live_sessions

    def _has_active_session_for_peer(self, peer_id: str) -> bool:
        k = self._normalize_peer_addr(peer_id)
        s = self._live_sessions.get(k)
        return bool(s and s.conn)

    def _prefer_incoming_session(self, peer_id: str) -> bool:
        try:
            peer = self._normalize_peer_addr(peer_id)
        except Exception:
            return True
        local_raw = getattr(self.my_dest, "base32", "") if self.my_dest is not None else ""
        try:
            local = self._normalize_peer_addr(local_raw)
        except Exception:
            local = ""
        if not local or not peer:
            return True
        return local > peer

    def _start_receive_loop_task(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        *,
        peer_id: str,
    ) -> Optional[asyncio.Task[Any]]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        normalized_peer = self._normalize_peer_addr(peer_id)
        task = loop.create_task(self.receive_loop(connection, peer_id=normalized_peer))
        sess = self._live_sessions.get(normalized_peer)
        if sess is not None and sess.conn == connection:
            sess.receive_task = task
        return task

    def has_active_live_session(self, peer_address: str) -> bool:
        """True, если с этим пиром уже есть активный SAM-поток (legacy или extra)."""
        return self._has_active_session_for_peer(peer_address)

    def frame_message_with_id(
        self,
        msg_type: str,
        content: str,
        *,
        force_plain: bool = False,
        peer_id: Optional[str] = None,
    ) -> tuple[bytes, int]:
        """
        Формирует vNext-фрейм:
        MAGIC | VERSION | TYPE | FLAGS | MSG_ID | LEN | PAYLOAD
        """
        body = content.encode("utf-8")
        msg_id = self._allocate_msg_id()
        sess = self._session_for_frame(peer_id)

        if sess.shared_key and sess.use_encryption and not force_plain:
            if not crypto.NACL_AVAILABLE:
                raise RuntimeError("NaCl is required for secure protocol mode")
            sess._send_seq += 1
            seq = sess._send_seq
            mac_key = sess.shared_mac_key or sess.shared_key
            padded_body = self._apply_padding_profile(body)
            encrypted_body = crypto.encrypt_message(sess.shared_key, padded_body)
            mac = crypto.compute_mac(
                mac_key,
                msg_type,
                encrypted_body,
                seq=seq,
                msg_id=msg_id,
                flags=FLAG_ENCRYPTED,
            )
            payload = seq.to_bytes(8, "big", signed=False) + encrypted_body + mac
            return (
                self._codec.encode(
                    msg_type, payload, msg_id=msg_id, flags=FLAG_ENCRYPTED
                ),
                msg_id,
            )

        return self._codec.encode(msg_type, body, msg_id=msg_id, flags=0), msg_id

    def _apply_padding_profile(self, body: bytes) -> bytes:
        if self.padding_profile == PADDING_PROFILE_OFF:
            return body
        wrapped = PADDING_ENVELOPE_MAGIC + len(body).to_bytes(4, "big", signed=False) + body
        target_len = (
            (len(wrapped) + PADDING_BALANCED_BLOCK - 1) // PADDING_BALANCED_BLOCK
        ) * PADDING_BALANCED_BLOCK
        pad_len = target_len - len(wrapped)
        if pad_len <= 0:
            return wrapped
        return wrapped + os.urandom(pad_len)

    def _remove_padding_profile(self, decrypted: bytes) -> bytes:
        if not decrypted.startswith(PADDING_ENVELOPE_MAGIC):
            return decrypted
        header_len = len(PADDING_ENVELOPE_MAGIC) + 4
        if len(decrypted) < header_len:
            raise ValueError("Malformed padded payload header")
        original_len = int.from_bytes(
            decrypted[len(PADDING_ENVELOPE_MAGIC):header_len], "big", signed=False
        )
        payload = decrypted[header_len:]
        if original_len > len(payload):
            raise ValueError("Malformed padded payload length")
        return payload[:original_len]

    def frame_message(
        self, msg_type: str, content: str, *, peer_id: Optional[str] = None
    ) -> bytes:
        frame, _ = self.frame_message_with_id(msg_type, content, peer_id=peer_id)
        return frame

    def frame_message_plain(
        self, msg_type: str, content: str, *, peer_id: Optional[str] = None
    ) -> bytes:
        """Формирует незашифрованный фрейм (handshake/control)."""
        frame, _ = self.frame_message_with_id(
            msg_type, content, force_plain=True, peer_id=peer_id
        )
        return frame

    # ---------- инициализация сессии ----------

    def _profile_scoped_path(self, filename: str) -> str:
        app = get_profiles_dir()
        migrate_legacy_profile_files_if_needed(app_root=app, profile=self.profile)
        base_dir = os.path.abspath(
            get_profile_data_dir(self.profile, create=True, app_root=app)
        )
        base_real = os.path.realpath(base_dir)
        target = os.path.abspath(os.path.join(base_dir, filename))
        if not target.startswith(base_dir + os.sep) and target != base_dir:
            raise ValueError(f"Refusing profile path outside profiles dir: {filename!r}")
        if os.path.lexists(target) and os.path.islink(target):
            raise ValueError(f"Refusing symlinked profile path: {filename!r}")
        target_real = os.path.realpath(target)
        if not (target_real == base_real or target_real.startswith(base_real + os.sep)):
            raise ValueError(f"Refusing profile path outside profiles dir: {filename!r}")
        return target

    def _profile_path(self) -> str:
        """Полный путь к ``<имя>.dat`` внутри ``profiles/<имя>/`` (или legacy-плоский путь до миграции)."""
        return self._profile_scoped_path(f"{self.profile}.dat")

    def _trust_store_path(self) -> str:
        """Файл TOFU pinning для handshake signing keys."""
        return self._profile_scoped_path(f"{self.profile}.trust.json")

    def _signing_seed_path(self) -> str:
        """Файл seed локального signing-key (fallback при недоступном keyring)."""
        return self._profile_scoped_path(f"{self.profile}.signing")

    def get_identity_key_bytes(self) -> Optional[bytes]:
        """Return raw bytes of the I2P identity private key, or None."""
        if self.my_dest is not None and self.my_dest.private_key is not None:
            return self.my_dest.private_key.data
        return None

    def get_profiles_dir(self) -> str:
        """Корневой каталог данных приложения (см. ``get_profiles_dir``)."""
        return get_profiles_dir()

    def get_profile_data_dir(self, *, create: bool = True) -> str:
        """Каталог файлов текущего профиля: ``profiles/<имя>/``."""
        app = get_profiles_dir()
        migrate_legacy_profile_files_if_needed(app_root=app, profile=self.profile)
        return get_profile_data_dir(self.profile, create=create, app_root=app)

    def _local_group_member_id(self) -> str:
        if self.my_dest is None or not getattr(self.my_dest, "base32", ""):
            raise RuntimeError("Local destination is not initialized")
        return normalize_member_id(str(self.my_dest.base32))

    def _load_group_conversation(
        self, group_id: str
    ) -> Optional[StoredGroupConversation]:
        conversation = load_group_conversation(
            self.get_profile_data_dir(create=True),
            self.profile,
            group_id,
        )
        if conversation is not None:
            self.group_manager.prime_group_sequence(
                group_id,
                next_group_seq=conversation.next_group_seq,
            )
        return conversation

    def load_group(self, group_id: str) -> Optional[StoredGroupConversation]:
        return self._load_group_conversation(group_id)

    def save_group(
        self,
        state: GroupState,
        *,
        next_group_seq: Optional[int] = None,
    ) -> StoredGroupConversation:
        conversation = upsert_group_state(
            self.get_profile_data_dir(create=True),
            self.profile,
            state,
            next_group_seq=next_group_seq,
        )
        self.group_manager.prime_group_sequence(
            state.group_id,
            next_group_seq=conversation.next_group_seq,
        )
        return conversation

    @staticmethod
    def _is_recoverable_group_delivery_reason(reason: str) -> bool:
        return str(reason or "").strip() in {
            "blindbox-await-root",
            "needs-live-session",
        }

    @staticmethod
    def _group_delivery_reason_map(
        result: GroupSendResult,
    ) -> dict[str, str]:
        reasons: dict[str, str] = {}
        for peer_id, delivery in result.delivery_results.items():
            reason = str(delivery.reason or "").strip()
            if not reason or reason in {"live-session", "blindbox-ready"}:
                continue
            reasons[peer_id] = reason
        return reasons

    def _save_stored_group_conversation(
        self,
        conversation: StoredGroupConversation,
    ) -> StoredGroupConversation:
        save_group_conversation(
            self.get_profile_data_dir(create=True),
            self.profile,
            conversation,
        )
        self.group_manager.prime_group_sequence(
            conversation.state.group_id,
            next_group_seq=conversation.next_group_seq,
        )
        return conversation

    def _replace_stored_group_conversation(
        self,
        conversation: StoredGroupConversation,
        *,
        state: Optional[GroupState] = None,
        history: Optional[tuple[GroupHistoryEntry, ...]] = None,
        pending_deliveries: Optional[tuple[GroupPendingDelivery, ...]] = None,
        blindbox_channel: Optional[GroupBlindBoxChannel | None] = None,
        pending_group_blindbox_messages: Optional[
            tuple[GroupPendingBlindBoxMessage, ...]
        ] = None,
    ) -> StoredGroupConversation:
        return self._save_stored_group_conversation(
            StoredGroupConversation(
                state=state or conversation.state,
                next_group_seq=conversation.next_group_seq,
                history=history if history is not None else conversation.history,
                seen_msg_ids=conversation.seen_msg_ids,
                pending_deliveries=(
                    pending_deliveries
                    if pending_deliveries is not None
                    else conversation.pending_deliveries
                ),
                blindbox_channel=(
                    blindbox_channel
                    if blindbox_channel is not None
                    else conversation.blindbox_channel
                ),
                pending_group_blindbox_messages=(
                    pending_group_blindbox_messages
                    if pending_group_blindbox_messages is not None
                    else conversation.pending_group_blindbox_messages
                ),
            )
        )

    def _build_group_pending_delivery(
        self,
        state: GroupState,
        envelope: GroupEnvelope,
        delivery: GroupMemberDeliveryResult,
    ) -> GroupPendingDelivery | None:
        delivery_id = str(delivery.delivery_id or "").strip()
        if not delivery_id:
            return None
        recipient_id = normalize_member_id(delivery.recipient_id)
        if not recipient_id:
            return None
        return GroupPendingDelivery(
            group_id=state.group_id,
            group_title=state.title,
            group_members=state.members,
            sender_id=envelope.sender_id,
            recipient_id=recipient_id,
            delivery_id=delivery_id,
            msg_id=str(envelope.msg_id or "").strip(),
            group_seq=int(envelope.group_seq),
            epoch=int(envelope.epoch),
            content_type=envelope.content_type,
            payload=(
                dict(envelope.payload)
                if envelope.content_type == GroupContentType.GROUP_CONTROL
                and isinstance(envelope.payload, dict)
                else envelope.payload
            ),
            created_at=envelope.created_at,
        )

    def _mark_recoverable_group_deliveries_pending(
        self,
        state: GroupState,
        result: GroupSendResult,
    ) -> tuple[GroupPendingDelivery, ...]:
        pending: list[GroupPendingDelivery] = []
        for peer_id, delivery in list(result.delivery_results.items()):
            reason = str(delivery.reason or "").strip()
            if (
                delivery.status != GroupDeliveryStatus.FAILED
                or not self._is_recoverable_group_delivery_reason(reason)
            ):
                continue
            pending_item = self._build_group_pending_delivery(
                state,
                result.envelope,
                delivery,
            )
            if pending_item is None:
                continue
            pending.append(pending_item)
            result.delivery_results[peer_id] = GroupMemberDeliveryResult(
                recipient_id=delivery.recipient_id,
                status=GroupDeliveryStatus.QUEUED_OFFLINE,
                reason=reason,
                transport_message_id=delivery.transport_message_id,
                delivery_id=delivery.delivery_id,
            )
        return tuple(pending)

    def _merge_group_pending_deliveries(
        self,
        conversation: StoredGroupConversation,
        pending: tuple[GroupPendingDelivery, ...],
    ) -> StoredGroupConversation:
        if not pending:
            return conversation
        merged: dict[str, GroupPendingDelivery] = {
            item.delivery_id: item for item in conversation.pending_deliveries
        }
        for item in pending:
            merged[item.delivery_id] = item
        ordered = tuple(
            sorted(
                merged.values(),
                key=lambda item: (
                    item.created_at.timestamp(),
                    item.delivery_id,
                ),
            )
        )
        return self._replace_stored_group_conversation(
            conversation,
            pending_deliveries=ordered,
        )

    def _build_pending_group_blindbox_message(
        self,
        state: GroupState,
        envelope: GroupEnvelope,
        offline_recipients: tuple[str, ...],
    ) -> GroupPendingBlindBoxMessage:
        del offline_recipients
        payload = envelope.payload
        if (
            envelope.content_type == GroupContentType.GROUP_CONTROL
            and isinstance(payload, dict)
        ):
            payload = dict(payload)
        return GroupPendingBlindBoxMessage(
            group_id=state.group_id,
            group_title=state.title,
            group_members=state.members,
            sender_id=envelope.sender_id,
            msg_id=str(envelope.msg_id or "").strip(),
            group_seq=int(envelope.group_seq),
            epoch=int(envelope.epoch),
            content_type=envelope.content_type,
            payload=payload,
            created_at=envelope.created_at,
        )

    def _merge_pending_group_blindbox_messages(
        self,
        conversation: StoredGroupConversation,
        pending: tuple[GroupPendingBlindBoxMessage, ...],
    ) -> StoredGroupConversation:
        if not pending:
            return conversation
        merged: dict[str, GroupPendingBlindBoxMessage] = {
            item.msg_id: item for item in conversation.pending_group_blindbox_messages
        }
        for item in pending:
            if item.msg_id:
                merged[item.msg_id] = item
        ordered = tuple(
            sorted(
                merged.values(),
                key=lambda item: (
                    item.created_at.timestamp(),
                    item.msg_id,
                ),
            )
        )
        return self._replace_stored_group_conversation(
            conversation,
            pending_group_blindbox_messages=ordered,
        )

    def _pending_group_blindbox_recipients(
        self,
        conversation: StoredGroupConversation,
        pending: GroupPendingBlindBoxMessage,
    ) -> tuple[str, ...]:
        for entry in reversed(conversation.history):
            if entry.msg_id != pending.msg_id:
                continue
            recipients = tuple(
                recipient_id
                for recipient_id, status in entry.delivery_results.items()
                if status == GroupDeliveryStatus.QUEUED_OFFLINE.value
                and entry.delivery_reasons.get(recipient_id, "")
                == "blindbox-await-group-root"
            )
            if recipients:
                return recipients
            break
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        return tuple(
            member_id
            for member_id in pending.group_members
            if member_id and not same_i2p_destination(member_id, local_member)
        )

    async def _flush_pending_group_blindbox_messages_for_group(
        self, group_id: str
    ) -> int:
        conversation = self._load_group_conversation(group_id)
        if conversation is None or not conversation.pending_group_blindbox_messages:
            return 0
        remaining: list[GroupPendingBlindBoxMessage] = []
        flushed = 0
        for pending in conversation.pending_group_blindbox_messages:
            result = await self._send_group_envelope_via_group_blindbox(
                group_id,
                pending.as_envelope(),
                state_snapshot=pending.as_group_state(),
            )
            if not result.accepted:
                remaining.append(pending)
                continue
            for recipient_id in self._pending_group_blindbox_recipients(
                conversation,
                pending,
            ):
                conversation = self._update_group_history_delivery_status(
                    conversation,
                    msg_id=pending.msg_id,
                    recipient_id=recipient_id,
                    status=GroupDeliveryStatus.QUEUED_OFFLINE,
                    reason="",
                )
            flushed += 1
        if flushed or len(remaining) != len(conversation.pending_group_blindbox_messages):
            conversation = self._replace_stored_group_conversation(
                conversation,
                pending_group_blindbox_messages=tuple(remaining),
            )
        return flushed

    def _schedule_flush_pending_group_blindbox_messages(
        self, group_id: str
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._flush_pending_group_blindbox_messages_for_group(group_id))

    def _update_group_history_delivery_status(
        self,
        conversation: StoredGroupConversation,
        *,
        msg_id: str,
        recipient_id: str,
        status: GroupDeliveryStatus,
        reason: str = "",
    ) -> StoredGroupConversation:
        normalized_msg_id = str(msg_id or "").strip()
        normalized_recipient = normalize_member_id(recipient_id)
        if not normalized_msg_id or not normalized_recipient:
            return conversation
        updated_history: list[GroupHistoryEntry] = []
        changed = False
        for entry in conversation.history:
            if entry.msg_id != normalized_msg_id:
                updated_history.append(entry)
                continue
            delivery_results = dict(entry.delivery_results)
            delivery_results[normalized_recipient] = status.value
            delivery_reasons = dict(entry.delivery_reasons)
            clean_reason = str(reason or "").strip()
            if clean_reason:
                delivery_reasons[normalized_recipient] = clean_reason
            else:
                delivery_reasons.pop(normalized_recipient, None)
            updated_history.append(
                GroupHistoryEntry(
                    kind=entry.kind,
                    sender_id=entry.sender_id,
                    content_type=entry.content_type,
                    text=entry.text,
                    payload=entry.payload,
                    msg_id=entry.msg_id,
                    group_seq=entry.group_seq,
                    epoch=entry.epoch,
                    created_at=entry.created_at,
                    source_peer=entry.source_peer,
                    delivery_results=delivery_results,
                    delivery_reasons=delivery_reasons,
                )
            )
            changed = True
        if not changed:
            return conversation
        return self._replace_stored_group_conversation(
            conversation,
            history=tuple(updated_history),
        )

    def _schedule_group_pending_flush(self, peer_addrs: list[str]) -> None:
        if not peer_addrs:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for raw in peer_addrs:
            try:
                normalized = self._normalize_peer_addr(raw)
            except Exception:
                continue
            if normalized:
                self._group_pending_flush_backlog.add(normalized)
        self._ensure_group_pending_flush_runner(loop)

    def _ensure_group_pending_flush_runner(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        if (
            self._group_pending_flush_task is not None
            and not self._group_pending_flush_task.done()
        ):
            return
        self._group_pending_flush_task = loop.create_task(
            self._run_group_pending_flush_backlog()
        )

    async def _run_group_pending_flush_backlog(self) -> None:
        try:
            while self._group_pending_flush_backlog:
                batch = sorted(self._group_pending_flush_backlog)
                self._group_pending_flush_backlog.clear()
                for peer_id in batch:
                    await self._flush_pending_group_deliveries_for_peer(peer_id)
        finally:
            self._group_pending_flush_task = None
            if self._group_pending_flush_backlog:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    self._ensure_group_pending_flush_runner(loop)

    async def _flush_pending_group_deliveries_for_peer(self, peer_id: str) -> int:
        normalized_peer = self._normalize_peer_addr(peer_id or "")
        if not normalized_peer:
            return 0
        flushed = 0
        for state in self.list_group_states():
            conversation = self._load_group_conversation(state.group_id)
            if conversation is None or not conversation.pending_deliveries:
                continue
            matching = [
                item
                for item in conversation.pending_deliveries
                if same_i2p_destination(item.recipient_id, normalized_peer)
            ]
            if not matching:
                continue
            remaining = [
                item
                for item in conversation.pending_deliveries
                if not same_i2p_destination(item.recipient_id, normalized_peer)
            ]
            cleared_any = False
            for pending in matching:
                delivery = await self._deliver_group_envelope_to_member(
                    pending.recipient_id,
                    pending.as_envelope(),
                    pending.as_metadata(),
                    state_snapshot=pending.as_group_state(),
                    requested_route="auto",
                )
                if delivery.status == GroupDeliveryStatus.FAILED:
                    remaining.append(pending)
                    continue
                clean_reason = str(delivery.reason or "").strip()
                if clean_reason in {"live-session", "blindbox-ready"}:
                    clean_reason = ""
                conversation = self._update_group_history_delivery_status(
                    conversation,
                    msg_id=pending.msg_id,
                    recipient_id=pending.recipient_id,
                    status=delivery.status,
                    reason=clean_reason,
                )
                flushed += 1
                cleared_any = True
            if cleared_any or len(remaining) != len(conversation.pending_deliveries):
                conversation = self._replace_stored_group_conversation(
                    conversation,
                    pending_deliveries=tuple(remaining),
                )
        return flushed

    async def _deliver_group_envelope_to_member(
        self,
        recipient_id: str,
        envelope: GroupEnvelope,
        metadata: GroupRecipientDeliveryMetadata,
        *,
        requested_route: str = "auto",
        state_snapshot: Optional[GroupState] = None,
    ) -> GroupMemberDeliveryResult:
        policy = self.session_manager.select_outbound_policy(
            requested_route=requested_route,
            peer_id=recipient_id,
        )
        if policy in (
            OutboundPolicy.LIVE_ONLY,
            OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX,
        ):
            live_ready = self.session_manager.is_live_path_alive(peer_id=recipient_id)
            if live_ready:
                live_result = await self._send_group_envelope_live(
                    recipient_id,
                    envelope,
                    metadata,
                    state_snapshot=state_snapshot,
                )
                if live_result.accepted:
                    return GroupMemberDeliveryResult(
                        recipient_id=recipient_id,
                        status=GroupDeliveryStatus.DELIVERED_LIVE,
                        reason=live_result.reason or "live-session",
                        transport_message_id=live_result.transport_message_id,
                        delivery_id=metadata.delivery_id,
                    )
                if policy == OutboundPolicy.LIVE_ONLY:
                    return GroupMemberDeliveryResult(
                        recipient_id=recipient_id,
                        status=GroupDeliveryStatus.FAILED,
                        reason=live_result.reason or "needs-live-session",
                        transport_message_id=live_result.transport_message_id,
                        delivery_id=metadata.delivery_id,
                    )
            elif policy == OutboundPolicy.LIVE_ONLY:
                return GroupMemberDeliveryResult(
                    recipient_id=recipient_id,
                    status=GroupDeliveryStatus.FAILED,
                    reason="needs-live-session",
                    delivery_id=metadata.delivery_id,
                )

        offline_result = await self._send_group_envelope_via_blindbox(
            recipient_id,
            envelope,
            metadata,
            state_snapshot=state_snapshot,
        )
        if offline_result.accepted:
            return GroupMemberDeliveryResult(
                recipient_id=recipient_id,
                status=GroupDeliveryStatus.QUEUED_OFFLINE,
                reason=offline_result.reason or "blindbox-ready",
                transport_message_id=offline_result.transport_message_id,
                delivery_id=metadata.delivery_id,
            )
        return GroupMemberDeliveryResult(
            recipient_id=recipient_id,
            status=GroupDeliveryStatus.FAILED,
            reason=offline_result.reason or "blindbox-unavailable",
            transport_message_id=offline_result.transport_message_id,
            delivery_id=metadata.delivery_id,
        )

    def load_group_state(self, group_id: str) -> Optional[GroupState]:
        conversation = self.load_group(group_id)
        if conversation is not None:
            return conversation.state
        return load_persisted_group_state(
            self.get_profile_data_dir(create=True),
            self.profile,
            group_id,
        )

    def save_group_state(
        self,
        state: GroupState,
        *,
        next_group_seq: Optional[int] = None,
    ) -> GroupState:
        return self.save_group(
            state,
            next_group_seq=next_group_seq,
        ).state

    def list_group_states(self) -> list[GroupState]:
        return list_persisted_group_states(
            self.get_profile_data_dir(create=True),
            self.profile,
        )

    def delete_group(self, group_id: str) -> bool:
        """Remove local group record and history file; does not notify remote members."""
        gid = (group_id or "").strip()
        if not gid:
            return False
        ok = delete_group_record(
            self.get_profile_data_dir(create=True),
            self.profile,
            gid,
        )
        if ok:
            self.group_manager.forget_group(gid)
            self._notify_group_mesh_manager()
        return ok

    def _group_auto_intro_enabled(self) -> bool:
        v = os.environ.get("I2PCHAT_GROUP_AUTO_INTRO", "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    def _notify_group_mesh_manager(self) -> None:
        if self._group_mesh_task is None or self._group_mesh_task.done():
            return
        self.group_mesh_manager.request_scan()

    def _build_group_mesh_peer_snapshot(self, peer_id: str) -> GroupMeshPeerSnapshot:
        normalized_peer = self._normalize_peer_addr(peer_id or "")
        peer_transport = self.session_manager.get_peer_transport(normalized_peer)
        peer_state = (
            peer_transport.peer_state.value
            if peer_transport is not None
            else "disconnected"
        )
        blindbox_ready = False
        if self.blindbox_enabled:
            try:
                peer_snapshot = self._load_blindbox_peer_snapshot(normalized_peer)
            except Exception:
                blindbox_ready = False
            else:
                blindbox_ready = peer_snapshot.root_secret is not None
        reconnect = self.session_manager.get_reconnect_metadata(peer_id=normalized_peer)
        return GroupMeshPeerSnapshot(
            peer_id=normalized_peer,
            peer_state=peer_state,
            live_ready=self.session_manager.is_live_path_alive(peer_id=normalized_peer),
            active_session=self._has_active_session_for_peer(normalized_peer),
            blindbox_ready=blindbox_ready,
            next_retry_mono=float(reconnect.next_retry_mono),
        )

    def _ensure_group_mesh_runner(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self.group_mesh_manager.enabled():
            return
        if self._group_mesh_task is not None and not self._group_mesh_task.done():
            return
        self.group_mesh_manager.request_scan()
        self._group_mesh_task = loop.create_task(self.group_mesh_manager.run())

    def _repair_group_delivery_failures(self, result: GroupSendResult) -> None:
        repair_peers: list[str] = []
        for peer_id, delivery in result.delivery_results.items():
            reason = str(delivery.reason or "").strip()
            if delivery.status == GroupDeliveryStatus.FAILED and reason in {
                "blindbox-await-root",
                "needs-live-session",
            }:
                repair_peers.append(peer_id)
        if repair_peers:
            self._schedule_group_peer_intros(repair_peers)
        self._notify_group_mesh_manager()

    def _sync_group_members_to_saved_contacts(self, state: GroupState, local_member: str) -> bool:
        """Add all remote group members to Saved peers. Returns True if contacts file changed."""
        changed = False
        for m in state.members:
            if not m or same_i2p_destination(m, local_member):
                continue
            if self.ensure_peer_in_saved_contacts(m):
                changed = True
        return changed

    def _collect_group_peers_needing_secure_intro(
        self,
        new_state: GroupState,
        local_member: str,
    ) -> list[str]:
        """
        Все удалённые участники группы, с которыми ещё нет завершённого secure handshake.

        В отличие от выборки только «новых» member (added), это даёт полный mesh:
        при каждом событии членства/импорте можно повторно попытаться достроить live-сессии.
        """
        out: list[str] = []
        for mid in new_state.members:
            if not mid or same_i2p_destination(mid, local_member):
                continue
            try:
                n = self._normalize_peer_addr(mid)
            except Exception:
                continue
            if not n:
                continue
            if self._handshake_complete_for_peer_route(n):
                continue
            if self._has_live_session_slot_for_peer(n):
                continue
            out.append(mid)
        return sorted(out, key=lambda x: normalize_member_id(x))

    def _on_group_membership_changed(
        self,
        previous_members: frozenset[str],
        previous_epoch: int | None,
        new_state: GroupState,
        *,
        group_label: str = "",
    ) -> None:
        """
        При изменении состава или импорте группы: сохранить пиров в контакты,
        затем по возможности достроить live-сессии ко всем участникам (mesh).
        """
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        if self._legacy_group_blindbox_outbound_enabled():
            self._on_group_blindbox_membership_changed(
                previous_members,
                previous_epoch,
                new_state,
            )
        try:
            local_member = self._local_group_member_id()
        except Exception:
            return
        if self._sync_group_members_to_saved_contacts(new_state, local_member):
            if self.on_saved_contacts_changed is not None:
                try:
                    self.on_saved_contacts_changed()
                except Exception:
                    logger.debug("on_saved_contacts_changed failed", exc_info=True)
        intro = self._collect_group_peers_needing_secure_intro(
            new_state, local_member
        )
        self._notify_group_mesh_manager()
        if not intro:
            return
        prev = {normalize_member_id(x) for x in previous_members if x}
        cur = {normalize_member_id(x) for x in new_state.members if x}
        added = cur - prev
        intro_norm = {normalize_member_id(x) for x in intro}
        new_member_intros = sorted(intro_norm & added)
        label = (group_label or new_state.title or new_state.group_id or "group").strip()
        if new_member_intros:
            short = ", ".join(
                f"{p[:10]}…" if len(p) > 12 else p for p in new_member_intros[:4]
            )
            if len(new_member_intros) > 4:
                short += ", …"
            self._emit_system(
                f'Group "{label}": secure introduction starting for new peer(s): {short}'
            )
        else:
            logger.info(
                'Group "%s": mesh top-up, scheduling live session(s) to %d peer(s)',
                label,
                len(intro),
            )
        self._schedule_group_peer_intros(intro)
        if self._legacy_group_blindbox_outbound_enabled():
            self._schedule_group_blindbox_root_push(new_state)

    def _on_group_blindbox_membership_changed(
        self,
        previous_members: frozenset[str],
        previous_epoch: int | None,
        new_state: GroupState,
    ) -> None:
        snapshot_bundle = self._group_blindbox_runtime_snapshot(new_state.group_id)
        if snapshot_bundle is None:
            return
        snapshot, save_state = snapshot_bundle
        current_members = frozenset(normalize_member_id(x) for x in new_state.members if x)
        previous_members_norm = frozenset(
            normalize_member_id(x) for x in previous_members if x
        )
        members_changed = previous_members_norm != current_members
        epoch_changed = (
            previous_epoch is None or int(previous_epoch) != int(new_state.epoch)
        )
        if (
            not members_changed
            and not epoch_changed
            and int(snapshot.group_epoch) == int(new_state.epoch)
        ):
            return
        if snapshot.root_secret is not None:
            snapshot.prev_roots.append(
                {
                    "group_epoch": int(snapshot.group_epoch),
                    "root_epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                    "expires_at": int(time.time())
                    + int(self._blindbox_previous_grace_seconds),
                }
            )
        snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
            snapshot.prev_roots
        )
        snapshot.group_epoch = int(new_state.epoch)
        snapshot.root_secret = None
        snapshot.root_epoch = 0
        snapshot.root_created_at = 0
        snapshot.root_send_index_base = int(snapshot.state.send_index)
        snapshot.pending_root_secret = None
        snapshot.pending_root_epoch = 0
        snapshot.pending_root_created_at = 0
        snapshot.pending_root_send_index_base = int(snapshot.state.send_index)
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        snapshot.pending_root_target_members = tuple(
            member_id
            for member_id in new_state.members
            if member_id and not same_i2p_destination(member_id, local_member)
        )
        snapshot.pending_root_acked_members.clear()
        save_state()

    def _schedule_group_blindbox_root_push(self, state: GroupState) -> None:
        if not self._legacy_group_blindbox_outbound_enabled():
            return
        if not self._should_initiate_group_blindbox_root_exchange(state):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for member_id in self._group_blindbox_target_members(state):
            writer, frame_peer_id, _text_acks = self._writer_frame_peer_and_text_acks(
                member_id
            )
            if (
                writer is None
                or frame_peer_id is None
                or not self.session_manager.is_live_path_alive(peer_id=member_id)
            ):
                continue
            loop.create_task(
                self._send_group_blindbox_root_if_needed(
                    writer,
                    state.group_id,
                    peer_id=frame_peer_id,
                )
            )

    def _schedule_group_peer_intros(self, peer_addrs: list[str]) -> None:
        if not peer_addrs or not self._group_auto_intro_enabled():
            return
        if not crypto.NACL_AVAILABLE:
            return
        # Unit tests often have a running asyncio loop; never open real SAM connects there.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for raw in peer_addrs:
            try:
                n = self._normalize_peer_addr(raw)
            except Exception:
                continue
            if n:
                self._group_intro_backlog.add(n)
        self._ensure_group_intro_runner(loop)

    def _ensure_group_intro_runner(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._group_intro_task is not None and not self._group_intro_task.done():
            return
        self._group_intro_task = loop.create_task(self._run_group_intro_backlog())

    async def _run_group_intro_backlog(self) -> None:
        try:
            while self._group_intro_backlog:
                batch = sorted(self._group_intro_backlog)
                self._group_intro_backlog.clear()
                for n in batch:
                    if self._handshake_complete_for_peer_route(n):
                        continue
                    if self._has_live_session_slot_for_peer(n):
                        continue
                    activate = self._live_stream_count() == 0
                    await self.connect_to_peer(
                        n,
                        activate_as_current=activate,
                        announce_to_ui=False,
                    )
        finally:
            self._group_intro_task = None
            if self._group_intro_backlog:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    self._ensure_group_intro_runner(loop)

    def load_group_history(self, group_id: str) -> list[GroupHistoryEntry]:
        conversation = self.load_group(group_id)
        if conversation is None:
            return []
        return list(conversation.history)

    def create_group(
        self,
        *,
        title: str,
        members: list[str] | tuple[str, ...],
        group_id: Optional[str] = None,
        epoch: int = 0,
    ) -> GroupState:
        local_member_id = self._local_group_member_id()
        normalized_members = [local_member_id]
        normalized_members.extend(normalize_member_id(member) for member in members)
        state = GroupState(
            group_id=(group_id or secrets.token_hex(12)),
            epoch=int(epoch),
            members=tuple(normalized_members),
            title=title,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        state = self.save_group(
            state,
            next_group_seq=1,
        ).state
        self._emit_system(
            f"Group ready: {state.title or state.group_id} ({max(0, len(state.members) - 1)} peers)."
        )
        self._on_group_membership_changed(
            frozenset(),
            None,
            state,
            group_label=state.title or state.group_id,
        )
        return state

    def update_group(
        self,
        group_id: str,
        *,
        title: str,
        members: list[str] | tuple[str, ...],
    ) -> GroupState:
        """
        Локально обновить заголовок и список участников (как display name у saved peer).
        Увеличивает epoch; next_group_seq и история сохраняются.
        """
        existing = self.load_group_state(group_id)
        if existing is None:
            raise ValueError(f"Unknown group: {group_id}")
        prev_members = frozenset(existing.members)
        local_member_id = self._local_group_member_id()
        normalized_members = [local_member_id]
        normalized_members.extend(normalize_member_id(member) for member in members)
        clean_title = (title or "").strip()
        new_state = GroupState(
            group_id=existing.group_id,
            epoch=int(existing.epoch) + 1,
            members=tuple(normalized_members),
            title=clean_title or None,
            created_at=existing.created_at,
            updated_at=utc_now(),
        )
        saved = self.save_group(new_state, next_group_seq=None)
        self._emit_system(
            f"Group updated: {saved.state.title or saved.state.group_id} "
            f"({max(0, len(saved.state.members) - 1)} peers)."
        )
        self._on_group_membership_changed(
            prev_members,
            int(existing.epoch),
            saved.state,
            group_label=saved.state.title or saved.state.group_id,
        )
        return saved.state

    def _group_display_name(self, state: GroupState) -> str:
        return state.title or state.group_id

    def _blindbox_peer_id(self) -> Optional[str]:
        lap = self._last_active_peer_for_telemetry()
        peer = self._normalize_peer_addr(self.current_peer_addr or lap or "")
        if not peer:
            return None
        return peer

    def _blindbox_peer_id_for_peer(self, peer_addr: str) -> Optional[str]:
        peer = self._normalize_peer_addr(peer_addr or "")
        if not peer:
            return None
        return peer

    def _blindbox_state_path_for_peer(self, peer_id: str) -> str:
        safe_peer = re.sub(r"[^a-z0-9._-]", "_", peer_id.lower())
        return self._profile_scoped_path(f"{self.profile}.blindbox.{safe_peer}.json")

    def _blindbox_state_path(self) -> str:
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            raise ValueError("BlindBox peer id is not available")
        safe_peer = re.sub(r"[^a-z0-9._-]", "_", peer_id.lower())
        return self._profile_scoped_path(f"{self.profile}.blindbox.{safe_peer}.json")

    def _load_blindbox_peer_snapshot(self, peer_addr: str) -> _BlindBoxPeerSnapshot:
        peer = self._normalize_peer_addr(peer_addr or "")
        peer_id = self._blindbox_peer_id_for_peer(peer)
        if not peer_id:
            raise ValueError("BlindBox peer id is not available")
        snapshot = _BlindBoxPeerSnapshot(
            peer_addr=peer,
            peer_id=peer_id,
            state=BlindBoxState(),
        )
        path = self._blindbox_state_path_for_peer(peer_id)
        if not os.path.exists(path):
            return snapshot
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("BlindBox state must be a JSON object")
        snapshot.state = BlindBoxState.from_dict(raw)
        wrap_version_raw = raw.get(
            "blindbox_wrap_version", BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
        )
        try:
            wrap_version = int(wrap_version_raw)
        except Exception:
            wrap_version = BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
        enc_root = raw.get("blindbox_root_secret_enc")
        if isinstance(enc_root, str) and enc_root:
            snapshot.root_secret, _used_wrap_version = self._blindbox_decrypt_root_secret(
                enc_root,
                peer_id,
                wrap_version=wrap_version,
            )
        snapshot.root_epoch = int(raw.get("blindbox_root_epoch", 0))
        snapshot.root_created_at = int(
            raw.get("blindbox_root_created_at", int(time.time()))
        )
        snapshot.root_send_index_base = int(
            raw.get("blindbox_root_send_index_base", int(snapshot.state.send_index))
        )
        enc_pending_root = raw.get("blindbox_pending_root_secret_enc")
        if isinstance(enc_pending_root, str) and enc_pending_root:
            snapshot.pending_root_secret, _pending_wrap_version = (
                self._blindbox_decrypt_root_secret(
                    enc_pending_root,
                    peer_id,
                    wrap_version=wrap_version,
                )
            )
        snapshot.pending_root_epoch = int(raw.get("blindbox_pending_root_epoch", 0))
        snapshot.pending_root_created_at = int(
            raw.get("blindbox_pending_root_created_at", int(time.time()))
        )
        snapshot.pending_root_send_index_base = int(
            raw.get(
                "blindbox_pending_root_send_index_base",
                int(snapshot.state.send_index),
            )
        )
        prev_items = raw.get("blindbox_prev_roots", [])
        if isinstance(prev_items, list):
            for prev in prev_items:
                if not isinstance(prev, dict):
                    continue
                enc_prev = prev.get("secret_enc")
                if not isinstance(enc_prev, str) or not enc_prev:
                    continue
                try:
                    dec_prev, _prev_wrap_version = self._blindbox_decrypt_root_secret(
                        enc_prev, peer_id, wrap_version=wrap_version
                    )
                except Exception:
                    continue
                if len(dec_prev) != 32:
                    continue
                snapshot.prev_roots.append(
                    {
                        "epoch": int(prev.get("epoch", 0)),
                        "secret": dec_prev,
                        "expires_at": int(prev.get("expires_at", 0)),
                    }
                )
        snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
            snapshot.prev_roots
        )
        return snapshot

    def _save_blindbox_peer_snapshot(self, snapshot: _BlindBoxPeerSnapshot) -> None:
        if snapshot.root_secret is None and snapshot.pending_root_secret is None:
            return
        payload = snapshot.state.to_dict()
        payload["blindbox_wrap_version"] = BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
        if snapshot.root_secret is not None:
            payload["blindbox_root_secret_enc"] = self._blindbox_encrypt_root_secret(
                snapshot.root_secret,
                snapshot.peer_id,
            )
        payload["blindbox_root_epoch"] = int(snapshot.root_epoch)
        payload["blindbox_root_created_at"] = int(snapshot.root_created_at)
        payload["blindbox_root_send_index_base"] = int(snapshot.root_send_index_base)
        if snapshot.pending_root_secret is not None:
            payload["blindbox_pending_root_secret_enc"] = self._blindbox_encrypt_root_secret(
                snapshot.pending_root_secret,
                snapshot.peer_id,
            )
        payload["blindbox_pending_root_epoch"] = int(snapshot.pending_root_epoch)
        payload["blindbox_pending_root_created_at"] = int(
            snapshot.pending_root_created_at
        )
        payload["blindbox_pending_root_send_index_base"] = int(
            snapshot.pending_root_send_index_base
        )
        payload["blindbox_prev_roots"] = [
            {
                "epoch": int(item.get("epoch", 0)),
                "expires_at": int(item.get("expires_at", 0)),
                "secret_enc": self._blindbox_encrypt_root_secret(
                    bytes(item["secret"]), snapshot.peer_id
                ),
            }
            for item in self._blindbox_prune_previous_roots_list(snapshot.prev_roots)
            if isinstance(item.get("secret"), (bytes, bytearray))
            and len(bytes(item["secret"])) == 32
        ]
        atomic_write_json(
            self._blindbox_state_path_for_peer(snapshot.peer_id),
            payload,
        )

    def _blindbox_local_wrap_key(
        self,
        peer_id: str,
        *,
        wrap_version: int = BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
    ) -> bytes:
        if not self.my_signing_seed:
            raise ValueError("Local signing seed is not initialized")
        peer_norm = (peer_id or "").strip().lower()
        if not peer_norm:
            raise ValueError("BlindBox peer id is not available")
        if peer_norm.endswith(".b32.i2p"):
            peer_norm = peer_norm[: -len(".b32.i2p")]
        profile_bytes = self.profile.encode("utf-8")
        peer_bytes = peer_norm.encode("utf-8")
        if wrap_version == BLINDBOX_LOCAL_WRAP_VERSION_LEGACY:
            salt = crypto.hkdf_extract(
                b"",
                hashlib.sha256(
                    b"BLINDBOX-LOCAL-WRAP-SALT|" + profile_bytes + b"|" + peer_bytes
                ).digest(),
            )
            return crypto.hkdf_expand(salt, b"BLINDBOX-LOCAL-WRAP-KEY", 32)
        if wrap_version != BLINDBOX_LOCAL_WRAP_VERSION_CURRENT:
            raise ValueError(f"Unsupported BlindBox local wrap version: {wrap_version}")
        salt = hashlib.sha256(
            b"BLINDBOX-LOCAL-WRAP-SALT-V2|" + profile_bytes + b"|" + peer_bytes
        ).digest()
        prk = crypto.hkdf_extract(salt, self.my_signing_seed)
        return crypto.hkdf_expand(
            prk,
            b"BLINDBOX-LOCAL-WRAP-KEY-V2|" + profile_bytes + b"|" + peer_bytes,
            32,
        )

    def _blindbox_encrypt_root_secret(self, root_secret: bytes, peer_id: str) -> str:
        wrap_key = self._blindbox_local_wrap_key(
            peer_id, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
        )
        encrypted = crypto.encrypt_message(wrap_key, root_secret)
        return encrypted.hex()

    def _blindbox_decrypt_root_secret(
        self,
        encrypted_hex: str,
        peer_id: str,
        *,
        wrap_version: Optional[int] = None,
    ) -> tuple[bytes, int]:
        encrypted = bytes.fromhex(encrypted_hex)
        versions: list[int] = []
        if wrap_version is not None:
            versions.append(int(wrap_version))
        versions.extend(
            [
                BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
                BLINDBOX_LOCAL_WRAP_VERSION_LEGACY,
            ]
        )
        seen: set[int] = set()
        for version in versions:
            if version in seen:
                continue
            seen.add(version)
            try:
                wrap_key = self._blindbox_local_wrap_key(peer_id, wrap_version=version)
            except Exception:
                continue
            decrypted = crypto.decrypt_message(wrap_key, encrypted)
            if decrypted is not None:
                return decrypted, version
        raise ValueError("Failed to decrypt BlindBox root secret")

    def _group_blindbox_wrap_scope(self, group_id: str) -> str:
        group_key = str(group_id or "").strip()
        if not group_key:
            raise ValueError("Group id is required")
        return f"group:{group_key}"

    def _group_blindbox_encrypt_root_secret(
        self, root_secret: bytes, group_id: str
    ) -> str:
        wrap_scope = self._group_blindbox_wrap_scope(group_id)
        wrap_key = self._blindbox_local_wrap_key(
            wrap_scope,
            wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
        )
        encrypted = crypto.encrypt_message(wrap_key, root_secret)
        return encrypted.hex()

    def _group_blindbox_decrypt_root_secret(
        self, encrypted_hex: str, group_id: str
    ) -> bytes:
        encrypted = bytes.fromhex(encrypted_hex)
        wrap_scope = self._group_blindbox_wrap_scope(group_id)
        for version in (
            BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
            BLINDBOX_LOCAL_WRAP_VERSION_LEGACY,
        ):
            try:
                wrap_key = self._blindbox_local_wrap_key(
                    wrap_scope,
                    wrap_version=version,
                )
            except Exception:
                continue
            decrypted = crypto.decrypt_message(wrap_key, encrypted)
            if decrypted is not None:
                return decrypted
        raise ValueError("Failed to decrypt group BlindBox root secret")

    def _load_group_blindbox_channel(
        self, group_id: str
    ) -> GroupBlindBoxChannel | None:
        conversation = self._load_group_conversation(group_id)
        if conversation is None:
            return None
        channel = conversation.blindbox_channel
        if channel is None:
            return None
        return channel

    def _save_group_blindbox_channel(
        self,
        group_id: str,
        channel: GroupBlindBoxChannel,
    ) -> None:
        conversation = self._load_group_conversation(group_id)
        if conversation is None:
            raise ValueError(f"Unknown group: {group_id}")
        self._replace_stored_group_conversation(
            conversation,
            blindbox_channel=channel,
        )

    def _group_blindbox_runtime_snapshot(
        self, group_id: str
    ) -> tuple[_GroupBlindBoxSnapshot, Callable[[], None]] | None:
        conversation = self._load_group_conversation(group_id)
        if conversation is None:
            return None
        state = conversation.state
        channel = conversation.blindbox_channel
        if channel is None:
            snapshot = _GroupBlindBoxSnapshot(
                group_id=group_id,
                group_epoch=int(state.epoch),
                state=BlindBoxState(),
            )
        else:
            root_secret = None
            if channel.root_secret_enc:
                root_secret = self._group_blindbox_decrypt_root_secret(
                    channel.root_secret_enc,
                    group_id,
                )
            pending_root_secret = None
            if channel.pending_root_secret_enc:
                pending_root_secret = self._group_blindbox_decrypt_root_secret(
                    channel.pending_root_secret_enc,
                    group_id,
                )
            prev_roots: list[dict[str, Any]] = []
            for item in channel.prev_roots:
                secret_enc = str(item.get("secret_enc") or "").strip()
                if not secret_enc:
                    continue
                try:
                    secret = self._group_blindbox_decrypt_root_secret(
                        secret_enc,
                        group_id,
                    )
                except Exception:
                    continue
                if len(secret) != 32:
                    continue
                prev_roots.append(
                    {
                        "group_epoch": int(item.get("group_epoch", 0)),
                        "root_epoch": int(item.get("root_epoch", 0)),
                        "expires_at": int(item.get("expires_at", 0)),
                        "secret": secret,
                    }
                )
            snapshot = _GroupBlindBoxSnapshot(
                group_id=group_id,
                group_epoch=int(channel.group_epoch or state.epoch),
                state=BlindBoxState.from_dict(channel.state.to_dict()),
                root_secret=root_secret,
                root_epoch=int(channel.root_epoch),
                root_created_at=int(channel.root_created_at),
                root_send_index_base=int(channel.root_send_index_base),
                pending_root_secret=pending_root_secret,
                pending_root_epoch=int(channel.pending_root_epoch),
                pending_root_created_at=int(channel.pending_root_created_at),
                pending_root_send_index_base=int(channel.pending_root_send_index_base),
                pending_root_target_members=tuple(channel.pending_root_target_members),
                pending_root_acked_members=set(channel.pending_root_acked_members),
                prev_roots=self._blindbox_prune_previous_roots_list(prev_roots),
            )

        def _save_group_snapshot() -> None:
            serialized_prev_roots = tuple(
                {
                    "group_epoch": int(item.get("group_epoch", snapshot.group_epoch)),
                    "root_epoch": int(item.get("root_epoch", 0)),
                    "expires_at": int(item.get("expires_at", 0)),
                    "secret_enc": self._group_blindbox_encrypt_root_secret(
                        bytes(item["secret"]),
                        group_id,
                    ),
                }
                for item in self._blindbox_prune_previous_roots_list(
                    list(snapshot.prev_roots)
                )
                if isinstance(item.get("secret"), (bytes, bytearray))
                and len(bytes(item["secret"])) == 32
            )
            self._save_group_blindbox_channel(
                group_id,
                GroupBlindBoxChannel(
                    channel_id=self._group_blindbox_wrap_scope(group_id),
                    group_epoch=int(snapshot.group_epoch),
                    state=BlindBoxState.from_dict(snapshot.state.to_dict()),
                    root_secret_enc=(
                        self._group_blindbox_encrypt_root_secret(
                            snapshot.root_secret,
                            group_id,
                        )
                        if snapshot.root_secret is not None
                        else None
                    ),
                    root_epoch=int(snapshot.root_epoch),
                    root_created_at=int(snapshot.root_created_at),
                    root_send_index_base=int(snapshot.root_send_index_base),
                    pending_root_secret_enc=(
                        self._group_blindbox_encrypt_root_secret(
                            snapshot.pending_root_secret,
                            group_id,
                        )
                        if snapshot.pending_root_secret is not None
                        else None
                    ),
                    pending_root_epoch=int(snapshot.pending_root_epoch),
                    pending_root_created_at=int(snapshot.pending_root_created_at),
                    pending_root_send_index_base=int(
                        snapshot.pending_root_send_index_base
                    ),
                    pending_root_target_members=tuple(
                        snapshot.pending_root_target_members
                    ),
                    pending_root_acked_members=tuple(
                        sorted(snapshot.pending_root_acked_members)
                    ),
                    prev_roots=serialized_prev_roots,
                ),
            )

        return snapshot, _save_group_snapshot

    def _blindbox_prune_previous_roots(self) -> None:
        self._blindbox_prev_roots = self._blindbox_prune_previous_roots_list(
            self._blindbox_prev_roots
        )

    def _blindbox_prune_previous_roots_list(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        now_ts = int(time.time())
        filtered = [
            item
            for item in items
            if int(item.get("expires_at", 0)) > now_ts
        ]
        filtered.sort(key=lambda item: int(item.get("epoch", 0)), reverse=True)
        if self._blindbox_max_previous_roots >= 0:
            filtered = filtered[: self._blindbox_max_previous_roots]
        return filtered

    def _blindbox_root_candidates(self) -> list[dict[str, Any]]:
        self._blindbox_prune_previous_roots()
        candidates: list[dict[str, Any]] = []
        if self._blindbox_root_secret is not None:
            candidates.append(
                {
                    "epoch": int(self._blindbox_root_epoch),
                    "secret": self._blindbox_root_secret,
                }
            )
        for item in self._blindbox_prev_roots:
            secret = item.get("secret")
            if isinstance(secret, (bytes, bytearray)) and len(secret) == 32:
                candidates.append(
                    {
                        "epoch": int(item.get("epoch", 0)),
                        "secret": bytes(secret),
                    }
                )
        return candidates

    def _blindbox_root_candidates_for_snapshot(
        self,
        snapshot: _BlindBoxPeerSnapshot,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if snapshot.root_secret is not None:
            candidates.append(
                {
                    "epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                }
            )
        for item in self._blindbox_prune_previous_roots_list(snapshot.prev_roots):
            secret = item.get("secret")
            if isinstance(secret, (bytes, bytearray)) and len(secret) == 32:
                candidates.append(
                    {
                        "epoch": int(item.get("epoch", 0)),
                        "secret": bytes(secret),
                    }
                )
        return candidates

    def _group_blindbox_root_candidates(
        self,
        snapshot: _GroupBlindBoxSnapshot,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if snapshot.root_secret is not None:
            candidates.append(
                {
                    "group_epoch": int(snapshot.group_epoch),
                    "root_epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                }
            )
        for item in self._blindbox_prune_previous_roots_list(snapshot.prev_roots):
            secret = item.get("secret")
            if isinstance(secret, (bytes, bytearray)) and len(secret) == 32:
                candidates.append(
                    {
                        "group_epoch": int(
                            item.get("group_epoch", snapshot.group_epoch)
                        ),
                        "root_epoch": int(item.get("root_epoch", 0)),
                        "secret": bytes(secret),
                    }
                )
        return candidates

    def _blindbox_send_snapshot_for_peer(
        self, peer_addr: str
    ) -> tuple[_BlindBoxPeerSnapshot, Callable[[], None]] | None:
        return self._blindbox_runtime_snapshot_for_peer(peer_addr)

    def _blindbox_runtime_snapshot_for_peer(
        self, peer_addr: str
    ) -> tuple[_BlindBoxPeerSnapshot, Callable[[], None]] | None:
        peer_id = self._blindbox_peer_id_for_peer(peer_addr)
        if not peer_id:
            return None
        current_peer = self._blindbox_peer_id()
        if current_peer and same_i2p_destination(peer_id, current_peer):
            snapshot = _BlindBoxPeerSnapshot(
                peer_addr=peer_id,
                peer_id=peer_id,
                state=self._blindbox_state,
                root_secret=self._blindbox_root_secret,
                root_epoch=int(self._blindbox_root_epoch),
                root_created_at=int(self._blindbox_root_created_at),
                root_send_index_base=int(self._blindbox_root_send_index_base),
                pending_root_secret=self._blindbox_pending_root_secret,
                pending_root_epoch=int(self._blindbox_pending_root_epoch),
                pending_root_created_at=int(self._blindbox_pending_root_created_at),
                pending_root_send_index_base=int(
                    self._blindbox_pending_root_send_index_base
                ),
                prev_roots=list(self._blindbox_prev_roots),
            )
            def _save_current_snapshot() -> None:
                self._blindbox_state = snapshot.state
                self._blindbox_root_secret = snapshot.root_secret
                self._blindbox_root_epoch = int(snapshot.root_epoch)
                self._blindbox_root_created_at = int(snapshot.root_created_at)
                self._blindbox_root_send_index_base = int(
                    snapshot.root_send_index_base
                )
                self._blindbox_pending_root_secret = snapshot.pending_root_secret
                self._blindbox_pending_root_epoch = int(snapshot.pending_root_epoch)
                self._blindbox_pending_root_created_at = int(
                    snapshot.pending_root_created_at
                )
                self._blindbox_pending_root_send_index_base = int(
                    snapshot.pending_root_send_index_base
                )
                self._blindbox_prev_roots = list(snapshot.prev_roots)
                self._save_blindbox_state()

            return snapshot, _save_current_snapshot
        snapshot = self._load_blindbox_peer_snapshot(peer_id)
        return snapshot, lambda: self._save_blindbox_peer_snapshot(snapshot)

    def _blindbox_recv_candidates_for_state(self, state: BlindBoxState) -> list[int]:
        recv_backtrack = max(
            0, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_BACKTRACK", "0"))
        )
        recv_lookahead = max(
            0, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD", "64"))
        )
        recv_max_per_poll = max(
            1, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_MAX_PER_POLL", "64"))
        )
        recv_start = max(0, state.recv_base - recv_backtrack)
        recv_span = max(state.recv_window, recv_lookahead)
        recv_end = state.recv_base + recv_span
        forward = (
            idx
            for idx in range(state.recv_base, recv_end)
            if idx not in state.consumed_recv
        )
        backtrack = (
            idx
            for idx in range(recv_start, state.recv_base)
            if idx not in state.consumed_recv
        )
        ordered = [*forward, *backtrack]
        if len(ordered) > recv_max_per_poll:
            return ordered[:recv_max_per_poll]
        return ordered

    def _blindbox_state_file_peer_ids(self) -> list[str]:
        profile_dir = self.get_profile_data_dir(create=True)
        prefix = f"{self.profile}.blindbox."
        suffix = ".json"
        peers: list[str] = []
        try:
            names = sorted(os.listdir(profile_dir))
        except OSError:
            return peers
        for name in names:
            if not (name.startswith(prefix) and name.endswith(suffix)):
                continue
            token = name[len(prefix) : -len(suffix)].strip().lower()
            if not token:
                continue
            peers.append(token)
        return peers

    def _blindbox_poll_peer_ids(self) -> list[str]:
        peers: set[str] = set()
        current_peer = self._blindbox_peer_id()
        if current_peer:
            peers.add(current_peer)
        if self.profile != TRANSIENT_PROFILE_NAME:
            try:
                book = load_book(self._contacts_json_path())
            except Exception:
                book = None
            contacts_iter = getattr(book, "contacts", ()) if book is not None else ()
            for rec in contacts_iter:
                try:
                    normalized = self._normalize_peer_addr(getattr(rec, "addr", ""))
                except Exception:
                    normalized = ""
                if normalized:
                    peers.add(normalized)
            lap = str(getattr(book, "last_active_peer", "") or "").strip() if book is not None else ""
            if lap:
                try:
                    peers.add(self._normalize_peer_addr(lap))
                except Exception:
                    pass
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        for state in self.list_group_states():
            for member_id in state.members:
                normalized = normalize_member_id(member_id)
                if not normalized or same_i2p_destination(normalized, local_member):
                    continue
                try:
                    peers.add(self._normalize_peer_addr(normalized))
                except Exception:
                    continue
        peers.update(self._blindbox_state_file_peer_ids())
        return sorted(peers)

    def _blindbox_poll_contexts(self) -> list[_BlindBoxPollContext]:
        contexts: list[_BlindBoxPollContext] = []
        current_peer = self._blindbox_peer_id()
        for peer_id in self._blindbox_poll_peer_ids():
            if current_peer and same_i2p_destination(peer_id, current_peer):
                root_candidates = self._blindbox_root_candidates()
                if not root_candidates:
                    try:
                        snapshot = self._load_blindbox_peer_snapshot(peer_id)
                    except Exception:
                        snapshot = None
                    if snapshot is not None:
                        root_candidates = self._blindbox_root_candidates_for_snapshot(
                            snapshot
                        )
                if not root_candidates:
                    continue
                contexts.append(
                    _BlindBoxPollContext(
                        channel_id=peer_id,
                        state=self._blindbox_state,
                        root_candidates=root_candidates,
                        save_state=self._save_blindbox_state,
                    )
                )
                continue
            try:
                snapshot = self._load_blindbox_peer_snapshot(peer_id)
            except Exception:
                continue
            root_candidates = self._blindbox_root_candidates_for_snapshot(snapshot)
            if not root_candidates:
                continue
            contexts.append(
                _BlindBoxPollContext(
                    channel_id=peer_id,
                    state=snapshot.state,
                    root_candidates=root_candidates,
                    save_state=lambda snap=snapshot: self._save_blindbox_peer_snapshot(
                        snap
                    ),
                )
            )
        return contexts

    def _blindbox_group_poll_contexts(self) -> list[_BlindBoxPollGroupContext]:
        contexts: list[_BlindBoxPollGroupContext] = []
        for group_state in self.list_group_states():
            snapshot_bundle = self._group_blindbox_runtime_snapshot(
                group_state.group_id
            )
            if snapshot_bundle is None:
                continue
            snapshot, save_state = snapshot_bundle
            root_candidates = self._group_blindbox_root_candidates(snapshot)
            if not root_candidates:
                continue
            contexts.append(
                _BlindBoxPollGroupContext(
                    group_id=group_state.group_id,
                    group_epoch=int(snapshot.group_epoch),
                    state=snapshot.state,
                    root_candidates=root_candidates,
                    save_state=save_state,
                )
            )
        return contexts

    def _load_blindbox_state(self) -> None:
        if not self.blindbox_enabled:
            return
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            return
        try:
            path = self._blindbox_state_path()
            if not os.path.exists(path):
                self._blindbox_state = BlindBoxState()
                self._blindbox_root_secret = None
                self._blindbox_root_epoch = 0
                self._blindbox_root_created_at = 0
                self._blindbox_root_send_index_base = int(self._blindbox_state.send_index)
                self._blindbox_pending_root_secret = None
                self._blindbox_pending_root_epoch = 0
                self._blindbox_pending_root_created_at = 0
                self._blindbox_pending_root_send_index_base = int(
                    self._blindbox_state.send_index
                )
                self._blindbox_prev_roots = []
                return

            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("BlindBox state must be a JSON object")

            # Single-snapshot load: use one parsed JSON object for both state and root metadata.
            self._blindbox_state = BlindBoxState.from_dict(raw)
            wrap_version_raw = raw.get(
                "blindbox_wrap_version", BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
            )
            try:
                wrap_version = int(wrap_version_raw)
            except Exception:
                wrap_version = BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
            wrap_migration_needed = (
                wrap_version != BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
            )
            enc_root = raw.get("blindbox_root_secret_enc")
            if isinstance(enc_root, str) and enc_root:
                self._blindbox_root_secret, used_wrap_version = (
                    self._blindbox_decrypt_root_secret(
                        enc_root, peer_id, wrap_version=wrap_version
                    )
                )
                if used_wrap_version != BLINDBOX_LOCAL_WRAP_VERSION_CURRENT:
                    wrap_migration_needed = True
            self._blindbox_root_epoch = int(raw.get("blindbox_root_epoch", 0))
            self._blindbox_root_created_at = int(
                raw.get("blindbox_root_created_at", int(time.time()))
            )
            self._blindbox_root_send_index_base = int(
                raw.get(
                    "blindbox_root_send_index_base",
                    int(self._blindbox_state.send_index),
                )
            )
            enc_pending_root = raw.get("blindbox_pending_root_secret_enc")
            self._blindbox_pending_root_secret = None
            if isinstance(enc_pending_root, str) and enc_pending_root:
                self._blindbox_pending_root_secret, pending_wrap_version = (
                    self._blindbox_decrypt_root_secret(
                        enc_pending_root, peer_id, wrap_version=wrap_version
                    )
                )
                if pending_wrap_version != BLINDBOX_LOCAL_WRAP_VERSION_CURRENT:
                    wrap_migration_needed = True
            self._blindbox_pending_root_epoch = int(
                raw.get("blindbox_pending_root_epoch", 0)
            )
            self._blindbox_pending_root_created_at = int(
                raw.get("blindbox_pending_root_created_at", int(time.time()))
            )
            self._blindbox_pending_root_send_index_base = int(
                raw.get(
                    "blindbox_pending_root_send_index_base",
                    int(self._blindbox_state.send_index),
                )
            )
            if (
                self._blindbox_pending_root_secret is not None
                and len(self._blindbox_pending_root_secret) != 32
            ):
                self._blindbox_pending_root_secret = None
                self._blindbox_pending_root_epoch = 0
                self._blindbox_pending_root_created_at = 0
                self._blindbox_pending_root_send_index_base = int(
                    self._blindbox_state.send_index
                )
            prev_items = raw.get("blindbox_prev_roots", [])
            self._blindbox_prev_roots = []
            if isinstance(prev_items, list):
                for prev in prev_items:
                    if not isinstance(prev, dict):
                        continue
                    enc_prev = prev.get("secret_enc")
                    if not isinstance(enc_prev, str) or not enc_prev:
                        continue
                    try:
                        dec_prev, prev_wrap_version = self._blindbox_decrypt_root_secret(
                            enc_prev, peer_id, wrap_version=wrap_version
                        )
                    except Exception:
                        continue
                    if len(dec_prev) != 32:
                        continue
                    if prev_wrap_version != BLINDBOX_LOCAL_WRAP_VERSION_CURRENT:
                        wrap_migration_needed = True
                    self._blindbox_prev_roots.append(
                        {
                            "epoch": int(prev.get("epoch", 0)),
                            "secret": dec_prev,
                            "expires_at": int(prev.get("expires_at", 0)),
                        }
                    )
            self._blindbox_prune_previous_roots()
            if wrap_migration_needed and self._blindbox_root_secret is not None:
                self._save_blindbox_state()
        except Exception as e:
            detail = str(e).strip()
            if "Failed to decrypt BlindBox root secret" in detail:
                logger.info("Ignoring stale BlindBox state: %s", detail)
            else:
                logger.warning("Failed to load BlindBox state: %s", e)
            self._blindbox_state = BlindBoxState()
            self._blindbox_root_secret = None
            self._blindbox_root_epoch = 0
            self._blindbox_root_created_at = 0
            self._blindbox_root_send_index_base = 0
            self._blindbox_pending_root_secret = None
            self._blindbox_pending_root_epoch = 0
            self._blindbox_pending_root_created_at = 0
            self._blindbox_pending_root_send_index_base = 0
            self._blindbox_prev_roots = []

    def _save_blindbox_state(self) -> None:
        if not self.blindbox_enabled:
            return
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            return
        if (
            self._blindbox_root_secret is None
            and self._blindbox_pending_root_secret is None
        ):
            return
        try:
            path = self._blindbox_state_path()
            payload = self._blindbox_state.to_dict()
            if self._blindbox_root_secret is not None:
                payload["blindbox_root_secret_enc"] = self._blindbox_encrypt_root_secret(
                    self._blindbox_root_secret, peer_id
                )
            payload["blindbox_wrap_version"] = BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
            payload["blindbox_root_epoch"] = int(self._blindbox_root_epoch)
            payload["blindbox_root_created_at"] = int(self._blindbox_root_created_at)
            payload["blindbox_root_send_index_base"] = int(
                self._blindbox_root_send_index_base
            )
            if self._blindbox_pending_root_secret is not None:
                payload["blindbox_pending_root_secret_enc"] = (
                    self._blindbox_encrypt_root_secret(
                        self._blindbox_pending_root_secret, peer_id
                    )
                )
            payload["blindbox_pending_root_epoch"] = int(
                self._blindbox_pending_root_epoch
            )
            payload["blindbox_pending_root_created_at"] = int(
                self._blindbox_pending_root_created_at
            )
            payload["blindbox_pending_root_send_index_base"] = int(
                self._blindbox_pending_root_send_index_base
            )
            self._blindbox_prune_previous_roots()
            payload["blindbox_prev_roots"] = [
                {
                    "epoch": int(item.get("epoch", 0)),
                    "expires_at": int(item.get("expires_at", 0)),
                    "secret_enc": self._blindbox_encrypt_root_secret(
                        bytes(item["secret"]), peer_id
                    ),
                }
                for item in self._blindbox_prev_roots
                if isinstance(item.get("secret"), (bytes, bytearray))
                and len(bytes(item["secret"])) == 32
            ]
            atomic_write_json(path, payload)
        except Exception as e:
            logger.warning("Failed to save BlindBox state: %s", e)

    def _blindbox_ready(self) -> bool:
        return (
            self.blindbox_enabled
            and bool(self.blindbox_replicas)
            and self.my_dest is not None
        )

    def _blindbox_runtime_retry_active(self, *, now_mono: Optional[float] = None) -> bool:
        now = time.monotonic() if now_mono is None else now_mono
        return now < self._blindbox_runtime_retry_not_before_mono

    def _blindbox_runtime_unavailable_reason(self) -> str:
        detail = str(self._blindbox_runtime_last_error or "").strip()
        if not detail:
            return ""
        return f"BlindBox runtime unavailable: {detail}"

    def _legacy_group_blindbox_outbound_enabled(self) -> bool:
        # Legacy group-wide BlindBox stays available only as an explicit
        # compatibility lane. The default runtime path is pairwise BlindBox.
        return _env_truthy("I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX")

    @staticmethod
    def _blindbox_client_runtime_ready(client: Any) -> bool:
        if client is None:
            return False
        probe = getattr(client, "is_runtime_ready", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
        # Older tests and lightweight doubles only provide put/get/close methods.
        return True

    def _load_trust_store(self) -> None:
        """Загружает pinning-таблицу peer_addr -> signing_pub_hex."""
        self.peer_trusted_signing_keys = {}
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        path = self._trust_store_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        self.peer_trusted_signing_keys[k] = v.lower()
        except Exception as e:
            logger.warning("Failed to load trust store %s: %s", path, e)

    def _save_trust_store(self) -> None:
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        path = self._trust_store_path()
        try:
            atomic_write_json(path, self.peer_trusted_signing_keys)
        except Exception as e:
            logger.warning("Failed to save trust store %s: %s", path, e)

    def forget_pinned_peer_key(self, peer_addr: str) -> bool:
        """Удаляет TOFU pin для пира из памяти и trust store профиля."""
        normalized = self._normalize_peer_addr(peer_addr)
        if not normalized:
            raise ValueError("Peer address is empty")
        removed = self.peer_trusted_signing_keys.pop(normalized, None) is not None
        if removed:
            self._save_trust_store()
        return removed

    def get_peer_trust_info(self, peer_addr: str) -> Optional[PeerTrustInfo]:
        """Trust pin state for a peer; None if the address string is invalid."""
        try:
            normalized = self._normalize_peer_addr(peer_addr)
        except ValueError:
            return None
        if not normalized:
            return None
        hex_key = self.peer_trusted_signing_keys.get(normalized)
        fp: Optional[str] = None
        if hex_key:
            try:
                raw = bytes.fromhex(hex_key)
                fp = self._fingerprint_pubkey(raw)
            except ValueError:
                fp = None
        return PeerTrustInfo(
            peer_normalized=normalized,
            pinned=hex_key is not None,
            signing_key_hex=hex_key,
            fingerprint_short=fp,
        )

    @staticmethod
    def _fingerprint_pubkey(pubkey: bytes) -> str:
        import hashlib

        return hashlib.sha256(pubkey).hexdigest()[:16]

    def _normalize_peer_addr(self, addr: str) -> str:
        """
        Канонический peer id: только lowercase base32 host (без суффикса ``.b32.i2p``).
        Допускает типичный ввод из UI/чата: пробелы, префиксы («My Addr: …»), вставку с суффиксом или без.
        """
        raw = (addr or "").strip()
        if not raw:
            return ""
        lower = raw.lower()
        # Первая подходящая подстрока … .b32.i2p (игнорирует префикс/мусор вокруг).
        m = re.search(r"([a-z2-7]{40,80})\.b32\.i2p", lower)
        if m:
            return m.group(1)
        # «My Addr: <base32>» без суффикса (как в бабле Online / peer_b32).
        m_my = re.search(r"my\s*addr\s*:\s*([a-z2-7]{40,80})(?:\s|$)", lower)
        if m_my:
            return m_my.group(1)
        compact = re.sub(r"\s+", "", lower)
        if any(ch in compact for ch in ("\r", "\n", "\x00", "\t", "=")):
            raise ValueError("Peer address contains forbidden characters")
        if compact.endswith(".b32.i2p"):
            host = compact[: -len(".b32.i2p")]
        elif "." in compact:
            raise ValueError("Peer address must be base32 host or *.b32.i2p")
        else:
            host = compact
        if not re.fullmatch(r"[a-z2-7]{40,80}", host):
            raise ValueError("Peer address format is invalid")
        return host

    def _peer_sam_hostname(self, addr: str) -> str:
        """SAM NAMING LOOKUP / STREAM CONNECT ожидают строку вида ``<base32>.b32.i2p``."""
        bare = self._normalize_peer_addr(addr)
        if not bare:
            return ""
        return f"{bare}.b32.i2p"

    def _canonical_dest_base64(self, raw_dest: str) -> str:
        dest = i2plib.Destination((raw_dest or "").strip())
        return dest.base64

    async def _verify_address_binding_via_sam(
        self, peer_addr: str, dest_base64: str
    ) -> bool:
        """Проверяет, что peer_addr в SAM резолвится именно в этот destination."""
        normalized_addr = self._normalize_peer_addr(peer_addr)
        if not normalized_addr:
            return False
        try:
            looked_up = await asyncio.wait_for(
                i2plib.dest_lookup(
                    self._peer_sam_hostname(normalized_addr),
                    sam_address=self.sam_address,
                ),
                timeout=12.0,
            )
            looked_up_base64: str
            if isinstance(looked_up, i2plib.Destination):
                looked_up_base64 = looked_up.base64
            else:
                looked_up_base64 = i2plib.Destination(str(looked_up)).base64
            return looked_up_base64 == dest_base64
        except Exception as e:
            logger.warning(
                "SAM binding verification failed for %s: %s",
                self._peer_sam_hostname(normalized_addr),
                e,
            )
            return False

    async def _set_verified_peer_identity(
        self, peer_addr: str, raw_dest: str, *, source: str
    ) -> bool:
        """Фиксирует peer identity только после SAM-проверки binding."""
        normalized_addr = self._normalize_peer_addr(peer_addr)
        canonical_dest = self._canonical_dest_base64(raw_dest)
        if not await self._verify_address_binding_via_sam(normalized_addr, canonical_dest):
            self._emit_error(
                f"Rejected {source} identity: SAM lookup does not confirm {normalized_addr[:24]}..."
            )
            return False
        self.activate_peer_context(normalized_addr)
        self.current_peer_dest_b64 = canonical_dest
        self.peer_identity_binding_verified = True
        return True

    def activate_peer_context(self, peer_addr: str) -> str:
        normalized_addr = self._normalize_peer_addr(peer_addr)
        try:
            previous_peer = self._normalize_peer_addr(self.current_peer_addr or "")
        except Exception:
            previous_peer = ""
        self.current_peer_addr = normalized_addr
        self.active_live_peer_id = normalized_addr
        self.session_manager.set_active_peer(normalized_addr)
        if self.blindbox_enabled and (
            normalized_addr != previous_peer or self._blindbox_root_secret is None
        ):
            self._load_blindbox_state()
            self._trigger_blindbox_hot_poll("peer-switch")
        return normalized_addr

    def _is_probable_peer_addr(self, value: str) -> bool:
        raw = (value or "").strip().lower()
        if not raw:
            return False
        if raw.endswith(".b32.i2p"):
            raw = raw[:-8]
        return bool(re.fullmatch(r"[a-z2-7]{40,80}", raw))

    def _contacts_json_path(self) -> str:
        return os.path.join(
            self.get_profile_data_dir(create=True), f"{self.profile}.contacts.json"
        )

    def peer_in_saved_contacts(self, peer_addr: str) -> bool:
        """True if peer is allowlisted for inbound. Transient profile allows any."""
        if self.profile == TRANSIENT_PROFILE_NAME:
            return True
        try:
            norm = self._normalize_peer_addr(peer_addr)
        except Exception:
            return False
        if not norm:
            return False
        try:
            current = self._normalize_peer_addr(self.current_peer_addr or "")
        except Exception:
            current = ""
        if current and same_i2p_destination(current, norm):
            return True
        book = load_book(self._contacts_json_path())
        for rec in book.contacts:
            if same_i2p_destination(rec.addr, norm):
                return True
        if book.last_active_peer and same_i2p_destination(book.last_active_peer, norm):
            return True
        return False

    def ensure_peer_in_saved_contacts(self, peer_addr: str) -> bool:
        """Add peer to Saved peers if missing. Returns True if the book changed."""
        if self.profile == TRANSIENT_PROFILE_NAME:
            return False
        try:
            norm = self._normalize_peer_addr(peer_addr)
        except Exception:
            return False
        if not norm:
            return False
        path = self._contacts_json_path()
        book = load_book(path)
        if remember_peer(book, norm):
            save_book(path, trim_book(book))
            return True
        return False

    def _telemetry_has_peer_target(self) -> bool:
        if self.current_peer_addr:
            return True
        if self.profile == TRANSIENT_PROFILE_NAME:
            return False
        book = load_book(self._contacts_json_path())
        return bool(book.last_active_peer or book.contacts)

    def _last_active_peer_for_telemetry(self) -> str:
        if self.current_peer_addr:
            return self.current_peer_addr
        if self.profile == TRANSIENT_PROFILE_NAME:
            return ""
        book = load_book(self._contacts_json_path())
        return (book.last_active_peer or "").strip()

    def _blindbox_live_peer_ok_for_root_exchange(
        self, peer_id: Optional[str] = None
    ) -> bool:
        """Live session with a Saved peer — replaces legacy lock-to-peer check."""
        target_peer = peer_id or self.current_peer_addr or ""
        if not target_peer:
            return False
        try:
            cur = self._normalize_peer_addr(target_peer)
        except Exception:
            return False
        if not self._handshake_complete_for_peer_route(cur):
            return False
        if self.profile == TRANSIENT_PROFILE_NAME:
            return bool(cur)
        return bool(cur) and self.peer_in_saved_contacts(cur)

    def _write_profile_dat(
        self,
        private_key_base64: Optional[str],
        stored_peer: Optional[str],
    ) -> None:
        """Persist identity key on line 1 only. Legacy second-line lock peer is never written."""
        del stored_peer  # kept in signature for callers; multi-peer model uses contacts.json
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        key = (private_key_base64 or "").strip()
        if not key:
            return
        path = self._profile_path()
        atomic_write_text(path, key + "\n")

    def save_stored_peer(self, peer_addr: str) -> None:
        """
        Legacy API: adds the peer to Saved peers (Lock to peer removed).
        """
        if self.profile == TRANSIENT_PROFILE_NAME:
            raise ValueError("Cannot store peer for transient profile")
        normalized_peer = self._normalize_peer_addr(peer_addr)
        if not normalized_peer:
            raise ValueError("Peer address is empty")
        if self.ensure_peer_in_saved_contacts(normalized_peer):
            self._emit_system(
                f"Added to Saved peers: {normalized_peer[:24]}..."
            )
        self.stored_peer = None
        self._load_blindbox_state()

    def clear_locked_peer(self) -> None:
        """Legacy no-op: lock removed; optionally strip legacy 2nd line from .dat."""
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        private_key_base64: Optional[str] = None
        if self.my_dest is not None:
            try:
                private_key_base64 = self.my_dest.private_key.base64
            except Exception:
                private_key_base64 = None
        key_file = self._profile_path()
        if not private_key_base64 and os.path.exists(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            if lines and not self._is_probable_peer_addr(lines[0]):
                private_key_base64 = lines[0]
        if private_key_base64:
            self._write_profile_dat(private_key_base64, None)
        else:
            if os.path.isfile(key_file):
                try:
                    with open(key_file, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f.readlines() if line.strip()]
                    if len(lines) == 1 and self._is_probable_peer_addr(lines[0]):
                        os.remove(key_file)
                except OSError:
                    pass
        self.stored_peer = None
        self._load_blindbox_state()

    def is_current_peer_verified_for_lock(self) -> bool:
        k = self._normalize_peer_addr(self.current_peer_addr or "")
        ls = self._live_sessions.get(k) if k else None
        if not ls:
            return False
        return bool(ls.handshake_complete and ls.peer_identity_binding_verified)

    async def _request_trust_decision(
        self, peer_addr: str, fingerprint: str, signing_key_hex: str
    ) -> bool:
        """Запрашивает TOFU-решение у UI без блокировки активной корутины."""
        if self.on_trust_decision is None:
            return False
        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[bool] = loop.create_future()

        def _schedule_result(approved: bool) -> None:
            # Откладываем set_result на следующий тик цикла: иначе при Qt/async UI
            # возможна реентерабельная активация ожидающих задач (RuntimeError в 3.12+).
            def _set() -> None:
                if not decision_future.done():
                    decision_future.set_result(approved)

            loop.call_soon(_set)

        def _ask_user() -> None:
            async def _resolve() -> None:
                try:
                    result = self.on_trust_decision(peer_addr, fingerprint, signing_key_hex)
                    approved = bool(await result) if inspect.isawaitable(result) else bool(result)
                except Exception as e:
                    logger.warning("TOFU trust callback failed: %s", e)
                    approved = False
                _schedule_result(approved)

            loop.create_task(_resolve())

        loop.call_soon(_ask_user)
        return await decision_future

    async def _request_trust_mismatch_decision(
        self,
        peer_addr: str,
        old_fingerprint: str,
        new_fingerprint: str,
        old_signing_key_hex: str,
        new_signing_key_hex: str,
    ) -> bool:
        if self.on_trust_mismatch_decision is None:
            return False
        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[bool] = loop.create_future()

        def _schedule_result(approved: bool) -> None:
            def _set() -> None:
                if not decision_future.done():
                    decision_future.set_result(approved)

            loop.call_soon(_set)

        def _ask_user() -> None:
            async def _resolve() -> None:
                try:
                    result = self.on_trust_mismatch_decision(
                        peer_addr,
                        old_fingerprint,
                        new_fingerprint,
                        old_signing_key_hex,
                        new_signing_key_hex,
                    )
                    approved = bool(await result) if inspect.isawaitable(result) else bool(result)
                except Exception as e:
                    logger.warning("Trust mismatch callback failed: %s", e)
                    approved = False
                _schedule_result(approved)

            loop.create_task(_resolve())

        loop.call_soon(_ask_user)
        return await decision_future

    async def _pin_or_verify_peer_signing_key(self, peer_addr: str, verify_key: bytes) -> bool:
        peer_addr = self._normalize_peer_addr(peer_addr)
        if not peer_addr:
            self._emit_error("Cannot pin signing key: unknown peer address")
            return False
        fp = self._fingerprint_pubkey(verify_key)
        current_hex = verify_key.hex().lower()
        pinned_hex = self.peer_trusted_signing_keys.get(peer_addr)
        if pinned_hex is None:
            if self.on_trust_decision is not None:
                # Продлеваем окно handshake перед блокирующим UI-диалогом TOFU.
                ls_tofu = self._live_sessions.get(peer_addr)
                if (
                    ls_tofu
                    and ls_tofu.conn is not None
                    and not ls_tofu.handshake_complete
                ):
                    self._start_handshake_watchdog(ls_tofu.conn, peer_id=peer_addr)
                self._emit_system("Waiting for TOFU trust confirmation...")
                approved = await self._request_trust_decision(peer_addr, fp, current_hex)
                if not approved:
                    self._emit_error(
                        f"TOFU rejected: peer signing key {fp} for {peer_addr[:20]}..."
                    )
                    return False
            elif not self._trust_auto:
                self._emit_error(
                    "TOFU confirmation required for a new peer key. "
                    "Use the GUI trust prompt or set I2PCHAT_TRUST_AUTO=1 "
                    "for explicit CLI/TUI auto-pin."
                )
                return False
            self.peer_trusted_signing_keys[peer_addr] = current_hex
            self._save_trust_store()
            if self.on_trust_decision is None:
                self._emit_system(
                    "TOFU: auto-pinning peer signing key because "
                    "I2PCHAT_TRUST_AUTO=1 is enabled."
                )
            self._emit_system(
                f"TOFU: pinned peer signing key {fp} for {peer_addr[:20]}..."
            )
            self._emit_system(
                "Verify peer fingerprint out-of-band to mitigate first-contact MITM."
            )
            return True
        if pinned_hex != current_hex:
            try:
                old_fp = self._fingerprint_pubkey(bytes.fromhex(pinned_hex))
            except Exception:
                old_fp = pinned_hex[:16]
            if self.on_trust_mismatch_decision is not None:
                ls_mm = self._live_sessions.get(peer_addr)
                if (
                    ls_mm
                    and ls_mm.conn is not None
                    and not ls_mm.handshake_complete
                ):
                    self._start_handshake_watchdog(ls_mm.conn, peer_id=peer_addr)
                self._emit_system("Trusted key changed. Waiting for user decision...")
                approved = await self._request_trust_mismatch_decision(
                    peer_addr,
                    old_fp,
                    fp,
                    pinned_hex,
                    current_hex,
                )
                if approved:
                    self.peer_trusted_signing_keys[peer_addr] = current_hex
                    self._save_trust_store()
                    self._emit_system(
                        f"Updated trusted signing key {old_fp} → {fp} for {peer_addr[:20]}..."
                    )
                    self._emit_system(
                        "Verify the new fingerprint out-of-band before continuing to rely on this peer."
                    )
                    return True
            self._emit_error(
                f"Peer signing key mismatch for {peer_addr[:20]}... "
                f"(expected {pinned_hex[:16]}, got {current_hex[:16]})"
            )
            self._emit_system(
                "Trusted key change was not approved. Session remains blocked until you explicitly trust the new key."
            )
            return False
        return True

    def _ensure_local_signing_key(self) -> None:
        """Гарантирует наличие стабильного Ed25519 ключа подписи handshake."""
        if not crypto.NACL_AVAILABLE:
            raise RuntimeError("PyNaCl is required for handshake signing")

        if self.profile == TRANSIENT_PROFILE_NAME:
            seed, pub = crypto.generate_signing_keypair()
            self.my_signing_seed = seed
            self.my_signing_public = pub
            return

        keyring_name = f"{self.profile}{SIGNING_KEYRING_SUFFIX}"
        seed_hex = _try_keyring_get(keyring_name)
        if seed_hex:
            try:
                seed = bytes.fromhex(seed_hex)
                if len(seed) != 32:
                    raise ValueError("invalid seed length")
                self.my_signing_seed = seed
                self.my_signing_public = crypto.get_verify_key_from_seed(seed)
                return
            except Exception:
                logger.warning("Invalid signing seed in keyring for profile %s", self.profile)

        path = self._signing_seed_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                seed = bytes.fromhex(raw)
                if len(seed) != 32:
                    raise ValueError("invalid seed length")
                self.my_signing_seed = seed
                self.my_signing_public = crypto.get_verify_key_from_seed(seed)
                return
            except Exception:
                logger.warning("Invalid signing seed file %s", path)

        seed, pub = crypto.generate_signing_keypair()
        self.my_signing_seed = seed
        self.my_signing_public = pub
        if not _try_keyring_set(keyring_name, seed.hex()):
            atomic_write_text(path, seed.hex())

    def _build_init_sig_payload(
        self,
        signer_addr: str,
        remote_addr: str,
        nonce_hex: str,
        eph_hex: str,
        sign_pub_hex: str,
    ) -> bytes:
        signer_addr = self._normalize_peer_addr(signer_addr)
        remote_addr = self._normalize_peer_addr(remote_addr)
        payload = (
            f"I2PCHAT-HS3|INIT|{signer_addr}|{remote_addr}|"
            f"{nonce_hex}|{eph_hex}|{sign_pub_hex}"
        )
        return payload.encode("utf-8")

    def _build_resp_sig_payload(
        self,
        signer_addr: str,
        remote_addr: str,
        init_nonce_hex: str,
        init_eph_hex: str,
        init_sign_pub_hex: str,
        resp_nonce_hex: str,
        resp_eph_hex: str,
        resp_sign_pub_hex: str,
    ) -> bytes:
        signer_addr = self._normalize_peer_addr(signer_addr)
        remote_addr = self._normalize_peer_addr(remote_addr)
        payload = (
            f"I2PCHAT-HS3|RESP|{signer_addr}|{remote_addr}|"
            f"{init_nonce_hex}|{init_eph_hex}|{init_sign_pub_hex}|"
            f"{resp_nonce_hex}|{resp_eph_hex}|{resp_sign_pub_hex}"
        )
        return payload.encode("utf-8")

    async def init_session(self) -> None:
        """Создать/загрузить идентичность и SAM-сессию."""
        try:
            self.session_manager.transition_transport(
                TransportState.STARTING, reason="init-session"
            )
            await self._do_init_session()
        except Exception as e:
            self.session_manager.transition_transport(
                TransportState.FAILED, reason=f"init-session:{type(e).__name__}"
            )
            if _tcp_refusal_in_exception_chain(e):
                msg = _sam_unreachable_user_message(self.sam_address)
                self._emit_error(msg)
                logger.warning(
                    "SAM unreachable (connection refused) at %s:%s",
                    self.sam_address[0],
                    self.sam_address[1],
                    exc_info=True,
                )
                raise RuntimeError(msg) from e
            raise

    async def _do_init_session(self) -> None:
        """Создать/загрузить идентичность и SAM-сессию (тело init_session)."""
        self._emit_status("initializing")
        self._emit_system(f"Initializing Profile: {self.profile}")

        key_file = self._profile_path()
        is_persistent = self.profile != TRANSIENT_PROFILE_NAME
        if not is_persistent:
            self._emit_system(
                "Security note: TRANSIENT profile does not persist TOFU trust pins between restarts."
            )
            self._emit_system(
                "Use a named profile for persistent peer-key trust continuity."
            )
        dest: Optional[i2plib.Destination] = None
        legacy_lock_peer: Optional[str] = None

        if is_persistent:
            keyring_key = _try_keyring_get(self.profile)
            if keyring_key:
                dest = i2plib.Destination(keyring_key, has_private_key=True)
                self._emit_system(f"Loaded identity from secure keyring")
            elif os.path.exists(key_file):
                with open(key_file, "r") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]

                if len(lines) > 0 and not self._is_probable_peer_addr(lines[0]):
                    raw_private_key = lines[0]
                    try:
                        dest = i2plib.Destination(raw_private_key, has_private_key=True)
                        self._emit_system(f"Loaded identity from {key_file}")
                    except Exception:
                        dest = None

            if os.path.exists(key_file):
                with open(key_file, "r") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                stored_line: Optional[str] = None
                if len(lines) > 1 and self._is_probable_peer_addr(lines[1]):
                    stored_line = lines[1]
                elif len(lines) > 0 and self._is_probable_peer_addr(lines[0]):
                    # keyring-сценарий: в .dat может быть только pinned peer
                    stored_line = lines[0]
                if stored_line:
                    legacy_lock_peer = self._normalize_peer_addr(stored_line)
                    disp_peer = self._peer_sam_hostname(legacy_lock_peer)
                    self._emit_system(
                        f"Legacy Lock to peer line (migrating to Saved peers): {disp_peer}"
                    )

        if dest is None:
            self._emit_system("Generating new Ed25519 identity...")
            dest = await i2plib.new_destination(
                sam_address=self.sam_address, sig_type=7
            )

            if is_persistent:
                if _try_keyring_set(self.profile, dest.private_key.base64):
                    self._emit_system("Identity saved to secure keyring")
                else:
                    self._emit_system(f"Identity saved to {key_file}")

        self.my_dest = dest
        if is_persistent:
            self.stored_peer = None
            if legacy_lock_peer:
                self.ensure_peer_in_saved_contacts(legacy_lock_peer)
                self._emit_system(
                    "Former lock peer was added to Saved peers; profile .dat no longer stores a second line."
                )
            self._write_profile_dat(self.my_dest.private_key.base64, None)
        self._load_trust_store()
        self._ensure_local_signing_key()
        self._load_blindbox_state()
        telemetry = self.get_blindbox_telemetry()
        if bool(telemetry.get("insecure_local_mode")):
            self._emit_system(
                "Warning: BlindBox insecure local mode is active "
                "(I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1)."
            )
            self._emit_system(
                "Set I2PCHAT_BLINDBOX_LOCAL_TOKEN and disable insecure local mode for stronger local security."
            )

        self._emit_system("Starting I2P session, please wait…")

        # Важно: сохраняем сокет сессии и не закрываем его до shutdown.
        # Иначе по SAM-спеку сессия умирает при закрытии сокета, и STREAM CONNECT/ACCEPT ломают роутер.
        self.session_manager.session_socket = await i2plib.create_session(
            self.session_id,
            destination=self.my_dest,
            sam_address=self.sam_address,
            options={
                "inbound.length": "2",
                "outbound.length": "2",
                "inbound.quantity": "3",
                "outbound.quantity": "3",
            },
            session_create_timeout=self._sam_session_create_timeout,
        )
        self.session_manager.transition_transport(
            TransportState.SAM_CONNECTED, reason="sam-session-created"
        )

        # Blind Box uses a second SAM stream session; starting it here overlaps with
        # the tunnel-build poll below so "BlindBox runtime started" is not delayed
        # by the full main-tunnel wait (previously ~up to 90s extra wall time).
        if self._blindbox_ready():
            self._emit_system(
                "Blind Box: starting SAM session in parallel with main tunnels…"
            )
            asyncio.create_task(self._ensure_blindbox_runtime_started())

        my_address = self.my_dest.base32 + ".b32.i2p"
        my_addr_display = self.my_dest.base32
        self._emit_system("Building I2P tunnels (may take 1–2 min)...")
        self.session_manager.transition_transport(
            TransportState.WARMING_TUNNELS, reason="tunnel-warmup"
        )
        tunnels_ready = False
        wait_until = time.monotonic() + 90
        while time.monotonic() < wait_until:
            try:
                await asyncio.wait_for(
                    i2plib.dest_lookup(
                        my_address,
                        sam_address=self.sam_address,
                    ),
                    timeout=5.0,
                )
                tunnels_ready = True
                break
            except (asyncio.TimeoutError, Exception):
                await asyncio.sleep(3)

        if tunnels_ready:
            self._emit_status("visible")
            self._emit_message("success", f"Online! My Address: {my_addr_display}")
            self._emit_system("Tunnels ready. Waiting for incoming connections...")
            self.session_manager.transition_transport(
                TransportState.READY, reason="tunnels-ready"
            )
        else:
            self._emit_status("local_ok")
            self._emit_message("success", f"Online! My Address: {my_addr_display}")
            self._emit_system(
                "Tunnels may still be building. Wait 1–2 min before connecting."
            )
            self.session_manager.transition_transport(
                TransportState.DEGRADED, reason="tunnels-pending"
            )

        self.peer_b32 = f"My Addr: {my_addr_display}"

        # запуск фоновых задач
        loop = asyncio.get_running_loop()
        self.session_manager.accept_task = loop.create_task(self.accept_loop())
        self.session_manager.tunnel_task = loop.create_task(self.tunnel_watcher())
        self._ensure_group_mesh_runner(loop)
        if self._blindbox_ready():
            # Sync with early parallel boot (or run once if that task has not won yet).
            await self._ensure_blindbox_runtime_started()

    # ---------- публичные операции ----------

    # Таймаут на установку соединения (I2P может долго строить туннели)
    CONNECT_TIMEOUT = 120
    # Таймаут на операции чтения в receive_loop (защита от зависания)
    # Увеличен для устойчивости при простое: keepalive 15s + запас на латентность I2P
    READ_TIMEOUT = 50.0
    # Таймаут на установление защищённого канала после TCP/I2P connect.
    # Учитывает задержки I2P и возможное TOFU-подтверждение пользователем.
    HANDSHAKE_TIMEOUT = 90.0
    # Максимальное количество строк в буфере изображения (защита от OOM)
    MAX_IMAGE_LINES = 500
    # Максимальный размер принимаемого файла в байтах (защита от заполнения диска)
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
    # Ограничение на размер одного фрейма протокола (защита от memory DoS)
    MAX_FRAME_BODY = 2 * 1024 * 1024  # 2 MB
    # Ограничения на pending ACK-контекст (anti-spoofing / anti-memory-growth)
    ACK_TTL_SECONDS = 300.0
    ACK_MAX_PENDING = 4096
    ACK_PRUNE_INTERVAL = 15.0

    def _activate_ack_session(self, peer_id: Optional[str] = None) -> None:
        """Сброс inflight и новый epoch ACK для legacy-потока или указанного live-пира."""
        if peer_id:
            k = self._normalize_peer_addr(peer_id)
            ls = self._live_sessions.get(k)
            if ls:
                ls._ack_session_epoch += 1
                if ls._ack_session_epoch > 0x7FFFFFFF:
                    ls._ack_session_epoch = 1
        else:
            self._ack_session_epoch += 1
            if self._ack_session_epoch > 0x7FFFFFFF:
                self._ack_session_epoch = 1
        self.session_manager.clear_inflight_messages(
            peer_id=self._normalize_peer_addr(peer_id or self.current_peer_addr or "")
        )

    def _ack_epoch_for_peer_addr(self, peer_addr: str) -> int:
        k = self._normalize_peer_addr(peer_addr)
        if k and k in self._live_sessions:
            return self._live_sessions[k]._ack_session_epoch
        return self._ack_session_epoch

    def _current_ack_peer(self) -> str:
        return self._normalize_peer_addr(self.current_peer_addr or "")

    def _total_pending_acks(self) -> int:
        return (
            len(self._pending_text_acks)
            + len(self._pending_file_acks)
            + len(self._pending_image_acks)
        )

    def _prune_pending_acks(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_ack_prune_ts < self.ACK_PRUNE_INTERVAL:
            return
        expiry_threshold = now - self.ACK_TTL_SECONDS
        for table in (
            self._pending_text_acks,
            self._pending_file_acks,
            self._pending_image_acks,
        ):
            stale_ids = [
                ack_id
                for ack_id, entry in table.items()
                if entry.state != "awaiting_ack" or entry.created_at < expiry_threshold
            ]
            for ack_id in stale_ids:
                table.pop(ack_id, None)

        while self._total_pending_acks() > self.ACK_MAX_PENDING:
            oldest_ref: Optional[tuple[dict[int, PendingAckEntry], int, float]] = None
            for table in (
                self._pending_text_acks,
                self._pending_file_acks,
                self._pending_image_acks,
            ):
                for ack_id, entry in table.items():
                    if oldest_ref is None or entry.created_at < oldest_ref[2]:
                        oldest_ref = (table, ack_id, entry.created_at)
            if oldest_ref is None:
                break
            oldest_ref[0].pop(oldest_ref[1], None)
        self._last_ack_prune_ts = now

    def _register_pending_ack(
        self,
        table: dict[int, PendingAckEntry],
        msg_id: int,
        *,
        token: str,
        ack_kind: str,
        routing_peer_id: str = "",
    ) -> None:
        self._prune_pending_acks(force=False)
        try:
            cap = self._normalize_peer_addr(routing_peer_id) if routing_peer_id else ""
        except ValueError:
            cap = ""
        if not cap:
            cap = self._current_ack_peer()
        table[msg_id] = PendingAckEntry(
            token=token,
            ack_kind=ack_kind,
            created_at=time.monotonic(),
            peer_addr=cap,
            ack_session_epoch=self._ack_epoch_for_peer_addr(cap),
            state="awaiting_ack",
        )
        self.session_manager.register_inflight_message(
            msg_id,
            peer_id=cap,
        )
        self._prune_pending_acks(force=False)

    def _record_ack_drop(self, reason: str, details: str = "") -> None:
        if reason not in self._ack_drop_counters:
            self._ack_drop_counters[reason] = 0
        self._ack_drop_counters[reason] += 1
        if details:
            logger.warning("ACK dropped (%s): %s", reason, details)
        else:
            logger.warning("ACK dropped (%s)", reason)

    def get_ack_telemetry(self) -> dict[str, int]:
        """Returns counters for dropped/invalid ACK signals."""
        return dict(self._ack_drop_counters)

    def get_blindbox_telemetry(self) -> dict[str, Any]:
        """Returns non-sensitive local BlindBox runtime telemetry."""
        has_client = self._blindbox_client is not None
        has_loopback_replicas = any(
            _is_loopback_replica(item) for item in self.blindbox_replicas
        )
        insecure_local_mode = bool(
            has_loopback_replicas
            and not self._blindbox_local_auth_token
            and self._blindbox_allow_insecure_local
        )
        # Task is "running" during client.start() too; UI should not say "polling" until
        # the Blind Box SAM/direct session is actually up (matches "runtime started").
        poller_running = (
            self._blindbox_task is not None
            and not self._blindbox_task.done()
            and has_client
            and self._blindbox_client_runtime_ready(self._blindbox_client)
        )
        return {
            "enabled": bool(self.blindbox_enabled),
            "enabled_source": str(self._blindbox_enabled_source),
            "blind_boxes": len(self.blindbox_replicas),
            "blind_boxes_source": str(self._blindbox_replicas_source),
            "replica_endpoints": list(self.blindbox_replicas),
            "replicas_gui_locked": bool(self.blindbox_replicas_gui_locked()),
            "use_sam_for_blind_boxes": bool(self._blindbox_use_sam),
            "require_sam_for_blind_boxes": bool(self._blindbox_require_sam),
            "replicas_source": str(self._blindbox_replicas_source),
            "use_sam_for_replicas": bool(self._blindbox_use_sam),
            "require_sam_for_replicas": bool(self._blindbox_require_sam),
            "local_auth_token_enabled": bool(self._blindbox_local_auth_token),
            "allow_insecure_local_replicas": bool(self._blindbox_allow_insecure_local),
            "has_loopback_replicas": has_loopback_replicas,
            "insecure_local_mode": insecure_local_mode,
            "ready": bool(self._blindbox_ready()),
            "has_root_secret": self._blindbox_root_secret is not None,
            "replicas": len(self.blindbox_replicas),
            "put_quorum": int(self.blindbox_put_quorum),
            "get_quorum": int(self.blindbox_get_quorum),
            "client_initialized": has_client,
            "poller_running": poller_running,
            "send_index": int(self._blindbox_state.send_index),
            "recv_base": int(self._blindbox_state.recv_base),
            "recv_window": int(self._blindbox_state.recv_window),
            "root_epoch": int(self._blindbox_root_epoch),
            "privacy_profile": str(self._blindbox_privacy_profile),
            "poll_mode": self._blindbox_poll_mode(),
            "poll_min_sec": float(self._blindbox_poll_min_sec),
            "poll_max_sec": float(self._blindbox_poll_max_sec),
            "poll_hot_sec": float(self._blindbox_poll_hot_sec),
            "poll_hot_window_sec": float(self._blindbox_poll_hot_window_sec),
            "poll_cooldown_sec": float(self._blindbox_poll_cooldown_sec),
            "poll_cooldown_window_sec": float(
                self._blindbox_poll_cooldown_window_sec
            ),
            "cover_gets": int(self._blindbox_cover_gets),
            "padding_bucket": int(self._blindbox_padding_bucket),
            "root_rotate_messages": int(self._blindbox_root_rotate_messages),
            "root_rotate_seconds": int(self._blindbox_root_rotate_seconds),
            "max_previous_roots": int(self._blindbox_max_previous_roots),
            "previous_roots_loaded": int(len(self._blindbox_prev_roots)),
        }

    def get_delivery_telemetry(self) -> dict[str, Any]:
        """Returns current delivery route hints for UI decisions."""
        lap = self._last_active_peer_for_telemetry()
        peer_for_route = self._normalize_peer_addr(
            self.current_peer_addr or lap or ""
        )
        connected = self._any_live_stream()
        live_kwargs: dict[str, Any] = {"peer_id": peer_for_route}
        policy_kwargs: dict[str, Any] = {
            "requested_route": "auto",
            "peer_id": peer_for_route,
        }
        if not peer_for_route:
            live_kwargs["connected"] = connected
            live_kwargs["handshake_complete"] = self._handshake_complete_for_peer_route(
                ""
            )
            policy_kwargs["connected"] = connected
            policy_kwargs["handshake_complete"] = self._handshake_complete_for_peer_route(
                ""
            )

        secure_live = self.session_manager.is_live_path_alive(**live_kwargs)
        has_target = self._telemetry_has_peer_target()
        ready = bool(self._blindbox_ready())
        has_root_secret = self._blindbox_root_secret is not None
        bb_client = self._blindbox_client
        blindbox_runtime_ready = bool(
            self._blindbox_client_runtime_ready(bb_client)
        )
        outbound_policy = self.session_manager.select_outbound_policy(
            **policy_kwargs
        ).value
        peer_transport = self.session_manager.get_peer_transport(peer_for_route)
        reconnect_meta = self.session_manager.get_reconnect_metadata(
            peer_id=peer_for_route
        )
        peer_state = (
            peer_transport.peer_state.value
            if peer_transport is not None
            else self.session_manager.peer_state.value
        )
        outbound_streams = (
            len(peer_transport.outbound_streams)
            if peer_transport is not None
            else len(self.session_manager.outbound_streams)
        )

        route_hc = (
            self._handshake_complete_for_peer_route(peer_for_route)
            if peer_for_route
            else self._handshake_complete_for_peer_route("")
        )
        if connected and not route_hc:
            state = "connecting-handshake"
        elif secure_live:
            state = "online-live"
        elif ready and has_root_secret:
            state = "offline-ready"
        elif ready and not has_root_secret:
            state = "await-live-root"
        elif self.blindbox_enabled and len(self.blindbox_replicas) <= 0:
            state = "blindbox-needs-boxes"
        elif self.blindbox_enabled and self.my_dest is None:
            state = "blindbox-starting-local-session"
        elif self.blindbox_enabled:
            state = "blindbox-initializing"
        elif self.profile == TRANSIENT_PROFILE_NAME:
            state = "blindbox-disabled-transient"
        else:
            state = "blindbox-disabled"

        return {
            "state": state,
            "connected": connected,
            "secure_live": secure_live,
            "has_target": has_target,
            "blindbox_enabled": bool(self.blindbox_enabled),
            "blindbox_enabled_source": str(self._blindbox_enabled_source),
            "blindbox_replicas_source": str(self._blindbox_replicas_source),
            "blind_boxes_source": str(self._blindbox_replicas_source),
            "blindbox_use_sam_for_replicas": bool(self._blindbox_use_sam),
            "blindbox_use_sam_for_blind_boxes": bool(self._blindbox_use_sam),
            "blindbox_ready": ready,
            "blindbox_runtime_ready": blindbox_runtime_ready,
            "has_root_secret": has_root_secret,
            "stored_peer": bool(self.stored_peer),
            "blind_boxes": len(self.blindbox_replicas),
            "replicas": len(self.blindbox_replicas),
            "network_status": str(self.network_status),
            "transport_state": self.session_manager.transport_state.value,
            "peer_state": peer_state,
            "outbound_policy": outbound_policy,
            "outbound_streams": outbound_streams,
            "reconnect_attempt": int(reconnect_meta.attempt),
            "reconnect_next_retry_mono": float(reconnect_meta.next_retry_mono),
            "reconnect_last_failure_reason": str(reconnect_meta.last_failure_reason),
        }

    def get_group_send_ui_hints(self, group_id: str) -> dict[str, Any]:
        """Aggregate route hints for the group Send button (mirrors 1:1 live vs offline-ready)."""
        state = self.load_group_state(group_id)
        if state is None:
            return {
                "can_send": False,
                "show_offline_button": False,
                "any_live_to_member": False,
                "reason": "missing",
                "live_by_recipient": {},
                "group_blindbox_ready": False,
                "await_group_root": False,
            }
        try:
            local = self._local_group_member_id()
        except RuntimeError:
            return {
                "can_send": False,
                "show_offline_button": False,
                "any_live_to_member": False,
                "reason": "no-local-dest",
                "live_by_recipient": {},
                "group_blindbox_ready": False,
                "await_group_root": False,
            }
        recipients = [
            m
            for m in state.members
            if normalize_member_id(m) != normalize_member_id(local)
        ]
        if not recipients:
            return {
                "can_send": False,
                "show_offline_button": False,
                "any_live_to_member": False,
                "reason": "no-recipients",
                "live_by_recipient": {},
                "group_blindbox_ready": False,
                "await_group_root": False,
            }
        any_live = False
        live_by_recipient: dict[str, bool] = {}
        for raw in recipients:
            target = self._normalize_peer_addr(raw)
            if not target:
                continue
            alive = self.session_manager.is_live_path_alive(peer_id=target)
            live_by_recipient[target] = alive
            if alive:
                any_live = True
        d = self.get_delivery_telemetry()
        blindbox_runtime_ok = bool(
            d.get("blindbox_enabled")
            and d.get("blindbox_ready")
            and d.get("blindbox_runtime_ready")
        )
        (
            blindbox_ready_by_recipient,
            group_blindbox_ready,
            await_group_root,
        ) = self._group_pairwise_blindbox_status(
            state,
            local_member_id=local,
            live_by_member=live_by_recipient,
            blindbox_runtime_ok=blindbox_runtime_ok,
        )
        can_send = bool(any_live or blindbox_runtime_ok)
        show_offline = bool((not any_live) and blindbox_runtime_ok)
        return {
            "can_send": can_send,
            "show_offline_button": show_offline,
            "any_live_to_member": any_live,
            "reason": "await-group-root" if await_group_root and not any_live else "ok",
            "live_by_recipient": live_by_recipient,
            "blindbox_ready_by_recipient": blindbox_ready_by_recipient,
            "group_blindbox_ready": group_blindbox_ready,
            "await_group_root": await_group_root,
        }

    def get_group_topology_snapshot(
        self, group_id: str
    ) -> Optional[GroupTopologySnapshot]:
        """Observed group mesh from the local node's point of view."""
        state = self.load_group_state(group_id)
        if state is None:
            return None
        try:
            local_member_id = self._local_group_member_id()
        except RuntimeError:
            local_member_id = ""

        live_by_member: dict[str, bool] = {}
        peer_state_by_member: dict[str, str] = {}
        for member_id in state.members:
            normalized_member = normalize_member_id(member_id)
            if not normalized_member:
                continue
            if local_member_id and normalized_member == normalize_member_id(local_member_id):
                continue
            live_by_member[normalized_member] = self.session_manager.is_live_path_alive(
                peer_id=normalized_member
            )
            peer_transport = self.session_manager.get_peer_transport(normalized_member)
            peer_state_by_member[normalized_member] = (
                peer_transport.peer_state.value
                if peer_transport is not None
                else "disconnected"
            )
        (
            blindbox_ready_by_member,
            group_blindbox_ready,
            await_group_root,
        ) = self._group_pairwise_blindbox_status(
            state,
            local_member_id=local_member_id,
            live_by_member=live_by_member,
            blindbox_runtime_ok=self._blindbox_ready(),
        )

        delivery_status_by_member: dict[str, str] = {}
        delivery_reason_by_member: dict[str, str] = {}
        conversation = self.load_group(group_id)
        if conversation is not None:
            for entry in reversed(conversation.history):
                if entry.kind != "me" or not entry.delivery_results:
                    continue
                delivery_status_by_member = dict(entry.delivery_results)
                delivery_reason_by_member = dict(entry.delivery_reasons)
                break

        return build_observed_group_topology(
            state,
            local_member_id=local_member_id,
            live_by_member=live_by_member,
            peer_state_by_member=peer_state_by_member,
            group_blindbox_ready=group_blindbox_ready,
            await_group_root=await_group_root,
            blindbox_ready_by_member=blindbox_ready_by_member,
            delivery_status_by_member=delivery_status_by_member,
            delivery_reason_by_member=delivery_reason_by_member,
        )

    def _group_pairwise_blindbox_status(
        self,
        state: GroupState,
        *,
        local_member_id: str,
        live_by_member: Mapping[str, bool],
        blindbox_runtime_ok: bool,
    ) -> tuple[dict[str, bool], bool, bool]:
        blindbox_ready_by_member: dict[str, bool] = {}
        offline_members = 0
        offline_members_without_root = 0

        for member_id in state.members:
            normalized_member = normalize_member_id(member_id)
            if not normalized_member:
                continue
            if local_member_id and same_i2p_destination(normalized_member, local_member_id):
                continue

            root_ready = False
            if blindbox_runtime_ok:
                try:
                    snapshot = self._load_blindbox_peer_snapshot(normalized_member)
                except Exception:
                    snapshot = None
                root_ready = bool(snapshot is not None and snapshot.root_secret is not None)
            blindbox_ready_by_member[normalized_member] = root_ready

            if live_by_member.get(normalized_member, False):
                continue
            offline_members += 1
            if not root_ready:
                offline_members_without_root += 1

        group_blindbox_ready = bool(
            blindbox_runtime_ok
            and offline_members > 0
            and offline_members_without_root == 0
        )
        await_group_root = bool(
            blindbox_runtime_ok
            and offline_members > 0
            and offline_members_without_root > 0
        )
        return blindbox_ready_by_member, group_blindbox_ready, await_group_root

    def get_group_topology_ascii(self, group_id: str) -> str:
        snapshot = self.get_group_topology_snapshot(group_id)
        if snapshot is None:
            return ""
        return render_group_topology_ascii(snapshot)

    def get_group_topology_mermaid(self, group_id: str) -> str:
        snapshot = self.get_group_topology_snapshot(group_id)
        if snapshot is None:
            return ""
        return render_group_topology_mermaid(snapshot)

    def _offline_send_block_feedback(self) -> tuple[str, str]:
        delivery = self.get_delivery_telemetry()
        state = str(delivery.get("state", "unknown"))
        runtime_unavailable = self._blindbox_runtime_unavailable_reason()
        if not delivery.get("has_target"):
            return (
                "no-target",
                "No peer selected. Choose a saved contact or enter a peer address, then send.",
            )
        if runtime_unavailable:
            return ("blindbox-runtime-unavailable", runtime_unavailable)
        if state == "blindbox-disabled-transient":
            return (
                "transient-profile",
                "Offline delivery is unavailable in TRANSIENT mode. Use Connect for a live session.",
            )
        if state == "blindbox-disabled":
            return (
                "blindbox-disabled",
                "No active secure session. Offline delivery is disabled by configuration. "
                "Unset I2PCHAT_BLINDBOX_ENABLED=0 or Connect live.",
            )
        if state == "connecting-handshake":
            return (
                "handshake-in-progress",
                "Secure channel handshake is in progress. Please wait a moment.",
            )
        if state == "blindbox-needs-boxes":
            return (
                "blindbox-needs-boxes",
                "Blind Box servers are not configured. Set I2PCHAT_BLINDBOX_REPLICAS, "
                "I2PCHAT_BLINDBOX_DEFAULT_REPLICAS, or I2PCHAT_BLINDBOX_DEFAULT_REPLICAS_FILE "
                "(or unset I2PCHAT_BLINDBOX_NO_BUILTIN_DEFAULTS to use release defaults).",
            )
        if state == "blindbox-starting-local-session":
            return (
                "blindbox-starting-local-session",
                "BlindBox is initializing local I2P session. Wait for Pending/Visible and retry.",
            )
        if state == "await-live-root":
            return (
                "blindbox-await-root",
                "BlindBox needs one successful live secure chat with this peer first. "
                "Press Connect once to initialize offline delivery.",
            )
        return ("blocked", "No active secure session. Connect to peer or retry later.")

    @staticmethod
    def _blindbox_send_busy_feedback() -> tuple[str, str]:
        return (
            "blindbox-send-busy",
            "Previous BlindBox upload is still in progress. "
            "Wait for queued/failed status, then send again.",
        )

    def _blindbox_poll_sleep_interval(self) -> float:
        mode = self._blindbox_poll_mode()
        if mode == BLINDBOX_POLL_MODE_HOT:
            return self._blindbox_poll_hot_sec
        if mode == BLINDBOX_POLL_MODE_COOLDOWN:
            return self._blindbox_poll_cooldown_sec
        if self._blindbox_poll_max_sec <= self._blindbox_poll_min_sec:
            return self._blindbox_poll_min_sec
        return self._blindbox_rng.uniform(
            self._blindbox_poll_min_sec, self._blindbox_poll_max_sec
        )

    def _blindbox_poll_mode(self, *, now_mono: Optional[float] = None) -> str:
        now = time.monotonic() if now_mono is None else now_mono
        if now < self._blindbox_poll_hot_until_mono:
            return BLINDBOX_POLL_MODE_HOT
        if now < self._blindbox_poll_cooldown_until_mono:
            return BLINDBOX_POLL_MODE_COOLDOWN
        return BLINDBOX_POLL_MODE_IDLE

    def _trigger_blindbox_hot_poll(self, reason: str) -> None:
        now = time.monotonic()
        if self._blindbox_poll_hot_window_sec > 0:
            self._blindbox_poll_hot_until_mono = max(
                self._blindbox_poll_hot_until_mono,
                now + self._blindbox_poll_hot_window_sec,
            )
        cooldown_anchor = max(self._blindbox_poll_hot_until_mono, now)
        if self._blindbox_poll_cooldown_window_sec > 0:
            self._blindbox_poll_cooldown_until_mono = max(
                self._blindbox_poll_cooldown_until_mono,
                cooldown_anchor + self._blindbox_poll_cooldown_window_sec,
            )
        logger.debug("BlindBox poller -> HOT (%s)", reason)
        self._blindbox_poll_wakeup.set()

    def _emit_blindbox_debug_poll(self, text: str, *, force: bool = False) -> None:
        if not self._blindbox_debug_ui:
            return
        now = time.monotonic()
        if (
            not force
            and now - self._blindbox_debug_ui_last_emit_mono
            < self._blindbox_debug_ui_interval_sec
        ):
            return
        self._blindbox_debug_ui_last_emit_mono = now
        self._emit_system(f"[BBDBG] {text}")

    async def _blindbox_poll_sleep(self) -> None:
        interval = self._blindbox_poll_sleep_interval()
        if interval <= 0:
            return
        if self._blindbox_poll_wakeup.is_set():
            self._blindbox_poll_wakeup.clear()
            return
        try:
            await asyncio.wait_for(self._blindbox_poll_wakeup.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        finally:
            self._blindbox_poll_wakeup.clear()

    def _remember_blindbox_seen_hash(self, digest: str) -> None:
        token = str(digest or "").strip()
        if not token or token in self._blindbox_seen_hashes:
            return
        self._blindbox_seen_hashes.add(token)
        self._blindbox_seen_hash_order.append(token)
        while len(self._blindbox_seen_hash_order) > self._blindbox_max_seen_hashes:
            oldest = self._blindbox_seen_hash_order.popleft()
            self._blindbox_seen_hashes.discard(oldest)

    def _blindbox_recv_candidates(self) -> list[int]:
        recv_backtrack = max(
            0, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_BACKTRACK", "0"))
        )
        recv_lookahead = max(
            0, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_LOOKAHEAD", "64"))
        )
        recv_max_per_poll = max(
            1, int(os.environ.get("I2PCHAT_BLINDBOX_RECV_MAX_PER_POLL", "64"))
        )
        recv_start = max(0, self._blindbox_state.recv_base - recv_backtrack)
        recv_span = max(self._blindbox_state.recv_window, recv_lookahead)
        recv_end = self._blindbox_state.recv_base + recv_span
        # Fast path for latency: probe from recv_base forward first (most probable
        # next indexes), then optional backtrack tail. Random full-window shuffle
        # increases median delivery latency when Blind Box GET is slow.
        forward = (
            idx
            for idx in range(self._blindbox_state.recv_base, recv_end)
            if idx not in self._blindbox_state.consumed_recv
        )
        backtrack = (
            idx
            for idx in range(recv_start, self._blindbox_state.recv_base)
            if idx not in self._blindbox_state.consumed_recv
        )
        ordered = [*forward, *backtrack]
        if len(ordered) > recv_max_per_poll:
            return ordered[:recv_max_per_poll]
        return ordered

    async def _blindbox_emit_cover_gets(self, client: BlindBoxClient) -> None:
        if self._blindbox_cover_gets <= 0:
            return
        for _ in range(self._blindbox_cover_gets):
            fake_lookup_token = hashlib.sha256(os.urandom(32)).hexdigest()
            try:
                await client.get(fake_lookup_token, require_quorum=False)
            except Exception:
                # Cover traffic must never affect application flow.
                pass

    async def _ensure_blindbox_runtime_started(self) -> None:
        if not self._blindbox_ready():
            return
        async with self._blindbox_runtime_lock:
            if not self._blindbox_ready():
                return
            now = time.monotonic()
            client = self._blindbox_client
            if (
                self._blindbox_runtime_retry_active(now_mono=now)
                and not self._blindbox_client_runtime_ready(client)
            ):
                return
            if self._blindbox_client is None:
                if self._blindbox_replicas_source == "local-auto":
                    try:
                        endpoint = await ensure_local_blindbox_replica(
                            auth_token=self._blindbox_local_auth_token,
                            max_entries=self._blindbox_local_max_entries,
                        )
                        self.blindbox_replicas = [endpoint]
                        self._blindbox_use_sam = False
                    except Exception as e:
                        self._emit_error(
                            f"Local Blind Box startup failed: {_exception_user_message(e)}"
                        )
                        return
                # Unique SAM ID: avoids collisions if two app instances share profile+second,
                # and pairs with BlindBoxClient.start() lock against duplicate SESSION CREATE.
                bb_sam_id = f"{self.session_id}_bb_{secrets.token_hex(6)}"
                bb_sam_sess_timeout = max(
                    15.0,
                    float(
                        os.environ.get(
                            "I2PCHAT_BLINDBOX_SAM_SESSION_TIMEOUT", "120"
                        )
                    ),
                )
                self._blindbox_client = BlindBoxClient(
                    session_id=bb_sam_id,
                    blind_boxes=self.blindbox_replicas,
                    sam_host=self.sam_address[0],
                    sam_port=self.sam_address[1],
                    use_sam=self._blindbox_use_sam,
                    put_quorum=min(
                        self.blindbox_put_quorum, len(self.blindbox_replicas)
                    ),
                    get_quorum=min(
                        self.blindbox_get_quorum, len(self.blindbox_replicas)
                    ),
                    sam_session_timeout=bb_sam_sess_timeout,
                    local_auth_token=self._blindbox_local_auth_token,
                    replica_auth=self._blindbox_replica_auth,
                )
            if self._blindbox_task is None or self._blindbox_task.done():
                loop = asyncio.get_running_loop()
                self._blindbox_task = loop.create_task(self._blindbox_poll_loop())

    async def ensure_blindbox_runtime_started(self) -> None:
        await self._ensure_blindbox_runtime_started()

    def get_blindbox_replica_endpoints_readonly(self) -> Tuple[str, ...]:
        return tuple(self.blindbox_replicas)

    def blindbox_replicas_gui_locked(self) -> bool:
        if is_transient_profile_name(self.profile):
            return True
        src = self._blindbox_replicas_source
        return src in ("env", "env-default", "file-default", "local-auto")

    async def _stop_blindbox_runtime_only(self) -> None:
        task = self._blindbox_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._blindbox_task = None
        if self._blindbox_client is not None:
            try:
                await self._blindbox_client.close()
            except Exception:
                pass
            self._blindbox_client = None

    async def apply_blindbox_replica_endpoints(
        self,
        endpoints: list[str],
        replica_auth: Optional[dict[str, str]] = None,
    ) -> str:
        """
        Save per-profile replica list (and optional per-replica auth) and restart BlindBox runtime.
        Returns empty string on success, otherwise a user-facing error message.
        """
        if is_transient_profile_name(self.profile):
            return "BlindBox replica editing is only available for named profiles."
        if not self.blindbox_enabled:
            return "BlindBox is disabled for this profile."
        if self.blindbox_replicas_gui_locked():
            return (
                "Replica list is controlled by environment variables "
                "(or local-auto mode) and cannot be changed here."
            )
        norm = normalize_replica_endpoints(endpoints)
        if not norm:
            return "Add at least one Blind Box endpoint (one per line)."
        norm_set = set(norm)
        auth_in = dict(replica_auth or {})
        filtered_auth: dict[str, str] = {}
        for k, v in auth_in.items():
            k2 = (k or "").strip()
            v2 = (v or "").strip()
            if not k2 or not v2:
                continue
            if k2 not in norm_set:
                logger.warning(
                    "BlindBox replica_auth key not in endpoint list, ignoring: %s", k2
                )
                continue
            filtered_auth[k2] = v2
        use_sam = not (
            len(norm) > 0 and all(_is_host_port_replica(x) for x in norm)
        )
        sec = _blindbox_direct_replicas_security_issue(
            norm,
            use_sam=use_sam,
            require_sam=self._blindbox_require_sam,
            local_auth_token=self._blindbox_local_auth_token,
            allow_insecure_local=self._blindbox_allow_insecure_local,
        )
        if sec:
            return sec
        try:
            save_profile_blindbox_replicas_bundle(
                self.get_profile_data_dir(create=True),
                self.profile,
                norm,
                filtered_auth,
            )
        except ValueError as e:
            return str(e)
        except OSError as e:
            return f"Failed to save replica list: {_exception_user_message(e)}"
        async with self._blindbox_runtime_lock:
            await self._stop_blindbox_runtime_only()
            self.blindbox_replicas = norm
            self._blindbox_replica_auth = dict(filtered_auth)
            self._blindbox_use_sam = use_sam
            self._blindbox_replicas_source = "profile-file"
            if self.blindbox_enabled and len(self.blindbox_replicas) > 0 and not self._blindbox_use_sam:
                logger.warning(
                    "BlindBox transport is using direct TCP (non-SAM): %s",
                    ", ".join(self.blindbox_replicas),
                )
            await self._ensure_blindbox_runtime_started()
        self._emit_system("BlindBox replica list updated; runtime restarted.")
        return ""

    async def _blindbox_poll_loop(self) -> None:
        client = self._blindbox_client
        if client is None:
            return
        try:
            await client.start()
            self._blindbox_runtime_last_error = ""
            self._blindbox_runtime_retry_not_before_mono = 0.0
            self._emit_system("BlindBox runtime started")
            self._trigger_blindbox_hot_poll("startup")
            self._schedule_group_pending_flush(self._blindbox_poll_peer_ids())
        except Exception as e:
            now = time.monotonic()
            detail = _exception_user_message(e)
            self._blindbox_runtime_last_error = detail
            self._blindbox_runtime_retry_not_before_mono = (
                now + self._blindbox_runtime_retry_sec
            )
            if _blindbox_runtime_transport_error(e):
                logger.warning(
                    "BlindBox startup failed (retry in %.1fs): %s",
                    self._blindbox_runtime_retry_sec,
                    detail,
                )
            else:
                logger.exception("BlindBox startup failed: %s", detail)
            if self._blindbox_client is client:
                try:
                    await client.close()
                except Exception:
                    pass
                self._blindbox_client = None
            self._emit_error(f"BlindBox startup failed: {detail}")
            return
        try:
            while True:
                if not self._blindbox_ready():
                    await self._blindbox_poll_sleep()
                    continue
                # Poll even when a live TCP session exists: offline sends only hit Blind Box;
                # the peer must GET+decrypt while connected too, otherwise messages never arrive.
                if not self.my_dest:
                    await self._blindbox_poll_sleep()
                    continue
                local_id = self.my_dest.base32
                poll_contexts = self._blindbox_poll_contexts()
                group_poll_contexts = self._blindbox_group_poll_contexts()
                if not poll_contexts and not group_poll_contexts:
                    await self._blindbox_poll_sleep()
                    continue
                cycle_started_mono = time.monotonic()
                cycle_checked = 0
                cycle_hit = 0
                cycle_miss = 0
                cycle_timeout = 0
                slow_samples: list[str] = []
                for context in poll_contexts:
                    peer_id = context.channel_id
                    recv_cands = self._blindbox_recv_candidates_for_state(context.state)
                    if len(recv_cands) > self._blindbox_recv_scan_budget:
                        recv_cands = recv_cands[: self._blindbox_recv_scan_budget]
                    for recv_index in recv_cands:
                        cycle_checked += 1
                        got_valid = False
                        for root_item in context.root_candidates:
                            keys = derive_blindbox_message_keys(
                                bytes(root_item["secret"]),
                                local_id,
                                peer_id,
                                "recv",
                                recv_index,
                                epoch=int(root_item["epoch"]),
                            )

                            async def _accept_blob(blob: bytes) -> bool:
                                digest = hashlib.sha256(blob).hexdigest()
                                if digest in self._blindbox_seen_hashes:
                                    return False
                                try:
                                    frame = decrypt_blindbox_blob(
                                        blob,
                                        keys.blob_key,
                                        expected_direction="send",
                                        expected_index=recv_index,
                                        expected_state_tag=keys.state_tag,
                                    )
                                except Exception:
                                    return False
                                accepted = await self._process_blindbox_frame(
                                    frame, source_peer=peer_id
                                )
                                if accepted:
                                    self._remember_blindbox_seen_hash(digest)
                                    return True
                                return False

                            get_diag: dict[str, Any] = {}
                            get_started_mono = time.monotonic()
                            timed_out = False
                            try:
                                accepted_blob = await asyncio.wait_for(
                                    client.get_first_accepted(
                                        keys.lookup_token,
                                        accept_blob=_accept_blob,
                                        miss_grace_sec=self._blindbox_get_first_miss_grace_sec,
                                        diag=get_diag,
                                    ),
                                    timeout=self._blindbox_get_first_timeout_sec,
                                )
                            except asyncio.TimeoutError:
                                timed_out = True
                                accepted_blob = None
                                cycle_timeout += 1
                            except Exception as exc:
                                logger.debug(
                                    "BlindBox get_first_accepted failed peer=%s recv_index=%s epoch=%s: %s",
                                    peer_id,
                                    recv_index,
                                    int(root_item["epoch"]),
                                    exc,
                                    exc_info=True,
                                )
                                continue
                            get_elapsed = max(
                                0.0, time.monotonic() - get_started_mono
                            )
                            if get_elapsed >= self._blindbox_debug_ui_slow_sec:
                                accepted_addr = str(get_diag.get("accepted_addr", "")).strip()
                                first_addr = str(get_diag.get("first_result_addr", "")).strip()
                                canceled = [
                                    str(x).strip()
                                    for x in (get_diag.get("canceled_pending_addrs") or [])
                                    if str(x).strip()
                                ]
                                if accepted_addr:
                                    slow_addr = accepted_addr
                                elif canceled:
                                    slow_addr = canceled[0]
                                else:
                                    slow_addr = first_addr or "unknown"
                                if timed_out:
                                    slow_addr = f"{slow_addr} (timeout)"
                                slow_samples.append(
                                    f"peer={peer_id[:10]} idx={recv_index} t={get_elapsed:.2f}s box={slow_addr}"
                                )
                            if accepted_blob is not None:
                                got_valid = True
                                break
                        if got_valid:
                            context.state.mark_consumed(recv_index)
                            context.save_state()
                            self._trigger_blindbox_hot_poll("received-offline-message")
                            cycle_hit += 1
                        else:
                            cycle_miss += 1
                for context in group_poll_contexts:
                    recv_cands = self._blindbox_recv_candidates_for_state(context.state)
                    if len(recv_cands) > self._blindbox_recv_scan_budget:
                        recv_cands = recv_cands[: self._blindbox_recv_scan_budget]
                    for recv_index in recv_cands:
                        cycle_checked += 1
                        got_valid = False
                        for root_item in context.root_candidates:
                            keys = derive_group_blindbox_message_keys(
                                bytes(root_item["secret"]),
                                context.group_id,
                                "send",
                                recv_index,
                                group_epoch=int(root_item["group_epoch"]),
                                root_epoch=int(root_item["root_epoch"]),
                            )

                            async def _accept_group_blob(blob: bytes) -> bool:
                                digest = hashlib.sha256(blob).hexdigest()
                                if digest in self._blindbox_seen_hashes:
                                    return False
                                try:
                                    frame = decrypt_blindbox_blob(
                                        blob,
                                        keys.blob_key,
                                        expected_direction="send",
                                        expected_index=recv_index,
                                        expected_state_tag=keys.state_tag,
                                    )
                                except Exception:
                                    return False
                                accepted = await self._process_blindbox_frame(frame)
                                if accepted:
                                    self._remember_blindbox_seen_hash(digest)
                                    return True
                                return False

                            get_diag: dict[str, Any] = {}
                            get_started_mono = time.monotonic()
                            timed_out = False
                            try:
                                accepted_blob = await asyncio.wait_for(
                                    client.get_first_accepted(
                                        keys.lookup_token,
                                        accept_blob=_accept_group_blob,
                                        miss_grace_sec=self._blindbox_get_first_miss_grace_sec,
                                        diag=get_diag,
                                    ),
                                    timeout=self._blindbox_get_first_timeout_sec,
                                )
                            except asyncio.TimeoutError:
                                timed_out = True
                                accepted_blob = None
                                cycle_timeout += 1
                            except Exception as exc:
                                logger.debug(
                                    "BlindBox group get_first_accepted failed group=%s recv_index=%s group_epoch=%s root_epoch=%s: %s",
                                    context.group_id,
                                    recv_index,
                                    int(root_item["group_epoch"]),
                                    int(root_item["root_epoch"]),
                                    exc,
                                    exc_info=True,
                                )
                                continue
                            get_elapsed = max(
                                0.0, time.monotonic() - get_started_mono
                            )
                            if get_elapsed >= self._blindbox_debug_ui_slow_sec:
                                accepted_addr = str(get_diag.get("accepted_addr", "")).strip()
                                first_addr = str(get_diag.get("first_result_addr", "")).strip()
                                canceled = [
                                    str(x).strip()
                                    for x in (get_diag.get("canceled_pending_addrs") or [])
                                    if str(x).strip()
                                ]
                                if accepted_addr:
                                    slow_addr = accepted_addr
                                elif canceled:
                                    slow_addr = canceled[0]
                                else:
                                    slow_addr = first_addr or "unknown"
                                if timed_out:
                                    slow_addr = f"{slow_addr} (timeout)"
                                slow_samples.append(
                                    f"group={context.group_id[:10]} idx={recv_index} t={get_elapsed:.2f}s box={slow_addr}"
                                )
                            if accepted_blob is not None:
                                got_valid = True
                                break
                        if got_valid:
                            context.state.mark_consumed(recv_index)
                            context.save_state()
                            self._trigger_blindbox_hot_poll("received-offline-message")
                            cycle_hit += 1
                        else:
                            cycle_miss += 1
                cycle_elapsed = max(0.0, time.monotonic() - cycle_started_mono)
                if self._blindbox_debug_ui and cycle_checked > 0:
                    slow_note = slow_samples[0] if slow_samples else ""
                    should_emit = (
                        cycle_hit > 0
                        or bool(slow_samples)
                        or cycle_elapsed >= self._blindbox_debug_ui_slow_sec
                        or (
                            cycle_miss > 0
                            and (
                                time.monotonic() - self._blindbox_debug_ui_last_emit_mono
                                >= self._blindbox_debug_ui_interval_sec
                            )
                        )
                    )
                    if should_emit:
                        msg = (
                            f"poll checked={cycle_checked} hit={cycle_hit} miss={cycle_miss} "
                            f"timeout={cycle_timeout} cycle={cycle_elapsed:.2f}s"
                        )
                        if slow_note:
                            msg += f" slow={slow_note}"
                        self._emit_blindbox_debug_poll(msg)
                elif (
                    self._blindbox_slow_warn_ui
                    and cycle_checked > 0
                    and cycle_elapsed >= self._blindbox_slow_warn_sec
                    and cycle_hit == 0
                ):
                    now_mono = time.monotonic()
                    if (
                        now_mono - self._blindbox_slow_warn_last_mono
                        >= self._blindbox_slow_warn_interval_sec
                    ):
                        self._blindbox_slow_warn_last_mono = now_mono
                        self._emit_system(
                            "BlindBox polling is slow; a replica may be lagging "
                            f"(timeouts this cycle: {cycle_timeout}). "
                            "Delivery can be delayed."
                        )
                await self._blindbox_emit_cover_gets(client)
                await self._blindbox_poll_sleep()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            detail = _exception_user_message(e)
            logger.exception("BlindBox poller stopped: %s", detail)
            self._emit_error(f"BlindBox poller stopped: {detail}")

    async def _process_blindbox_frame(
        self, frame: bytes, *, source_peer: Optional[str] = None
    ) -> bool:
        if len(frame) < HEADER_STRUCT.size:
            return False
        magic, version, msg_type_raw, flags, msg_id, msg_len = HEADER_STRUCT.unpack(
            frame[: HEADER_STRUCT.size]
        )
        if magic != MAGIC or version != PROTOCOL_VERSION:
            return False
        if flags != 0:
            # Offline envelope already provides confidentiality/integrity.
            return False
        payload = frame[HEADER_STRUCT.size :]
        if len(payload) < msg_len:
            return False
        body = payload[:msg_len]
        msg_type = chr(msg_type_raw)
        if msg_type != "U":
            return False
        text = body.decode("utf-8", errors="strict")
        lap = self._last_active_peer_for_telemetry()
        sp = (source_peer or self.current_peer_addr or lap or "").strip() or None
        result = self.import_group_transport(text, source_peer=sp)
        if result is not None:
            # INVALID нельзя считать успехом: иначе слот BlindBox «съедается» без импорта
            # (типичный симптом: офлайн группы доходят только одному участнику).
            return bool(result.imported or result.duplicate)
        self._emit_message("peer", text, source_peer=sp)
        self._emit_notify("peer", text, source_peer=sp)
        return True

    @staticmethod
    def _is_blindbox_slot_conflict(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        return (
            "verification mismatch" in text
            or "put exists verification failed" in text
            or "put exists" in text
        )

    def _blindbox_send_slot_retry_limit(self) -> int:
        raw = os.environ.get("I2PCHAT_BLINDBOX_SEND_SLOT_RETRIES", "4").strip()
        try:
            value = int(raw)
        except ValueError:
            value = 4
        return max(0, min(value, 32))

    async def _put_blindbox_frame_with_slot_retry_common(
        self,
        *,
        frame: bytes,
        state: BlindBoxState,
        save_state: Callable[[], None],
        put_timeout_sec: float,
        log_label: str,
        target_label: str,
        derive_keys: Callable[[int], Any],
    ) -> None:
        if self._blindbox_client is None or self.my_dest is None:
            raise RuntimeError("BlindBox runtime is not ready")
        retry_limit = self._blindbox_send_slot_retry_limit()
        attempt = 0
        while True:
            keys = derive_keys(state.send_index)
            blob = encrypt_blindbox_blob(
                frame,
                keys.blob_key,
                "send",
                state.send_index,
                keys.state_tag,
                padding_bucket=self._blindbox_padding_bucket,
            )
            try:
                await asyncio.wait_for(
                    self._blindbox_client.put(keys.lookup_token, blob),
                    timeout=put_timeout_sec,
                )
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                if attempt < retry_limit and self._is_blindbox_slot_conflict(exc):
                    state.send_index += 1
                    state.updated_at = int(time.time())
                    save_state()
                    attempt += 1
                    logger.info(
                        "%s: BlindBox slot conflict for %s, retrying with send_index=%s",
                        log_label,
                        target_label[:24],
                        state.send_index,
                    )
                    continue
                raise
            state.send_index += 1
            state.updated_at = int(time.time())
            save_state()
            return

    async def _put_blindbox_frame_with_slot_retry(
        self,
        *,
        frame: bytes,
        root_secret: bytes,
        root_epoch: int,
        peer_id: str,
        state: BlindBoxState,
        save_state: Callable[[], None],
        put_timeout_sec: float,
        log_label: str,
    ) -> None:
        await self._put_blindbox_frame_with_slot_retry_common(
            frame=frame,
            state=state,
            save_state=save_state,
            put_timeout_sec=put_timeout_sec,
            log_label=log_label,
            target_label=peer_id,
            derive_keys=lambda send_index: derive_blindbox_message_keys(
                root_secret,
                self.my_dest.base32,
                peer_id,
                "send",
                send_index,
                epoch=root_epoch,
            ),
        )

    async def _put_group_blindbox_frame_with_slot_retry(
        self,
        *,
        frame: bytes,
        root_secret: bytes,
        group_id: str,
        group_epoch: int,
        root_epoch: int,
        state: BlindBoxState,
        save_state: Callable[[], None],
        put_timeout_sec: float,
        log_label: str,
    ) -> None:
        await self._put_blindbox_frame_with_slot_retry_common(
            frame=frame,
            state=state,
            save_state=save_state,
            put_timeout_sec=put_timeout_sec,
            log_label=log_label,
            target_label=f"group:{group_id}",
            derive_keys=lambda send_index: derive_group_blindbox_message_keys(
                root_secret,
                group_id,
                "send",
                send_index,
                group_epoch=group_epoch,
                root_epoch=root_epoch,
            ),
        )

    async def _send_text_via_blindbox(
        self, text: str, *, peer_address: Optional[str] = None
    ) -> Optional[int]:
        if not self._blindbox_ready():
            return None
        if not self.my_dest or self._blindbox_client is None:
            return None
        target_peer = self._blindbox_peer_id_for_peer(
            peer_address or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer:
            return None

        async with self._blindbox_send_lock:
            if not self._blindbox_ready():
                return None
            client = self._blindbox_client
            if not self.my_dest or not self._blindbox_client_runtime_ready(client):
                return None
            if not target_peer:
                return None
            try:
                send_snapshot = self._blindbox_send_snapshot_for_peer(target_peer)
            except Exception as e:
                detail = _exception_user_message(e)
                logger.warning(
                    "Failed to load BlindBox snapshot for %s: %s",
                    target_peer[:24],
                    detail,
                    exc_info=True,
                )
                self._emit_error(f"BlindBox send failed: {detail}")
                return None
            if send_snapshot is None:
                return None
            snapshot, save_state = send_snapshot
            if snapshot.root_secret is None:
                return None
            chunks = split_long_chat_text(text)
            if not chunks:
                return None
            last_msg_id: Optional[int] = None
            try:
                for chunk in chunks:
                    msg_id = self._allocate_msg_id()
                    frame = self._codec.encode(
                        "U", chunk.encode("utf-8"), msg_id=msg_id, flags=0
                    )
                    # Optimistic UI: show the bubble immediately; PUT over I2P can take many seconds.
                    self._emit_message(
                        "me",
                        chunk,
                        message_id=str(msg_id),
                        delivery_state=DELIVERY_STATE_SENDING,
                        delivery_route="offline-pending",
                        delivery_hint="Uploading to Blind Box (I2P)…",
                        delivery_reason="blindbox-put",
                        retryable=False,
                    )
                    # Max wait for upload to a replica (sender). Peer pickup depends on their poll interval + I2P.
                    put_timeout_sec = max(
                        5.0,
                        float(
                            os.environ.get(
                                "I2PCHAT_BLINDBOX_PUT_TIMEOUT_SEC", "30"
                            )
                        ),
                    )
                    try:
                        await self._put_blindbox_frame_with_slot_retry(
                            frame=frame,
                            root_secret=snapshot.root_secret,
                            root_epoch=snapshot.root_epoch,
                            peer_id=snapshot.peer_id,
                            state=snapshot.state,
                            save_state=save_state,
                            put_timeout_sec=put_timeout_sec,
                            log_label="Direct BlindBox send",
                        )
                    except asyncio.TimeoutError:
                        self._emit_outbound_delivery_update(
                            str(msg_id),
                            delivery_state=DELIVERY_STATE_FAILED,
                            delivery_hint=(
                                f"Blind Box upload timed out after {put_timeout_sec:.0f}s "
                                "(I2P path to replica may be stalled)."
                            ),
                            delivery_reason="blindbox-put-timeout",
                            retryable=False,
                        )
                        raise RuntimeError(
                            f"Blind Box PUT timed out after {put_timeout_sec:.0f}s"
                        ) from None
                    except Exception:
                        self._emit_outbound_delivery_update(
                            str(msg_id),
                            delivery_state=DELIVERY_STATE_FAILED,
                            delivery_hint="Blind Box upload failed.",
                            delivery_reason="blindbox-put-failed",
                            retryable=False,
                        )
                        raise
                    self._emit_outbound_delivery_update(
                        str(msg_id),
                        delivery_state=DELIVERY_STATE_QUEUED,
                        delivery_hint="Message queued for offline delivery.",
                        delivery_reason="blindbox-ready",
                        retryable=False,
                    )
                    last_msg_id = msg_id
                self._trigger_blindbox_hot_poll("offline-send")
                return last_msg_id
            except Exception as e:
                detail = _exception_user_message(e)
                logger.warning(
                    "BlindBox send failed: %s",
                    detail,
                    exc_info=not _blindbox_runtime_transport_error(e),
                )
                self._emit_error(f"BlindBox send failed: {detail}")
                return None

    def _encode_group_transport_body(
        self,
        state: GroupState,
        envelope: GroupEnvelope,
        metadata: GroupRecipientDeliveryMetadata | None = None,
        *,
        delivery_scope: str = "recipient",
    ) -> str:
        if delivery_scope == "recipient":
            if metadata is None:
                raise ValueError("Recipient delivery metadata is required for v1 group transport")
            return encode_group_transport_text(state, envelope, metadata)
        if delivery_scope == "group_blindbox":
            return encode_group_transport_text_v2(state, envelope)
        raise ValueError(f"Unsupported group delivery scope: {delivery_scope}")

    def _format_group_text_for_ui(
        self,
        state: GroupState,
        *,
        sender_id: str,
        text: str,
        incoming: bool,
    ) -> str:
        group_label = self._group_display_name(state)
        if incoming:
            return f"[Group {group_label}] {sender_id}: {text}"
        return f"[Group {group_label}] {text}"

    def _format_group_control_for_ui(
        self,
        state: GroupState,
        *,
        sender_id: str,
        payload: Mapping[str, Any] | None,
    ) -> str:
        actor_label = "You"
        try:
            if normalize_member_id(sender_id) != self._local_group_member_id():
                actor_label = short_member_label(sender_id)
        except Exception:
            actor_label = short_member_label(sender_id)
        group_label = self._group_display_name(state)
        return f"[Group {group_label}] " + render_group_control_text(
            payload,
            actor_label=actor_label,
        )

    def _group_history_kind(self, sender_id: str) -> str:
        try:
            if normalize_member_id(sender_id) == self._local_group_member_id():
                return "me"
        except Exception:
            pass
        return "peer"

    def _build_group_history_entry(
        self,
        envelope: GroupEnvelope,
        *,
        source_peer: Optional[str] = None,
        delivery_results: Optional[dict[str, str]] = None,
        delivery_reasons: Optional[dict[str, str]] = None,
    ) -> GroupHistoryEntry:
        payload = envelope.payload
        text = ""
        if envelope.content_type == GroupContentType.GROUP_TEXT:
            text = str(envelope.payload or "")
        elif envelope.content_type == GroupContentType.GROUP_CONTROL and isinstance(
            envelope.payload, dict
        ):
            payload = dict(envelope.payload)
        return GroupHistoryEntry(
            kind=self._group_history_kind(envelope.sender_id),
            sender_id=envelope.sender_id,
            content_type=envelope.content_type,
            text=text,
            payload=payload,
            msg_id=envelope.msg_id,
            group_seq=envelope.group_seq,
            epoch=envelope.epoch,
            created_at=envelope.created_at,
            source_peer=self._normalize_peer_addr(source_peer or "") or None,
            delivery_results=dict(delivery_results or {}),
            delivery_reasons=dict(delivery_reasons or {}),
        )

    def _validate_imported_group_transport(self, decoded: Any) -> None:
        if getattr(decoded, "state", None) is None or getattr(decoded, "envelope", None) is None:
            raise ValueError("Group transport state and envelope are required")
        state = decoded.state
        envelope = decoded.envelope
        delivery_scope = str(
            getattr(decoded, "delivery_scope", "recipient") or "recipient"
        ).strip()
        group_id = str(getattr(envelope, "group_id", "") or "").strip()
        if not group_id:
            raise ValueError("Missing required group transport field: group_id")
        if group_id != str(getattr(state, "group_id", "") or "").strip():
            raise ValueError("Group transport group_id does not match state snapshot")
        try:
            envelope_epoch = int(envelope.epoch)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid group transport integer field: epoch") from exc
        if envelope_epoch < 0:
            raise ValueError("Invalid group transport integer field: epoch")
        msg_id = str(getattr(envelope, "msg_id", "") or "").strip()
        if not msg_id:
            raise ValueError("Missing required group transport field: msg_id")
        sender_id = normalize_member_id(str(getattr(envelope, "sender_id", "") or ""))
        if not sender_id:
            raise ValueError("Missing required group transport field: sender_id")
        try:
            group_seq = int(envelope.group_seq)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid group transport integer field: group_seq") from exc
        if group_seq < 1:
            raise ValueError("Invalid group transport integer field: group_seq")
        if envelope.content_type not in (
            GroupContentType.GROUP_TEXT,
            GroupContentType.GROUP_CONTROL,
        ):
            raise ValueError("Unsupported group transport content type")
        if not any(same_i2p_destination(sender_id, m) for m in state.members if m):
            raise ValueError("Group transport sender is not a group member")
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        if delivery_scope == "recipient":
            if not decoded.recipient_id or not decoded.delivery_id:
                raise ValueError("Group transport recipient metadata is required")
            if not any(
                same_i2p_destination(decoded.recipient_id, m) for m in state.members if m
            ):
                raise ValueError("Group transport recipient is not a group member")
            if local_member and not same_i2p_destination(
                decoded.recipient_id, local_member
            ):
                raise ValueError("Group transport recipient does not match this profile")
        elif delivery_scope == "group_blindbox":
            if decoded.recipient_id or decoded.delivery_id:
                raise ValueError(
                    "Group blindbox transport must not include recipient metadata"
                )
            if local_member and not any(
                same_i2p_destination(local_member, member) for member in state.members if member
            ):
                raise ValueError("Group blindbox transport does not include this profile")
        else:
            raise ValueError("Unsupported group delivery scope")
        if envelope.content_type == GroupContentType.GROUP_TEXT and not isinstance(
            envelope.payload, str
        ):
            raise ValueError("GROUP_TEXT payload must be a string")
        if envelope.content_type == GroupContentType.GROUP_CONTROL and not isinstance(
            envelope.payload, dict
        ):
            raise ValueError("GROUP_CONTROL payload must be an object")

    def _merge_group_state_snapshot(
        self,
        existing: Optional[GroupState],
        incoming: GroupState,
        *,
        envelope: GroupEnvelope,
    ) -> GroupState:
        local_member = ""
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        merged_members = list(existing.members if existing is not None else ())
        merged_members.extend(incoming.members)
        merged_members.append(envelope.sender_id)
        if local_member:
            merged_members.append(local_member)
        return GroupState(
            group_id=incoming.group_id,
            title=(incoming.title or (existing.title if existing is not None else None)),
            epoch=max(
                int(envelope.epoch),
                int(incoming.epoch),
                int(existing.epoch) if existing is not None else 0,
            ),
            members=tuple(merged_members),
            created_at=existing.created_at if existing is not None else incoming.created_at,
            updated_at=max(
                incoming.updated_at,
                envelope.created_at,
                existing.updated_at if existing is not None else incoming.updated_at,
            ),
        )

    def _apply_group_control_payload(
        self,
        state: GroupState,
        payload: Any,
        *,
        updated_at: datetime,
        epoch: int,
    ) -> GroupState:
        if not isinstance(payload, dict):
            return state
        title = str(payload.get("title") or "").strip() or state.title
        members = tuple(
            str(member)
            for member in payload.get("members", state.members)
        )
        control_epoch = int(payload.get("epoch", epoch))
        return GroupState(
            group_id=state.group_id,
            title=title,
            epoch=max(int(state.epoch), control_epoch),
            members=members,
            created_at=state.created_at,
            updated_at=updated_at,
        )

    async def _send_group_envelope_live(
        self,
        recipient_id: str,
        envelope: GroupEnvelope,
        metadata: GroupRecipientDeliveryMetadata,
        *,
        state_snapshot: Optional[GroupState] = None,
    ) -> GroupTransportOutcome:
        target_peer = self._normalize_peer_addr(recipient_id)
        if not target_peer or not self._has_active_session_for_peer(target_peer):
            return GroupTransportOutcome(accepted=False, reason="needs-live-session")
        if not self.session_manager.is_live_path_alive(peer_id=target_peer):
            return GroupTransportOutcome(accepted=False, reason="needs-live-session")
        state = state_snapshot or self.load_group_state(envelope.group_id)
        if state is None:
            return GroupTransportOutcome(accepted=False, reason="unknown-group")
        frame_peer_id: Optional[str] = None
        try:
            writer, frame_peer_id, text_ack_table = self._writer_frame_peer_and_text_acks(
                target_peer
            )
            if writer is None:
                return GroupTransportOutcome(accepted=False, reason="needs-live-session")
            if self._blindbox_ready():
                await self._send_blindbox_root_if_needed(
                    writer, peer_id=frame_peer_id
                )
            body = self._encode_group_transport_body(state, envelope, metadata)
            frame, msg_id = self.frame_message_with_id(
                "U", body, peer_id=frame_peer_id
            )
            writer.write(frame)
            await writer.drain()
            routing_pid = frame_peer_id or self._normalize_peer_addr(target_peer)
            self._register_pending_ack(
                text_ack_table,
                msg_id,
                token=body[:128],
                ack_kind="msg",
                routing_peer_id=routing_pid,
            )
            return GroupTransportOutcome(
                accepted=True,
                reason="live-session",
                transport_message_id=str(msg_id),
            )
        except Exception as e:
            self.session_manager.mark_live_failure(
                reason="group-send-failed",
                peer_id=target_peer,
            )
            self._schedule_disconnect(frame_peer_id)
            return GroupTransportOutcome(
                accepted=False,
                reason=_exception_user_message(e),
            )

    async def _send_group_envelope_via_blindbox(
        self,
        recipient_id: str,
        envelope: GroupEnvelope,
        metadata: GroupRecipientDeliveryMetadata,
        *,
        state_snapshot: Optional[GroupState] = None,
    ) -> GroupTransportOutcome:
        if not self._blindbox_ready():
            return GroupTransportOutcome(accepted=False, reason="blindbox-disabled")
        # Не проверять locked() до async with: иначе между fan-out к N участникам
        # другая корутина может успеть взять lock — второй+ получатель получал
        # blindbox-send-busy без ожидания (в отличие от _send_text_via_blindbox).
        if self.my_dest is None:
            return GroupTransportOutcome(
                accepted=False,
                reason="blindbox-starting-local-session",
            )
        await self._ensure_blindbox_runtime_started()
        client = self._blindbox_client
        if not self._blindbox_client_runtime_ready(client):
            runtime_reason = self._blindbox_runtime_unavailable_reason()
            return GroupTransportOutcome(
                accepted=False,
                reason=runtime_reason or "blindbox-starting-local-session",
            )
        state = state_snapshot or self.load_group_state(envelope.group_id)
        if state is None:
            return GroupTransportOutcome(accepted=False, reason="unknown-group")
        try:
            snapshot = self._load_blindbox_peer_snapshot(recipient_id)
        except Exception as e:
            return GroupTransportOutcome(
                accepted=False,
                reason=_exception_user_message(e),
            )
        if snapshot.root_secret is None:
            return GroupTransportOutcome(accepted=False, reason="blindbox-await-root")

        async with self._blindbox_send_lock:
            try:
                body = self._encode_group_transport_body(state, envelope, metadata)
                msg_id = self._allocate_msg_id()
                frame = self._codec.encode(
                    "U",
                    body.encode("utf-8"),
                    msg_id=msg_id,
                    flags=0,
                )
                put_timeout_sec = max(
                    5.0,
                    float(os.environ.get("I2PCHAT_BLINDBOX_PUT_TIMEOUT_SEC", "30")),
                )
                await self._put_blindbox_frame_with_slot_retry(
                    frame=frame,
                    root_secret=snapshot.root_secret,
                    root_epoch=snapshot.root_epoch,
                    peer_id=snapshot.peer_id,
                    state=snapshot.state,
                    save_state=lambda: self._save_blindbox_peer_snapshot(snapshot),
                    put_timeout_sec=put_timeout_sec,
                    log_label="Group BlindBox send",
                )
                self._trigger_blindbox_hot_poll("group-offline-send")
                return GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id=str(msg_id),
                )
            except asyncio.TimeoutError:
                return GroupTransportOutcome(
                    accepted=False,
                    reason="blindbox-put-timeout",
                )
            except Exception as e:
                detail = _exception_user_message(e)
                logger.warning(
                    "Group BlindBox send failed: %s",
                    detail,
                    exc_info=not _blindbox_runtime_transport_error(e),
                )
                return GroupTransportOutcome(
                    accepted=False,
                    reason=detail or "blindbox-put-failed",
                )

    async def _send_group_envelope_via_group_blindbox(
        self,
        group_id: str,
        envelope: GroupEnvelope,
        *,
        state_snapshot: Optional[GroupState] = None,
    ) -> GroupTransportOutcome:
        if not self._blindbox_ready():
            return GroupTransportOutcome(accepted=False, reason="blindbox-disabled")
        if self.my_dest is None:
            return GroupTransportOutcome(
                accepted=False,
                reason="blindbox-starting-local-session",
            )
        await self._ensure_blindbox_runtime_started()
        client = self._blindbox_client
        if not self._blindbox_client_runtime_ready(client):
            runtime_reason = self._blindbox_runtime_unavailable_reason()
            return GroupTransportOutcome(
                accepted=False,
                reason=runtime_reason or "blindbox-starting-local-session",
            )
        state = state_snapshot or self.load_group_state(group_id)
        if state is None:
            return GroupTransportOutcome(accepted=False, reason="unknown-group")
        snapshot_bundle = self._group_blindbox_runtime_snapshot(group_id)
        if snapshot_bundle is None:
            return GroupTransportOutcome(accepted=False, reason="unknown-group")
        snapshot, save_state = snapshot_bundle
        if snapshot.root_secret is None or int(snapshot.group_epoch) != int(state.epoch):
            return GroupTransportOutcome(
                accepted=False,
                reason="blindbox-await-group-root",
            )

        async with self._blindbox_send_lock:
            try:
                body = self._encode_group_transport_body(
                    state,
                    envelope,
                    delivery_scope="group_blindbox",
                )
                msg_id = self._allocate_msg_id()
                frame = self._codec.encode(
                    "U",
                    body.encode("utf-8"),
                    msg_id=msg_id,
                    flags=0,
                )
                put_timeout_sec = max(
                    5.0,
                    float(os.environ.get("I2PCHAT_BLINDBOX_PUT_TIMEOUT_SEC", "30")),
                )
                await self._put_group_blindbox_frame_with_slot_retry(
                    frame=frame,
                    root_secret=snapshot.root_secret,
                    group_id=group_id,
                    group_epoch=int(snapshot.group_epoch),
                    root_epoch=int(snapshot.root_epoch),
                    state=snapshot.state,
                    save_state=save_state,
                    put_timeout_sec=put_timeout_sec,
                    log_label="Group BlindBox send",
                )
                self._trigger_blindbox_hot_poll("group-offline-send")
                return GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id=str(msg_id),
                )
            except asyncio.TimeoutError:
                return GroupTransportOutcome(
                    accepted=False,
                    reason="blindbox-put-timeout",
                )
            except Exception as e:
                detail = _exception_user_message(e)
                logger.warning(
                    "Group BlindBox channel send failed group=%s: %s",
                    group_id,
                    detail,
                    exc_info=True,
                )
                return GroupTransportOutcome(
                    accepted=False,
                    reason=detail or "blindbox-put-failed",
                )

    async def _send_group_payload(
        self,
        state: GroupState,
        *,
        sender_id: str,
        payload: Any,
        content_type: GroupContentType,
        requested_route: str,
    ) -> tuple[GroupSendResult, tuple[GroupPendingBlindBoxMessage, ...]]:
        recipients = self.group_manager._recipient_ids(state, sender_id)
        envelope = self.group_manager._build_envelope(
            state=state,
            sender_id=sender_id,
            payload=payload,
            content_type=content_type,
            recipients=recipients,
        )
        delivery_results: dict[str, GroupMemberDeliveryResult] = {}
        offline_recipients: list[str] = []
        for recipient_id in recipients:
            metadata = envelope.member_metadata[recipient_id]
            policy = self.session_manager.select_outbound_policy(
                requested_route=requested_route,
                peer_id=recipient_id,
            )
            if policy in (
                OutboundPolicy.LIVE_ONLY,
                OutboundPolicy.PREFER_LIVE_FALLBACK_BLINDBOX,
            ):
                live_ready = self.session_manager.is_live_path_alive(peer_id=recipient_id)
                if live_ready:
                    live_result = await self._send_group_envelope_live(
                        recipient_id,
                        envelope,
                        metadata,
                        state_snapshot=state,
                    )
                    if live_result.accepted:
                        delivery_results[recipient_id] = GroupMemberDeliveryResult(
                            recipient_id=recipient_id,
                            status=GroupDeliveryStatus.DELIVERED_LIVE,
                            reason=live_result.reason or "live-session",
                            transport_message_id=live_result.transport_message_id,
                            delivery_id=metadata.delivery_id,
                        )
                        continue
                    if policy == OutboundPolicy.LIVE_ONLY:
                        delivery_results[recipient_id] = GroupMemberDeliveryResult(
                            recipient_id=recipient_id,
                            status=GroupDeliveryStatus.FAILED,
                            reason=live_result.reason or "needs-live-session",
                            transport_message_id=live_result.transport_message_id,
                            delivery_id=metadata.delivery_id,
                        )
                        continue
                elif policy == OutboundPolicy.LIVE_ONLY:
                    delivery_results[recipient_id] = GroupMemberDeliveryResult(
                        recipient_id=recipient_id,
                        status=GroupDeliveryStatus.FAILED,
                        reason="needs-live-session",
                        delivery_id=metadata.delivery_id,
                    )
                    continue
            offline_recipients.append(recipient_id)

        pending_group_blindbox: tuple[GroupPendingBlindBoxMessage, ...] = ()
        if offline_recipients:
            # Group offline delivery remains per-recipient: each member uses the
            # same pairwise BlindBox channel as direct chat with that peer.
            for recipient_id in offline_recipients:
                metadata = envelope.member_metadata[recipient_id]
                delivery_results[recipient_id] = await self._deliver_group_envelope_to_member(
                    recipient_id,
                    envelope,
                    metadata,
                    requested_route=requested_route,
                    state_snapshot=state,
                )

        return (
            GroupSendResult(
                envelope=envelope,
                delivery_results=delivery_results,
            ),
            pending_group_blindbox,
        )

    async def send_group_text(
        self,
        group_id: str,
        text: str,
        *,
        route: Literal["auto", "live", "offline"] = "auto",
    ) -> GroupSendResult:
        conversation = self._load_group_conversation(group_id)
        if conversation is None:
            raise ValueError(f"Unknown group: {group_id}")
        sender_id = self._local_group_member_id()
        self.group_manager.prime_group_sequence(
            group_id,
            next_group_seq=conversation.next_group_seq,
        )
        result, pending_group_blindbox = await self._send_group_payload(
            conversation.state,
            sender_id=sender_id,
            payload=text,
            content_type=GroupContentType.GROUP_TEXT,
            requested_route=route,
        )
        self._repair_group_delivery_failures(result)
        pending_deliveries = self._mark_recoverable_group_deliveries_pending(
            conversation.state,
            result,
        )
        updated_state = GroupState(
            group_id=conversation.state.group_id,
            title=conversation.state.title,
            epoch=max(int(conversation.state.epoch), int(result.envelope.epoch)),
            members=conversation.state.members,
            created_at=conversation.state.created_at,
            updated_at=utc_now(),
        )
        append_group_history_entry(
            self.get_profile_data_dir(create=True),
            self.profile,
            updated_state,
            self._build_group_history_entry(
                result.envelope,
                delivery_results={
                    peer_id: delivery.status.value
                    for peer_id, delivery in result.delivery_results.items()
                },
                delivery_reasons=self._group_delivery_reason_map(result),
            ),
            next_group_seq=result.envelope.group_seq + 1,
        )
        conversation = self._load_group_conversation(updated_state.group_id)
        if conversation is not None and pending_deliveries:
            conversation = self._merge_group_pending_deliveries(
                conversation,
                pending_deliveries,
            )
        if conversation is not None and pending_group_blindbox:
            conversation = self._merge_pending_group_blindbox_messages(
                conversation,
                pending_group_blindbox,
            )
        self._emit_message(
            "me",
            self._format_group_text_for_ui(
                updated_state,
                sender_id=sender_id,
                text=str(result.envelope.payload or ""),
                incoming=False,
            ),
            message_id=result.envelope.msg_id,
            conversation_kind="group",
            conversation_id=updated_state.group_id,
            conversation_title=self._group_display_name(updated_state),
            group_sender_id=sender_id,
            group_content_type=result.envelope.content_type,
            group_plain_text=str(result.envelope.payload or ""),
        )
        return result

    async def send_group_control(
        self,
        group_id: str,
        payload: dict[str, Any],
        *,
        route: Literal["auto", "live", "offline"] = "auto",
    ) -> GroupSendResult:
        conversation = self._load_group_conversation(group_id)
        if conversation is None:
            raise ValueError(f"Unknown group: {group_id}")
        sender_id = self._local_group_member_id()
        self.group_manager.prime_group_sequence(
            group_id,
            next_group_seq=conversation.next_group_seq,
        )
        result, pending_group_blindbox = await self._send_group_payload(
            conversation.state,
            sender_id=sender_id,
            payload=payload,
            content_type=GroupContentType.GROUP_CONTROL,
            requested_route=route,
        )
        self._repair_group_delivery_failures(result)
        pending_deliveries = self._mark_recoverable_group_deliveries_pending(
            conversation.state,
            result,
        )
        prev_members = frozenset(conversation.state.members)
        updated_state = self._apply_group_control_payload(
            conversation.state,
            payload,
            updated_at=utc_now(),
            epoch=result.envelope.epoch,
        )
        append_group_history_entry(
            self.get_profile_data_dir(create=True),
            self.profile,
            updated_state,
            self._build_group_history_entry(
                result.envelope,
                delivery_results={
                    peer_id: delivery.status.value
                    for peer_id, delivery in result.delivery_results.items()
                },
                delivery_reasons=self._group_delivery_reason_map(result),
            ),
            next_group_seq=result.envelope.group_seq + 1,
        )
        conversation = self._load_group_conversation(updated_state.group_id)
        if conversation is not None and pending_deliveries:
            conversation = self._merge_group_pending_deliveries(
                conversation,
                pending_deliveries,
            )
        if conversation is not None and pending_group_blindbox:
            conversation = self._merge_pending_group_blindbox_messages(
                conversation,
                pending_group_blindbox,
            )
        self._emit_message(
            "system",
            self._format_group_control_for_ui(
                updated_state,
                sender_id=sender_id,
                payload=payload,
            ),
            message_id=result.envelope.msg_id,
            conversation_kind="group",
            conversation_id=updated_state.group_id,
            conversation_title=self._group_display_name(updated_state),
            group_sender_id=sender_id,
            group_content_type=result.envelope.content_type,
            group_plain_text=render_group_control_text(payload, actor_label="You"),
        )
        self._on_group_membership_changed(
            prev_members,
            int(conversation.state.epoch),
            updated_state,
            group_label=updated_state.title or updated_state.group_id,
        )
        return result

    def import_group_transport(
        self,
        text: str,
        *,
        source_peer: Optional[str] = None,
    ) -> GroupImportResult | None:
        try:
            decoded = decode_group_transport_text(text)
        except Exception as e:
            detail = _exception_user_message(e)
            self._emit_error(f"Invalid group transport payload: {detail}")
            return GroupImportResult(
                status=GroupImportStatus.INVALID,
                error=detail,
            )
        if decoded is None:
            return None
        try:
            self._validate_imported_group_transport(decoded)
        except Exception as e:
            detail = _exception_user_message(e)
            self._emit_error(f"Invalid group transport payload: {detail}")
            return GroupImportResult(
                status=GroupImportStatus.INVALID,
                envelope=decoded.envelope,
                state=decoded.state,
                source_peer=(
                    self._normalize_peer_addr(source_peer or decoded.envelope.sender_id)
                    or None
                ),
                error=detail,
            )
        history_source_peer = source_peer or decoded.envelope.sender_id
        normalized_source_peer = self._normalize_peer_addr(history_source_peer) or None
        existing_conversation = self._load_group_conversation(decoded.state.group_id)
        normalized_msg_id = str(decoded.envelope.msg_id or "").strip()
        if (
            existing_conversation is not None
            and normalized_msg_id
            and normalized_msg_id in set(existing_conversation.seen_msg_ids)
        ):
            return GroupImportResult(
                status=GroupImportStatus.DUPLICATE,
                envelope=decoded.envelope,
                state=existing_conversation.state,
                source_peer=normalized_source_peer,
            )
        existing_state = self.load_group_state(decoded.state.group_id)
        if existing_conversation is not None:
            prev_members_for_sync = frozenset(existing_conversation.state.members)
        elif existing_state is not None:
            prev_members_for_sync = frozenset(existing_state.members)
        else:
            prev_members_for_sync = frozenset()
        merged_state = self._merge_group_state_snapshot(
            existing_state,
            decoded.state,
            envelope=decoded.envelope,
        )
        if decoded.envelope.content_type == GroupContentType.GROUP_CONTROL:
            merged_state = self._apply_group_control_payload(
                merged_state,
                decoded.envelope.payload,
                updated_at=decoded.envelope.created_at,
                epoch=decoded.envelope.epoch,
            )
        history_entry = self._build_group_history_entry(
            decoded.envelope,
            source_peer=history_source_peer,
        )
        next_group_seq = max(
            decoded.envelope.group_seq + 1,
            existing_conversation.next_group_seq if existing_conversation is not None else 1,
        )
        conversation, imported = append_group_history_entry(
            self.get_profile_data_dir(create=True),
            self.profile,
            merged_state,
            history_entry,
            next_group_seq=next_group_seq,
        )
        if not imported:
            return GroupImportResult(
                status=GroupImportStatus.DUPLICATE,
                envelope=decoded.envelope,
                state=conversation.state,
                source_peer=normalized_source_peer,
            )
        if decoded.envelope.content_type == GroupContentType.GROUP_TEXT:
            rendered_text = self._format_group_text_for_ui(
                conversation.state,
                sender_id=decoded.envelope.sender_id,
                text=str(decoded.envelope.payload or ""),
                incoming=True,
            )
            self._emit_message(
                "peer",
                rendered_text,
                source_peer=normalized_source_peer,
                message_id=decoded.envelope.msg_id,
                conversation_kind="group",
                conversation_id=conversation.state.group_id,
                conversation_title=self._group_display_name(conversation.state),
                group_sender_id=decoded.envelope.sender_id,
                group_content_type=decoded.envelope.content_type,
                group_plain_text=str(decoded.envelope.payload or ""),
            )
            self._emit_notify(
                "peer",
                rendered_text,
                source_peer=normalized_source_peer,
                conversation_kind="group",
                conversation_id=conversation.state.group_id,
                conversation_title=self._group_display_name(conversation.state),
                group_sender_id=decoded.envelope.sender_id,
                group_content_type=decoded.envelope.content_type,
                group_plain_text=str(decoded.envelope.payload or ""),
            )
        else:
            self._emit_message(
                "system",
                self._format_group_control_for_ui(
                    conversation.state,
                    sender_id=decoded.envelope.sender_id,
                    payload=(
                        decoded.envelope.payload
                        if isinstance(decoded.envelope.payload, Mapping)
                        else None
                    ),
                ),
                source_peer=normalized_source_peer,
                message_id=decoded.envelope.msg_id,
                conversation_kind="group",
                conversation_id=conversation.state.group_id,
                conversation_title=self._group_display_name(conversation.state),
                group_sender_id=decoded.envelope.sender_id,
                group_content_type=decoded.envelope.content_type,
                group_plain_text=render_group_control_text(
                    (
                        decoded.envelope.payload
                        if isinstance(decoded.envelope.payload, Mapping)
                        else None
                    ),
                    actor_label=short_member_label(decoded.envelope.sender_id),
                ),
            )
        self._on_group_membership_changed(
            prev_members_for_sync,
            (
                int(existing_conversation.state.epoch)
                if existing_conversation is not None
                else (int(existing_state.epoch) if existing_state is not None else None)
            ),
            conversation.state,
            group_label=conversation.state.title or conversation.state.group_id,
        )
        return GroupImportResult(
            status=GroupImportStatus.IMPORTED,
            envelope=decoded.envelope,
            state=conversation.state,
            source_peer=normalized_source_peer,
        )

    def import_group_transport_text(
        self,
        text: str,
        *,
        source_peer: Optional[str] = None,
    ) -> bool:
        result = self.import_group_transport(text, source_peer=source_peer)
        return bool(result is not None and result.imported)

    def is_outbound_connect_busy(self) -> bool:
        """True, пока выполняется исходящий connect_to_peer (ожидание stream_connect)."""
        return self.session_manager.outbound_connect_busy

    async def connect_to_peer(
        self,
        target_address: str,
        *,
        activate_as_current: bool = True,
        announce_to_ui: bool = True,
    ) -> None:
        target_preview = (target_address or "").strip()[:24]

        def _emit_connect_system(text: str) -> None:
            if announce_to_ui:
                self._emit_system(text)
            else:
                logger.info("Silent connect to %s: %s", target_preview, text)

        def _emit_connect_error(text: str) -> None:
            if announce_to_ui:
                self._emit_error(text)
            else:
                logger.info(
                    "Silent connect to %s suppressed UI error: %s",
                    target_preview,
                    text,
                )

        if not crypto.NACL_AVAILABLE:
            detail = getattr(crypto, "NACL_IMPORT_ERROR", "") or "pynacl not installed"
            _emit_connect_error(
                "Secure protocol requires PyNaCl. Install: pip install pynacl. "
                f"({detail})"
            )
            return
        try:
            normalized_target = self._normalize_peer_addr(target_address)
        except ValueError as e:
            _emit_connect_error(str(e).strip() or "Invalid peer address")
            return
        target_preview = normalized_target[:24]
        self.ensure_peer_in_saved_contacts(normalized_target)
        if self._has_live_session_slot_for_peer(normalized_target):
            _emit_connect_system("Already connected to this peer.")
            return
        max_live = max_concurrent_live_sessions()
        if self._live_stream_count() >= max_live:
            _emit_connect_system(
                f"Maximum concurrent live sessions ({max_live}) reached. "
                "Disconnect a peer first."
            )
            return
        if self.session_manager.outbound_connect_busy:
            _emit_connect_system("Connection attempt already in progress.")
            return
        extra_ls = LivePeerSession(
            peer_id=normalized_target,
            announce_lifecycle=bool(activate_as_current and announce_to_ui),
        )
        self._live_sessions[normalized_target] = extra_ls
        if activate_as_current:
            self.session_manager.set_active_peer(normalized_target)
        self.session_manager.set_outbound_connect_busy(True, peer_id=normalized_target)
        self.session_manager.set_peer_connected(
            normalized_target, state=PeerState.CONNECTING, reason="outbound-connect"
        )
        self.session_manager.transition_transport(
            TransportState.RECONNECTING, reason="outbound-connect"
        )
        deferred_error: Optional[str] = None
        deferred_system: Optional[str] = None
        try:
            try:
                if activate_as_current:
                    self.activate_peer_context(normalized_target)
                    if announce_to_ui:
                        self._emit_system(
                            f"Connecting to {normalized_target[:24]}... "
                            "(may take 1–2 min while I2P builds tunnels)"
                        )
                    else:
                        logger.info(
                            "Silent connect to %s started.",
                            normalized_target[:24],
                        )
                else:
                    logger.info(
                        "Group intro: background connect to %s...",
                        normalized_target[:24],
                    )
                reader: asyncio.StreamReader
                writer: asyncio.StreamWriter
                last_connect_exc: Optional[Exception] = None
                for attempt in range(2):
                    try:
                        reader, writer = await asyncio.wait_for(
                            i2plib.stream_connect(
                                self.session_id,
                                self._peer_sam_hostname(normalized_target),
                                sam_address=self.sam_address,
                            ),
                            timeout=self.CONNECT_TIMEOUT,
                        )
                        break
                    except Exception as e:
                        last_connect_exc = e
                        is_first_attempt = attempt == 0
                        if is_first_attempt and _is_cant_reach_peer_error(e):
                            logger.info(
                                "CantReachPeer on first connect attempt; retrying once after 2s"
                            )
                            await asyncio.sleep(2.0)
                            continue
                        raise
                else:
                    if last_connect_exc is None:
                        raise RuntimeError(
                            "outbound connect loop exited without success but no exception was recorded"
                        )
                    raise last_connect_exc

                if self._live_sessions.get(normalized_target) is not extra_ls:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                    logger.info(
                        "Outbound connect to %s yielded to another session slot.",
                        normalized_target[:24],
                    )
                    return

                if self.my_dest is not None:
                    # Backward-safe identity preface для accept_loop(reader.readline()).
                    writer.write(self.my_dest.base64.encode("utf-8") + b"\n")
                    writer.write(
                        self.frame_message_plain(
                            "S", self.my_dest.base64, peer_id=normalized_target
                        )
                    )
                    await writer.drain()

                    extra_ls.proven = True
                    self._emit_status("visible")

                if self._live_sessions.get(normalized_target) is not extra_ls:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                    logger.info(
                        "Outbound connect to %s yielded after preface write.",
                        normalized_target[:24],
                    )
                    return

                connection = (reader, writer)
                extra_ls.conn = connection
                self.session_manager.register_stream(
                    normalized_target,
                    state=PeerState.HANDSHAKING,
                    peer_id=normalized_target,
                )
                self._activate_ack_session(normalized_target)
                if extra_ls.announce_lifecycle:
                    self._emit_message(
                        "info", "Handshake sent. Establishing secure channel... Wait"
                    )

                loop = asyncio.get_running_loop()
                self._start_receive_loop_task(connection, peer_id=normalized_target)
                loop.create_task(
                    self.initiate_secure_handshake(normalized_target)
                )
                self._start_handshake_watchdog(
                    connection, peer_id=normalized_target
                )
                if (
                    self.session_manager.keepalive_task is None
                    or self.session_manager.keepalive_task.done()
                ):
                    self.session_manager.keepalive_task = loop.create_task(
                        self._keepalive_loop()
                    )
            except asyncio.TimeoutError:
                self._live_sessions.pop(normalized_target, None)
                self.session_manager.mark_peer_failed(
                    normalized_target, reason="connect-timeout"
                )
                delay = self.session_manager.schedule_reconnect_backoff(
                    reason="connect-timeout",
                    peer_id=normalized_target,
                )
                logger.info("Outbound connect backoff scheduled: %.2fs", delay)
                deferred_error = (
                    "Connection timed out. Check: I2P router running, peer address correct, peer online."
                )
                deferred_system = "Waiting for incoming connections..."
            except Exception as e:
                self._live_sessions.pop(normalized_target, None)
                self.session_manager.mark_peer_failed(
                    normalized_target, reason="connect-failed"
                )
                delay = self.session_manager.schedule_reconnect_backoff(
                    reason=type(e).__name__,
                    peer_id=normalized_target,
                )
                logger.info("Outbound connect backoff scheduled: %.2fs", delay)
                # Пустое сообщение у SAM-исключений (например CantReachPeer()) — только имя типа,
                # без «CantReachPeer: CantReachPeer()».
                detail = str(e).strip() or type(e).__name__
                hint = _sam_stream_connect_hint(e)
                deferred_error = (
                    f"Connection failed: {detail}" + (f" {hint}" if hint else "")
                )
                deferred_system = "Waiting for incoming connections..."
        finally:
            self.session_manager.set_outbound_connect_busy(
                False, peer_id=normalized_target
            )

        # После сброса busy — иначе UI обновится с is_outbound_connect_busy()==True и Connect останется серым.
        if activate_as_current and announce_to_ui:
            if deferred_error:
                self._emit_error(deferred_error)
            if deferred_system:
                self._emit_system(deferred_system)
        elif deferred_error:
            logger.info(
                "Background/silent connect to %s failed without UI notice: %s",
                normalized_target[:24],
                deferred_error,
            )

    async def send_text(
        self,
        text: str,
        *,
        route: Literal["auto", "live", "offline"] = "auto",
        peer_address: Optional[str] = None,
    ) -> SendTextResult:
        if not text:
            lifecycle = delivery_lifecycle_from_send_result(
                route="blocked",
                accepted=False,
                reason="empty-text",
                hint="Message is empty.",
            )
            return SendTextResult(
                route="blocked",
                accepted=False,
                reason="empty-text",
                hint="Message is empty.",
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )
        r = route if route in ("auto", "live", "offline") else "auto"
        peer_for_route = ""
        if peer_address and str(peer_address).strip():
            try:
                peer_for_route = self._normalize_peer_addr(peer_address)
            except ValueError:
                peer_for_route = ""
        if not peer_for_route:
            lap = self._last_active_peer_for_telemetry()
            peer_for_route = self._normalize_peer_addr(
                self.current_peer_addr or lap or ""
            )
        connected = (
            bool(peer_for_route and self._has_active_session_for_peer(peer_for_route))
            if peer_for_route
            else self._any_live_stream()
        )
        live_kwargs: dict[str, Any] = {"peer_id": peer_for_route}
        policy_kwargs: dict[str, Any] = {
            "requested_route": r,
            "peer_id": peer_for_route,
        }
        if not peer_for_route:
            live_kwargs["connected"] = connected
            live_kwargs["handshake_complete"] = self._handshake_complete_for_peer_route(
                ""
            )
            policy_kwargs["connected"] = connected
            policy_kwargs["handshake_complete"] = self._handshake_complete_for_peer_route(
                ""
            )

        policy = self.session_manager.select_outbound_policy(**policy_kwargs)

        if policy == OutboundPolicy.BLINDBOX_ONLY:
            if self._blindbox_send_lock.locked():
                reason, hint = self._blindbox_send_busy_feedback()
                self._emit_error(hint)
                lifecycle = delivery_lifecycle_from_send_result(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                )
                return SendTextResult(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                    delivery_state=lifecycle.state,
                    retryable=lifecycle.retryable,
                )
            sent_offline = await self._send_text_via_blindbox(
                text, peer_address=peer_for_route
            )
            if sent_offline is None:
                reason, hint = self._offline_send_block_feedback()
                self._emit_error(hint)
                lifecycle = delivery_lifecycle_from_send_result(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                )
                return SendTextResult(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                    delivery_state=lifecycle.state,
                    retryable=lifecycle.retryable,
                )
            lifecycle = delivery_lifecycle_from_send_result(
                route="offline-queued",
                accepted=True,
                reason="blindbox-ready",
                hint="Message queued for offline delivery.",
            )
            return SendTextResult(
                route="offline-queued",
                accepted=True,
                reason="blindbox-ready",
                hint="Message queued for offline delivery.",
                message_id=str(sent_offline),
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )

        secure_live = self.session_manager.is_live_path_alive(**live_kwargs)

        if policy == OutboundPolicy.LIVE_ONLY:
            if not secure_live:
                if connected and not self._handshake_complete_for_peer_route(
                    peer_for_route
                ):
                    hint = (
                        "Secure channel handshake is in progress. "
                        "Please wait before sending live."
                    )
                    reason = "handshake-in-progress"
                else:
                    hint = (
                        "Live send needs an active secure session. "
                        "Press Connect and wait until the session is ready."
                    )
                    reason = "needs-live-session"
                self._emit_error(hint)
                lifecycle = delivery_lifecycle_from_send_result(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                )
                return SendTextResult(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                    delivery_state=lifecycle.state,
                    retryable=lifecycle.retryable,
                )

        if policy == OutboundPolicy.QUEUE_THEN_RETRY_LIVE and not secure_live:
            if self._blindbox_send_lock.locked():
                reason, hint = self._blindbox_send_busy_feedback()
                self._emit_error(hint)
                lifecycle = delivery_lifecycle_from_send_result(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                )
                return SendTextResult(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                    delivery_state=lifecycle.state,
                    retryable=lifecycle.retryable,
                )
            sent_offline = await self._send_text_via_blindbox(
                text, peer_address=peer_for_route
            )
            if sent_offline is None:
                reason, hint = self._offline_send_block_feedback()
                self._emit_error(hint)
                lifecycle = delivery_lifecycle_from_send_result(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                )
                return SendTextResult(
                    route="blocked",
                    accepted=False,
                    reason=reason,
                    hint=hint,
                    delivery_state=lifecycle.state,
                    retryable=lifecycle.retryable,
                )
            lifecycle = delivery_lifecycle_from_send_result(
                route="offline-queued",
                accepted=True,
                reason="blindbox-ready",
                hint="Message queued for offline delivery.",
            )
            return SendTextResult(
                route="offline-queued",
                accepted=True,
                reason="blindbox-ready",
                hint="Message queued for offline delivery.",
                message_id=str(sent_offline),
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )
        if not self._require_secure_channel(
            outbound_peer=peer_for_route or None
        ):
            lifecycle = delivery_lifecycle_from_send_result(
                route="blocked",
                accepted=False,
                reason="secure-channel-not-ready",
                hint="Secure channel is not ready.",
            )
            return SendTextResult(
                route="blocked",
                accepted=False,
                reason="secure-channel-not-ready",
                hint="Secure channel is not ready.",
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )
        try:
            writer, frame_peer_id, text_ack_table = self._writer_frame_peer_and_text_acks(
                peer_for_route
            )
            if writer is None:
                raise ConnectionError("No live connection for the selected peer")
            if self._blindbox_ready():
                await self._send_blindbox_root_if_needed(
                    writer, peer_id=frame_peer_id
                )
            chunks = split_long_chat_text(text)
            last_msg_id: Optional[int] = None
            lifecycle = delivery_lifecycle_from_send_result(
                route="online-live",
                accepted=True,
                reason="live-session",
                hint="Message sent over live secure session.",
            )
            for chunk in chunks:
                frame, msg_id = self.frame_message_with_id(
                    "U", chunk, peer_id=frame_peer_id
                )
                writer.write(frame)
                await writer.drain()
                routing_pid = frame_peer_id or self._normalize_peer_addr(peer_for_route)
                self._register_pending_ack(
                    text_ack_table,
                    msg_id,
                    token=chunk[:128],
                    ack_kind="msg",
                    routing_peer_id=routing_pid,
                )
                self._emit_message(
                    "me",
                    chunk,
                    message_id=str(msg_id),
                    delivery_state=lifecycle.state,
                    delivery_route="online-live",
                    delivery_hint=lifecycle.hint,
                    delivery_reason=lifecycle.reason,
                    retryable=lifecycle.retryable,
                )
                last_msg_id = msg_id
            return SendTextResult(
                route="online-live",
                accepted=True,
                reason="live-session",
                hint="Message sent over live secure session.",
                message_id=str(last_msg_id) if last_msg_id is not None else None,
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            self.session_manager.mark_live_failure(
                reason="send-failed",
                peer_id=peer_for_route,
            )
            self._schedule_disconnect(self._peer_id_for_frame())
            lifecycle = delivery_lifecycle_from_send_result(
                route="blocked",
                accepted=False,
                reason="send-failed",
                hint=str(e),
            )
            return SendTextResult(
                route="blocked",
                accepted=False,
                reason="send-failed",
                hint=str(e),
                delivery_state=lifecycle.state,
                retryable=lifecycle.retryable,
            )

    async def _write_signal_frame_maybe_soft_drain(
        self,
        writer: asyncio.StreamWriter,
        frame: bytes,
        *,
        sess: Any,
    ) -> None:
        """S-фрейм (MSG_ACK, IMG_ACK): при исходящей передаче файла реже await drain()."""
        writer.write(frame)
        if not sess._file_transfer_active:
            await writer.drain()
            sess._soft_signal_ack_since_drain = 0
            return
        sess._soft_signal_ack_since_drain += 1
        if sess._soft_signal_ack_since_drain >= _msg_ack_soft_drain_every():
            await writer.drain()
            sess._soft_signal_ack_since_drain = 0

    async def _send_abort_file(self, peer_for_route: Optional[str] = None) -> None:
        """Отправить пиру сигнал отмены передачи файла (получатель отменил или отправитель)."""
        lap = self._last_active_peer_for_telemetry()
        pr = self._normalize_peer_addr(
            peer_for_route or self.current_peer_addr or lap or ""
        )
        writer, fpid, _ = self._writer_frame_peer_and_file_acks(pr)
        if writer is None:
            return
        try:
            writer.write(
                self.frame_message("S", "__SIGNAL__:ABORT_FILE", peer_id=fpid)
            )
            await writer.drain()
        except Exception:
            pass

    async def reject_incoming_file(
        self, filename: str, *, peer_for_route: Optional[str] = None
    ) -> None:
        """Уведомить отправителя, что получатель отклонил входящий файл."""
        lap = self._last_active_peer_for_telemetry()
        pr = self._normalize_peer_addr(
            peer_for_route or self.current_peer_addr or lap or ""
        )
        writer, fpid, _ = self._writer_frame_peer_and_file_acks(pr)
        if writer is None:
            return
        try:
            writer.write(
                self.frame_message(
                    "S",
                    f"__SIGNAL__:REJECT_FILE|{filename}",
                    peer_id=fpid,
                )
            )
            await writer.drain()
        except Exception:
            pass

    def cancel_file_transfer(self) -> None:
        """Отменить текущую передачу файла (на получателе — также уведомить отправителя)."""
        lap = self._last_active_peer_for_telemetry()
        pr = self._normalize_peer_addr(self.current_peer_addr or lap or "")
        sess = self._session_view_for_peer_route(pr)
        sess._cancel_transfer = True
        self._cancel_transfer = True
        if sess.incoming_file:
            try:
                sess.incoming_file.close()
            except Exception:
                pass
            sess.incoming_file = None
        if sess.incoming_info:
            self._emit_file_event(
                FileTransferInfo(
                    filename=sess.incoming_info.filename,
                    size=sess.incoming_info.size,
                    received=-1,
                    is_sending=False,
                )
            )
            sess.incoming_info = None
        if sess.conn:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._send_abort_file(pr))
            except RuntimeError:
                pass

    async def send_file(
        self, path: str, *, peer_address: Optional[str] = None
    ) -> None:
        peer_for_route = ""
        if peer_address and str(peer_address).strip():
            try:
                peer_for_route = self._normalize_peer_addr(peer_address)
            except ValueError:
                peer_for_route = ""
        if not peer_for_route:
            lap = self._last_active_peer_for_telemetry()
            peer_for_route = self._normalize_peer_addr(
                self.current_peer_addr or lap or ""
            )
        if not peer_for_route:
            self._emit_error("No peer selected for file transfer.")
            return
        if not self._require_secure_channel(outbound_peer=peer_for_route):
            return

        writer, fpid, file_acks = self._writer_frame_peer_and_file_acks(peer_for_route)
        if writer is None:
            self._emit_error("No live connection for the selected peer.")
            return

        sess = self._session_view_for_peer_route(peer_for_route)
        filename = os.path.basename(path)
        filesize = os.path.getsize(path)

        sess._file_transfer_active = True
        sess._soft_signal_ack_since_drain = 0
        sess._cancel_transfer = False
        sess._transfer_aborted_by_peer = False
        sess._transfer_rejected_by_peer = False

        try:
            self._emit_system(f"Sending file: {filename} ({filesize} bytes)")

            header = f"{filename}|{filesize}"
            header_frame, file_msg_id = self.frame_message_with_id(
                "F", header, peer_id=fpid
            )
            writer.write(header_frame)
            await writer.drain()
            routing_pid = fpid or self._normalize_peer_addr(peer_for_route)
            self._register_pending_ack(
                file_acks,
                file_msg_id,
                token=os.path.basename(filename),
                ack_kind="file",
                routing_peer_id=routing_pid,
            )

            info = FileTransferInfo(
                filename=filename,
                size=filesize,
                received=0,
                is_sending=True,
                source_path=path,
            )
            self._emit_file_event(info)

            chunk_size = _file_read_chunk_bytes()
            drain_batch = _file_send_drain_batch()
            sent = 0
            pending_drains = 0
            with open(path, "rb") as f:
                while True:
                    if sess._cancel_transfer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        await self._send_abort_file(peer_for_route)
                        raise Exception("Transfer cancelled by user")
                    if sess._transfer_aborted_by_peer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        self._emit_system("Receiver cancelled the transfer")
                        raise Exception("Transfer cancelled by receiver")
                    if sess._transfer_rejected_by_peer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        raise Exception("Receiver rejected the file")
                    if not sess.conn:
                        raise ConnectionError("Connection lost during transfer")

                    chunk = await asyncio.to_thread(f.read, chunk_size)
                    if not chunk:
                        break

                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("D", encoded, peer_id=fpid))
                    pending_drains += 1
                    if pending_drains >= drain_batch:
                        t0 = time.monotonic()
                        await writer.drain()
                        if self._file_xfer_debug:
                            dt = time.monotonic() - t0
                            if dt >= 0.25:
                                logger.info(
                                    "file xfer send: slow drain %.3fs bytes_sent=%s batch=%s",
                                    dt,
                                    sent + len(chunk),
                                    drain_batch,
                                )
                        pending_drains = 0

                    sent += len(chunk)
                    if should_emit_file_progress(sent, len(chunk), filesize):
                        info = FileTransferInfo(
                            filename=filename,
                            size=filesize,
                            received=sent,
                            is_sending=True,
                            source_path=path,
                        )
                        self._emit_file_event(info)

            if pending_drains:
                await writer.drain()

            writer.write(self.frame_message("E", "", peer_id=fpid))
            await writer.drain()

            info = FileTransferInfo(
                filename=filename,
                size=filesize,
                received=filesize,
                is_sending=True,
                source_path=path,
            )
            self._emit_file_event(info)

            if sess.conn:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.receive_loop(sess.conn, peer_id=fpid))
                except RuntimeError:
                    pass

        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError) as e:
            info = FileTransferInfo(
                filename=filename,
                size=filesize,
                received=-1,
                is_sending=True,
                source_path=path,
            )
            self._emit_file_event(info)
            self._emit_error(f"File transfer interrupted: connection lost")

        except Exception as e:
            rejected = "rejected" in str(e).lower()
            info = FileTransferInfo(
                filename=filename,
                size=filesize,
                received=-1,
                is_sending=True,
                rejected_by_peer=rejected,
                source_path=path,
            )
            self._emit_file_event(info)
            if rejected:
                self._emit_error("Receiver rejected the file.")
            else:
                self._emit_error(f"File transfer failed: {e}")
        finally:
            sess._file_transfer_active = False
            sess._soft_signal_ack_since_drain = 0

    async def send_image_lines(
        self, lines: list[str], *, peer_address: Optional[str] = None
    ) -> None:
        """Отправить уже отрендеренное изображение построчно."""
        peer_for_route = ""
        if peer_address and str(peer_address).strip():
            try:
                peer_for_route = self._normalize_peer_addr(peer_address)
            except ValueError:
                peer_for_route = ""
        if not peer_for_route:
            lap = self._last_active_peer_for_telemetry()
            peer_for_route = self._normalize_peer_addr(
                self.current_peer_addr or lap or ""
            )
        if not self._require_secure_channel(outbound_peer=peer_for_route):
            return
        writer, fpid, _ = self._writer_frame_peer_and_text_acks(peer_for_route)
        if writer is None:
            self._emit_error("No live connection for the selected peer.")
            return

        for line in lines:
            writer.write(self.frame_message("I", line, peer_id=fpid))
        writer.write(self.frame_message("I", "__END__", peer_id=fpid))
        await writer.drain()

    async def send_image(
        self, path: str, *, peer_address: Optional[str] = None
    ) -> Optional[str]:
        """
        Отправить изображение (PNG/JPEG/WebP) с валидацией.
        
        Args:
            path: путь к файлу изображения
            
        Returns:
            путь к копии изображения в images/ или None при ошибке
        """
        # Валидация изображения
        is_valid, error_msg, detected_ext = validate_image(path)
        if not is_valid:
            self._emit_error(f"Invalid image: {error_msg}")
            return None
        
        filename = sanitize_filename(os.path.basename(path))
        filesize = os.path.getsize(path)
        
        # Копируем изображение в images/ для локального отображения
        import hashlib
        with open(path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()[:8]
        
        local_filename = f"img_{int(time.time())}_{file_hash}.{detected_ext}"
        local_path = os.path.join(get_images_dir(), local_filename)
        
        try:
            import shutil
            shutil.copy2(path, local_path)
        except Exception as e:
            self._emit_error(f"Failed to copy image: {e}")
            return None

        peer_for_route = ""
        if peer_address and str(peer_address).strip():
            try:
                peer_for_route = self._normalize_peer_addr(peer_address)
            except ValueError:
                peer_for_route = ""
        if not peer_for_route:
            lap = self._last_active_peer_for_telemetry()
            peer_for_route = self._normalize_peer_addr(
                self.current_peer_addr or lap or ""
            )
        if not peer_for_route:
            self._emit_error("No peer selected for image transfer.")
            return None
        if not self._require_secure_channel(outbound_peer=peer_for_route):
            return None

        writer, fpid, image_acks = self._writer_frame_peer_and_image_acks(
            peer_for_route
        )
        if writer is None:
            self._emit_error("No live connection for the selected peer.")
            return None

        sess = self._session_view_for_peer_route(peer_for_route)
        sess._file_transfer_active = True
        sess._soft_signal_ack_since_drain = 0
        sess._cancel_transfer = False

        try:
            self._emit_system(f"Sending image: {filename} ({filesize} bytes)")

            # Прогресс загрузки в UI (is_inline_image — чтобы GUI заменил виджет на превью, не «File sent»)
            self._emit_file_event(
                FileTransferInfo(
                    filename=filename,
                    size=filesize,
                    received=0,
                    is_sending=True,
                    is_inline_image=True,
                    source_path=path,
                )
            )

            # Отправляем заголовок: G + filename|size
            header = f"{filename}|{filesize}"
            header_frame, image_msg_id = self.frame_message_with_id(
                "G", header, peer_id=fpid
            )
            writer.write(header_frame)
            await writer.drain()
            routing_pid = fpid or self._normalize_peer_addr(peer_for_route)
            self._register_pending_ack(
                image_acks,
                image_msg_id,
                token=os.path.basename(filename),
                ack_kind="image",
                routing_peer_id=routing_pid,
            )

            chunk_size = _file_read_chunk_bytes()
            drain_batch = _file_send_drain_batch()
            sent = 0
            pending_drains = 0
            with open(path, "rb") as f:
                while True:
                    if sess._cancel_transfer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        raise Exception("Transfer cancelled by user")
                    if not sess.conn:
                        raise ConnectionError("Connection lost during transfer")

                    chunk = await asyncio.to_thread(f.read, chunk_size)
                    if not chunk:
                        break

                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("G", encoded, peer_id=fpid))
                    pending_drains += 1
                    if pending_drains >= drain_batch:
                        t0 = time.monotonic()
                        await writer.drain()
                        if self._file_xfer_debug:
                            dt = time.monotonic() - t0
                            if dt >= 0.25:
                                logger.info(
                                    "file xfer image send: slow drain %.3fs bytes_sent=%s batch=%s",
                                    dt,
                                    sent + len(chunk),
                                    drain_batch,
                                )
                        pending_drains = 0

                    sent += len(chunk)
                    if should_emit_file_progress(sent, len(chunk), filesize):
                        self._emit_file_event(
                            FileTransferInfo(
                                filename=filename,
                                size=filesize,
                                received=sent,
                                is_sending=True,
                                is_inline_image=True,
                                source_path=path,
                            )
                        )

            if pending_drains:
                await writer.drain()

            # Отправляем маркер завершения
            writer.write(self.frame_message("G", "__IMG_END__", peer_id=fpid))
            await writer.drain()
            
            self._emit_file_event(
                FileTransferInfo(
                    filename=filename,
                    size=filesize,
                    received=filesize,
                    is_sending=True,
                    is_inline_image=True,
                    source_path=path,
                )
            )
            
            # Уведомляем UI об отправленном изображении (filename для галочки доставки)
            self._emit_inline_image(local_path, is_from_me=True, sent_filename=filename)
            
            # Очистка кэша при необходимости
            cleanup_images_cache()
            
            return local_path
            
        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError) as e:
            self._emit_file_event(
                FileTransferInfo(
                    filename=filename,
                    size=filesize,
                    received=-1,
                    is_sending=True,
                    is_inline_image=True,
                    source_path=path,
                )
            )
            self._emit_error(f"Image transfer interrupted: connection lost")
            return None
            
        except Exception as e:
            self._emit_file_event(
                FileTransferInfo(
                    filename=filename,
                    size=filesize,
                    received=-1,
                    is_sending=True,
                    is_inline_image=True,
                    source_path=path,
                )
            )
            self._emit_error(f"Image transfer failed: {e}")
            return None
        finally:
            sess._file_transfer_active = False
            sess._soft_signal_ack_since_drain = 0

    async def send_control(self, signal: str) -> None:
        lap = self._last_active_peer_for_telemetry()
        pr = self._normalize_peer_addr(self.current_peer_addr or lap or "")
        if not pr:
            return
        writer, fpid, _ = self._writer_frame_peer_and_text_acks(pr)
        if writer is None:
            return
        try:
            writer.write(
                self.frame_message("S", f"__SIGNAL__:{signal}", peer_id=fpid)
            )
            await writer.drain()
        except Exception:
            pass

    def _any_live_stream(self) -> bool:
        return any(s.conn for s in self._live_sessions.values())

    def any_live_stream(self) -> bool:
        """Публичная проверка: есть ли хотя бы один активный SAM live-поток."""
        return self._any_live_stream()

    def live_stream_count(self) -> int:
        """Число активных SAM live-потоков."""
        return self._live_stream_count()

    def is_current_peer_secure(self) -> bool:
        """Handshake завершён для текущего выбранного пира."""
        p = self._normalize_peer_addr(self.current_peer_addr or "")
        return self._handshake_complete_for_peer_route(p)

    async def _maybe_stop_keepalive_if_idle(self) -> None:
        if self._any_live_stream():
            return
        if self.session_manager.keepalive_task:
            asyncio.get_running_loop().call_soon(
                self.session_manager.keepalive_task.cancel
            )
            self.session_manager.keepalive_task = None

    async def _yield_session_slot_to_incoming(
        self,
        peer_id: str,
        *,
        expected_session: Optional[LivePeerSession] = None,
    ) -> None:
        k = self._normalize_peer_addr(peer_id)
        current = self._live_sessions.get(k)
        if current is None:
            return
        if expected_session is not None and current is not expected_session:
            return
        receive_task = current.receive_task
        if (
            receive_task is not None
            and receive_task is not asyncio.current_task()
            and not receive_task.done()
        ):
            receive_task.cancel()
        current.receive_task = None
        writer: Optional[asyncio.StreamWriter] = None
        if current.conn is not None:
            _, writer = current.conn
            current.conn = None
        self._live_sessions.pop(k, None)
        self._cancel_handshake_watchdog(k)
        current.reset_crypto()
        self.session_manager.reset_peer_lifecycle(
            k, reason="incoming-preferred", keep_reconnect_metadata=True
        )
        if self.active_live_peer_id == k:
            self.active_live_peer_id = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _disconnect_extra_session(self, peer_id: str) -> None:
        k = self._normalize_peer_addr(peer_id)
        ls = self._live_sessions.get(k)
        if not ls:
            return
        receive_task = ls.receive_task
        if (
            receive_task is not None
            and receive_task is not asyncio.current_task()
            and not receive_task.done()
        ):
            receive_task.cancel()
        ls.receive_task = None
        if not ls.conn:
            self._live_sessions.pop(k, None)
            self._cancel_handshake_watchdog(k)
            self.session_manager.reset_peer_lifecycle(k, reason="disconnect-pending")
            self.session_manager.mark_live_failure(
                reason="disconnect",
                mark_peer_stale=False,
                peer_id=k,
            )
            if self.active_live_peer_id == k:
                self.active_live_peer_id = None
            return
        _, writer = ls.conn
        ls.conn = None
        had_secure = ls.handshake_complete and ls.use_encryption and bool(ls.shared_key)
        try:
            quit_frame: Optional[bytes] = None
            if had_secure:
                quit_frame = self.frame_message("S", "__SIGNAL__:QUIT", peer_id=k)
            else:
                quit_frame = self.frame_message_plain("S", "__SIGNAL__:QUIT", peer_id=k)
            if quit_frame is not None:
                writer.write(quit_frame)
                await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        self._live_sessions.pop(k, None)
        ls.reset_crypto()
        self.session_manager.reset_peer_lifecycle(k, reason="disconnect")
        self.session_manager.mark_live_failure(
            reason="disconnect",
            mark_peer_stale=False,
            peer_id=k,
        )
        if self.active_live_peer_id == k:
            self.active_live_peer_id = None
        if ls.announce_lifecycle:
            self._emit_message("info", f"You disconnected from {k[:16]}...")
        self._notify_group_mesh_manager()
        await self._maybe_stop_keepalive_if_idle()
        if self._any_live_stream() and (
            self.session_manager.keepalive_task is None
            or self.session_manager.keepalive_task.done()
        ):
            try:
                loop = asyncio.get_running_loop()
                self.session_manager.keepalive_task = loop.create_task(
                    self._keepalive_loop()
                )
            except RuntimeError:
                pass

    async def disconnect_peer(self, peer_id: Optional[str] = None) -> None:
        """
        Закрыть одну live-сессию в ``_live_sessions``.
        ``peer_id=None`` — сессия для ``active_live_peer_id`` или ``current_peer_addr``.
        """
        if peer_id is None:
            k = self._normalize_peer_addr(
                self.active_live_peer_id or self.current_peer_addr or ""
            )
            if not k:
                return
            await self._disconnect_extra_session(k)
            return
        await self._disconnect_extra_session(self._normalize_peer_addr(peer_id))

    async def disconnect(self) -> None:
        """Отключить выбранную live-сессию: сначала active_live_peer_id, иначе current_peer_addr при наличии потока."""
        ap = self.active_live_peer_id
        if not ap:
            cur = self._normalize_peer_addr(self.current_peer_addr or "")
            if cur and self._has_active_session_for_peer(cur):
                ap = cur
        if ap and self._has_active_session_for_peer(ap):
            await self.disconnect_peer(ap)
            return
        await self.disconnect_peer(None)

    async def _keepalive_loop(self) -> None:
        """Отправляет Ping каждые 15 секунд для всех live-потоков."""
        while self._any_live_stream():
            await asyncio.sleep(15)
            for pid, ls in list(self._live_sessions.items()):
                if not ls.conn or ls._file_transfer_active:
                    continue
                if not (
                    ls.handshake_complete and ls.use_encryption and ls.shared_key
                ):
                    continue
                try:
                    _, w = ls.conn
                    w.write(self.frame_message("P", "", peer_id=pid))
                    await w.drain()
                except Exception:
                    self.session_manager.mark_live_failure(
                        reason="keepalive-failed",
                        peer_id=pid,
                    )
    
    def _reset_crypto_state(self) -> None:
        """Сбрасывает криптографическое состояние при отключении."""
        self.shared_key = None
        self.shared_mac_key = None
        self.my_nonce = None
        self.peer_nonce = None
        self.my_ephemeral_private = None
        self.my_ephemeral_public = None
        self.peer_ephemeral_public = None
        self.peer_signing_public = None
        self.use_encryption = False
        self.handshake_complete = False
        self._handshake_initiated = False
        self._send_seq = 0
        self._recv_seq = 0
        self._pending_text_acks.clear()
        self._pending_file_acks.clear()
        self._pending_image_acks.clear()
        self._incoming_file_msg_id = None
        self._incoming_image_msg_id = None
        self._ack_session_epoch = 0
        self.peer_identity_binding_verified = False
        self.current_peer_dest_b64 = None

    async def initiate_secure_handshake(self, peer_id: Optional[str] = None) -> bool:
        """
        Инициирует защищённый handshake (v2 протокол с PFS).
        
        Обязательный NaCl-режим с эфемерными X25519 ключами (PFS).
        Формат: INIT:<nonce_hex>:<ephemeral_pubkey_hex>:<sign_pub_hex>:<signature_hex>
        
        Returns:
            True если handshake успешен
        """
        if not peer_id:
            return False
        ls = self._live_sessions.get(self._normalize_peer_addr(peer_id))
        if not ls or not ls.conn:
            return False
        _, writer = ls.conn
        sess = ls
        if not crypto.NACL_AVAILABLE:
            self._emit_error("PyNaCl is required for secure protocol")
            self._schedule_disconnect(peer_id)
            return False

        try:
            sess.my_nonce = crypto.generate_nonce()
            sess.my_ephemeral_private, sess.my_ephemeral_public = (
                crypto.generate_ephemeral_keypair()
            )
            if not self.my_signing_seed or not self.my_signing_public:
                raise ValueError("Local handshake signing key is missing")
            peer_addr = self._normalize_peer_addr(peer_id or self.current_peer_addr or "")
            if not peer_addr:
                raise ValueError("Peer address is unknown")
            init_nonce_hex = sess.my_nonce.hex()
            init_eph_hex = sess.my_ephemeral_public.hex()
            init_sign_pub_hex = self.my_signing_public.hex()
            if not self.my_dest:
                raise ValueError("Local destination is not initialized")
            init_sig_payload = self._build_init_sig_payload(
                self.my_dest.base32,
                peer_addr,
                init_nonce_hex,
                init_eph_hex,
                init_sign_pub_hex,
            )
            init_sig_hex = crypto.sign_data(
                self.my_signing_seed,
                init_sig_payload,
            ).hex()
            handshake_data = (
                f"INIT:{init_nonce_hex}:{init_eph_hex}:{init_sign_pub_hex}:{init_sig_hex}"
            )
            sess._handshake_initiated = True
            if sess.announce_lifecycle:
                self._emit_system("Initiating secure handshake with PFS...")
            writer.write(self.frame_message_plain("H", handshake_data, peer_id=peer_id))
            await writer.drain()
            return True
        except Exception as e:
            logger.error(f"Handshake initiation failed: {e}")
            self._schedule_disconnect(peer_id)
            return False

    def _compute_session_subkeys(self, is_initiator: bool, sess: Any) -> Tuple[bytes, bytes]:
        """
        Вычисляет финальные subkeys для сессии.

        С PFS + key separation:
        HKDF(dh_shared, nonce_init, nonce_resp) -> (k_enc, k_mac)
        """
        if not crypto.NACL_AVAILABLE:
            raise RuntimeError("PyNaCl is required for secure protocol")
        if not sess.my_ephemeral_private or not sess.peer_ephemeral_public:
            raise ValueError("Missing ephemeral keys")
        if not sess.my_nonce or not sess.peer_nonce:
            raise ValueError("Missing handshake nonces")

        dh_shared = crypto.compute_dh_shared_secret(
            sess.my_ephemeral_private, sess.peer_ephemeral_public
        )
        if is_initiator:
            nonce_init = sess.my_nonce
            nonce_resp = sess.peer_nonce
        else:
            nonce_init = sess.peer_nonce
            nonce_resp = sess.my_nonce
        return crypto.derive_handshake_subkeys(dh_shared, nonce_init, nonce_resp)

    def _should_initiate_blindbox_root_exchange(
        self, peer_id: Optional[str] = None
    ) -> bool:
        if not self._blindbox_ready() or not self.my_dest:
            return False
        target_peer = self._blindbox_peer_id_for_peer(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer:
            return False
        local_id = self.my_dest.base32.strip().lower()
        return local_id < target_peer.strip().lower()

    def _blindbox_should_rotate_root(
        self, *, snapshot: Optional[_BlindBoxPeerSnapshot] = None
    ) -> bool:
        root_secret = (
            self._blindbox_root_secret if snapshot is None else snapshot.root_secret
        )
        if root_secret is None:
            return False
        now_ts = int(time.time())
        root_created_at = (
            self._blindbox_root_created_at
            if snapshot is None
            else snapshot.root_created_at
        )
        state = self._blindbox_state if snapshot is None else snapshot.state
        root_send_index_base = (
            self._blindbox_root_send_index_base
            if snapshot is None
            else snapshot.root_send_index_base
        )
        elapsed_sec = max(0, now_ts - int(root_created_at or now_ts))
        sent_since_epoch = max(
            0, int(state.send_index) - int(root_send_index_base)
        )
        return (
            elapsed_sec >= self._blindbox_root_rotate_seconds
            or sent_since_epoch >= self._blindbox_root_rotate_messages
        )

    def _group_blindbox_root_coordinator(self, state: GroupState) -> str:
        members = [normalize_member_id(member_id) for member_id in state.members if member_id]
        if not members:
            return ""
        return sorted(members)[0]

    def _should_initiate_group_blindbox_root_exchange(self, state: GroupState) -> bool:
        if not self._blindbox_ready():
            return False
        try:
            local_member = self._local_group_member_id()
        except Exception:
            return False
        coordinator = self._group_blindbox_root_coordinator(state)
        has_remote_members = any(
            member_id and not same_i2p_destination(member_id, local_member)
            for member_id in state.members
        )
        if not has_remote_members:
            return False
        if coordinator and same_i2p_destination(local_member, coordinator):
            return True
        snapshot_bundle = self._group_blindbox_runtime_snapshot(state.group_id)
        if snapshot_bundle is None:
            return True
        snapshot, _save_state = snapshot_bundle
        has_current_root = (
            snapshot.root_secret is not None
            and int(snapshot.group_epoch) == int(state.epoch)
        )
        has_pending_root = (
            snapshot.pending_root_secret is not None
            and int(snapshot.group_epoch) == int(state.epoch)
        )
        # Bootstrap is allowed from any live member when the current epoch has no root yet.
        # This avoids permanent await-group-root stalls when the canonical coordinator is offline.
        return not has_current_root and not has_pending_root

    def _group_blindbox_should_rotate_root(
        self, snapshot: _GroupBlindBoxSnapshot
    ) -> bool:
        if snapshot.root_secret is None:
            return False
        now_ts = int(time.time())
        elapsed_sec = max(0, now_ts - int(snapshot.root_created_at or now_ts))
        sent_since_epoch = max(
            0, int(snapshot.state.send_index) - int(snapshot.root_send_index_base)
        )
        return (
            elapsed_sec >= self._blindbox_root_rotate_seconds
            or sent_since_epoch >= self._blindbox_root_rotate_messages
        )

    def _group_blindbox_target_members(self, state: GroupState) -> tuple[str, ...]:
        try:
            local_member = self._local_group_member_id()
        except Exception:
            local_member = ""
        return tuple(
            normalize_member_id(member_id)
            for member_id in state.members
            if member_id and not same_i2p_destination(member_id, local_member)
        )

    def _ensure_pending_group_blindbox_root(
        self,
        state: GroupState,
        snapshot: _GroupBlindBoxSnapshot,
        *,
        force_rotate: bool = False,
        save_state: Callable[[], None],
    ) -> tuple[int, bytes, str, bool] | None:
        target_members = self._group_blindbox_target_members(state)
        if not target_members:
            return None
        should_bootstrap = (
            snapshot.root_secret is None
            or int(snapshot.group_epoch) != int(state.epoch)
        )
        should_rotate = force_rotate or self._group_blindbox_should_rotate_root(snapshot)
        if (
            snapshot.pending_root_secret is not None
            and int(snapshot.pending_root_epoch) > 0
            and tuple(snapshot.pending_root_target_members) == target_members
            and int(snapshot.group_epoch) == int(state.epoch)
        ):
            reason = "initialized" if should_bootstrap else "rotated"
            return (
                int(snapshot.pending_root_epoch),
                snapshot.pending_root_secret,
                reason,
                False,
            )
        if not should_bootstrap and not should_rotate:
            return None
        snapshot.group_epoch = int(state.epoch)
        snapshot.pending_root_secret = os.urandom(32)
        snapshot.pending_root_epoch = max(
            int(snapshot.root_epoch),
            int(snapshot.pending_root_epoch),
        ) + 1
        snapshot.pending_root_created_at = int(time.time())
        snapshot.pending_root_send_index_base = int(snapshot.state.send_index)
        snapshot.pending_root_target_members = target_members
        snapshot.pending_root_acked_members.clear()
        save_state()
        reason = "initialized" if should_bootstrap else "rotated"
        return (
            int(snapshot.pending_root_epoch),
            snapshot.pending_root_secret,
            reason,
            True,
        )

    def _commit_pending_group_blindbox_root(
        self,
        state: GroupState,
        ack_member: str,
        ack_epoch: int,
        *,
        snapshot: _GroupBlindBoxSnapshot,
        save_state: Callable[[], None],
    ) -> bool:
        normalized_ack_member = normalize_member_id(ack_member)
        if (
            snapshot.pending_root_secret is None
            or int(snapshot.pending_root_epoch) <= 0
            or int(ack_epoch) != int(snapshot.pending_root_epoch)
            or not normalized_ack_member
            or normalized_ack_member not in set(snapshot.pending_root_target_members)
            or normalized_ack_member
            not in {normalize_member_id(member_id) for member_id in state.members if member_id}
        ):
            return False
        snapshot.pending_root_acked_members.add(normalized_ack_member)
        if not set(snapshot.pending_root_target_members).issubset(
            snapshot.pending_root_acked_members
        ):
            save_state()
            return False
        if snapshot.root_secret is not None:
            snapshot.prev_roots.append(
                {
                    "group_epoch": int(snapshot.group_epoch),
                    "root_epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                    "expires_at": int(time.time())
                    + int(self._blindbox_previous_grace_seconds),
                }
            )
        snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
            snapshot.prev_roots
        )
        snapshot.root_secret = snapshot.pending_root_secret
        snapshot.root_epoch = int(snapshot.pending_root_epoch)
        snapshot.root_created_at = int(snapshot.pending_root_created_at)
        snapshot.root_send_index_base = int(snapshot.pending_root_send_index_base)
        snapshot.group_epoch = int(state.epoch)
        snapshot.pending_root_secret = None
        snapshot.pending_root_epoch = 0
        snapshot.pending_root_created_at = 0
        snapshot.pending_root_send_index_base = int(snapshot.state.send_index)
        snapshot.pending_root_target_members = ()
        snapshot.pending_root_acked_members.clear()
        save_state()
        return True

    def _blindbox_has_pending_root(
        self, *, snapshot: Optional[_BlindBoxPeerSnapshot] = None
    ) -> bool:
        pending_root_secret = (
            self._blindbox_pending_root_secret
            if snapshot is None
            else snapshot.pending_root_secret
        )
        pending_root_epoch = (
            self._blindbox_pending_root_epoch
            if snapshot is None
            else snapshot.pending_root_epoch
        )
        return (
            pending_root_secret is not None
            and len(pending_root_secret) == 32
            and int(pending_root_epoch) > 0
        )

    def _clear_pending_blindbox_root(
        self, *, snapshot: Optional[_BlindBoxPeerSnapshot] = None
    ) -> None:
        if snapshot is None:
            self._blindbox_pending_root_secret = None
            self._blindbox_pending_root_epoch = 0
            self._blindbox_pending_root_created_at = 0
            self._blindbox_pending_root_send_index_base = int(
                self._blindbox_state.send_index
            )
            return
        snapshot.pending_root_secret = None
        snapshot.pending_root_epoch = 0
        snapshot.pending_root_created_at = 0
        snapshot.pending_root_send_index_base = int(snapshot.state.send_index)

    def _ensure_pending_blindbox_root(
        self,
        *,
        force_rotate: bool = False,
        snapshot: Optional[_BlindBoxPeerSnapshot] = None,
        save_state: Optional[Callable[[], None]] = None,
    ) -> tuple[int, bytes, str, bool] | None:
        if self._blindbox_has_pending_root(snapshot=snapshot):
            pending_root_secret = (
                self._blindbox_pending_root_secret
                if snapshot is None
                else snapshot.pending_root_secret
            )
            pending_root_epoch = (
                self._blindbox_pending_root_epoch
                if snapshot is None
                else snapshot.pending_root_epoch
            )
            root_secret = (
                self._blindbox_root_secret if snapshot is None else snapshot.root_secret
            )
            if pending_root_secret is None:
                raise RuntimeError("BlindBox pending root invariant violated: secret is None")
            reason = (
                "initialized"
                if root_secret is None
                else "rotated"
            )
            return (
                int(pending_root_epoch),
                pending_root_secret,
                reason,
                False,
            )
        root_secret = (
            self._blindbox_root_secret if snapshot is None else snapshot.root_secret
        )
        state = self._blindbox_state if snapshot is None else snapshot.state
        root_epoch = self._blindbox_root_epoch if snapshot is None else snapshot.root_epoch
        pending_root_epoch = (
            self._blindbox_pending_root_epoch
            if snapshot is None
            else snapshot.pending_root_epoch
        )
        should_bootstrap = root_secret is None
        should_rotate = force_rotate or self._blindbox_should_rotate_root(
            snapshot=snapshot
        )
        if not should_bootstrap and not should_rotate:
            return None
        next_epoch = max(
            int(root_epoch),
            int(pending_root_epoch),
        ) + 1
        new_root_secret = os.urandom(32)
        if snapshot is None:
            self._blindbox_pending_root_secret = new_root_secret
            self._blindbox_pending_root_epoch = next_epoch
            self._blindbox_pending_root_created_at = int(time.time())
            self._blindbox_pending_root_send_index_base = int(state.send_index)
        else:
            snapshot.pending_root_secret = new_root_secret
            snapshot.pending_root_epoch = next_epoch
            snapshot.pending_root_created_at = int(time.time())
            snapshot.pending_root_send_index_base = int(state.send_index)
        if save_state is not None:
            save_state()
        elif snapshot is None:
            self._save_blindbox_state()
        reason = "rotated" if should_rotate and not should_bootstrap else "initialized"
        return (next_epoch, new_root_secret, reason, True)

    def _commit_pending_blindbox_root(
        self,
        ack_epoch: int,
        *,
        snapshot: Optional[_BlindBoxPeerSnapshot] = None,
        save_state: Optional[Callable[[], None]] = None,
    ) -> bool:
        if not self._blindbox_has_pending_root(snapshot=snapshot):
            return False
        pending_root_epoch = (
            self._blindbox_pending_root_epoch
            if snapshot is None
            else snapshot.pending_root_epoch
        )
        if int(ack_epoch) != int(pending_root_epoch):
            return False
        pending_root_secret = (
            self._blindbox_pending_root_secret
            if snapshot is None
            else snapshot.pending_root_secret
        )
        if pending_root_secret is None:
            raise RuntimeError("BlindBox commit: pending root secret is None")
        root_secret = (
            self._blindbox_root_secret if snapshot is None else snapshot.root_secret
        )
        reason = (
            "initialized"
            if root_secret is None
            else "rotated"
        )
        if root_secret is not None:
            expires_at = int(time.time()) + int(self._blindbox_previous_grace_seconds)
            prev_roots = (
                self._blindbox_prev_roots
                if snapshot is None
                else snapshot.prev_roots
            )
            prev_roots.append(
                {
                    "epoch": int(
                        self._blindbox_root_epoch
                        if snapshot is None
                        else snapshot.root_epoch
                    ),
                    "secret": root_secret,
                    "expires_at": expires_at,
                }
            )
        if snapshot is None:
            self._blindbox_root_secret = pending_root_secret
            self._blindbox_root_epoch = int(self._blindbox_pending_root_epoch)
            self._blindbox_root_created_at = int(self._blindbox_pending_root_created_at)
            self._blindbox_root_send_index_base = int(
                self._blindbox_pending_root_send_index_base
            )
            self._clear_pending_blindbox_root()
            self._blindbox_prune_previous_roots()
        else:
            snapshot.root_secret = pending_root_secret
            snapshot.root_epoch = int(snapshot.pending_root_epoch)
            snapshot.root_created_at = int(snapshot.pending_root_created_at)
            snapshot.root_send_index_base = int(snapshot.pending_root_send_index_base)
            self._clear_pending_blindbox_root(snapshot=snapshot)
            snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
                snapshot.prev_roots
            )
        if save_state is not None:
            save_state()
        elif snapshot is None:
            self._save_blindbox_state()
        if reason == "initialized":
            self._emit_system("BlindBox root secret initialized")
        else:
            self._emit_system("BlindBox root secret rotated")
        return True

    async def _send_group_blindbox_root_if_needed(
        self,
        writer: asyncio.StreamWriter,
        group_id: str,
        *,
        force_rotate: bool = False,
        peer_id: Optional[str] = None,
    ) -> None:
        if not self._blindbox_ready():
            return
        state = self.load_group_state(group_id)
        if state is None or not self._should_initiate_group_blindbox_root_exchange(state):
            return
        target_peer = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer or not any(
            same_i2p_destination(target_peer, member_id) for member_id in state.members if member_id
        ):
            return
        if not self._blindbox_live_peer_ok_for_root_exchange(target_peer):
            return
        snapshot_bundle = self._group_blindbox_runtime_snapshot(group_id)
        if snapshot_bundle is None:
            return
        snapshot, save_state = snapshot_bundle
        pending_root = self._ensure_pending_group_blindbox_root(
            state,
            snapshot,
            force_rotate=force_rotate,
            save_state=save_state,
        )
        if pending_root is None:
            return
        if (
            target_peer in snapshot.pending_root_acked_members
            or target_peer not in set(snapshot.pending_root_target_members)
        ):
            return
        next_epoch, root_secret, reason, is_new_pending = pending_root
        writer.write(
            self.frame_message(
                "S",
                "__SIGNAL__:GROUP_BLINDBOX_ROOT|"
                + state.group_id
                + "|"
                + str(int(state.epoch))
                + "|"
                + str(next_epoch)
                + "|"
                + root_secret.hex(),
                peer_id=target_peer,
            )
        )
        await writer.drain()
        if is_new_pending:
            self._emit_system(
                f"Group BlindBox root {reason} for {state.title or state.group_id}; awaiting ACKs"
            )

    async def _handle_incoming_group_blindbox_root_signal(
        self,
        body: str,
        writer: asyncio.StreamWriter,
        *,
        peer_id: Optional[str] = None,
    ) -> None:
        raw_tail = body.split("GROUP_BLINDBOX_ROOT|", 1)[1].strip()
        parts = raw_tail.split("|", 3)
        if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError("invalid group blindbox root payload")
        group_id = parts[0].strip()
        incoming_group_epoch = int(parts[1])
        incoming_root_epoch = int(parts[2])
        root_secret = bytes.fromhex(parts[3].strip())
        if len(root_secret) != 32:
            raise ValueError("invalid group blindbox root length")
        state = self.load_group_state(group_id)
        if state is None:
            return
        if incoming_group_epoch < int(state.epoch):
            return
        target_peer = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer or not any(
            same_i2p_destination(target_peer, member_id) for member_id in state.members if member_id
        ):
            return
        snapshot_bundle = self._group_blindbox_runtime_snapshot(group_id)
        if snapshot_bundle is None:
            return
        snapshot, save_state = snapshot_bundle
        if incoming_group_epoch < int(snapshot.group_epoch):
            return
        if (
            snapshot.root_secret is not None
            and int(snapshot.group_epoch) == incoming_group_epoch
            and int(snapshot.root_epoch) == incoming_root_epoch
            and snapshot.root_secret == root_secret
        ):
            writer.write(
                self.frame_message(
                    "S",
                    "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|"
                    + group_id
                    + "|"
                    + str(incoming_group_epoch)
                    + "|"
                    + str(incoming_root_epoch),
                    peer_id=target_peer,
                )
            )
            await writer.drain()
            return
        if (
            snapshot.root_secret is not None
            and int(snapshot.group_epoch) == incoming_group_epoch
            and incoming_root_epoch < int(snapshot.root_epoch)
        ):
            return
        if snapshot.root_secret is not None:
            snapshot.prev_roots.append(
                {
                    "group_epoch": int(snapshot.group_epoch),
                    "root_epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                    "expires_at": int(time.time())
                    + int(self._blindbox_previous_grace_seconds),
                }
            )
        snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
            snapshot.prev_roots
        )
        snapshot.group_epoch = incoming_group_epoch
        snapshot.root_secret = root_secret
        snapshot.root_epoch = incoming_root_epoch
        snapshot.root_created_at = int(time.time())
        snapshot.root_send_index_base = int(snapshot.state.send_index)
        snapshot.pending_root_secret = None
        snapshot.pending_root_epoch = 0
        snapshot.pending_root_created_at = 0
        snapshot.pending_root_send_index_base = int(snapshot.state.send_index)
        snapshot.pending_root_target_members = ()
        snapshot.pending_root_acked_members.clear()
        save_state()
        writer.write(
            self.frame_message(
                "S",
                "__SIGNAL__:GROUP_BLINDBOX_ROOT_ACK|"
                + group_id
                + "|"
                + str(incoming_group_epoch)
                + "|"
                + str(incoming_root_epoch),
                peer_id=target_peer,
            )
        )
        await writer.drain()
        self._schedule_flush_pending_group_blindbox_messages(group_id)

    def _handle_group_blindbox_root_ack_signal(
        self,
        body: str,
        *,
        peer_id: Optional[str] = None,
    ) -> None:
        raw_tail = body.split("GROUP_BLINDBOX_ROOT_ACK|", 1)[1].strip()
        parts = raw_tail.split("|", 2)
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError("invalid group blindbox root ACK payload")
        group_id = parts[0].strip()
        group_epoch = int(parts[1])
        ack_epoch = int(parts[2])
        state = self.load_group_state(group_id)
        if state is None or int(state.epoch) != group_epoch:
            return
        ack_member = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not ack_member:
            return
        snapshot_bundle = self._group_blindbox_runtime_snapshot(group_id)
        if snapshot_bundle is None:
            return
        snapshot, save_state = snapshot_bundle
        committed = self._commit_pending_group_blindbox_root(
            state,
            ack_member,
            ack_epoch,
            snapshot=snapshot,
            save_state=save_state,
        )
        if committed:
            self._emit_system(
                f"Group BlindBox root ready for {state.title or state.group_id}"
            )
            self._schedule_flush_pending_group_blindbox_messages(group_id)

    def _group_states_for_member(self, peer_id: str) -> list[GroupState]:
        target_peer = self._normalize_peer_addr(peer_id or "")
        if not target_peer:
            return []
        return [
            state
            for state in self.list_group_states()
            if any(
                same_i2p_destination(target_peer, member_id)
                for member_id in state.members
                if member_id
            )
        ]

    async def _send_blindbox_root_ack(
        self,
        writer: asyncio.StreamWriter,
        incoming_epoch: int,
        *,
        peer_id: Optional[str] = None,
    ) -> None:
        writer.write(
            self.frame_message(
                "S",
                f"__SIGNAL__:BLINDBOX_ROOT_ACK|{int(incoming_epoch)}",
                peer_id=peer_id,
            )
        )
        await writer.drain()

    async def _handle_incoming_blindbox_root_signal(
        self,
        body: str,
        writer: asyncio.StreamWriter,
        *,
        peer_id: Optional[str] = None,
    ) -> None:
        raw_tail = body.split("BLINDBOX_ROOT|", 1)[1].strip()
        parts = raw_tail.split("|")
        if len(parts) < 2 or not parts[0].isdigit():
            raise ValueError("missing root epoch")
        incoming_epoch = int(parts[0])
        root_hex = parts[1].strip()
        root_secret = bytes.fromhex(root_hex)
        if len(root_secret) != 32:
            raise ValueError("invalid root secret length")
        if not self._blindbox_ready():
            self._emit_error(
                "BlindBox root received while BlindBox is not ready."
            )
            return
        target_peer = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer:
            self._emit_error("BlindBox root ignored: missing peer context.")
            return
        if not self._blindbox_live_peer_ok_for_root_exchange(target_peer):
            self._emit_error(
                "BlindBox root ignored: need a verified live session with a Saved peer."
            )
            return
        snapshot_bundle = self._blindbox_runtime_snapshot_for_peer(target_peer)
        if snapshot_bundle is None:
            self._emit_error("BlindBox root ignored: missing peer snapshot.")
            return
        snapshot, save_state = snapshot_bundle
        if snapshot.root_secret is None:
            snapshot.root_secret = root_secret
            snapshot.root_epoch = incoming_epoch
            snapshot.root_created_at = int(time.time())
            snapshot.root_send_index_base = int(snapshot.state.send_index)
            save_state()
            self._emit_system(
                f"BlindBox root secret received (epoch={incoming_epoch})"
            )
            await self._send_blindbox_root_ack(
                writer, incoming_epoch, peer_id=target_peer
            )
            return
        if incoming_epoch > int(snapshot.root_epoch):
            expires_at = int(time.time()) + int(self._blindbox_previous_grace_seconds)
            snapshot.prev_roots.append(
                {
                    "epoch": int(snapshot.root_epoch),
                    "secret": snapshot.root_secret,
                    "expires_at": expires_at,
                }
            )
            snapshot.root_secret = root_secret
            snapshot.root_epoch = incoming_epoch
            snapshot.root_created_at = int(time.time())
            snapshot.root_send_index_base = int(snapshot.state.send_index)
            snapshot.prev_roots = self._blindbox_prune_previous_roots_list(
                snapshot.prev_roots
            )
            save_state()
            self._emit_system("BlindBox root rotated")
            await self._send_blindbox_root_ack(
                writer, incoming_epoch, peer_id=target_peer
            )
            return
        if (
            incoming_epoch == int(snapshot.root_epoch)
            and snapshot.root_secret == root_secret
        ):
            await self._send_blindbox_root_ack(
                writer, incoming_epoch, peer_id=target_peer
            )
            return
        self._emit_system("Ignoring stale BlindBox root signal.")

    def _handle_blindbox_root_ack_signal(
        self, body: str, *, peer_id: Optional[str] = None
    ) -> None:
        ack_raw = body.split("BLINDBOX_ROOT_ACK|", 1)[1].strip().split("|", 1)[0]
        if not ack_raw.isdigit():
            raise ValueError("invalid root ack epoch")
        ack_epoch = int(ack_raw)
        if not self._blindbox_ready():
            self._emit_error(
                "BlindBox root ACK received while BlindBox is not ready."
            )
            return
        target_peer = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer:
            self._emit_error("BlindBox root ACK ignored: missing peer context.")
            return
        if not self._blindbox_live_peer_ok_for_root_exchange(target_peer):
            self._emit_error(
                "BlindBox root ACK ignored: need a verified live session with a Saved peer."
            )
            return
        snapshot_bundle = self._blindbox_runtime_snapshot_for_peer(target_peer)
        if snapshot_bundle is None:
            self._emit_error("BlindBox root ACK ignored: missing peer snapshot.")
            return
        snapshot, save_state = snapshot_bundle
        if not self._commit_pending_blindbox_root(
            ack_epoch, snapshot=snapshot, save_state=save_state
        ):
            self._emit_system("Ignoring stale BlindBox root ACK.")

    async def _send_blindbox_root_if_needed(
        self,
        writer: asyncio.StreamWriter,
        *,
        force_rotate: bool = False,
        peer_id: Optional[str] = None,
    ) -> None:
        if not self._blindbox_ready():
            return
        target_peer = self._normalize_peer_addr(
            peer_id or self.current_peer_addr or self._last_active_peer_for_telemetry()
        )
        if not target_peer:
            return
        if not self._blindbox_live_peer_ok_for_root_exchange(target_peer):
            self._emit_error(
                "BlindBox root exchange blocked: connect to a Saved peer and complete the handshake."
            )
            return
        if not self._should_initiate_blindbox_root_exchange(target_peer):
            return
        snapshot_bundle = self._blindbox_runtime_snapshot_for_peer(target_peer)
        if snapshot_bundle is None:
            return
        snapshot, save_state = snapshot_bundle
        pending_root = self._ensure_pending_blindbox_root(
            force_rotate=force_rotate,
            snapshot=snapshot,
            save_state=save_state,
        )
        if pending_root is None:
            return
        next_epoch, root_secret, reason, is_new_pending = pending_root
        writer.write(
            self.frame_message(
                "S",
                "__SIGNAL__:BLINDBOX_ROOT|"
                + str(next_epoch)
                + "|"
                + root_secret.hex(),
                peer_id=target_peer,
            )
        )
        await writer.drain()
        if is_new_pending:
            self._emit_system(f"BlindBox root secret {reason}; awaiting ACK")

    async def _handle_handshake_message(
        self,
        body: str,
        writer: asyncio.StreamWriter,
        peer_id: Optional[str] = None,
    ) -> None:
        """Обрабатывает входящее signed-handshake сообщение с поддержкой PFS."""
        sess = self._session_for_frame(peer_id)
        peer_addr = self._normalize_peer_addr(peer_id or self.current_peer_addr or "")
        try:
            if not crypto.NACL_AVAILABLE:
                raise RuntimeError("PyNaCl is required for secure protocol")

            def _parse_signed_payload(
                payload: str,
            ) -> Tuple[bytes, bytes, bytes, bytes, str, str, str]:
                parts = payload.split(":")
                if len(parts) != 4:
                    raise ValueError(
                        "Handshake payload must contain nonce, ephemeral key, signing key and signature."
                    )
                nonce_hex, eph_hex, sign_pub_hex, signature_hex = [p.strip().lower() for p in parts]
                nonce = bytes.fromhex(nonce_hex)
                eph_pub = bytes.fromhex(eph_hex)
                sign_pub = bytes.fromhex(sign_pub_hex)
                signature = bytes.fromhex(signature_hex)
                if len(nonce) != crypto.NONCE_SIZE:
                    raise ValueError("Invalid handshake nonce length")
                if len(eph_pub) != 32:
                    raise ValueError("Invalid ephemeral public key length")
                if len(sign_pub) != 32:
                    raise ValueError("Invalid handshake signing public key length")
                if len(signature) != 64:
                    raise ValueError("Invalid handshake signature length")
                return nonce, eph_pub, sign_pub, signature, nonce_hex, eph_hex, sign_pub_hex

            if body.startswith("INIT:"):
                if sess._handshake_initiated:
                    logger.warning(
                        "Received INIT while local INIT is pending; closing to avoid handshake role conflict."
                    )
                    self._emit_error(
                        "Handshake role conflict detected; reconnecting."
                    )
                    if peer_addr:
                        self.session_manager.mark_peer_failed(
                            peer_addr, reason="handshake-role-conflict"
                        )
                    self._schedule_disconnect(peer_id)
                    return
                if not peer_addr or not self.my_dest:
                    raise ValueError("Missing peer/local address for INIT verification")
                if not self.my_signing_seed or not self.my_signing_public:
                    raise ValueError("Missing local handshake signing key")
                (
                    sess.peer_nonce,
                    sess.peer_ephemeral_public,
                    peer_sign_pub,
                    peer_signature,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                ) = _parse_signed_payload(body[5:])
                init_sig_payload = self._build_init_sig_payload(
                    peer_addr,
                    self.my_dest.base32,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                )
                if not crypto.verify_signature(peer_sign_pub, init_sig_payload, peer_signature):
                    raise ValueError("INIT signature verification failed")
                if not await self._pin_or_verify_peer_signing_key(peer_addr, peer_sign_pub):
                    raise ValueError("Peer signing key does not match pinned key")
                sess.peer_signing_public = peer_sign_pub

                sess.my_ephemeral_private, sess.my_ephemeral_public = (
                    crypto.generate_ephemeral_keypair()
                )
                sess.my_nonce = crypto.generate_nonce()

                resp_nonce_hex = sess.my_nonce.hex()
                resp_eph_hex = sess.my_ephemeral_public.hex()
                resp_sign_pub_hex = self.my_signing_public.hex()
                resp_sig_payload = self._build_resp_sig_payload(
                    self.my_dest.base32,
                    peer_addr,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                    resp_nonce_hex,
                    resp_eph_hex,
                    resp_sign_pub_hex,
                )
                resp_signature_hex = crypto.sign_data(
                    self.my_signing_seed, resp_sig_payload
                ).hex()
                response = (
                    f"RESP:{resp_nonce_hex}:{resp_eph_hex}:{resp_sign_pub_hex}:{resp_signature_hex}"
                )
                writer.write(self.frame_message_plain("H", response, peer_id=peer_id))
                await writer.drain()

                sess.shared_key, sess.shared_mac_key = self._compute_session_subkeys(
                    is_initiator=False, sess=sess
                )
                sess.use_encryption = True
                sess.handshake_complete = True
                sess._handshake_initiated = False
                sess._recv_seq = 0
                sess._send_seq = 0
                self._cancel_handshake_watchdog()
                peer_addr_norm = peer_addr
                if peer_addr_norm:
                    self.session_manager.set_peer_handshake_complete(
                        peer_addr_norm, reason="handshake-ok"
                    )
                    self.session_manager.update_stream_state(
                        peer_addr_norm,
                        PeerState.SECURE,
                        peer_id=peer_addr_norm,
                    )
                self.session_manager.mark_live_healthy(peer_id=peer_addr_norm)
                if sess.announce_lifecycle:
                    self._emit_message("info", "Secure channel with PFS established")
                    self._emit_system("✔ Ready! You can now send messages.")
                self._trigger_blindbox_hot_poll("peer-online")
                self._notify_group_mesh_manager()
                if peer_addr_norm:
                    self._schedule_group_pending_flush([peer_addr_norm])
                await self._send_blindbox_root_if_needed(writer, peer_id=peer_id)
                if (
                    peer_addr_norm
                    and self._legacy_group_blindbox_outbound_enabled()
                ):
                    for group_state in self._group_states_for_member(peer_addr_norm):
                        if self._should_initiate_group_blindbox_root_exchange(
                            group_state
                        ):
                            await self._send_group_blindbox_root_if_needed(
                                writer,
                                group_state.group_id,
                                peer_id=peer_id,
                            )
                logger.info("Handshake completed (responder)")

            elif body.startswith("RESP:"):
                if (
                    not sess._handshake_initiated
                    or sess.my_nonce is None
                    or sess.my_ephemeral_public is None
                ):
                    logger.warning("Received RESP without prior INIT")
                    if peer_addr:
                        self.session_manager.mark_peer_failed(
                            peer_addr, reason="handshake-resp-without-init"
                        )
                    self._schedule_disconnect(peer_id)
                    return
                if not peer_addr or not self.my_dest:
                    raise ValueError("Missing peer/local address for RESP verification")
                if not self.my_signing_public:
                    raise ValueError("Missing local handshake signing public key")

                (
                    sess.peer_nonce,
                    sess.peer_ephemeral_public,
                    peer_sign_pub,
                    peer_signature,
                    resp_nonce_hex,
                    resp_eph_hex,
                    resp_sign_pub_hex,
                ) = _parse_signed_payload(body[5:])
                init_nonce_hex = sess.my_nonce.hex()
                init_eph_hex = sess.my_ephemeral_public.hex()
                init_sign_pub_hex = self.my_signing_public.hex()
                resp_sig_payload = self._build_resp_sig_payload(
                    peer_addr,
                    self.my_dest.base32,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                    resp_nonce_hex,
                    resp_eph_hex,
                    resp_sign_pub_hex,
                )
                if not crypto.verify_signature(peer_sign_pub, resp_sig_payload, peer_signature):
                    raise ValueError("RESP signature verification failed")
                if not await self._pin_or_verify_peer_signing_key(peer_addr, peer_sign_pub):
                    raise ValueError("Peer signing key does not match pinned key")
                sess.peer_signing_public = peer_sign_pub
                sess.shared_key, sess.shared_mac_key = self._compute_session_subkeys(
                    is_initiator=True, sess=sess
                )
                sess.use_encryption = True
                sess.handshake_complete = True
                sess._handshake_initiated = False
                sess._recv_seq = 0
                sess._send_seq = 0
                self._cancel_handshake_watchdog()
                peer_addr_norm = peer_addr
                if peer_addr_norm:
                    self.session_manager.set_peer_handshake_complete(
                        peer_addr_norm, reason="handshake-ok"
                    )
                    self.session_manager.update_stream_state(
                        peer_addr_norm,
                        PeerState.SECURE,
                        peer_id=peer_addr_norm,
                    )
                self.session_manager.mark_live_healthy(peer_id=peer_addr_norm)
                if sess.announce_lifecycle:
                    self._emit_message("info", "Secure channel with PFS established")
                    self._emit_system("✔ Ready! You can now send messages.")
                self._trigger_blindbox_hot_poll("peer-online")
                self._notify_group_mesh_manager()
                if peer_addr_norm:
                    self._schedule_group_pending_flush([peer_addr_norm])
                await self._send_blindbox_root_if_needed(writer, peer_id=peer_id)
                if (
                    peer_addr_norm
                    and self._legacy_group_blindbox_outbound_enabled()
                ):
                    for group_state in self._group_states_for_member(peer_addr_norm):
                        if self._should_initiate_group_blindbox_root_exchange(
                            group_state
                        ):
                            await self._send_group_blindbox_root_if_needed(
                                writer,
                                group_state.group_id,
                                peer_id=peer_id,
                            )
                logger.info("Handshake completed (initiator)")

            else:
                logger.warning(f"Unknown handshake message: {body[:20]}")
                
        except Exception as e:
            logger.error(f"Handshake error: {e}")
            self._emit_error(f"Secure handshake failed: {e}")
            peer_addr_norm = peer_addr
            if peer_addr_norm:
                self.session_manager.mark_peer_failed(
                    peer_addr_norm, reason="handshake-error"
                )
            self.session_manager.mark_live_failure(
                reason="handshake-error",
                peer_id=peer_addr_norm,
            )
            self._schedule_disconnect(peer_id)

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
        self.session_manager.transition_transport(
            TransportState.SHUTTING_DOWN, reason="shutdown"
        )
        if self._group_mesh_task is not None and not self._group_mesh_task.done():
            self.group_mesh_manager.stop()
            self._group_mesh_task.cancel()
            await asyncio.gather(self._group_mesh_task, return_exceptions=True)
        self._group_mesh_task = None
        if (
            self._group_pending_flush_task is not None
            and not self._group_pending_flush_task.done()
        ):
            self._group_pending_flush_task.cancel()
            await asyncio.gather(
                self._group_pending_flush_task, return_exceptions=True
            )
        self._group_pending_flush_task = None
        self.session_manager.invalidate_handshake_watchdog()
        self.session_manager.set_outbound_connect_busy(
            False,
            peer_id=self._normalize_peer_addr(self.current_peer_addr or ""),
        )
        for pid in list(self._live_sessions.keys()):
            await self.disconnect_peer(pid)

        await self.session_manager.cancel_tasks_and_close_session()
        if self._blindbox_task is not None and not self._blindbox_task.done():
            self._blindbox_task.cancel()
            await asyncio.gather(self._blindbox_task, return_exceptions=True)
        self._blindbox_task = None
        if self._blindbox_client is not None:
            try:
                await self._blindbox_client.close()
            except Exception:
                pass
            self._blindbox_client = None
        self.session_manager.transition_transport(
            TransportState.STOPPED, reason="shutdown-complete"
        )

    # ---------- фоновые циклы ----------

    async def accept_loop(self) -> None:
        while True:
            if not crypto.NACL_AVAILABLE:
                self._emit_error("PyNaCl is required for secure protocol")
                await asyncio.sleep(5)
                continue
            try:
                reader, writer = await i2plib.stream_accept(
                    self.session_id, sam_address=self.sam_address
                )

                try:
                    peer_identity_line = await asyncio.wait_for(
                        reader.readline(), timeout=10.0
                    )
                except asyncio.TimeoutError:
                    writer.close()
                    continue

                if not peer_identity_line:
                    writer.close()
                    continue

                try:
                    raw_dest = peer_identity_line.decode().strip()
                    peer_addr = i2plib.Destination(raw_dest).base32

                    verified = await self._set_verified_peer_identity(
                        peer_addr, raw_dest, source="preface"
                    )
                    if not verified:
                        writer.close()
                        continue
                    if self.ensure_peer_in_saved_contacts(peer_addr):
                        self._emit_system(
                            f"Accepted first contact from {peer_addr[:12]}... "
                            "and added it to Saved peers."
                        )
                    peer_addr_norm = self._normalize_peer_addr(peer_addr)
                    existing_session = self._live_sessions.get(peer_addr_norm)
                    if existing_session is not None:
                        if existing_session.handshake_complete:
                            logger.info(
                                "Duplicate incoming connection from %s ignored; secure session already active.",
                                peer_addr[:24],
                            )
                            writer.close()
                            continue
                        if self._prefer_incoming_session(peer_addr_norm):
                            await self._yield_session_slot_to_incoming(
                                peer_addr_norm,
                                expected_session=existing_session,
                            )
                            logger.info(
                                "Incoming connection from %s won simultaneous-connect tie-break.",
                                peer_addr[:24],
                            )
                        else:
                            logger.info(
                                "Incoming connection from %s dropped; outbound session keeps tie-break.",
                                peer_addr[:24],
                            )
                            writer.close()
                            continue
                    max_live = max_concurrent_live_sessions()
                    if self._live_stream_count() >= max_live:
                        self._emit_error(
                            f"Incoming connection rejected: maximum live sessions ({max_live}) reached."
                        )
                        writer.close()
                        continue

                    extra_ls = LivePeerSession(peer_id=peer_addr_norm)
                    self._live_sessions[peer_addr_norm] = extra_ls

                    self._emit_message(
                        "info", f"Connection accepted from {peer_addr[:12]}..."
                    )
                    # Отдельное событие для системного уведомления о входящем подключении.
                    self._emit_notify("connect", peer_addr)
                except Exception as e:
                    self._emit_error(f"Rejected incoming connection: invalid identity preface ({e})")
                    writer.close()
                    continue

                if self.my_dest is not None:
                    writer.write(
                        self.frame_message_plain(
                            "S", self.my_dest.base64, peer_id=peer_addr_norm
                        )
                    )
                    await writer.drain()

                connection = (reader, writer)
                extra_ls.conn = connection

                if peer_addr_norm:
                    self.session_manager.register_stream(
                        peer_addr_norm,
                        state=PeerState.HANDSHAKING,
                        peer_id=peer_addr_norm,
                    )
                self._activate_ack_session(peer_addr_norm)
                self.session_manager.transition_transport(
                    TransportState.RECONNECTING, reason="incoming-connection"
                )

                loop = asyncio.get_running_loop()
                self._start_receive_loop_task(connection, peer_id=peer_addr_norm)
                self._start_handshake_watchdog(
                    connection, peer_id=peer_addr_norm
                )
                if (
                    self.session_manager.keepalive_task is None
                    or self.session_manager.keepalive_task.done()
                ):
                    self.session_manager.keepalive_task = loop.create_task(
                        self._keepalive_loop()
                    )
            except Exception:
                await asyncio.sleep(1)

    @staticmethod
    def _peer_protocol_err_suffix(peer_id: Optional[str]) -> str:
        """Short peer label for user-facing protocol errors (canonical bare id)."""
        if not peer_id:
            return ""
        p = str(peer_id).strip()
        if len(p) <= 20:
            return f" (peer {p})"
        return f" (peer {p[:12]}…)"

    async def receive_loop(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        initial_type: Optional[str] = None,
        peer_id: Optional[str] = None,
    ) -> None:
        if not peer_id:
            return
        peer_id = self._normalize_peer_addr(peer_id)
        sess = self._live_sessions.get(peer_id)
        if sess is None or sess.conn != connection:
            return
        if sess._recv_loop_active:
            return
        sess._recv_loop_active = True
        err_peer = peer_id or getattr(sess, "peer_id", None) or ""
        _ps = self._peer_protocol_err_suffix

        reader, writer = connection
        current_type = initial_type
        restart_after_timeout = False

        try:
            while True:
                self._prune_pending_acks(force=False)
                msg_id = 0
                if current_type:
                    msg_type = current_type
                    current_type = None
                    body_data = b""
                    is_encrypted = False
                else:
                    try:
                        frame = await asyncio.wait_for(
                            self._codec.read_frame(reader), timeout=self.READ_TIMEOUT
                        )
                    except ValueError as e:
                        if sess.handshake_complete:
                            logger.warning(
                                "Protocol framing violation after handshake: %s (peer=%s)",
                                e,
                                (err_peer or "?")[:24],
                            )
                            self._emit_error(
                                "Protocol downgrade detected"
                                + _ps(err_peer)
                            )
                            self._schedule_disconnect(peer_id)
                            break
                        raise
                    except asyncio.TimeoutError:
                        if sess._file_transfer_active:
                            # Исходящая передача файла/картинки: цикл приёма не глушим — иначе до конца
                            # отправки не обрабатываются входящие U/P/сигналы собеседника.
                            continue
                        if sess.incoming_info is not None:
                            restart_after_timeout = True
                            return
                        # Приём inline-картинки (G): как для F/D — не рвём сессию из-за долгой паузы I2P
                        if sess.inline_image_info is not None:
                            restart_after_timeout = True
                            return
                        if sess.conn == connection:
                            self._emit_error(
                                "Connection timed out (no data received)" + _ps(err_peer)
                            )
                        return
                    msg_type = frame.msg_type
                    msg_id = frame.msg_id
                    body_data = frame.payload
                    is_encrypted = bool(frame.flags & FLAG_ENCRYPTED)

                # disconnect() делает pop до отмены receive_loop: не разбирать хвост кадра на «осиротевшей» сессии.
                if self._live_sessions.get(peer_id) is not sess:
                    break

                if sess.handshake_complete and msg_type == "H":
                    logger.warning(
                        "Unexpected handshake frame after secure channel (peer=%s)",
                        (err_peer or "?")[:24],
                    )
                    self._emit_error(
                        "Protocol violation: unexpected handshake frame" + _ps(err_peer)
                    )
                    self._schedule_disconnect(peer_id)
                    break
                if not sess.handshake_complete and msg_type not in ["S", "H", "P", "O"]:
                    logger.warning(
                        "Protocol violation: non-handshake frame before secure channel "
                        "(msg_type=%r peer=%s)",
                        msg_type,
                        (err_peer or "?")[:24],
                    )
                    self._emit_error(
                        "Protocol violation: data before secure handshake" + _ps(err_peer)
                    )
                    self._schedule_disconnect(peer_id)
                    break
                seq_num: Optional[int] = None
                if is_encrypted:
                    if not sess.shared_key or not sess.use_encryption:
                        logger.warning(
                            "Encrypted frame received before key setup (peer=%s)",
                            (err_peer or "?")[:24],
                        )
                        self._emit_error(
                            "Protocol error: encrypted frame before handshake"
                            + _ps(err_peer)
                        )
                        self._schedule_disconnect(peer_id)
                        break
                    if len(body_data) < ENCRYPTED_TRAILER_SIZE:
                        logger.warning(
                            "Encrypted payload is too short (peer=%s)",
                            (err_peer or "?")[:24],
                        )
                        self._emit_error(
                            "Protocol error: encrypted payload too short" + _ps(err_peer)
                        )
                        self._schedule_disconnect(peer_id)
                        break
                    seq_num = int.from_bytes(body_data[:8], "big", signed=False)
                    encrypted_body = body_data[8:-crypto.HMAC_SIZE]
                    received_mac = body_data[-crypto.HMAC_SIZE:]
                    if len(encrypted_body) == 0:
                        logger.warning("Encrypted body is empty")
                        break
                    mac_key = sess.shared_mac_key or sess.shared_key
                    if not crypto.verify_mac(
                        mac_key,
                        msg_type,
                        encrypted_body,
                        received_mac,
                        seq=seq_num,
                        msg_id=msg_id,
                        flags=frame.flags,
                    ):
                        logger.warning(
                            "HMAC verification failed - message integrity compromised "
                            "(msg_type=%r body_len=%d peer=%s recv_seq=%s)",
                            msg_type,
                            len(body_data),
                            (err_peer or "?")[:24],
                            getattr(sess, "_recv_seq", "?"),
                        )
                        self._emit_error(
                            "Message integrity check failed" + _ps(err_peer)
                        )
                        self._schedule_disconnect(peer_id)
                        break
                    expected_seq = sess._recv_seq + 1
                    if seq_num != expected_seq:
                        logger.warning(
                            "Replay/out-of-order frame detected: got=%d expected=%d peer=%s",
                            seq_num,
                            expected_seq,
                            (err_peer or "?")[:24],
                        )
                        self._emit_error(
                            "Replay protection triggered"
                            + _ps(err_peer)
                            + f" (seq got {seq_num}, expected {expected_seq})"
                        )
                        self._schedule_disconnect(peer_id)
                        break

                    decrypted = crypto.decrypt_message(sess.shared_key, encrypted_body)
                    if decrypted is None:
                        logger.warning("Decryption failed")
                        self._emit_error("Failed to decrypt message")
                        break
                    try:
                        body_data = self._remove_padding_profile(decrypted)
                    except ValueError as e:
                        logger.warning("Padded payload parse failed: %s", e)
                        self._emit_error("Protocol error: malformed padded payload")
                        self._schedule_disconnect(peer_id)
                        break
                    sess._recv_seq = seq_num
                elif sess.handshake_complete:
                    logger.warning(
                        "Protocol downgrade detected: plaintext frame after handshake "
                        "(msg_type=%r peer=%s encrypted=%s)",
                        msg_type,
                        (err_peer or "?")[:24],
                        is_encrypted,
                    )
                    self._emit_error(
                        "Protocol downgrade detected"
                        + _ps(err_peer)
                        + f" (msg_type={msg_type!r}, encrypted={is_encrypted})"
                    )
                    self._schedule_disconnect(peer_id)
                    break

                body = body_data.decode("utf-8")
                peer_addr_norm = self._normalize_peer_addr(sess.peer_id or "")
                if peer_addr_norm:
                    self.session_manager.touch_stream(peer_addr_norm)

                if msg_type == "U":
                    sp = sess.peer_id
                    if self.import_group_transport(body, source_peer=sp) is None:
                        self._emit_message("peer", body, source_peer=sp)
                        self._emit_notify("peer", body, source_peer=sp)
                    # Подтверждение доставки по MSG_ID (vNext)
                    if msg_id:
                        try:
                            await self._write_signal_frame_maybe_soft_drain(
                                writer,
                                self.frame_message(
                                    "S",
                                    f"__SIGNAL__:MSG_ACK|{msg_id}",
                                    peer_id=peer_id,
                                ),
                                sess=sess,
                            )
                        except Exception:
                            pass

                elif msg_type == "I":
                    if body == "__END__":
                        img_text = "\n".join(sess.image_buffer)
                        sess.image_buffer = []
                        if self.on_image_received:
                            self.on_image_received(img_text)
                        else:
                            self._emit_message(
                                "peer", img_text, source_peer=sess.peer_id
                            )
                    else:
                        if len(sess.image_buffer) < self.MAX_IMAGE_LINES:
                            sess.image_buffer.append(body)
                        elif len(sess.image_buffer) == self.MAX_IMAGE_LINES:
                            sess.image_buffer.append("[Image truncated - too large]")
                            self._emit_error("Image too large, truncating")

                elif msg_type == "G":
                    # Inline image (binary PNG / JPEG / WebP)
                    if body == "__IMG_END__":
                        # Завершение приёма изображения
                        if sess.inline_image_info and sess.inline_image_buffer:
                            filename, expected_size = sess.inline_image_info
                            actual_size = len(sess.inline_image_buffer)
                            
                            # Проверяем размер
                            if actual_size > MAX_IMAGE_SIZE:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image too large, discarding")
                                sess.inline_image_buffer = bytearray()
                                sess.inline_image_info = None
                                sess._incoming_image_msg_id = None
                                continue

                            if actual_size != expected_size:
                                self._emit_file_event(
                                    FileTransferInfo(
                                        filename=filename,
                                        size=expected_size,
                                        received=-1,
                                        is_sending=False,
                                        is_inline_image=True,
                                    )
                                )
                                self._emit_error(
                                    f"Image transfer incomplete: received {actual_size} of {expected_size} bytes"
                                )
                                sess.inline_image_buffer = bytearray()
                                sess.inline_image_info = None
                                sess._incoming_image_msg_id = None
                                continue
                            
                            # Проверяем magic bytes
                            header = bytes(sess.inline_image_buffer[:12])
                            detected_ext = detect_inline_image_format(header)
                            if detected_ext is None:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image has invalid format")
                                sess.inline_image_buffer = bytearray()
                                sess.inline_image_info = None
                                sess._incoming_image_msg_id = None
                                continue
                            
                            # Сохраняем и валидируем в thread pool (hash/PIL не блокируют qasync/Qt)
                            images_dir = get_images_dir()
                            payload = bytes(sess.inline_image_buffer)
                            safe_path, disk_err = await asyncio.to_thread(
                                _finalize_inline_image_worker,
                                payload,
                                detected_ext,
                                images_dir,
                            )
                            try:
                                if safe_path is None:
                                    self._emit_file_event(
                                        FileTransferInfo(
                                            filename=filename,
                                            size=expected_size,
                                            received=-1,
                                            is_sending=False,
                                            is_inline_image=True,
                                        )
                                    )
                                    self._emit_error(
                                        f"Received invalid image: {disk_err}"
                                        if disk_err
                                        else "Received invalid image"
                                    )
                                else:
                                    self._emit_file_event(
                                        FileTransferInfo(
                                            filename=filename,
                                            size=expected_size,
                                            received=actual_size,
                                            is_sending=False,
                                            is_inline_image=True,
                                        )
                                    )
                                    self._emit_inline_image(safe_path, is_from_me=False)
                                    try:
                                        ack_id = sess._incoming_image_msg_id or 0
                                        await self._write_signal_frame_maybe_soft_drain(
                                            writer,
                                            self.frame_message(
                                                "S",
                                                f"__SIGNAL__:IMG_ACK|{filename}|{ack_id}",
                                                peer_id=peer_id,
                                            ),
                                            sess=sess,
                                        )
                                    except Exception:
                                        pass
                                    cleanup_images_cache()
                            except Exception as e:
                                self._emit_file_event(
                                    FileTransferInfo(
                                        filename=filename,
                                        size=expected_size,
                                        received=-1,
                                        is_sending=False,
                                        is_inline_image=True,
                                    )
                                )
                                self._emit_error(f"Failed to save image: {e}")
                            
                            sess.inline_image_buffer = bytearray()
                            sess.inline_image_info = None
                            sess._incoming_image_msg_id = None
                    elif sess.inline_image_info is None:
                        # Заголовок: filename|size
                        try:
                            parts = body.split("|")
                            if len(parts) == 2:
                                filename = sanitize_filename(parts[0])
                                size = int(parts[1])
                                if size > MAX_IMAGE_SIZE:
                                    self._emit_error(
                                        f"Incoming image too large: {size} bytes "
                                        f"(max {MAX_IMAGE_SIZE // (1024*1024)} MB)"
                                    )
                                else:
                                    sess.inline_image_info = (filename, size)
                                    sess._incoming_image_msg_id = msg_id or None
                                    sess.inline_image_buffer = bytearray()
                                    sess._inline_image_last_emit = 0
                                    self._emit_system(f"Receiving image: {filename} ({size} bytes)")
                                    self._emit_file_event(FileTransferInfo(filename=filename, size=size, received=0, is_sending=False, is_inline_image=True))
                        except Exception as e:
                            self._emit_error(f"Invalid image header: {e}")
                    else:
                        # Данные изображения (base64)
                        try:
                            fn, total = sess.inline_image_info
                            remaining = total - len(sess.inline_image_buffer)
                            if remaining <= 0:
                                raise ValueError("Image chunk exceeds declared size")
                            if len(body) > max_base64_chars_for_bytes(remaining):
                                raise ValueError("Image chunk is too large for remaining size")
                            chunk = base64.b64decode(body, validate=True)
                            if len(chunk) > remaining:
                                raise ValueError("Decoded image chunk exceeds remaining size")
                            sess.inline_image_buffer.extend(chunk)
                            if sess.inline_image_info:
                                received = len(sess.inline_image_buffer)
                                if received - getattr(self, "_inline_image_last_emit", 0) >= 65536 or received == total:
                                    sess._inline_image_last_emit = received
                                    self._emit_file_event(FileTransferInfo(filename=fn, size=total, received=received, is_sending=False, is_inline_image=True))
                        except Exception as e:
                            self._emit_error(f"Image data error: {e}")
                            if sess.inline_image_info:
                                fn, sz = sess.inline_image_info
                                self._emit_file_event(FileTransferInfo(filename=fn, size=sz, received=-1, is_sending=False, is_inline_image=True))
                            sess.inline_image_buffer = bytearray()
                            sess.inline_image_info = None
                            sess._incoming_image_msg_id = None

                elif msg_type == "F":
                    try:
                        filename, size_str = body.split("|")
                        filename = sanitize_filename(filename)
                        size = int(size_str)
                        if size > self.MAX_FILE_SIZE:
                            self._emit_error(
                                f"File too large: {size} bytes "
                                f"(max {self.MAX_FILE_SIZE // (1024*1024)} MB)"
                            )
                            sess.incoming_file = None
                            sess.incoming_info = None
                            continue
                        safe_name = os.path.basename(filename)
                        safe_path = allocate_unique_filename(get_downloads_dir(), safe_name)
                        final_name = os.path.basename(safe_path)
                        accepted = await self._request_file_offer_decision(
                            final_name, size
                        )
                        if not accepted:
                            prj = peer_id or self._normalize_peer_addr(
                                self.current_peer_addr or ""
                            )
                            await self.reject_incoming_file(
                                final_name, peer_for_route=prj
                            )
                            self._emit_system(
                                f"Incoming file rejected by user: {final_name}"
                            )
                            sess.incoming_file = None
                            sess.incoming_info = None
                            continue
                        if final_name != safe_name:
                            self._emit_system(
                                f"Filename collision detected: saved as {final_name}"
                            )
                        sess.incoming_file = open(safe_path, "xb")
                        sess._incoming_file_msg_id = msg_id or None
                        sess.incoming_info = FileTransferInfo(
                            filename=safe_path, size=size, received=0
                        )
                        self._file_xfer_debug_last_recv_emit_mono = None
                        self._emit_system(
                            f"Receiving file: {final_name} ({size} bytes)"
                        )
                        self._emit_file_event(sess.incoming_info)
                    except Exception as e:
                        self._emit_error(f"Invalid file header: {e}")

                elif msg_type == "D":
                    try:
                        if sess.incoming_file and sess.incoming_info:
                            remaining = sess.incoming_info.size - sess.incoming_info.received
                            if remaining <= 0:
                                raise ValueError("File chunk exceeds declared size")
                            if len(body) > max_base64_chars_for_bytes(remaining):
                                raise ValueError("File chunk is too large for remaining size")
                            chunk = base64.b64decode(body, validate=True)
                            if len(chunk) > remaining:
                                raise ValueError("Decoded file chunk exceeds remaining size")
                            sess.incoming_file.write(chunk)
                            sess.incoming_info.received += len(chunk)
                            rcv = sess.incoming_info.received
                            tot = sess.incoming_info.size
                            clen = len(chunk)
                            if should_emit_file_progress(rcv, clen, tot):
                                if self._file_xfer_debug:
                                    now = time.monotonic()
                                    prev = self._file_xfer_debug_last_recv_emit_mono
                                    if prev is not None:
                                        gap = now - prev
                                        if gap >= 0.25:
                                            logger.info(
                                                "file xfer recv: emit gap %.3fs received=%s/%s",
                                                gap,
                                                rcv,
                                                tot,
                                            )
                                    self._file_xfer_debug_last_recv_emit_mono = now
                                self._emit_file_event(sess.incoming_info)
                    except Exception as e:
                        self._emit_error(f"File chunk error: {e}")
                        if sess.incoming_file:
                            try:
                                sess.incoming_file.close()
                            except Exception:
                                pass
                        if sess.incoming_info:
                            self._emit_file_event(
                                FileTransferInfo(
                                    filename=sess.incoming_info.filename,
                                    size=sess.incoming_info.size,
                                    received=-1,
                                    is_sending=False,
                                )
                            )
                            try:
                                os.remove(sess.incoming_info.filename)
                            except OSError:
                                pass
                        sess.incoming_file = None
                        sess.incoming_info = None
                        sess._incoming_file_msg_id = None

                elif msg_type == "E":
                    if sess.incoming_file and sess.incoming_info:
                        ack_filename = sess.incoming_info.filename
                        expected_size = sess.incoming_info.size
                        received_size = sess.incoming_info.received
                        try:
                            sess.incoming_file.close()
                        except Exception:
                            pass
                        if received_size != expected_size:
                            self._emit_error(
                                f"File transfer incomplete: expected {expected_size} bytes, got {received_size}"
                            )
                            self._emit_file_event(
                                FileTransferInfo(
                                    filename=ack_filename,
                                    size=expected_size,
                                    received=-1,
                                    is_sending=False,
                                )
                            )
                            try:
                                os.remove(ack_filename)
                            except OSError:
                                pass
                            sess.incoming_file = None
                            sess.incoming_info = None
                            sess._incoming_file_msg_id = None
                            continue
                        ack_msg_id = sess._incoming_file_msg_id or 0
                        self._emit_file_event(
                            FileTransferInfo(
                                filename=ack_filename,
                                size=expected_size,
                                received=expected_size,
                                is_sending=False,
                            )
                        )
                        sess.incoming_file = None
                        sess.incoming_info = None
                        # Подтверждение получения файла (галочки у отправителя); отправляем basename, чтобы совпало с file_name у отправителя
                        try:
                            writer.write(
                                self.frame_message(
                                    "S",
                                    f"__SIGNAL__:FILE_ACK|{os.path.basename(ack_filename)}|{ack_msg_id}",
                                    peer_id=peer_id,
                                )
                            )
                            await writer.drain()
                        except Exception:
                            pass
                        sess._incoming_file_msg_id = None

                elif msg_type == "S":
                    if "__SIGNAL__:" in body:
                        if "GROUP_BLINDBOX_ROOT|" in body:
                            try:
                                await self._handle_incoming_group_blindbox_root_signal(
                                    body, writer, peer_id=peer_id
                                )
                            except Exception as e:
                                self._emit_error(
                                    f"Invalid Group BlindBox root signal: {e}"
                                )
                        elif "GROUP_BLINDBOX_ROOT_ACK|" in body:
                            try:
                                self._handle_group_blindbox_root_ack_signal(
                                    body, peer_id=peer_id
                                )
                            except Exception as e:
                                self._emit_error(
                                    f"Invalid Group BlindBox root ACK signal: {e}"
                                )
                        elif "BLINDBOX_ROOT|" in body:
                            try:
                                await self._handle_incoming_blindbox_root_signal(
                                    body, writer, peer_id=peer_id
                                )
                            except Exception as e:
                                self._emit_error(f"Invalid BlindBox root signal: {e}")
                        elif "BLINDBOX_ROOT_ACK|" in body:
                            try:
                                self._handle_blindbox_root_ack_signal(
                                    body, peer_id=peer_id
                                )
                            except Exception as e:
                                self._emit_error(
                                    f"Invalid BlindBox root ACK signal: {e}"
                                )
                        elif "MSG_ACK|" in body:
                            try:
                                ack_id_raw = body.split("MSG_ACK|", 1)[1].strip().split("|", 1)[0]
                                ack_id = int(ack_id_raw)
                                entry = sess._pending_text_acks.get(ack_id)
                                if entry is None:
                                    self._record_ack_drop("unknown_id", f"MSG_ACK id={ack_id}")
                                elif entry.state != "awaiting_ack":
                                    self._record_ack_drop(
                                        "expired_or_state",
                                        f"MSG_ACK id={ack_id} state={entry.state}",
                                    )
                                elif (
                                    entry.ack_kind != "msg"
                                    or entry.peer_addr != self._normalize_peer_addr(sess.peer_id or "")
                                    or entry.ack_session_epoch != sess._ack_session_epoch
                                ):
                                    self._record_ack_drop(
                                        "context_mismatch",
                                        f"MSG_ACK id={ack_id}",
                                    )
                                else:
                                    if self.on_text_delivered:
                                        self.on_text_delivered(str(ack_id))
                                    sess._pending_text_acks.pop(ack_id, None)
                                    self.session_manager.acknowledge_inflight_message(
                                        ack_id,
                                        peer_id=self._normalize_peer_addr(
                                            sess.peer_id or ""
                                        ),
                                    )
                            except Exception:
                                self._record_ack_drop("invalid_format", "MSG_ACK parse failed")
                        elif "IMG_ACK|" in body:
                            try:
                                ack_payload = body.split("IMG_ACK|", 1)[1].strip()
                                parts = ack_payload.split("|")
                                ack_filename = parts[0].strip()
                                ack_valid = False
                                if len(parts) > 1:
                                    try:
                                        ack_id = int(parts[1].strip())
                                        entry = sess._pending_image_acks.get(ack_id)
                                        ack_name = os.path.basename(ack_filename)
                                        if entry is None:
                                            self._record_ack_drop(
                                                "unknown_id",
                                                f"IMG_ACK id={ack_id} name={ack_name}",
                                            )
                                        elif entry.state != "awaiting_ack":
                                            self._record_ack_drop(
                                                "expired_or_state",
                                                f"IMG_ACK id={ack_id} state={entry.state}",
                                            )
                                        elif (
                                            entry.ack_kind == "image"
                                            and os.path.basename(entry.token) == ack_name
                                            and entry.peer_addr
                                            == self._normalize_peer_addr(sess.peer_id or "")
                                            and entry.ack_session_epoch
                                            == sess._ack_session_epoch
                                        ):
                                            sess._pending_image_acks.pop(ack_id, None)
                                            self.session_manager.acknowledge_inflight_message(
                                                ack_id,
                                                peer_id=self._normalize_peer_addr(
                                                    sess.peer_id or ""
                                                ),
                                            )
                                            ack_valid = True
                                        else:
                                            self._record_ack_drop(
                                                "context_mismatch",
                                                f"IMG_ACK id={ack_id} name={ack_name}",
                                            )
                                    except Exception:
                                        self._record_ack_drop("invalid_format", "IMG_ACK parse id failed")
                                else:
                                    self._record_ack_drop("invalid_format", "IMG_ACK missing id")
                                if ack_valid and self.on_image_delivered:
                                    self.on_image_delivered(ack_filename)
                            except Exception:
                                self._record_ack_drop("invalid_format", "IMG_ACK parse failed")
                        elif "FILE_ACK|" in body:
                            try:
                                ack_payload = body.split("FILE_ACK|", 1)[1].strip()
                                parts = ack_payload.split("|")
                                ack_filename = parts[0].strip()
                                ack_valid = False
                                if len(parts) > 1:
                                    try:
                                        ack_id = int(parts[1].strip())
                                        entry = sess._pending_file_acks.get(ack_id)
                                        ack_name = os.path.basename(ack_filename)
                                        if entry is None:
                                            self._record_ack_drop(
                                                "unknown_id",
                                                f"FILE_ACK id={ack_id} name={ack_name}",
                                            )
                                        elif entry.state != "awaiting_ack":
                                            self._record_ack_drop(
                                                "expired_or_state",
                                                f"FILE_ACK id={ack_id} state={entry.state}",
                                            )
                                        elif (
                                            entry.ack_kind == "file"
                                            and os.path.basename(entry.token) == ack_name
                                            and entry.peer_addr
                                            == self._normalize_peer_addr(sess.peer_id or "")
                                            and entry.ack_session_epoch
                                            == sess._ack_session_epoch
                                        ):
                                            sess._pending_file_acks.pop(ack_id, None)
                                            self.session_manager.acknowledge_inflight_message(
                                                ack_id,
                                                peer_id=self._normalize_peer_addr(
                                                    sess.peer_id or ""
                                                ),
                                            )
                                            ack_valid = True
                                        else:
                                            self._record_ack_drop(
                                                "context_mismatch",
                                                f"FILE_ACK id={ack_id} name={ack_name}",
                                            )
                                    except Exception:
                                        self._record_ack_drop("invalid_format", "FILE_ACK parse id failed")
                                else:
                                    self._record_ack_drop("invalid_format", "FILE_ACK missing id")
                                if ack_valid and self.on_file_delivered:
                                    self.on_file_delivered(ack_filename)
                            except Exception:
                                self._record_ack_drop("invalid_format", "FILE_ACK parse failed")
                        elif "REJECT_FILE|" in body:
                            sess._transfer_rejected_by_peer = True
                        elif "QUIT" in body:
                            self._emit_system("Peer requested disconnect.")
                            break
                        elif "ABORT_FILE" in body:
                            sess._transfer_aborted_by_peer = True
                            if sess.incoming_file and sess.incoming_info:
                                try:
                                    sess.incoming_file.close()
                                except Exception:
                                    pass
                                sess.incoming_file = None
                                self._emit_file_event(FileTransferInfo(
                                    filename=sess.incoming_info.filename,
                                    size=sess.incoming_info.size,
                                    received=-1,
                                    is_sending=False,
                                ))
                                sess.incoming_info = None
                                self._emit_system("Sender cancelled the transfer")
                            continue
                    else:
                        try:
                            dest_obj = i2plib.Destination(body)
                            new_peer = dest_obj.base32
                            if sess.peer_id and new_peer != sess.peer_id:
                                self._emit_error(
                                    f"Blocked identity mismatch: expected {sess.peer_id[:16]}..., got {new_peer[:16]}..."
                                )
                                break
                            if not await self._set_verified_peer_identity(
                                new_peer, body, source="framed"
                            ):
                                break
                            self.peer_b32 = new_peer
                            self._emit_message(
                                "info", f"Peer Identity: {self.peer_b32}"
                            )
                            self._emit_peer_changed(self.peer_b32)
                        except Exception:
                            pass

                elif msg_type == "H":
                    await self._handle_handshake_message(body, writer, peer_id=peer_id)

                elif msg_type == "P":
                    writer.write(self.frame_message("O", "", peer_id=peer_id))
                    await writer.drain()

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            if sess.conn == connection:
                self._emit_error(f"Protocol Error: {e}")
        finally:
            current_task = asyncio.current_task()
            sess._recv_loop_active = False
            skip_cleanup = False
            if (
                restart_after_timeout
                and sess.conn == connection
                and (
                    sess.incoming_info is not None
                    or sess.inline_image_info is not None
                )
                and not sess._file_transfer_active
            ):
                try:
                    self._start_receive_loop_task(connection, peer_id=peer_id)
                except RuntimeError:
                    pass
                skip_cleanup = True
            if sess.receive_task is current_task:
                sess.receive_task = None
            # Не сбрасываем соединение если идёт передача или приём файла / inline-изображения
            if (
                not skip_cleanup
                and sess.conn == connection
                and not sess._file_transfer_active
                and sess.incoming_info is None
                and sess.inline_image_info is None
            ):
                self._cancel_handshake_watchdog(peer_id)
                peer_before_cleanup = self._normalize_peer_addr(sess.peer_id or "")
                self._live_sessions.pop(peer_id, None)
                sess.reset_crypto()
                if peer_before_cleanup:
                    self.session_manager.reset_peer_lifecycle(
                        peer_before_cleanup, reason="receive-loop-cleanup"
                    )
                self.session_manager.mark_live_failure(
                    reason="peer-disconnect",
                    mark_peer_stale=False,
                    peer_id=peer_before_cleanup,
                )
                if sess.announce_lifecycle:
                    self._emit_message("info", "Peer disconnected.")
                self.peer_b32 = "Waiting for incoming connections..."
                if sess.announce_lifecycle:
                    self._emit_system("Waiting for incoming connections...")
                self._notify_group_mesh_manager()
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                await self._maybe_stop_keepalive_if_idle()
                if self._any_live_stream() and (
                    self.session_manager.keepalive_task is None
                    or self.session_manager.keepalive_task.done()
                ):
                    try:
                        loop = asyncio.get_running_loop()
                        self.session_manager.keepalive_task = loop.create_task(
                            self._keepalive_loop()
                        )
                    except RuntimeError:
                        pass

    async def tunnel_watcher(self) -> None:
        while True:
            if not self.my_dest:
                await asyncio.sleep(2)
                continue
            try:
                await asyncio.wait_for(
                    i2plib.naming_lookup(
                        self.my_dest.base32 + ".b32.i2p",
                        sam_address=self.sam_address,
                    ),
                    timeout=5.0,
                )
                if self.network_status != "visible":
                    self._emit_status("visible")
                    self._emit_message(
                        "success",
                        "Tunnels confirmed. You are now VISIBLE.",
                    )
                self.session_manager.transition_transport(
                    TransportState.READY, reason="tunnel-watch-ok"
                )
            except asyncio.TimeoutError:
                if self.session_manager.transport_state == TransportState.READY:
                    self.session_manager.transition_transport(
                        TransportState.DEGRADED, reason="tunnel-watch-timeout"
                    )
            except Exception:
                # Keep current network status on transient lookup errors.
                if self.session_manager.transport_state == TransportState.READY:
                    self.session_manager.transition_transport(
                        TransportState.DEGRADED, reason="tunnel-watch-error"
                    )

            await asyncio.sleep(20)


def _load_image(path: str, max_width: int = 80) -> Image.Image:
    img = Image.open(path).convert("L")
    w, h = img.size
    if w > max_width:
        ratio = max_width / float(w)
        img = img.resize((max_width, max(int(h * ratio))), Image.LANCZOS)
    return img


def render_bw(path: str) -> List[str]:
    img = _load_image(path)
    img = img.point(lambda v: 0 if v < 128 else 255, mode="1")

    chars = {0: "█", 255: " "}
    pixels = img.load()
    w, h = img.size

    lines: List[str] = []
    for y in range(h):
        row_chars: List[str] = []
        for x in range(w):
            row_chars.append(chars[255 if pixels[x, y] else 0])
        lines.append("".join(row_chars).rstrip())
    return lines


def render_braille(path: str) -> List[str]:
    img = _load_image(path)
    w, h = img.size
    w_aligned = w - (w % 2)
    h_aligned = h - (h % 4)
    if w_aligned <= 0 or h_aligned <= 0:
        return []
    img = img.crop((0, 0, w_aligned, h_aligned))
    img = img.point(lambda v: 0 if v < 128 else 1, mode="1")
    pixels = img.load()
    w, h = img.size

    def cell_to_braille(cx: int, cy: int) -> str:
        offsets = [
            (0, 0, 0),
            (0, 1, 1),
            (0, 2, 2),
            (1, 0, 3),
            (1, 1, 4),
            (1, 2, 5),
            (0, 3, 6),
            (1, 3, 7),
        ]
        value = 0
        for dx, dy, bit in offsets:
            if pixels[cx + dx, cy + dy] == 0:
                value |= 1 << bit
        if value == 0:
            return " "
        return chr(0x2800 + value)

    lines: List[str] = []
    for cy in range(0, h, 4):
        row_chars: List[str] = []
        for cx in range(0, w, 2):
            row_chars.append(cell_to_braille(cx, cy))
        lines.append("".join(row_chars).rstrip())
    return lines
