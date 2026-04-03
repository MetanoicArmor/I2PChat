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
_STANDALONE_NAME = "blindbox_service_standalone.py"
_FAIL2BAN_FILTER_NAME = "i2pchat-blindbox.conf"
_FAIL2BAN_JAIL_NAME = "jail.local.example"
_PROD_SYSTEMD_NAME = "i2pchat-blindbox.service"
_PROD_ENV_NAME = "daemon.env.example"
_PROD_INSTALL_NAME = "install_blindbox_daemon.sh"
_PROD_PACKAGE_NAME = "package_blindbox_daemon.sh"
_ONE_SHOT_INSTALL_NAME = "install.sh"
_ONE_SHOT_INSTALL_RAW_URL = (
    "https://raw.githubusercontent.com/MetanoicArmor/I2PChat/main/"
    "i2pchat/blindbox/daemon/install/install.sh"
)


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


def _read_asset_via_importlib_resources(*parts: str) -> Optional[str]:
    try:
        from importlib.resources import files
    except ImportError:
        return None
    try:
        candidate = files("i2pchat.blindbox")
        for part in parts:
            candidate = candidate.joinpath(part)
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


def resolve_bundled_asset_path(*parts: str) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, *parts)
    if os.path.isfile(candidate):
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        alt = os.path.join(meipass, "i2pchat", "blindbox", *parts)
        if os.path.isfile(alt):
            return alt
        alt2 = os.path.join(meipass, *parts)
        if os.path.isfile(alt2):
            return alt2
    return None


def _read_bundled_text(*parts: str) -> Optional[str]:
    path = resolve_bundled_asset_path(*parts)
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return _read_asset_via_importlib_resources(*parts)


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


def get_blindbox_standalone_launcher_source() -> str:
    text = _read_bundled_text(_STANDALONE_NAME)
    if text is not None:
        return text
    return f"# Example script {_STANDALONE_NAME} not found in the package install.\n"


def get_fail2ban_filter_example_source() -> str:
    text = _read_bundled_text("fail2ban", _FAIL2BAN_FILTER_NAME)
    if text is not None:
        return text
    return f"# Example file {_FAIL2BAN_FILTER_NAME} not found in the package install.\n"


def get_fail2ban_jail_example_source() -> str:
    text = _read_bundled_text("fail2ban", _FAIL2BAN_JAIL_NAME)
    if text is not None:
        return text
    return f"# Example file {_FAIL2BAN_JAIL_NAME} not found in the package install.\n"


def get_production_daemon_systemd_source() -> str:
    text = _read_bundled_text("daemon", "systemd", _PROD_SYSTEMD_NAME)
    if text is not None:
        return text
    return f"# Example file {_PROD_SYSTEMD_NAME} not found in the package install.\n"


def get_production_daemon_env_source() -> str:
    text = _read_bundled_text("daemon", "env", _PROD_ENV_NAME)
    if text is not None:
        return text
    return f"# Example file {_PROD_ENV_NAME} not found in the package install.\n"


def get_production_daemon_install_script_source() -> str:
    text = _read_bundled_text("daemon", "install", _PROD_INSTALL_NAME)
    if text is not None:
        return text
    return f"# Example file {_PROD_INSTALL_NAME} not found in the package install.\n"


def get_production_daemon_package_script_source() -> str:
    text = _read_bundled_text("daemon", "install", _PROD_PACKAGE_NAME)
    if text is not None:
        return text
    return f"# Example file {_PROD_PACKAGE_NAME} not found in the package install.\n"


def get_production_daemon_one_shot_install_source() -> str:
    text = _read_bundled_text("daemon", "install", _ONE_SHOT_INSTALL_NAME)
    if text is not None:
        return text
    return f"# Example file {_ONE_SHOT_INSTALL_NAME} not found in the package install.\n"


def get_production_daemon_one_shot_install_curl_command() -> str:
    return (
        f"curl -fsSL {_ONE_SHOT_INSTALL_RAW_URL} -o install.sh && "
        "sudo bash install.sh"
    )


def get_local_blindbox_server_example_note() -> str:
    return (
        "<b>1)</b> On the i2pd host, save this file as <code>.py</code> and keep it running. "
        "It listens on <code>127.0.0.1:19444</code> and now enforces TTL + storage quotas "
        "even when you keep the replica public / tokenless; it also exposes "
        "<code>PING</code> / <code>STATUS</code> / <code>METRICS</code> for simple health checks. "
        "<b>2)</b> i2pd: use the next tab, then in I2PChat add that tunnel&apos;s "
        "<code>*.b32.i2p:19444</code> under Blind Box diagnostics. "
        "<b>3)</b> Optional password: create <code>~/.i2pchat-blindbox/.env</code> as on the "
        "<code>.env</code> tab, and the same secret in Replica auth (Tab after the b32 address). "
        "For raw loopback/direct TCP keep a token; for a public I2P tunnel the token can stay empty. "
        "Admin/metrics commands can use a separate <code>BLINDBOX_ADMIN_TOKEN</code>."
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


def get_blindbox_standalone_launcher_note() -> str:
    return (
        "<b>Standalone wrapper:</b> copy this file next to <code>blindbox_server_example.py</code> "
        "when you want a cleaner service entrypoint for <code>systemd</code> or manual deployment. "
        "It first tries the sibling file, then falls back to the installed <code>i2pchat</code> package."
    )


def get_production_daemon_package_note() -> str:
    return (
        "<b>Production daemon package:</b> this is the supported package-local deployment path. "
        "Use <code>python3 -m i2pchat.blindbox.daemon</code> or point <code>systemd</code> to the "
        "same module. The package bundles a dedicated <code>systemd</code> unit, env example, and "
        "matching fail2ban assets, plus install/package helper scripts. "
        "If you want a single downloaded server installer, use <code>install.sh</code>."
    )


def get_systemd_blindbox_unit_example_source() -> str:
    return (
        "# /etc/systemd/system/blindbox.service\n"
        "# sudo systemctl daemon-reload && sudo systemctl enable --now blindbox.service\n"
        "# Optional TCP health / metrics check (swap STATUS_JSON with METRICS for Prometheus text):\n"
        "# /usr/bin/python3 -c \"import socket; s=socket.create_connection(('127.0.0.1',19444),2); s.sendall(b'STATUS_JSON\\n'); print(s.recv(4096).decode().strip()); s.close()\"\n"
        "# Optional localhost HTTP status endpoint: set BLINDBOX_HTTP_STATUS=1 and curl http://127.0.0.1:19445/healthz\n"
        "\n"
        "[Unit]\n"
        "Description=I2PChat Blind Box server\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=YOUR_LINUX_USER\n"
        "WorkingDirectory=%h\n"
        "EnvironmentFile=-%h/.i2pchat-blindbox/.env\n"
        "ExecStart=/usr/bin/python3 -u /path/to/blindbox_server_example.py\n"
        "Restart=always\n"
        "RestartSec=2\n"
        "UMask=0077\n"
        "NoNewPrivileges=yes\n"
        "PrivateTmp=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        "ReadWritePaths=%h/.i2pchat-blindbox\n"
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n"
        "LockPersonality=yes\n"
        "MemoryDenyWriteExecute=yes\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def get_systemd_blindbox_unit_example_note() -> str:
    return (
        "<b>Set <code>User=</code> and the <code>ExecStart</code> path to your <code>.py</code>.</b> "
        "This example adds a safer <code>systemd</code> sandbox and a strict "
        "<code>UMask=0077</code>. "
        "Put <code>BLINDBOX_AUTH_TOKEN</code> in <code>~/.i2pchat-blindbox/.env</code> (see "
        "<code>.env</code> tab) only if you want replica auth; public I2P replicas may leave it empty. "
        "Then <code>daemon-reload</code> and enable the service. "
        "Use <code>printf 'PING\\n'</code>, <code>printf 'STATUS\\n'</code>, "
        "<code>printf 'STATUS_JSON\\n'</code>, or <code>printf 'METRICS\\n'</code> "
        "against the local port for health / metrics checks "
        "(when auth is enabled, pass the token as <code>STATUS token</code> / "
        "<code>STATUS_JSON token</code> / <code>METRICS token</code>). "
        "For cleaner separation you can set a dedicated <code>BLINDBOX_ADMIN_TOKEN</code>. "
        "If <code>BLINDBOX_HTTP_STATUS=1</code> is enabled, the example also exposes "
        "<code>/healthz</code>, <code>/status.json</code>, and <code>/metrics</code> on localhost only. "
        "If you use a password, put the same secret in Blind Box diagnostics → Replica auth."
    )


def get_fail2ban_filter_example_note() -> str:
    return (
        "<b>Fail2ban filter:</b> matches the example service&apos;s "
        "<code>FAIL2BAN reason=...</code> audit lines for auth failures, HTTP auth failures, "
        "and rate-limit abuse."
    )


def get_fail2ban_jail_example_note() -> str:
    return (
        "<b>Fail2ban jail example:</b> point <code>logpath</code> to "
        "<code>~/.i2pchat-blindbox/audit.log</code>, adjust ports if you expose the optional "
        "localhost HTTP monitor, then copy the filter and jail example into your fail2ban config."
    )


def get_blindbox_dotenv_example_note() -> str:
    return (
        "<b>On the server, save this as <code>~/.i2pchat-blindbox/.env</code></b> "
        "(create the folder if needed, preferably <code>chmod 600</code>). "
        "The Python script reads it at startup. "
        "I2PChat does not use this file — set the same secret under Blind Box diagnostics → "
        "Replica auth for your <code>*.b32.i2p:19444</code> line (optional). "
        "Leaving the token empty is acceptable for a public I2P replica; keep it non-empty for raw TCP/loopback."
    )


def get_blindbox_dotenv_example_source() -> str:
    return (
        "# File: ~/.i2pchat-blindbox/.env\n"
        "# Optional for public I2P replicas; recommended for raw TCP / loopback.\n"
        "BLINDBOX_AUTH_TOKEN=\n"
        "# Optional separate secret for STATUS / STATUS_JSON / METRICS and localhost HTTP monitoring.\n"
        "BLINDBOX_ADMIN_TOKEN=\n"
        "BLINDBOX_MAX_TOTAL_BYTES=536870912\n"
        "BLINDBOX_MAX_FILES=4096\n"
        "BLINDBOX_MAX_PREFIX_BYTES=33554432\n"
        "BLINDBOX_MAX_PREFIX_FILES=256\n"
        "BLINDBOX_TTL_SEC=1209600\n"
        "BLINDBOX_RATE_LIMIT_PUTS_PER_MINUTE=240\n"
        "BLINDBOX_RATE_LIMIT_BYTES_PER_MINUTE=67108864\n"
        "BLINDBOX_AUDIT_LOG_MAX_BYTES=1048576\n"
        "BLINDBOX_AUDIT_LOG_BACKUPS=3\n"
        "BLINDBOX_HTTP_STATUS=0\n"
        "BLINDBOX_HTTP_HOST=127.0.0.1\n"
        "BLINDBOX_HTTP_PORT=19445\n"
        "# Optional exports:\n"
        "BLINDBOX_METRICS_JSON_PATH=\n"
        "BLINDBOX_METRICS_PROM_PATH=\n"
    )
