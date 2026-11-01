from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def app_dir() -> Path:
    """Directory containing the frozen exe or the box-online project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # .../box-online/box_online/paths.py → project root box-online/
    return Path(__file__).resolve().parent.parent


def detect_bundle_hang_root() -> Optional[Path]:
    """
    Portable layout: <挂机端>/Box-Online/...  with sibling .minecraft / PCL.
    Also accept running directly inside the hang root.
    """
    here = app_dir()
    candidates = [here, here.parent]
    # onedir PyInstaller: <挂机端>/Box-Online/Box-Online.exe
    if here.name.lower() in {"box-online", "_internal"}:
        candidates.append(here.parent)
        candidates.append(here.parent.parent)
    for cand in candidates:
        try:
            c = cand.resolve()
        except OSError:
            continue
        if (c / ".minecraft").is_dir() and (
            (c / "PCL").is_dir() or (c / "请读我-挂机专用.txt").is_file()
        ):
            return c
        # Panel folder named Box-Online under hang root
        if c.name.lower() in {"box-online"} and (c.parent / ".minecraft").is_dir():
            return c.parent.resolve()
    return None


DATA_DIRNAME = "box-online-data"
HOME_DIRNAME = ".box-online"

# 账号库文件特征：用来「按内容」认出已存在的数据目录，而不是靠固定目录名。
_DATA_MARKERS = ("accounts.json", "settings.json")


def _find_existing_data(parent: Path) -> Optional[Path]:
    """在 parent 下找一个**已经装着账号库**的子目录（不依赖目录叫什么名字）。

    这样即使项目改过名字、或用户把数据目录改了名，只要里面有账号库就能被认出来，
    不会出现「换个名字就读到空库、账号像丢了」的情况。
    """
    try:
        for d in sorted((x for x in parent.iterdir() if x.is_dir()), key=lambda x: x.name.lower()):
            if any((d / m).is_file() for m in _DATA_MARKERS):
                return d
    except OSError:
        pass
    return None


def _prefer(new: Path, existing: Optional[Path]) -> Path:
    """新目录已存在就用新的；否则沿用「按内容认出来的」已有数据目录。"""
    if new.is_dir():
        return new
    if existing is not None:
        return existing
    return new


def _desktop_dirs() -> list:
    """桌面上的候选安装目录（按名字排序，保证结果稳定）。"""
    desktop = Path.home() / "Desktop"
    try:
        return sorted((d for d in desktop.iterdir() if d.is_dir()), key=lambda d: d.name.lower())
    except OSError:
        return []


def hang_root() -> Optional[Path]:
    """挂机端目录：环境变量 -> 可执行文件位置 -> 按特征扫描桌面。

    不依赖固定目录名，只要「有 .minecraft，并且带 mf_bot 或数据目录」就认作挂机端。
    """
    for key in ("BOX_ONLINE_HANG_ROOT",):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).is_dir():
            return Path(raw)
    detected = detect_bundle_hang_root()
    if detected is not None:
        return detected
    for d in _desktop_dirs():
        if not (d / ".minecraft").is_dir():
            continue
        if (d / "mf_bot").is_dir():
            return d
    return None


def play_root() -> Optional[Path]:
    """日常游玩端：桌面上另一个有 .minecraft 的目录（用于一次性播种资源）。"""
    hang = hang_root()
    for d in _desktop_dirs():
        if (d / ".minecraft").is_dir() and (hang is None or d.resolve() != hang.resolve()):
            return d
    return None


def hang_dir_name() -> str:
    """挂机端目录名（检测不到时用中性默认名）。"""
    h = hang_root()
    return h.name if h is not None else "Box挂机端"


def data_home() -> Path:
    raw = (os.environ.get("BOX_ONLINE_HOME") or "").strip()
    if raw:
        return Path(raw)
    hang = hang_root()
    if hang is not None:
        # Keep settings/accounts next to the portable hang folder
        return _prefer(hang / DATA_DIRNAME, _find_existing_data(hang))
    return Path.home() / HOME_DIRNAME


def ensure_home() -> Path:
    home = data_home()
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def settings_path() -> Path:
    return ensure_home() / "settings.json"


def accounts_path() -> Path:
    return ensure_home() / "accounts.json"


def known_homes() -> list:
    """枚举所有可能的账号库目录（标签, 路径, 是否「面板实际在用」）。

    存在两份库是这套部署的常态：

    - **便携库** `<挂机端>/box-online-data/`：`启动挂机面板.bat/.vbs` 会把
      `BOX_ONLINE_HOME` 指到这里，所以双击面板读写的是它 —— 打包分发的正式路径。
    - **旧库** `~/.box-online/`：`BOX_ONLINE_HOME` 未设且检测不到挂机端目录时的回退，
      例如直接在源码目录里 `py -3 run.py` 或跑 CLI 工具。

    工具类代码（导入器等）必须用这个函数判断目标，否则很容易像 CLI 那样
    写进旧库、而面板读的是便携库，用户完全看不到导入结果。
    """
    out = []
    seen = set()

    env_raw = (os.environ.get("BOX_ONLINE_HOME") or "").strip()
    if env_raw:
        p = Path(env_raw)
        out.append(("env BOX_ONLINE_HOME", p, True))
        seen.add(str(p).lower())

    # 优先按已知挂机端位置找便携库（与 package_run.py / mc_client 一致）
    hang_raw = (os.environ.get("BOX_ONLINE_HANG_ROOT") or "").strip()
    hang_candidates = []
    if hang_raw:
        hang_candidates.append(Path(hang_raw))
    detected = detect_bundle_hang_root()
    if detected is not None:
        hang_candidates.append(detected)
    for d in _desktop_dirs():
        if (d / ".minecraft").is_dir() and (d / "mf_bot").is_dir():
            hang_candidates.append(d)

    for hang in hang_candidates:
        # 新名优先；再按内容识别已存在的数据目录（改过名字也找得到）
        for p in (hang / DATA_DIRNAME, _find_existing_data(hang)):
            if p is None:
                continue
            key = str(p).lower()
            if key in seen:
                continue
            if p.is_dir():
                out.append((f"便携 {hang.name}", p, True))
                seen.add(key)

    for legacy in (Path.home() / HOME_DIRNAME,):
        if str(legacy).lower() not in seen and legacy.is_dir():
            out.append((f"旧 {legacy.name}", legacy, False))

    return out


def preferred_home() -> Path:
    """工具应写入的库：优先便携库（面板实际读的那个），否则回退 data_home()。

    与 data_home() 的区别：data_home() 只看环境变量/当前可执行文件位置；
    这里再兜一层「桌面上的挂机端」，避免从源码目录跑工具时写错库。
    """
    homes = known_homes()
    for _label, path, is_active in homes:
        if is_active:
            return path
    return data_home()



DEFAULT_SETTINGS: Dict[str, Any] = {
    "slot_count": 8,
    "max_slots": 32,
    "start_stagger_sec": 3.0,
    "same_account_stagger_sec": 8.0,
    "bridge_host": "127.0.0.1",
    "bridge_port": 0,
    "lost_world_keyword": "示例服",
    "portal_timeout_sec": 90,
    "portal_walk_min_sec": 6.0,
    "portal_baritone_blocks": 40,  # 兼容旧字段；现用固定目标坐标
    "portal_target_x": -1.0,
    "portal_target_y": 106.0,
    "portal_target_z": 8.0,
    "portal_join_delay_sec": 3.0,  # 进服后等 N 秒再记进服坐标并开始寻路
    # xyz=目标服「世界之门」固定坐标；end_portal=原版末地门（轻量扫描）
    "portal_mode": "xyz",
    "portal_scan_radius": 16,
    "hang_mode": "protocol",  # protocol=轻量执行体协议挂机 | client=挂机端Forge
    # 可选：模组信息覆盖（执行体实现时使用）
    "forge_mods": [],
    # 仅作首次复制种子；挂机端已就绪后不需要
    "mc_install_root": "",
    # 可移植：空=自动（环境变量 / 捆绑目录 / 桌面默认名）
    "hang_install_root": "",
    "mc_xmx_mb": 896,  # 挂机堆；Direct≈384，优先进服稳定
    "claim_interval_sec": 45,
    "reconnect_max": 3,
    # 循环挂角（角色队列）
    "char_loop_min_online_min": 123,
    "char_loop_claim_retry_sec": 45,
    "char_loop_tick_sec": 5.0,
    # 单次补位最多开几个号；同时在线窗口上限（领完关窗再接下一个）
    "char_loop_launch_batch": 2,
    "char_loop_max_windows": 4,
    # 面板调度模式：manual=首次代理+追加 | loop=循环挂角
    "hang_work_mode": "manual",
}


def load_settings() -> Dict[str, Any]:
    path = settings_path()
    data = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except (OSError, json.JSONDecodeError):
            pass
    data["slot_count"] = max(1, min(int(data.get("max_slots", 32)), int(data.get("slot_count", 8))))
    return data


def save_settings(data: Dict[str, Any]) -> None:
    path = settings_path()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data or {})
    merged["slot_count"] = max(1, min(int(merged.get("max_slots", 32)), int(merged.get("slot_count", 8))))
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
