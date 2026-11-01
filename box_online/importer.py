"""批量账号导入：解析粘贴文本 / 表格 → 校验 → 去重 → 入库。

设计目标（对齐面板「粘贴导入」与 CLI 两种入口）：

1. 吃「粘贴」格式：每行 `用户名 密码`（空格 / 制表 / 逗号 / 全角空格分隔均可）
2. 也吃 CSV/XLSX 语义的文本：首行可带表头（method,account,password,remark,...）
3. 校验：必填、method 合法、批内重复、与库内重复
4. 默认**入库即停用**（enabled=False），避免一次误粘贴就拉起一堆进程
5. 报告里**永不回显密码原文**，只给长度与批次前缀族
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .accounts import METHODS, AccountStore
from .sauth import validate as sauth_validate
from .paths import known_homes, preferred_home

# 表头识别（首行首字段命中即视为表头）
_HEADER_KEYS = {
    "method",
    "account",
    "password",
    "remark",
    "character",
    "enabled",
    "账号",
    "用户名",
    "密码",
    "备注",
    "角色",
    "类型",
    "登录类型",
}

# 回退分隔符：任意 ASCII 空白 + 全角空格（制表/逗号在前面单独处理，以保留 CSV 引号语义）
_FIELD_SEP_RE = re.compile(r"[\s\u3000]+")


@dataclass
class Row:
    """一条待导入记录。"""

    method: str
    account: str
    password: str
    remark: str = ""
    line_no: int = 0
    raw: str = ""
    enabled: bool = False

    def key(self) -> Tuple[str, str]:
        return (self.method, self.account)


@dataclass
class Plan:
    """导入计划：新增 / 覆盖已有 / 批内重复 / 非法行。"""

    method: str = "4399"
    rows: List[Row] = field(default_factory=list)
    updates: List[Row] = field(default_factory=list)
    dups_in_file: List[Row] = field(default_factory=list)
    invalid: List[Tuple[int, str, str]] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return len(self.rows) + len(self.updates) + len(self.dups_in_file) + len(self.invalid)


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------


def mask_account(account: str) -> str:
    """账号脱敏：保留前 4 位与长度，便于对数但不落明文。"""
    a = (account or "").strip()
    if not a:
        return "(空)"
    if len(a) <= 4:
        return a[0] + "*" * (len(a) - 1) if len(a) > 1 else "*"
    return f"{a[:4]}{'*' * min(6, len(a) - 4)}"


def password_family(password: str) -> str:
    """密码字母前缀族：号商批量号通常整批同前缀（yas###### / pac######）。"""
    m = re.match(r"[A-Za-z]+", password or "")
    return m.group(0) if m else ""


def mask_password(password: str) -> str:
    """只暴露长度与字母前缀族，绝不回显密码。"""
    pw = password or ""
    fam = password_family(pw)
    tag = f"{fam}…" if fam else "无字母前缀"
    return f"••••(len={len(pw)}, {tag})"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def _split_fields(line: str) -> List[str]:
    """按行拆字段：优先制表符，其次逗号(CSV)，最后任意空白。"""
    raw = line.strip()
    if not raw:
        return []
    # 整行就是一个 JSON 对象时，它是**一个字段**。
    # sauth 凭据就是 JSON，里面必然有逗号；走下面的 CSV 分支会被切成
    # ['{"sessionid":"abc"', 'sdkuid:"u1"', ...] 导致导入必然失败。
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            return [raw]
    if "\t" in raw:
        return [p.strip() for p in raw.split("\t")]
    if "," in raw:
        try:
            return [p.strip() for p in next(csv.reader(io.StringIO(raw)))]
        except (StopIteration, csv.Error):
            pass
    return [p for p in _FIELD_SEP_RE.split(raw) if p != ""] or raw.split()


def _looks_like_header(fields: Sequence[str]) -> bool:
    """整行基本都是表头关键字才算表头；避免把数据行误吞。

    数据行不可能出现「首列是表头词 且 至少两列是表头词」的形状：
    `4399,account,password,remark` 虽然后三列像表头，但首列不是，仍按数据行处理。
    """
    if len(fields) < 2:
        return False
    keys = sum(1 for f in fields if f.strip().lower() in _HEADER_KEYS)
    return keys >= 2 and fields[0].strip().lower() in _HEADER_KEYS


def _header_index(fields: Sequence[str]) -> Dict[str, int]:
    idx: Dict[str, int] = {}
    for i, name in enumerate(fields):
        key = name.strip().lower()
        alias = {
            "账号": "account",
            "用户名": "account",
            "密码": "password",
            "备注": "remark",
            "角色": "character",
            "类型": "method",
            "登录类型": "method",
        }.get(key, key)
        if alias and alias not in idx:
            idx[alias] = i
    return idx


def _row_from_fields(
    fields: Sequence[str],
    *,
    line_no: int,
    raw: str,
    default_method: str,
    header: Optional[Dict[str, int]] = None,
) -> Tuple[Optional[Row], str]:
    """字段 → Row；返回 (row, 失败原因)。"""
    if header:
        def pick(name: str) -> str:
            i = header.get(name, -1)
            if 0 <= i < len(fields):
                return fields[i].strip()
            return ""

        method = pick("method") or default_method
        account = pick("account")
        password = pick("password")
        remark = pick("remark")
    else:
        parts = [f for f in fields]
        if not parts:
            return None, "空行"
        if len(parts) == 1:
            # 单列：无密码。sauth 例外（凭据在 remark/sauth 字段）
            if default_method == "sauth":
                return Row(default_method, parts[0], "", "", line_no, raw), ""
            # 这一行本身就是一段合法 sauth 凭据 → 自动认成 sauth 行。
            # 否则用户在"类型=4399"下贴混合文本（账号密码 + sauth）时，
            # sauth 行会被当成"只有账号没有密码"丢掉。
            ok_sauth, _why = sauth_validate(parts[0])
            if ok_sauth:
                return Row("sauth", parts[0], "", "", line_no, raw), ""
            return None, "只有账号没有密码"
        first = parts[0].strip().lower()
        if first in METHODS and len(parts) >= 3:
            if len(parts) > 6:
                return None, f"字段过多（{len(parts)} 列）"
            method = parts[0].strip()
            account = parts[1].strip()
            password = parts[2].strip()
            remark = parts[3].strip() if len(parts) > 3 else ""
        else:
            if len(parts) > 3:
                return None, f"字段过多（{len(parts)} 列且首列不是登录类型，疑似非账号行）"
            method = default_method
            account = parts[0].strip()
            password = parts[1].strip()
            remark = parts[2].strip() if len(parts) > 2 else ""

    method = (method or default_method).strip()
    account = (account or "").strip()
    password = (password or "").strip()

    if method not in METHODS:
        return None, f"未知登录类型 {method}"
    if not account and method != "sauth":
        return None, "账号为空"
    if method != "sauth" and not password:
        return None, "密码为空"
    if any(ch.isspace() for ch in account):
        return None, "账号含空白字符"
    return Row(method, account, password, remark, line_no, raw), ""


def parse_text(text: str, *, default_method: str = "4399") -> Tuple[List[Row], List[Tuple[int, str, str]]]:
    """解析整段文本 → (rows, invalid)。invalid 为 (行号, 原行, 原因)。"""
    rows: List[Row] = []
    invalid: List[Tuple[int, str, str]] = []
    header: Optional[Dict[str, int]] = None
    header_consumed = False

    for line_no, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = _split_fields(line)
        if not fields:
            continue
        # 表头：整行关键字判定是明确的，位置不限（且只吃一次）
        if not header_consumed and _looks_like_header(fields):
            header = _header_index(fields)
            header_consumed = True
            continue
        row, reason = _row_from_fields(
            fields, line_no=line_no, raw=stripped, default_method=default_method, header=header
        )
        if row is None:
            invalid.append((line_no, stripped, reason))
            continue
        if row.method == "sauth":
            # 守卫：method=sauth 的"账号"位其实是 sauth 凭据本身。
            # 这里曾经零校验，于是把 4399 用户名当 sauth 导进来，
            # 生成了 20 个登不进去的号（对接层报 sauth_json 不是有效 JSON）。
            ok, why = sauth_validate(row.account)
            if not ok:
                invalid.append(
                    (
                        line_no,
                        stripped,
                        f"类型选 sauth，但内容不是 sauth 凭据（{why}）。"
                        "sauth 行要贴 sdkuid/sessionid 那段 JSON；"
                        "如果是账号密码请把类型改成 4399。",
                    )
                )
                continue
        rows.append(row)
    return rows, invalid


def read_clipboard() -> str:
    """读系统剪贴板（tkinter，无需额外依赖）。"""
    try:
        import tkinter  # noqa: PLC0415

        root = tkinter.Tk()
        root.withdraw()
        try:
            data = root.clipboard_get()
        finally:
            root.destroy()
        return data or ""
    except Exception as exc:  # noqa: BLE001 - 剪贴板不可用时给出可读原因
        raise RuntimeError(f"读取剪贴板失败：{exc}") from exc


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------


def build_plan(
    text: str,
    store: Optional[AccountStore] = None,
    *,
    default_method: str = "4399",
    remark: str = "",
) -> Plan:
    """解析 + 去重（批内 / 与库内）。不改动 store。"""
    plan = Plan(method=default_method)
    rows, invalid = parse_text(text, default_method=default_method)
    plan.invalid = invalid

    seen: Dict[Tuple[str, str], Row] = {}
    for row in rows:
        if remark and not row.remark:
            row.remark = remark
        key = row.key()
        if key in seen:
            plan.dups_in_file.append(row)
            continue
        seen[key] = row

    existing = {(a.method, a.account): a for a in (store.accounts if store else [])}
    for row in seen.values():
        if row.key() in existing:
            plan.updates.append(row)
        else:
            plan.rows.append(row)
    return plan


def apply_plan(
    store: AccountStore,
    plan: Plan,
    *,
    enabled: bool = False,
    update_existing: bool = False,
    protect_existing: bool = False,
) -> Dict[str, int]:
    """落地到账号库。

    enabled=False（默认）→ 新增账号入库即停用，人工确认后再勾选参与调度。
    update_existing=True → 已存在的同 method+account 覆盖密码。
    protect_existing=True → 导入前把库里原有账号全部标 play_protect（保护主号不被卷入）。
    """
    stats = {"added": 0, "updated": 0, "protected": 0, "dups_in_file": len(plan.dups_in_file)}

    if protect_existing:
        for acc in store.accounts:
            if not acc.play_protect:
                acc.play_protect = True
                stats["protected"] += 1
        if stats["protected"]:
            store.save()

    for row in plan.rows:
        store.add(
            method=row.method,
            account=row.account,
            password=row.password,
            remark=row.remark,
            enabled=enabled,
            hang_status="未挂机",
            last_status="批量导入（停用）" if not enabled else "批量导入",
        )
        stats["added"] += 1

    if update_existing:
        for row in plan.updates:
            acc = store.find_by_login(row.method, row.account)
            if acc is None:
                continue
            if row.password:
                acc.password = row.password
            if row.remark and not acc.remark:
                acc.remark = row.remark
            acc.last_status = "批量导入覆盖"
            store.save()
            stats["updated"] += 1

    return stats


# --------------------------------------------------------------------------
# 风险体检（只报告，不做规避）
# --------------------------------------------------------------------------


def risk_report(plan: Plan, store: Optional[AccountStore] = None) -> List[str]:
    """导入前的运营风险体检。只陈述事实，不提供反检测手段。"""
    notes: List[str] = []
    all_rows = list(plan.rows) + list(plan.updates)

    # 1) 密码前缀族：同族 = 同一号商批次，同批号通常同源、同生命周期
    fams: Dict[str, List[str]] = {}
    for row in all_rows:
        fam = password_family(row.password)
        if fam:
            fams.setdefault(fam, []).append(row.account)
    for fam, accounts in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        if len(accounts) >= 2:
            notes.append(
                f"密码族「{fam}」共 {len(accounts)} 个号 → 同一号商批次。"
                "同批号往往同源同寿命：一批被回收/改密时通常整批一起失效，"
                "排期时别把整批压在同一个账号池时间窗里。"
            )

    # 2) 完全相同的密码
    by_pw: Dict[str, List[str]] = {}
    for row in all_rows:
        if row.password:
            by_pw.setdefault(row.password, []).append(row.account)
    for pw, accounts in by_pw.items():
        if len(accounts) >= 2:
            notes.append(f"{len(accounts)} 个号共用同一密码 → 这批号几乎肯定同源，号商可批量找回。")

    # 3) 用户名形态：纯随机串是号商批量注册特征，不是真人注册
    randomish = [r.account for r in all_rows if re.fullmatch(r"[A-Za-z0-9]{12,16}", r.account or "")]
    if randomish and len(randomish) >= max(2, len(all_rows) // 2):
        notes.append(
            f"{len(randomish)}/{len(all_rows)} 个用户名是 12–16 位纯随机串 → 号商批量注册特征。"
            "这类号没有绑定你自己可控的手机/实名，申诉基本无门。"
        )

    # 4) 登录标识：整机共用一个 4399 设备文件（这是事实，不是可选项）
    dev = Path.home() / ".box-online" / "4399-device.cds"
    notes.append(
        f"登录标识：所有 4399 号共用 {dev}（n4399.py 的 DEVICE_FILE）。"
        "也就是说这台机器上跑的号在服务端是可聚类的——这是现状；"
        "我不会做「每号伪造独立指纹」这类反检测层，但你要知道这个风险会连带主号与 IP。"
    )

    # 5) 调度上限：确认并发没被悄悄放大
    try:
        from .paths import load_settings  # noqa: PLC0415

        st = load_settings()
        notes.append(
            "当前调度上限："
            f"slot_count={st.get('slot_count')} · "
            f"char_loop_max_windows={st.get('char_loop_max_windows')} · "
            f"char_loop_launch_batch={st.get('char_loop_launch_batch')} · "
            f"hang_mode={st.get('hang_mode')}"
        )
    except Exception:  # noqa: BLE001 - 只读体检，读不到不阻断
        pass

    return notes


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def format_plan(plan: Plan, *, store: Optional[AccountStore] = None, show_risk: bool = True) -> str:
    out: List[str] = []
    out.append(f"解析：共 {plan.total_seen} 行")
    out.append(f"  新增      {len(plan.rows)}")
    out.append(f"  库内已存在 {len(plan.updates)}")
    out.append(f"  批内重复  {len(plan.dups_in_file)}")
    out.append(f"  非法      {len(plan.invalid)}")

    if plan.rows:
        out.append("")
        out.append("新增预览（账号脱敏 / 密码不回显）：")
        for row in plan.rows[:20]:
            out.append(f"  L{row.line_no:<4} {row.method:<8} {mask_account(row.account):<14} {mask_password(row.password)}")
        if len(plan.rows) > 20:
            out.append(f"  … 另有 {len(plan.rows) - 20} 条")

    if plan.updates:
        out.append("")
        out.append("库内已存在（默认跳过，--update 才覆盖密码）：")
        for row in plan.updates[:10]:
            out.append(f"  L{row.line_no:<4} {row.method:<8} {mask_account(row.account)}")
        if len(plan.updates) > 10:
            out.append(f"  … 另有 {len(plan.updates) - 10} 条")

    if plan.invalid:
        out.append("")
        out.append("非法行：")
        for line_no, raw, reason in plan.invalid[:10]:
            out.append(f"  L{line_no:<4} {reason}  ← {raw[:60]}")
        if len(plan.invalid) > 10:
            out.append(f"  … 另有 {len(plan.invalid) - 10} 行")

    if show_risk:
        notes = risk_report(plan, store)
        if notes:
            out.append("")
            out.append("风险体检：")
            for n in notes:
                out.append(f"  · {n}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def describe_homes() -> List[str]:
    """列出所有候选账号库及其条数。

    这套部署天然有两份库（便携 = 面板在用 / 旧库 = 面板不读），
    写入前先把两份都摆出来，避免像 CLI 那样导进面板看不到的那份。
    """
    lines: List[str] = []
    for label, path, is_active in known_homes():
        f = path / "accounts.json"
        n = "无文件"
        if f.is_file():
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                rows = raw if isinstance(raw, list) else raw.get("accounts", [])
                n = f"{len(rows)} 条"
            except (OSError, json.JSONDecodeError):
                n = "解析失败"
        mark = "面板在用 ←" if is_active else "旧库(面板不读)"
        lines.append(f"  {mark:<16} [{n:>6}]  {f}")
    return lines


def _load_text(args: argparse.Namespace) -> str:
    if args.clipboard:
        return read_clipboard()
    if args.file == "-":
        return sys.stdin.read()
    return Path(args.file).read_text(encoding="utf-8-sig")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="box_online.importer",
        description="批量导入账号到 box-online 账号库（默认只预览，--apply 才写入）",
    )
    ap.add_argument("file", nargs="?", default="-", help="文本/CSV 文件；- = stdin（默认）")
    ap.add_argument("--clipboard", action="store_true", help="从系统剪贴板读取（忽略 file）")
    ap.add_argument("--method", default="4399", help="默认登录类型（默认 4399）")
    ap.add_argument("--remark", default="", help="为本次导入的行填备注（仅空备注时填）")
    ap.add_argument("--apply", action="store_true", help="真正写入账号库（不加则只预览）")
    ap.add_argument("--enable", action="store_true", help="导入即启用（默认停用，需手工勾选）")
    ap.add_argument("--update", action="store_true", help="库内已存在的同号覆盖密码")
    ap.add_argument("--protect-existing", action="store_true", help="导入前把库里原有账号全标「游玩保护」")
    ap.add_argument("--store", default="", help="覆盖 accounts.json 路径（默认按 BOX_ONLINE_HOME / 便携目录）")
    ap.add_argument("--no-risk", action="store_true", help="跳过风险体检")
    args = ap.parse_args(argv)

    try:
        text = _load_text(args)
    except (OSError, RuntimeError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2

    store = (
        AccountStore(Path(args.store))
        if args.store
        else AccountStore(preferred_home() / "accounts.json")
    )
    plan = build_plan(text, store, default_method=args.method, remark=args.remark)

    print("候选账号库：")
    for line in describe_homes():
        print(line)
    print()
    print(f"本次写入：{store.path}")
    print(format_plan(plan, store=store, show_risk=not args.no_risk))

    if not args.apply:
        print("")
        print("（预览模式，未写入。加 --apply 落地）")
        return 0

    if not plan.rows and not (args.update and plan.updates):
        print("")
        print("无可写入内容。")
        return 1

    stats = apply_plan(
        store,
        plan,
        enabled=args.enable,
        update_existing=args.update,
        protect_existing=args.protect_existing,
    )
    print("")
    print(
        f"已写入：新增 {stats['added']} · 覆盖 {stats['updated']} · "
        f"保护 {stats['protected']} · 批内重复跳过 {stats['dups_in_file']}"
    )
    if not args.enable and stats["added"]:
        print("新增账号为「停用」状态：到面板账号库勾选启用后才会参与挂机调度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
