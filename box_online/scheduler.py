from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .accounts import Account, AccountStore
from .bridge import BridgeError, CaptchaNeeded, load_bridge_client, pick_lost_world
from .states import CAN_START, SlotState

_LOG = logging.getLogger("box-online.scheduler")

# 两次 4399 密码登录之间的最小间隔（秒）。实测 5~24 秒间隔连成 4 次都没问题，
# 而"同时一拥而上"必吃 202。这里取 3 秒把请求铺开。
_MIN_LOGIN_GAP_SEC = 3.0
_LAST_LOGIN_AT = 0.0

StatusCb = Callable[[int, SlotState, str], None]
PickCharCb = Callable[[int, Account, list], Optional[str]]
EmbedCb = Callable[[int, int], None]  # slot_index, hwnd


def _fmt_duration(sec: float) -> str:
    s = max(0, int(sec))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


_CAPTCHA_MARKERS = ("验证码", "captcha")


def _is_captcha_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in _CAPTCHA_MARKERS)


def _label_account(label: str) -> str:
    """对接层的会话 label 形如 '4399com / <账号>' 或 'netease / <邮箱>'。"""
    txt = str(label or "")
    if "/" in txt:
        return txt.rsplit("/", 1)[-1].strip().lower()
    return txt.strip().lower()


def sessions_for_account(bridge, account: str) -> List[str]:
    """该账号已有的会话 id 列表（可能为空）。

    用来做两件相反的事：
      · **有**会话时复用它 —— 不重登就不会产生"同账号两条连接"；
      · 真要重登时先把它们 logout —— 确保服务端那条连接真的断了。
    """
    want = (account or "").strip().lower()
    if not want:
        return []
    out: List[str] = []
    try:
        for s in bridge.sessions() or []:
            if _label_account(s.get("label")) == want:
                sid = str(s.get("id") or s.get("session_id") or "")
                if sid:
                    out.append(sid)
    except BridgeError:
        pass
    return out


def _gated_password_login(
    bridge,
    method: str,
    account: str,
    password: str,
    sauth_text: str = "",
    captcha_id: str = "",
    captcha_code: str = "",
    *,
    api_lock=None,
    login_lock=None,
) -> Dict[str, Any]:
    """4399 密码登录：**登录之间串行 + 最小间隔**，吃到 202 只让这一次短重试。

    实测（2026-10-16）4399 限的是"短时间内的频率"：
      · 单个请求、安静 8 分钟后 -> HTTP 200，0.7 秒
      · 4 次登录间隔 5~24 秒    -> 4 次全成功
      · 7 个槽**同时**登        -> 绝大多数吃 202（「请稍后再试。」）

    所以：**排队 + 间隔 + 几秒重试**，而不是全局封锁，也不是一拥而上。

    锁的用法很关键：
      · `login_lock`：登录之间串行（防止一堆槽同时撞上去），持有一段**正常**的时间
      · `api_lock`  ：只在真正发请求的那一瞬间持有；**sleep 重试时不持有**
        —— 否则一个账号等重试会把全程序所有对接层调用堵死
        （实际发生过：15 个槽全卡在「登录中」不动）
    """
    from .gate import RETRY_WAITS, shared
    from .sauth import is_rate_limited

    def _once() -> Dict[str, Any]:
        if method != "4399":
            return bridge.login(
                method,
                account=account,
                password=password,
                sauth=sauth_text,
                captcha_id=captcha_id,
                captcha_code=captcha_code,
            )
        gate = shared()
        last: Optional[BaseException] = None
        for wait in (0.0,) + tuple(RETRY_WAITS):
            if wait:
                time.sleep(wait)  # 注意：这里**不持有** api_lock
            try:
                if api_lock is not None:
                    with api_lock:
                        resp = bridge.login(
                            method,
                            account=account,
                            password=password,
                            sauth=sauth_text,
                            captcha_id=captcha_id,
                            captcha_code=captcha_code,
                        )
                else:
                    resp = bridge.login(
                        method,
                        account=account,
                        password=password,
                        sauth=sauth_text,
                        captcha_id=captcha_id,
                        captcha_code=captcha_code,
                    )
            except BridgeError as exc:
                last = exc
                if is_rate_limited(exc):
                    gate.note_rate_limited(str(exc))
                    continue
                gate.note_failure()
                raise
            # 统计：验证码算"请求被用掉"，干净成功才清零限流等级
            if isinstance(resp, dict) and resp.get("need_captcha"):
                gate.note_attempt()
            else:
                gate.note_success()
            return resp
        raise BridgeError(
            f"4399 连续限流（已按 {RETRY_WAITS} 秒各重试一次）：{last}"
        ) from last

    if method != "4399" or login_lock is None:
        return _once()

    with login_lock:
        # 登录之间留最小间隔，把请求铺开而不是一拥而上
        global _LAST_LOGIN_AT
        gap = _MIN_LOGIN_GAP_SEC - (time.time() - _LAST_LOGIN_AT)
        if gap > 0:
            time.sleep(gap)
        try:
            return _once()
        finally:
            _LAST_LOGIN_AT = time.time()


def login_via_bridge(
    bridge,
    acc: Account,
    *,
    captcha_id: str = "",
    captcha_code: str = "",
    api_lock=None,
    login_lock=None,
) -> Dict[str, Any]:
    """按账号偏好登录：优先复用 sauth，失败自动回退密码登录。

    返回 dict 带 `_login_via` 标记，调用方据此写 last_status：

    - ``sauth``            走 sauth 缓存，**没有**打 4399 密码登录
    - ``password``         本来就没有 sauth，正常密码登录
    - ``password_fallback`` sauth 失效 → 回退打了一次 4399 密码登录
                           （如果频繁出现，说明该批号的 sauth 正在集体过期）

    注意：`BridgeClient.login` 在 `ok=false` 时**抛 BridgeError**，
    所以图片验证码是以异常形式到达的，不是返回值——这里顺势把
    sauth 失败与验证码拦截分开处理。
    """
    method, account, password, sauth_text = acc.effective_login()

    if method == "sauth" and sauth_text:
        try:
            # account 是 BridgeClient.login 的**必填位置参数**；走 sauth 时传空串，
            # 由 login() 内部决定要不要放进 body。漏传会直接 TypeError
            # （实际踩过：检测角色/建角在 sauth 复用路径上报 missing 'account'）。
            resp = bridge.login("sauth", account="", sauth=sauth_text)
            if resp.get("need_captcha"):
                raise BridgeError("sauth 登录被要求验证码")
            resp["_login_via"] = "sauth"
            return resp
        except BridgeError as exc:
            fb_method, fb_account, fb_password, fb_sauth = acc.password_login()
            if fb_method == "sauth":
                # 手填 sauth 账号（method="sauth"）没有密码可回退
                raise
            _LOG.warning("sauth 登录失败，回退密码登录 account=%s: %s", fb_account, exc)
            resp = _gated_password_login(
                bridge,
                fb_method,
                fb_account,
                fb_password,
                fb_sauth,
                captcha_id=captcha_id,
                captcha_code=captcha_code,
                api_lock=api_lock,
                login_lock=login_lock,
            )
            if resp.get("need_captcha"):
                raise BridgeError("需要图片验证码（sauth 已失效，密码登录被拦）") from exc
            resp["_login_via"] = "password_fallback"
            resp["_sauth_error"] = str(exc)
            return resp

    resp = _gated_password_login(
        bridge,
        method,
        account,
        password,
        sauth_text,
        captcha_id=captcha_id,
        captcha_code=captcha_code,
        api_lock=api_lock,
        login_lock=login_lock,
    )
    resp["_login_via"] = "password"
    return resp


@dataclass
class SlotRuntime:
    index: int
    account_id: str = ""
    state: SlotState = SlotState.IDLE
    detail: str = ""
    character: str = ""
    stage_text: str = ""
    timer_text: str = "--:--"
    time_text: str = ""  # 登录/挂机/合计
    pos_text: str = ""  # 进服 / 进门 / 实时 XYZ
    verify_url: str = ""
    proxy_host: str = ""
    proxy_port: int = 0
    session_id: str = ""
    stop_flag: bool = False
    claim_now: bool = False
    hang_started_at: float = 0.0
    online_ms_server: int = 0
    online_ms_sampled_at: float = 0.0
    online_ms_at_hang_start: int = 0
    join_xyz: Optional[tuple] = None
    portal_xyz: Optional[tuple] = None
    live_xyz: Optional[tuple] = None  # 每 20s 刷新的实时坐标
    live_xyz_at: float = 0.0
    online_reward: Optional[dict] = None  # box_online_reward.json 扫描结果
    online_reward_at: float = 0.0
    client_pid: int = 0  # 本槽拉起的挂机 Forge PID（仅停自己的）
    lock_character: bool = False  # True=批量已指定角色，跳过弹窗
    release_account: Optional[Callable[[], None]] = field(default=None, repr=False)
    thread: Optional[threading.Thread] = field(default=None, repr=False)


class Scheduler:
    def __init__(
        self,
        store: AccountStore,
        settings: Dict[str, Any],
        on_status: Optional[StatusCb] = None,
        on_pick_char: Optional[PickCharCb] = None,
        on_embed: Optional[EmbedCb] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.on_status = on_status or (lambda *_: None)
        self.on_pick_char = on_pick_char
        self.on_embed = on_embed or (lambda *_: None)
        self.log = log or logging.getLogger("box-online")
        self._lock = threading.RLock()
        # 对接层调用互斥
        self._bridge_api_lock = threading.Lock()
        # 4399 密码登录之间串行 + 间隔（见 _gated_password_login）。
        # 与 _bridge_api_lock 分开：登录会持有一段正常时间，而 api_lock 只
        # 在发请求的瞬间持有，两者混用会把所有对接层调用一起堵住。
        self._login_lock = threading.Lock()
        # 同账号串行：网易侧通常一号同时只能有一条进服链路
        self._account_locks: Dict[str, threading.Lock] = {}
        n = int(settings.get("slot_count", 8))
        self.slots = [SlotRuntime(index=i) for i in range(n)]

    def _account_lock(self, account_id: str) -> threading.Lock:
        with self._lock:
            lk = self._account_locks.get(account_id)
            if lk is None:
                lk = threading.Lock()
                self._account_locks[account_id] = lk
            return lk

    @staticmethod
    def _release_account_channel(slot: SlotRuntime) -> None:
        """挂机开始后释放同账号锁，让下一槽继续登录。"""
        rel = slot.release_account
        if rel is None:
            return
        slot.release_account = None
        try:
            rel()
        except Exception:
            pass

    def resize(self, n: int) -> None:
        n = max(1, min(int(self.settings.get("max_slots", 32)), n))
        with self._lock:
            if n == len(self.slots):
                return
            if n < len(self.slots):
                for s in self.slots[n:]:
                    s.stop_flag = True
                self.slots = self.slots[:n]
            else:
                for i in range(len(self.slots), n):
                    self.slots.append(SlotRuntime(index=i))

    def poll_rewards(self, *, force: bool = False) -> None:
        """后台轻量读奖励文件；勿在 UI 点击路径里做全量 snapshot。"""
        from .mc_client import hang_instance_dir, read_reward_file

        now = time.time()
        with self._lock:
            targets = [
                s
                for s in self.slots
                if s.state
                in (
                    SlotState.AFK,
                    SlotState.CLAIMING,
                    SlotState.ENTERING_PORTAL,
                    SlotState.JOINING,
                )
            ]
        for s in targets:
            if not force and s.online_reward_at and (now - s.online_reward_at) < 2.5:
                continue
            try:
                inst = hang_instance_dir(s.index)
                rw = read_reward_file(inst, max_age_sec=60.0)
                aid = ch = ""
                hang_sec = 0.0
                base_ms = 0
                sampled = 0.0
                hang_start = 0
                with self._lock:
                    if 0 <= s.index < len(self.slots) and self.slots[s.index] is s:
                        if rw:
                            s.online_reward = rw
                            s.online_reward_at = now
                        aid = s.account_id
                        ch = s.character or ""
                        if s.hang_started_at > 0:
                            hang_sec = max(0.0, now - s.hang_started_at)
                        base_ms = int(s.online_ms_server or 0)
                        sampled = float(s.online_ms_sampled_at or 0)
                        hang_start = int(s.online_ms_at_hang_start or 0)
                if aid and (rw or hang_sec > 0 or base_ms > 0):
                    try:
                        self.store.upsert_character_progress(
                            aid,
                            character=ch,
                            sign_count=int((rw or {}).get("total_count") or 0) if rw else None,
                            hang_session_sec=float(hang_sec) if hang_sec > 0 else None,
                            online_ms_server=int(base_ms) if base_ms > 0 else None,
                            online_ms_sampled_at=sampled if sampled > 0 else None,
                            online_ms_at_hang_start=hang_start if hang_start > 0 else None,
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    def slot_list(self) -> list:
        """轻量槽位列表（奖励台下拉用）。"""
        with self._lock:
            return [
                {
                    "index": s.index,
                    "account_id": s.account_id,
                    "character": s.character,
                    "state": s.state.value,
                }
                for s in self.slots
            ]

    def reward_for(self, index: int) -> Optional[dict]:
        """单槽奖励视图（不扫盘、不锁太久）。"""
        from .claim import reward_view

        with self._lock:
            if index < 0 or index >= len(self.slots):
                return None
            s = self.slots[index]
            base_ms = int(s.online_ms_server or 0)
            sampled = float(s.online_ms_sampled_at or 0)
            total_ms = -1
            if base_ms > 0 and sampled > 0:
                total_ms = base_ms + int(max(0.0, time.time() - sampled) * 1000)
            raw = s.online_reward
            st = s.state.value
            ch = s.character
            aid = s.account_id
        return {
            "index": index,
            "account_id": aid,
            "character": ch,
            "state": st,
            "online_ms": total_ms,
            "reward": reward_view(raw, now_online_ms=total_ms if total_ms >= 0 else None),
        }

    def snapshot(self) -> list:
        from .claim import fmt_ms, fmt_sec, reward_view

        now = time.time()
        with self._lock:
            out = []
            for s in self.slots:
                hang_sec = 0.0
                if s.hang_started_at > 0 and s.state in (
                    SlotState.AFK,
                    SlotState.CLAIMING,
                    SlotState.ENTERING_PORTAL,
                    SlotState.JOINING,
                    SlotState.ERROR,
                    SlotState.NEED_VERIFY,
                    SlotState.STOPPING,
                ):
                    hang_sec = max(0.0, now - s.hang_started_at)

                # 合计 = 服务器采样时长 + 采样后流逝（大字计时显示这个）
                base_ms = int(s.online_ms_server or 0)
                has_server = base_ms > 0 and float(s.online_ms_sampled_at or 0) > 0
                if has_server and s.online_ms_sampled_at > 0:
                    total_ms = base_ms + int(max(0.0, now - s.online_ms_sampled_at) * 1000)
                elif has_server:
                    total_ms = base_ms
                else:
                    total_ms = -1
                login_ms = int(s.online_ms_at_hang_start or 0) if has_server else -1
                if has_server and login_ms <= 0:
                    login_ms = base_ms
                login_disp = fmt_ms(login_ms if login_ms > 0 else None)
                total_disp = fmt_ms(total_ms if total_ms >= 0 else None)

                # 大计时 = 合计；未同步时 --:--
                if total_ms >= 0:
                    timer = _fmt_duration(total_ms / 1000.0)
                    s.timer_text = timer
                elif s.state in (
                    SlotState.LOGGING_IN,
                    SlotState.PICK_CHAR,
                    SlotState.JOINING,
                    SlotState.ENTERING_PORTAL,
                    SlotState.IDLE,
                ):
                    timer = "--:--"
                    s.timer_text = timer
                else:
                    timer = s.timer_text or "--:--"

                in_world_pos = bool(s.join_xyz or s.live_xyz)
                # 不再显示挂机时长；合计已在大字，这里只留登录
                time_text = (
                    f"登录 {login_disp}"
                    if (s.hang_started_at > 0 or has_server or in_world_pos)
                    else ""
                )
                s.time_text = time_text

                def _fmt_xyz(p) -> str:
                    if not p or len(p) < 3:
                        return "—"
                    return f"{p[0]:.1f},{p[1]:.1f},{p[2]:.1f}"

                parts = []
                if s.join_xyz or s.portal_xyz:
                    parts.append(f"进服 {_fmt_xyz(s.join_xyz)} → 进门 {_fmt_xyz(s.portal_xyz)}")
                if s.live_xyz:
                    parts.append(f"实时 {_fmt_xyz(s.live_xyz)}")
                pos_text = " · ".join(parts)
                s.pos_text = pos_text

                # 注意：禁止在 snapshot 里写账号库（UI 每秒多次调用，写盘会拖死界面）

                # 大字旁标签：挂机后固定「合计」，进服前用阶段文案
                timer_label = (
                    "合计"
                    if (total_ms >= 0 or s.hang_started_at > 0 or s.state == SlotState.AFK)
                    else (s.stage_text or "")
                )
                if s.online_reward:
                    rv = reward_view(
                        s.online_reward,
                        now_online_ms=total_ms if total_ms >= 0 else None,
                    )
                else:
                    rv = {}
                out.append(
                    {
                        "index": s.index,
                        "account_id": s.account_id,
                        "state": s.state.value,
                        "detail": s.detail,
                        "character": s.character,
                        "stage_text": s.stage_text,
                        "timer_label": timer_label,
                        "timer_text": timer,
                        "time_text": time_text,
                        "pos_text": pos_text,
                        "verify_url": s.verify_url,
                        "proxy": f"{s.proxy_host}:{s.proxy_port}" if s.proxy_port else "",
                        "can_claim": s.state in (SlotState.AFK, SlotState.CLAIMING, SlotState.ENTERING_PORTAL),
                        "online_ms": total_ms,
                        "hang_sec": hang_sec,
                        "reward": rv,
                    }
                )
            return out

    def apply_online_sync(
        self,
        index: int,
        online_ms: Optional[int] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        *,
        phase: str = "",
    ) -> None:
        """Update slot from server sync / file probe. phase=join|portal|live|auto."""
        now = time.time()
        aid = ""
        hang_start_ms = 0
        with self._lock:
            if index < 0 or index >= len(self.slots):
                return
            s = self.slots[index]
            # >=0 都写入采样；仅 >0 才能锁定「登录」基线（避免 0 钉死 00:00）
            ph0 = (phase or "").lower().strip()
            if online_ms is not None and int(online_ms) >= 0:
                ms = int(online_ms)
                if (
                    ms > 0
                    or s.join_xyz is not None
                    or s.live_xyz is not None
                    or ph0 == "hang"
                ):
                    s.online_ms_server = ms
                    s.online_ms_sampled_at = now
                if ms > 0 and s.online_ms_at_hang_start <= 0:
                    s.online_ms_at_hang_start = ms
                hang_start_ms = int(s.online_ms_at_hang_start)
            if x is not None and y is not None and z is not None:
                xyz = (float(x), float(y), float(z))
                ph = (phase or "").lower().strip()
                # 进服点只锁一次，后续 live 不得覆盖
                if s.join_xyz is None and ph in ("join", "live", ""):
                    s.join_xyz = xyz
                if ph == "portal":
                    s.portal_xyz = xyz
                if ph in ("live", "portal", "join", ""):
                    s.live_xyz = xyz
                    s.live_xyz_at = now
                if ph == "" and s.hang_started_at > 0 and s.portal_xyz is None:
                    s.portal_xyz = xyz
                # 进门完成，或相对进服点位移够大 → 只开挂机计时（不改状态机）
                if s.hang_started_at <= 0 and s.join_xyz is not None:
                    dx = xyz[0] - float(s.join_xyz[0])
                    dy = xyz[1] - float(s.join_xyz[1])
                    dz = xyz[2] - float(s.join_xyz[2])
                    moved_xz = (dx * dx + dz * dz) ** 0.5
                    moved = (dx * dx + dy * dy + dz * dz) ** 0.5
                    start_hang = ph == "portal" or moved_xz >= 2.5 or moved >= 3.0
                    if start_hang and s.state in (
                        SlotState.JOINING,
                        SlotState.ENTERING_PORTAL,
                        SlotState.AFK,
                        SlotState.CLAIMING,
                    ):
                        s.hang_started_at = now
                        s.timer_text = "00:00"
                        if s.online_ms_server > 0 and s.online_ms_at_hang_start <= 0:
                            s.online_ms_at_hang_start = s.online_ms_server
                        if s.state in (SlotState.AFK, SlotState.CLAIMING):
                            s.stage_text = "挂机中"
                        # 挂机计时开始 → 放同行账号通道，下一槽可登录
                        self._release_account_channel(s)
            aid = s.account_id
            hang_start_ms = int(s.online_ms_at_hang_start or hang_start_ms)
        if aid and online_ms is not None and int(online_ms) > 0:
            try:
                ch = ""
                with self._lock:
                    if 0 <= index < len(self.slots):
                        ch = self.slots[index].character or ""
                self.store.upsert_character_progress(
                    aid,
                    character=ch,
                    online_ms_server=int(online_ms),
                    online_ms_sampled_at=now,
                    online_ms_at_hang_start=hang_start_ms,
                )
            except Exception:
                pass

    def counts(self) -> Dict[str, int]:
        """Lightweight counts — never call snapshot() (avoid double work on UI timer)."""
        with self._lock:
            total = len(self.slots)
            running = idle = bad = 0
            for s in self.slots:
                if s.state == SlotState.IDLE:
                    idle += 1
                elif s.state in (SlotState.NEED_VERIFY, SlotState.ERROR):
                    bad += 1
                else:
                    running += 1
            return {"total": total, "running": running, "idle": idle, "bad": bad}

    def slot_is_busy(self, index: int) -> bool:
        """True = 槽上有活线程或正处于流程中，追加挂机不得占用。"""
        with self._lock:
            if index < 0 or index >= len(self.slots):
                return True
            s = self.slots[index]
            if s.thread and s.thread.is_alive():
                return True
            return s.state not in CAN_START

    def free_slot_indices(self) -> List[int]:
        with self._lock:
            n = len(self.slots)
        return [i for i in range(n) if not self.slot_is_busy(i)]

    def occupied_account_ids(self) -> set:
        """正在跑的账号 id（追加时跳过，避免同号再开）。"""
        out = set()
        with self._lock:
            for s in self.slots:
                if not s.account_id:
                    continue
                if s.thread and s.thread.is_alive():
                    out.add(s.account_id)
                elif s.state not in CAN_START:
                    out.add(s.account_id)
        return out

    def occupied_account_characters(self) -> set:
        """(account_id, character) 已占用键；角色名为空则仅按账号占。"""
        out = set()
        with self._lock:
            for s in self.slots:
                if not s.account_id:
                    continue
                busy = (s.thread and s.thread.is_alive()) or (s.state not in CAN_START)
                if not busy:
                    continue
                out.add((s.account_id, (s.character or "").strip()))
        return out

    def start_all(self) -> None:
        """Legacy: start enabled accounts in order on free slots."""
        accounts = self.store.enabled_accounts()
        self.start_accounts([a.id for a in accounts])

    def start_accounts(self, account_ids: list) -> None:
        ids = [str(x) for x in account_ids if x]
        if not ids:
            return
        # 默认占满前 N 个窗口
        assignments = [(i, aid) for i, aid in enumerate(ids)]
        self.start_batch(assignments, replace=True)

    def start_batch(self, assignments: list, *, replace: bool = True) -> None:
        """按 (slot_index, account_id[, character]) 批量开代理窗口。

        replace=True：占用槽会先停再开（首次代理）。
        replace=False：占用槽跳过、绝不 stop（追加挂机）。
        """
        cleaned = []
        for item in assignments:
            if not item or len(item) < 2:
                continue
            slot_i = int(item[0])
            aid = str(item[1])
            char = str(item[2]) if len(item) >= 3 else ""
            cleaned.append((slot_i, aid, char))
        if not cleaned:
            return
        threading.Thread(
            target=self._start_batch_bg,
            args=(cleaned, bool(replace)),
            daemon=True,
            name="start-batch",
        ).start()

    def _start_batch_bg(self, assignments: list, replace: bool = True) -> None:
        stagger = float(self.settings.get("start_stagger_sec", 2.5))
        same_stagger = float(self.settings.get("same_account_stagger_sec", 8.0))
        hang_wait = float(self.settings.get("wait_hang_before_next_sec", 180.0))
        last_account = ""
        label_prefix = "首次代理" if replace else "追加挂机"
        for i, (slot_index, account_id, character) in enumerate(assignments):
            with self._lock:
                if slot_index < 0 or slot_index >= len(self.slots):
                    continue
                slot = self.slots[slot_index]
                alive = bool(slot.thread and slot.thread.is_alive())
                if alive:
                    if not replace:
                        self.log.info(
                            "append skip busy SLOT%02d (account=%s)",
                            slot_index + 1,
                            slot.account_id[:8] if slot.account_id else "-",
                        )
                        continue
                    slot.stop_flag = True
            if replace:
                deadline = time.time() + 8.0
                while time.time() < deadline:
                    with self._lock:
                        alive = bool(
                            self.slots[slot_index].thread and self.slots[slot_index].thread.is_alive()
                        )
                    if not alive:
                        break
                    time.sleep(0.15)
            else:
                with self._lock:
                    alive = bool(
                        self.slots[slot_index].thread and self.slots[slot_index].thread.is_alive()
                    )
                if alive:
                    continue
            with self._lock:
                slot = self.slots[slot_index]
                if (slot.thread and slot.thread.is_alive()) or (
                    not replace and slot.state not in CAN_START
                ):
                    continue
                slot.state = SlotState.IDLE
                slot.stop_flag = False
                slot.account_id = account_id
                slot.lock_character = bool(character)
                slot.character = character
                acc = self.store.get(account_id)
                if acc:
                    if not character:
                        slot.character = acc.character or ""
                        slot.lock_character = False
                    label = acc.display()
                    if slot.character:
                        label = f"{label} / {slot.character}"
                    slot.detail = f"{label_prefix} · {label}"
                else:
                    slot.detail = f"{label_prefix}排队…"
            # 上一窗已挂机后尽快开下一窗；同号仍稍拉开
            if last_account:
                gap = same_stagger if last_account == account_id else 0.4
                time.sleep(max(0.35, gap))
            self.start_slot(slot_index, account_id, character=character, lock_character=bool(character))
            last_account = account_id
            # 本窗进入挂机后立刻开下一窗（失败/超时也继续往后）
            if i + 1 >= len(assignments):
                break
            wait_deadline = time.time() + max(30.0, hang_wait)
            while time.time() < wait_deadline:
                with self._lock:
                    s = self.slots[slot_index]
                    started = s.hang_started_at > 0 or s.state == SlotState.AFK
                    bad = s.state in (
                        SlotState.ERROR,
                        SlotState.NEED_VERIFY,
                        SlotState.IDLE,
                        SlotState.STOPPING,
                    )
                    alive = bool(s.thread and s.thread.is_alive())
                if started or bad or not alive:
                    break
                time.sleep(0.2)

    @contextlib.contextmanager
    def _bridge_session(
        self,
        account_id: str,
        acc: Account,
        *,
        captcha_id: str = "",
        captcha_code: str = "",
        force_login: bool = False,
    ):
        """登录该账号并选好示例服服，yield 一个就绪的 bridge。

        `probe_characters` 与 `create_character` 共用：两者都要求
        「已登录 + 已选服」才能碰角色。退出时释放账号锁。
        """
        from .bridge import ensure_bridge_running

        bridge, _ = ensure_bridge_running(timeout_sec=20.0)
        if self.settings.get("bridge_host"):
            bridge.host = str(self.settings.get("bridge_host"))
            bridge.port = int(self.settings.get("bridge_port") or bridge.port)
            bridge.base = f"http://{bridge.host}:{bridge.port}"
        if not bridge.token:
            # 刚拉起时 json 可能稍晚写出
            time.sleep(0.6)
            bridge = load_bridge_client()
        if not bridge.token:
            raise BridgeError("未读到对接层凭据（配置文件缺失）")

        acc_lock = self._account_lock(account_id)
        if not acc_lock.acquire(timeout=60):
            raise BridgeError("账号通道忙，请稍后再试")
        try:
            session_id = ""
            via = ""
            if not force_login:
                # ① 复用已有会话
                # 重登会制造「同账号两条连接」：对接层只清理本地记录、
                # 并不关闭服务端那条连接（日志 replaced=1 占 70%），服务端随即
                # 以「连接异常」踢掉，登录成功到被踢只隔 55 毫秒。
                # 这里只做几秒钟的调用，**锁不跨登录、更不跨重试等待**。
                with self._bridge_api_lock:
                    existing = sessions_for_account(bridge, acc.account)
                    if existing:
                        session_id = existing[0]
                        via = "reuse"
                        try:
                            bridge.select_session(session_id)
                        except BridgeError:
                            session_id = ""
                            via = ""
            if not session_id:
                # ② 重登前先把同账号旧会话真正 logout（确保服务端那条连接断开）
                with self._bridge_api_lock:
                    for sid in sessions_for_account(bridge, acc.account):
                        try:
                            bridge.logout(sid)
                            _LOG.info("logout stale session %s (%s)", sid[:10], acc.account)
                        except BridgeError:
                            pass
                # 登录：内部**每次尝试单独取 api_lock**，并在不持锁的情况下
                # sleep 重试。否则一个账号在等 202 重试时会把全程序所有对接层调用
                # 堵死 —— 实际发生过：15 个槽全卡在「登录中」不动。
                resp = login_via_bridge(
                    bridge,
                    acc,
                    captcha_id=captcha_id,
                    captcha_code=captcha_code,
                    api_lock=self._bridge_api_lock,
                    login_lock=self._login_lock,
                )
                if resp.get("need_captcha"):
                    # 把 captcha_id / 图片一起带出去，否则上层只能原样重来
                    raise CaptchaNeeded.from_response(
                        resp, account=acc.account, label=acc.display()
                    )
                via = str(resp.get("_login_via") or "")
                if via == "sauth":
                    self.store.update(account_id, last_status="SAUTH 复用登录")
                elif via == "password_fallback":
                    self.store.update(
                        account_id,
                        last_status=(
                            "SAUTH 失效 → 已回退密码登录："
                            + str(resp.get("_sauth_error") or "")
                        )[:120],
                    )
                session_id = str(resp.get("session_id") or "")
                with self._bridge_api_lock:
                    if not session_id:
                        sessions = bridge.sessions()
                        if sessions:
                            session_id = str(
                                sessions[0].get("id") or sessions[0].get("session_id") or ""
                            )
                    if session_id:
                        try:
                            bridge.select_session(session_id)
                        except BridgeError:
                            pass
            else:
                self.store.update(account_id, last_status="复用已有会话（未重登）")

            # 选服：短暂取锁
            with self._bridge_api_lock:
                kw = str(self.settings.get("lost_world_keyword", "示例服"))
                servers = bridge.servers()
                server = pick_lost_world(servers, kw)
                if not server:
                    raise BridgeError("未找到示例服服务器")
                server_id = str(
                    server.get("id") or server.get("entity_id") or server.get("server_id") or ""
                )
                server_name = str(server.get("name") or server.get("server_name") or "")
                if not server_id:
                    raise BridgeError("服务器 ID 为空")
                bridge.select_server(server_id, server_name)

            # ① 锁已释放后才 yield：调用方拿着 bridge 做多久的事都不堵别人
            yield bridge
        finally:
            acc_lock.release()

    def _fetch_characters(self, bridge) -> List[str]:
        chars = bridge.characters()
        names = [str(c.get("name") or c.get("character") or "") for c in chars if c]
        return [n for n in names if n]

    def _remember_characters(self, account_id: str, names: List[str]) -> None:
        try:
            self.store.ensure_characters(account_id, names)
        except Exception:
            pass

    def probe_characters(self, account_id: str, *, force_login: bool = False) -> List[str]:
        """登录（或复用会话）并拉取示例服角色名列表（不进服）。

        默认优先**复用已有的会话**；如果复用的会话其实已经失效（服务端那边
        掉了），自动强制重登一次再试 —— 否则会卡在一条死会话上永远好不了。
        """
        acc = self.store.get(account_id)
        if not acc:
            raise BridgeError("账号不存在")
        try:
            with self._bridge_session(account_id, acc, force_login=force_login) as bridge:
                names = self._fetch_characters(bridge)
                self._remember_characters(account_id, names)
                return names
        except CaptchaNeeded:
            raise
        except BridgeError as exc:
            if force_login:
                raise
            _LOG.info("复用会话失败(%s)，强制重登一次 account=%s", exc, acc.account)
            return self.probe_characters(account_id, force_login=True)

    def warmup_accounts(
        self,
        account_ids: Optional[List[str]] = None,
        *,
        on_progress=None,
        only_4399: bool = False,
    ):
        """预热：把所有账号过一遍「登录 + 选服 + 拉角色」。

        用户要的流程是「先全部登录，把要验证码的集中弹出来填，填完再挂机」。
        这里就做这件事，并且：

        - **优先 sauth**：sauth 不打 4399 密码接口、不吃限流（实测密码登录一次
          成功、紧接着第二次就 202，所以连登 20 个号必须先走 sauth）。
        - **验证码不阻塞**：收集成 `WarmupItem(status=CAPTCHA)` 继续下一个账号，
          而不是像以前那样在这里抛异常卡住。
        - **闸门冷却立刻停**：继续打只会把冷却拖更长（15 分钟变 2 小时）。

        返回 `WarmupSummary`。
        """
        from .gate import shared as shared_gate
        from .warmup import CAPTCHA, EMPTY, FAILED, READY, SKIPPED, WarmupItem, WarmupSummary

        gate = shared_gate()
        if account_ids is None:
            accs = [a for a in self.store.enabled_accounts() if a.has_characters()]
            if only_4399:
                accs = [a for a in accs if a.method == "4399"]
        else:
            accs = [a for a in (self.store.get(i) for i in account_ids) if a]

        summary = WarmupSummary()
        for idx, acc in enumerate(accs):
            if on_progress:
                try:
                    on_progress(idx, len(accs), acc.display())
                except Exception:
                    pass

            # sauth 账号不吃限流；只有真要打密码登录时才需要闸门放行
            needs_password = not (acc.method == "4399" and acc.has_sauth())
            if needs_password:
                d = gate.check()
                if not d.allowed:
                    summary.stopped_for_cooldown = True
                    summary.cooldown_text = d.message()
                    for rest in accs[idx:]:
                        summary.items.append(
                            WarmupItem(rest.id, rest.display(), SKIPPED, detail="闸门冷却，未轮到你")
                        )
                    break

            try:
                names = self.probe_characters(acc.id)
            except CaptchaNeeded as exc:
                summary.items.append(
                    WarmupItem(
                        acc.id,
                        acc.display(),
                        CAPTCHA,
                        detail="需要图片验证码，填完即可继续",
                        captcha_id=exc.captcha_id,
                        captcha_image_base64=exc.image_base64,
                        captcha_content_type=exc.content_type,
                    )
                )
                continue
            except BridgeError as exc:
                # 打密码登录时被闸门拦下 -> 当成冷却，立刻停
                d2 = gate.check()
                if not d2.allowed:
                    summary.stopped_for_cooldown = True
                    summary.cooldown_text = d2.message()
                    summary.items.append(
                        WarmupItem(acc.id, acc.display(), SKIPPED, detail="闸门冷却，未轮到你")
                    )
                    for rest in accs[idx + 1 :]:
                        summary.items.append(
                            WarmupItem(rest.id, rest.display(), SKIPPED, detail="闸门冷却，未轮到你")
                        )
                    break
                summary.items.append(
                    WarmupItem(acc.id, acc.display(), FAILED, detail=str(exc)[:160])
                )
                continue
            except Exception as exc:  # noqa: BLE001
                summary.items.append(
                    WarmupItem(acc.id, acc.display(), FAILED, detail=f"{exc.__class__.__name__}: {exc}"[:160])
                )
                continue

            if names:
                summary.items.append(
                    WarmupItem(acc.id, acc.display(), READY, characters=list(names))
                )
            else:
                summary.items.append(
                    WarmupItem(acc.id, acc.display(), EMPTY, detail="账号下没有角色，需要先建角")
                )
        return summary

    def finish_captcha_login(self, account_id: str, captcha_id: str, code: str) -> List[str]:
        """用户填完验证码后，**只补这一步**完成登录（不重头再来）。

        以前的做法是丢掉 captcha_id 后原样重试，于是同一个账号反复撞验证码。
        """
        code = (code or "").strip()
        if not code:
            raise BridgeError("请输入验证码")
        acc = self.store.get(account_id)
        if not acc:
            raise BridgeError("账号不存在")
        with self._bridge_session(account_id, acc, captcha_id=captcha_id, captcha_code=code) as bridge:
            names = self._fetch_characters(bridge)
            self._remember_characters(account_id, names)
            self.store.update(account_id, last_status="验证码通过，已登录")
            return names

    def create_character(self, account_id: str, name: str) -> List[str]:
        """给账号建角色：登录 → 选服 → 建角 → 回读列表。

        走对接层的 /api/characters/create（对接层内等价于 GUI 的「创建角色」，
        即 WPFLauncherClient.create_character，需要已选服务器）。
        返回建角后的角色名列表。
        """
        wanted = (name or "").strip()
        if not wanted:
            raise BridgeError("请输入角色名")
        acc = self.store.get(account_id)
        if not acc:
            raise BridgeError("账号不存在")
        with self._bridge_session(account_id, acc) as bridge:
            resp = bridge.create_character(wanted)
            names = [
                str(c.get("name") or c.get("character") or "")
                for c in (resp.get("characters") or [])
                if c
            ]
            names = [n for n in names if n]
            if not names:
                # 对接层没回读列表时自己再查一次
                names = self._fetch_characters(bridge)
            self._remember_characters(account_id, names)
            self.store.update(account_id, last_status=f"已创建角色：{wanted}")
            return names

    def set_client_pid(self, index: int, pid: int) -> None:
        with self._lock:
            if 0 <= index < len(self.slots):
                self.slots[index].client_pid = int(pid) if pid else 0

    def stop_all(self, *, scan: bool = True) -> int:
        """Signal workers to stop and kill hang MC processes. Returns killed count."""
        from .procutil import kill_all_hang_instance_clients, kill_hang_clients_for_slots

        pids: List[int] = []
        indices: List[int] = []
        to_mark: List[SlotRuntime] = []
        with self._lock:
            for s in self.slots:
                s.stop_flag = True
                indices.append(s.index)
                if s.client_pid:
                    pids.append(int(s.client_pid))
                    s.client_pid = 0
                if s.state not in (SlotState.IDLE,):
                    to_mark.append(s)
        n = kill_hang_clients_for_slots(
            indices, extra_pids=pids, scan=bool(scan), scan_timeout=3.0
        )
        if scan:
            # 再扫全部挂机端，防止漏网（绝不碰游玩端）
            try:
                n += kill_all_hang_instance_clients(timeout_sec=3.0)
            except Exception as exc:
                self.log.warning("stop_all full scan: %s", exc)
        for s in to_mark:
            self._set(s, SlotState.STOPPING, f"安全停止…已结束挂机端 {n} 个进程")

        def _finalize() -> None:
            time.sleep(1.2)
            with self._lock:
                for s in self.slots:
                    alive = bool(s.thread and s.thread.is_alive())
                    if s.stop_flag and not alive and s.state == SlotState.STOPPING:
                        s.stop_flag = False
                        self._set(s, SlotState.IDLE, "已安全停止")
                    elif s.stop_flag and s.state == SlotState.STOPPING and not alive:
                        self._set(s, SlotState.IDLE, "已安全停止")

        threading.Thread(target=_finalize, name="aw-stop-finalize", daemon=True).start()
        self.log.info("stop_all: killed=%s pids=%s scan=%s", n, pids, scan)
        return int(n)

    def stop_slot(self, index: int, *, scan: bool = True) -> int:
        """关闭单个槽：停 worker + 杀本槽挂机端（同步扫进程），不影响其他槽。"""
        from .procutil import kill_hang_clients_for_slots

        index = int(index)
        pids: List[int] = []
        with self._lock:
            if index < 0 or index >= len(self.slots):
                return 0
            s = self.slots[index]
            s.stop_flag = True
            if s.client_pid:
                pids.append(int(s.client_pid))
                s.client_pid = 0
            was_active = s.state not in (SlotState.IDLE,)
        if was_active:
            self._set(s, SlotState.STOPPING, "关闭本槽…")
        # 协议模式没有 Forge 进程，扫 java/wmic 会卡住；只停本进程里的 bot
        hang_mode = str(self.settings.get("hang_mode") or "protocol").lower()
        do_scan = bool(scan) and hang_mode == "client"
        n = kill_hang_clients_for_slots(
            [index],
            extra_pids=pids,
            scan=do_scan,
            scan_timeout=2.5,
        )
        slot_stopping = None
        with self._lock:
            if 0 <= index < len(self.slots):
                s = self.slots[index]
                if s.state == SlotState.STOPPING:
                    slot_stopping = s
        if slot_stopping is not None:
            self._set(slot_stopping, SlotState.STOPPING, f"关闭中…已结束 {n} 个进程")

        def _finalize_one() -> None:
            time.sleep(1.0)
            with self._lock:
                if index < 0 or index >= len(self.slots):
                    return
                s = self.slots[index]
                alive = bool(s.thread and s.thread.is_alive())
                if s.stop_flag and not alive:
                    s.stop_flag = False
                    s.account_id = ""
                    s.character = ""
                    s.lock_character = False
                    s.detail = "已关闭"
                    s.hang_started_at = 0.0
                    s.timer_text = "--:--"
                    s.time_text = ""
                    s.pos_text = ""
                    s.stage_text = ""
                    s.proxy_host = ""
                    s.proxy_port = 0
                    s.verify_url = ""
                    s.online_reward = None
                    self._set(s, SlotState.IDLE, "已关闭")

        threading.Thread(target=_finalize_one, name=f"aw-stop-slot-{index}", daemon=True).start()
        self.log.info("stop_slot %s killed=%s pids=%s", index, n, pids)
        return n

    def shutdown(self) -> int:
        """Exit cleanup: stop workers + kill all hang clients. Never touch play."""
        n = self.stop_all(scan=True)
        self.log.info("shutdown: hang clients killed≈%s (play untouched)", n)
        return int(n)

    def start_slot(
        self,
        index: int,
        account_id: str,
        *,
        character: str = "",
        lock_character: bool = False,
    ) -> None:
        with self._lock:
            if index < 0 or index >= len(self.slots):
                return
            slot = self.slots[index]
            if slot.thread and slot.thread.is_alive():
                return
            if slot.state not in CAN_START:
                return
            acc = self.store.get(account_id)
            if not acc:
                self._set(slot, SlotState.ERROR, "账号不存在")
                return
            slot.account_id = account_id
            slot.stop_flag = False
            slot.claim_now = False
            slot.verify_url = ""
            slot.lock_character = bool(lock_character and character)
            slot.character = character if slot.lock_character else (character or acc.character or "")
            slot.hang_started_at = 0.0
            slot.timer_text = "--:--"
            slot.time_text = ""
            slot.pos_text = ""
            slot.stage_text = ""
            slot.online_ms_server = 0
            slot.online_ms_sampled_at = 0.0
            slot.online_ms_at_hang_start = 0
            slot.online_reward = None
            slot.online_reward_at = 0.0
            slot.join_xyz = None
            slot.portal_xyz = None
            slot.live_xyz = None
            slot.live_xyz_at = 0.0
            slot.client_pid = 0
            t = threading.Thread(target=self._worker, args=(index, account_id), daemon=True, name=f"slot-{index}")
            slot.thread = t
            t.start()

    def retry_slot(self, index: int) -> None:
        """重试：先关本槽游戏进程，再按原账号/角色重新走登录进服流程。"""
        index = int(index)
        with self._lock:
            if index < 0 or index >= len(self.slots):
                return
            slot = self.slots[index]
            aid = (slot.account_id or "").strip()
            char = (slot.character or "").strip()
            lock_char = bool(slot.lock_character and char)
            if not aid:
                pass
            else:
                slot.stop_flag = True
        if not aid:
            self._set(slot, SlotState.ERROR, "无账号，无法重试")
            return
        self._set(slot, SlotState.STOPPING, "重试：结束本槽会话…")

        def _work() -> None:
            from .procutil import kill_hang_clients_for_slots

            pids: List[int] = []
            with self._lock:
                if 0 <= index < len(self.slots):
                    s = self.slots[index]
                    if s.client_pid:
                        pids.append(int(s.client_pid))
                        s.client_pid = 0
            hang_mode = str(self.settings.get("hang_mode") or "protocol").lower()
            try:
                n = kill_hang_clients_for_slots(
                    [index],
                    extra_pids=pids,
                    scan=(hang_mode == "client"),
                    scan_timeout=2.0,
                )
            except Exception as exc:
                self.log.warning("retry kill slot=%s: %s", index, exc)
                n = 0
            self.log.info("retry_slot %s killed=%s pids=%s", index, n, pids)

            # 等旧 worker 退出，避免 start_slot 因 thread alive 直接 return
            deadline = time.time() + 12.0
            while time.time() < deadline:
                with self._lock:
                    if index < 0 or index >= len(self.slots):
                        return
                    s = self.slots[index]
                    alive = bool(s.thread and s.thread.is_alive())
                if not alive:
                    break
                time.sleep(0.2)

            with self._lock:
                if index < 0 or index >= len(self.slots):
                    return
                s = self.slots[index]
                # 旧线程若仍卡死，强制摘掉引用再开（进程已杀）
                if s.thread and s.thread.is_alive():
                    self.log.warning("retry_slot %s old thread still alive, force restart", index)
                s.thread = None
                s.stop_flag = False
                s.state = SlotState.IDLE
                s.detail = "重试：重新进服…"
                s.account_id = aid
                s.character = char
                s.lock_character = lock_char
                s.client_pid = 0
                s.verify_url = ""
                s.hang_started_at = 0.0

            self.start_slot(
                index,
                aid,
                character=char,
                lock_character=lock_char or bool(char),
            )

        threading.Thread(
            target=_work, name=f"aw-retry-slot-{index}", daemon=True
        ).start()

    def request_claim(
        self,
        index: int,
        *,
        slot: Optional[int] = None,
        milestone: bool = False,
        claim_all: bool = False,
        claim_everything: bool = False,
        claim_stages_only: bool = False,
    ) -> bool:
        """写 box_online_claim.json 让挂机端发包；protocol 模式仍用 claim_now 标志。"""
        from .mc_client import (
            hang_instance_dir,
            write_claim_all_command,
            write_claim_batch_command,
            write_claim_command,
        )

        with self._lock:
            if index < 0 or index >= len(self.slots):
                return False
            s = self.slots[index]
            if s.state not in (SlotState.AFK, SlotState.CLAIMING, SlotState.ENTERING_PORTAL):
                return False
            s.claim_now = True
            hang_mode = str(self.settings.get("hang_mode") or "client").lower()
            inst = hang_instance_dir(index)
            reward = dict(s.online_reward or {})
        if hang_mode != "client":
            return True
        try:
            if claim_stages_only:
                # 循环挂角：只打 4 个阶段键
                write_claim_batch_command(inst, [(i, False) for i in range(4)])
                return True
            if claim_everything:
                # 已开游戏：一次打满 8 个领取按钮
                write_claim_all_command(inst)
                return True
            if claim_all:
                stage_c = list(reward.get("stage_claimable") or [])
                mile_c = list(reward.get("milestone_claimable") or [])
                batch = []
                for i, ok in enumerate(stage_c[:4]):
                    if ok:
                        batch.append((i, False))
                for i, ok in enumerate(mile_c[:4]):
                    if ok:
                        batch.append((i, True))
                if batch:
                    write_claim_batch_command(inst, batch)
                else:
                    # 无扫描数据：盲领全部 8 键
                    write_claim_all_command(inst)
                return True
            if slot is None:
                write_claim_command(inst, 0, milestone=False)
            else:
                write_claim_command(inst, int(slot), milestone=bool(milestone))
            return True
        except Exception:
            return False

    def snapshot_claim_flags(self, index: int) -> Dict[str, Any]:
        """当前槽阶段/里程碑已领位（来自游戏同步导出）。"""
        from .claim import claimed_flags

        with self._lock:
            if index < 0 or index >= len(self.slots):
                return {"stage": [False] * 4, "mile": [False] * 4, "total": 0, "online_ms": -1}
            s = self.slots[index]
            raw = dict(s.online_reward or {})
            online = int(s.online_ms_server or -1)
        stage, mile = claimed_flags(raw)
        return {
            "stage": stage,
            "mile": mile,
            "total": int(raw.get("total_count") or 0),
            "online_ms": online,
        }

    def verify_claim_synced(
        self,
        index: int,
        before: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        milestone: bool = False,
        expect_any: bool = False,
        wait_sec: float = 6.0,
    ) -> Dict[str, Any]:
        """领取后轮询 box_online_reward.json，看游戏同步位是否真的变「已领」。

        能侦测：服务器回包后 OnlineActiveManager 的 claimed 翻转。
        不能侦测：背包满掉地上但仍算领取成功（同步位仍会变 true）。
        """
        from .claim import claimed_flags
        from .mc_client import hang_instance_dir, read_reward_file

        deadline = time.time() + max(1.5, float(wait_sec))
        before_stage = list((before or {}).get("stage") or [False] * 4)
        before_mile = list((before or {}).get("mile") or [False] * 4)
        before_total = int((before or {}).get("total") or 0)
        last = {"stage": before_stage, "mile": before_mile, "total": before_total}
        confirmed: List[str] = []
        while time.time() < deadline:
            try:
                self.poll_rewards(force=True)
            except Exception:
                pass
            inst = hang_instance_dir(index)
            rw = read_reward_file(inst, max_age_sec=30.0)
            if rw:
                with self._lock:
                    if 0 <= index < len(self.slots):
                        self.slots[index].online_reward = rw
                        self.slots[index].online_reward_at = time.time()
                st, mi = claimed_flags(rw)
                tot = int(rw.get("total_count") or 0)
                last = {"stage": st, "mile": mi, "total": tot}
                confirmed = []
                for i in range(4):
                    if st[i] and not before_stage[i]:
                        confirmed.append(f"阶段{i + 1}")
                for i in range(4):
                    if mi[i] and not before_mile[i]:
                        confirmed.append(f"里程碑{i + 1}")
                if tot > before_total:
                    confirmed.append(f"累计次数 {before_total}→{tot}")
                if slot is not None and not expect_any:
                    key = f"{'里程碑' if milestone else '阶段'}{int(slot) % 4 + 1}"
                    if any(key in c for c in confirmed) or (
                        milestone and mi[int(slot) % 4] and not before_mile[int(slot) % 4]
                    ) or (
                        (not milestone) and st[int(slot) % 4] and not before_stage[int(slot) % 4]
                    ):
                        return {
                            "ok": True,
                            "confirmed": confirmed,
                            "detail": f"游戏已确认领取 {key}",
                            "after": last,
                        }
                elif confirmed:
                    return {
                        "ok": True,
                        "confirmed": confirmed,
                        "detail": "游戏已确认：" + "、".join(confirmed),
                        "after": last,
                    }
            time.sleep(0.45)
        # timeout
        if slot is not None and not expect_any:
            i = int(slot) % 4
            still = (last["mile"][i] if milestone else last["stage"][i])
            if still and not (before_mile[i] if milestone else before_stage[i]):
                return {
                    "ok": True,
                    "confirmed": confirmed,
                    "detail": "游戏已确认领取",
                    "after": last,
                }
            return {
                "ok": False,
                "confirmed": confirmed,
                "detail": (
                    "已发包，但同步位未变（未到时间 / 背包异常 / 同步延迟）。"
                    "可点「刷新扫描」再看。"
                ),
                "after": last,
            }
        if confirmed:
            return {
                "ok": True,
                "confirmed": confirmed,
                "detail": "游戏已确认：" + "、".join(confirmed),
                "after": last,
            }
        return {
            "ok": False,
            "confirmed": [],
            "detail": "已发包，等待同步超时（未检测到 claimed 变化）",
            "after": last,
        }

    def claim_all_running(self, *, everything: bool = True) -> int:
        """对所有已开（挂机中）游戏领取奖励。everything=True 打满全部 8 键。"""
        n = 0
        try:
            self.poll_rewards(force=True)
        except Exception:
            pass
        with self._lock:
            idxs = [
                s.index
                for s in self.slots
                if s.state in (SlotState.AFK, SlotState.CLAIMING, SlotState.ENTERING_PORTAL)
            ]
        for i in idxs:
            if everything:
                ok = self.request_claim(i, claim_everything=True)
            else:
                ok = self.request_claim(i, claim_all=True)
            if ok:
                n += 1
        return n

    def _pop_claim(self, slot: SlotRuntime) -> bool:
        with self._lock:
            if slot.claim_now:
                slot.claim_now = False
                return True
            return False

    def _worker(self, index: int, account_id: str) -> None:
        slot = self.slots[index]
        acc = self.store.get(account_id)
        if not acc:
            self._set(slot, SlotState.ERROR, "账号不存在")
            return
        from .bridge import ensure_bridge_running

        try:
            bridge, bridge_msg = ensure_bridge_running(timeout_sec=25.0)
        except BridgeError as exc:
            self._set(slot, SlotState.ERROR, str(exc))
            return
        if self.settings.get("bridge_host"):
            bridge.host = str(self.settings.get("bridge_host"))
            bridge.port = int(self.settings.get("bridge_port") or bridge.port)
            bridge.base = f"http://{bridge.host}:{bridge.port}"

        acc_lock = self._account_lock(account_id)
        self._set(slot, SlotState.LOGGING_IN, bridge_msg if "启动" in bridge_msg else "等待同账号通道…")
        if not acc_lock.acquire(timeout=180):
            self._set(slot, SlotState.ERROR, "同账号其他槽位占用超时（一号通常不能同时多开）")
            return
        released = {"v": False}

        def _release() -> None:
            if released["v"]:
                return
            released["v"] = True
            try:
                acc_lock.release()
            except RuntimeError:
                pass

        slot.release_account = _release
        try:
            self._worker_locked(index, account_id, acc, bridge, slot)
        finally:
            slot.release_account = None
            _release()

    def _worker_locked(self, index: int, account_id: str, acc, bridge, slot: SlotRuntime) -> None:
        try:
            self._set(slot, SlotState.LOGGING_IN, acc.display())
            if not bridge.health():
                from .bridge import ensure_bridge_running

                bridge, _ = ensure_bridge_running(timeout_sec=15.0)
            if not bridge.token:
                time.sleep(0.5)
                bridge = load_bridge_client()
            if not bridge.token:
                raise BridgeError("未读到对接层凭据（请检查配置文件）")

            with self._bridge_api_lock:
                resp = login_via_bridge(bridge, acc)
                if resp.get("need_captcha"):
                    raise BridgeError("需要图片验证码，请改用手动过验证后再试")
                via = str(resp.get("_login_via") or "")
                if via == "sauth":
                    self.store.update(account_id, last_status="SAUTH 复用登录")
                elif via == "password_fallback":
                    self.store.update(
                        account_id,
                        last_status=(
                            "SAUTH 失效 → 已回退密码登录：" + str(resp.get("_sauth_error") or "")
                        )[:120],
                    )

                session_id = str(resp.get("session_id") or "")
                if not session_id:
                    sessions = bridge.sessions()
                    if sessions:
                        session_id = str(sessions[0].get("id") or sessions[0].get("session_id") or "")
                slot.session_id = session_id
                if session_id:
                    try:
                        bridge.select_session(session_id)
                    except BridgeError:
                        pass

                if slot.stop_flag:
                    self._set(slot, SlotState.IDLE, "已停止")
                    return

                kw = str(self.settings.get("lost_world_keyword", "示例服"))
                servers = bridge.servers()
                server = pick_lost_world(servers, kw)
                if not server:
                    raise BridgeError("未找到示例服服务器")
                server_id = str(server.get("id") or server.get("entity_id") or server.get("server_id") or "")
                server_name = str(server.get("name") or server.get("server_name") or "")
                if not server_id:
                    raise BridgeError("服务器 ID 为空")

                self._set(slot, SlotState.PICK_CHAR, "选服并拉取角色…")
                bridge.select_server(server_id, server_name)
                chars = bridge.characters()
                names = [str(c.get("name") or c.get("character") or "") for c in chars if c]
                names = [n for n in names if n]
                if not names:
                    raise BridgeError("该账号无角色")

            character = slot.character
            if slot.lock_character and character and character in names:
                self._set(slot, SlotState.PICK_CHAR, f"已指定角色 {character}")
            else:
                if self.on_pick_char and not slot.lock_character:
                    picked = self.on_pick_char(index, acc, names)
                    if picked:
                        character = picked
                if not character or character not in names:
                    character = names[0]
            slot.character = character
            if not slot.lock_character:
                self.store.update(account_id, character=character)

            if slot.stop_flag:
                self._set(slot, SlotState.IDLE, "已停止")
                return

            self._set(slot, SlotState.JOINING, f"{character} @ {server_name or server_id}")
            with self._bridge_api_lock:
                join = bridge.join(server_id, character, server_name=server_name, session_id=session_id)
            host = str(join.get("host") or "127.0.0.1")
            port = int(join.get("port") or 0)
            slot.proxy_host = host
            slot.proxy_port = port
            if not port:
                raise BridgeError("代理未返回端口")
            self._set(slot, SlotState.JOINING, f"等待本地代理 {host}:{port}")
            if not bridge.wait_proxy(port, timeout_sec=45):
                if not join.get("pending"):
                    raise BridgeError(f"代理未就绪 port={port}")
                time.sleep(2.0)
                self._set(slot, SlotState.JOINING, f"代理 pending，继续尝试 {host}:{port}")
            else:
                # aw-join 注明勿乱 TCP 探测；就绪后再稍等再连
                time.sleep(2.0)
                self._set(slot, SlotState.JOINING, f"代理已监听 {host}:{port}")

            if slot.stop_flag:
                self._set(slot, SlotState.IDLE, "已停止")
                return

            self._set(slot, SlotState.JOINING, f"连接代理进服 {host}:{port}")
            hang_mode = str(self.settings.get("hang_mode") or "client").lower()
            walk_min = float(self.settings.get("portal_walk_min_sec", 6.0))
            if hang_mode == "protocol":
                from .mc_bot import run_afk_session

                attempts = 2
                last_exc: Optional[Exception] = None
                for attempt in range(attempts):
                    if slot.stop_flag:
                        break
                    if attempt > 0:
                        self._set(
                            slot,
                            SlotState.ENTERING_PORTAL,
                            f"重连代理 {host}:{port} ({attempt + 1}/{attempts})",
                        )
                        time.sleep(2.0)
                        if not bridge.wait_proxy(port, timeout_sec=20):
                            time.sleep(1.0)
                    try:
                        run_afk_session(
                            host=host,
                            port=port,
                            username=character,
                            stop_flag=lambda: slot.stop_flag,
                            on_state=lambda st, detail: self._set(slot, st, detail),
                            portal_timeout=float(self.settings.get("portal_timeout_sec", 90)),
                            walk_min_sec=walk_min,
                            claim_interval_sec=float(self.settings.get("claim_interval_sec", 45)),
                            claim_now=lambda: self._pop_claim(slot),
                            on_online_ms=lambda ms=None, x=None, y=None, z=None, phase="", idx=index: self.apply_online_sync(
                                idx, ms, x, y, z, phase=phase
                            ),
                            forge_mods=self.settings.get("forge_mods") or None,
                            portal_target=(
                                float(self.settings.get("portal_target_x", -1)),
                                float(self.settings.get("portal_target_y", 106)),
                                float(self.settings.get("portal_target_z", 8)),
                            ),
                            join_delay_sec=float(self.settings.get("portal_join_delay_sec", 3)),
                        )
                        last_exc = None
                        break
                    except Exception as exc:  # pylint: disable=broad-except
                        last_exc = exc
                        msg = str(exc)
                        if attempt + 1 < attempts and (
                            "10053" in msg
                            or "10054" in msg
                            or "连接关闭" in msg
                            or "连接被中断" in msg
                            or "未就绪" in msg
                            or "login servers" in msg
                        ):
                            self.log.warning("slot %s proxy attempt %s failed: %s", index, attempt + 1, exc)
                            continue
                        raise
                if last_exc is not None:
                    raise last_exc
            else:
                from .mc_client import run_real_client_session

                install = str(self.settings.get("mc_install_root") or "").strip() or None
                run_real_client_session(
                    host=host,
                    port=port,
                    username=character,
                    slot_index=index,
                    stop_flag=lambda: slot.stop_flag,
                    on_state=lambda st, detail: self._set(slot, st, detail),
                    walk_min_sec=walk_min,
                    portal_timeout_sec=float(self.settings.get("portal_timeout_sec", 90)),
                    baritone_blocks=float(self.settings.get("portal_baritone_blocks") or 0),
                    portal_target=(
                        float(self.settings.get("portal_target_x", -1)),
                        float(self.settings.get("portal_target_y", 106)),
                        float(self.settings.get("portal_target_z", 8)),
                    ),
                    join_delay_sec=float(self.settings.get("portal_join_delay_sec", 10)),
                    install_root=install,
                    xmx_mb=int(self.settings.get("mc_xmx_mb") or 896),
                    # 不镶嵌：窗口独立显示，便于确认是否真进服
                    on_hwnd=None,
                    on_online_ms=lambda ms=None, x=None, y=None, z=None, phase="", idx=index: self.apply_online_sync(
                        idx, ms, x, y, z, phase=phase
                    ),
                    on_client_pid=lambda pid, idx=index: self.set_client_pid(idx, int(pid or 0)),
                )
            if slot.stop_flag:
                self._set(slot, SlotState.IDLE, "已停止")
            elif slot.state not in (SlotState.NEED_VERIFY, SlotState.ERROR):
                self._set(slot, SlotState.IDLE, "会话结束")
        except BridgeError as exc:
            verify = exc.verify_url or ""
            slot.verify_url = verify
            self.store.update(
                account_id,
                last_status=str(exc),
                last_verify_url=verify,
            )
            if verify or "验证" in str(exc) or "风险" in str(exc) or "1351" in str(exc):
                self._set(slot, SlotState.NEED_VERIFY, str(exc)[:80])
            else:
                self._set(slot, SlotState.ERROR, str(exc)[:80])
            self.log.warning("slot %s error: %s", index, exc)
        except Exception as exc:  # pylint: disable=broad-except
            if slot.stop_flag:
                self._set(slot, SlotState.IDLE, "已停止")
                return
            msg = str(exc)
            if "login servers" in msg:
                msg = "代理鉴权失败（同号多开冲突或会话被冲）。同账号请一次只挂一角，或停掉其他槽再重试"
            self.store.update(account_id, last_status=msg, last_verify_url="")
            self._set(slot, SlotState.ERROR, msg[:100])
            self.log.exception("slot %s crashed", index)

    def _set(self, slot: SlotRuntime, state: SlotState, detail: str = "") -> None:
        slot.state = state
        slot.detail = detail
        detail_s = detail or ""
        # 状态仍由 mc_client 推进；「已进门/挂机中」时兜底开计时
        if state == SlotState.AFK and (
            "已进门" in detail_s or "挂机中" in detail_s or "已在服内" in detail_s
        ):
            if slot.hang_started_at <= 0:
                slot.hang_started_at = time.time()
                slot.timer_text = "00:00"
                if slot.online_ms_server > 0 and slot.online_ms_at_hang_start <= 0:
                    slot.online_ms_at_hang_start = slot.online_ms_server
            slot.stage_text = "挂机中"
            self._release_account_channel(slot)
        elif state == SlotState.AFK and ("未确认" in detail_s or "未进服" in detail_s):
            slot.stage_text = "未确认进门"
        elif state == SlotState.AFK and slot.hang_started_at > 0:
            slot.stage_text = "挂机中"
            self._release_account_channel(slot)
        elif state == SlotState.CLAIMING and slot.hang_started_at > 0:
            slot.stage_text = slot.stage_text or "挂机中"
        elif state == SlotState.ENTERING_PORTAL:
            slot.stage_text = "进门中"
        elif state == SlotState.JOINING:
            slot.stage_text = "进服中"
        if state in (SlotState.IDLE, SlotState.ERROR, SlotState.NEED_VERIFY, SlotState.STOPPING):
            if state != SlotState.IDLE:
                slot.stage_text = state.value
            else:
                slot.stage_text = ""
                slot.hang_started_at = 0.0
                slot.timer_text = "--:--"
        if slot.account_id:
            hang_map = {
                SlotState.NEED_VERIFY: "需验证",
                SlotState.ERROR: "异常",
                SlotState.AFK: "挂机中",
                SlotState.CLAIMING: "挂机中",
                SlotState.LOGGING_IN: "登录中",
                SlotState.PICK_CHAR: "选角中",
                SlotState.JOINING: "进服中",
                SlotState.ENTERING_PORTAL: "进门中",
                SlotState.STOPPING: "未挂机",
                SlotState.IDLE: "未挂机",
            }
            hang = hang_map.get(state, "未挂机")
            online_done = bool(state == SlotState.IDLE and "已挂完" in (detail or ""))
            if online_done:
                hang = "已挂完"
            try:
                import time as _time

                kw = {
                    "hang_status": hang,
                    "stage_text": (slot.stage_text or detail or "")[:60],
                    "online_done": online_done,
                    "last_hang_at": _time.time(),
                    "last_status": (detail or hang)[:120],
                    "last_verify_url": slot.verify_url,
                }
                if slot.character:
                    kw["character"] = slot.character
                self.store.update(slot.account_id, **kw)
            except Exception:
                pass
        try:
            self.on_status(slot.index, state, detail)
        except Exception:
            pass
