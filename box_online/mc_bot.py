"""挂机执行体（轻量）—— **本版本不包含，留作后续开发**。

执行体要做的事：连上「进服对接层」为该挂机位准备的连接入口，
进世界 → 走到挂机点 → 保持在线 → 到点领奖 → 被要求停止时退出。

本版本只保留调用入口，方便后续实现替换。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

NOT_READY = "挂机执行体尚未实现（本版本不包含，留作后续开发）"


def run_afk_session(
    *,
    host: str,
    port: int,
    username: str,
    stop_flag: Callable[[], bool],
    on_state: Callable[..., None],
    **kwargs: Any,
) -> None:
    """执行一个挂机会话（占位实现）。"""
    raise NotImplementedError(NOT_READY)
