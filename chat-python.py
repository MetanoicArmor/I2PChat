import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

from rich import box
from rich.align import Align
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

from i2p_chat_core import (
    ChatMessage,
    FileTransferInfo,
    I2PChatCore,
    render_braille,
    render_bw,
)


class I2PChat(App):
    """TUI-фронтенд к I2PChatCore."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
    ]

    CSS = """
    RichLog { height: 1fr; border: solid white; background: $surface; } 
    Input { dock: bottom; }
    #status_bar {
        dock: top;
        height: 3;
        margin: 0 0;
        content-align: center middle;
        background: $surface; 
        color: $text;              
    }
    """

    peer_b32 = reactive("Waiting for incoming connections...")
    network_status = reactive("initializing")

    def __init__(self) -> None:
        super().__init__()
        self.profile = sys.argv[1] if len(sys.argv) > 1 else "default"

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

    def compose(self) -> ComposeResult:
        yield Static(id="status_bar")
        yield RichLog(id="chat_window", highlight=False, markup=True)
        yield Input(placeholder="Type message and press Enter...")

    def watch_network_status(self, _) -> None:
        self.watch_peer_b32(self.peer_b32)

    def watch_peer_b32(self, new_val: str) -> None:
        status_map = {
            "initializing": ("[grey62]●[/]", "INITIALIZING", "grey62"),
            "local_ok": ("[yellow]●[/]", "BUILDING TUNNELS", "yellow"),
            "visible": ("[green]●[/]", "VISIBLE / READY", "green"),
        }

        dot, _, _ = status_map.get(self.network_status, status_map["initializing"])

        is_active = "Waiting" not in new_val and "My Addr" not in new_val
        is_persistent = self.profile != "default"

        if self.core.proven:
            border_col, title = "green", "VERIFIED SESSION"
        elif is_active:
            border_col, title = "cyan", "ACTIVE SESSION"
        else:
            border_col, title = "yellow", "TUNNELS READY"

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)

        if is_persistent:
            mode_tag = "P"
            tag_bg = "green"
        else:
            mode_tag = "T"
            tag_bg = "grey62"

        left_content = (
            f"[black on {tag_bg}] [bold]{mode_tag}[/] [/] "
            f"[bold]{self.profile.upper()}[/]"
        )

        if is_active:
            link_color = "green" if self.core.proven else "cyan"
            link_symbol = "●" if self.core.proven else "o"
            conn_viz = f"[bold {link_color}]{link_symbol}[/] [dim]CONNECTED[/]"
        else:
            conn_viz = f"[dim]{dot} [dim]STANDBY[/]"

        if self.core.my_dest:
            full_addr = self.core.my_dest.base32
            my_b32 = f"{full_addr[:6]}...{full_addr[-6:]}"
        else:
            my_b32 = "----"

        if is_active and self.core.current_peer_addr:
            clean_peer = self.core.current_peer_addr.replace(".b32.i2p", "")
            peer_disp = f"{clean_peer[:6]}..{clean_peer[-6:]}"
        else:
            peer_disp = "------"

        right_content = f"[green]{my_b32}[/] [white]:[/] [cyan dim]{peer_disp}[/]"

        grid.add_row(left_content, conn_viz, right_content)

        status_panel = Panel(
            grid,
            title=f"[bold {border_col}]{title}[/]",
            border_style=border_col,
            box=box.ROUNDED,
            style="default",
        )

        try:
            self.query_one("#status_bar").update(status_panel)
        except Exception:
            pass

    @property
    def chat_log(self) -> RichLog:
        return self.query_one("#chat_window", RichLog)

    def post(self, type_name: str, message: str) -> None:
        styles = {
            "info": "[bold blue]STATUS:[/] [white]{}[/]",
            "error": "[bold red]ERROR:[/] [red]{}[/]",
            "system": "[#878700]SYSTEM:[/] [dim #9f9f9f italic]{}[/]",
            "me": "[bold green]Me:[/] [white]{}[/]",
            "peer": "[bold cyan]Peer:[/] [white]{}[/]",
            "success": "[bold green]✔[/] [white]{}[/]",
            "disconnect": "[bold red]X[/] [white]{}[/]",
            "help": "[dim]HELP:[/] [gray62]{}[/]",
        }

        safe_message = escape(str(message))
        address_pattern = r"([a-z0-9]+\.b32\.i2p|[a-z0-9]+\.i2p)"
        formatted_msg = re.sub(address_pattern, r"[bold cyan]\1[/]", safe_message)

        content = styles.get(type_name, "{}").format(formatted_msg)

        if type_name in ["me", "peer"]:
            now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
            box_color = "green" if type_name == "me" else "cyan"
            display_name = "Me" if type_name == "me" else "Peer"
            alignment = "left" if type_name == "me" else "right"

            message_panel = Panel(
                f"[white]{formatted_msg}[/]",
                title=f"[#5f5f5f][{now_utc} UTC][/] [bold {box_color}]{display_name}[/]",
                title_align="left",
                border_style=box_color,
                box=box.ROUNDED,
                expand=False,
            )

            self.chat_log.write(Align(message_panel, align=alignment), expand=True)
        else:
            self.chat_log.write(content)

        # любое пользовательское действие / вывод можно считать поводом сбросить «непрочитанные»,
        # но сами счётчики увеличиваем только при входящих peer-сообщениях.

    # ----- callbacks из ядра -----

    def handle_status(self, status: str) -> None:
        self.network_status = status

    def handle_message(self, msg: ChatMessage) -> None:
        self.post(msg.kind, msg.text)

    def handle_system(self, text: str) -> None:
        self.post("system", text)

    def handle_error(self, text: str) -> None:
        self.post("error", text)

    def handle_file_event(self, info: FileTransferInfo) -> None:
        self.post(
            "system",
            f"File {info.filename}: {info.received}/{info.size} bytes",
        )

    def handle_image_received(self, art: str) -> None:
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        message_panel = Panel(
            art,
            title=f"[#5f5f5f][{now_utc} UTC][/] [bold cyan]Peer[/]",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
        self.chat_log.write(Align(message_panel, align="right"), expand=True)

    def handle_peer_changed(self, peer: Optional[str]) -> None:
        if peer:
            self.peer_b32 = peer

    # ----- жизненный цикл -----

    async def on_mount(self) -> None:
        self.network_status = "initializing"
        self.peer_b32 = "Initializing SAM Session..."

        self.post("system", f"Initializing Profile: [bold yellow]{self.profile}[/]")

        is_persistent = self.profile != "default"
        self.chat_log.write(
            f"[#878700]SYSTEM:[/] [dim #5f5f5f italic]Mode:[/][not bold "
            f"{'yellow' if is_persistent else 'green'}] "
            f"{'PERSISTENT' if is_persistent else 'TRANSIENT'}[/]"
        )

        await self.core.init_session()

    async def on_unmount(self) -> None:
        await self.core.shutdown()

    # ----- ввод пользователя -----

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.value = ""

        if msg.startswith("/connect"):
            parts = msg.split(" ", 1)
            if len(parts) > 1:
                target = parts[1].strip()
                self.post("system", f"Connecting to {target}...")
                self.run_worker(self.core.connect_to_peer(target))
            elif self.core.stored_peer:
                self.post("system", "Connecting to stored contact...")
                self.run_worker(self.core.connect_to_peer(self.core.stored_peer))
            else:
                self.post("error", "No stored contact. Use /connect <address>")

        elif msg.strip() == "/save":
            if self.profile == "default":
                self.post(
                    "error",
                    "Cannot save in [bold green]TRANSIENT[/] mode. "
                    "Restart with a profile name.",
                )
                return

            if self.core.stored_peer:
                self.post(
                    "error",
                    f"Profile already locked to: {self.core.stored_peer}...",
                )
                return

            if self.core.current_peer_addr:
                # Сохраняем .dat в той же директории профилей, что и ядро/GUI.
                base_dir = os.path.join(os.path.expanduser("~"), ".i2pchat")
                os.makedirs(base_dir, exist_ok=True)
                key_file = os.path.join(base_dir, f"{self.profile}.dat")
                try:
                    with open(key_file, "a") as f:
                        f.write(self.core.current_peer_addr + "\n")
                    self.core.stored_peer = self.core.current_peer_addr
                    self.post(
                        "success",
                        f"Identity [bold yellow]{self.profile}[/] "
                        "is now locked to this peer.",
                    )
                except Exception as e:
                    self.post("error", f"Failed to save: {e}")
            else:
                self.post("error", "Peer address not yet verified.")

        elif msg.startswith("/sendfile"):
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                self.post("error", "Usage: /sendfile <path>")
                return

            path = parts[1].strip()
            if not os.path.exists(path):
                self.post("error", "File not found.")
                return

            self.run_worker(self.core.send_file(path))

        elif msg.startswith("/img "):
            path = msg[5:].strip()
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return

            lines = render_braille(path)
            await self._send_image_me(lines)
            self.run_worker(self.core.send_image_lines(lines))

        elif msg.startswith("/img-bw "):
            path = msg[7:].strip()
            if not os.path.exists(path):
                self.post("error", f"File not found: {path}")
                return

            lines = render_bw(path)
            await self._send_image_me(lines)
            self.run_worker(self.core.send_image_lines(lines))

        elif msg.strip() == "/help":
            self.show_help()

        elif msg.strip() == "/disconnect":
            self.run_worker(self.core.disconnect())

        else:
            await self.core.send_text(msg)

    async def _send_image_me(self, lines) -> None:
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        img_text = "\n".join(lines)

        message_panel = Panel(
            img_text,
            title=f"[#5f5f5f][{now_utc} UTC][/] [bold green]Me[/]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )

        self.chat_log.write(Align(message_panel, align="left"), expand=True)

    # ----- help -----

    def show_help(self) -> None:
        self.post("help", "Available commands:")
        self.post("help", "Connection:")
        self.post("help", "  /connect <b32-address>   Connect to peer")
        self.post("help", "  /disconnect              Close connection")
        self.post("help", "Messaging:")
        self.post("help", "  Type text and press ENTER to send message")
        self.post("help", "Identity:")
        self.post("help", "  /save                    Save identity (not available in TRANSIENT mode)")
        self.post("help", "Files:")
        self.post("help", "  /sendfile <path>         Send file")
        self.post("help", "Images:")
        self.post("help", "  /img <path>              Send image (braille renderer)")
        self.post("help", "  /img-bw <path>           Send image (block renderer for QR / diagrams)")
        self.post("help", "Utility:")
        self.post("help", "  /help                    Show this help")
        self.post("help", "  /CTRL+q                  Exit program")


if __name__ == "__main__":
    app = I2PChat()
    app.run()