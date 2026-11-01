"""进服对接层 —— **本版本不包含，留作后续开发**。

本仓库是最初版本，只包含：面板、账号库、导入、登录节流、槽位调度、
循环挂角、在线奖励与执行体接口。

与「游戏登录 / 进服」相关的对接**不在本版本内**。要接入自己的实现，
保持下面的接口签名不变即可，其余模块不需要改动：

    BridgeClient.login(...)            登录一个账号
    BridgeClient.sessions/select_session/logout     会话管理
    BridgeClient.servers/select_server  选服
    BridgeClient.characters/create_character        角色
    BridgeClient.join(...)              为某个挂机位准备连接入口
    BridgeClient.wait_proxy/is_proxy_running        入口就绪判断

约定：
- 需要人工验证时抛 :class:`CaptchaNeeded`（带图片与标识）
- 其余失败抛 :class:`BridgeError`
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

NOT_READY = "进服对接尚未实现（本版本不包含，留作后续开发）"


class BridgeError(RuntimeError):
    """对接层的通用错误。"""

    def __init__(self, message: str, *, verify_url: str = "") -> None:
        super().__init__(message)
        self.verify_url = verify_url


class CaptchaNeeded(BridgeError):
    """需要人工输入图片验证码。"""

    def __init__(
        self,
        message: str = "需要图片验证码",
        *,
        captcha_id: str = "",
        image_base64: str = "",
        content_type: str = "image/png",
        captcha_url: str = "",
        captcha_path: str = "",
        account: str = "",
        label: str = "",
    ) -> None:
        super().__init__(message)
        self.captcha_id = captcha_id
        self.image_base64 = image_base64
        self.content_type = content_type or "image/png"
        self.captcha_url = captcha_url
        self.captcha_path = captcha_path
        self.account = account
        self.label = label

    @classmethod
    def from_response(cls, resp: Dict[str, Any], *, account: str = "", label: str = "") -> "CaptchaNeeded":
        resp = resp or {}
        return cls(
            str(resp.get("message") or resp.get("error") or "需要图片验证码"),
            captcha_id=str(resp.get("captcha_id") or ""),
            image_base64=str(resp.get("captcha_image_base64") or ""),
            content_type=str(resp.get("captcha_content_type") or "image/png"),
            captcha_url=str(resp.get("captcha_url") or ""),
            captcha_path=str(resp.get("captcha_path") or ""),
            account=account,
            label=label,
        )

    def data_url(self) -> str:
        if not self.image_base64:
            return ""
        return f"data:{self.content_type};base64,{self.image_base64}"

    def image_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.image_base64 or "")
        except Exception:  # noqa: BLE001
            return b""


class BridgeClient:
    """进服组件客户端（接口占位，方法未实现）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, token: str = "") -> None:
        self.host = host
        self.port = int(port or 0)
        self.token = token
        self.base = f"http://{host}:{self.port}" if self.port else ""

    # --- 健康 / 状态 ---
    def health(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {"ok": False, "message": NOT_READY}

    # --- 登录与会话 ---
    def login(self, method: str, account: str = "", password: str = "", sms_code: str = "",
              sauth: str = "", captcha_id: str = "", captcha_code: str = "") -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    def sessions(self) -> List[Dict[str, Any]]:
        return []

    def select_session(self, session_id: str) -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    def logout(self, session_id: str = "") -> Dict[str, Any]:
        return {"ok": True}

    # --- 选服 / 角色 ---
    def servers(self, *, all_servers: bool = False) -> List[Dict[str, Any]]:
        return []

    def select_server(self, server_id: str, server_name: str = "") -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    def characters(self) -> List[Dict[str, Any]]:
        return []

    def create_character(self, name: str) -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    # --- 进服 ---
    def join(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    def send_sms(self, phone: str) -> Dict[str, Any]:
        raise NotImplementedError(NOT_READY)

    def wait_proxy(self, port: int, timeout_sec: float = 45.0) -> bool:
        return False

    def is_proxy_running(self, port: int) -> bool:
        return False


def load_bridge_client() -> BridgeClient:
    """返回占位客户端（真实实现由后续开发提供）。"""
    return BridgeClient()


def ensure_bridge_running(*, timeout_sec: float = 20.0) -> Tuple[BridgeClient, str]:
    """占位：不启动任何外部程序，直接返回占位客户端。"""
    return BridgeClient(), NOT_READY


def pick_server(servers: List[Dict[str, Any]], keyword: str = "示例服") -> Optional[Dict[str, Any]]:
    """按关键词挑服务器；匹配不上返回 None（由调用方报错，不静默兜底）。"""
    key = (keyword or "").strip().lower().replace(" ", "")
    if not key:
        return None
    for s in servers or []:
        name = str(s.get("name") or s.get("server_name") or "")
        if key in name.lower().replace(" ", ""):
            return s
    return None


pick_lost_world = pick_server
