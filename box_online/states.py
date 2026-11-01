from __future__ import annotations

from enum import Enum


class SlotState(str, Enum):
    IDLE = "空闲"
    LOGGING_IN = "登录中"
    PICK_CHAR = "选角中"
    JOINING = "进服中"
    ENTERING_PORTAL = "进门中"
    AFK = "挂机中"
    CLAIMING = "领奖中"
    NEED_VERIFY = "需验证"
    ERROR = "异常"
    STOPPING = "停止中"


TERMINAL_OK = {SlotState.IDLE, SlotState.AFK}
# 可以被"自动补位/批量开窗"重新启动的状态。
#
# 注意：NEED_VERIFY **不在**这里——它表示"在等人工输入"（图片验证码/风险验证）。
# 曾经它被包含在内，于是 slot_is_busy() 把等待验证的槽算成"空闲"，
# char_loop 每 5 秒就把它重新塞回同一个账号、再撞一次验证码，
# 无限循环且每次都打一次 4399 登录（自己猛烧限流配额）。
# 手动重试不受影响：Scheduler.retry_slot() 会先把状态显式置回 IDLE。
CAN_START = {SlotState.IDLE, SlotState.ERROR}
