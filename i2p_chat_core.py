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

# #region agent log
def _get_debug_log_path():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/I2PChat/debug-transfer.log")
    elif sys.platform == "win32":
        return os.path.join(os.environ.get("TEMP", "."), "i2pchat-debug-transfer.log")
    else:
        return "/tmp/i2pchat-debug-transfer.log"

_DBG_LOG = _get_debug_log_path()
os.makedirs(os.path.dirname(_DBG_LOG), exist_ok=True) if os.path.dirname(_DBG_LOG) else None

def _debug_log(msg, **data):
    try:
        entry = {"timestamp": int(time.time()*1000), "message": msg, "data": data}
        with open(_DBG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except: pass
# #endregion

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


StatusCallback = Callable[[str], Any]
MessageCallback = Callable[[ChatMessage], Any]
PeerChangedCallback = Callable[[Optional[str]], Any]
FileEventCallback = Callable[[FileTransferInfo], Any]
SimpleCallback = Callable[[str], Any]


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


SAFE_FILENAME_RE = re.compile(r'^[\w\-. ]+$')


def sanitize_filename(name: str) -> str:
    """
    Очистка имени файла от потенциально опасных символов.
    Возвращает безопасное имя или генерирует новое при невалидном вводе.
    """
    name = os.path.basename(name)
    if not name or not SAFE_FILENAME_RE.match(name) or name.startswith('.'):
        return f"file_{int(time.time())}"
    if len(name) > 200:
        ext = os.path.splitext(name)[1][:10]
        name = f"file_{int(time.time())}{ext}"
    return name


KEYRING_SERVICE = "i2pchat"


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

        # буфер для изображений
        self.image_buffer: list[str] = []

        # криптография (устанавливается при handshake v2)
        self.shared_key: Optional[bytes] = None
        self.my_nonce: Optional[bytes] = None
        self.peer_nonce: Optional[bytes] = None
        self.my_ephemeral_private: Optional[bytes] = None
        self.my_ephemeral_public: Optional[bytes] = None
        self.peer_ephemeral_public: Optional[bytes] = None
        self.use_encryption: bool = False

        self._accept_task: Optional[asyncio.Task[Any]] = None
        self._tunnel_task: Optional[asyncio.Task[Any]] = None
        # Сокет сессии SAM: по спецификации сессия живёт только пока этот сокет открыт.
        # Если его не хранить, сокет закрывается и сессия умирает — при Connect роутер может падать.
        self._session_socket: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
        # Флаг активной передачи файла (для защиты от timeout в receive_loop)
        self._file_transfer_active: bool = False

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

    # ---------- протокол ----------

    def frame_message(self, msg_type: str, content: str) -> bytes:
        """
        Формирует фрейм сообщения.
        
        Если установлен shared_key и use_encryption=True:
        - При наличии NaCl: шифрует тело + добавляет HMAC
        - Без NaCl: только HMAC
        
        Формат без шифрования: TYPE(1) + LEN(4) + BODY + \n
        Формат с HMAC:         TYPE(1) + LEN(4) + BODY + HMAC(32) + \n
        Формат с шифрованием:  TYPE(1) + "E" + LEN(6) + ENCRYPTED_BODY + HMAC(32) + \n
        """
        body = content.encode("utf-8")
        
        if self.shared_key and self.use_encryption:
            if crypto.NACL_AVAILABLE:
                encrypted_body = crypto.encrypt_message(self.shared_key, body)
                length_str = f"{len(encrypted_body):06d}"
                mac = crypto.compute_mac(self.shared_key, msg_type, encrypted_body)
                return msg_type.encode() + b"E" + length_str.encode() + encrypted_body + mac + b"\n"
            else:
                length_str = f"{len(body):04d}"
                mac = crypto.compute_mac(self.shared_key, msg_type, body)
                return msg_type.encode() + length_str.encode() + body + mac + b"\n"
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

        self._emit_status("local_ok")
        my_address = self.my_dest.base32 + ".b32.i2p"
        self._emit_message("success", f"Online! My Address: {my_address}")
        self.peer_b32 = f"My Addr: {my_address}"

        self._emit_system("Waiting for incoming connections...")

        # запуск фоновых задач
        loop = asyncio.get_running_loop()
        self._accept_task = loop.create_task(self.accept_loop())
        self._tunnel_task = loop.create_task(self.tunnel_watcher())

    # ---------- публичные операции ----------

    # Таймаут на установку соединения (I2P может долго строить туннели)
    CONNECT_TIMEOUT = 120
    # Таймаут на операции чтения в receive_loop (защита от зависания)
    READ_TIMEOUT = 30.0
    # Максимальное количество строк в буфере изображения (защита от OOM)
    MAX_IMAGE_LINES = 500
    # Максимальный размер принимаемого файла в байтах (защита от заполнения диска)
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

    async def connect_to_peer(self, target_address: str) -> None:
        try:
            self.current_peer_addr = target_address
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
        if not self.conn:
            self._emit_error("No active connection.")
            return
        try:
            _, writer = self.conn
            writer.write(self.frame_message("U", text))
            await writer.drain()
            self._emit_message("me", text)
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            self.conn = None

    async def send_file(self, path: str) -> None:
        if not self.conn:
            self._emit_error("No active connection.")
            return
        
        filename = os.path.basename(path)
        filesize = os.path.getsize(path)
        
        self._file_transfer_active = True
        # #region agent log
        _debug_log("send_file_start", hypothesisId="B,C", filename=filename, filesize=filesize)
        # #endregion
        
        try:
            reader, writer = self.conn
            self._emit_system(f"Sending file: {filename} ({filesize} bytes)")

            header = f"{filename}|{filesize}"
            writer.write(self.frame_message("F", header))
            await writer.drain()

            info = FileTransferInfo(filename=filename, size=filesize, received=0, is_sending=True)
            self._emit_file_event(info)

            sent = 0
            chunk_count = 0
            with open(path, "rb") as f:
                while True:
                    if not self.conn:
                        # #region agent log
                        _debug_log("send_conn_lost", hypothesisId="E", sent=sent, chunk_count=chunk_count)
                        # #endregion
                        raise ConnectionError("Connection lost during transfer")
                    
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    
                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("D", encoded))
                    await writer.drain()
                    
                    sent += len(chunk)
                    chunk_count += 1
                    # #region agent log
                    if chunk_count % 100 == 0:
                        _debug_log("send_progress", hypothesisId="B,C", sent=sent, chunk_count=chunk_count, filesize=filesize)
                    # #endregion
                    if sent % 65536 < 4096:
                        info = FileTransferInfo(filename=filename, size=filesize, received=sent, is_sending=True)
                        self._emit_file_event(info)

            writer.write(self.frame_message("E", ""))
            await writer.drain()

            info = FileTransferInfo(filename=filename, size=filesize, received=filesize, is_sending=True)
            self._emit_file_event(info)
            self._emit_message("success", f"File sent: {filename}")
            # #region agent log
            _debug_log("send_file_done", hypothesisId="B,C", sent=sent, chunk_count=chunk_count)
            # #endregion
            
            # Перезапуск receive_loop если он был прерван timeout'ом во время передачи
            if self.conn:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.receive_loop(self.conn))
                except RuntimeError:
                    pass
            
        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError) as e:
            # #region agent log
            _debug_log("send_file_conn_error", hypothesisId="B,C,D", error=str(e), sent=sent if 'sent' in dir() else 0)
            # #endregion
            info = FileTransferInfo(filename=filename, size=filesize, received=-1, is_sending=True)
            self._emit_file_event(info)
            self._emit_error(f"File transfer interrupted: connection lost")
            
        except Exception as e:
            # #region agent log
            import traceback
            _debug_log("send_file_error", hypothesisId="B,C", error=str(e), tb=traceback.format_exc())
            # #endregion
            info = FileTransferInfo(filename=filename, size=filesize, received=-1, is_sending=True)
            self._emit_file_event(info)
            self._emit_error(f"File transfer failed: {e}")
        finally:
            self._file_transfer_active = False

    async def send_image_lines(self, lines: list[str]) -> None:
        """Отправить уже отрендеренное изображение построчно."""
        if not self.conn:
            self._emit_error("No active connection.")
            return
        reader, writer = self.conn

        for line in lines:
            writer.write(self.frame_message("I", line))
        writer.write(self.frame_message("I", "__END__"))
        await writer.drain()

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
    
    def _reset_crypto_state(self) -> None:
        """Сбрасывает криптографическое состояние при отключении."""
        self.shared_key = None
        self.my_nonce = None
        self.peer_nonce = None
        self.my_ephemeral_private = None
        self.my_ephemeral_public = None
        self.peer_ephemeral_public = None
        self.use_encryption = False

    async def initiate_secure_handshake(self) -> bool:
        """
        Инициирует защищённый handshake (v2 протокол с PFS).
        
        При наличии NaCl использует эфемерные X25519 ключи для Perfect Forward Secrecy.
        Формат: INIT:<nonce_hex>[:<ephemeral_pubkey_hex>]
        
        Returns:
            True если handshake успешен
        """
        if not self.conn:
            return False
        
        try:
            _, writer = self.conn
            self.my_nonce = crypto.generate_nonce()
            
            if crypto.NACL_AVAILABLE:
                self.my_ephemeral_private, self.my_ephemeral_public = \
                    crypto.generate_ephemeral_keypair()
                handshake_data = f"INIT:{self.my_nonce.hex()}:{self.my_ephemeral_public.hex()}"
                self._emit_system("Initiating secure handshake with PFS...")
            else:
                handshake_data = f"INIT:{self.my_nonce.hex()}"
                self._emit_system("Initiating secure handshake...")
            
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
        Без PFS: SHA256(nonce_init || nonce_resp)
        """
        import hashlib
        
        if (crypto.NACL_AVAILABLE and 
            self.my_ephemeral_private and 
            self.peer_ephemeral_public):
            dh_shared = crypto.compute_dh_shared_secret(
                self.my_ephemeral_private, self.peer_ephemeral_public
            )
            if is_initiator:
                return hashlib.sha256(
                    dh_shared + self.my_nonce + self.peer_nonce
                ).digest()
            else:
                return hashlib.sha256(
                    dh_shared + self.peer_nonce + self.my_nonce
                ).digest()
        else:
            if is_initiator:
                return crypto.compute_shared_key(self.my_nonce, self.peer_nonce)
            else:
                return crypto.compute_shared_key(self.peer_nonce, self.my_nonce)

    async def _handle_handshake_message(
        self, body: str, writer: asyncio.StreamWriter
    ) -> None:
        """Обрабатывает входящее handshake сообщение с поддержкой PFS."""
        try:
            if body.startswith("INIT:"):
                parts = body[5:].split(":")
                self.peer_nonce = bytes.fromhex(parts[0])
                
                if len(parts) > 1 and crypto.NACL_AVAILABLE:
                    self.peer_ephemeral_public = bytes.fromhex(parts[1])
                    self.my_ephemeral_private, self.my_ephemeral_public = \
                        crypto.generate_ephemeral_keypair()
                
                self.my_nonce = crypto.generate_nonce()
                
                if self.my_ephemeral_public:
                    response = f"RESP:{self.my_nonce.hex()}:{self.my_ephemeral_public.hex()}"
                else:
                    response = f"RESP:{self.my_nonce.hex()}"
                    
                writer.write(self.frame_message_plain("H", response))
                await writer.drain()
                
                self.shared_key = self._compute_final_shared_key(is_initiator=False)
                self.use_encryption = True
                
                if self.peer_ephemeral_public:
                    self._emit_message("success", "Secure channel with PFS established")
                else:
                    self._emit_message("success", "Secure channel established (HMAC enabled)")
                self._emit_system("✔ Ready! You can now send messages.")
                logger.info("Handshake completed (responder)")
                
            elif body.startswith("RESP:"):
                parts = body[5:].split(":")
                self.peer_nonce = bytes.fromhex(parts[0])
                
                if len(parts) > 1 and crypto.NACL_AVAILABLE:
                    self.peer_ephemeral_public = bytes.fromhex(parts[1])
                
                if self.my_nonce is None:
                    logger.warning("Received RESP without prior INIT")
                    return
                
                self.shared_key = self._compute_final_shared_key(is_initiator=True)
                self.use_encryption = True
                
                if self.peer_ephemeral_public and self.my_ephemeral_private:
                    self._emit_message("success", "Secure channel with PFS established")
                else:
                    self._emit_message("success", "Secure channel established (HMAC enabled)")
                self._emit_system("✔ Ready! You can now send messages.")
                logger.info("Handshake completed (initiator)")
                
            else:
                logger.warning(f"Unknown handshake message: {body[:20]}")
                
        except Exception as e:
            logger.error(f"Handshake error: {e}")
            self._emit_error(f"Secure handshake failed: {e}")

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
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
            except Exception:
                await asyncio.sleep(1)

    async def receive_loop(
        self,
        connection: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
        initial_type: Optional[str] = None,
    ) -> None:
        reader, writer = connection
        current_type = initial_type
        # #region agent log
        _debug_log("recv_loop_start", hypothesisId="E", file_active=self._file_transfer_active)
        # #endregion

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

                if msg_type not in ["U", "S", "P", "O", "F", "D", "E", "I", "H"]:
                    logger.warning(f"Invalid message type received: {repr(msg_type)}")
                    break

                first_len_byte = await asyncio.wait_for(
                    reader.read(1), timeout=self.READ_TIMEOUT
                )
                if not first_len_byte:
                    break
                
                is_encrypted = (first_len_byte == b"E")
                
                if is_encrypted:
                    len_data = await asyncio.wait_for(
                        reader.readexactly(6), timeout=self.READ_TIMEOUT
                    )
                else:
                    remaining_len = await asyncio.wait_for(
                        reader.readexactly(3), timeout=self.READ_TIMEOUT
                    )
                    len_data = first_len_byte + remaining_len
                
                try:
                    msg_len = int(len_data.decode())
                except ValueError:
                    logger.warning(f"Invalid length field: {repr(len_data)}")
                    break

                body_data = await asyncio.wait_for(
                    reader.readexactly(msg_len), timeout=self.READ_TIMEOUT
                )
                
                if is_encrypted or (self.shared_key and self.use_encryption):
                    received_mac = await asyncio.wait_for(
                        reader.readexactly(crypto.HMAC_SIZE), timeout=self.READ_TIMEOUT
                    )
                    if not crypto.verify_mac(self.shared_key, msg_type, body_data, received_mac):
                        logger.warning("HMAC verification failed - message integrity compromised")
                        self._emit_error("Message integrity check failed")
                        break
                    
                    if is_encrypted and crypto.NACL_AVAILABLE:
                        decrypted = crypto.decrypt_message(self.shared_key, body_data)
                        if decrypted is None:
                            logger.warning("Decryption failed")
                            self._emit_error("Failed to decrypt message")
                            break
                        body_data = decrypted
                
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

                elif msg_type == "F":
                    try:
                        filename, size_str = body.split("|")
                        filename = sanitize_filename(filename)
                        size = int(size_str)
                        # #region agent log
                        _debug_log("recv_file_header", hypothesisId="A", filename=filename, size=size)
                        # #endregion
                        if size > self.MAX_FILE_SIZE:
                            self._emit_error(
                                f"File too large: {size} bytes "
                                f"(max {self.MAX_FILE_SIZE // (1024*1024)} MB)"
                            )
                            self.incoming_file = None
                            self.incoming_info = None
                            continue
                        safe_name = f"recv_{filename}"
                        safe_path = os.path.join(get_downloads_dir(), safe_name)
                        self.incoming_file = open(safe_path, "wb")
                        self.incoming_info = FileTransferInfo(
                            filename=safe_path, size=size, received=0
                        )
                        self._recv_chunk_count = 0  # для debug
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
                            self._recv_chunk_count = getattr(self, '_recv_chunk_count', 0) + 1
                            # #region agent log
                            if self._recv_chunk_count % 100 == 0:
                                _debug_log("recv_progress", hypothesisId="A", received=self.incoming_info.received, chunk_count=self._recv_chunk_count, size=self.incoming_info.size)
                            # #endregion
                            self._emit_file_event(self.incoming_info)
                    except Exception as e:
                        # #region agent log
                        _debug_log("recv_chunk_error", hypothesisId="A", error=str(e))
                        # #endregion
                        self._emit_error(f"File chunk error: {e}")

                elif msg_type == "E":
                    if self.incoming_file and self.incoming_info:
                        # #region agent log
                        _debug_log("recv_file_done", hypothesisId="A", received=self.incoming_info.received, size=self.incoming_info.size)
                        # #endregion
                        self.incoming_file.close()
                        self._emit_message(
                            "success",
                            f"File received: {self.incoming_info.filename} "
                            f"({self.incoming_info.received} bytes)",
                        )
                        self.incoming_file = None
                        self.incoming_info = None

                elif msg_type == "S":
                    if "__SIGNAL__:" in body:
                        if "QUIT" in body:
                            self._emit_system("Peer requested disconnect.")
                            break
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

        except (asyncio.IncompleteReadError, ConnectionResetError) as e:
            # #region agent log
            _debug_log("recv_conn_reset", hypothesisId="D", error=str(type(e).__name__), recv=getattr(getattr(self, 'incoming_info', None), 'received', 0))
            # #endregion
            pass
        except asyncio.TimeoutError:
            # #region agent log
            _debug_log("recv_timeout", hypothesisId="A,F", file_active=self._file_transfer_active, incoming=self.incoming_info is not None, recv=getattr(getattr(self, 'incoming_info', None), 'received', 0))
            # #endregion
            # Не рвём соединение если идёт передача (отправка) или приём файла
            if self._file_transfer_active:
                return
            if self.incoming_info is not None:
                # Приём файла в процессе — просто перезапускаем receive_loop
                # #region agent log
                _debug_log("recv_timeout_restart", hypothesisId="F", recv=self.incoming_info.received)
                # #endregion
                loop = asyncio.get_running_loop()
                loop.create_task(self.receive_loop(connection))
                return
            if self.conn == connection:
                self._emit_error("Connection timed out (no data received)")
        except Exception as e:
            # #region agent log
            import traceback
            _debug_log("recv_exception", hypothesisId="A,D", error=str(e), tb=traceback.format_exc())
            # #endregion
            if self.conn == connection:
                self._emit_error(f"Protocol Error: {e}")
        finally:
            # Не сбрасываем соединение если идёт передача или приём файла
            if self.conn == connection and not self._file_transfer_active and self.incoming_info is None:
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


