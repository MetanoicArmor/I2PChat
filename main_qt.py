import asyncio
import sys
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets, sip
import qasync

from i2p_chat_core import ChatMessage, FileTransferInfo, I2PChatCore
from renderer import render_braille, render_bw


class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("I2P Chat (PyQt)")
        self.resize(900, 600)

        self.profile = profile or "default"

        # UI
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)

        # статусная панель
        self.status_label = QtWidgets.QLabel("Status: initializing", self)
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        # основной чат
        self.chat_view = QtWidgets.QTextEdit(self)
        self.chat_view.setReadOnly(True)
        self.chat_view.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)

        # панель ввода
        input_layout = QtWidgets.QHBoxLayout()
        self.input_edit = QtWidgets.QLineEdit(self)
        self.input_edit.setPlaceholderText("Type message and press Enter...")
        self.send_button = QtWidgets.QPushButton("Send", self)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(self.send_button)

        # панель действий
        actions_layout = QtWidgets.QHBoxLayout()

        self.addr_edit = QtWidgets.QLineEdit(self)
        self.addr_edit.setPlaceholderText("Peer .b32.i2p address")

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        self.disconnect_button = QtWidgets.QPushButton("Disconnect", self)

        self.send_file_button = QtWidgets.QPushButton("Send File", self)
        self.send_img_braille_button = QtWidgets.QPushButton("Send Image (braille)", self)
        self.send_img_bw_button = QtWidgets.QPushButton("Send Image (bw)", self)

        actions_layout.addWidget(self.addr_edit)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.disconnect_button)
        actions_layout.addWidget(self.send_file_button)
        actions_layout.addWidget(self.send_img_braille_button)
        actions_layout.addWidget(self.send_img_bw_button)

        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.chat_view, 1)
        main_layout.addLayout(input_layout)
        main_layout.addLayout(actions_layout)

        # сигналы
        self.send_button.clicked.connect(self.on_send_clicked)
        self.input_edit.returnPressed.connect(self.on_send_clicked)
        self.connect_button.clicked.connect(self.on_connect_clicked)
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.send_file_button.clicked.connect(self.on_send_file_clicked)
        self.send_img_braille_button.clicked.connect(self.on_send_img_braille_clicked)
        self.send_img_bw_button.clicked.connect(self.on_send_img_bw_clicked)

        # ядро
        self.core = I2PChatCore(
            profile=self.profile,
            on_status=self.handle_status,
            on_message=self.handle_message,
            on_peer_changed=self.handle_peer_changed,
            on_system=self.handle_system,
            on_error=self.handle_error,
            on_file_event=self.handle_file_event,
            on_image_received=self.handle_image_received,
        )

    # ----- callbacks из ядра -----

    @QtCore.pyqtSlot(str)
    def handle_status(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")

    @QtCore.pyqtSlot(object)
    def handle_message(self, msg: ChatMessage) -> None:
        kind = msg.kind
        ts = msg.timestamp.strftime("%H:%M:%S")
        text = msg.text

        if kind == "me":
            color = "#00ff00"
            prefix = "Me"
        elif kind == "peer":
            color = "#00ffff"
            prefix = "Peer"
        elif kind == "error":
            color = "#ff5555"
            prefix = "ERROR"
        elif kind == "success":
            color = "#50fa7b"
            prefix = "OK"
        elif kind == "disconnect":
            color = "#ffb86c"
            prefix = "X"
        elif kind == "help":
            color = "#aaaaaa"
            prefix = "HELP"
        elif kind == "info":
            color = "#8be9fd"
            prefix = "INFO"
        else:
            color = "#f1fa8c"
            prefix = "SYSTEM"

        self.chat_view.append(f"[{ts}] {prefix}: {text}")

    @QtCore.pyqtSlot(str)
    def handle_system(self, text: str) -> None:
        self.chat_view.append(f"[SYSTEM] {text}")

    @QtCore.pyqtSlot(str)
    def handle_error(self, text: str) -> None:
        self.chat_view.append(f"[ERROR] {text}")

    @QtCore.pyqtSlot(object)
    def handle_file_event(self, info: FileTransferInfo) -> None:
        self.chat_view.append(
            f"[FILE] {info.filename}: {info.received}/{info.size} bytes"
        )

    @QtCore.pyqtSlot(str)
    def handle_image_received(self, art: str) -> None:
        self.chat_view.append("[IMAGE]\n" + art)

    @QtCore.pyqtSlot(object)
    def handle_peer_changed(self, peer: Optional[str]) -> None:
        if peer:
            self.addr_edit.setText(peer)

    # ----- обработчики UI -----

    @QtCore.pyqtSlot()
    def on_send_clicked(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        asyncio.create_task(self.core.send_text(text))

    @QtCore.pyqtSlot()
    def on_connect_clicked(self) -> None:
        addr = self.addr_edit.text().strip()
        if not addr:
            QtWidgets.QMessageBox.warning(
                self, "Connect", "Please enter peer address"
            )
            return
        asyncio.create_task(self.core.connect_to_peer(addr))

    @QtCore.pyqtSlot()
    def on_disconnect_clicked(self) -> None:
        asyncio.create_task(self.core.disconnect())

    @QtCore.pyqtSlot()
    def on_send_file_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select file to send"
        )
        if not path:
            return
        asyncio.create_task(self.core.send_file(path))

    @QtCore.pyqtSlot()
    def on_send_img_braille_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select image to send (braille)"
        )
        if not path:
            return
        lines = render_braille(path)
        art = "\n".join(lines)
        ts = asyncio.get_event_loop().time()
        # показать локально как сообщение от "Me"
        self.chat_view.append("[IMAGE braille]\n" + art)
        asyncio.create_task(self.core.send_image_lines(lines))

    @QtCore.pyqtSlot()
    def on_send_img_bw_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select image to send (bw)"
        )
        if not path:
            return
        lines = render_bw(path)
        art = "\n".join(lines)
        self.chat_view.append("[IMAGE bw]\n" + art)
        asyncio.create_task(self.core.send_image_lines(lines))

    async def start_core(self) -> None:
        await self.core.init_session()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Останавливаем ядро и event loop при закрытии окна."""
        loop = asyncio.get_event_loop()

        async def _shutdown() -> None:
            try:
                await self.core.shutdown()
            finally:
                loop.stop()

        asyncio.ensure_future(_shutdown())
        event.accept()


def main() -> None:
    """Точка входа без qasync.run, чтобы избежать падений при завершении."""
    if hasattr(sip, "setdestroyonexit"):
        sip.setdestroyonexit(False)

    profile = sys.argv[1] if len(sys.argv) > 1 else None

    app = QtWidgets.QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = ChatWindow(profile=profile)
    window.show()

    # запускаем инициализацию ядра в Qt-совместимом event loop
    asyncio.ensure_future(window.start_core())

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()

