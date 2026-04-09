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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Literal, Optional, Tuple

from i2pchat import sam as i2plib
from PIL import Image

from i2pchat import crypto
from i2pchat.blindbox.blindbox_blob import decrypt_blindbox_blob, encrypt_blindbox_blob
from i2pchat.blindbox.blindbox_client import BlindBoxClient
from i2pchat.blindbox.blindbox_key_schedule import derive_blindbox_message_keys
from i2pchat.blindbox.blindbox_local_replica import ensure_local_blindbox_replica
from i2pchat.storage.blindbox_state import (
    BlindBoxState,
    atomic_write_json,
    atomic_write_text,
)
from i2pchat.storage.profile_blindbox_replicas import (
    load_profile_blindbox_replicas_bundle,
    normalize_replica_endpoints,
    save_profile_blindbox_replicas_bundle,
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
    return host + ".b32.i2p"


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
        self.current_peer_addr: Optional[str] = None
        self.current_peer_dest_b64: Optional[str] = None
        self.peer_identity_binding_verified: bool = False
        self.conn: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
        self._outbound_connect_busy: bool = False
        self.proven: bool = False

        # файловый приём
        self.incoming_file = None
        self.incoming_info: Optional[FileTransferInfo] = None

        # буфер для изображений (ASCII-арт)
        self.image_buffer: list[str] = []
        
        # буфер для inline-изображений (бинарные данные)
        self.inline_image_buffer: bytearray = bytearray()
        self.inline_image_info: Optional[Tuple[str, int]] = None  # (filename, size)

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

        self._accept_task: Optional[asyncio.Task[Any]] = None
        self._tunnel_task: Optional[asyncio.Task[Any]] = None
        self._keepalive_task: Optional[asyncio.Task[Any]] = None
        self._handshake_watchdog_task: Optional[asyncio.Task[Any]] = None
        self._handshake_watchdog_generation: int = 0
        self._disconnect_task: Optional[asyncio.Task[Any]] = None
        self._disconnecting: bool = False
        # Сокет сессии SAM: по спецификации сессия живёт только пока этот сокет открыт.
        # Если его не хранить, сокет закрывается и сессия умирает — при Connect роутер может падать.
        self._session_socket: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
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
        self._blindbox_poll_wakeup = asyncio.Event()
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
    ) -> None:
        if self.on_message:
            sp = source_peer if kind == "peer" else None
            msg = ChatMessage(
                kind=kind,
                text=text,
                timestamp=datetime.now(timezone.utc),
                source_peer=sp,
                message_id=message_id,
                delivery_state=delivery_state,
                delivery_route=delivery_route,
                delivery_hint=delivery_hint,
                delivery_reason=delivery_reason,
                retryable=retryable,
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
        self, kind: str, text: str, source_peer: Optional[str] = None
    ) -> None:
        """
        Уведомление UI о новом сообщении для системных нотификаций.

        Отдельный слой, чтобы ядро не зависело от конкретной реализации уведомлений.
        """
        callback = getattr(self, "on_notify", None)
        if callback is not None:
            try:
                sp = source_peer if kind == "peer" else None
                callback(
                    ChatMessage(
                        kind=kind,
                        text=text,
                        timestamp=datetime.now(timezone.utc),
                        source_peer=sp,
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

    def _require_secure_channel(self) -> bool:
        """Проверяет, что можно отправлять пользовательские данные."""
        if not self.conn:
            self._emit_error("No active connection.")
            return False
        if not self.handshake_complete:
            self._emit_error("Secure channel not ready yet. Wait for 'Ready'.")
            return False
        return True

    def _cancel_handshake_watchdog(self) -> None:
        # Не отменяем задачу напрямую внутри активной корутины: в некоторых
        # loop-интеграциях (Qt/qasync) это может вызвать re-entrant step Task.
        self._handshake_watchdog_generation += 1
        self._handshake_watchdog_task = None

    def _start_handshake_watchdog(
        self, connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter]
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cancel_handshake_watchdog()
        generation = self._handshake_watchdog_generation
        self._handshake_watchdog_task = loop.create_task(
            self._handshake_watchdog(connection, generation)
        )

    def _schedule_disconnect(self) -> None:
        if self._disconnecting or self.conn is None:
            return
        if self._disconnect_task is not None and not self._disconnect_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._disconnect_task = loop.create_task(self.disconnect())

    async def _handshake_watchdog(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        generation: int,
    ) -> None:
        """Закрывает соединение, если handshake не завершился вовремя."""
        await asyncio.sleep(self.HANDSHAKE_TIMEOUT)
        if generation != self._handshake_watchdog_generation:
            return
        if self.conn == connection and not self.handshake_complete:
            self._emit_error("Secure handshake timed out")
            self._schedule_disconnect()

    # ---------- протокол ----------

    def _allocate_msg_id(self) -> int:
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        if self._next_msg_id > 0xFFFFFFFFFFFFFFFF:
            self._next_msg_id = 1
        return msg_id

    def frame_message_with_id(
        self, msg_type: str, content: str, *, force_plain: bool = False
    ) -> tuple[bytes, int]:
        """
        Формирует vNext-фрейм:
        MAGIC | VERSION | TYPE | FLAGS | MSG_ID | LEN | PAYLOAD
        """
        body = content.encode("utf-8")
        msg_id = self._allocate_msg_id()

        if (
            self.shared_key
            and self.use_encryption
            and not force_plain
        ):
            if not crypto.NACL_AVAILABLE:
                raise RuntimeError("NaCl is required for secure protocol mode")
            self._send_seq += 1
            seq = self._send_seq
            mac_key = self.shared_mac_key or self.shared_key
            padded_body = self._apply_padding_profile(body)
            encrypted_body = crypto.encrypt_message(self.shared_key, padded_body)
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

    def frame_message(self, msg_type: str, content: str) -> bytes:
        frame, _ = self.frame_message_with_id(msg_type, content)
        return frame

    def frame_message_plain(self, msg_type: str, content: str) -> bytes:
        """Формирует незашифрованный фрейм (handshake/control)."""
        frame, _ = self.frame_message_with_id(msg_type, content, force_plain=True)
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

    def _blindbox_peer_id(self) -> Optional[str]:
        peer = self._normalize_peer_addr(self.stored_peer or self.current_peer_addr or "")
        if not peer:
            return None
        if peer.endswith(".b32.i2p"):
            return peer[: -len(".b32.i2p")]
        return peer

    def _blindbox_state_path(self) -> str:
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            raise ValueError("BlindBox peer id is not available")
        safe_peer = re.sub(r"[^a-z0-9._-]", "_", peer_id.lower())
        return self._profile_scoped_path(f"{self.profile}.blindbox.{safe_peer}.json")

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

    def _blindbox_prune_previous_roots(self) -> None:
        now_ts = int(time.time())
        filtered = [
            item
            for item in self._blindbox_prev_roots
            if int(item.get("expires_at", 0)) > now_ts
        ]
        filtered.sort(key=lambda item: int(item.get("epoch", 0)), reverse=True)
        if self._blindbox_max_previous_roots >= 0:
            filtered = filtered[: self._blindbox_max_previous_roots]
        self._blindbox_prev_roots = filtered

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
            and bool(self.stored_peer)
            and bool(self.blindbox_replicas)
            and self.my_dest is not None
        )

    def _blindbox_current_peer_matches_locked_peer(self) -> bool:
        try:
            locked_peer = self._normalize_peer_addr(self.stored_peer or "")
            current_peer = self._normalize_peer_addr(self.current_peer_addr or "")
        except ValueError:
            return False
        return bool(locked_peer and current_peer and locked_peer == current_peer)

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
        Канонический peer id: host из base32 + '.b32.i2p'.
        Допускает типичный ввод из UI/чата: пробелы, префиксты («My Addr: …»), вставку строки целиком.
        """
        raw = (addr or "").strip()
        if not raw:
            return ""
        lower = raw.lower()
        # Первая подходящая подстрока … .b32.i2p (игнорирует префикс/мусор вокруг).
        m = re.search(r"([a-z2-7]{40,80})\.b32\.i2p", lower)
        if m:
            return m.group(1) + ".b32.i2p"
        compact = re.sub(r"\s+", "", lower)
        if any(ch in compact for ch in ("\r", "\n", "\x00", "\t", "=")):
            raise ValueError("Peer address contains forbidden characters")
        if compact.endswith(".b32.i2p"):
            host = compact[: -len(".b32.i2p")]
        elif "." in compact:
            raise ValueError("Peer address must use .b32.i2p format")
        else:
            host = compact
        if not re.fullmatch(r"[a-z2-7]{40,80}", host):
            raise ValueError("Peer address format is invalid")
        return host + ".b32.i2p"

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
                i2plib.dest_lookup(normalized_addr, sam_address=self.sam_address),
                timeout=12.0,
            )
            looked_up_base64: str
            if isinstance(looked_up, i2plib.Destination):
                looked_up_base64 = looked_up.base64
            else:
                looked_up_base64 = i2plib.Destination(str(looked_up)).base64
            return looked_up_base64 == dest_base64
        except Exception as e:
            logger.warning("SAM binding verification failed for %s: %s", normalized_addr, e)
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
        self.current_peer_addr = normalized_addr
        self.current_peer_dest_b64 = canonical_dest
        self.peer_identity_binding_verified = True
        return True

    def _is_probable_peer_addr(self, value: str) -> bool:
        raw = (value or "").strip().lower()
        if not raw:
            return False
        if raw.endswith(".b32.i2p"):
            raw = raw[:-8]
        return bool(re.fullmatch(r"[a-z2-7]{40,80}", raw))

    def _write_profile_dat(
        self,
        private_key_base64: Optional[str],
        stored_peer: Optional[str],
    ) -> None:
        """Сохраняет .dat в каноничном формате: key на 1-й, peer на 2-й строке."""
        if self.profile == TRANSIENT_PROFILE_NAME:
            return
        lines: list[str] = []
        key = (private_key_base64 or "").strip()
        peer = self._normalize_peer_addr(stored_peer or "")
        if key:
            lines.append(key)
        if peer:
            lines.append(peer)
        if not lines:
            return
        path = self._profile_path()
        atomic_write_text(path, "\n".join(lines) + "\n")

    def save_stored_peer(self, peer_addr: str) -> None:
        """
        Сохраняет lock-пир в профиль без дублирования строк.

        Форматы, которые поддерживаем:
        - line1=private_key, line2=stored_peer
        - line1=stored_peer (когда identity хранится в keyring)
        """
        if self.profile == TRANSIENT_PROFILE_NAME:
            raise ValueError("Cannot store peer for transient profile")
        normalized_peer = self._normalize_peer_addr(peer_addr)
        if not normalized_peer:
            raise ValueError("Peer address is empty")

        private_key_base64: Optional[str] = None
        if self.my_dest is not None:
            try:
                private_key_base64 = self.my_dest.private_key.base64
            except Exception:
                private_key_base64 = None
        if not private_key_base64:
            key_file = self._profile_path()
            if os.path.exists(key_file):
                with open(key_file, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                if lines and not self._is_probable_peer_addr(lines[0]):
                    private_key_base64 = lines[0]

        self._write_profile_dat(private_key_base64, normalized_peer)
        self.stored_peer = normalized_peer
        self._load_blindbox_state()

    def clear_locked_peer(self) -> None:
        """
        Снять Lock to peer: в .dat остаётся только приватный ключ (или файл с одной строкой
        пира удаляется при сценарии keyring-only .dat).
        """
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
        return bool(
            self.current_peer_addr
            and self.handshake_complete
            and self.peer_identity_binding_verified
        )

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
                if self.conn is not None and not self.handshake_complete:
                    self._start_handshake_watchdog(self.conn)
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
                if self.conn is not None and not self.handshake_complete:
                    self._start_handshake_watchdog(self.conn)
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
            await self._do_init_session()
        except Exception as e:
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
                    self.stored_peer = self._normalize_peer_addr(stored_line)
                    disp_peer = self.stored_peer
                    if not disp_peer.endswith(".b32.i2p"):
                        disp_peer = disp_peer + ".b32.i2p"
                    self._emit_system(f"Stored Contact: {disp_peer}")

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
            self._write_profile_dat(self.my_dest.private_key.base64, self.stored_peer)
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
        self._session_socket = await i2plib.create_session(
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

        # Blind Box uses a second SAM stream session; starting it here overlaps with
        # the tunnel-build poll below so "BlindBox runtime started" is not delayed
        # by the full main-tunnel wait (previously ~up to 90s extra wall time).
        if self._blindbox_ready():
            self._emit_system(
                "Blind Box: starting SAM session in parallel with main tunnels…"
            )
            asyncio.create_task(self._ensure_blindbox_runtime_started())

        my_address = self.my_dest.base32 + ".b32.i2p"
        self._emit_system("Building I2P tunnels (may take 1–2 min)...")
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
            self._emit_message("success", f"Online! My Address: {my_address}")
            self._emit_system("Tunnels ready. Waiting for incoming connections...")
        else:
            self._emit_status("local_ok")
            self._emit_message("success", f"Online! My Address: {my_address}")
            self._emit_system(
                "Tunnels may still be building. Wait 1–2 min before connecting."
            )

        self.peer_b32 = f"My Addr: {my_address}"

        # запуск фоновых задач
        loop = asyncio.get_running_loop()
        self._accept_task = loop.create_task(self.accept_loop())
        self._tunnel_task = loop.create_task(self.tunnel_watcher())
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

    def _activate_ack_session(self) -> None:
        self._ack_session_epoch += 1
        if self._ack_session_epoch > 0x7FFFFFFF:
            self._ack_session_epoch = 1

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
    ) -> None:
        self._prune_pending_acks(force=False)
        table[msg_id] = PendingAckEntry(
            token=token,
            ack_kind=ack_kind,
            created_at=time.monotonic(),
            peer_addr=self._current_ack_peer(),
            ack_session_epoch=self._ack_session_epoch,
            state="awaiting_ack",
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
            and self._blindbox_client.is_runtime_ready()
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
        connected = self.conn is not None
        secure_live = bool(connected and self.handshake_complete)
        has_target = bool(self.current_peer_addr or self.stored_peer)
        ready = bool(self._blindbox_ready())
        has_root_secret = self._blindbox_root_secret is not None
        bb_client = self._blindbox_client
        blindbox_runtime_ready = bool(
            bb_client is not None and bb_client.is_runtime_ready()
        )

        if connected and not self.handshake_complete:
            state = "connecting-handshake"
        elif secure_live:
            state = "online-live"
        elif ready and has_root_secret:
            state = "offline-ready"
        elif ready and not has_root_secret:
            state = "await-live-root"
        elif self.blindbox_enabled and not self.stored_peer:
            state = "blindbox-needs-locked-peer"
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
        }

    def _offline_send_block_feedback(self) -> tuple[str, str]:
        delivery = self.get_delivery_telemetry()
        state = str(delivery.get("state", "unknown"))
        if not delivery.get("has_target"):
            return (
                "no-target",
                "No peer selected. Enter or lock a peer address, then send.",
            )
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
        if state == "blindbox-needs-locked-peer":
            return (
                "blindbox-needs-locked-peer",
                "BlindBox requires a locked peer in the current profile.",
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
        recv_start = max(0, self._blindbox_state.recv_base - recv_backtrack)
        recv_span = max(self._blindbox_state.recv_window, recv_lookahead)
        recv_end = self._blindbox_state.recv_base + recv_span
        candidates = [
            idx
            for idx in range(recv_start, recv_end)
            if idx not in self._blindbox_state.consumed_recv
        ]
        self._blindbox_rng.shuffle(candidates)
        return candidates

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
            self._emit_system("BlindBox runtime started")
            self._trigger_blindbox_hot_poll("startup")
        except Exception as e:
            detail = _exception_user_message(e)
            logger.exception("BlindBox startup failed: %s", detail)
            self._emit_error(f"BlindBox startup failed: {detail}")
            return
        try:
            while True:
                if not self._blindbox_ready():
                    await self._blindbox_poll_sleep()
                    continue
                # Poll even when a live TCP session exists: offline sends only hit Blind Box;
                # the peer must GET+decrypt while connected too, otherwise messages never arrive.
                if self._blindbox_root_secret is None:
                    await self._blindbox_poll_sleep()
                    continue
                peer_id = self._blindbox_peer_id()
                if not peer_id or not self.my_dest:
                    await self._blindbox_poll_sleep()
                    continue
                local_id = self.my_dest.base32
                root_candidates = self._blindbox_root_candidates()
                if not root_candidates:
                    await self._blindbox_poll_sleep()
                    continue
                recv_cands = self._blindbox_recv_candidates()
                for recv_index in recv_cands:
                    got_valid = False
                    for root_item in root_candidates:
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
                                    # Blob direction is encoded by sender perspective.
                                    # Receiver must expect "send" for inbound offline envelopes.
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

                        try:
                            accepted_blob = await client.get_first_accepted(
                                keys.lookup_token,
                                accept_blob=_accept_blob,
                            )
                        except Exception as exc:
                            logger.debug(
                                "BlindBox get_first_accepted failed recv_index=%s epoch=%s: %s",
                                recv_index,
                                int(root_item["epoch"]),
                                exc,
                                exc_info=True,
                            )
                            continue
                        if accepted_blob is not None:
                            got_valid = True
                            break
                    if got_valid:
                        self._blindbox_state.mark_consumed(recv_index)
                        self._save_blindbox_state()
                        self._trigger_blindbox_hot_poll("received-offline-message")
                await self._blindbox_emit_cover_gets(client)
                await self._blindbox_poll_sleep()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            detail = _exception_user_message(e)
            logger.exception("BlindBox poller stopped: %s", detail)
            self._emit_error(f"BlindBox poller stopped: {detail}")

    async def _process_blindbox_frame(self, frame: bytes) -> bool:
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
        sp = (self.stored_peer or self.current_peer_addr or "").strip() or None
        self._emit_message("peer", text, source_peer=sp)
        self._emit_notify("peer", text, source_peer=sp)
        return True

    async def _send_text_via_blindbox(self, text: str) -> Optional[int]:
        if not self._blindbox_ready():
            return None
        if self._blindbox_root_secret is None:
            return None
        if not self.my_dest or self._blindbox_client is None:
            return None
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            return None

        async with self._blindbox_send_lock:
            if not self._blindbox_ready():
                return None
            if self._blindbox_root_secret is None:
                return None
            if not self.my_dest or self._blindbox_client is None:
                return None
            peer_id = self._blindbox_peer_id()
            if not peer_id:
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
                    keys = derive_blindbox_message_keys(
                        self._blindbox_root_secret,
                        self.my_dest.base32,
                        peer_id,
                        "send",
                        self._blindbox_state.send_index,
                        epoch=self._blindbox_root_epoch,
                    )
                    blob = encrypt_blindbox_blob(
                        frame,
                        keys.blob_key,
                        "send",
                        self._blindbox_state.send_index,
                        keys.state_tag,
                        padding_bucket=self._blindbox_padding_bucket,
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
                        await asyncio.wait_for(
                            self._blindbox_client.put(keys.lookup_token, blob),
                            timeout=put_timeout_sec,
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
                    self._blindbox_state.send_index += 1
                    self._blindbox_state.updated_at = int(time.time())
                    self._save_blindbox_state()
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
                logger.warning("BlindBox send failed: %s", detail, exc_info=True)
                self._emit_error(f"BlindBox send failed: {detail}")
                return None

    def is_outbound_connect_busy(self) -> bool:
        """True, пока выполняется исходящий connect_to_peer (ожидание stream_connect)."""
        return self._outbound_connect_busy

    async def connect_to_peer(self, target_address: str) -> None:
        if not crypto.NACL_AVAILABLE:
            detail = getattr(crypto, "NACL_IMPORT_ERROR", "") or "pynacl not installed"
            self._emit_error(f"Secure protocol requires PyNaCl. Install: pip install pynacl. ({detail})")
            return
        try:
            normalized_target = self._normalize_peer_addr(target_address)
        except ValueError as e:
            self._emit_error(str(e).strip() or "Invalid peer address")
            return
        try:
            locked_peer = self._normalize_peer_addr(self.stored_peer or "")
        except ValueError:
            locked_peer = ""
        if locked_peer and normalized_target and normalized_target != locked_peer:
            self._emit_error(
                "Profile is locked to another peer. "
                "Connect to the stored peer or unlock/change the profile first."
            )
            return
        if self.conn is not None:
            self._emit_system("Already connected. Disconnect first.")
            return
        if self._outbound_connect_busy:
            self._emit_system("Connection attempt already in progress.")
            return
        self._outbound_connect_busy = True
        deferred_error: Optional[str] = None
        deferred_system: Optional[str] = None
        try:
            try:
                self.current_peer_addr = normalized_target
                self._reset_crypto_state()
                self.current_peer_addr = normalized_target
                self._emit_system(
                    f"Connecting to {normalized_target[:24]}... "
                    "(may take 1–2 min while I2P builds tunnels)"
                )
                reader: asyncio.StreamReader
                writer: asyncio.StreamWriter
                last_connect_exc: Optional[Exception] = None
                for attempt in range(2):
                    try:
                        reader, writer = await asyncio.wait_for(
                            i2plib.stream_connect(
                                self.session_id,
                                normalized_target,
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

                if self.my_dest is not None:
                    # Backward-safe identity preface для accept_loop(reader.readline()).
                    writer.write(self.my_dest.base64.encode("utf-8") + b"\n")
                    writer.write(self.frame_message("S", self.my_dest.base64))
                    await writer.drain()

                    self.proven = True
                    self._emit_status("visible")

                self.conn = (reader, writer)
                self._activate_ack_session()
                self._emit_message(
                    "info", "Handshake sent. Establishing secure channel... Wait"
                )

                loop = asyncio.get_running_loop()
                loop.create_task(self.receive_loop(self.conn))
                loop.create_task(self.initiate_secure_handshake())
                self._start_handshake_watchdog(self.conn)
                self._keepalive_task = loop.create_task(self._keepalive_loop())
            except asyncio.TimeoutError:
                self.conn = None
                deferred_error = (
                    "Connection timed out. Check: I2P router running, peer address correct, peer online."
                )
                deferred_system = "Waiting for incoming connections..."
            except Exception as e:
                self.conn = None
                # Пустое сообщение у SAM-исключений (например CantReachPeer()) — только имя типа,
                # без «CantReachPeer: CantReachPeer()».
                detail = str(e).strip() or type(e).__name__
                hint = _sam_stream_connect_hint(e)
                deferred_error = (
                    f"Connection failed: {detail}" + (f" {hint}" if hint else "")
                )
                deferred_system = "Waiting for incoming connections..."
        finally:
            self._outbound_connect_busy = False

        # После сброса busy — иначе UI обновится с is_outbound_connect_busy()==True и Connect останется серым.
        if deferred_error:
            self._emit_error(deferred_error)
        if deferred_system:
            self._emit_system(deferred_system)

    async def send_text(
        self,
        text: str,
        *,
        route: Literal["auto", "live", "offline"] = "auto",
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

        if r == "offline":
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
            sent_offline = await self._send_text_via_blindbox(text)
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

        secure_live = bool(self.conn and self.handshake_complete)

        if r == "live":
            if not secure_live:
                if self.conn is not None and not self.handshake_complete:
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

        if r == "auto" and not secure_live:
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
            sent_offline = await self._send_text_via_blindbox(text)
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
        if not self._require_secure_channel():
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
            _, writer = self.conn
            if self._blindbox_ready():
                await self._send_blindbox_root_if_needed(writer)
            chunks = split_long_chat_text(text)
            last_msg_id: Optional[int] = None
            lifecycle = delivery_lifecycle_from_send_result(
                route="online-live",
                accepted=True,
                reason="live-session",
                hint="Message sent over live secure session.",
            )
            for chunk in chunks:
                frame, msg_id = self.frame_message_with_id("U", chunk)
                writer.write(frame)
                await writer.drain()
                self._register_pending_ack(
                    self._pending_text_acks,
                    msg_id,
                    token=chunk[:128],
                    ack_kind="msg",
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
            self._schedule_disconnect()
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
        self, writer: asyncio.StreamWriter, frame: bytes
    ) -> None:
        """S-фрейм (MSG_ACK, IMG_ACK): при исходящей передаче файла реже await drain()."""
        writer.write(frame)
        if not self._file_transfer_active:
            await writer.drain()
            self._soft_signal_ack_since_drain = 0
            return
        self._soft_signal_ack_since_drain += 1
        if self._soft_signal_ack_since_drain >= _msg_ack_soft_drain_every():
            await writer.drain()
            self._soft_signal_ack_since_drain = 0

    async def _send_abort_file(self) -> None:
        """Отправить пиру сигнал отмены передачи файла (получатель отменил или отправитель)."""
        if not self.conn:
            return
        try:
            _, writer = self.conn
            writer.write(self.frame_message("S", "__SIGNAL__:ABORT_FILE"))
            await writer.drain()
        except Exception:
            pass

    async def reject_incoming_file(self, filename: str) -> None:
        """Уведомить отправителя, что получатель отклонил входящий файл."""
        if not self.conn:
            return
        try:
            _, writer = self.conn
            writer.write(self.frame_message("S", f"__SIGNAL__:REJECT_FILE|{filename}"))
            await writer.drain()
        except Exception:
            pass

    def cancel_file_transfer(self) -> None:
        """Отменить текущую передачу файла (на получателе — также уведомить отправителя)."""
        self._cancel_transfer = True
        if self.incoming_file:
            try:
                self.incoming_file.close()
            except Exception:
                pass
            self.incoming_file = None
        if self.incoming_info:
            self._emit_file_event(FileTransferInfo(
                filename=self.incoming_info.filename,
                size=self.incoming_info.size,
                received=-1,
                is_sending=False,
            ))
            self.incoming_info = None
        # Уведомить отправителя, чтобы он прекратил слать чанки
        if self.conn:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._send_abort_file())
            except RuntimeError:
                pass

    async def send_file(self, path: str) -> None:
        if not self._require_secure_channel():
            return
        
        filename = os.path.basename(path)
        filesize = os.path.getsize(path)
        
        self._file_transfer_active = True
        self._soft_signal_ack_since_drain = 0
        self._cancel_transfer = False
        self._transfer_aborted_by_peer = False
        self._transfer_rejected_by_peer = False
        
        try:
            reader, writer = self.conn
            self._emit_system(f"Sending file: {filename} ({filesize} bytes)")

            header = f"{filename}|{filesize}"
            header_frame, file_msg_id = self.frame_message_with_id("F", header)
            writer.write(header_frame)
            await writer.drain()
            self._register_pending_ack(
                self._pending_file_acks,
                file_msg_id,
                token=os.path.basename(filename),
                ack_kind="file",
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
                    if self._cancel_transfer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        await self._send_abort_file()
                        raise Exception("Transfer cancelled by user")
                    if self._transfer_aborted_by_peer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        self._emit_system("Receiver cancelled the transfer")
                        raise Exception("Transfer cancelled by receiver")
                    if self._transfer_rejected_by_peer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        raise Exception("Receiver rejected the file")
                    if not self.conn:
                        raise ConnectionError("Connection lost during transfer")

                    chunk = await asyncio.to_thread(f.read, chunk_size)
                    if not chunk:
                        break

                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("D", encoded))
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

            writer.write(self.frame_message("E", ""))
            await writer.drain()

            info = FileTransferInfo(
                filename=filename,
                size=filesize,
                received=filesize,
                is_sending=True,
                source_path=path,
            )
            self._emit_file_event(info)
            
            # Перезапуск receive_loop если он был прерван timeout'ом во время передачи
            if self.conn:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.receive_loop(self.conn))
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
            self._file_transfer_active = False
            self._soft_signal_ack_since_drain = 0

    async def send_image_lines(self, lines: list[str]) -> None:
        """Отправить уже отрендеренное изображение построчно."""
        if not self._require_secure_channel():
            return
        reader, writer = self.conn

        for line in lines:
            writer.write(self.frame_message("I", line))
        writer.write(self.frame_message("I", "__END__"))
        await writer.drain()

    async def send_image(self, path: str) -> Optional[str]:
        """
        Отправить изображение (PNG/JPEG/WebP) с валидацией.
        
        Args:
            path: путь к файлу изображения
            
        Returns:
            путь к копии изображения в images/ или None при ошибке
        """
        if not self._require_secure_channel():
            return None
        
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
        
        self._file_transfer_active = True
        self._soft_signal_ack_since_drain = 0
        self._cancel_transfer = False
        
        try:
            reader, writer = self.conn
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
            header_frame, image_msg_id = self.frame_message_with_id("G", header)
            writer.write(header_frame)
            await writer.drain()
            self._register_pending_ack(
                self._pending_image_acks,
                image_msg_id,
                token=os.path.basename(filename),
                ack_kind="image",
            )

            chunk_size = _file_read_chunk_bytes()
            drain_batch = _file_send_drain_batch()
            sent = 0
            pending_drains = 0
            with open(path, "rb") as f:
                while True:
                    if self._cancel_transfer:
                        if pending_drains:
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                        raise Exception("Transfer cancelled by user")
                    if not self.conn:
                        raise ConnectionError("Connection lost during transfer")

                    chunk = await asyncio.to_thread(f.read, chunk_size)
                    if not chunk:
                        break

                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("G", encoded))
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
            writer.write(self.frame_message("G", "__IMG_END__"))
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
            self._file_transfer_active = False
            self._soft_signal_ack_since_drain = 0

    async def send_control(self, signal: str) -> None:
        if not self.conn:
            return
        try:
            _, writer = self.conn
            writer.write(self.frame_message("S", f"__SIGNAL__:{signal}"))
            await writer.drain()
        except Exception:
            pass

    async def disconnect(self) -> None:
        if self._disconnecting or not self.conn:
            return
        self._disconnecting = True
        try:
            self._cancel_handshake_watchdog()
            # Останавливаем keepalive
            if self._keepalive_task:
                asyncio.get_running_loop().call_soon(self._keepalive_task.cancel)
                self._keepalive_task = None
            _, writer = self.conn
            self.conn = None
            self.peer_b32 = "Waiting for incoming connections..."
            had_secure_channel = self.handshake_complete and self.use_encryption and bool(self.shared_key)
            try:
                if had_secure_channel:
                    writer.write(self.frame_message("S", "__SIGNAL__:QUIT"))
                else:
                    writer.write(self.frame_message_plain("S", "__SIGNAL__:QUIT"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self._reset_crypto_state()
            self._emit_message("info", "You disconnected.")
            self._emit_system("Waiting for incoming connections...")
        finally:
            self._disconnecting = False
            if self._disconnect_task is asyncio.current_task():
                self._disconnect_task = None
    
    async def _keepalive_loop(self) -> None:
        """Отправляет Ping каждые 15 секунд для поддержания соединения при простое."""
        while self.conn:
            await asyncio.sleep(15)
            if self.conn and not self._file_transfer_active:
                if not (self.handshake_complete and self.use_encryption and self.shared_key):
                    continue
                try:
                    _, writer = self.conn
                    writer.write(self.frame_message("P", ""))
                    await writer.drain()
                except Exception:
                    break
    
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

    async def initiate_secure_handshake(self) -> bool:
        """
        Инициирует защищённый handshake (v2 протокол с PFS).
        
        Обязательный NaCl-режим с эфемерными X25519 ключами (PFS).
        Формат: INIT:<nonce_hex>:<ephemeral_pubkey_hex>:<sign_pub_hex>:<signature_hex>
        
        Returns:
            True если handshake успешен
        """
        if not self.conn:
            return False
        if not crypto.NACL_AVAILABLE:
            self._emit_error("PyNaCl is required for secure protocol")
            self._schedule_disconnect()
            return False
        
        try:
            _, writer = self.conn
            self.my_nonce = crypto.generate_nonce()
            self.my_ephemeral_private, self.my_ephemeral_public = \
                crypto.generate_ephemeral_keypair()
            if not self.my_signing_seed or not self.my_signing_public:
                raise ValueError("Local handshake signing key is missing")
            if not self.current_peer_addr:
                raise ValueError("Peer address is unknown")
            init_nonce_hex = self.my_nonce.hex()
            init_eph_hex = self.my_ephemeral_public.hex()
            init_sign_pub_hex = self.my_signing_public.hex()
            if not self.my_dest:
                raise ValueError("Local destination is not initialized")
            init_sig_payload = self._build_init_sig_payload(
                self.my_dest.base32,
                self.current_peer_addr,
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
            self._handshake_initiated = True
            self._emit_system("Initiating secure handshake with PFS...")
            writer.write(self.frame_message_plain("H", handshake_data))
            await writer.drain()
            return True
        except Exception as e:
            logger.error(f"Handshake initiation failed: {e}")
            return False

    def _compute_session_subkeys(self, is_initiator: bool) -> Tuple[bytes, bytes]:
        """
        Вычисляет финальные subkeys для сессии.

        С PFS + key separation:
        HKDF(dh_shared, nonce_init, nonce_resp) -> (k_enc, k_mac)
        """
        if not crypto.NACL_AVAILABLE:
            raise RuntimeError("PyNaCl is required for secure protocol")
        if not self.my_ephemeral_private or not self.peer_ephemeral_public:
            raise ValueError("Missing ephemeral keys")
        if not self.my_nonce or not self.peer_nonce:
            raise ValueError("Missing handshake nonces")

        dh_shared = crypto.compute_dh_shared_secret(
            self.my_ephemeral_private, self.peer_ephemeral_public
        )
        if is_initiator:
            nonce_init = self.my_nonce
            nonce_resp = self.peer_nonce
        else:
            nonce_init = self.peer_nonce
            nonce_resp = self.my_nonce
        return crypto.derive_handshake_subkeys(dh_shared, nonce_init, nonce_resp)

    def _should_initiate_blindbox_root_exchange(self) -> bool:
        if not self._blindbox_ready() or not self.my_dest:
            return False
        peer_id = self._blindbox_peer_id()
        if not peer_id:
            return False
        local_id = self.my_dest.base32.strip().lower()
        return local_id < peer_id.strip().lower()

    def _blindbox_should_rotate_root(self) -> bool:
        if self._blindbox_root_secret is None:
            return False
        now_ts = int(time.time())
        elapsed_sec = max(0, now_ts - int(self._blindbox_root_created_at or now_ts))
        sent_since_epoch = max(
            0, int(self._blindbox_state.send_index) - int(self._blindbox_root_send_index_base)
        )
        return (
            elapsed_sec >= self._blindbox_root_rotate_seconds
            or sent_since_epoch >= self._blindbox_root_rotate_messages
        )

    def _blindbox_has_pending_root(self) -> bool:
        return (
            self._blindbox_pending_root_secret is not None
            and len(self._blindbox_pending_root_secret) == 32
            and int(self._blindbox_pending_root_epoch) > 0
        )

    def _clear_pending_blindbox_root(self) -> None:
        self._blindbox_pending_root_secret = None
        self._blindbox_pending_root_epoch = 0
        self._blindbox_pending_root_created_at = 0
        self._blindbox_pending_root_send_index_base = int(self._blindbox_state.send_index)

    def _ensure_pending_blindbox_root(
        self, *, force_rotate: bool = False
    ) -> tuple[int, bytes, str, bool] | None:
        if self._blindbox_has_pending_root():
            if self._blindbox_pending_root_secret is None:
                raise RuntimeError("BlindBox pending root invariant violated: secret is None")
            reason = (
                "initialized"
                if self._blindbox_root_secret is None
                else "rotated"
            )
            return (
                int(self._blindbox_pending_root_epoch),
                self._blindbox_pending_root_secret,
                reason,
                False,
            )
        should_bootstrap = self._blindbox_root_secret is None
        should_rotate = force_rotate or self._blindbox_should_rotate_root()
        if not should_bootstrap and not should_rotate:
            return None
        next_epoch = max(
            int(self._blindbox_root_epoch),
            int(self._blindbox_pending_root_epoch),
        ) + 1
        root_secret = os.urandom(32)
        self._blindbox_pending_root_secret = root_secret
        self._blindbox_pending_root_epoch = next_epoch
        self._blindbox_pending_root_created_at = int(time.time())
        self._blindbox_pending_root_send_index_base = int(self._blindbox_state.send_index)
        self._save_blindbox_state()
        reason = "rotated" if should_rotate and not should_bootstrap else "initialized"
        return (next_epoch, root_secret, reason, True)

    def _commit_pending_blindbox_root(self, ack_epoch: int) -> bool:
        if not self._blindbox_has_pending_root():
            return False
        if int(ack_epoch) != int(self._blindbox_pending_root_epoch):
            return False
        if self._blindbox_pending_root_secret is None:
            raise RuntimeError("BlindBox commit: pending root secret is None")
        reason = (
            "initialized"
            if self._blindbox_root_secret is None
            else "rotated"
        )
        if self._blindbox_root_secret is not None:
            expires_at = int(time.time()) + int(self._blindbox_previous_grace_seconds)
            self._blindbox_prev_roots.append(
                {
                    "epoch": int(self._blindbox_root_epoch),
                    "secret": self._blindbox_root_secret,
                    "expires_at": expires_at,
                }
            )
        self._blindbox_root_secret = self._blindbox_pending_root_secret
        self._blindbox_root_epoch = int(self._blindbox_pending_root_epoch)
        self._blindbox_root_created_at = int(self._blindbox_pending_root_created_at)
        self._blindbox_root_send_index_base = int(
            self._blindbox_pending_root_send_index_base
        )
        self._clear_pending_blindbox_root()
        self._blindbox_prune_previous_roots()
        self._save_blindbox_state()
        if reason == "initialized":
            self._emit_system("BlindBox root secret initialized")
        else:
            self._emit_system("BlindBox root secret rotated")
        return True

    async def _send_blindbox_root_ack(
        self, writer: asyncio.StreamWriter, incoming_epoch: int
    ) -> None:
        writer.write(
            self.frame_message("S", f"__SIGNAL__:BLINDBOX_ROOT_ACK|{int(incoming_epoch)}")
        )
        await writer.drain()

    async def _handle_incoming_blindbox_root_signal(
        self,
        body: str,
        writer: asyncio.StreamWriter,
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
                "BlindBox root received outside persistent locked-peer mode."
            )
            return
        if not self._blindbox_current_peer_matches_locked_peer():
            self._emit_error(
                "BlindBox root ignored: connected peer does not match locked peer."
            )
            return
        if self._blindbox_root_secret is None:
            self._blindbox_root_secret = root_secret
            self._blindbox_root_epoch = incoming_epoch
            self._blindbox_root_created_at = int(time.time())
            self._blindbox_root_send_index_base = int(self._blindbox_state.send_index)
            self._save_blindbox_state()
            self._emit_system(
                f"BlindBox root secret received (epoch={incoming_epoch})"
            )
            await self._send_blindbox_root_ack(writer, incoming_epoch)
            return
        if incoming_epoch > int(self._blindbox_root_epoch):
            expires_at = int(time.time()) + int(self._blindbox_previous_grace_seconds)
            self._blindbox_prev_roots.append(
                {
                    "epoch": int(self._blindbox_root_epoch),
                    "secret": self._blindbox_root_secret,
                    "expires_at": expires_at,
                }
            )
            self._blindbox_root_secret = root_secret
            self._blindbox_root_epoch = incoming_epoch
            self._blindbox_root_created_at = int(time.time())
            self._blindbox_root_send_index_base = int(self._blindbox_state.send_index)
            self._blindbox_prune_previous_roots()
            self._save_blindbox_state()
            self._emit_system("BlindBox root rotated")
            await self._send_blindbox_root_ack(writer, incoming_epoch)
            return
        if (
            incoming_epoch == int(self._blindbox_root_epoch)
            and self._blindbox_root_secret == root_secret
        ):
            await self._send_blindbox_root_ack(writer, incoming_epoch)
            return
        self._emit_system("Ignoring stale BlindBox root signal.")

    def _handle_blindbox_root_ack_signal(self, body: str) -> None:
        ack_raw = body.split("BLINDBOX_ROOT_ACK|", 1)[1].strip().split("|", 1)[0]
        if not ack_raw.isdigit():
            raise ValueError("invalid root ack epoch")
        ack_epoch = int(ack_raw)
        if not self._blindbox_ready():
            self._emit_error(
                "BlindBox root ACK received outside persistent locked-peer mode."
            )
            return
        if not self._blindbox_current_peer_matches_locked_peer():
            self._emit_error(
                "BlindBox root ACK ignored: connected peer does not match locked peer."
            )
            return
        if not self._commit_pending_blindbox_root(ack_epoch):
            self._emit_system("Ignoring stale BlindBox root ACK.")

    async def _send_blindbox_root_if_needed(
        self, writer: asyncio.StreamWriter, *, force_rotate: bool = False
    ) -> None:
        if not self._blindbox_ready():
            return
        if not self._blindbox_current_peer_matches_locked_peer():
            self._emit_error(
                "BlindBox root exchange blocked: connected peer does not match locked peer."
            )
            return
        if not self._should_initiate_blindbox_root_exchange():
            return
        pending_root = self._ensure_pending_blindbox_root(force_rotate=force_rotate)
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
            )
        )
        await writer.drain()
        if is_new_pending:
            self._emit_system(f"BlindBox root secret {reason}; awaiting ACK")

    async def _handle_handshake_message(
        self, body: str, writer: asyncio.StreamWriter
    ) -> None:
        """Обрабатывает входящее signed-handshake сообщение с поддержкой PFS."""
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
                if self._handshake_initiated:
                    logger.warning(
                        "Received INIT while local INIT is pending; closing to avoid handshake role conflict."
                    )
                    self._emit_error(
                        "Handshake role conflict detected; reconnecting."
                    )
                    self._schedule_disconnect()
                    return
                if not self.current_peer_addr or not self.my_dest:
                    raise ValueError("Missing peer/local address for INIT verification")
                if not self.my_signing_seed or not self.my_signing_public:
                    raise ValueError("Missing local handshake signing key")
                (
                    self.peer_nonce,
                    self.peer_ephemeral_public,
                    peer_sign_pub,
                    peer_signature,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                ) = _parse_signed_payload(body[5:])
                init_sig_payload = self._build_init_sig_payload(
                    self.current_peer_addr,
                    self.my_dest.base32,
                    init_nonce_hex,
                    init_eph_hex,
                    init_sign_pub_hex,
                )
                if not crypto.verify_signature(peer_sign_pub, init_sig_payload, peer_signature):
                    raise ValueError("INIT signature verification failed")
                if not await self._pin_or_verify_peer_signing_key(self.current_peer_addr, peer_sign_pub):
                    raise ValueError("Peer signing key does not match pinned key")
                self.peer_signing_public = peer_sign_pub

                self.my_ephemeral_private, self.my_ephemeral_public = \
                    crypto.generate_ephemeral_keypair()
                self.my_nonce = crypto.generate_nonce()

                resp_nonce_hex = self.my_nonce.hex()
                resp_eph_hex = self.my_ephemeral_public.hex()
                resp_sign_pub_hex = self.my_signing_public.hex()
                resp_sig_payload = self._build_resp_sig_payload(
                    self.my_dest.base32,
                    self.current_peer_addr,
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
                writer.write(self.frame_message_plain("H", response))
                await writer.drain()

                self.shared_key, self.shared_mac_key = self._compute_session_subkeys(
                    is_initiator=False
                )
                self.use_encryption = True
                self.handshake_complete = True
                self._handshake_initiated = False
                self._recv_seq = 0
                self._send_seq = 0
                self._cancel_handshake_watchdog()
                self._emit_message("info", "Secure channel with PFS established")
                self._emit_system("✔ Ready! You can now send messages.")
                self._trigger_blindbox_hot_poll("peer-online")
                await self._send_blindbox_root_if_needed(writer)
                logger.info("Handshake completed (responder)")

            elif body.startswith("RESP:"):
                if (
                    not self._handshake_initiated
                    or self.my_nonce is None
                    or self.my_ephemeral_public is None
                ):
                    logger.warning("Received RESP without prior INIT")
                    self._schedule_disconnect()
                    return
                if not self.current_peer_addr or not self.my_dest:
                    raise ValueError("Missing peer/local address for RESP verification")
                if not self.my_signing_public:
                    raise ValueError("Missing local handshake signing public key")

                (
                    self.peer_nonce,
                    self.peer_ephemeral_public,
                    peer_sign_pub,
                    peer_signature,
                    resp_nonce_hex,
                    resp_eph_hex,
                    resp_sign_pub_hex,
                ) = _parse_signed_payload(body[5:])
                init_nonce_hex = self.my_nonce.hex()
                init_eph_hex = self.my_ephemeral_public.hex()
                init_sign_pub_hex = self.my_signing_public.hex()
                resp_sig_payload = self._build_resp_sig_payload(
                    self.current_peer_addr,
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
                if not await self._pin_or_verify_peer_signing_key(self.current_peer_addr, peer_sign_pub):
                    raise ValueError("Peer signing key does not match pinned key")
                self.peer_signing_public = peer_sign_pub
                self.shared_key, self.shared_mac_key = self._compute_session_subkeys(
                    is_initiator=True
                )
                self.use_encryption = True
                self.handshake_complete = True
                self._handshake_initiated = False
                self._recv_seq = 0
                self._send_seq = 0
                self._cancel_handshake_watchdog()
                self._emit_message("info", "Secure channel with PFS established")
                self._emit_system("✔ Ready! You can now send messages.")
                self._trigger_blindbox_hot_poll("peer-online")
                await self._send_blindbox_root_if_needed(writer)
                logger.info("Handshake completed (initiator)")

            else:
                logger.warning(f"Unknown handshake message: {body[:20]}")
                
        except Exception as e:
            logger.error(f"Handshake error: {e}")
            self._emit_error(f"Secure handshake failed: {e}")
            self._schedule_disconnect()

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
        self._handshake_watchdog_generation += 1
        self._outbound_connect_busy = False
        if self.conn:
            await self.disconnect()

        tasks_to_cancel: list[asyncio.Task[Any]] = []
        for attr in (
            "_accept_task",
            "_tunnel_task",
            "_keepalive_task",
            "_handshake_watchdog_task",
            "_disconnect_task",
            "_blindbox_task",
        ):
            task = getattr(self, attr)
            if task is not None and not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
            setattr(self, attr, None)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        if self._session_socket:
            try:
                _, writer = self._session_socket
                writer.close()
            except Exception:
                pass
            self._session_socket = None
        if self._blindbox_client is not None:
            try:
                await self._blindbox_client.close()
            except Exception:
                pass
            self._blindbox_client = None

    # ---------- фоновые циклы ----------

    async def accept_loop(self) -> None:
        while True:
            if not crypto.NACL_AVAILABLE:
                self._emit_error("PyNaCl is required for secure protocol")
                await asyncio.sleep(5)
                continue
            if self.conn:
                await asyncio.sleep(1)
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
                    peer_addr = i2plib.Destination(raw_dest).base32 + ".b32.i2p"

                    if self.stored_peer and peer_addr != self.stored_peer:
                        self._emit_error(
                            f"Blocked unauthorized call from {peer_addr}..."
                        )
                        writer.close()
                        continue

                    verified = await self._set_verified_peer_identity(
                        peer_addr, raw_dest, source="preface"
                    )
                    if not verified:
                        writer.close()
                        continue
                    self.peer_b32 = peer_addr
                    self._emit_message(
                        "info", f"Connection accepted from {peer_addr[:12]}..."
                    )
                    # Отдельное событие для системного уведомления о входящем подключении.
                    self._emit_notify("connect", peer_addr)
                    self._emit_peer_changed(peer_addr)
                except Exception as e:
                    self._emit_error(f"Rejected incoming connection: invalid identity preface ({e})")
                    writer.close()
                    continue

                if self.my_dest is not None:
                    writer.write(self.frame_message("S", self.my_dest.base64))
                    await writer.drain()

                self.conn = (reader, writer)
                self._activate_ack_session()

                loop = asyncio.get_running_loop()
                loop.create_task(self.receive_loop(self.conn))
                self._start_handshake_watchdog(self.conn)
                self._keepalive_task = loop.create_task(self._keepalive_loop())
            except Exception:
                await asyncio.sleep(1)

    async def receive_loop(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        initial_type: Optional[str] = None,
    ) -> None:
        # Предотвращаем запуск дублирующего receive_loop
        if self._recv_loop_active:
            return
        self._recv_loop_active = True
        
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
                        if self.handshake_complete:
                            logger.warning(
                                "Protocol framing violation after handshake: %s",
                                e,
                            )
                            self._emit_error("Protocol downgrade detected")
                            self._schedule_disconnect()
                            break
                        raise
                    except asyncio.TimeoutError:
                        if self._file_transfer_active:
                            # Исходящая передача файла/картинки: цикл приёма не глушим — иначе до конца
                            # отправки не обрабатываются входящие U/P/сигналы собеседника.
                            continue
                        if self.incoming_info is not None:
                            restart_after_timeout = True
                            return
                        # Приём inline-картинки (G): как для F/D — не рвём сессию из-за долгой паузы I2P
                        if self.inline_image_info is not None:
                            restart_after_timeout = True
                            return
                        if self.conn == connection:
                            self._emit_error("Connection timed out (no data received)")
                        return
                    msg_type = frame.msg_type
                    msg_id = frame.msg_id
                    body_data = frame.payload
                    is_encrypted = bool(frame.flags & FLAG_ENCRYPTED)

                if self.handshake_complete and msg_type == "H":
                    logger.warning("Unexpected handshake frame after secure channel established")
                    self._emit_error("Protocol violation: unexpected handshake frame")
                    self._schedule_disconnect()
                    break
                if not self.handshake_complete and msg_type not in ["S", "H", "P", "O"]:
                    logger.warning(
                        "Protocol violation: non-handshake frame before secure channel "
                        "(msg_type=%r)",
                        msg_type,
                    )
                    self._emit_error("Protocol violation: data before secure handshake")
                    self._schedule_disconnect()
                    break
                seq_num: Optional[int] = None
                if is_encrypted:
                    if not self.shared_key or not self.use_encryption:
                        logger.warning("Encrypted frame received before key setup")
                        self._emit_error("Protocol error: encrypted frame before handshake")
                        self._schedule_disconnect()
                        break
                    if len(body_data) < ENCRYPTED_TRAILER_SIZE:
                        logger.warning("Encrypted payload is too short")
                        self._emit_error("Protocol error: encrypted payload too short")
                        self._schedule_disconnect()
                        break
                    seq_num = int.from_bytes(body_data[:8], "big", signed=False)
                    encrypted_body = body_data[8:-crypto.HMAC_SIZE]
                    received_mac = body_data[-crypto.HMAC_SIZE:]
                    if len(encrypted_body) == 0:
                        logger.warning("Encrypted body is empty")
                        break
                    mac_key = self.shared_mac_key or self.shared_key
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
                            "(msg_type=%r body_len=%d)", msg_type, len(body_data)
                        )
                        self._emit_error("Message integrity check failed")
                        self._schedule_disconnect()
                        break
                    expected_seq = self._recv_seq + 1
                    if seq_num != expected_seq:
                        logger.warning(
                            "Replay/out-of-order frame detected: got=%d expected=%d",
                            seq_num,
                            expected_seq,
                        )
                        self._emit_error("Replay protection triggered")
                        self._schedule_disconnect()
                        break

                    decrypted = crypto.decrypt_message(self.shared_key, encrypted_body)
                    if decrypted is None:
                        logger.warning("Decryption failed")
                        self._emit_error("Failed to decrypt message")
                        break
                    try:
                        body_data = self._remove_padding_profile(decrypted)
                    except ValueError as e:
                        logger.warning("Padded payload parse failed: %s", e)
                        self._emit_error("Protocol error: malformed padded payload")
                        self._schedule_disconnect()
                        break
                    self._recv_seq = seq_num
                elif self.handshake_complete:
                    logger.warning(
                        "Protocol downgrade detected: plaintext frame after handshake "
                        "(msg_type=%r)",
                        msg_type,
                    )
                    self._emit_error("Protocol downgrade detected")
                    self._schedule_disconnect()
                    break

                body = body_data.decode("utf-8")

                if msg_type == "U":
                    sp = self.current_peer_addr
                    self._emit_message("peer", body, source_peer=sp)
                    self._emit_notify("peer", body, source_peer=sp)
                    # Подтверждение доставки по MSG_ID (vNext)
                    if msg_id:
                        try:
                            await self._write_signal_frame_maybe_soft_drain(
                                writer,
                                self.frame_message(
                                    "S", f"__SIGNAL__:MSG_ACK|{msg_id}"
                                ),
                            )
                        except Exception:
                            pass

                elif msg_type == "I":
                    if body == "__END__":
                        img_text = "\n".join(self.image_buffer)
                        self.image_buffer = []
                        if self.on_image_received:
                            self.on_image_received(img_text)
                        else:
                            self._emit_message(
                                "peer", img_text, source_peer=self.current_peer_addr
                            )
                    else:
                        if len(self.image_buffer) < self.MAX_IMAGE_LINES:
                            self.image_buffer.append(body)
                        elif len(self.image_buffer) == self.MAX_IMAGE_LINES:
                            self.image_buffer.append("[Image truncated - too large]")
                            self._emit_error("Image too large, truncating")

                elif msg_type == "G":
                    # Inline image (binary PNG / JPEG / WebP)
                    if body == "__IMG_END__":
                        # Завершение приёма изображения
                        if self.inline_image_info and self.inline_image_buffer:
                            filename, expected_size = self.inline_image_info
                            actual_size = len(self.inline_image_buffer)
                            
                            # Проверяем размер
                            if actual_size > MAX_IMAGE_SIZE:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image too large, discarding")
                                self.inline_image_buffer = bytearray()
                                self.inline_image_info = None
                                self._incoming_image_msg_id = None
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
                                self.inline_image_buffer = bytearray()
                                self.inline_image_info = None
                                self._incoming_image_msg_id = None
                                continue
                            
                            # Проверяем magic bytes
                            header = bytes(self.inline_image_buffer[:12])
                            detected_ext = detect_inline_image_format(header)
                            if detected_ext is None:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image has invalid format")
                                self.inline_image_buffer = bytearray()
                                self.inline_image_info = None
                                self._incoming_image_msg_id = None
                                continue
                            
                            # Сохраняем и валидируем в thread pool (hash/PIL не блокируют qasync/Qt)
                            images_dir = get_images_dir()
                            payload = bytes(self.inline_image_buffer)
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
                                        ack_id = self._incoming_image_msg_id or 0
                                        await self._write_signal_frame_maybe_soft_drain(
                                            writer,
                                            self.frame_message(
                                                "S",
                                                f"__SIGNAL__:IMG_ACK|{filename}|{ack_id}",
                                            ),
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
                            
                            self.inline_image_buffer = bytearray()
                            self.inline_image_info = None
                            self._incoming_image_msg_id = None
                    elif self.inline_image_info is None:
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
                                    self.inline_image_info = (filename, size)
                                    self._incoming_image_msg_id = msg_id or None
                                    self.inline_image_buffer = bytearray()
                                    self._inline_image_last_emit = 0
                                    self._emit_system(f"Receiving image: {filename} ({size} bytes)")
                                    self._emit_file_event(FileTransferInfo(filename=filename, size=size, received=0, is_sending=False, is_inline_image=True))
                        except Exception as e:
                            self._emit_error(f"Invalid image header: {e}")
                    else:
                        # Данные изображения (base64)
                        try:
                            fn, total = self.inline_image_info
                            remaining = total - len(self.inline_image_buffer)
                            if remaining <= 0:
                                raise ValueError("Image chunk exceeds declared size")
                            if len(body) > max_base64_chars_for_bytes(remaining):
                                raise ValueError("Image chunk is too large for remaining size")
                            chunk = base64.b64decode(body, validate=True)
                            if len(chunk) > remaining:
                                raise ValueError("Decoded image chunk exceeds remaining size")
                            self.inline_image_buffer.extend(chunk)
                            if self.inline_image_info:
                                received = len(self.inline_image_buffer)
                                if received - getattr(self, "_inline_image_last_emit", 0) >= 65536 or received == total:
                                    self._inline_image_last_emit = received
                                    self._emit_file_event(FileTransferInfo(filename=fn, size=total, received=received, is_sending=False, is_inline_image=True))
                        except Exception as e:
                            self._emit_error(f"Image data error: {e}")
                            if self.inline_image_info:
                                fn, sz = self.inline_image_info
                                self._emit_file_event(FileTransferInfo(filename=fn, size=sz, received=-1, is_sending=False, is_inline_image=True))
                            self.inline_image_buffer = bytearray()
                            self.inline_image_info = None
                            self._incoming_image_msg_id = None

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
                            self.incoming_file = None
                            self.incoming_info = None
                            continue
                        safe_name = os.path.basename(filename)
                        safe_path = allocate_unique_filename(get_downloads_dir(), safe_name)
                        final_name = os.path.basename(safe_path)
                        accepted = await self._request_file_offer_decision(
                            final_name, size
                        )
                        if not accepted:
                            await self.reject_incoming_file(final_name)
                            self._emit_system(
                                f"Incoming file rejected by user: {final_name}"
                            )
                            self.incoming_file = None
                            self.incoming_info = None
                            continue
                        if final_name != safe_name:
                            self._emit_system(
                                f"Filename collision detected: saved as {final_name}"
                            )
                        self.incoming_file = open(safe_path, "xb")
                        self._incoming_file_msg_id = msg_id or None
                        self.incoming_info = FileTransferInfo(
                            filename=safe_path, size=size, received=0
                        )
                        self._file_xfer_debug_last_recv_emit_mono = None
                        self._emit_system(
                            f"Receiving file: {final_name} ({size} bytes)"
                        )
                        self._emit_file_event(self.incoming_info)
                    except Exception as e:
                        self._emit_error(f"Invalid file header: {e}")

                elif msg_type == "D":
                    try:
                        if self.incoming_file and self.incoming_info:
                            remaining = self.incoming_info.size - self.incoming_info.received
                            if remaining <= 0:
                                raise ValueError("File chunk exceeds declared size")
                            if len(body) > max_base64_chars_for_bytes(remaining):
                                raise ValueError("File chunk is too large for remaining size")
                            chunk = base64.b64decode(body, validate=True)
                            if len(chunk) > remaining:
                                raise ValueError("Decoded file chunk exceeds remaining size")
                            self.incoming_file.write(chunk)
                            self.incoming_info.received += len(chunk)
                            rcv = self.incoming_info.received
                            tot = self.incoming_info.size
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
                                self._emit_file_event(self.incoming_info)
                    except Exception as e:
                        self._emit_error(f"File chunk error: {e}")
                        if self.incoming_file:
                            try:
                                self.incoming_file.close()
                            except Exception:
                                pass
                        if self.incoming_info:
                            self._emit_file_event(
                                FileTransferInfo(
                                    filename=self.incoming_info.filename,
                                    size=self.incoming_info.size,
                                    received=-1,
                                    is_sending=False,
                                )
                            )
                            try:
                                os.remove(self.incoming_info.filename)
                            except OSError:
                                pass
                        self.incoming_file = None
                        self.incoming_info = None
                        self._incoming_file_msg_id = None

                elif msg_type == "E":
                    if self.incoming_file and self.incoming_info:
                        ack_filename = self.incoming_info.filename
                        expected_size = self.incoming_info.size
                        received_size = self.incoming_info.received
                        try:
                            self.incoming_file.close()
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
                            self.incoming_file = None
                            self.incoming_info = None
                            self._incoming_file_msg_id = None
                            continue
                        ack_msg_id = self._incoming_file_msg_id or 0
                        self._emit_file_event(
                            FileTransferInfo(
                                filename=ack_filename,
                                size=expected_size,
                                received=expected_size,
                                is_sending=False,
                            )
                        )
                        self.incoming_file = None
                        self.incoming_info = None
                        # Подтверждение получения файла (галочки у отправителя); отправляем basename, чтобы совпало с file_name у отправителя
                        try:
                            writer.write(
                                self.frame_message(
                                    "S",
                                    f"__SIGNAL__:FILE_ACK|{os.path.basename(ack_filename)}|{ack_msg_id}",
                                )
                            )
                            await writer.drain()
                        except Exception:
                            pass
                        self._incoming_file_msg_id = None

                elif msg_type == "S":
                    if "__SIGNAL__:" in body:
                        if "BLINDBOX_ROOT|" in body:
                            try:
                                await self._handle_incoming_blindbox_root_signal(
                                    body, writer
                                )
                            except Exception as e:
                                self._emit_error(f"Invalid BlindBox root signal: {e}")
                        elif "BLINDBOX_ROOT_ACK|" in body:
                            try:
                                self._handle_blindbox_root_ack_signal(body)
                            except Exception as e:
                                self._emit_error(
                                    f"Invalid BlindBox root ACK signal: {e}"
                                )
                        elif "MSG_ACK|" in body:
                            try:
                                ack_id_raw = body.split("MSG_ACK|", 1)[1].strip().split("|", 1)[0]
                                ack_id = int(ack_id_raw)
                                entry = self._pending_text_acks.get(ack_id)
                                if entry is None:
                                    self._record_ack_drop("unknown_id", f"MSG_ACK id={ack_id}")
                                elif entry.state != "awaiting_ack":
                                    self._record_ack_drop(
                                        "expired_or_state",
                                        f"MSG_ACK id={ack_id} state={entry.state}",
                                    )
                                elif (
                                    entry.ack_kind != "msg"
                                    or entry.peer_addr != self._current_ack_peer()
                                    or entry.ack_session_epoch != self._ack_session_epoch
                                ):
                                    self._record_ack_drop(
                                        "context_mismatch",
                                        f"MSG_ACK id={ack_id}",
                                    )
                                else:
                                    if self.on_text_delivered:
                                        self.on_text_delivered(str(ack_id))
                                    self._pending_text_acks.pop(ack_id, None)
                            except Exception:
                                self._record_ack_drop("invalid_format", "MSG_ACK parse failed")
                        if "IMG_ACK|" in body:
                            try:
                                ack_payload = body.split("IMG_ACK|", 1)[1].strip()
                                parts = ack_payload.split("|")
                                ack_filename = parts[0].strip()
                                ack_valid = False
                                if len(parts) > 1:
                                    try:
                                        ack_id = int(parts[1].strip())
                                        entry = self._pending_image_acks.get(ack_id)
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
                                            and entry.peer_addr == self._current_ack_peer()
                                            and entry.ack_session_epoch == self._ack_session_epoch
                                        ):
                                            self._pending_image_acks.pop(ack_id, None)
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
                                        entry = self._pending_file_acks.get(ack_id)
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
                                            and entry.peer_addr == self._current_ack_peer()
                                            and entry.ack_session_epoch == self._ack_session_epoch
                                        ):
                                            self._pending_file_acks.pop(ack_id, None)
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
                            self._transfer_rejected_by_peer = True
                        elif "QUIT" in body:
                            self._emit_system("Peer requested disconnect.")
                            break
                        elif "ABORT_FILE" in body:
                            self._transfer_aborted_by_peer = True
                            if self.incoming_file and self.incoming_info:
                                try:
                                    self.incoming_file.close()
                                except Exception:
                                    pass
                                self.incoming_file = None
                                self._emit_file_event(FileTransferInfo(
                                    filename=self.incoming_info.filename,
                                    size=self.incoming_info.size,
                                    received=-1,
                                    is_sending=False,
                                ))
                                self.incoming_info = None
                                self._emit_system("Sender cancelled the transfer")
                            continue
                    else:
                        try:
                            dest_obj = i2plib.Destination(body)
                            new_peer = dest_obj.base32 + ".b32.i2p"
                            if self.current_peer_addr and new_peer != self.current_peer_addr:
                                self._emit_error(
                                    f"Blocked identity mismatch: expected {self.current_peer_addr[:16]}..., got {new_peer[:16]}..."
                                )
                                break
                            if self.stored_peer and new_peer != self.stored_peer:
                                self._emit_error(
                                    f"Blocked identity spoof: {new_peer[:16]}..."
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
                    await self._handle_handshake_message(body, writer)

                elif msg_type == "P":
                    writer.write(self.frame_message("O", ""))
                    await writer.drain()

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            if self.conn == connection:
                self._emit_error(f"Protocol Error: {e}")
        finally:
            self._recv_loop_active = False
            skip_cleanup = False
            if (
                restart_after_timeout
                and self.conn == connection
                and (
                    self.incoming_info is not None
                    or self.inline_image_info is not None
                )
                and not self._file_transfer_active
            ):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.receive_loop(connection))
                except RuntimeError:
                    pass
                skip_cleanup = True
            # Не сбрасываем соединение если идёт передача или приём файла / inline-изображения
            if (
                not skip_cleanup
                and self.conn == connection
                and not self._file_transfer_active
                and self.incoming_info is None
                and self.inline_image_info is None
            ):
                self._cancel_handshake_watchdog()
                if self._keepalive_task:
                    self._keepalive_task.cancel()
                    self._keepalive_task = None
                self.conn = None
                self._reset_crypto_state()
                self._emit_message("info", "Peer disconnected.")
                self.peer_b32 = "Waiting for incoming connections..."
                self._emit_system("Waiting for incoming connections...")
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
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
            except asyncio.TimeoutError:
                pass
            except Exception:
                # Keep current network status on transient lookup errors.
                pass

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
