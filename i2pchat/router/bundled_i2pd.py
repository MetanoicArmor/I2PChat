from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from .runtime import is_tcp_open, pick_free_tcp_port, wait_for_sam_ready
from .settings import RouterSettings, router_runtime_dir


@dataclass
class BundledI2pdRuntime:
    sam_host: str
    sam_port: int
    http_proxy_port: int
    socks_proxy_port: int
    control_http_port: int
    data_dir: str
    conf_path: str
    tunconf_path: str
    log_path: str


def resolve_bundled_i2pd_binary() -> Optional[str]:
    rel = {
        "darwin": ("vendor", "i2pd", "darwin-arm64", "i2pd"),
        "win32": ("vendor", "i2pd", "windows-x64", "i2pd.exe"),
    }.get(sys.platform, ("vendor", "i2pd", "linux-x86_64", "i2pd"))

    candidates = []

    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root.joinpath(*rel))

    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        candidates.append(Path(meipass).joinpath(*rel))

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir.joinpath(*rel))
        candidates.append(exe_dir.joinpath("vendor", "i2pd", rel[-2], rel[-1]))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def render_i2pd_conf(rt: BundledI2pdRuntime) -> str:
    return f"""daemon = false
service = false

sam.enabled = true
sam.address = {rt.sam_host}
sam.port = {rt.sam_port}

http.enabled = true
http.address = 127.0.0.1
http.port = {rt.control_http_port}

httpproxy.enabled = true
httpproxy.address = 127.0.0.1
httpproxy.port = {rt.http_proxy_port}

socksproxy.enabled = true
socksproxy.address = 127.0.0.1
socksproxy.port = {rt.socks_proxy_port}

log = file
logfile = {rt.log_path}
"""


def render_tunnels_conf() -> str:
    return ""


class BundledI2pdManager:
    def __init__(self, settings: RouterSettings) -> None:
        self.settings = settings
        self._proc: asyncio.subprocess.Process | None = None
        self._runtime: BundledI2pdRuntime | None = None
        self._log_handle = None

    def sam_address(self) -> tuple[str, int]:
        if self._runtime is None:
            raise RuntimeError("Bundled i2pd is not initialized")
        return (self._runtime.sam_host, self._runtime.sam_port)

    def http_proxy_address(self) -> tuple[str, int]:
        if self._runtime is None:
            raise RuntimeError("Bundled i2pd is not initialized")
        return ("127.0.0.1", self._runtime.http_proxy_port)

    def log_path(self) -> str:
        if self._runtime is None:
            raise RuntimeError("Bundled i2pd is not initialized")
        return self._runtime.log_path

    def data_dir(self) -> str:
        if self._runtime is None:
            raise RuntimeError("Bundled i2pd is not initialized")
        return self._runtime.data_dir

    @staticmethod
    def _pick_preferred_or_free_port(host: str, preferred: int) -> int:
        if preferred > 0 and not is_tcp_open(host, preferred):
            return preferred
        return pick_free_tcp_port(host)

    def _build_runtime(self) -> BundledI2pdRuntime:
        root = router_runtime_dir()
        data_dir = os.path.join(root, "data")
        os.makedirs(data_dir, exist_ok=True)

        host = self.settings.bundled_sam_host
        sam_port = self._pick_preferred_or_free_port(host, int(self.settings.bundled_sam_port))
        http_proxy_port = self._pick_preferred_or_free_port("127.0.0.1", int(self.settings.bundled_http_proxy_port))
        socks_proxy_port = self._pick_preferred_or_free_port("127.0.0.1", int(self.settings.bundled_socks_proxy_port))
        control_http_port = self._pick_preferred_or_free_port("127.0.0.1", int(self.settings.bundled_control_http_port))

        return BundledI2pdRuntime(
            sam_host=host,
            sam_port=sam_port,
            http_proxy_port=http_proxy_port,
            socks_proxy_port=socks_proxy_port,
            control_http_port=control_http_port,
            data_dir=data_dir,
            conf_path=os.path.join(root, "i2pd.conf"),
            tunconf_path=os.path.join(root, "tunnels.conf"),
            log_path=os.path.join(root, "router.log"),
        )

    def _write_config(self, rt: BundledI2pdRuntime) -> None:
        Path(rt.conf_path).write_text(render_i2pd_conf(rt), encoding="utf-8")
        Path(rt.tunconf_path).write_text(render_tunnels_conf(), encoding="utf-8")

    @staticmethod
    def _build_launch_args(binary: str, rt: BundledI2pdRuntime) -> list[str]:
        return [
            binary,
            f"--datadir={rt.data_dir}",
            f"--conf={rt.conf_path}",
            f"--tunconf={rt.tunconf_path}",
        ]

    async def start(self) -> tuple[str, int]:
        if self._proc is not None and self._proc.returncode is None:
            return self.sam_address()

        binary = resolve_bundled_i2pd_binary()
        if not binary:
            raise RuntimeError("Bundled i2pd binary not found")

        rt = self._build_runtime()
        self._write_config(rt)
        self._runtime = rt

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._log_handle = open(rt.log_path, "ab")
        self._proc = await asyncio.create_subprocess_exec(
            *self._build_launch_args(binary, rt),
            stdout=self._log_handle,
            stderr=self._log_handle,
            creationflags=creationflags,
        )

        try:
            await wait_for_sam_ready(rt.sam_host, rt.sam_port, timeout=60.0)
        except Exception:
            await self.stop()
            raise

        return (rt.sam_host, rt.sam_port)

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        try:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        finally:
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None
