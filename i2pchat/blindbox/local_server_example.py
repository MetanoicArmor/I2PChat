"""
In-app Blind Box setup examples (installer-first deployment guidance).
"""

from __future__ import annotations

import os

_INSTALLER_NAME = "scripts/install_blindbox_replica.sh"
_INSTALLER_BASENAME = "install_blindbox_replica.sh"


def _resolve_repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def get_blindbox_installer_script_source() -> str:
    repo_root = _resolve_repo_root()
    path = os.path.join(repo_root, "scripts", _INSTALLER_BASENAME)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def get_blindbox_installer_script_basename() -> str:
    return _INSTALLER_BASENAME


def get_local_blindbox_server_example_source() -> str:
    return (
        "# Protected replica (recommended)\n"
        "chmod +x ./scripts/install_blindbox_replica.sh\n"
        "sudo ./scripts/install_blindbox_replica.sh \\\n"
        "  --user blindbox \\\n"
        "  --group blindbox \\\n"
        "  --service blindbox \\\n"
        "  --install-dir /opt/i2pchat-blindbox \\\n"
        "  --base-dir /var/lib/blindbox/.i2pchat-blindbox \\\n"
        "  --bind-host 127.0.0.1 \\\n"
        "  --port 19444 \\\n"
        "  --max-blob 1048576 \\\n"
        "  --ttl-sec 1209600 \\\n"
        "  --token 'CHANGE_ME_TO_A_LONG_RANDOM_SECRET'\n"
    )


def get_local_blindbox_server_example_note() -> str:
    return (
        "<p><b>Recommended path:</b> use the installer script "
        f"<code>{_INSTALLER_NAME}</code>.</p>"
        "<p>It installs the queue-only BlindBox server, writes the runtime "
        "<code>.env</code>, creates the systemd unit, and can optionally emit an "
        "i2pd tunnel snippet.</p>"
        "<p><b>How to use this tab:</b></p>"
        "<ol>"
        "<li>Click <b>Get install</b> to save the real installer script.</li>"
        "<li>Run the command from the box below on the server host.</li>"
        "<li>Replace the token placeholder with your own long random secret.</li>"
        "<li>Use the resulting <code>*.b32.i2p:19444</code> endpoint in Blind Box diagnostics.</li>"
        "<li>Put the same token into <b>Replica auth</b> for that endpoint in I2PChat.</li>"
        "</ol>"
        "<p><b>Tip:</b> this command assumes you already have the i2pd tunnel and only need "
        "to install or update the BlindBox replica service itself. If you really want a public "
        "replica without auth, you can replace <code>--token ...</code> with <code>--public</code>.</p>"
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
        "The server tunnel forwards I2P traffic to <code>127.0.0.1:19444</code>, where the "
        "queue-only BlindBox Python server listens. Use this tunnel&apos;s "
        "<code>*.b32.i2p:19444</code> in I2PChat (Blind Box diagnostics)."
    )


def get_systemd_blindbox_unit_example_source() -> str:
    return (
        "# /etc/systemd/system/blindbox.service\n"
        "# sudo systemctl daemon-reload && sudo systemctl enable --now blindbox.service\n"
        "\n"
        "[Unit]\n"
        "Description=I2PChat BlindBox replica\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=blindbox\n"
        "Group=blindbox\n"
        "WorkingDirectory=/opt/i2pchat-blindbox\n"
        "Environment=HOME=/var/lib/blindbox\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "ExecStart=/usr/bin/python3 /opt/i2pchat-blindbox/blindbox_server_example.py\n"
        "Restart=always\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def get_systemd_blindbox_unit_example_note() -> str:
    return (
        "<b>The installer writes this unit for you.</b> "
        "Use this tab only as a reference if you need to inspect or customize the generated "
        "service after installation."
    )


def get_blindbox_dotenv_example_note() -> str:
    return (
        "<b>The installer writes this file for you.</b> "
        "Use this tab only to inspect or tweak the generated runtime settings. "
        "Public mode does not need <code>BLINDBOX_AUTH_TOKEN</code>; only add it if you want "
        "a protected replica."
    )


def get_blindbox_dotenv_example_source() -> str:
    return (
        "# Example: /opt/i2pchat-blindbox/.env\n"
        "BLINDBOX_BASE=/var/lib/blindbox/.i2pchat-blindbox\n"
        "BLINDBOX_BIND_HOST=127.0.0.1\n"
        "BLINDBOX_PORT=19444\n"
        "BLINDBOX_MAX_BLOB=1048576\n"
        "BLINDBOX_TTL_SEC=1209600\n"
        "# Optional for a protected replica:\n"
        "# BLINDBOX_AUTH_TOKEN=\n"
    )
