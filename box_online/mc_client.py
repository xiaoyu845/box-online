"""挂机执行体（真实客户端）与目录工具 —— **本版本不包含，留作后续开发**。

本版本保留路径工具与命令写入函数的接口，执行体本身留待后续实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

NOT_READY = "真实客户端执行体尚未实现（本版本不包含，留作后续开发）"

DEFAULT_HANG_NAME = "Box挂机端"


def hang_install_root() -> Path:
    """挂机端运行目录（用于放 instances 与命令文件）。"""
    return Path.home() / "Desktop" / DEFAULT_HANG_NAME


def hang_instance_dir(index: int) -> Path:
    """某个挂机位的实例目录。"""
    return hang_install_root() / "instances" / f"slot{int(index):02d}"


def read_reward_file(index: int) -> Optional[dict]:
    """读取某个挂机位的奖励状态文件（占位：无数据）。"""
    return None


def _write_command(index: int, name: str, payload: str) -> Path:
    d = hang_instance_dir(index)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return d / name
    p = d / name
    try:
        p.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    return p


def write_claim_command(index: int, payload: str = "") -> Path:
    return _write_command(index, "claim.cmd", payload)


def write_claim_all_command(index: int, payload: str = "") -> Path:
    return _write_command(index, "claim_all.cmd", payload)


def write_claim_batch_command(index: int, payload: str = "") -> Path:
    return _write_command(index, "claim_batch.cmd", payload)


def run_real_client_session(*args: Any, **kwargs: Any) -> None:
    """启动真实客户端挂机（占位实现）。"""
    raise NotImplementedError(NOT_READY)
