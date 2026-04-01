"""
In-app Blind Box setup examples (Python / i2pd / systemd / .env reference).

Source file: ``i2pchat/blindbox/blindbox_server_example.py`` (same directory).
PyInstaller: may also ship as ``blindbox_server_example.py`` under ``_MEIPASS``.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

_EXAMPLE_NAME = "blindbox_server_example.py"


def _read_example_via_importlib_resources() -> Optional[str]:
    """Works for installs where the file is package data (and some frozen layouts)."""
    try:
        from importlib.resources import files
    except ImportError:
        return None
    try:
        candidate = files("i2pchat.blindbox").joinpath(_EXAMPLE_NAME)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except (OSError, TypeError, FileNotFoundError, ModuleNotFoundError, ValueError):
        pass
    return None


def resolve_bundled_example_path() -> Optional[str]:
    """Package file next to this module, or same name under ``_MEIPASS``."""
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, _EXAMPLE_NAME)
    if os.path.isfile(p):
        return p
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        alt = os.path.join(meipass, _EXAMPLE_NAME)
        if os.path.isfile(alt):
            return alt
    return None


def get_local_blindbox_server_example_source() -> str:
    path = resolve_bundled_example_path()
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    embedded = _read_example_via_importlib_resources()
    if embedded is not None:
        return embedded
    return (
        f"# Example script {_EXAMPLE_NAME} not found in the package install.\n"
    )


def get_local_blindbox_server_example_note() -> str:
    return (
        "<b>1)</b> On the i2pd host, save this file as <code>.py</code> and keep it running. "
        "It listens on <code>127.0.0.1:19444</code>. "
        "<b>2)</b> i2pd: use the next tab, then in I2PChat add that tunnel&apos;s "
        "<code>*.b32.i2p:19444</code> under Blind Box diagnostics. "
        "<b>3)</b> Optional password: create <code>~/.i2pchat-blindbox/.env</code> as on the "
        "<code>.env</code> tab, and the same secret in Replica auth (Tab after the b32 address)."
    )


def get_i2pd_blindbox_tunnel_example_source() -> str:
    return (
        "# /etc/i2pd/tunnels.conf (merge + restart i2pd; set keys= to your .dat)\n"
        "\n"
        "[blindbox]\n"
        "type = server\n"
        "host = 127.0.0.1\n"
        "port = 19444\n"
        "keys = blindbox.dat\n"
        "inport = 19444\n"
    )


def get_i2pd_blindbox_tunnel_example_note() -> str:
    return (
        "<b>Merge into <code>tunnels.conf</code>, restart i2pd.</b> "
        "I2P traffic hits <code>127.0.0.1:19444</code> where the Python server listens. "
        "Use this tunnel&apos;s <code>*.b32.i2p:19444</code> in I2PChat (Blind Box diagnostics)."
    )


def get_systemd_blindbox_unit_example_source() -> str:
    return (
        "# /etc/systemd/system/blindbox.service\n"
        "# sudo systemctl daemon-reload && sudo systemctl enable --now blindbox.service\n"
        "\n"
        "[Unit]\n"
        "Description=I2PChat Blind Box server\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=YOUR_LINUX_USER\n"
        "ExecStart=/usr/bin/python3 /path/to/blindbox_server_example.py\n"
        "Restart=always\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def get_systemd_blindbox_unit_example_note() -> str:
    return (
        "<b>Set <code>User=</code> and the <code>ExecStart</code> path to your <code>.py</code>.</b> "
        "Put <code>BLINDBOX_AUTH_TOKEN</code> in <code>~/.i2pchat-blindbox/.env</code> (see "
        "<code>.env</code> tab) — the script loads it. "
        "Then <code>daemon-reload</code> and enable the service. "
        "Same secret in Blind Box diagnostics → Replica auth if you use a password."
    )


def get_blindbox_dotenv_example_note() -> str:
    return (
        "<b>On the server, save this as <code>~/.i2pchat-blindbox/.env</code></b> "
        "(create the folder if needed). The Python script reads it at startup. "
        "I2PChat does not use this file — set the same secret under Blind Box diagnostics → "
        "Replica auth for your <code>*.b32.i2p:19444</code> line (optional)."
    )


def get_blindbox_dotenv_example_source() -> str:
    return (
        "# File: ~/.i2pchat-blindbox/.env\n"
        "BLINDBOX_AUTH_TOKEN=\n"
    )
