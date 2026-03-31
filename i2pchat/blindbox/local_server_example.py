"""
In-app Blind Box server example (no secrets).

Source file: ``i2pchat/blindbox/blindbox_server_example.py`` (same directory).
PyInstaller: may also ship as ``blindbox_server_example.py`` under ``_MEIPASS``.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

_EXAMPLE_NAME = "blindbox_server_example.py"


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
    return (
        f"# Example script {_EXAMPLE_NAME} not found in the package install.\n"
    )


def get_local_blindbox_server_example_note() -> str:
    return (
        "Tiny TCP server on 127.0.0.1:19444 — save the code below as a .py file and run it. "
        "In I2PChat use replica 127.0.0.1:19444, set I2PCHAT_BLINDBOX_LOCAL_FALLBACK=1 "
        "(and I2PCHAT_BLINDBOX_LOCAL_TOKEN if the app asks). Loopback only."
    )


def get_i2pd_blindbox_tunnel_example_source() -> str:
    return (
        "# /etc/i2pd/tunnels.conf — add or merge this block; restart i2pd after edits.\n"
        "# Point `keys` at a destination .dat you generated for this service.\n"
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
        "i2pd forwards I2P to the same 127.0.0.1:19444 server as the Python tab. "
        "Merge the block, restart i2pd, create keys if needed, then add "
        "<your>.b32.i2p:19444 as a replica in I2PChat."
    )


def get_systemd_blindbox_unit_example_source() -> str:
    return (
        "# /etc/systemd/system/blindbox.service\n"
        "# Replace YOUR_LINUX_USER and the script path; then:\n"
        "#   sudo systemctl daemon-reload && sudo systemctl enable --now blindbox.service\n"
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
        "Runs the Python server from the first tab under systemd. "
        "Copy `blindbox_server_example.py` to the path in ExecStart, fix User=, "
        "install the unit, then daemon-reload and enable the service."
    )
