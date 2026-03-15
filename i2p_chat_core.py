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

logger = logging.getLogger("i2pchat")

PROTOCOL_VERSION = 2


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
        return None
    except Exception:
        return None


def _try_keyring_set(profile: str, private_key: str) -> bool:
    """Попытка сохранить приватный ключ в системный keyring."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, profile, private_key)
        return True
    except ImportError:
        return False
    except Exception:
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

        self._accept_task: Optional[asyncio.Task[Any]] = None
        self._tunnel_task: Optional[asyncio.Task[Any]] = None
        self._keepalive_task: Optional[asyncio.Task[Any]] = None
        self._handshake_watchdog_task: Optional[asyncio.Task[Any]] = None
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
        if self._handshake_watchdog_task:
            self._handshake_watchdog_task.cancel()
            self._handshake_watchdog_task = None

    async def _handshake_watchdog(
        self, connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter]
    ) -> None:
        """Закрывает соединение, если handshake не завершился вовремя."""
        await asyncio.sleep(self.HANDSHAKE_TIMEOUT)
        if self.conn == connection and not self.handshake_complete:
            self._emit_error("Secure handshake timed out")
            await self.disconnect()

    # ---------- протокол ----------

    def frame_message(self, msg_type: str, content: str) -> bytes:
        """
        Формирует фрейм сообщения.
        
        Если установлен shared_key и use_encryption=True:
        - Обязательно шифрует тело (NaCl) + добавляет HMAC и sequence number
        
        Формат без шифрования: TYPE(1) + LEN(4) + BODY + \n
        Формат с шифрованием:  TYPE(1) + "E" + SEQ(8 hex) + LEN(6) + ENCRYPTED_BODY + HMAC(32) + \n
        """
        body = content.encode("utf-8")
        
        if self.shared_key and self.use_encryption:
            if not crypto.NACL_AVAILABLE:
                raise RuntimeError("NaCl is required for secure protocol mode")
            self._send_seq += 1
            seq = self._send_seq
            encrypted_body = crypto.encrypt_message(self.shared_key, body)
            length_str = f"{len(encrypted_body):06d}"
            seq_hex = f"{seq:08x}".encode()
            mac = crypto.compute_mac(self.shared_key, msg_type, encrypted_body, seq=seq)
            return (
                msg_type.encode()
                + b"E"
                + seq_hex
                + length_str.encode()
                + encrypted_body
                + mac
                + b"\n"
            )
        else:
            length_str = f"{len(body):04d}"
            return msg_type.encode() + length_str.encode() + body + b"\n"
    
    @staticmethod
    def frame_message_plain(msg_type: str, content: str) -> bytes:
        """Формирует фрейм без HMAC (для handshake)."""
        body = content.encode("utf-8")
        length_str = f"{len(body):04d}"
        return msg_type.encode() + length_str.encode() + body + b"\n"

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

    def _pin_or_verify_peer_signing_key(self, peer_addr: str, verify_key: bytes) -> bool:
        peer_addr = self._normalize_peer_addr(peer_addr)
        if not peer_addr:
            self._emit_error("Cannot pin signing key: unknown peer address")
            return False
        fp = self._fingerprint_pubkey(verify_key)
        current_hex = verify_key.hex().lower()
        pinned_hex = self.peer_trusted_signing_keys.get(peer_addr)
        if pinned_hex is None:
            if self.on_trust_decision is not None:
                try:
                    approved = bool(self.on_trust_decision(peer_addr, fp, current_hex))
                except Exception as e:
                    logger.warning("TOFU trust callback failed: %s", e)
                    approved = False
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
                self.my_signing_public = bytes(crypto.SigningKey(seed).verify_key)
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
                self.my_signing_public = bytes(crypto.SigningKey(seed).verify_key)
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

                if len(lines) > 0:
                    raw_private_key = lines[0]
                    dest = i2plib.Destination(raw_private_key, has_private_key=True)
                    self._emit_system(f"Loaded identity from {key_file}")

            if os.path.exists(key_file):
                with open(key_file, "r") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                if len(lines) > 1:
                    self.stored_peer = lines[1]
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
                    with open(key_file, "w") as f:
                        f.write(dest.private_key.base64 + "\n")
                    try:
                        os.chmod(key_file, 0o600)
                    except OSError:
                        pass
                    self._emit_message("success", f"Identity saved to {key_file}")

        self.my_dest = dest
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
    # Таймаут на установление защищённого канала после TCP/I2P connect
    HANDSHAKE_TIMEOUT = 20.0
    # Максимальное количество строк в буфере изображения (защита от OOM)
    MAX_IMAGE_LINES = 500
    # Максимальный размер принимаемого файла в байтах (защита от заполнения диска)
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
    # Ограничение на размер одного фрейма протокола (защита от memory DoS)
    MAX_FRAME_BODY = 2 * 1024 * 1024  # 2 MB

    async def connect_to_peer(self, target_address: str) -> None:
        if not crypto.NACL_AVAILABLE:
            self._emit_error("Secure protocol requires PyNaCl. Install dependency: pynacl")
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
                writer.write(self.frame_message("S", self.my_dest.base64))
                await writer.drain()

                self.proven = True
                self._emit_status("visible")

            self.conn = (reader, writer)
            self._emit_message("success", "Handshake sent. Establishing secure channel... Wait")

            loop = asyncio.get_running_loop()
            loop.create_task(self.receive_loop(self.conn))
            loop.create_task(self.initiate_secure_handshake())
            self._cancel_handshake_watchdog()
            self._handshake_watchdog_task = loop.create_task(self._handshake_watchdog(self.conn))
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
            writer.write(self.frame_message("U", text))
            await writer.drain()
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
            writer.write(self.frame_message("F", header))
            await writer.drain()

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
            writer.write(self.frame_message("G", header))
            await writer.drain()
            
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
        if not self.conn:
            return
        self._cancel_handshake_watchdog()
        # Останавливаем keepalive
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        reader, writer = self.conn
        self.conn = None
        self.peer_b32 = "Waiting for incoming connections..."
        self._reset_crypto_state()
        try:
            writer.write(self.frame_message_plain("S", "__SIGNAL__:QUIT"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        self._emit_message("disconnect", "You disconnected.")
        self._emit_system("Waiting for incoming connections...")
    
    async def _keepalive_loop(self) -> None:
        """Отправляет Ping каждые 15 секунд для поддержания соединения при простое."""
        while self.conn:
            await asyncio.sleep(15)
            if self.conn and not self._file_transfer_active:
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
            await self.disconnect()
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
                        "Handshake payload must contain nonce, ephemeral key, signing key and signature"
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
                if not self._pin_or_verify_peer_signing_key(self.current_peer_addr, peer_sign_pub):
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
                    await self.disconnect()
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
                if not self._pin_or_verify_peer_signing_key(self.current_peer_addr, peer_sign_pub):
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
            await self.disconnect()

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
        self._cancel_handshake_watchdog()
        if self.conn:
            await self.disconnect()

        if self._accept_task:
            self._accept_task.cancel()
        if self._tunnel_task:
            self._tunnel_task.cancel()
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
                    self._emit_peer_changed(peer_addr)
                except Exception:
                    peer_addr = "Unknown"  # noqa: F841

                if self.my_dest is not None:
                    writer.write(self.frame_message("S", self.my_dest.base64))
                    await writer.drain()

                self.conn = (reader, writer)

                loop = asyncio.get_running_loop()
                loop.create_task(self.receive_loop(self.conn))
                self._cancel_handshake_watchdog()
                self._handshake_watchdog_task = loop.create_task(self._handshake_watchdog(self.conn))
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
                if current_type:
                    msg_type = current_type
                    current_type = None
                else:
                    type_data = await asyncio.wait_for(
                        reader.read(1), timeout=self.READ_TIMEOUT
                    )
                    if not type_data:
                        break
                    msg_type = type_data.decode()

                if msg_type not in ["U", "S", "P", "O", "F", "D", "E", "I", "H", "G"]:
                    logger.warning(f"Invalid message type received: {repr(msg_type)}")
                    break
                if self.handshake_complete and msg_type == "H":
                    logger.warning("Unexpected handshake frame after secure channel established")
                    self._emit_error("Protocol violation: unexpected handshake frame")
                    await self.disconnect()
                    break
                if not self.handshake_complete and msg_type not in ["S", "H", "P", "O"]:
                    logger.warning(
                        "Protocol violation: non-handshake frame before secure channel "
                        "(msg_type=%r)",
                        msg_type,
                    )
                    self._emit_error("Protocol violation: data before secure handshake")
                    await self.disconnect()
                    break

                first_len_byte = await asyncio.wait_for(
                    reader.read(1), timeout=self.READ_TIMEOUT
                )
                if not first_len_byte:
                    break
                
                is_encrypted = (first_len_byte == b"E")
                seq_num: Optional[int] = None
                
                if is_encrypted:
                    seq_data = await asyncio.wait_for(
                        reader.readexactly(8), timeout=self.READ_TIMEOUT
                    )
                    try:
                        seq_num = int(seq_data.decode(), 16)
                    except ValueError:
                        logger.warning(f"Invalid sequence field: {repr(seq_data)}")
                        break
                    len_data = await asyncio.wait_for(
                        reader.readexactly(6), timeout=self.READ_TIMEOUT
                    )
                else:
                    if self.handshake_complete:
                        logger.warning("Protocol downgrade detected: plaintext frame after handshake")
                        self._emit_error("Protocol downgrade detected")
                        await self.disconnect()
                        break
                    remaining_len = await asyncio.wait_for(
                        reader.readexactly(3), timeout=self.READ_TIMEOUT
                    )
                    len_data = first_len_byte + remaining_len
                
                try:
                    msg_len = int(len_data.decode())
                except ValueError:
                    logger.warning(f"Invalid length field: {repr(len_data)}")
                    break
                if msg_len < 0 or msg_len > self.MAX_FRAME_BODY:
                    logger.warning("Frame too large: msg_len=%d", msg_len)
                    self._emit_error("Protocol error: frame too large")
                    break

                body_data = await asyncio.wait_for(
                    reader.readexactly(msg_len), timeout=self.READ_TIMEOUT
                )
                
                if is_encrypted:
                    if not self.shared_key or not self.use_encryption:
                        logger.warning("Encrypted frame received before key setup")
                        self._emit_error("Protocol error: encrypted frame before handshake")
                        await self.disconnect()
                        break
                    if seq_num is None:
                        logger.warning("Missing sequence number for encrypted frame")
                        break
                    received_mac = await asyncio.wait_for(
                        reader.readexactly(crypto.HMAC_SIZE), timeout=self.READ_TIMEOUT
                    )
                    if not crypto.verify_mac(
                        self.shared_key,
                        msg_type,
                        body_data,
                        received_mac,
                        seq=seq_num,
                    ):
                        logger.warning(
                            "HMAC verification failed - message integrity compromised "
                            "(msg_type=%r body_len=%d)", msg_type, len(body_data)
                        )
                        self._emit_error("Message integrity check failed")
                        await self.disconnect()
                        break
                    expected_seq = self._recv_seq + 1
                    if seq_num != expected_seq:
                        logger.warning(
                            "Replay/out-of-order frame detected: got=%d expected=%d",
                            seq_num,
                            expected_seq,
                        )
                        self._emit_error("Replay protection triggered")
                        await self.disconnect()
                        break

                    decrypted = crypto.decrypt_message(self.shared_key, body_data)
                    if decrypted is None:
                        logger.warning("Decryption failed")
                        self._emit_error("Failed to decrypt message")
                        break
                    body_data = decrypted
                    self._recv_seq = seq_num
                
                body = body_data.decode("utf-8")

                delim = await asyncio.wait_for(
                    reader.readexactly(1), timeout=self.READ_TIMEOUT
                )
                if delim != b"\n":
                    logger.warning(f"Invalid delimiter: expected newline, got {repr(delim)}")
                    break

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
                                        writer.write(self.frame_message("S", f"__SIGNAL__:IMG_ACK|{filename}"))
                                        await writer.drain()
                                    except Exception:
                                        pass
                                    cleanup_images_cache()
                            except Exception as e:
                                self._emit_file_event(FileTransferInfo(filename=filename, size=expected_size, received=-1, is_sending=False, is_inline_image=True))
                                self._emit_error(f"Failed to save image: {e}")
                            
                            self.inline_image_buffer = bytearray()
                            self.inline_image_info = None
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
                                    f"__SIGNAL__:FILE_ACK|{os.path.basename(ack_filename)}",
                                )
                            )
                            await writer.drain()
                        except Exception:
                            pass

                elif msg_type == "S":
                    if "__SIGNAL__:" in body:
                        if "IMG_ACK|" in body:
                            try:
                                ack_filename = body.split("IMG_ACK|", 1)[1].strip()
                                if self.on_image_delivered:
                                    self.on_image_delivered(ack_filename)
                            except Exception:
                                pass
                        elif "FILE_ACK|" in body:
                            try:
                                ack_filename = body.split("FILE_ACK|", 1)[1].strip()
                                if self.on_file_delivered:
                                    self.on_file_delivered(ack_filename)
                            except Exception:
                                pass
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


