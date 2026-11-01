"""1.12.2 protocol bot through local proxy: login → play → walk portal → AFK → claim.

Proxy handles NetEase encryption to remote; client↔proxy is typically plaintext.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import claim as claim_mod
from .states import SlotState

_LOG = logging.getLogger("box-online.mc_bot")

# Protocol 340 (1.12.2) — play IDs
CB_KEEP_ALIVE = 0x1F
CB_PLUGIN = 0x18
CB_CHUNK = 0x20  # Chunk Data — 有区块才像真进世界
CB_JOIN_GAME = 0x23
CB_PLAYER_POS_LOOK = 0x2F
CB_DISCONNECT = 0x1A
CB_RESPAWN = 0x35  # 1.12.2 play: dimension change


def _parse_join_game_dim(payload: bytes) -> Optional[int]:
    """Join Game: int eid, ubyte gm, int dimension, ..."""
    if len(payload) < 4 + 1 + 4:
        return None
    return struct.unpack_from(">i", payload, 5)[0]


def _parse_respawn_dim(payload: bytes) -> Optional[int]:
    """Respawn: int dimension, ubyte difficulty, ubyte gm, string levelType."""
    if len(payload) < 4:
        return None
    return struct.unpack_from(">i", payload, 0)[0]


def _dim_label(dim: Optional[int]) -> str:
    if dim is None:
        return "?"
    names = {0: "主世界", -1: "下界", 1: "末地"}
    return names.get(dim, f"dim{dim}")

SB_TELEPORT_CONFIRM = 0x00
SB_CLIENT_SETTINGS = 0x04
SB_PLUGIN = 0x09
SB_KEEP_ALIVE = 0x0B
SB_POS = 0x0D
SB_POS_LOOK = 0x0E

# 示例服服务端相关假 ModList（不含客户端-only：Xaero/OptiFine/aw-* 等）
# 示例模组列表：需按目标服务端的实际模组填写，或用设置里的 forge_mods 覆盖。
# 不匹配时服务端会在握手阶段把连接判为原版客户端并断开。
DEFAULT_FORGE_MODS = (
    ("minecraft", "1.12.2"),
    ("mcp", "9.42"),
    ("FML", "8.0.99.99"),
    ("forge", "14.23.5.2860"),
    ("adventureworld", "5.0.0"),
    ("bpl", "1.0.1"),
    ("email", "1.0.1"),
    ("Furniture", "1.3.0"),
    ("shopblock", "1.1.0"),
)


def _normalize_forge_mods(raw) -> Tuple[Tuple[str, str], ...]:
    """Accept settings list of {modid,version} or [modid,version]; else default."""
    if not raw:
        return DEFAULT_FORGE_MODS
    out = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                mid = str(item.get("modid") or item.get("id") or "").strip()
                ver = str(item.get("version") or item.get("ver") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                mid, ver = str(item[0]).strip(), str(item[1]).strip()
            else:
                continue
            if mid and ver:
                out.append((mid, ver))
    return tuple(out) if out else DEFAULT_FORGE_MODS


def _varint(n: int) -> bytes:
    out = bytearray()
    nn = int(n) & 0xFFFFFFFF
    while True:
        b = nn & 0x7F
        nn >>= 7
        if nn:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pack_string(s: str) -> bytes:
    data = s.encode("utf-8")
    return _varint(len(data)) + data


def _read_varint_bytes(data: bytes, offset: int) -> Tuple[int, int]:
    num = 0
    for i in range(5):
        if offset + i >= len(data):
            raise ValueError("varint truncated")
        val = data[offset + i]
        num |= (val & 0x7F) << (7 * i)
        if not (val & 0x80):
            return num, offset + i + 1
    raise ValueError("varint too long")


def _read_string(data: bytes, offset: int) -> Tuple[str, int]:
    length, offset = _read_varint_bytes(data, offset)
    raw = data[offset : offset + length]
    return raw.decode("utf-8", errors="replace"), offset + length


class McConnection:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.compression_threshold = -1
        self.buf = bytearray()

    def send_packet(self, packet_id: int, payload: bytes = b"") -> None:
        body = _varint(packet_id) + payload
        if self.compression_threshold >= 0:
            if len(body) >= self.compression_threshold:
                compressed = zlib.compress(body)
                data = _varint(len(body)) + compressed
            else:
                data = _varint(0) + body
            packet = _varint(len(data)) + data
        else:
            packet = _varint(len(body)) + body
        self.sock.sendall(packet)

    def recv_packet(self, timeout: Optional[float] = None) -> Optional[Tuple[int, bytes]]:
        if timeout is not None:
            self.sock.settimeout(timeout)
        while True:
            length = self._read_varint_sock()
            if length is None:
                return None
            if length <= 0 or length > 2_000_000:
                return None
            data = self._recv_exact(length)
            if data is None:
                return None
            if self.compression_threshold >= 0:
                data_len, idx = _read_varint_bytes(data, 0)
                rest = data[idx:]
                if data_len == 0:
                    body = rest
                else:
                    body = zlib.decompress(rest)
            else:
                body = data
            pid, idx = _read_varint_bytes(body, 0)
            return pid, body[idx:]

    def _read_varint_sock(self) -> Optional[int]:
        num = 0
        for i in range(5):
            b = self._recv_exact(1)
            if b is None:
                return None
            val = b[0]
            num |= (val & 0x7F) << (7 * i)
            if not (val & 0x80):
                return num
        return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                if not buf:
                    raise
                continue
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


def _send_register(conn: McConnection, channels: Tuple[str, ...]) -> None:
    payload = b"\x00".join(c.encode("utf-8") for c in channels)
    conn.send_packet(SB_PLUGIN, _pack_string("REGISTER") + payload)


def _send_client_settings(conn: McConnection) -> None:
    # locale, viewDistance, chatMode, chatColors, skinParts, mainHand
    body = (
        _pack_string("zh_cn")
        + bytes([8])
        + _varint(0)
        + b"\x01"
        + bytes([0x7F])
        + _varint(1)
    )
    conn.send_packet(SB_CLIENT_SETTINGS, body)


def _send_brand(conn: McConnection) -> None:
    conn.send_packet(SB_PLUGIN, _pack_string("MC|Brand") + _pack_string("fml,forge"))


def _apply_pos_look(
    x: float, y: float, z: float, yaw: float, pitch: float, flags: int, payload: bytes
) -> Tuple[float, float, float, float, float, int]:
    nx, ny, nz = struct.unpack_from(">ddd", payload, 0)
    nyaw, npitch = struct.unpack_from(">ff", payload, 24)
    if flags & 0x01:
        x += nx
    else:
        x = nx
    if flags & 0x02:
        y += ny
    else:
        y = ny
    if flags & 0x04:
        z += nz
    else:
        z = nz
    if flags & 0x08:
        yaw += nyaw
    else:
        yaw = nyaw
    if flags & 0x10:
        pitch += npitch
    else:
        pitch = npitch
    tid = 0
    if len(payload) > 33:
        tid, _ = _read_varint_bytes(payload, 33)
    return x, y, z, yaw, pitch, tid


def _friendly_conn_err(exc: BaseException) -> str:
    msg = str(exc)
    if "10053" in msg or "10054" in msg or isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
        return "连接被中断（常见：模组列表不匹配 / 入口未就绪 / 被踢出）。可点重试；若反复失败请确认对接层仍在运行"
    if "10061" in msg:
        return "代理端口未监听，请等进服完成后再挂"
    return msg[:160]


def _yaw_toward(x: float, z: float, tx: float, tz: float) -> float:
    """Minecraft yaw: forward = (-sin(yaw), +cos(yaw)) in XZ."""
    return math.degrees(math.atan2(-(tx - x), tz - z))


def _step_toward(
    x: float, y: float, z: float, tx: float, tz: float, step: float
) -> Tuple[float, float, float, float, float]:
    """Move one step toward (tx,tz). Returns nx,y,nz,yaw,horiz_dist."""
    dx, dz = tx - x, tz - z
    dist = math.hypot(dx, dz)
    yaw = _yaw_toward(x, z, tx, tz)
    if dist < 1e-6:
        return x, y, z, yaw, 0.0
    move = min(step, dist)
    rad = math.radians(yaw)
    return x + (-math.sin(rad)) * move, y, z + math.cos(rad) * move, yaw, dist


def _mf_bot_js() -> Path:
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "mf_bot" / "bot.js")
    else:
        cands.append(Path(__file__).resolve().parent.parent / "mf_bot" / "bot.js")
    hang = os.environ.get("BOX_ONLINE_HANG_ROOT", "").strip()
    if hang:
        cands.append(Path(hang) / "mf_bot" / "bot.js")
    try:
        from .mc_client import hang_install_root

        cands.append(hang_install_root() / "mf_bot" / "bot.js")
    except Exception:
        from . import paths as _p
        _root = _p.hang_root()
        if _root is not None:
            cands.append(_root / "mf_bot" / "bot.js")
    seen = set()
    for path in cands:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and (path.parent / "node_modules" / "mineflayer").is_dir():
            return path
    raise RuntimeError(
        "未找到 Mineflayer 依赖。请确认挂机端目录下存在 mf_bot/node_modules"
    )


def _run_mineflayer_session(
    *,
    host: str,
    port: int,
    username: str,
    stop_flag: Callable[[], bool],
    on_state: Callable[[SlotState, str], None],
    claim_interval_sec: float,
    claim_now: Optional[Callable[[], bool]],
    on_online_ms: Optional[Callable[..., None]],
    forge_mods,
    portal_target: Tuple[float, float, float],
    join_delay_sec: float,
) -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未安装 Node.js，无法用 Mineflayer 寻路")
    script = _mf_bot_js()
    tx, ty, tz = portal_target
    mods = [{"modid": a, "version": b} for a, b in _normalize_forge_mods(forge_mods)]
    cfg = {
        "host": host,
        "port": int(port),
        "username": username,
        "tx": tx,
        "ty": ty,
        "tz": tz,
        "mods": mods,
        "claimIntervalSec": float(claim_interval_sec),
        "joinDelaySec": float(join_delay_sec),
    }
    cfg_path = script.parent / f"_cfg_{os.getpid()}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    on_state(SlotState.ENTERING_PORTAL, f"Mineflayer 连接 {host}:{port}")
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [node, str(script), str(cfg_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(script.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation,
    )
    lines: "queue.Queue[str]" = queue.Queue()

    def _read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    import threading

    threading.Thread(target=_read, name="mf-bot-out", daemon=True).start()
    last_detail = ""
    try:
        while True:
            if stop_flag():
                try:
                    if proc.stdin:
                        proc.stdin.write("STOP\n")
                        proc.stdin.flush()
                except OSError:
                    pass
                break
            if claim_now is not None:
                try:
                    want = bool(claim_now())
                except Exception:
                    want = False
                if want and proc.stdin:
                    try:
                        proc.stdin.write("CLAIM\n")
                        proc.stdin.flush()
                    except OSError:
                        pass
            try:
                line = lines.get(timeout=0.3)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            line = line.strip()
            if not line.startswith("MF "):
                if line:
                    _LOG.info("mf %s", line[:200])
                continue
            try:
                msg = json.loads(line[3:])
            except json.JSONDecodeError:
                continue
            state_name = str(msg.get("state") or "")
            detail = str(msg.get("detail") or "")
            state = SlotState.ENTERING_PORTAL
            for item in SlotState:
                if item.value == state_name:
                    state = item
                    break
            if detail and detail != last_detail:
                last_detail = detail
                on_state(state, detail)
            if on_online_ms and any(k in msg for k in ("x", "y", "z", "online_ms")):
                try:
                    on_online_ms(
                        msg.get("online_ms"),
                        msg.get("x"),
                        msg.get("y"),
                        msg.get("z"),
                        str(msg.get("phase") or ""),
                    )
                except Exception:
                    pass
            if state == SlotState.ERROR:
                raise RuntimeError(detail or "Mineflayer 异常")
        code = proc.poll()
        if code not in (0, None) and not stop_flag():
            raise RuntimeError(last_detail or f"Mineflayer 退出 code={code}")
    finally:
        try:
            cfg_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if cfg_path.is_file():
                cfg_path.unlink()
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def run_afk_session(
    *,
    host: str,
    port: int,
    username: str,
    stop_flag: Callable[[], bool],
    on_state: Callable[[SlotState, str], None],
    portal_timeout: float = 90.0,
    walk_min_sec: float = 6.0,
    protocol: int = 340,
    claim_interval_sec: float = 45.0,
    claim_now: Optional[Callable[[], bool]] = None,
    on_online_ms: Optional[Callable[..., None]] = None,
    forge_mods=None,
    portal_target: Optional[Tuple[float, float, float]] = None,
    join_delay_sec: float = 3.0,
) -> None:
    mods = _normalize_forge_mods(forge_mods)
    tx, ty, tz = portal_target if portal_target else (-1.0, 106.0, 8.0)
    join_delay = max(0.0, float(join_delay_sec))
    _run_mineflayer_session(
        host=host,
        port=port,
        username=username,
        stop_flag=stop_flag,
        on_state=on_state,
        claim_interval_sec=claim_interval_sec,
        claim_now=claim_now,
        on_online_ms=on_online_ms,
        forge_mods=mods,
        portal_target=(tx, ty, tz),
        join_delay_sec=join_delay,
    )
    return
    on_state(SlotState.ENTERING_PORTAL, f"连接 {host}:{port}")
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError as exc:
        raise RuntimeError(_friendly_conn_err(exc)) from exc
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn = McConnection(sock)

    x = y = z = 0.0
    yaw = 0.0
    pitch = 0.0
    on_ground = True
    in_play = False
    joined = False
    portal_done = False
    start_y: Optional[float] = None
    dimension: Optional[int] = None
    start_dim: Optional[int] = None
    walk_started_at: Optional[float] = None
    walk_deadline: float = 0.0
    saw_join_game = False
    chunk_count = 0
    adw_sync = False
    in_game_ok = False  # 强证据：维度变 / Y 降 / ADW 同步
    last_claim = 0.0
    claim_idx = 0
    last_walk = 0.0
    last_status_push = 0.0
    last_afk_pos = 0.0
    last_evidence_log = 0.0

    min_walk = max(6.0, float(walk_min_sec))
    max_walk = max(min_walk, float(portal_timeout))

    def _evidence() -> str:
        bits = []
        bits.append("JoinGame✓" if saw_join_game else "JoinGame✗")
        bits.append(f"区块{chunk_count}")
        bits.append(f"dim={_dim_label(dimension)}")
        bits.append("ADW✓" if adw_sync else "ADW✗")
        return " ".join(bits)

    def _mark_in_game(reason: str) -> None:
        nonlocal portal_done, in_game_ok
        in_game_ok = True
        portal_done = True
        if on_online_ms:
            try:
                on_online_ms(None, x, y, z, "portal")
            except Exception:
                pass
        on_state(
            SlotState.AFK,
            f"已进门({reason}) · 进门XYZ {x:.1f},{y:.1f},{z:.1f} · {_evidence()}",
        )

    try:
        # Handshake: host + FML marker so Forge 不当 VANILLA 踢
        addr = f"{host}\x00FML\x00"
        hs = _varint(protocol) + _pack_string(addr) + struct.pack(">H", int(port)) + _varint(2)
        conn.send_packet(0x00, hs)
        conn.send_packet(0x00, _pack_string(username))

        login_deadline = time.time() + 60.0
        while time.time() < login_deadline and not stop_flag() and not in_play:
            try:
                pkt = conn.recv_packet(timeout=2.0)
            except socket.timeout:
                continue
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
                raise RuntimeError(_friendly_conn_err(exc)) from exc
            if pkt is None:
                raise RuntimeError("登录阶段连接关闭（代理可能尚未就绪，请重试）")
            pid, payload = pkt
            if pid == 0x00:
                msg, _ = _read_string(payload, 0) if payload else ("?", 0)
                if "login servers" in msg.lower() or "contacting login" in msg.lower():
                    raise RuntimeError(
                        "代理鉴权失败（login servers）。同账号多角同时开会冲会话，请一次只挂一角后重试"
                    )
                raise RuntimeError(f"登录断开: {msg[:160]}")
            if pid == 0x01:
                raise RuntimeError("收到 Encryption Request（本地代理异常，应在代理侧处理加密）")
            if pid == 0x03:
                thr, _ = _read_varint_bytes(payload, 0)
                conn.compression_threshold = thr
                _LOG.info("compression threshold=%s", thr)
                continue
            if pid == 0x02:
                in_play = True
                on_state(SlotState.ENTERING_PORTAL, "进 Play，等待世界…")
                # Vanilla/Forge client announces channels ASAP
                try:
                    _send_register(
                        conn,
                        ("FML|HS", "FML", "FML|MP", "FORGE", "MC|Brand", claim_mod.PLUGIN_CHANNEL),
                    )
                    _send_client_settings(conn)
                    _send_brand(conn)
                    _send_fml_hello_modlist(conn, mods)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
                    raise RuntimeError(_friendly_conn_err(exc)) from exc
                break

        if not in_play:
            raise RuntimeError("登录超时未进入 Play")

        # 等坐标后再开始算进门走路时间（最少 walk_min_sec）
        wait_pos_deadline = time.time() + max(15.0, max_walk)
        while not stop_flag():
            try:
                pkt = conn.recv_packet(timeout=0.35)
            except socket.timeout:
                pkt = None
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
                raise RuntimeError(_friendly_conn_err(exc)) from exc

            if pkt is not None:
                pid, payload = pkt
                if pid == CB_DISCONNECT:
                    msg, _ = _read_string(payload, 0) if payload else ("?", 0)
                    raise RuntimeError(f"游戏断开: {msg[:160]}")
                elif pid == CB_KEEP_ALIVE:
                    if len(payload) >= 8:
                        conn.send_packet(SB_KEEP_ALIVE, payload[:8])
                elif pid == CB_CHUNK:
                    chunk_count += 1
                    if chunk_count == 1:
                        on_state(SlotState.ENTERING_PORTAL, f"收到区块数据 · {_evidence()}")
                elif pid == CB_JOIN_GAME:
                    joined = True
                    saw_join_game = True
                    dim = _parse_join_game_dim(payload)
                    if dim is not None:
                        dimension = dim
                        if start_dim is None:
                            start_dim = dim
                    on_state(
                        SlotState.ENTERING_PORTAL,
                        f"JoinGame · {_dim_label(dimension)}，等待坐标…",
                    )
                    try:
                        _send_client_settings(conn)
                        _send_brand(conn)
                    except OSError as exc:
                        raise RuntimeError(_friendly_conn_err(exc)) from exc
                elif pid == CB_RESPAWN:
                    dim = _parse_respawn_dim(payload)
                    if dim is not None:
                        old = dimension
                        dimension = dim
                        if start_dim is None:
                            start_dim = dim
                        on_state(
                            SlotState.ENTERING_PORTAL,
                            f"维度变化 {_dim_label(old)} → {_dim_label(dim)}",
                        )
                        if (
                            start_dim is not None
                            and dim != start_dim
                            and walk_started_at is not None
                            and (now := time.time()) - walk_started_at >= min_walk
                            and not in_game_ok
                        ):
                            _mark_in_game(f"维度 {_dim_label(start_dim)}→{_dim_label(dim)}")
                elif pid == CB_PLAYER_POS_LOOK:
                    if len(payload) >= 8 * 3 + 4 + 4 + 1:
                        flags = payload[32]
                        sx, sy, sz, syaw, spitch, tid = _apply_pos_look(
                            x, y, z, yaw, pitch, flags, payload
                        )
                        # 只确认传送。不要把服务器坐标再发回去，否则会把走路盖掉、人钉在出生点。
                        conn.send_packet(SB_TELEPORT_CONFIRM, _varint(tid))
                        if start_y is None:
                            x, y, z, yaw, pitch = sx, sy, sz, syaw, spitch
                            start_y = y
                            if start_dim is None:
                                start_dim = dimension
                            walk_started_at = time.time() + min(0.4, join_delay)
                            walk_deadline = walk_started_at + max_walk
                            yaw = _yaw_toward(x, z, tx, tz)
                            if on_online_ms:
                                try:
                                    on_online_ms(None, x, y, z, "join")
                                except Exception:
                                    pass
                            dist0 = math.hypot(tx - x, tz - z)
                            on_state(
                                SlotState.ENTERING_PORTAL,
                                f"进服 {x:.1f},{y:.1f},{z:.1f} · 前往世界之门 "
                                f"{tx:g},{ty:g},{tz:g} (距{dist0:.1f}) · {_evidence()}",
                            )
                        else:
                            gap = math.hypot(sx - x, sz - z)
                            # 只接受明显传送（进门/换层）；出生点反复下发不拉回
                            if abs(sy - y) > 1.5 or gap > 8.0:
                                x, y, z, yaw, pitch = sx, sy, sz, syaw, spitch
                        on_ground = True
                elif pid == CB_PLUGIN:
                    try:
                        ch, o = _read_string(payload, 0)
                        data = payload[o:]
                    except Exception:
                        ch, data = "", b""
                    if ch == "FML|HS":
                        _handle_fml_hs(conn, data, mods)
                    elif ch == claim_mod.PLUGIN_CHANNEL:
                        info = claim_mod.try_parse_sync(data)
                        if info:
                            adw_sync = True
                            oms = info.get("online_ms")
                            if on_online_ms:
                                try:
                                    on_online_ms(
                                        int(oms) if oms is not None else None,
                                        x,
                                        y,
                                        z,
                                        "portal" if portal_done else "join",
                                    )
                                except Exception:
                                    pass
                            if not in_game_ok:
                                _mark_in_game("收到ADW在线活动同步=真进服")
                            else:
                                from .claim import fmt_ms

                                on_state(
                                    SlotState.AFK,
                                    f"ADW同步 · 在线 {fmt_ms(oms)} · XYZ {x:.1f},{y:.1f},{z:.1f} · {_evidence()}",
                                )

            now = time.time()
            if now - last_evidence_log >= 5.0 and start_y is not None and not in_game_ok:
                last_evidence_log = now
                on_state(SlotState.ENTERING_PORTAL, f"证据体检 · {_evidence()}")

            if not portal_done:
                walked = (now - walk_started_at) if walk_started_at else 0.0
                horiz = math.hypot(tx - x, tz - z) if start_y is not None else 999.0
                # 走到门心（约 0.4 格内）才往下沉，站在框上不算进门
                at_center = horiz <= 0.45
                y_ok = start_y is not None and y < start_y - 2.0
                dim_ok = (
                    start_dim is not None
                    and dimension is not None
                    and dimension != start_dim
                )

                def _send_look(px: float, py: float, pz: float, ground: bool) -> None:
                    conn.send_packet(
                        SB_POS_LOOK,
                        struct.pack(">dddff?", px, py, pz, yaw, pitch, ground),
                    )

                def _walk_step(step: float, label: str) -> None:
                    nonlocal x, y, z, yaw, pitch, last_walk, last_status_push, on_ground
                    if now - last_walk < 0.05:
                        return
                    last_walk = now
                    if at_center:
                        # 锁到门心并下沉，让碰撞盒穿过传送门方块
                        x, z = tx, tz
                        y = max(ty - 1.2, y - 0.18)
                        yaw = _yaw_toward(x, z, tx, tz)
                        pitch = 80.0
                        on_ground = False
                        dist = 0.0
                        label = "门心下沉"
                    else:
                        x, y, z, yaw, dist = _step_toward(x, y, z, tx, tz, step)
                        pitch = 20.0
                        on_ground = True
                    try:
                        _send_look(x, y, z, on_ground)
                    except OSError as exc:
                        raise RuntimeError(_friendly_conn_err(exc)) from exc
                    if now - last_status_push >= 1.0:
                        last_status_push = now
                        on_state(
                            SlotState.ENTERING_PORTAL,
                            f"{label} 距门{dist:.1f} 走{max(0.0, walked):.1f}s "
                            f"XYZ {x:.1f},{y:.1f},{z:.1f} · {_evidence()}",
                        )

                # join_delay 未到：只对着门站着等
                if start_y is not None and walked < 0:
                    if now - last_status_push >= 1.0:
                        last_status_push = now
                        left = max(0.0, -walked)
                        on_state(
                            SlotState.ENTERING_PORTAL,
                            f"进门倒计时 {left:.1f}s · 目标 {tx:g},{ty:g},{tz:g} "
                            f"距{horiz:.1f} · {_evidence()}",
                        )
                        yaw = _yaw_toward(x, z, tx, tz)
                        try:
                            conn.send_packet(
                                SB_POS_LOOK,
                                struct.pack(">dddff?", x, y, z, yaw, 0.0, on_ground),
                            )
                        except OSError as exc:
                            raise RuntimeError(_friendly_conn_err(exc)) from exc
                elif start_y is not None and walked >= min_walk and (y_ok or dim_ok or adw_sync):
                    reason = [f"已走{walked:.1f}s"]
                    if adw_sync:
                        reason.append("ADW")
                    if y_ok:
                        reason.append(f"Y {start_y:.1f}→{y:.1f}")
                    if dim_ok:
                        reason.append(f"{_dim_label(start_dim)}→{_dim_label(dimension)}")
                    _mark_in_game(" / ".join(reason))
                elif start_y is not None and walked >= 0 and now < walk_deadline:
                    step = 0.12 if horiz < 2.5 else 0.20
                    label = "前往门心" if not at_center else "门心下沉"
                    _walk_step(step, label)
                elif start_y is None and now >= wait_pos_deadline:
                    portal_done = True
                    on_state(
                        SlotState.AFK,
                        f"未进服：无坐标，仅连上代理 · {_evidence()}",
                    )
                elif start_y is not None and now >= walk_deadline:
                    portal_done = True
                    on_state(
                        SlotState.AFK,
                        f"未确认游戏服(已走{max(0.0, walked):.1f}s 距门{horiz:.1f} "
                        f"无维度/Y/ADW证据) · {_evidence()}",
                    )

            # 仅真进服后才领奖；未确认则不空发
            do_claim = False
            if claim_now is not None:
                try:
                    do_claim = bool(claim_now())
                except Exception:
                    do_claim = False
            if portal_done and in_game_ok and (do_claim or now - last_claim >= claim_interval_sec):
                last_claim = now
                plan = claim_mod.claim_plan()
                if do_claim:
                    on_state(SlotState.CLAIMING, "手动领取在线…")
                    for slot_i, milestone in plan:
                        _send_adw_claim(conn, slot_i, milestone)
                        time.sleep(0.05)
                    on_state(SlotState.AFK, "已发送全部在线领取包")
                else:
                    slot_i, milestone = plan[claim_idx % len(plan)]
                    claim_idx += 1
                    on_state(SlotState.CLAIMING, f"领奖 slot={slot_i} milestone={milestone}")
                    _send_adw_claim(conn, slot_i, milestone)
                    on_state(SlotState.AFK, "挂机中 · 已尝试领奖")
            elif portal_done and do_claim and not in_game_ok:
                on_state(SlotState.AFK, f"未真进服，跳过领奖 · {_evidence()}")

            if portal_done and now - last_afk_pos >= 15.0:
                last_afk_pos = now
                try:
                    conn.send_packet(SB_POS, struct.pack(">ddd?", x, y, z, on_ground))
                except OSError as exc:
                    raise RuntimeError(_friendly_conn_err(exc)) from exc

            time.sleep(0.02)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _send_adw_claim(conn: McConnection, slot_index: int, milestone: bool) -> None:
    payload = claim_mod.build_claim_payload(slot_index, milestone=milestone)
    body = _pack_string(claim_mod.PLUGIN_CHANNEL) + payload
    try:
        conn.send_packet(SB_PLUGIN, body)
    except OSError as exc:
        raise RuntimeError(_friendly_conn_err(exc)) from exc


def _send_fml_hello_modlist(
    conn: McConnection, mods: Tuple[Tuple[str, str], ...]
) -> None:
    """主动发 ClientHello + ModList（进 Play 后立即冒充 Forge 客户端）。"""
    # disc 1 + FML protocol version byte (Forge 1.12 ClientHello)
    conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes([1, 2]))
    body = bytearray([2])
    body.extend(_varint(len(mods)))
    for mid, ver in mods:
        body.extend(_pack_string(mid))
        body.extend(_pack_string(ver))
    conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes(body))
    _LOG.info("FML|HS ModList sent (%s mods)", len(mods))


def _handle_fml_hs(
    conn: McConnection, data: bytes, mods: Tuple[Tuple[str, str], ...]
) -> None:
    """FML|HS: 响应服务端 ServerHello / ModList / Registry / Ack。"""
    if not data:
        return
    disc = data[0]
    try:
        if disc == 0:
            # ServerHello → ClientHello + ModList
            _send_fml_hello_modlist(conn, mods)
        elif disc == 2:
            # Server ModList done → WaitingServerData ack phase 2
            conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes([255, 2]))
        elif disc == 3:
            has_more = True
            if len(data) > 1:
                has_more = bool(data[1]) if len(data) == 2 else True
            conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes([255, 3]))
            if not has_more:
                conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes([255, 4]))
        elif disc == 255:
            phase = data[1] if len(data) > 1 else 0
            if phase < 5:
                conn.send_packet(SB_PLUGIN, _pack_string("FML|HS") + bytes([255, max(2, phase)]))
    except OSError as exc:
        _LOG.warning("FML|HS send failed: %s", exc)
