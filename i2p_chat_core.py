import asyncio
import base64
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Tuple

import i2plib


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


StatusCallback = Callable[[str], Any]
MessageCallback = Callable[[ChatMessage], Any]
PeerChangedCallback = Callable[[Optional[str]], Any]
FileEventCallback = Callable[[FileTransferInfo], Any]
SimpleCallback = Callable[[str], Any]


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

        self._accept_task: Optional[asyncio.Task[Any]] = None
        self._tunnel_task: Optional[asyncio.Task[Any]] = None

    # ---------- вспомогательные уведомления ----------

    def _emit_status(self, status: str) -> None:
        self.network_status = status
        if self.on_status:
            self.on_status(status)

    def _emit_message(self, kind: str, text: str) -> None:
        if self.on_message:
            msg = ChatMessage(kind=kind, text=text, timestamp=datetime.now(timezone.utc))
            self.on_message(msg)

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

    @staticmethod
    def frame_message(msg_type: str, content: str) -> bytes:
        body = content.encode("utf-8")
        length_str = f"{len(body):04d}"
        return msg_type.encode() + length_str.encode() + body + b"\n"

    # ---------- инициализация сессии ----------

    async def init_session(self) -> None:
        """Создать/загрузить идентичность и SAM-сессию."""
        self._emit_status("initializing")
        self._emit_system(f"Initializing Profile: {self.profile}")

        key_file = f"{self.profile}.dat"
        is_persistent = self.profile != "default"

        dest: Optional[i2plib.Destination] = None

        if is_persistent and os.path.exists(key_file):
            with open(key_file, "r") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]

            if len(lines) > 0:
                raw_private_key = lines[0]
                dest = i2plib.Destination(raw_private_key, has_private_key=True)
                self._emit_system(f"Loaded identity from {key_file}")

            if len(lines) > 1:
                self.stored_peer = lines[1]
                self._emit_system(f"Stored Contact: {self.stored_peer}.b32.i2p")

        if dest is None:
            self._emit_system("Generating new Ed25519 identity...")
            dest = await i2plib.new_destination(
                sam_address=self.sam_address, sig_type=7
            )

            if is_persistent:
                with open(key_file, "w") as f:
                    f.write(dest.private_key.base64 + "\n")
                self._emit_message("success", f"Identity saved to {key_file}")

        self.my_dest = dest

        await i2plib.create_session(
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

    async def connect_to_peer(self, target_address: str) -> None:
        try:
            self.current_peer_addr = target_address
            reader, writer = await i2plib.stream_connect(
                self.session_id, target_address, sam_address=self.sam_address
            )

            if self.my_dest is not None:
                writer.write(self.my_dest.base64.encode() + b"\n")
                writer.write(self.frame_message("S", self.my_dest.base64))
                await writer.drain()

                self.proven = True
                self._emit_status("visible")

            self.conn = (reader, writer)
            self._emit_message("success", "Handshake sent. Establishing tunnel...")

            loop = asyncio.get_running_loop()
            loop.create_task(self.receive_loop(self.conn))
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
        try:
            reader, writer = self.conn
            filename = os.path.basename(path)
            filesize = os.path.getsize(path)

            self._emit_system(f"Sending file: {filename} ({filesize} bytes)")

            header = f"{filename}|{filesize}"
            writer.write(self.frame_message("F", header))
            await writer.drain()

            info = FileTransferInfo(filename=filename, size=filesize, received=0)
            self._emit_file_event(info)

            with open(path, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    encoded = base64.b64encode(chunk).decode()
                    writer.write(self.frame_message("D", encoded))
                    await writer.drain()

            writer.write(self.frame_message("E", ""))
            await writer.drain()

            self._emit_message("success", f"File sent: {filename}")
        except Exception as e:
            self._emit_error(f"File transfer failed: {e}")

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
        try:
            writer.write(self.frame_message("S", "__SIGNAL__:QUIT"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        self._emit_message("disconnect", "You disconnected.")
        self._emit_system("Waiting for incoming connections...")

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
        if self.conn:
            await self.disconnect()

        if self._accept_task:
            self._accept_task.cancel()
        if self._tunnel_task:
            self._tunnel_task.cancel()

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

        try:
            while True:
                if current_type:
                    msg_type = current_type
                    current_type = None
                else:
                    type_data = await reader.read(1)
                    if not type_data:
                        break
                    msg_type = type_data.decode()

                if msg_type not in ["U", "S", "P", "O", "F", "D", "E", "I"]:
                    remainder = await reader.readline()
                    try:
                        raw_dest = (msg_type + remainder.decode()).strip()
                        dest_obj = i2plib.Destination(raw_dest)
                        self.proven = True
                        self._emit_status("visible")
                        self.peer_b32 = dest_obj.base32 + ".b32.i2p"
                        self.current_peer_addr = self.peer_b32
                        self._emit_message(
                            "info", f"Connected to: {self.peer_b32}"
                        )
                        self._emit_peer_changed(self.peer_b32)
                    except Exception:
                        pass
                    continue

                len_data = await reader.readexactly(4)
                try:
                    msg_len = int(len_data.decode())
                except ValueError:
                    break

                body_data = await reader.readexactly(msg_len)
                body = body_data.decode("utf-8")

                delim = await reader.readexactly(1)
                if delim != b"\n":
                    break

                if msg_type == "U":
                    self._emit_message("peer", body)

                elif msg_type == "I":
                    if body == "__END__":
                        img_text = "\n".join(self.image_buffer)
                        self.image_buffer = []
                        if self.on_image_received:
                            self.on_image_received(img_text)
                        else:
                            self._emit_message("peer", img_text)
                    else:
                        self.image_buffer.append(body)

                elif msg_type == "F":
                    try:
                        filename, size_str = body.split("|")
                        filename = os.path.basename(filename)
                        size = int(size_str)
                        safe_name = f"recv_{filename}"
                        self.incoming_file = open(safe_name, "wb")
                        self.incoming_info = FileTransferInfo(
                            filename=safe_name, size=size, received=0
                        )
                        self._emit_system(
                            f"Receiving file: {safe_name} ({size} bytes)"
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
                            self.peer_b32 = dest_obj.base32 + ".b32.i2p"
                            self.current_peer_addr = self.peer_b32
                            self._emit_message(
                                "info", f"Peer Identity: {self.peer_b32}"
                            )
                            self._emit_peer_changed(self.peer_b32)
                        except Exception:
                            pass

                elif msg_type == "P":
                    writer.write(self.frame_message("O", ""))
                    await writer.drain()

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            if self.conn == connection:
                self._emit_error(f"Protocol Error: {e}")
        finally:
            if self.conn == connection:
                self.conn = None
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

