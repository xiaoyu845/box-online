from __future__ import annotations

import csv
import functools
import io
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .paths import accounts_path, ensure_home


def _as_bool_flag(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def _as_char_row(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    name = str(item.get("name") or item.get("character") or "").strip()
    if not name:
        return {}
    return {
        "name": name,
        "enabled": _as_bool_flag(item.get("enabled"), True),
        "online_done": _as_bool_flag(item.get("online_done"), False),
        "hang_status": str(item.get("hang_status") or "未挂机").strip() or "未挂机",
        "sign_count": int(item.get("sign_count") or 0),
        "online_ms_server": int(item.get("online_ms_server") or 0),
        "online_ms_sampled_at": float(item.get("online_ms_sampled_at") or 0),
        "hang_session_sec": float(item.get("hang_session_sec") or 0),
        "online_ms_at_hang_start": int(item.get("online_ms_at_hang_start") or 0),
        "updated_at": float(item.get("updated_at") or 0),
    }

METHODS = (
    "netease_email",
    "netease_phone_password",
    "netease_phone_sms",
    "4399",
    "sauth",
)

METHOD_LABELS = {
    "netease_email": "网易邮箱",
    "netease_phone_password": "手机+密码",
    "netease_phone_sms": "手机+验证码",
    "4399": "4399",
    "sauth": "SAUTH/Cookie",
}


@dataclass
class Account:
    id: str = ""
    method: str = "netease_email"
    account: str = ""
    password: str = ""
    remark: str = ""
    character: str = ""
    enabled: bool = True
    # 游玩保护：永不参与挂机调度（避免同号顶掉自己的游玩端）
    play_protect: bool = False
    last_status: str = ""
    last_verify_url: str = ""
    # 挂在线进度
    hang_status: str = "未挂机"  # 未挂机 / 挂机中 / 已挂完 / 需验证 / 异常
    stage_text: str = ""
    online_done: bool = False
    last_hang_at: float = 0.0
    session_label: str = ""
    updated_at: float = 0.0
    # 在线时长（毫秒）：服务器记录 / 采样时刻 / 本次挂机累计秒
    online_ms_server: int = 0
    online_ms_sampled_at: float = 0.0
    hang_session_sec: float = 0.0
    online_ms_at_hang_start: int = 0
    # 累计次数（里程碑用，界面称「签到次数」）
    sign_count: int = 0
    # 4399 → sauth 缓存：避免每次挂机都打一次 4399 密码登录（最易触发验证码的动作）
    sauth: str = ""
    sauth_at: float = 0.0
    # 账号下各角色：签到次数 + 时长
    characters_data: List[Dict[str, Any]] = field(default_factory=list)

    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method)

    # ----- 登录方式选择 -----

    def effective_login(self) -> Tuple[str, str, str, str]:
        """决定实际交给对接层的 (method, account, password, sauth)。

        优先 sauth：有缓存 sauth 的 4399 账号走 `method="sauth"`，
        不再去请求 4399 密码登录。对接层的凭据登录是现成能力，无需改动对接层。

        - 旧式手填 sauth 账号（method="sauth"，凭据存在 account）照旧
        - 无缓存 sauth 的 4399 账号回退到密码登录
        """
        if self.method == "sauth":
            return ("sauth", "", "", (self.sauth or self.account or "").strip())
        if self.method == "4399" and (self.sauth or "").strip():
            return ("sauth", "", "", self.sauth.strip())
        return (self.method, self.account, self.password, "")

    def password_login(self) -> Tuple[str, str, str, str]:
        """强制走原始方式（不含 sauth）——sauth 失效时的一次性回退。"""
        if self.method == "sauth":
            return ("sauth", "", "", (self.sauth or self.account or "").strip())
        return (self.method, self.account, self.password, "")

    def has_sauth(self) -> bool:
        return bool((self.sauth or "").strip())

    def sauth_age_text(self) -> str:
        """sauth 缓存年龄（界面用）。"""
        from .sauth import age_label

        return age_label(self.sauth_at)

    def sauth_state_text(self) -> str:
        """登录方式列：显示是否会走 sauth 复用。"""
        if self.method == "sauth":
            return "SAUTH(手填)"
        if self.method == "4399" and self.has_sauth():
            # sauth_at=0 说明是外部导入的、不知道转换时间，别显示成「未转换」
            if float(self.sauth_at or 0.0) <= 0:
                return "SAUTH 复用"
            return f"SAUTH 复用 · {self.sauth_age_text()}"
        if self.method == "4399":
            return "密码登录"
        return "—"

    def display(self) -> str:
        note = (self.remark or "").strip()
        acc = (self.account or "").strip()
        if note and acc:
            return f"{note} · {acc}"
        return note or acc or self.id

    def hang_display(self) -> str:
        if self.online_done:
            return "已挂完"
        return self.hang_status or "未挂机"

    def estimated_online_ms(self, now: Optional[float] = None) -> int:
        """服务器时长 + 自上次采样后的流逝。"""
        base = max(0, int(self.online_ms_server))
        if self.online_ms_sampled_at <= 0:
            return base
        t = time.time() if now is None else float(now)
        return base + int(max(0.0, t - self.online_ms_sampled_at) * 1000)

    def time_parts(self, now: Optional[float] = None) -> tuple:
        """三段式时长：(登录, 挂机, 合计)。"""
        from .claim import fmt_ms, fmt_sec

        t = time.time() if now is None else float(now)
        login = fmt_ms(self.online_ms_at_hang_start or self.online_ms_server)
        hang = fmt_sec(self.hang_session_sec)
        total = fmt_ms(self.estimated_online_ms(t))
        return login, hang, total

    def time_summary(self, now: Optional[float] = None) -> str:
        login, hang, total = self.time_parts(now)
        return f"登录 {login} · 挂机 {hang} · 合计 {total}"

    def chars_sign_text(self) -> str:
        rows = list(self.characters_data or [])
        if not rows and self.character:
            return f"{self.character}:{int(self.sign_count or 0)}"
        if not rows:
            return "—"
        return " · ".join(
            f"{r.get('name') or '?'}:{int(r.get('sign_count') or 0)}" for r in rows if r.get("name")
        )

    def chars_status_text(self) -> str:
        """角色级状态摘要：角A:挂机中 · 角B:已挂完"""
        rows = list(self.characters_data or [])
        if not rows and self.character:
            st = "已挂完" if self.online_done else (self.hang_status or "未挂机")
            return f"{self.character}:{st}"
        if not rows:
            return "—"
        parts = []
        for r in rows:
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            if r.get("online_done"):
                st = "已挂完"
            else:
                st = str(r.get("hang_status") or "未挂机")
            en = _as_bool_flag(r.get("enabled"), True)
            if not en:
                st = f"停用/{st}"
            parts.append(f"{name}:{st}")
        return " · ".join(parts) if parts else "—"

    def iter_character_names(self) -> List[str]:
        rows = list(self.characters_data or [])
        names = [str(r.get("name") or "").strip() for r in rows if str(r.get("name") or "").strip()]
        if names:
            return names
        if (self.character or "").strip():
            return [(self.character or "").strip()]
        return []

    def has_characters(self) -> bool:
        """是否有任何角色。空号排不进挂机队列（队列按「账号+角色」出单元）。"""
        return bool(self.iter_character_names())

    def char_state_text(self) -> str:
        """角色状态列。空号要显眼——它启用了也挂不了，得先建角。"""
        if not self.has_characters():
            return "⚠ 无角色（需建角）"
        return self.chars_status_text()

    def chars_time_text(self) -> str:
        from .claim import fmt_ms

        rows = list(self.characters_data or [])
        if not rows:
            return "—"
        parts = []
        for r in rows:
            name = r.get("name") or "?"
            ms = int(r.get("online_ms_server") or 0)
            parts.append(f"{name}:{fmt_ms(ms if ms > 0 else None)}")
        return " · ".join(parts)


class AccountStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        ensure_home()
        self.path = path or accounts_path()
        self.accounts: List[Account] = []
        # 可重入锁：本类被 4 类线程共用（UI / 调度器 worker / char_loop / sauth 转换）。
        # 用 RLock 是因为 add()->save()、update()->save() 这类嵌套调用要能重入。
        # 见文件末尾的 _guard_account_store()。
        self._lock = threading.RLock()
        # 读取时无法解析、但已用最小字段保留下来的记录说明（绝不静默丢号）
        self.load_issues: List[str] = []
        # 最近一次 save 被防呆拒绝的原因（空 = 正常）
        self.last_save_refused = ""
        # 显式允许"变少"的窗口（删除账号 / replace=True 导入）
        self._allow_shrink = False
        self.load()

    def load(self) -> None:
        # 先把新列表整个建好，最后一次性替换 self.accounts。
        # 旧实现是"self.accounts = [] 然后逐条 append"，别的线程在这个窗口里
        # 读到的是半截（甚至空）列表 —— 实测会直接 IndexError。
        parsed: List[Account] = []
        issues: List[str] = []
        raw = None
        trailing = 0
        if self.path.exists():
            text = ""
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                # 文件尾部有垃圾（实测见过：合法 JSON + 41 字符残尾，
                # 特征是非截断写入）。**绝不能因此返回空库** ——
                # 那会让面板显示 0 个账号，紧接着一次 save 就等于清库。
                # 这里尽力抢救出第一段完整 JSON。
                try:
                    raw, end = json.JSONDecoder().raw_decode(text.lstrip())
                    trailing = max(0, len(text.lstrip()) - end)
                    issues.append(
                        f"文件尾部有 {trailing} 字符多余内容，已抢救出前 {len(raw)} 条记录"
                    )
                except (ValueError, TypeError):
                    raw = None
        items = raw if isinstance(raw, list) else (raw or {}).get("accounts", [])
        for idx, item in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(self._from_dict(item))
            except Exception as exc:  # noqa: BLE001
                # 一条脏记录**不能**带走后面的账号：
                # 旧实现直接抛出去，self.accounts 只剩前半截，
                # 随后任何一次 save() 就会把丢掉的号永久写回磁盘（实际丢过 20 个）。
                try:
                    fallback = self._from_dict_minimal(item)
                except Exception:  # noqa: BLE001
                    fallback = None
                if fallback is not None:
                    parsed.append(fallback)
                    issues.append(
                        f"第 {idx + 1} 条只恢复了账号/密码（{exc.__class__.__name__}: {exc}）"
                    )
                else:
                    issues.append(
                        f"第 {idx + 1} 条无法解析，已跳过（{exc.__class__.__name__}: {exc}）"
                    )
        self.accounts = parsed
        self.load_issues = issues

    @staticmethod
    def _from_dict_minimal(item: Dict[str, Any]) -> Optional[Account]:
        """保命解析：只取最关键字段，尽量别把号丢掉。"""
        acct = str(item.get("account") or "").strip()
        pwd = str(item.get("password") or "").strip()
        if not acct and not pwd:
            return None
        return Account(
            id=str(item.get("id") or uuid.uuid4().hex[:12]),
            method=str(item.get("method") or "netease_email"),
            account=acct,
            password=pwd,
            remark=str(item.get("remark") or "") + "（读取异常，已按最小字段恢复）",
            enabled=False,
        )

    def _count_on_disk(self) -> int:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        items = raw if isinstance(raw, list) else raw.get("accounts", [])
        return len(items) if isinstance(items, list) else 0

    def _backup_disk_file(self, tag: str) -> Optional[Path]:
        if not self.path.exists():
            return None
        dest = self.path.with_name(f"{self.path.name}.{tag}-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            shutil.copy2(self.path, dest)
            return dest
        except OSError:
            return None

    def save(self, *, allow_shrink: bool = False) -> bool:
        """原子写入账号库。返回是否真的写入。

        两道防护：
        * 原子：先写 .tmp 再 os.replace，避免写一半留下半个文件。
        * 防缩水：新列表若不到磁盘上的一半，除非显式允许，**拒绝写入**并备份磁盘文件。
          这是为了不再发生「某进程内存里只有 2 个号，把磁盘上 22 个号覆盖掉」。
        """
        payload = [asdict(a) for a in self.accounts]
        if not (allow_shrink or self._allow_shrink):
            on_disk = self._count_on_disk()
            if on_disk >= 4 and len(payload) * 2 < on_disk:
                backup = self._backup_disk_file("shrink-guard")
                self.last_save_refused = (
                    f"拒绝保存：内存里只有 {len(payload)} 条，磁盘上有 {on_disk} 条；"
                    f"已备份到 {backup.name if backup else '（备份失败）'}"
                )
                return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self.last_save_refused = ""
        return True

    def add(self, **kwargs: Any) -> Account:
        acc = self._from_dict({**kwargs, "id": kwargs.get("id") or uuid.uuid4().hex[:12]})
        acc.updated_at = time.time()
        self.accounts.append(acc)
        self.save()
        return acc

    def upsert_login(
        self,
        *,
        method: str,
        account: str,
        password: str = "",
        remark: str = "",
        session_label: str = "",
    ) -> Account:
        """登录成功后写入账号库：同 method+account 则更新，否则新增。"""
        method = method or "netease_email"
        account = (account or "").strip()
        for acc in self.accounts:
            if acc.method == method and acc.account == account:
                acc.password = password if password else acc.password
                if remark:
                    acc.remark = remark
                if session_label:
                    acc.session_label = session_label
                acc.last_status = "登录成功，已入库"
                acc.hang_status = acc.hang_status or "未挂机"
                acc.enabled = True
                acc.updated_at = time.time()
                self.save()
                return acc
        return self.add(
            method=method,
            account=account,
            password=password,
            remark=remark,
            session_label=session_label,
            last_status="登录成功，已入库",
            hang_status="未挂机",
            enabled=True,
        )

    def set_sauth(self, account_id: str, sauth_text: str, *, status: str = "SAUTH 已更新") -> Optional[Account]:
        """写入/刷新 sauth 缓存。调用方负责先 normalize 校验。"""
        acc = self.get(account_id)
        if not acc:
            return None
        acc.sauth = (sauth_text or "").strip()
        acc.sauth_at = time.time()
        acc.last_status = status
        acc.updated_at = time.time()
        self.save()
        return acc

    def clear_sauth(self, account_id: str, *, status: str = "SAUTH 已清除") -> Optional[Account]:
        """清掉 sauth 缓存（下次挂机回退密码登录）。"""
        acc = self.get(account_id)
        if not acc:
            return None
        acc.sauth = ""
        acc.sauth_at = 0.0
        acc.last_status = status
        acc.updated_at = time.time()
        self.save()
        return acc

    def update(self, account_id: str, **kwargs: Any) -> Optional[Account]:
        for acc in self.accounts:
            if acc.id != account_id:
                continue
            for key, val in kwargs.items():
                if hasattr(acc, key):
                    setattr(acc, key, val)
            acc.updated_at = time.time()
            self.save()
            return acc
        return None

    def remove(self, account_id: str) -> bool:
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.id != account_id]
        if len(self.accounts) == before:
            return False
        # 删除是**显式**操作，允许账号数变少
        self.save(allow_shrink=True)
        return True

    def get(self, account_id: str) -> Optional[Account]:
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        return None

    def enabled_accounts(self) -> List[Account]:
        return [
            a
            for a in self.accounts
            if a.enabled and not a.play_protect and (a.account or a.method == "sauth")
        ]

    def empty_accounts(self) -> List[Account]:
        """没有任何角色的账号：启用也进不了挂机队列，需要先建角。"""
        return [a for a in self.accounts if not a.has_characters()]

    def enabled_characters(self, *, include_done: bool = False) -> List[tuple]:
        """队列单元：(Account, character_name)。账号启用且角色启用；默认跳过已挂完。"""
        out: List[tuple] = []
        for acc in self.enabled_accounts():
            rows = list(acc.characters_data or [])
            if not rows:
                name = (acc.character or "").strip()
                if not name:
                    continue
                if not include_done and acc.online_done:
                    continue
                out.append((acc, name))
                continue
            for r in rows:
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                if not _as_bool_flag(r.get("enabled"), True):
                    continue
                if not include_done and r.get("online_done"):
                    continue
                out.append((acc, name))
        return out

    def account_ids_blocked_by_play(
        self, play_usernames: Optional[Set[str]] = None
    ) -> Set[str]:
        """
        游玩端正在用的角色 → 整账号禁挂（同号登录会顶游玩端）。
        另含手动勾选 play_protect 的账号。
        """
        blocked: Set[str] = {a.id for a in self.accounts if a.play_protect and a.id}
        names = {str(n).strip() for n in (play_usernames or set()) if str(n).strip()}
        if not names:
            return blocked
        names_l = {n.lower() for n in names}
        for acc in self.accounts:
            if not acc.id:
                continue
            chars: List[str] = []
            if acc.character:
                chars.append(str(acc.character).strip())
            for r in acc.characters_data or []:
                n = str(r.get("name") or "").strip()
                if n:
                    chars.append(n)
            if any(c.lower() in names_l for c in chars if c):
                blocked.add(acc.id)
        return blocked

    def enabled_characters_safe(
        self,
        *,
        include_done: bool = False,
        play_usernames: Optional[Set[str]] = None,
    ) -> List[tuple]:
        """enabled_characters，并跳过游玩保护 / 游玩端在线同号。"""
        blocked = self.account_ids_blocked_by_play(play_usernames)
        return [
            (acc, name)
            for acc, name in self.enabled_characters(include_done=include_done)
            if acc.id not in blocked
        ]

    def set_character_flags(
        self,
        account_id: str,
        character: str,
        *,
        enabled: Optional[bool] = None,
        online_done: Optional[bool] = None,
        hang_status: Optional[str] = None,
    ) -> Optional[Account]:
        """更新单角色启用/挂完/状态。"""
        acc = self.get(account_id)
        if not acc:
            return None
        name = (character or "").strip()
        if not name:
            return acc
        rows = list(acc.characters_data or [])
        row = None
        for r in rows:
            if str(r.get("name") or "") == name:
                row = r
                break
        if row is None:
            row = _as_char_row({"name": name})
            rows.append(row)
        changed = False
        if enabled is not None and bool(row.get("enabled", True)) != bool(enabled):
            row["enabled"] = bool(enabled)
            changed = True
        if online_done is not None and bool(row.get("online_done")) != bool(online_done):
            row["online_done"] = bool(online_done)
            changed = True
            if online_done:
                row["hang_status"] = "已挂完"
        if hang_status is not None and str(row.get("hang_status") or "") != str(hang_status):
            row["hang_status"] = str(hang_status)
            changed = True
        if not changed:
            return acc
        row["updated_at"] = time.time()
        acc.characters_data = rows
        # 账号级汇总：所有启用角色都挂完 → 账号 online_done
        enabled_rows = [
            r
            for r in rows
            if str(r.get("name") or "").strip() and _as_bool_flag(r.get("enabled"), True)
        ]
        if enabled_rows and all(bool(r.get("online_done")) for r in enabled_rows):
            acc.online_done = True
            acc.hang_status = "已挂完"
        elif online_done is False:
            acc.online_done = False
            if acc.hang_status == "已挂完":
                acc.hang_status = "未挂机"
        acc.updated_at = time.time()
        self.save()
        return acc

    def find_by_login(self, method: str, account: str) -> Optional[Account]:
        for acc in self.accounts:
            if acc.method == method and acc.account == account:
                return acc
        return None

    def ensure_characters(self, account_id: str, names: List[str]) -> Optional[Account]:
        """Seed character rows when bridge lists roles (sign/time filled later)."""
        acc = self.get(account_id)
        if not acc:
            return None
        existing = {str(r.get("name") or ""): r for r in (acc.characters_data or [])}
        changed = False
        for name in names:
            name = (name or "").strip()
            if not name or name in existing:
                continue
            existing[name] = _as_char_row({"name": name})
            changed = True
        if changed:
            acc.characters_data = list(existing.values())
            acc.updated_at = time.time()
            self.save()
        return acc

    def upsert_character_progress(
        self,
        account_id: str,
        *,
        character: str = "",
        sign_count: Optional[int] = None,
        online_ms_server: Optional[int] = None,
        online_ms_sampled_at: Optional[float] = None,
        hang_session_sec: Optional[float] = None,
        online_ms_at_hang_start: Optional[int] = None,
    ) -> Optional[Account]:
        """Update account-level + per-character sign/time (三段数据源)."""
        acc = self.get(account_id)
        if not acc:
            return None
        now = time.time()
        name = (character or acc.character or "").strip()
        changed = False

        def _set(obj: Any, key: str, val: Any) -> None:
            nonlocal changed
            if isinstance(obj, dict):
                if obj.get(key) != val:
                    obj[key] = val
                    changed = True
            else:
                if getattr(obj, key) != val:
                    setattr(obj, key, val)
                    changed = True

        if sign_count is not None and int(sign_count) >= 0:
            _set(acc, "sign_count", int(sign_count))
        if online_ms_server is not None and int(online_ms_server) >= 0:
            _set(acc, "online_ms_server", int(online_ms_server))
        if online_ms_sampled_at is not None:
            # 采样时间几乎每秒变：仅随数值变化一起写，单独不刷盘
            acc.online_ms_sampled_at = float(online_ms_sampled_at)
        if hang_session_sec is not None:
            # 挂机秒数每秒变：整秒变化才落盘
            new_hang = float(int(float(hang_session_sec)))
            if int(acc.hang_session_sec) != int(new_hang):
                acc.hang_session_sec = new_hang
                changed = True
        if online_ms_at_hang_start is not None and int(online_ms_at_hang_start) >= 0:
            _set(acc, "online_ms_at_hang_start", int(online_ms_at_hang_start))
        if name:
            rows = list(acc.characters_data or [])
            row = None
            for r in rows:
                if str(r.get("name") or "") == name:
                    row = r
                    break
            if row is None:
                row = _as_char_row({"name": name})
                rows.append(row)
                changed = True
            if sign_count is not None and int(sign_count) >= 0:
                _set(row, "sign_count", int(sign_count))
            if online_ms_server is not None and int(online_ms_server) >= 0:
                _set(row, "online_ms_server", int(online_ms_server))
            if online_ms_sampled_at is not None:
                row["online_ms_sampled_at"] = float(online_ms_sampled_at)
            if hang_session_sec is not None:
                nh = float(int(float(hang_session_sec)))
                if int(row.get("hang_session_sec") or 0) != int(nh):
                    row["hang_session_sec"] = nh
                    changed = True
            if online_ms_at_hang_start is not None and int(online_ms_at_hang_start) >= 0:
                _set(row, "online_ms_at_hang_start", int(online_ms_at_hang_start))
            if changed:
                row["updated_at"] = now
            acc.characters_data = rows
            if not acc.character:
                acc.character = name
                changed = True
        if not changed:
            return acc
        # 签到变化立刻落盘；纯时长最多 12s 写一次，避免拖卡 UI
        important = sign_count is not None
        last_save = float(getattr(acc, "_last_progress_save", 0.0) or 0.0)
        if not important and (now - last_save) < 12.0:
            acc.updated_at = now
            return acc
        acc.updated_at = now
        setattr(acc, "_last_progress_save", now)
        self.save()
        return acc

    def export_csv(self, path: Path) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "method",
                    "account",
                    "password",
                    "remark",
                    "character",
                    "enabled",
                    "hang_status",
                    "online_done",
                    "sign_count",
                    "chars_sign",
                    "sauth_state",
                    "login_time",
                    "hang_time",
                    "total_time",
                ],
            )
            w.writeheader()
            for a in self.accounts:
                login, hang, total = a.time_parts()
                w.writerow(
                    {
                        "method": a.method,
                        "account": a.account,
                        "password": a.password,
                        "remark": a.remark,
                        "character": a.character,
                        "enabled": "1" if a.enabled else "0",
                        "hang_status": a.hang_status,
                        "online_done": "1" if a.online_done else "0",
                        "sign_count": a.sign_count,
                        "chars_sign": a.chars_sign_text(),
                        # 只导出状态，不导出 sauth 原文（那是凭据，别落进表格）
                        "sauth_state": a.sauth_state_text(),
                        "login_time": login,
                        "hang_time": hang,
                        "total_time": total,
                    }
                )

    def import_csv(self, path: Path, *, replace: bool = False) -> int:
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        # replace=True 是**显式**的整体替换，允许账号数变少（每行 add() 会各自 save）
        prev_allow = self._allow_shrink
        self._allow_shrink = replace
        try:
            return self._import_csv_rows(reader, replace)
        finally:
            self._allow_shrink = prev_allow

    def _import_csv_rows(self, reader, replace: bool) -> int:
        if replace:
            self.accounts = []
            self.save(allow_shrink=True)
        n = 0
        for row in reader:
            method = (row.get("method") or "netease_email").strip()
            account = (row.get("account") or "").strip()
            if not account and method != "sauth":
                continue
            self.add(
                method=method,
                account=account,
                password=(row.get("password") or "").strip(),
                remark=(row.get("remark") or "").strip(),
                character=(row.get("character") or "").strip(),
                enabled=str(row.get("enabled", "1")).strip() not in ("0", "false", "False", "no"),
                hang_status=(row.get("hang_status") or "未挂机").strip(),
                online_done=str(row.get("online_done", "0")).strip() in ("1", "true", "True", "yes"),
            )
            n += 1
        return n

    def export_xlsx(self, path: Path) -> None:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "accounts"
        ws.append(
            [
                "method",
                "account",
                "password",
                "remark",
                "character",
                "enabled",
                "hang_status",
                "online_done",
                "sign_count",
                "chars_sign",
                "sauth_state",
                "login_time",
                "hang_time",
                "total_time",
            ]
        )
        for a in self.accounts:
            login, hang, total = a.time_parts()
            ws.append(
                [
                    a.method,
                    a.account,
                    a.password,
                    a.remark,
                    a.character,
                    1 if a.enabled else 0,
                    a.hang_status,
                    1 if a.online_done else 0,
                    a.sign_count,
                    a.chars_sign_text(),
                    a.sauth_state_text(),
                    login,
                    hang,
                    total,
                ]
            )
        wb.save(path)

    def import_xlsx(self, path: Path, *, replace: bool = False) -> int:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return 0
        header = [str(c or "").strip().lower() for c in rows[0]]
        # replace=True 是**显式**的整体替换，允许账号数变少
        prev_allow = self._allow_shrink
        self._allow_shrink = replace
        try:
            return self._import_xlsx_rows(header, rows, replace)
        finally:
            self._allow_shrink = prev_allow

    def _import_xlsx_rows(self, header, rows, replace: bool) -> int:
        if replace:
            self.accounts = []
            self.save(allow_shrink=True)
        n = 0
        for row in rows[1:]:
            data = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
            method = str(data.get("method") or "netease_email").strip()
            account = str(data.get("account") or "").strip()
            if not account and method != "sauth":
                continue
            enabled_raw = data.get("enabled", 1)
            enabled = str(enabled_raw).strip() not in ("0", "false", "False", "no", "")
            done_raw = data.get("online_done", 0)
            online_done = str(done_raw).strip() in ("1", "true", "True", "yes")
            self.add(
                method=method,
                account=account,
                password=str(data.get("password") or "").strip(),
                remark=str(data.get("remark") or "").strip(),
                character=str(data.get("character") or "").strip(),
                enabled=enabled,
                hang_status=str(data.get("hang_status") or "未挂机").strip(),
                online_done=online_done,
            )
            n += 1
        return n

    @staticmethod
    def _from_dict(item: Dict[str, Any]) -> Account:
        chars_raw = item.get("characters_data") or item.get("characters") or []
        chars: List[Dict[str, Any]] = []
        if isinstance(chars_raw, list):
            for c in chars_raw:
                row = _as_char_row(c)
                if row:
                    chars.append(row)
        return Account(
            id=str(item.get("id") or uuid.uuid4().hex[:12]),
            method=str(item.get("method") or "netease_email"),
            account=str(item.get("account") or ""),
            password=str(item.get("password") or ""),
            remark=str(item.get("remark") or ""),
            character=str(item.get("character") or ""),
            enabled=bool(item.get("enabled", True)),
            play_protect=_as_bool_flag(item.get("play_protect"), False),
            last_status=str(item.get("last_status") or ""),
            last_verify_url=str(item.get("last_verify_url") or ""),
            hang_status=str(item.get("hang_status") or "未挂机"),
            stage_text=str(item.get("stage_text") or ""),
            online_done=bool(item.get("online_done", False)),
            last_hang_at=float(item.get("last_hang_at") or 0),
            session_label=str(item.get("session_label") or ""),
            updated_at=float(item.get("updated_at") or 0),
            online_ms_server=int(item.get("online_ms_server") or 0),
            online_ms_sampled_at=float(item.get("online_ms_sampled_at") or 0),
            hang_session_sec=float(item.get("hang_session_sec") or 0),
            online_ms_at_hang_start=int(item.get("online_ms_at_hang_start") or 0),
            sign_count=int(item.get("sign_count") or 0),
            sauth=str(item.get("sauth") or ""),
            sauth_at=float(item.get("sauth_at") or 0),
            characters_data=chars,
        )


# ---------------------------------------------------------------------------
# 账号库并发保护
# ---------------------------------------------------------------------------
# 背景：这个库被 4 类线程共用（Qt UI 线程 / 调度器 slot worker / char_loop tick /
# sauth 转换线程），而早期代码**没有任何互斥**。具体踩过的坑：
#   · char_loop 每 5 秒 store.load()，把 self.accounts 整个换成磁盘内容，
#     而此时别的线程正在 update()——内存里刚改、还没落盘的状态被丢掉；
#   · 同时 add() 在 append、save() 在遍历同一个 list；
#   · save() 写的是"当时那个快照"→ 谁最后写谁赢（22 个号被写成 2 个的温床）。
# 这里给所有会碰 self.accounts 的方法统一套上一把可重入锁。
# 用装饰器而不是逐个改，是为了让覆盖面一目了然、也不容易漏。
def _synchronized(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


_LOCKED_METHODS = (
    # 读写整份列表
    "load",
    "save",
    # 变更（内部会再调 save，靠 RLock 重入）
    "add",
    "upsert_login",
    "update",
    "set_character_flags",
    "upsert_character_progress",
    "ensure_characters",
    "remove",
    "set_sauth",
    "clear_sauth",
    # 查询（都要遍历 self.accounts，必须和上面互斥）
    "get",
    "find_by_login",
    "enabled_accounts",
    "enabled_characters",
    "empty_accounts",
    "account_ids_blocked_by_play",
    "has_characters",
    "iter_character_names",
    # 导入导出
    "import_csv",
    "import_xlsx",
    "export_csv",
    "export_xlsx",
)

for _name in _LOCKED_METHODS:
    _fn = getattr(AccountStore, _name, None)
    if _fn is not None and not getattr(_fn, "__wrapped_by_guard__", False):
        _wrapped = _synchronized(_fn)
        _wrapped.__wrapped_by_guard__ = True  # type: ignore[attr-defined]
        setattr(AccountStore, _name, _wrapped)
