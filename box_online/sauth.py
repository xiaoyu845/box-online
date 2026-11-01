"""4399 账号 → sauth 转换与校验。

## 为什么需要它

挂机每次都要建立一条网易 SDK 会话。旧做法是每次挂机都请求一次
**4399 密码登录**（ptlogin.4399.com）——而密码登录正是最容易触发
图片验证码 / 二次验证的动作。N 个账号挂 N 次 = N 次密码登录。

改成「先转一次 sauth，再用 sauth 登录」之后：

    首次：密码登录 1 次 → 拿到 sauth → 落库
    之后：直接用 sauth 走凭据登录，**不再打 4399 密码登录**

即 N 次挂机只付 1 次密码登录成本。

## 边界（别误解）

sauth 缓存**只减少密码登录次数**，不改变设备与 IP 层面的特征：
`n4399` 里的 `DEVICE_FILE`（`~/.box-online/4399-device.cds`）和
`client_login_sn` 仍然整机共用，服务端该聚类还是能聚类。反检测不在本模块职责内。

## 数据形态

`login_with_password()` 返回一个 JSON 对象字符串，字段含
`sdkuid / sessionid / udid / login_channel / sdk_version ...`，
对接层的凭据解析 可以直接吃这个裸对象。
本模块另外兼容两种历史形态（`{"sauth_json": "<内层>"}` 包裹、二次编码）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ._vendor.n4399 import (  # noqa: F401 - CaptchaRequired/LoginError 对外转发
    CaptchaImage,
    CaptchaRequired,
    LoginError,
    captcha_image_to_data_url,
    get_captcha,
    login_with_password,
)

__all__ = [
    "CaptchaImage",
    "CaptchaRequired",
    "LoginError",
    "RATE_LIMIT_HINT",
    "SauthError",
    "captcha_image_to_data_url",
    "convert",
    "get_captcha",
    "age_label",
    "is_rate_limited",
    "normalize",
    "sdkuid_of",
    "session_label_of",
    "validate",
]


class SauthError(ValueError):
    """sauth 文本不合法（格式/缺字段/被打码）。"""


def _try_json(text: Any) -> Optional[Any]:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _unwrap(text: Any, depth: int = 4) -> Optional[Dict[str, Any]]:
    """逐层剥掉 JSON 字符串编码，直到得到 dict。

    覆盖这些历史形态（都是复制粘贴时真实出现过的）：
        {"sessionid": ...}                     ← 裸对象
        "{\\"sessionid\\": ...}"                 ← 整体被二次编码
        {"sauth_json": "<内层>"}                ← 包裹
        "{\\"sauth_json\\": \\"<内层>\\"}"          ← 包裹 + 二次编码
    """
    cur: Any = text
    for _ in range(depth):
        if isinstance(cur, str):
            cur = _try_json(cur.strip())
            if cur is None:
                return None
            continue
        if isinstance(cur, dict):
            return cur
        return None
    return None


def normalize(text: str) -> str:
    """把各种粘贴形态统一成裸 sauth JSON（紧凑、不含包裹层）。

    校验项对齐对接层的凭据解析：必须是 JSON 对象、必须有 sessionid、
    sessionid 不能含 `*`（打码态）。这样存进库的东西对接层一定能解析。
    """
    raw = (text or "").strip()
    if not raw:
        raise SauthError("sauth 为空")

    obj = _unwrap(raw)
    if obj is None:
        raise SauthError("sauth 不是有效 JSON 对象")

    inner = obj.get("sauth_json")
    if isinstance(inner, str):
        sub = _unwrap(inner)
        if sub is None:
            raise SauthError("sauth_json 内层不是有效 JSON 对象")
        obj = sub

    sid = str(obj.get("sessionid") or obj.get("sessionId") or "").strip()
    if not sid:
        raise SauthError("sauth 缺少 sessionid")
    if "*" in sid:
        raise SauthError("sessionid 看起来被打码了，请用原始未打码内容")
    obj["sessionid"] = sid
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def convert(
    account: str,
    password: str,
    *,
    captcha_id: str = "",
    captcha_code: str = "",
) -> str:
    """4399 账号密码 → 裸 sauth JSON 字符串。

    抛 `CaptchaRequired`（附 captcha_id，需人工填图）或 `LoginError`。
    返回值已经过 `normalize`，可直接落库、可直接交给对接层。
    """
    raw = login_with_password(account, password, captcha_id, captcha_code)
    return normalize(raw)


def sdkuid_of(sauth_text: str) -> str:
    """取 sdkuid（用于界面显示与去重对账），失败返回空串。"""
    try:
        obj = json.loads(normalize(sauth_text))
    except (SauthError, json.JSONDecodeError):
        return ""
    return str(obj.get("sdkuid") or "").strip()


def session_label_of(sauth_text: str) -> str:
    """给账号库显示用：`SAUTH:<sdkuid>`。"""
    uid = sdkuid_of(sauth_text)
    return f"SAUTH:{uid}" if uid else "SAUTH"


def age_label(sauth_at: float, now: Optional[float] = None) -> str:
    """sauth 缓存年龄，界面用。"""
    at = float(sauth_at or 0.0)
    if at <= 0:
        return "未转换"
    t = time.time() if now is None else float(now)
    sec = max(0, int(t - at))
    if sec < 60:
        return f"{sec} 秒前"
    if sec < 3600:
        return f"{sec // 60} 分钟前"
    if sec < 86400:
        return f"{sec // 3600} 小时前"
    return f"{sec // 86400} 天前"


def validate(sauth_text: str) -> Tuple[bool, str]:
    """只校验不改写：返回 (是否可用, 原因)。"""
    try:
        normalize(sauth_text)
    except SauthError as exc:
        return False, str(exc)
    return True, ""


# 4399 限流：loginAndAuthorize.do 返回 HTTP 202 + "请稍后再试~"
# n4399 见到 202/429 会退避重试（6s + 12s）后抛 LoginError("请稍后再试。")，
# 这句话对用户毫无信息量，单独识别出来给可操作提示。
_RATE_LIMIT_MARKERS = ("请稍后再试", "临时限制", "频繁")

RATE_LIMIT_HINT = (
    "4399 登录接口正在限流（HTTP 202）：\n"
    "这台机器/IP 短时间内密码登录太多次了，与账号本身无关"
    "（不存在的账号也会得到同样响应）。\n\n"
    "处理办法：等几分钟再试；批量转 SAUTH 请分批做，不要一次跑几十个号。"
)


def is_rate_limited(exc_or_text: object) -> bool:
    """判断是否 4399 登录限流。"""
    text = str(exc_or_text or "")
    return bool(text) and any(m in text for m in _RATE_LIMIT_MARKERS)
