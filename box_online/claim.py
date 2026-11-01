"""adventureworld online reward claim packets (Forge SimpleNetworkWrapper).

# 在线奖励：由服务端插件消息同步
# 进度同步消息
# 领取消息

MessageOnlineActive.fromBytes (after disc byte):
  long onlineTimeMs
  int totalCount
  4x boolean stageClaimed
  4x boolean milestoneClaimed
  4x long rewardTimeMs thresholds
  4x int totalRewardCount
  boolean hasInfo (+ optional title/tips/items)
"""

from __future__ import annotations

import re
import struct
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_CHANNEL = ""  # 服务端插件通道名：进服不受影响，领奖前需按服务端填写
DISC_SYNC = 21
DISC_CLAIM = 22


def build_claim_payload(slot_index: int, *, milestone: bool = False) -> bytes:
    """Raw SimpleNetworkWrapper payload (消息标识 + message body)."""
    idx = int(slot_index) % 4
    return bytes([DISC_CLAIM & 0xFF]) + struct.pack(">i", idx) + (b"\x01" if milestone else b"\x00")


def build_claim_plugin_body(slot_index: int, *, milestone: bool = False) -> bytes:
    """Full plugin-message body after channel string is written by caller: just payload."""
    return build_claim_payload(slot_index, milestone=milestone)


def try_parse_sync(payload: bytes) -> Optional[Dict[str, Any]]:
    """Parse MessageOnlineActive sync header (同步消息)."""
    if not payload or payload[0] != DISC_SYNC:
        return None
    # Need at least: 1 + 8 + 4 + 4 + 4 + 32 + 16 + 1 = 70
    if len(payload) < 70:
        return {"kind": "online_sync", "raw_len": len(payload), "online_ms": None}
    o = 1
    online_ms = struct.unpack_from(">q", payload, o)[0]
    o += 8
    total_count = struct.unpack_from(">i", payload, o)[0]
    o += 4
    stage_claimed = [bool(payload[o + i]) for i in range(4)]
    o += 4
    mile_claimed = [bool(payload[o + i]) for i in range(4)]
    o += 4
    reward_times = list(struct.unpack_from(">4q", payload, o))
    o += 32
    total_reward_counts = list(struct.unpack_from(">4i", payload, o))
    o += 16
    has_info = bool(payload[o]) if o < len(payload) else False
    return {
        "kind": "online_sync",
        "raw_len": len(payload),
        "online_ms": max(0, int(online_ms)),
        "total_count": int(total_count),
        "stage_claimed": stage_claimed,
        "milestone_claimed": mile_claimed,
        "reward_times_ms": [max(0, int(x)) for x in reward_times],
        "total_reward_counts": total_reward_counts,
        "has_info": has_info,
    }


def fmt_ms(ms: Optional[float]) -> str:
    if ms is None or ms < 0:
        return "--:--"
    return fmt_sec(float(ms) / 1000.0)


def fmt_sec(sec: float) -> str:
    s = max(0, int(sec))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def claim_plan() -> Tuple[Tuple[int, bool], ...]:
    """All claim buttons: stage 0-3 then milestone 0-3."""
    stage = tuple((i, False) for i in range(4))
    total = tuple((i, True) for i in range(4))
    return stage + total


def next_reward_index(online_ms: int, reward_times_ms: List[int], claimed: List[bool]) -> int:
    """First unclaimed stage whose threshold is reached; else -1."""
    for i, need in enumerate(reward_times_ms[:4]):
        if i < len(claimed) and claimed[i]:
            continue
        if online_ms >= need:
            return i
    return -1


def fmt_remain_cn(ms: Optional[float]) -> str:
    if ms is None or ms < 0:
        return "--"
    s = max(0, int(float(ms) / 1000.0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分钟{sec}秒"
    return f"{m}分钟{sec}秒"


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(v)


def _bool4(raw: Any) -> List[bool]:
    seq = list(raw) if isinstance(raw, (list, tuple)) else []
    out = [_as_bool(seq[i]) if i < len(seq) else False for i in range(4)]
    return out


def claimed_flags(data: Optional[Dict[str, Any]]) -> Tuple[List[bool], List[bool]]:
    """Raw stage/milestone claimed bits from sync (unsanitized)."""
    if not data or not isinstance(data, dict):
        return [False] * 4, [False] * 4
    return _bool4(data.get("stage_claimed")), _bool4(data.get("milestone_claimed"))


def reward_view(data: Optional[Dict[str, Any]], *, now_online_ms: Optional[int] = None) -> Dict[str, Any]:
    """Normalize box_online_reward.json / sync dict into GUI view model."""
    empty = {
        "active": False,
        "title": "示例服在线奖励",
        "online_ms": -1,
        "stage_line": "累计在线得好礼 (?/4) 阶段",
        "countdown": "等待同步…",
        "mile_line": "满阶段在线获丰富豪礼",
        "mile_progress": "累计次数 [?/?]",
        "stages": [{"label": "未达成", "enabled": False, "done": False} for _ in range(4)],
        "milestones": [{"label": "未达成", "enabled": False, "done": False} for _ in range(4)],
        "any_claimable": False,
    }
    if not data or not isinstance(data, dict):
        return empty
    online = int(now_online_ms if now_online_ms is not None else (data.get("online_ms") or -1))
    times = list(data.get("reward_times_ms") or [0, 0, 0, 0])[:4]
    while len(times) < 4:
        times.append(0)
    times = [max(0, int(x or 0)) for x in times]
    stage_claimed = _bool4(data.get("stage_claimed"))
    mile_claimed = _bool4(data.get("milestone_claimed"))
    mile_need = list(data.get("total_reward_counts") or [0, 0, 0, 0])[:4]
    while len(mile_need) < 4:
        mile_need.append(0)
    mile_need = [max(0, int(x or 0)) for x in mile_need]
    total_count = int(data.get("total_count") or 0)
    stage_idx = int(data.get("stage_index") or 0)
    if online >= 0 and times:
        stage_idx = 3
        for i, need in enumerate(times):
            if online < int(need):
                stage_idx = i
                break
    remain = int(data.get("remain_ms") or -1)
    if online >= 0 and times:
        remain = max(0, int(times[stage_idx]) - online)
    # Prefer export's claimable arrays when present
    stage_claimable = data.get("stage_claimable")
    mile_claimable = data.get("milestone_claimable")
    stages = []
    any_ok = False
    for i in range(4):
        need = int(times[i] or 0)
        reached = online >= 0 and need > 0 and online >= need
        # 未到时间却标已领 → 脏数据，不当「完成」（修 1/4 倒计时却四格全完成）
        raw_done = stage_claimed[i]
        done = bool(raw_done and (reached or online < 0 or need <= 0))
        if isinstance(stage_claimable, (list, tuple)) and i < len(stage_claimable):
            ok = (not done) and _as_bool(stage_claimable[i])
        else:
            ok = (not done) and reached
        if ok:
            any_ok = True
        if done:
            lab = "完成"
        elif ok:
            lab = "领取"
        elif online >= 0 and need > 0:
            lab = "未达成"
        else:
            lab = "领取"
        stages.append(
            {
                "label": lab,
                "enabled": ok,
                "done": done,
                "slot": i,
                "milestone": False,
            }
        )
    miles = []
    for i in range(4):
        need = int(mile_need[i] or 0)
        reached = need > 0 and total_count >= need
        raw_done = mile_claimed[i]
        done = bool(raw_done and (reached or need <= 0))
        if isinstance(mile_claimable, (list, tuple)) and i < len(mile_claimable):
            ok = (not done) and _as_bool(mile_claimable[i])
        else:
            ok = (not done) and reached
        if ok:
            any_ok = True
        if done:
            lab = "完成"
        elif ok:
            lab = "领取"
        elif need > 0:
            lab = "未达成"
        else:
            lab = "领取"
        miles.append(
            {
                "label": lab,
                "enabled": ok,
                "done": done,
                "slot": i,
                "milestone": True,
            }
        )
    mile_max = int(mile_need[3] or mile_need[-1] or 0) if mile_need else 0
    title = str(data.get("title") or "示例服在线奖励")
    if "§" in title:
        title = re.sub(r"§.", "", title) or "示例服在线奖励"
    return {
        "active": bool(data.get("active")),
        "title": title,
        "online_ms": online,
        "stage_line": f"累计在线得好礼 ({stage_idx + 1}/4) 阶段",
        "countdown": (
            "今日已完成"
            if online >= 0 and times and remain == 0 and stage_idx >= 3 and online >= int(times[3] or 0)
            else (f"距离获得奖励 {fmt_remain_cn(remain)}" if remain >= 0 else "等待同步…")
        ),
        "mile_line": f"满阶段在线{mile_max or '?'}次获丰富豪礼" if mile_max else "满阶段在线获丰富豪礼",
        "mile_progress": f"累计次数 [{total_count}/{mile_max or '?'}]",
        "stages": stages,
        "milestones": miles,
        "any_claimable": any_ok,
        "stage_claimed": [s["done"] for s in stages],
        "milestone_claimed": [m["done"] for m in miles],
        "raw": data,
    }
