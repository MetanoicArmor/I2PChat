from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
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
    pidfile_path: str


_STATE_FILE = "managed-process.json"


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


def _ps_single_quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


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
        self._managed_pid: int | None = None

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

    @staticmethod
    def _runtime_root(rt: BundledI2pdRuntime) -> str:
        return os.path.dirname(rt.conf_path)

    @staticmethod
    def _state_path(root: str) -> str:
        return os.path.join(root, _STATE_FILE)

    @staticmethod
    def _runtime_to_dict(rt: BundledI2pdRuntime) -> dict[str, object]:
        return {
            "sam_host": rt.sam_host,
            "sam_port": rt.sam_port,
            "http_proxy_port": rt.http_proxy_port,
            "socks_proxy_port": rt.socks_proxy_port,
            "control_http_port": rt.control_http_port,
            "data_dir": rt.data_dir,
            "conf_path": rt.conf_path,
            "tunconf_path": rt.tunconf_path,
            "log_path": rt.log_path,
            "pidfile_path": rt.pidfile_path,
        }

    @staticmethod
    def _runtime_from_dict(raw: dict[str, object]) -> Optional[BundledI2pdRuntime]:
        try:
            return BundledI2pdRuntime(
                sam_host=str(raw["sam_host"]),
                sam_port=int(raw["sam_port"]),
                http_proxy_port=int(raw["http_proxy_port"]),
                socks_proxy_port=int(raw["socks_proxy_port"]),
                control_http_port=int(raw["control_http_port"]),
                data_dir=str(raw["data_dir"]),
                conf_path=str(raw["conf_path"]),
                tunconf_path=str(raw["tunconf_path"]),
                log_path=str(raw["log_path"]),
                pidfile_path=str(raw.get("pidfile_path") or os.path.join(os.path.dirname(str(raw["conf_path"])), "i2pd.pid")),
            )
        except Exception:
            return None

    def _read_state(
        self, root: str
    ) -> tuple[Optional[BundledI2pdRuntime], Optional[int], Optional[int]]:
        path = self._state_path(root)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return None, None, None
            rt_raw = raw.get("runtime")
            runtime = (
                self._runtime_from_dict(rt_raw)
                if isinstance(rt_raw, dict)
                else None
            )
            pid_raw = raw.get("pid")
            pid = int(pid_raw) if pid_raw is not None else None
            owner_raw = raw.get("owner_pid")
            owner_pid = int(owner_raw) if owner_raw is not None else None
            return runtime, pid, owner_pid
        except Exception:
            return None, None, None

    def _write_state(
        self, rt: BundledI2pdRuntime, pid: Optional[int], owner_pid: Optional[int] = None
    ) -> None:
        root = self._runtime_root(rt)
        os.makedirs(root, exist_ok=True)
        payload = {
            "runtime": self._runtime_to_dict(rt),
            "pid": pid,
            "owner_pid": owner_pid,
        }
        with open(self._state_path(root), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2, sort_keys=True)

    def _clear_state(self, root: str) -> None:
        try:
            os.remove(self._state_path(root))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _terminate_pid_sync(pid: int) -> None:
        if pid <= 0:
            return
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception:
                pass
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    async def _terminate_pid(self, pid: int, *, timeout: float = 10.0) -> None:
        if not self._pid_alive(pid):
            return
        if sys.platform == "win32":
            self._terminate_pid_sync(pid)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                if not self._pid_alive(pid):
                    return
                await asyncio.sleep(0.2)
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not self._pid_alive(pid):
                return
            await asyncio.sleep(0.2)
        force_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
        try:
            os.kill(pid, force_sig)
        except OSError:
            return
        deadline = loop.time() + 3.0
        while loop.time() < deadline:
            if not self._pid_alive(pid):
                return
            await asyncio.sleep(0.2)

    @staticmethod
    def _read_pidfile(path: str) -> Optional[int]:
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
            pid = int(raw)
            return pid if pid > 0 else None
        except Exception:
            return None

    @staticmethod
    def _discover_windows_runtime_pid(rt: BundledI2pdRuntime) -> Optional[int]:
        if sys.platform != "win32":
            return None
        conf = _ps_single_quoted(rt.conf_path)
        data = _ps_single_quoted(rt.data_dir)
        script = (
            f"$conf={conf}; "
            f"$data={data}; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.Name -eq 'i2pd.exe' -and $_.CommandLine -and "
            "(($_.CommandLine -like ('*' + $conf + '*')) -or ($_.CommandLine -like ('*' + $data + '*'))) "
            "} | "
            "Sort-Object ProcessId -Descending | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creationflags,
            ).strip()
            pid = int(out) if out else None
            return pid if pid and pid > 0 else None
        except Exception:
            return None

    @staticmethod
    def _spawn_windows_delayed_cleanup(rt: BundledI2pdRuntime) -> None:
        if sys.platform != "win32":
            return
        conf = _ps_single_quoted(rt.conf_path)
        data = _ps_single_quoted(rt.data_dir)
        script = (
            f"$conf={conf}; "
            f"$data={data}; "
            "Start-Sleep -Milliseconds 1500; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.Name -eq 'i2pd.exe' -and $_.CommandLine -and "
            "(($_.CommandLine -like ('*' + $conf + '*')) -or ($_.CommandLine -like ('*' + $data + '*'))) "
            "} | "
            "ForEach-Object { "
            "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} ; "
            "try { taskkill /PID $($_.ProcessId) /T /F | Out-Null } catch {} "
            "}"
        )
        creationflags = 0
        for attr in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= int(getattr(subprocess, attr, 0))
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception:
            pass

    @staticmethod
    def _spawn_windows_parent_exit_cleanup(
        rt: BundledI2pdRuntime, owner_pid: int
    ) -> None:
        if sys.platform != "win32" or owner_pid <= 0:
            return
        conf = _ps_single_quoted(rt.conf_path)
        data = _ps_single_quoted(rt.data_dir)
        state = _ps_single_quoted(
            os.path.join(os.path.dirname(rt.conf_path), _STATE_FILE)
        )
        script = (
            f"$owner={owner_pid}; "
            f"$conf={conf}; "
            f"$data={data}; "
            f"$state={state}; "
            "while (Get-Process -Id $owner -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 500 } "
            "Start-Sleep -Milliseconds 1500; "
            "$shouldKill = $true; "
            "if (Test-Path $state) { "
            "try { "
            "$raw = Get-Content $state -Raw | ConvertFrom-Json; "
            "if ($raw.owner_pid -and [int]$raw.owner_pid -ne $owner) { $shouldKill = $false } "
            "} catch {} "
            "} "
            "if (-not $shouldKill) { exit 0 } "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.Name -eq 'i2pd.exe' -and $_.CommandLine -and "
            "(($_.CommandLine -like ('*' + $conf + '*')) -or ($_.CommandLine -like ('*' + $data + '*'))) "
            "} | "
            "ForEach-Object { "
            "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} ; "
            "try { taskkill /PID $($_.ProcessId) /T /F | Out-Null } catch {} "
            "}"
        )
        creationflags = 0
        for attr in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= int(getattr(subprocess, attr, 0))
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception:
            pass

    @classmethod
    def force_cleanup_runtime_root(cls, root: Optional[str] = None) -> None:
        root = root or router_runtime_dir()
        runtime, pid, _owner_pid = cls(RouterSettings())._read_state(root)
        if runtime is None:
            runtime = cls._infer_runtime_from_existing_conf(root)
        pidfile_path = (
            runtime.pidfile_path
            if runtime is not None
            else os.path.join(root, "i2pd.pid")
        )
        if pid is None:
            pid = cls._read_pidfile(pidfile_path)
        if pid is None and runtime is not None:
            pid = cls._discover_windows_runtime_pid(runtime)
        if pid is not None:
            cls._terminate_pid_sync(pid)
        if runtime is not None:
            cls._spawn_windows_delayed_cleanup(runtime)
        try:
            os.remove(os.path.join(root, _STATE_FILE))
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            os.remove(pidfile_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _infer_runtime_from_existing_conf(root: str) -> Optional[BundledI2pdRuntime]:
        conf_path = os.path.join(root, "i2pd.conf")
        if not os.path.isfile(conf_path):
            return None
        values: dict[str, str] = {}
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    values[key.strip()] = value.strip()
            return BundledI2pdRuntime(
                sam_host=values.get("sam.address", "127.0.0.1"),
                sam_port=int(values["sam.port"]),
                http_proxy_port=int(values["httpproxy.port"]),
                socks_proxy_port=int(values["socksproxy.port"]),
                control_http_port=int(values["http.port"]),
                data_dir=os.path.join(root, "data"),
                conf_path=conf_path,
                tunconf_path=os.path.join(root, "tunnels.conf"),
                log_path=values.get("logfile", os.path.join(root, "router.log")),
                pidfile_path=os.path.join(root, "i2pd.pid"),
            )
        except Exception:
            return None

    async def _adopt_existing_runtime_if_available(self, root: str) -> bool:
        runtime, pid, _owner_pid = self._read_state(root)
        if runtime is not None:
            if pid is not None and self._pid_alive(pid):
                try:
                    await wait_for_sam_ready(
                        runtime.sam_host, runtime.sam_port, timeout=5.0
                    )
                    self._runtime = runtime
                    self._managed_pid = pid
                    self._write_state(runtime, pid, os.getpid())
                    self._spawn_windows_parent_exit_cleanup(runtime, os.getpid())
                    return True
                except Exception:
                    await self._terminate_pid(pid)
                    self._clear_state(root)
            else:
                self._clear_state(root)

        inferred = self._infer_runtime_from_existing_conf(root)
        if inferred is not None:
            try:
                await wait_for_sam_ready(
                    inferred.sam_host, inferred.sam_port, timeout=5.0
                )
                self._runtime = inferred
                self._managed_pid = self._discover_windows_runtime_pid(inferred)
                self._write_state(inferred, self._managed_pid, os.getpid())
                self._spawn_windows_parent_exit_cleanup(inferred, os.getpid())
                return True
            except Exception:
                pass
        return False

    def _build_runtime(self, root: Optional[str] = None) -> BundledI2pdRuntime:
        root = root or router_runtime_dir()
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
            pidfile_path=os.path.join(root, "i2pd.pid"),
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
            f"--pidfile={rt.pidfile_path}",
        ]

    async def start(self) -> tuple[str, int]:
        if self._proc is not None and self._proc.returncode is None:
            return self.sam_address()
        if self._runtime is not None and self._managed_pid is not None and self._pid_alive(self._managed_pid):
            return self.sam_address()

        binary = resolve_bundled_i2pd_binary()
        if not binary:
            raise RuntimeError("Bundled i2pd binary not found")

        root = router_runtime_dir()
        if await self._adopt_existing_runtime_if_available(root):
            return self.sam_address()

        rt = self._build_runtime(root)
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

        self._managed_pid = self._read_pidfile(rt.pidfile_path)
        if self._managed_pid is None:
            self._managed_pid = self._discover_windows_runtime_pid(rt)
        if self._managed_pid is None:
            self._managed_pid = self._proc.pid if self._proc is not None else None
        self._write_state(rt, self._managed_pid, os.getpid())
        self._spawn_windows_parent_exit_cleanup(rt, os.getpid())

        return (rt.sam_host, rt.sam_port)

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        runtime = self._runtime
        root = self._runtime_root(runtime) if runtime is not None else router_runtime_dir()
        target_pid = self._managed_pid
        if target_pid is None and proc is not None:
            target_pid = proc.pid
        try:
            if target_pid is not None:
                await self._terminate_pid(target_pid)
            elif proc is not None and proc.returncode is None:
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
            still_alive = bool(target_pid is not None and self._pid_alive(target_pid))
            if still_alive and runtime is not None:
                self._managed_pid = target_pid
                self._write_state(runtime, target_pid, os.getpid())
                self._runtime = None
            else:
                self._managed_pid = None
                self._runtime = None
                self._clear_state(root)
                if runtime is not None:
                    try:
                        os.remove(runtime.pidfile_path)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
