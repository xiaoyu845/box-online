"""预热阶段的返回类型。

## 为什么要"预热"

用户提出的流程：**先把所有账号登录一遍，把需要验证码的集中弹出来填，
填完再让它们去跑挂机。**

动机成立：
- 登录是「脆、慢、被限流」的一步；挂机是「长」的一步。分开之后长跑阶段
  不会再被验证码弹窗打断。
- 密码错 / 号死了 / sauth 失效 / 被限流，**在挂机开始前**就全部暴露，
  而不是挂了几小时后发现某个槽一直没起来。
- 登录天然串行，不会 N 个槽同时去打 4399。

实测（2026-10-16）4399 密码登录：**一次成功后紧接着第二次就 HTTP 202**。
所以预热必须**优先 sauth**（不打密码接口、不吃限流），只有无 sauth / sauth
失效的账号才走密码登录，并且全程过 `gate.LoginGate`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# 每个账号的预热结果
READY = "ready"  # 登录成功、有角色，可直接挂
EMPTY = "empty"  # 登录成功但账号下没有角色 -> 需要先建角
CAPTCHA = "captcha"  # 卡在图片验证码 -> 等你填
FAILED = "failed"  # 其他失败（密码错/号死了/封禁…）
SKIPPED = "skipped"  # 因为闸门冷却而没轮到

STATUS_TEXT = {
    READY: "就绪",
    EMPTY: "无角色",
    CAPTCHA: "需验证码",
    FAILED: "失败",
    SKIPPED: "未轮到",
}


@dataclass
class WarmupItem:
    account_id: str
    label: str
    status: str
    detail: str = ""
    characters: List[str] = field(default_factory=list)
    # 仅 CAPTCHA 时有效（对接层已经把图和标识都给了）
    captcha_id: str = ""
    captcha_image_base64: str = ""
    captcha_content_type: str = "image/jpeg"
    via: str = ""  # sauth / password / password_fallback

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    def data_url(self) -> str:
        if not self.captcha_image_base64:
            return ""
        return f"data:{self.captcha_content_type};base64,{self.captcha_image_base64}"


@dataclass
class WarmupSummary:
    items: List[WarmupItem] = field(default_factory=list)
    stopped_for_cooldown: bool = False
    cooldown_text: str = ""

    def by_status(self, status: str) -> List[WarmupItem]:
        return [i for i in self.items if i.status == status]

    @property
    def ready(self) -> List[WarmupItem]:
        return self.by_status(READY)

    @property
    def captcha(self) -> List[WarmupItem]:
        return self.by_status(CAPTCHA)

    @property
    def empty(self) -> List[WarmupItem]:
        return self.by_status(EMPTY)

    @property
    def failed(self) -> List[WarmupItem]:
        return self.by_status(FAILED)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out

    def text(self) -> str:
        c = self.counts()
        parts = [
            f"就绪 {c.get(READY, 0)}",
            f"无角色 {c.get(EMPTY, 0)}",
            f"需验证码 {c.get(CAPTCHA, 0)}",
            f"失败 {c.get(FAILED, 0)}",
        ]
        if c.get(SKIPPED):
            parts.append(f"未轮到 {c[SKIPPED]}")
        line = "预热完成：" + " · ".join(parts)
        if self.stopped_for_cooldown:
            line += f"\n\n因 4399 限流提前停止。{self.cooldown_text}"
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "counts": self.counts(),
            "stopped_for_cooldown": self.stopped_for_cooldown,
            "cooldown_text": self.cooldown_text,
            "items": [
                {
                    "account_id": i.account_id,
                    "label": i.label,
                    "status": i.status,
                    "detail": i.detail,
                    "characters": i.characters,
                }
                for i in self.items
            ],
        }
