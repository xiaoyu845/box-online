"""在线奖励控制台：阶段与里程碑的领取视图。"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6 import QtCore, QtWidgets

from .claim import reward_view


PARCHMENT = """
/* 领奖台主题：与主面板统一的深墨绿 + 铜金。
   旧版照搬游戏内奖励面板的米黄/金色（#c9a66b 系），在深色 App 里像贴了块补丁；
   这里保留结构（两排奖励格），只换成 App 自己的配色。 */
QWidget#rewardRoot {
  background-color: #121a17;
  border: 1px solid #2a3a34;
  border-radius: 10px;
}
QLabel#rewardTitle {
  color: #d4a574;
  font-size: 16px;
  font-weight: 800;
  font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
}
QLabel#rewardHead {
  color: #f0d2b0;
  font-size: 12px;
  font-weight: 700;
}
QLabel#rewardSub {
  color: #7dceb0;
  font-size: 11px;
  font-weight: 600;
}
QLabel#rewardTip {
  color: #6f8579;
  font-size: 11px;
}
QFrame#rewardSlot {
  background-color: #151d1a;
  border: 1px solid #2a3a34;
  border-radius: 8px;
  min-width: 72px;
  max-width: 88px;
  min-height: 86px;
}
QPushButton#claimBtn {
  background-color: #1c2824;
  border: 1px solid #2a3a34;
  border-radius: 6px;
  color: #8fa59a;
  font-weight: 700;
  padding: 3px 8px;
  min-height: 22px;
}
QPushButton#claimBtn:enabled {
  background-color: #c4925a;
  border-color: #e0b07a;
  color: #1a120c;
}
QPushButton#claimBtn:disabled {
  background-color: #1a221f;
  border-color: #24332e;
  color: #55665c;
}
QLabel#slotIcon {
  color: #d4a574;
  font-size: 20px;
  font-weight: 800;
}
QComboBox#slotPick {
  background: #1a2420;
  color: #e7ece6;
  border: 1px solid #2a3a34;
  padding: 4px 8px;
}
"""


class RewardMirror(QtWidgets.QWidget):
    """One parchment panel matching in-game online reward GUI."""

    claim_clicked = QtCore.Signal(int, bool)  # slot_index 0-3, milestone

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("rewardRoot")
        self.setStyleSheet(PARCHMENT)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.title = QtWidgets.QLabel("示例服在线奖励")
        self.title.setObjectName("rewardTitle")
        self.title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)

        self.stage_head = QtWidgets.QLabel("累计在线得好礼 (?/4) 阶段")
        self.stage_head.setObjectName("rewardHead")
        self.stage_cd = QtWidgets.QLabel("距离获得奖励 --")
        self.stage_cd.setObjectName("rewardSub")
        lay.addWidget(self.stage_head)
        lay.addWidget(self.stage_cd)

        self.stage_row = QtWidgets.QHBoxLayout()
        self.stage_row.setSpacing(8)
        self.stage_btns: List[QtWidgets.QPushButton] = []
        self.stage_icons: List[QtWidgets.QLabel] = []
        for i in range(4):
            box, icon, btn = self._make_slot(f"阶段{i + 1}")
            self.stage_icons.append(icon)
            self.stage_btns.append(btn)
            btn.clicked.connect(lambda _=False, idx=i: self.claim_clicked.emit(idx, False))
            self.stage_row.addWidget(box)
            if i < 3:
                arr = QtWidgets.QLabel("→")
                arr.setStyleSheet("color:#e07020;font-size:18px;font-weight:800;")
                self.stage_row.addWidget(arr)
        lay.addLayout(self.stage_row)

        self.mile_head = QtWidgets.QLabel("满阶段在线获丰富豪礼")
        self.mile_head.setObjectName("rewardHead")
        self.mile_prog = QtWidgets.QLabel("累计次数 [?/?]")
        self.mile_prog.setObjectName("rewardSub")
        lay.addWidget(self.mile_head)
        lay.addWidget(self.mile_prog)

        self.mile_row = QtWidgets.QHBoxLayout()
        self.mile_row.setSpacing(8)
        self.mile_btns: List[QtWidgets.QPushButton] = []
        for i in range(4):
            box, icon, btn = self._make_slot(f"豪礼{i + 1}")
            self.mile_btns.append(btn)
            btn.clicked.connect(lambda _=False, idx=i: self.claim_clicked.emit(idx, True))
            self.mile_row.addWidget(box)
            if i < 3:
                arr = QtWidgets.QLabel("→")
                arr.setStyleSheet("color:#e07020;font-size:18px;font-weight:800;")
                self.mile_row.addWidget(arr)
        lay.addLayout(self.mile_row)

        tip = QtWidgets.QLabel("领取时背包满将掉落在地上 · 数据来自服务端同步")
        tip.setObjectName("rewardTip")
        tip.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tip)

    @staticmethod
    def _make_slot(name: str):
        box = QtWidgets.QFrame()
        box.setObjectName("rewardSlot")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        icon = QtWidgets.QLabel("◆")
        icon.setObjectName("slotIcon")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon.setToolTip(name)
        btn = QtWidgets.QPushButton("领取")
        btn.setObjectName("claimBtn")
        btn.setEnabled(False)
        v.addWidget(icon, 1)
        v.addWidget(btn)
        return box, icon, btn

    def apply(self, view: dict) -> None:
        view = view or reward_view(None)
        title = str(view.get("title") or "示例服在线奖励")
        if self.title.text() != title:
            self.title.setText(title)
        sh = str(view.get("stage_line") or "")
        if self.stage_head.text() != sh:
            self.stage_head.setText(sh)
        cd = str(view.get("countdown") or "")
        if self.stage_cd.text() != cd:
            self.stage_cd.setText(cd)
        mh = str(view.get("mile_line") or "")
        if self.mile_head.text() != mh:
            self.mile_head.setText(mh)
        mp = str(view.get("mile_progress") or "")
        if self.mile_prog.text() != mp:
            self.mile_prog.setText(mp)
        stages = list(view.get("stages") or [])
        miles = list(view.get("milestones") or [])
        for i, btn in enumerate(self.stage_btns):
            st = stages[i] if i < len(stages) else {}
            lab = str(st.get("label") or "领取")
            en = bool(st.get("enabled"))
            if btn.text() != lab:
                btn.setText(lab)
            if btn.isEnabled() != en:
                btn.setEnabled(en)
            mark = "✓" if st.get("done") else ("◇" if en else "◆")
            if self.stage_icons[i].text() != mark:
                self.stage_icons[i].setText(mark)
        for i, btn in enumerate(self.mile_btns):
            st = miles[i] if i < len(miles) else {}
            lab = str(st.get("label") or "领取")
            en = bool(st.get("enabled"))
            if btn.text() != lab:
                btn.setText(lab)
            if btn.isEnabled() != en:
                btn.setEnabled(en)
            # milestone icons: reuse stage_icons layout — mile boxes only have btn; find sibling icon
            box = btn.parentWidget()
            if box is not None:
                icon = box.findChild(QtWidgets.QLabel, "slotIcon")
                if icon is not None:
                    mark = "✓" if st.get("done") else ("◇" if en else "◆")
                    if icon.text() != mark:
                        icon.setText(mark)


class OnlineRewardConsole(QtWidgets.QDialog):
    """总控制台：选槽位查看阶段奖励，一键领取。"""

    verify_done = QtCore.Signal(str)

    def __init__(
        self,
        get_slot_list: Callable[[], list],
        get_reward: Callable[[int], Optional[dict]],
        poll_rewards: Callable[..., None],
        on_claim: Callable[..., bool],
        on_claim_all: Callable[[], int],
        parent=None,
        *,
        snapshot_flags: Optional[Callable[[int], dict]] = None,
        verify_claim: Optional[Callable[..., dict]] = None,
    ) -> None:
        super().__init__(parent)
        self.get_slot_list = get_slot_list
        self.get_reward = get_reward
        self.poll_rewards = poll_rewards
        self.on_claim = on_claim
        self.on_claim_all = on_claim_all
        self.snapshot_flags = snapshot_flags
        self.verify_claim = verify_claim
        self.setWindowTitle("在线奖励总控制台")
        self.resize(720, 520)
        self.setStyleSheet("QDialog { background: #0e1412; }")
        self._busy = False
        self._labels_sig = ""
        self.verify_done.connect(self._on_verify_done)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("槽位"))
        self.slot_pick = QtWidgets.QComboBox()
        self.slot_pick.setObjectName("slotPick")
        self.slot_pick.setMinimumWidth(220)
        top.addWidget(self.slot_pick, 1)
        self.btn_refresh = QtWidgets.QPushButton("刷新扫描")
        self.btn_claim_one = QtWidgets.QPushButton("领取当前可领")
        self.btn_claim_one.setObjectName("primary")
        self.btn_claim_all = QtWidgets.QPushButton("已开游戏领全部")
        self.btn_mute = QtWidgets.QPushButton("游戏已静音（挂机端自动）")
        self.btn_mute.setEnabled(False)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_claim_one)
        top.addWidget(self.btn_claim_all)
        top.addWidget(self.btn_mute)
        root.addLayout(top)

        tip = QtWidgets.QLabel(
            "阶段奖励轻量扫描（约 3 秒刷新）。领取后会对照游戏同步位确认是否真的领到。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aab9f;")
        root.addWidget(tip)

        self.mirror = RewardMirror()
        root.addWidget(self.mirror, 1)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#d4a574;")
        root.addWidget(self.status)

        self.slot_pick.currentIndexChanged.connect(self._on_slot_changed)
        self.btn_refresh.clicked.connect(self._manual_refresh)
        self.btn_claim_one.clicked.connect(self._claim_current)
        self.btn_claim_all.clicked.connect(self._claim_all_slots)
        self.mirror.claim_clicked.connect(self._claim_btn)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(3000)
        self._fill_slots(force=True)
        self._refresh_view(scan=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fill_slots(force=True)
        self._refresh_view(scan=True)

    def _selected_index(self) -> Optional[int]:
        if self.slot_pick.count() <= 0:
            return None
        data = self.slot_pick.currentData()
        if data is None:
            return None
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _fill_slots(self, *, force: bool = False) -> None:
        slots = self.get_slot_list() or []
        parts = []
        for s in slots:
            idx = int(s.get("index") or 0)
            st = s.get("state") or ""
            ch = s.get("character") or ""
            parts.append(f"{idx}|{st}|{ch}")
        sig = ";".join(parts)
        if not force and sig == self._labels_sig and self.slot_pick.count() == len(slots):
            return
        self._labels_sig = sig
        cur = self._selected_index()
        self.slot_pick.blockSignals(True)
        self.slot_pick.clear()
        for s in slots:
            idx = int(s.get("index") or 0)
            ch = s.get("character") or ""
            st = s.get("state") or ""
            label = f"SLOT{idx + 1:02d} · {st}"
            if ch:
                label += f" · {ch}"
            self.slot_pick.addItem(label, idx)
        if cur is not None:
            for i in range(self.slot_pick.count()):
                if int(self.slot_pick.itemData(i)) == cur:
                    self.slot_pick.setCurrentIndex(i)
                    break
        self.slot_pick.blockSignals(False)

    def _on_slot_changed(self, _i: int = 0) -> None:
        self._refresh_view(scan=False)

    def _manual_refresh(self) -> None:
        self._refresh_view(scan=True)

    def _tick(self) -> None:
        if self._busy or not self.isVisible():
            return
        self._fill_slots(force=False)
        self._refresh_view(scan=True)

    def _refresh_view(self, *, scan: bool) -> None:
        if self._busy:
            return
        idx = self._selected_index()
        if scan:
            try:
                self.poll_rewards(force=False)
            except Exception:
                pass
        if idx is None:
            self.mirror.apply(reward_view(None))
            self.status.setText("无槽位")
            return
        data = None
        try:
            data = self.get_reward(idx)
        except Exception:
            data = None
        view = (data or {}).get("reward") or reward_view(None)
        self.mirror.apply(view)
        if data:
            st = data.get("state") or ""
            online = data.get("online_ms")
            self.status.setText(
                f"SLOT{idx + 1:02d} · {st} · 在线同步 "
                + (f"{int(online)}ms" if online is not None and int(online) >= 0 else "未同步")
                + (" · 有可领取" if view.get("any_claimable") else "")
            )
        else:
            self.status.setText(f"SLOT{idx + 1:02d} · 暂无缓存，点刷新扫描")

    def _on_verify_done(self, msg: str) -> None:
        self._busy = False
        self.status.setText(msg)
        self._refresh_view(scan=True)

    def _run_verify(
        self,
        idx: int,
        before: dict,
        *,
        slot: Optional[int] = None,
        milestone: bool = False,
        expect_any: bool = False,
        prefix: str = "",
    ) -> None:
        if not self.verify_claim:
            self._busy = False
            return

        def _work() -> None:
            try:
                result = self.verify_claim(
                    idx,
                    before,
                    slot=slot,
                    milestone=milestone,
                    expect_any=expect_any,
                    wait_sec=6.0,
                )
                detail = str((result or {}).get("detail") or "")
                ok = bool((result or {}).get("ok"))
                msg = (prefix + detail) if detail else (prefix + ("已确认" if ok else "未确认"))
            except Exception as exc:
                msg = f"{prefix}确认异常: {exc}"
            self.verify_done.emit(msg)

        threading.Thread(target=_work, name="aw-claim-verify", daemon=True).start()

    def _claim_btn(self, slot: int, milestone: bool) -> None:
        idx = self._selected_index()
        if idx is None or self._busy:
            return
        self._busy = True
        before: Dict[str, Any] = {}
        if self.snapshot_flags:
            try:
                before = self.snapshot_flags(idx) or {}
            except Exception:
                before = {}
        try:
            ok = self.on_claim(idx, slot=slot, milestone=milestone)
            kind = "里程碑" if milestone else "阶段"
            if not ok:
                self.status.setText(f"领取失败 · {kind}{slot + 1}（需挂机中）")
                self._busy = False
                return
            self.status.setText(f"已发包 · {kind}{slot + 1}，正在对照游戏同步…")
            self._run_verify(
                idx,
                before,
                slot=slot,
                milestone=milestone,
                prefix=f"{kind}{slot + 1} · ",
            )
        except Exception as exc:
            self.status.setText(f"领取异常: {exc}")
            self._busy = False

    def _claim_current(self) -> None:
        idx = self._selected_index()
        if idx is None or self._busy:
            return
        self._busy = True
        before: Dict[str, Any] = {}
        if self.snapshot_flags:
            try:
                before = self.snapshot_flags(idx) or {}
            except Exception:
                before = {}
        try:
            ok = self.on_claim(idx, claim_all=True)
            if not ok:
                self.status.setText("无法领取（需挂机中）")
                self._busy = False
                return
            self.status.setText("已请求领取当前可领，正在对照游戏同步…")
            self._run_verify(idx, before, expect_any=True, prefix="当前可领 · ")
        except Exception as exc:
            self.status.setText(f"领取异常: {exc}")
            self._busy = False

    def _claim_all_slots(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            n = self.on_claim_all()
            self.status.setText(
                f"已开游戏领全部 · {n} 个挂机槽已发送 8 键领取（各槽自行同步确认）"
            )
        finally:
            self._busy = False
