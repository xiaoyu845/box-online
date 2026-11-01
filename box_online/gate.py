"""4399 登录闸门：全局串行 + 令牌桶 + 202 冷却持久化。

## 为什么需要它

实测（2026-10-16）4399 密码登录的阈值极低：

    第 1 个账号  → HTTP 200，成功（8.4s）
    第 2 个账号  → HTTP 500，18.7s（6s+12s 退避的特征）= 被限流

也就是**一次成功、紧接着第二次就 202**。而程序里有 4 个入口会打 4399 密码登录
（转 SAUTH / 检测角色 / 创建角色 / 调度器登录），它们各自独立、还能并发。
只要一个入口吃到 202，其他入口继续打只会把冷却拖得更长。

## 它做三件事

1. **串行化**：所有 4399 密码登录排队，一次只放一个
2. **限速**：滑动窗口令牌桶（默认 4 次/小时，可配）
3. **冷却持久化**：吃到 202 就记 `blocked_until`，指数退避
   （15m → 30m → 1h → 2h → 4h）。冷却期内任何入口都**快速失败**并告知
   还要等多久，状态写盘、**重启不丢**。

## 只管密码登录

sauth 登录**不打 4399 密码接口**，不吃这个限流，所以走 sauth 的路径不经过闸门。
这正是「优先 sauth」能救命的原理。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 单次登录吃到 202 后的重试间隔（秒）。
#
# **这里刻意不叫"冷却"，也不再全局封锁。** 实测事实（2026-10-16 深夜）：
#   · 单个请求、安静 8 分钟后      -> HTTP 200，0.7 秒，直接给验证码
#   · 4 次登录，间隔 5~24 秒       -> 4 次全部成功
#   · 7 个槽**同时**登            -> 绝大多数吃 202（「请稍后再试。」）
# 结论：4399 限的是"短时间内的频率"，**几秒就恢复**，不是封禁。
# 所以正确做法是"排队 + 间隔 + 短重试"，而不是把所有人挡在门外等 90 秒。
# 之前那个全局冷却墙是把突发限速当封禁处理 —— 错误的工具。
RETRY_WAITS = (5, 10, 20)

# 统计窗口：只用来显示「近 1 小时发起了几次」，**不作为拒绝依据**。
WINDOW_SEC = 3600.0

DEFAULT_PER_HOUR = 60.0


@dataclass
class Decision:
    """一次「能不能打 4399 密码登录」的判定。"""

    allowed: bool
    wait_sec: float = 0.0
    reason: str = ""

    @property
    def wait_text(self) -> str:
        s = int(max(0.0, self.wait_sec))
        if s >= 3600:
            return f"{s // 3600} 小时 {(s % 3600) // 60} 分钟"
        if s >= 60:
            return f"{s // 60} 分钟"
        return f"{s} 秒"

    def message(self) -> str:
        if self.allowed:
            return ""
        return f"4399 限流（{self.reason}），{self.wait_text} 后重试"


@dataclass
class GateState:
    blocked_until: float = 0.0
    level: int = 0
    ok_times: List[float] = field(default_factory=list)
    last_note: str = ""


class LoginGate:
    """跨线程、跨重启的 4399 密码登录闸门。"""

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        per_hour: float = DEFAULT_PER_HOUR,
        enforce_quota: bool = False,
        log=None,
    ) -> None:
        self.path = path
        self.per_hour = max(1.0, float(per_hour))
        # 默认 False：配额只记录不拦人。想启用熔断的（例如跑自动化脚本）显式打开。
        self.enforce_quota = bool(enforce_quota)
        self.log = log
        self._lock = threading.RLock()
        self._state = GateState()
        self.load()

    # ---------- 持久化 ----------

    def load(self) -> None:
        st = GateState()
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                st = GateState(
                    blocked_until=float(raw.get("blocked_until") or 0),
                    level=int(raw.get("level") or 0),
                    ok_times=[float(x) for x in (raw.get("ok_times") or [])],
                    last_note=str(raw.get("last_note") or ""),
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        self._state = st

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "blocked_until": self._state.blocked_until,
                "level": self._state.level,
                "ok_times": self._state.ok_times[-64:],
                "last_note": self._state.last_note,
                "updated_at": time.time(),
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # ---------- 判定 ----------

    def _prune(self, now: float) -> None:
        cut = now - WINDOW_SEC
        self._state.ok_times = [t for t in self._state.ok_times if t > cut]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            self._prune(now)
            blocked = max(0.0, self._state.blocked_until - now)
            return {
                "blocked_sec": blocked,
                "blocked_until": self._state.blocked_until,
                "level": self._state.level,
                "used_last_hour": len(self._state.ok_times),
                "per_hour": self.per_hour,
                "last_note": self._state.last_note,
            }

    def status_text(self) -> str:
        with self._lock:
            now = time.time()
            self._prune(now)
            used = len(self._state.ok_times)
            lvl = self._state.level
        base = f"4399 近 1 小时已发起 {used} 次密码登录"
        if lvl:
            return f"{base}；{self.last_limited_text()}（连续限流 {lvl} 次）"
        return f"{base}；近期没有被限流"

    def check(self) -> Decision:
        """始终放行。

        **不再有全局冷却**：实测限流是突发性的、几秒就恢复，而全局封锁会把
        本可以成功的登录挡住（曾经 7 个槽一起显示"冷却中"，纯属自伤）。
        真正的节流交给：全局串行 + 每次登录之间的间隔 + 单次登录的短重试
        （见 scheduler._gated_password_login）。
        """
        return Decision(True)

    def suggest_wait(self) -> float:
        """刚吃到 202 时，建议这次登录等几秒再试。"""
        with self._lock:
            idx = min(max(0, self._state.level - 1), len(RETRY_WAITS) - 1)
            return float(RETRY_WAITS[idx])

    def last_limited_text(self) -> str:
        with self._lock:
            at = float(self._state.blocked_until or 0)
            if not at:
                return "近期没有限流"
            mins = max(0.0, (time.time() - at) / 60.0)
            return f"最近一次限流在 {mins:.0f} 分钟前"

    # ---------- 记事件 ----------

    def note_success(self) -> None:
        with self._lock:
            now = time.time()
            self._prune(now)
            self._state.ok_times.append(now)
            # 一次干净成功就把冷却等级清零：说明限流确实过去了
            self._state.level = 0
            self._state.last_note = ""
            self._save()

    def note_rate_limited(self, detail: str = "") -> Decision:
        """4399 回了 202/「请稍后再试」：**只做记录**，不设全局封锁。

        返回值里的 wait_sec 供**这一次登录自己**退避用（调用方 sleep 之后重试），
        不是让所有人停下来。
        """
        with self._lock:
            self._state.level += 1
            self._state.blocked_until = time.time()   # 记录"最后限流时刻"
            self._state.last_note = detail or "4399 返回「请稍后再试」"
            self._save()
            wait = float(RETRY_WAITS[min(self._state.level - 1, len(RETRY_WAITS) - 1)])
            if self.log:
                self.log.warning("4399 限流 #%s -> 本次登录等 %.0fs 重试", self._state.level, wait)
            return Decision(False, wait, self._state.last_note)

    def note_attempt(self) -> None:
        """消耗了一次请求，但既不是干净成功也不是限流（例如回了图片验证码）。

        计进滑动窗口（配额确实被用掉了），但**不清冷却等级** ——
        验证码挑战不代表限流已经过去。
        """
        with self._lock:
            now = time.time()
            self._prune(now)
            self._state.ok_times.append(now)
            self._save()

    def note_failure(self) -> None:
        """登录失败但不是限流（密码错等）：不惩罚桶，只记一句。"""
        with self._lock:
            self._state.last_note = "上次失败（非限流）"

    def reset(self) -> None:
        with self._lock:
            self._state = GateState()
            self._save()


# ---------------------------------------------------------------------------
# 进程内单例（面板各入口共用同一个闸门）
# ---------------------------------------------------------------------------

_shared: Optional[LoginGate] = None
_shared_lock = threading.Lock()


def shared() -> LoginGate:
    global _shared
    with _shared_lock:
        if _shared is None:
            from .paths import preferred_home

            _shared = LoginGate(Path(preferred_home()) / "4399_gate.json")
        return _shared


def configure(gate: Optional[LoginGate]) -> None:
    """测试用：替换单例。"""
    global _shared
    with _shared_lock:
        _shared = gate
