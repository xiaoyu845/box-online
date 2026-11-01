"""Track and stop only processes owned by box-online (hang MC clients)."""

from __future__ import annotations

import logging
import os
import re
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

_LOG = logging.getLogger("box-online.procutil")

# ASCII markers — 游玩端 gameDir 在 versions\，不会带 instances\slot
_HANG_SLOT_ASCII = (
    "instances\\slot",
    "instances/slot",
    ".box-online\\instances\\slot",
    ".box-online/instances/slot",
)
def _names() -> tuple:
    """动态取挂机端/游玩端目录名（不再写死具体目录名）。"""
    try:
        from . import paths

        hang = paths.hang_root()
        play = paths.play_root()
        return (hang.name.lower() if hang else "", play.name.lower() if play else "")
    except Exception:  # noqa: BLE001
        return ("", "")


def _gamedir_of(cmd: str) -> str:
    """从命令行里取 --gameDir 的值（小写），取不到返回空串。"""
    m = re.search(r'--gamedir[\s=]+"?([^"]+)"?', cmd or "", re.I)
    return (m.group(1) if m else "").strip().lower()


def win_no_window_flags() -> int:
    """Windows: hide console for child processes (taskkill/cmd/java/…)."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def subprocess_no_window_kwargs() -> Dict[str, Any]:
    flags = win_no_window_flags()
    return {"creationflags": flags} if flags else {}


def prefer_javaw(java_path: str) -> str:
    """java.exe → sibling javaw.exe when present (no console window)."""
    raw = (java_path or "").strip().strip('"')
    if not raw:
        return java_path
    p = Path(raw)
    name = p.name.lower()
    if name == "javaw.exe":
        return str(p)
    if name == "java.exe":
        sibling = p.with_name("javaw.exe")
        try:
            if sibling.is_file():
                return str(sibling.resolve())
        except OSError:
            pass
    return raw


def _norm_cmd(cmd: str) -> str:
    return (cmd or "").lower().replace("/", "\\")


def _has_hang_slot_ascii(cmd: str) -> bool:
    low = _norm_cmd(cmd)
    return any(m in low for m in ("instances\\slot", ".box-online\\instances\\slot"))


def is_play_client_cmdline(cmd: str) -> bool:
    """True if this Java is the user's normal play install (示例服-1), not hang."""
    if not cmd:
        return False
    # 挂机槽位标记优先：绝不当游玩端
    if _has_hang_slot_ascii(cmd):
        return False
    low = _norm_cmd(cmd)
    if _names()[0] and _names()[0] in low:
        return False
    # 游玩端：目录名 示例服-1，且无 hang slot
    return bool(_names()[1]) and _names()[1] in low


def _is_hang_instance_cmdline(cmd: str) -> bool:
    """Per-slot hang instance (instances\\slotXX)."""
    if not cmd or is_play_client_cmdline(cmd):
        return False
    return _has_hang_slot_ascii(cmd)


def is_hang_client_cmdline(cmd: str) -> bool:
    """Any hang-side Forge client. Never play."""
    if not cmd or is_play_client_cmdline(cmd):
        return False
    if _has_hang_slot_ascii(cmd):
        return True
    low = _norm_cmd(cmd)
    if _names()[0] and _names()[0] in low and ("--gamedir" in low or "\\instances\\" in low):
        return True
    return False


def _decode_proc_out(raw: bytes) -> str:
    """WMIC on Chinese Windows often emits GBK; try several."""
    if not raw:
        return ""
    for enc in ("utf-8", "gb18030", "mbcs", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _cmdline_for_pid(pid: int, *, timeout_sec: float = 2.0) -> str:
    """Best-effort CommandLine for one PID (empty if unknown)."""
    pid = int(pid)
    if pid <= 0:
        return ""
    nw = subprocess_no_window_kwargs()
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/FORMAT:LIST",
            ],
            capture_output=True,
            timeout=timeout_sec,
            **nw,
        )
        text = _decode_proc_out(completed.stdout or b"")
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("CommandLine="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    try:
        ps = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\"; "
            "if($p){$p.CommandLine}"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True,
            timeout=timeout_sec,
            **nw,
        )
        return _decode_proc_out(completed.stdout or b"").strip()
    except Exception:
        return ""


def kill_pid_tree(pid: int) -> bool:
    """Kill one process and its children (Windows). Does not touch unrelated PIDs."""
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            **subprocess_no_window_kwargs(),
        )
        ok = completed.returncode == 0
        if ok:
            _LOG.info("killed process tree PID=%s", pid)
        else:
            _LOG.info(
                "taskkill PID=%s rc=%s %s",
                pid,
                completed.returncode,
                (completed.stderr or "")[:200],
            )
        return ok
    except Exception as exc:
        _LOG.warning("kill_pid_tree %s failed: %s", pid, exc)
        return False


def kill_pid_tree_hang_only(
    pid: int, *, cmd: Optional[str] = None, allow_unknown: bool = False
) -> bool:
    """Kill hang clients only — never 示例服-1.

    allow_unknown=True：本程序记下的 client_pid；只要不像游玩端就杀。
    """
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    cmdline = cmd if cmd is not None else _cmdline_for_pid(pid)
    if cmdline and is_play_client_cmdline(cmdline):
        _LOG.warning("refused kill play client PID=%s", pid)
        return False
    if cmdline and is_hang_client_cmdline(cmdline):
        return kill_pid_tree(pid)
    if allow_unknown:
        # 信任调度器记下的 PID：只要不是游玩端就杀（编码乱码时 cmdline 可能匹配失败）
        if cmdline and is_play_client_cmdline(cmdline):
            return False
        _LOG.info(
            "kill tracked PID=%s allow_unknown (cmdline_hang=%s snip=%s)",
            pid,
            bool(cmdline and is_hang_client_cmdline(cmdline)),
            (cmdline or "")[:80],
        )
        return kill_pid_tree(pid)
    if cmdline:
        _LOG.warning("skip kill PID=%s (not recognized as hang)", pid)
        return False
    _LOG.warning("skip kill PID=%s (no cmdline)", pid)
    return False


def kill_pids_fast(pids: Iterable[int]) -> int:
    """Kill known hang PIDs only — never play."""
    killed = 0
    seen: Set[int] = set()
    for pid in pids:
        if not pid or int(pid) <= 0:
            continue
        pid = int(pid)
        if pid in seen:
            continue
        seen.add(pid)
        if kill_pid_tree_hang_only(pid, allow_unknown=True):
            killed += 1
    return killed


def _java_cmdlines(*, timeout_sec: float = 3.0) -> List[tuple]:
    """Return list of (pid, cmdline) for java/javaw."""
    out: List[tuple] = []
    nw = subprocess_no_window_kwargs()
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='java.exe' or name='javaw.exe'",
                "get",
                "ProcessId,CommandLine",
                "/FORMAT:LIST",
            ],
            capture_output=True,
            timeout=timeout_sec,
            **nw,
        )
        text = _decode_proc_out(completed.stdout or b"")
        pid: Optional[int] = None
        cmd = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                if pid is not None:
                    out.append((pid, cmd))
                pid, cmd = None, ""
                continue
            if line.startswith("ProcessId="):
                try:
                    pid = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pid = None
            elif line.startswith("CommandLine="):
                cmd = line.split("=", 1)[1].strip()
        if pid is not None:
            out.append((pid, cmd))
        if out:
            return out
    except Exception as exc:
        _LOG.info("wmic java list failed, fallback powershell: %s", exc)

    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='java.exe' OR Name='javaw.exe'\" | "
        "ForEach-Object { '{0}`t{1}' -f $_.ProcessId, $_.CommandLine }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True,
            timeout=timeout_sec,
            **nw,
        )
        text = _decode_proc_out(completed.stdout or b"")
        for line in text.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            pid_s, cmd = line.split("\t", 1)
            try:
                out.append((int(pid_s), cmd or ""))
            except ValueError:
                continue
    except Exception as exc:
        _LOG.warning("list java failed: %s", exc)
    return out


_USERNAME_RE = re.compile(r"--username\s+(?P<u>[^\s\"]+)", re.IGNORECASE)


def parse_mc_username(cmd: str) -> str:
    m = _USERNAME_RE.search(cmd or "")
    return (m.group("u") if m else "").strip()


def detect_play_clients(*, timeout_sec: float = 3.0) -> List[Dict[str, Any]]:
    """Live 示例服-1 clients: [{pid, username, cmd_snip}, ...]."""
    out: List[Dict[str, Any]] = []
    for pid, cmd in _java_cmdlines(timeout_sec=timeout_sec):
        if not is_play_client_cmdline(cmd):
            continue
        user = parse_mc_username(cmd)
        out.append(
            {
                "pid": int(pid),
                "username": user,
                "cmd_snip": (cmd or "")[:160],
            }
        )
    return out


def play_client_usernames(*, timeout_sec: float = 3.0) -> Set[str]:
    return {
        str(x.get("username") or "").strip()
        for x in detect_play_clients(timeout_sec=timeout_sec)
        if x.get("username")
    }


def kill_hang_clients_for_slots(
    slot_indices: Iterable[int],
    extra_pids: Optional[Iterable[int]] = None,
    *,
    scan: bool = True,
    scan_timeout: float = 3.0,
) -> int:
    """
    Kill hang Forge clients for given slots.
    Always trusts extra_pids (unless play). Optional scan by instances\\slotXX.
    Never kills 示例服-1.
    """
    killed: Set[int] = set()
    for pid in extra_pids or []:
        if pid and int(pid) > 0 and kill_pid_tree_hang_only(int(pid), allow_unknown=True):
            killed.add(int(pid))

    if not scan:
        return len(killed)

    needles = [f"instances\\slot{int(idx):02d}" for idx in slot_indices]
    needles += [n.replace("\\", "/") for n in list(needles)]
    if not needles:
        return len(killed)

    for pid, cmd in _java_cmdlines(timeout_sec=scan_timeout):
        if pid in killed:
            continue
        if is_play_client_cmdline(cmd):
            continue
        if not _is_hang_instance_cmdline(cmd):
            continue
        low = _norm_cmd(cmd)
        if any(n.lower() in low for n in needles):
            if kill_pid_tree_hang_only(pid, cmd=cmd, allow_unknown=False):
                killed.add(pid)
    return len(killed)


def kill_all_hang_instance_clients(*, timeout_sec: float = 4.0) -> int:
    """Kill every hang Forge client. Never touches 示例服-1."""
    killed = 0
    for pid, cmd in _java_cmdlines(timeout_sec=timeout_sec):
        if is_play_client_cmdline(cmd):
            continue
        if is_hang_client_cmdline(cmd):
            if kill_pid_tree(pid):
                killed += 1
    time.sleep(0.4)
    for pid, cmd in _java_cmdlines(timeout_sec=max(2.0, timeout_sec * 0.6)):
        if is_play_client_cmdline(cmd):
            continue
        if is_hang_client_cmdline(cmd):
            if kill_pid_tree(pid):
                killed += 1
    return killed
