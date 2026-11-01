"""循环挂角：以角色为队列单元，满在线时长后领齐 4 阶段再换角。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .accounts import AccountStore
from .claim import reward_view
from .scheduler import Scheduler
from .states import CAN_START, SlotState

LogCb = Callable[[str], None]


class CharLoopController:
    """后台 tick：空闲槽补角色；满门槛领 4 阶段；确认后关槽换角。"""

    def __init__(
        self,
        store: AccountStore,
        scheduler: Scheduler,
        settings: Dict[str, Any],
        *,
        on_log: Optional[LogCb] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.settings = settings
        self.on_log = on_log or (lambda _m: None)
        self.log = log or logging.getLogger("box-online.char_loop")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_claim_at: Dict[int, float] = {}
        self._claiming: Set[int] = set()
        self._queue_empty_logged = False
        self._pending_keys: Set[Tuple[str, str]] = set()
        self._pending_at: Dict[Tuple[str, str], float] = {}
        # 拿不到服务端在线时长、改用墙钟判断的槽（键=(槽位,账号)），避免刷日志
        self._wallclock_warned: Set[Tuple[int, str]] = set()
        self._verify_blocked = 0

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._queue_empty_logged = False
            self._pending_keys.clear()
            self._pending_at.clear()
            self._thread = threading.Thread(
                target=self._run, name="char-loop", daemon=True
            )
            self._thread.start()
        self.on_log("循环挂角已启动")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self.on_log("循环挂角已停止（不杀已在跑的槽）")

    def min_online_ms(self) -> int:
        mins = float(self.settings.get("char_loop_min_online_min") or 123)
        return max(1, int(mins * 60 * 1000))

    def claim_retry_sec(self) -> float:
        return max(15.0, float(self.settings.get("char_loop_claim_retry_sec") or 45))

    def tick_sec(self) -> float:
        return max(2.0, float(self.settings.get("char_loop_tick_sec") or 5.0))

    def max_windows(self) -> int:
        cap = int(self.settings.get("max_slots", 32) or 32)
        n = int(self.settings.get("char_loop_max_windows") or 4)
        return max(1, min(cap, n))

    def launch_batch(self) -> int:
        n = int(self.settings.get("char_loop_launch_batch") or 2)
        return max(1, min(self.max_windows(), n))

    def _busy_slot_count(self) -> int:
        with self.scheduler._lock:  # noqa: SLF001
            return sum(
                1
                for s in self.scheduler.slots
                if (s.thread and s.thread.is_alive()) or (s.state not in CAN_START)
            )

    def _run(self) -> None:
        while not self._stop.wait(self.tick_sec()):
            try:
                self._tick()
            except Exception as exc:
                self.log.warning("char_loop tick: %s", exc)
                self.on_log(f"循环挂角异常: {exc}")

    def _running_keys(self) -> Set[Tuple[str, str]]:
        """已占用 (账号, 角色)；同账号可多开不同角色，同角色不可重复。"""
        keys: Set[Tuple[str, str]] = set()
        with self.scheduler._lock:  # noqa: SLF001
            for s in self.scheduler.slots:
                if not s.account_id:
                    continue
                busy = (s.thread and s.thread.is_alive()) or (s.state not in CAN_START)
                if busy:
                    keys.add((s.account_id, (s.character or "").strip()))
        # 清掉已真正占上的 pending；超时 pending 也丢弃避免卡死队列
        now = time.time()
        stale = [
            k
            for k, ts in self._pending_at.items()
            if k in keys or (now - float(ts)) > 120.0
        ]
        for k in stale:
            self._pending_keys.discard(k)
            self._pending_at.pop(k, None)
        keys |= set(self._pending_keys)
        return keys

    def _enabled_units(self) -> List[Tuple[Any, str]]:
        """可参与循环的 (账号, 角色) 列表 —— 每次填充只算一次。

        这里要扫进程（`play_client_usernames` 最长 2 秒），旧实现把它放在
        逐候选的循环里：launch_batch 有几个候选就扫几次，而这些结果在一次
        填充里其实完全不变。

        也**不再调 `self.store.load()`**。store 是全局共享实例，UI 的改动本来
        就在同一份内存里；旧实现每 5 秒用磁盘内容整个替换一次 self.accounts，
        会把别处刚改、还没落盘的状态丢掉（并发压测能直接打出 IndexError）。
        """
        try:
            from .procutil import play_client_usernames

            play_names = play_client_usernames(timeout_sec=2.0)
        except Exception:
            play_names = set()
        blocked = self.store.account_ids_blocked_by_play(play_names)
        return [
            (acc, name)
            for acc, name in self.store.enabled_characters(include_done=False)
            if acc.id not in blocked
        ]

    def _next_from_queue(
        self,
        running: Set[Tuple[str, str]],
        units: Optional[List[Tuple[Any, str]]] = None,
    ) -> Optional[Tuple[str, str, str]]:
        """Return (account_id, character, label) or None.

        默认**同账号只许占一个槽**：服务端不允许同一账号有两条连接，否则
        以「连接异常」踢掉（实测登录成功后 55 毫秒即被踢）。同一账号的多个
        角色靠**轮换**依次挂，而不是同时开两个窗口。
        可用设置 `char_loop_one_per_account=False` 关掉。
        """
        one_per = bool(self.settings.get("char_loop_one_per_account", True))
        busy_accounts = {aid for aid, _ in running} if one_per else set()
        for acc, name in (units if units is not None else self._enabled_units()):
            key = (acc.id, name)
            if key in running:
                continue
            if acc.id in busy_accounts:
                continue  # 同账号已在别的槽上 -> 不能再开第二条连接
            return acc.id, name, f"{acc.display()} / {name}"
        return None

    def _queue_empty_reason(self) -> str:
        """队列空时给出**准确**原因。

        旧文案一律说「已全部挂完（或无启用角色）」。实际上你那种
        「20 个号启用了但一个角色都没有」的情况会命中这句，
        让人误以为活干完了 —— 真相是需要先去建角。
        """
        no_char = [a for a in self.store.enabled_accounts() if not a.has_characters()]
        if no_char:
            return (
                f"循环挂角 · {len(no_char)} 个启用账号**没有角色**，排不进队列 —— "
                "账号库勾「只看无角色」→ 选「创建角色」"
            )
        return "循环挂角 · 角色队列已全部挂完（或无启用角色）"

    def _warn_if_verify_blocking(self) -> None:
        """有槽卡在「需验证」时必须说明。

        这种槽现在算 busy（不会被自动补位重开，这是对的），但它会**悄悄占掉
        一个窗口**、让 room 变小，日志全静默 —— 看起来就像循环挂机不干活了。
        """
        with self.scheduler._lock:  # noqa: SLF001
            n = sum(1 for s in self.scheduler.slots if s.state == SlotState.NEED_VERIFY)
        if n and n != self._verify_blocked:
            self._verify_blocked = n
            self.on_log(
                f"循环挂角 · 有 {n} 个槽卡在「需验证」，正占用窗口且不会自动补位；"
                "处理完点该槽的「重试」"
            )
        elif not n:
            self._verify_blocked = 0

    def _ensure_slot_capacity(self, need: int) -> None:
        """保证调度器槽位数 ≥ 循环窗口上限。"""
        need = max(1, int(need))
        with self.scheduler._lock:  # noqa: SLF001
            cur = len(self.scheduler.slots)
        if cur < need:
            self.scheduler.resize(need)
            self.settings["slot_count"] = need

    def _fill_slots(self) -> None:
        max_win = self.max_windows()
        batch = self.launch_batch()
        self._ensure_slot_capacity(max_win)
        self._warn_if_verify_blocking()
        busy_n = self._busy_slot_count()
        running = self._running_keys()  # 含 pending，并清理已占上/超时的预占
        pending_n = len(self._pending_keys)
        room = max_win - busy_n - pending_n
        if room <= 0:
            return
        take = min(room, batch)
        free = self.scheduler.free_slot_indices()[:take]
        if not free:
            return
        units = self._enabled_units()  # 一次填充只算一次（含扫进程）
        assignments: List[tuple] = []
        now = time.time()
        for slot_i in free:
            nxt = self._next_from_queue(running, units)
            if not nxt:
                break
            aid, char, label = nxt
            key = (aid, char)
            assignments.append((slot_i, aid, char))
            running.add(key)
            self._pending_keys.add(key)
            self._pending_at[key] = now
            self.store.set_character_flags(aid, char, hang_status="排队中")
            self.on_log(
                f"循环挂角 · 取角 {label} → SLOT{slot_i + 1:02d} "
                f"（窗口 {busy_n + pending_n + len(assignments)}/{max_win}）"
            )
        if assignments:
            self._queue_empty_logged = False
            self.scheduler.start_batch(assignments, replace=False)
        elif not units and not self._queue_empty_logged:
            self._queue_empty_logged = True
            self.on_log(self._queue_empty_reason())

    def _slot_online_ms(self, index: int) -> int:
        data = self.scheduler.reward_for(index) or {}
        ms = data.get("online_ms")
        if ms is not None and int(ms) >= 0:
            return int(ms)
        with self.scheduler._lock:  # noqa: SLF001
            if 0 <= index < len(self.scheduler.slots):
                return int(self.scheduler.slots[index].online_ms_server or -1)
        return -1

    def _slot_wall_ms(self, index: int) -> int:
        """墙钟兜底：本槽从开始进服到现在挂了多久（毫秒）。"""
        with self.scheduler._lock:  # noqa: SLF001
            if 0 <= index < len(self.scheduler.slots):
                s = self.scheduler.slots[index]
                if s.hang_started_at > 0:
                    return int(max(0.0, time.time() - s.hang_started_at) * 1000)
        return 0

    def _stages_all_done(self, index: int, *, force: bool = True) -> bool:
        # force=True 时自己刷一次奖励（领奖 worker 必须看到"刚领完"的翻转）。
        # 巡检路径改传 force=False：_inspect_running 每 tick 已经统一刷过一次，
        # 旧实现是每个槽各强制刷一次 —— 5 个窗口 = 每 5 秒 5 次强制请求。
        if force:
            try:
                self.scheduler.poll_rewards(force=True)
            except Exception:
                pass
        data = self.scheduler.reward_for(index) or {}
        view = data.get("reward") or reward_view(None)
        stages = list(view.get("stages") or [])
        if len(stages) < 4:
            return False
        return all(bool(s.get("done")) for s in stages[:4])

    def _inspect_running(self) -> None:
        need_ms = self.min_online_ms()
        retry = self.claim_retry_sec()
        now = time.time()
        # 每 tick 统一强制刷一次奖励，下面逐槽判断复用这份数据（见 _stages_all_done）
        try:
            self.scheduler.poll_rewards(force=True)
        except Exception:
            pass
        with self.scheduler._lock:  # noqa: SLF001
            targets = [
                (s.index, s.account_id, (s.character or "").strip())
                for s in self.scheduler.slots
                if s.account_id
                and s.state in (SlotState.AFK, SlotState.CLAIMING, SlotState.ENTERING_PORTAL)
            ]
        for index, aid, char in targets:
            if index in self._claiming:
                continue
            key = (index, aid)
            online = self._slot_online_ms(index)
            if online < 0:
                # 服务端在线时长还没采到。旧实现直接 continue —— 一旦上报坏掉，
                # 循环就**永远不换角也不报错**，看着像还在挂着。这里用墙钟兜底
                # 并明确告警；是否真换角仍由 4 阶段是否领齐决定，不会误换。
                wall = self._slot_wall_ms(index)
                if wall < need_ms:
                    continue
                if key not in self._wallclock_warned:
                    self._wallclock_warned.add(key)
                    mins = max(1, int(need_ms / 60000))
                    self.on_log(
                        f"循环挂角 · SLOT{index + 1:02d} 拿不到服务端在线时长，"
                        f"改用墙钟 {wall / 60000:.0f}min（门槛 {mins}min）判断；"
                        "若长期如此请检查协议的时长上报"
                    )
                online = wall
            if online < need_ms:
                continue
            # 已满门槛：若已齐则换角；否则领奖
            if self._stages_all_done(index, force=False):
                self._finish_and_rotate(index, aid, char)
                continue
            last = float(self._last_claim_at.get(key) or 0)
            if now - last < retry:
                continue
            self._last_claim_at[key] = now
            self._claiming.add(index)
            mins = max(1, int(need_ms / 60000))
            self.on_log(
                f"循环挂角 · SLOT{index + 1:02d} 满{mins}min · 领取全部阶段 "
                f"({aid[:8] if aid else '?'}/{char or '?'})"
            )
            threading.Thread(
                target=self._claim_worker,
                args=(index, aid, char),
                name=f"char-loop-claim-{index}",
                daemon=True,
            ).start()

    def _claim_worker(self, index: int, aid: str, char: str) -> None:
        try:
            before = self.scheduler.snapshot_claim_flags(index)
            ok = self.scheduler.request_claim(index, claim_stages_only=True)
            if not ok:
                self.on_log(f"循环挂角 · SLOT{index + 1:02d} 领奖指令失败（槽未挂机？）")
                return
            # 等同步翻转；再检查是否 4 阶段全完成
            self.scheduler.verify_claim_synced(
                index, before, expect_any=True, wait_sec=8.0
            )
            if self._stages_all_done(index):
                self._finish_and_rotate(index, aid, char)
            else:
                self.on_log(
                    f"循环挂角 · SLOT{index + 1:02d} 阶段未齐，稍后重试领奖"
                )
        except Exception as exc:
            self.on_log(f"循环挂角 · SLOT{index + 1:02d} 领奖异常: {exc}")
        finally:
            self._claiming.discard(index)

    def _finish_and_rotate(self, index: int, aid: str, char: str) -> None:
        label = char or "?"
        if aid:
            acc = self.store.get(aid)
            if acc:
                label = f"{acc.display()} / {char}"
            self.store.set_character_flags(
                aid, char, online_done=True, hang_status="已挂完"
            )
        self.on_log(f"循环挂角 · 4阶段完成 · 换角 {label}（关 SLOT{index + 1:02d}）")
        try:
            n = self.scheduler.stop_slot(index, scan=True)
            self.on_log(f"循环挂角 · SLOT{index + 1:02d} 已关游戏进程 {n} 个")
        except Exception as exc:
            self.on_log(f"循环挂角 · 关槽失败: {exc}")
        self._last_claim_at.pop((index, aid), None)
        self._wallclock_warned.discard((index, aid))
        # 下一 tick 会补位

    def _tick(self) -> None:
        self.scheduler.settings = self.settings
        try:
            self.scheduler.poll_rewards(force=False)
        except Exception:
            pass
        self._inspect_running()
        if not self._stop.is_set():
            self._fill_slots()
