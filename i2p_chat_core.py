import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional, Tuple

import i2plib
from PIL import Image

import crypto
from protocol_codec import (
    ENCRYPTED_TRAILER_SIZE,
    FLAG_ENCRYPTED,
    ProtocolCodec,
)

logger = logging.getLogger("i2pchat")
PROTOCOL_VERSION = 4


@dataclass
class ChatMessage:
    kind: str  # "me", "peer", "system", "info", "error", "success", "disconnect", "help"
    text: str
    timestamp: datetime


@dataclass
class FileTransferInfo:
    filename: str
    size: int
    received: int = 0
    is_sending: bool = False
    is_inline_image: bool = False  # True только для Send Pic (G), не для Send File (F/D)
    rejected_by_peer: bool = False  # True если получатель отклонил входящий файл


@dataclass
class PendingAckEntry:
    token: str
    ack_kind: str
    created_at: float
    peer_addr: str
    ack_session_epoch: int
    state: str = "awaiting_ack"


StatusCallback = Callable[[str], Any]
MessageCallback = Callable[[ChatMessage], Any]
PeerChangedCallback = Callable[[Optional[str]], Any]
FileEventCallback = Callable[[FileTransferInfo], Any]
SimpleCallback = Callable[[str], Any]
TrustDecisionCallback = Callable[[str, str, str], bool]


def get_profiles_dir() -> str:
    """
    Директория для .dat профилей. На macOS — Application Support (надёжный доступ),
    на Windows — APPDATA, иначе ~/.i2pchat. Создаёт каталог и на Unix выставляет 0o700.
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


UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Безопасные форматы изображений (magic bytes -> extension)
ALLOWED_IMAGE_FORMATS = {
    b'\x89PNG\r\n\x1a\n': 'png',
    b'\xff\xd8\xff': 'jpeg',
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 4096  # max width/height in pixels
MAX_IMAGES_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB auto-cleanup threshold


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
    
    detected_ext = None
    
    # Check PNG (8 bytes magic)
    if header[:8] == b'\x89PNG\r\n\x1a\n':
        detected_ext = 'png'
    # Check JPEG (3 bytes magic)
    elif header[:3] == b'\xff\xd8\xff':
        detected_ext = 'jpeg'
    if detected_ext is None:
        return False, "Unsupported image format (only PNG and JPEG allowed)", None
    
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
            logger.info(f"Cleaned up old image: {path}")
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
        on_image_received: Optional[Callable[[str], Any]] = None,
        on_inline_image_received: Optional[Callable[..., Any]] = None,
        on_image_delivered: Optional[Callable[[str], Any]] = None,
        on_file_delivered: Optional[Callable[[str], Any]] = None,
        on_trust_decision: Optional[TrustDecisionCallback] = None,
        legacy_compat: bool = False,
    ) -> None:
        self.sam_address = sam_address
        self.profile = profile or "default"

        self.on_status = on_status
        self.on_message = on_message
        self.on_peer_changed = on_peer_changed
        self.on_system = on_system
        self.on_error = on_error
        self.on_file_event = on_file_event
        self.on_image_received = on_image_received
        self.on_inline_image_received = on_inline_image_received
        self.on_image_delivered = on_image_delivered
        self.on_file_delivered = on_file_delivered
        self.on_trust_decision = on_trust_decision
        self.legacy_compat = legacy_compat

        self.session_id = f"chat_{self.profile}_{int(time.time())}"
        self.network_status = "initializing"
        self.peer_b32: str = "Waiting for incoming connections..."

        self.my_dest: Optional[i2plib.Destination] = None
        self.stored_peer: Optional[str] = None
        self.current_peer_addr: Optional[str] = None
        self.conn: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
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
        # Флаг отмены передачи (локальная отмена пользователем)
        self._cancel_transfer: bool = False
        # Получен сигнал ABORT_FILE от пира — отменить текущую отправку
        self._transfer_aborted_by_peer: bool = False
        # Получен сигнал REJECT_FILE — получатель отклонил входящий файл
        self._transfer_rejected_by_peer: bool = False
        # Флаг активного receive_loop (предотвращает запуск дублирующих корутин)
        self._recv_loop_active: bool = False
        self._codec = ProtocolCodec(
            allowed_types={"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"},
            max_frame_body=self.MAX_FRAME_BODY,
            # Keep strict vNext by default; legacy mode should be explicit
            # and only re-enabled with full negotiation support.
            allow_legacy=False,
        )

    # ---------- вспомогательные уведомления ----------

    def _emit_status(self, status: str) -> None:
        self.network_status = status
        if self.on_status:
            self.on_status(status)

    def _emit_message(self, kind: str, text: str) -> None:
        if self.on_message:
            msg = ChatMessage(kind=kind, text=text, timestamp=datetime.now(timezone.utc))
            self.on_message(msg)

    def _emit_notify(self, kind: str, text: str) -> None:
        """
        Уведомление UI о новом сообщении для системных нотификаций.

        Отдельный слой, чтобы ядро не зависело от конкретной реализации уведомлений.
        """
        callback = getattr(self, "on_notify", None)
        if callback is not None:
            try:
                callback(ChatMessage(kind=kind, text=text, timestamp=datetime.now(timezone.utc)))
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
            encrypted_body = crypto.encrypt_message(self.shared_key, body)
            mac = crypto.compute_mac(self.shared_key, msg_type, encrypted_body, seq=seq)
            payload = seq.to_bytes(8, "big", signed=False) + encrypted_body + mac
            return (
                self._codec.encode(
                    msg_type, payload, msg_id=msg_id, flags=FLAG_ENCRYPTED
                ),
                msg_id,
            )

        return self._codec.encode(msg_type, body, msg_id=msg_id, flags=0), msg_id

    def frame_message(self, msg_type: str, content: str) -> bytes:
        frame, _ = self.frame_message_with_id(msg_type, content)
        return frame

    def frame_message_plain(self, msg_type: str, content: str) -> bytes:
        """Формирует незашифрованный фрейм (handshake/control)."""
        frame, _ = self.frame_message_with_id(msg_type, content, force_plain=True)
        return frame

    # ---------- инициализация сессии ----------

    def _profile_path(self) -> str:
        """Полный путь к .dat файлу профиля (общая директория профилей)."""
        return os.path.join(get_profiles_dir(), f"{self.profile}.dat")

    def _trust_store_path(self) -> str:
        """Файл TOFU pinning для handshake signing keys."""
        return os.path.join(get_profiles_dir(), f"{self.profile}.trust.json")

    def _load_trust_store(self) -> None:
        """Загружает pinning-таблицу peer_addr -> signing_pub_hex."""
        self.peer_trusted_signing_keys = {}
        if self.profile == "default":
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
        if self.profile == "default":
            return
        path = self._trust_store_path()
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.peer_trusted_signing_keys, f, ensure_ascii=True, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception as e:
            logger.warning("Failed to save trust store %s: %s", path, e)

    @staticmethod
    def _fingerprint_pubkey(pubkey: bytes) -> str:
        import hashlib

        return hashlib.sha256(pubkey).hexdigest()[:16]

    def _normalize_peer_addr(self, addr: str) -> str:
        raw = (addr or "").strip().lower()
        if raw and not raw.endswith(".b32.i2p"):
            raw += ".b32.i2p"
        return raw

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
        if self.profile == "default":
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
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def save_stored_peer(self, peer_addr: str) -> None:
        """
        Сохраняет lock-пир в профиль без дублирования строк.

        Форматы, которые поддерживаем:
        - line1=private_key, line2=stored_peer
        - line1=stored_peer (когда identity хранится в keyring)
        """
        if self.profile == "default":
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

    async def _request_trust_decision(
        self, peer_addr: str, fingerprint: str, signing_key_hex: str
    ) -> bool:
        """Запрашивает TOFU-решение у UI без блокировки активной корутины."""
        if self.on_trust_decision is None:
            return False
        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[bool] = loop.create_future()

        def _ask_user() -> None:
            try:
                approved = bool(self.on_trust_decision(peer_addr, fingerprint, signing_key_hex))
            except Exception as e:
                logger.warning("TOFU trust callback failed: %s", e)
                approved = False
            if not decision_future.done():
                decision_future.set_result(approved)

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
            self.peer_trusted_signing_keys[peer_addr] = current_hex
            self._save_trust_store()
            self._emit_system(
                f"TOFU: pinned peer signing key {fp} for {peer_addr[:20]}..."
            )
            self._emit_system(
                "Verify peer fingerprint out-of-band to mitigate first-contact MITM."
            )
            return True
        if pinned_hex != current_hex:
            self._emit_error(
                f"Peer signing key mismatch for {peer_addr[:20]}... "
                f"(expected {pinned_hex[:16]}, got {current_hex[:16]})"
            )
            return False
        return True

    def _ensure_local_signing_key(self) -> None:
        """Гарантирует наличие стабильного Ed25519 ключа подписи handshake."""
        if not crypto.NACL_AVAILABLE:
            raise RuntimeError("PyNaCl is required for handshake signing")

        if self.profile == "default":
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

        path = os.path.join(get_profiles_dir(), f"{self.profile}.signing")
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
            with open(path, "w", encoding="utf-8") as f:
                f.write(seed.hex())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

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
        self._emit_status("initializing")
        self._emit_system(f"Initializing Profile: {self.profile}")

        key_file = self._profile_path()
        is_persistent = self.profile != "default"

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
                    self._emit_message("success", "Identity saved to secure keyring")
                else:
                    self._emit_message("success", f"Identity saved to {key_file}")

        self.my_dest = dest
        if is_persistent:
            self._write_profile_dat(self.my_dest.private_key.base64, self.stored_peer)
        self._load_trust_store()
        self._ensure_local_signing_key()

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
        )

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

    async def connect_to_peer(self, target_address: str) -> None:
        if not crypto.NACL_AVAILABLE:
            detail = getattr(crypto, "NACL_IMPORT_ERROR", "") or "pynacl not installed"
            self._emit_error(f"Secure protocol requires PyNaCl. Install: pip install pynacl. ({detail})")
            return
        try:
            self.current_peer_addr = target_address
            self._reset_crypto_state()
            self._emit_system(
                f"Connecting to {target_address[:24]}... "
                "(may take 1–2 min while I2P builds tunnels)"
            )
            reader, writer = await asyncio.wait_for(
                i2plib.stream_connect(
                    self.session_id, target_address, sam_address=self.sam_address
                ),
                timeout=self.CONNECT_TIMEOUT,
            )

            if self.my_dest is not None:
                # Backward-safe identity preface for accept_loop(reader.readline()).
                writer.write(self.my_dest.base64.encode("utf-8") + b"\n")
                writer.write(self.frame_message("S", self.my_dest.base64))
                await writer.drain()

                self.proven = True
                self._emit_status("visible")

            self.conn = (reader, writer)
            self._activate_ack_session()
            self._emit_message("success", "Handshake sent. Establishing secure channel... Wait")

            loop = asyncio.get_running_loop()
            loop.create_task(self.receive_loop(self.conn))
            loop.create_task(self.initiate_secure_handshake())
            self._start_handshake_watchdog(self.conn)
            self._keepalive_task = loop.create_task(self._keepalive_loop())
        except asyncio.TimeoutError:
            self._emit_error(
                "Connection timed out. Check: I2P router running, peer address correct, peer online."
            )
            self.conn = None
            self._emit_system("Waiting for incoming connections...")
        except Exception as e:
            self._emit_error(f"Connection failed: {e}")
            self.conn = None
            self._emit_system("Waiting for incoming connections...")

    async def send_text(self, text: str) -> None:
        if not self._require_secure_channel():
            return
        try:
            _, writer = self.conn
            frame, msg_id = self.frame_message_with_id("U", text)
            writer.write(frame)
            await writer.drain()
            self._register_pending_ack(
                self._pending_text_acks,
                msg_id,
                token=text[:128],
                ack_kind="msg",
            )
            self._emit_message("me", text)
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            self.conn = None

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

            info = FileTransferInfo(filename=filename, size=filesize, received=0, is_sending=True)
            self._emit_file_event(info)

            sent = 0
            with open(path, "rb") as f:
                while True:
                    if self._cancel_transfer:
                        await self._send_abort_file()
                        raise Exception("Transfer cancelled by user")
                    if self._transfer_aborted_by_peer:
                        self._emit_system("Receiver cancelled the transfer")
                        raise Exception("Transfer cancelled by receiver")
                    if self._transfer_rejected_by_peer:
                        raise Exception("Receiver rejected the file")
                    if not self.conn:
                        raise ConnectionError("Connection lost during transfer")
                    
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    
                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("D", encoded))
                    await writer.drain()
                    
                    sent += len(chunk)
                    # Эмитить прогресс: после первого чанка (чтобы виджет успел появиться), затем по шагу
                    step = 4096 if filesize <= 65536 else 65536
                    first_chunk_done = sent <= 4096 and sent > 0
                    if first_chunk_done or sent % step < len(chunk) or sent == filesize:
                        info = FileTransferInfo(filename=filename, size=filesize, received=sent, is_sending=True)
                        self._emit_file_event(info)

            writer.write(self.frame_message("E", ""))
            await writer.drain()

            info = FileTransferInfo(filename=filename, size=filesize, received=filesize, is_sending=True)
            self._emit_file_event(info)
            
            # Перезапуск receive_loop если он был прерван timeout'ом во время передачи
            if self.conn:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.receive_loop(self.conn))
                except RuntimeError:
                    pass
            
        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError) as e:
            info = FileTransferInfo(filename=filename, size=filesize, received=-1, is_sending=True)
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
            )
            self._emit_file_event(info)
            if rejected:
                self._emit_error("Receiver rejected the file.")
            else:
                self._emit_error(f"File transfer failed: {e}")
        finally:
            self._file_transfer_active = False

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
            file_hash = hashlib.md5(f.read()).hexdigest()[:8]
        
        local_filename = f"img_{int(time.time())}_{file_hash}.{detected_ext}"
        local_path = os.path.join(get_images_dir(), local_filename)
        
        try:
            import shutil
            shutil.copy2(path, local_path)
        except Exception as e:
            self._emit_error(f"Failed to copy image: {e}")
            return None
        
        self._file_transfer_active = True
        self._cancel_transfer = False
        
        try:
            reader, writer = self.conn
            self._emit_system(f"Sending image: {filename} ({filesize} bytes)")
            
            # Прогресс загрузки в UI (is_inline_image — чтобы GUI заменил виджет на превью, не «File sent»)
            self._emit_file_event(FileTransferInfo(filename=filename, size=filesize, received=0, is_sending=True, is_inline_image=True))
            
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
            
            # Отправляем данные чанками в base64
            sent = 0
            last_emit_sent = 0
            with open(path, "rb") as f:
                while True:
                    if self._cancel_transfer:
                        raise Exception("Transfer cancelled by user")
                    if not self.conn:
                        raise ConnectionError("Connection lost during transfer")
                    
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    
                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("G", encoded))
                    await writer.drain()
                    
                    sent += len(chunk)
                    if sent - last_emit_sent >= 65536:
                        self._emit_file_event(FileTransferInfo(filename=filename, size=filesize, received=sent, is_sending=True, is_inline_image=True))
                        last_emit_sent = sent
            
            # Отправляем маркер завершения
            writer.write(self.frame_message("G", "__IMG_END__"))
            await writer.drain()
            
            self._emit_file_event(FileTransferInfo(filename=filename, size=filesize, received=filesize, is_sending=True, is_inline_image=True))
            
            # Уведомляем UI об отправленном изображении (filename для галочки доставки)
            self._emit_inline_image(local_path, is_from_me=True, sent_filename=filename)
            
            # Очистка кэша при необходимости
            cleanup_images_cache()
            
            return local_path
            
        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError) as e:
            self._emit_file_event(FileTransferInfo(filename=filename, size=filesize, received=-1, is_sending=True, is_inline_image=True))
            self._emit_error(f"Image transfer interrupted: connection lost")
            return None
            
        except Exception as e:
            self._emit_file_event(FileTransferInfo(filename=filename, size=filesize, received=-1, is_sending=True, is_inline_image=True))
            self._emit_error(f"Image transfer failed: {e}")
            return None
        finally:
            self._file_transfer_active = False

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
            self._emit_message("disconnect", "You disconnected.")
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

    def _compute_final_shared_key(self, is_initiator: bool) -> bytes:
        """
        Вычисляет финальный shared_key.
        
        С PFS: SHA256(DH_shared || nonce_init || nonce_resp)
        """
        import hashlib
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
            return hashlib.sha256(
                dh_shared + self.my_nonce + self.peer_nonce
            ).digest()
        return hashlib.sha256(
            dh_shared + self.peer_nonce + self.my_nonce
        ).digest()

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
                        "Handshake payload must contain nonce, ephemeral key, signing key and signature. "
                        "If the peer runs a pre-0.3.0 (legacy) client, both sides must use 0.3.0 or newer."
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

                self.shared_key = self._compute_final_shared_key(is_initiator=False)
                self.use_encryption = True
                self.handshake_complete = True
                self._handshake_initiated = False
                self._recv_seq = 0
                self._send_seq = 0
                self._cancel_handshake_watchdog()
                self._emit_message("success", "Secure channel with PFS established")
                self._emit_system("✔ Ready! You can now send messages.")
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
                self.shared_key = self._compute_final_shared_key(is_initiator=True)
                self.use_encryption = True
                self.handshake_complete = True
                self._handshake_initiated = False
                self._recv_seq = 0
                self._send_seq = 0
                self._cancel_handshake_watchdog()
                self._emit_message("success", "Secure channel with PFS established")
                self._emit_system("✔ Ready! You can now send messages.")
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
        if self.conn:
            await self.disconnect()

        tasks_to_cancel: list[asyncio.Task[Any]] = []
        for attr in (
            "_accept_task",
            "_tunnel_task",
            "_keepalive_task",
            "_handshake_watchdog_task",
            "_disconnect_task",
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

                    self.current_peer_addr = peer_addr
                    self.peer_b32 = peer_addr
                    self._emit_message(
                        "success", f"Connection accepted from {peer_addr[:12]}..."
                    )
                    # Отдельное событие для системного уведомления о входящем подключении.
                    self._emit_notify("connect", peer_addr)
                    self._emit_peer_changed(peer_addr)
                except Exception:
                    peer_addr = "Unknown"  # noqa: F841

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
                        if self.handshake_complete and "prefix" in str(e).lower():
                            logger.warning(
                                "Protocol downgrade detected: invalid vNext prefix after handshake"
                            )
                            self._emit_error("Protocol downgrade detected")
                            self._schedule_disconnect()
                            break
                        raise
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
                    if not crypto.verify_mac(
                        self.shared_key,
                        msg_type,
                        encrypted_body,
                        received_mac,
                        seq=seq_num,
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
                    body_data = decrypted
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
                    self._emit_message("peer", body)
                    # Дополнительное уведомление UI о входящем сообщении от собеседника.
                    notify_cb = getattr(self, "on_notify", None)
                    if notify_cb is not None:
                        try:
                            notify_cb(
                                ChatMessage(
                                    kind="peer",
                                    text=body,
                                    timestamp=datetime.now(timezone.utc),
                                )
                            )
                        except Exception:
                            # Ошибка в уведомлении не должна рвать сетевой цикл.
                            pass
                    # Подтверждение доставки по MSG_ID (vNext)
                    if msg_id:
                        try:
                            writer.write(
                                self.frame_message(
                                    "S", f"__SIGNAL__:MSG_ACK|{msg_id}"
                                )
                            )
                            await writer.drain()
                        except Exception:
                            pass

                elif msg_type == "I":
                    if body == "__END__":
                        img_text = "\n".join(self.image_buffer)
                        self.image_buffer = []
                        if self.on_image_received:
                            self.on_image_received(img_text)
                        else:
                            self._emit_message("peer", img_text)
                    else:
                        if len(self.image_buffer) < self.MAX_IMAGE_LINES:
                            self.image_buffer.append(body)
                        elif len(self.image_buffer) == self.MAX_IMAGE_LINES:
                            self.image_buffer.append("[Image truncated - too large]")
                            self._emit_error("Image too large, truncating")

                elif msg_type == "G":
                    # Inline image (binary PNG/JPEG/WebP)
                    if body == "__IMG_END__":
                        # Завершение приёма изображения
                        if self.inline_image_info and self.inline_image_buffer:
                            filename, expected_size = self.inline_image_info
                            
                            # Проверяем размер
                            if len(self.inline_image_buffer) > MAX_IMAGE_SIZE:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image too large, discarding")
                                self.inline_image_buffer = bytearray()
                                self.inline_image_info = None
                                continue
                            
                            # Проверяем magic bytes
                            header = bytes(self.inline_image_buffer[:12])
                            detected_ext = None
                            if header[:8] == b'\x89PNG\r\n\x1a\n':
                                detected_ext = 'png'
                            elif header[:3] == b'\xff\xd8\xff':
                                detected_ext = 'jpeg'
                            if detected_ext is None:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error("Received image has invalid format")
                                self.inline_image_buffer = bytearray()
                                self.inline_image_info = None
                                continue
                            
                            # Сохраняем изображение
                            import hashlib
                            file_hash = hashlib.md5(self.inline_image_buffer).hexdigest()[:8]
                            safe_filename = f"img_{int(time.time())}_{file_hash}.{detected_ext}"
                            safe_path = os.path.join(get_images_dir(), safe_filename)
                            
                            try:
                                with open(safe_path, 'wb') as f:
                                    f.write(self.inline_image_buffer)
                                
                                # Валидация с PIL
                                is_valid, error_msg, _ = validate_image(safe_path)
                                if not is_valid:
                                    os.remove(safe_path)
                                    self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                    self._emit_error(f"Received invalid image: {error_msg}")
                                else:
                                    self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=expected_size, is_sending=False, is_inline_image=True))
                                    self._emit_inline_image(safe_path, is_from_me=False)
                                    try:
                                        ack_id = self._incoming_image_msg_id or 0
                                        writer.write(
                                            self.frame_message(
                                                "S",
                                                f"__SIGNAL__:IMG_ACK|{filename}|{ack_id}",
                                            )
                                        )
                                        await writer.drain()
                                    except Exception:
                                        pass
                                    cleanup_images_cache()
                            except Exception as e:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
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
                            chunk = base64.b64decode(body)
                            self.inline_image_buffer.extend(chunk)
                            if self.inline_image_info:
                                fn, total = self.inline_image_info
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
                        safe_path = os.path.join(get_downloads_dir(), safe_name)
                        self.incoming_file = open(safe_path, "wb")
                        self._incoming_file_msg_id = msg_id or None
                        self.incoming_info = FileTransferInfo(
                            filename=safe_path, size=size, received=0
                        )
                        self._emit_system(
                            f"Receiving file: {safe_path} ({size} bytes)"
                        )
                        self._emit_file_event(self.incoming_info)
                    except Exception as e:
                        self._emit_error(f"Invalid file header: {e}")

                elif msg_type == "D":
                    try:
                        if self.incoming_file and self.incoming_info:
                            chunk = base64.b64decode(body)
                            self.incoming_file.write(chunk)
                            self.incoming_info.received += len(chunk)
                            self._emit_file_event(self.incoming_info)
                    except Exception as e:
                        self._emit_error(f"File chunk error: {e}")

                elif msg_type == "E":
                    if self.incoming_file and self.incoming_info:
                        self.incoming_file.close()
                        ack_filename = self.incoming_info.filename
                        ack_msg_id = self._incoming_file_msg_id or 0
                        self._emit_file_event(FileTransferInfo(
                            filename=ack_filename,
                            size=self.incoming_info.size,
                            received=self.incoming_info.size,
                            is_sending=False,
                        ))
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
                        if "MSG_ACK|" in body:
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
                            if self.stored_peer and new_peer != self.stored_peer:
                                self._emit_error(
                                    f"Blocked identity spoof: {new_peer[:16]}..."
                                )
                                break
                            self.peer_b32 = new_peer
                            self.current_peer_addr = self.peer_b32
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
        except asyncio.TimeoutError:
            if self._file_transfer_active:
                return
            if self.incoming_info is not None:
                loop = asyncio.get_running_loop()
                loop.create_task(self.receive_loop(connection))
                return
            if self.conn == connection:
                self._emit_error("Connection timed out (no data received)")
        except Exception as e:
            if self.conn == connection:
                self._emit_error(f"Protocol Error: {e}")
        finally:
            self._recv_loop_active = False
            # Не сбрасываем соединение если идёт передача или приём файла
            if self.conn == connection and not self._file_transfer_active and self.incoming_info is None:
                self._cancel_handshake_watchdog()
                if self._keepalive_task:
                    self._keepalive_task.cancel()
                    self._keepalive_task = None
                self.conn = None
                self._reset_crypto_state()
                self._emit_message("disconnect", "Peer disconnected.")
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
                if self.network_status == "visible":
                    self._emit_status("local_ok")

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


