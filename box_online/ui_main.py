from __future__ import annotations

import base64
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from .accounts import METHOD_LABELS, METHODS, Account, AccountStore
from .bridge import BridgeError, ensure_bridge_running, load_bridge_client
from .importer import apply_plan, build_plan, read_clipboard, risk_report
from .paths import load_settings, save_settings
from .scheduler import Scheduler
from .states import SlotState


# 秋铜灯笼：深墨青底 + 铜金点缀（示例服挂机台）
# 注意：不要给全局 QWidget 设实心背景，否则每个 QLabel 都会叠出深色块（看起来像阴影）
STYLE = """
* {
  font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif;
  color: #e7ece6;
  font-size: 13px;
}
QMainWindow { background-color: #0e1412; }
QWidget#root { background-color: #0e1412; }
QDialog { background-color: #121a17; border: 1px solid #2a3a34; }
QLabel { background-color: transparent; }
QGroupBox {
  background-color: transparent;
  border: 1px solid #24332e;
  border-radius: 10px;
  margin-top: 14px;
  padding: 14px 12px 12px 12px;
  font-weight: 600;
}
QGroupBox::title {
  subcontrol-origin: margin;
  left: 14px;
  padding: 0 8px;
  color: #d4a574;
  font-size: 12px;
  letter-spacing: 1px;
}
QPushButton {
  background-color: #1c2824;
  border: 1px solid #334740;
  border-radius: 10px;
  padding: 8px 14px;
  color: #e7ece6;
  font-weight: 600;
}
QPushButton:hover { background-color: #243530; border-color: #c4925a; color: #fff6ea; }
QPushButton:pressed { background-color: #18231f; }
QPushButton#primary {
  background-color: #c4925a;
  border: 1px solid #e0b07a;
  color: #1a120c;
}
QPushButton#primary:hover { background-color: #d4a574; }
QPushButton#danger {
  background-color: #3a1f22;
  border: 1px solid #8a4a52;
  color: #ffc9ce;
}
QPushButton#danger:hover { background-color: #4a282c; border-color: #c07078; }
QPushButton#ghost {
  background-color: transparent;
  border: 1px solid #3a4f47;
  color: #b7c7bf;
}
QPushButton#ghost:hover { border-color: #c4925a; color: #f0d2b0; }
QPushButton#modeBtn {
  background-color: #1a2420;
  border: 1px solid #334740;
  border-radius: 8px;
  padding: 7px 16px;
  color: #9aab9f;
  font-weight: 700;
}
QPushButton#modeBtn:checked {
  background-color: #c4925a;
  border-color: #e0b07a;
  color: #1a120c;
}
QPushButton#modeBtn:hover:!checked {
  border-color: #c4925a;
  color: #f0d2b0;
}
/* 工具栏分组：竖线分隔 + 组标题 */
QFrame#toolSep {
  background-color: #2a3a34;
  border: none;
}
QLabel#toolCap {
  color: #6f8579;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
}
QTableWidget {
  background-color: #101714;
  alternate-background-color: #141c19;
  gridline-color: #24332e;
  border: 1px solid #24332e;
  border-radius: 10px;
  selection-background-color: #2a3f37;
  selection-color: #fff6ea;
}
QHeaderView::section {
  background-color: #1a2420;
  color: #d4a574;
  padding: 8px;
  border: none;
  border-bottom: 1px solid #2a3a34;
  font-weight: 700;
  font-size: 12px;
}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QListWidget {
  background-color: #1a2420;
  border: 1px solid #2a3a34;
  border-radius: 8px;
  padding: 8px 10px;
  color: #e7ece6;
  selection-background-color: #c4925a;
  selection-color: #1a120c;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QListWidget:focus {
  border: 1px solid #c4925a;
}
QListWidget {
  padding: 4px;
  outline: none;
}
QListWidget::item {
  background-color: transparent;
  color: #e8dcc8;
  padding: 6px 8px;
  border-radius: 4px;
}
QListWidget::item:selected {
  background-color: #2a3f37;
  color: #fff6ea;
}
QListWidget::item:hover {
  background-color: #243530;
  color: #fff6ea;
}
QListWidget::indicator {
  width: 16px;
  height: 16px;
  border: 1px solid #5a6f66;
  border-radius: 3px;
  background: #101714;
}
QListWidget::indicator:checked {
  background: #c4925a;
  border-color: #e0b07a;
}
QComboBox::drop-down { border: none; width: 24px; }
QSpinBox { min-width: 64px; }
QLabel#brand {
  color: #f0d2b0;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 1px;
}
QLabel#brandSub { color: #8fa59a; font-size: 12px; }
QLabel#muted { color: #8fa59a; }
QLabel#statValue { color: #f0d2b0; font-size: 18px; font-weight: 700; }
QLabel#statLabel { color: #8fa59a; font-size: 11px; }
QFrame#hero {
  background-color: #16201c;
  border: 1px solid #2f4038;
  border-radius: 12px;
}
QFrame#panel {
  background-color: transparent;
  border: none;
}
QFrame#slotCard {
  background-color: #151d1a;
  border: 1px solid #2a3a34;
  border-radius: 10px;
}
QFrame#slotCard[tone="run"] { border: 1px solid #3d6b5a; background-color: #122018; }
QFrame#slotCard[tone="bad"] { border: 1px solid #7a3d45; background-color: #1c1416; }
QFrame#slotCard[tone="idle"] { border: 1px solid #2a3a34; background-color: #151d1a; }
QLabel#slotTitle { color: #d4a574; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#slotUser { color: #e7ece6; font-size: 13px; font-weight: 600; }
QLabel#slotState {
  background-color: #1c2824;
  border-radius: 8px;
  padding: 4px 8px;
  color: #b7c7bf;
  font-weight: 700;
  font-size: 12px;
}
QLabel#slotState[tone="run"] { background-color: #1a3a30; color: #7dceb0; }
QLabel#slotState[tone="bad"] { background-color: #3a1f22; color: #ff9aa3; }
QLabel#slotState[tone="idle"] { background-color: #1c2824; color: #8fa59a; }
QLabel#slotTimer {
  color: #f0d2b0;
  font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
  font-size: 20px;
  font-weight: 700;
}
QLabel#slotStage { color: #9eb8ab; font-size: 12px; }
QLabel#stateOk { color: #7dceb0; }
QLabel#stateBad { color: #ff9aa3; }
QLabel#stateRun { color: #7dceb0; }
QRadioButton { spacing: 8px; color: #c5d5cc; background-color: transparent; }
QRadioButton::indicator {
  width: 14px; height: 14px;
  border-radius: 8px;
  border: 1px solid #4a6359;
  background-color: #1a2420;
}
QRadioButton::indicator:checked {
  background-color: #c4925a;
  border: 1px solid #e0b07a;
}
QScrollArea { border: none; background-color: transparent; }
QPlainTextEdit#eventLog {
  background-color: #101714;
  border: 1px solid #2a3a34;
  border-radius: 10px;
  padding: 8px;
  color: #c5d5cc;
  font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
  font-size: 12px;
  selection-background-color: #c4925a;
  selection-color: #1a120c;
}
QFrame#logPanel {
  background-color: #121a17;
  border: 1px solid #2a3a34;
  border-radius: 12px;
}
QWidget#slotViewport {
  background-color: #0a100e;
  border: 1px solid #24332e;
  border-radius: 8px;
}
QLabel#viewportHint {
  color: #5a6e66;
  font-size: 11px;
}
"""


class SlotCard(QtWidgets.QFrame):
    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self._embed_hwnd = 0
        self.setObjectName("slotCard")
        self.setProperty("tone", "idle")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setMinimumHeight(112)
        # 纵向不拉伸：网格会把剩余高度全分给卡片，导致「未分配 / --:--」被拉得很散
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum
        )
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(f"SLOT {index + 1:02d}")
        title.setObjectName("slotTitle")
        title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self.state = QtWidgets.QLabel("空闲")
        self.state.setObjectName("slotState")
        self.state.setProperty("tone", "idle")
        self.state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.state.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.state)

        self.user = QtWidgets.QLabel("未分配")
        self.user.setObjectName("slotUser")
        self.user.setWordWrap(True)

        meta = QtWidgets.QHBoxLayout()
        self.timer = QtWidgets.QLabel("--:--")
        self.timer.setObjectName("slotTimer")
        self.stage = QtWidgets.QLabel("")
        self.stage.setObjectName("slotStage")
        self.stage.hide()
        meta.addWidget(self.timer)
        meta.addWidget(self.stage, 1)

        self.time_info = QtWidgets.QLabel("")
        self.time_info.setObjectName("muted")
        self.time_info.setWordWrap(True)
        self.time_info.hide()

        self.pos_info = QtWidgets.QLabel("")
        self.pos_info.setObjectName("muted")
        self.pos_info.setWordWrap(True)
        self.pos_info.hide()

        self.detail = QtWidgets.QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("muted")
        self.detail.hide()

        self.viewport = QtWidgets.QWidget()
        self.viewport.setObjectName("slotViewport")
        self.viewport.setMinimumHeight(140)
        self.viewport.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.viewport.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        vp_lay = QtWidgets.QVBoxLayout(self.viewport)
        vp_lay.setContentsMargins(0, 0, 0, 0)
        self.viewport_hint = QtWidgets.QLabel("游戏窗口已独立弹出\n（不镶嵌在此卡片内）")
        self.viewport_hint.setObjectName("viewportHint")
        self.viewport_hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.viewport_hint.setWordWrap(True)
        vp_lay.addWidget(self.viewport_hint)
        # 紧凑模式：没有真的嵌入游戏窗口时，这块不占任何空间。
        # 旧版默认显示一个 140px 高的空黑框（还写着"不镶嵌"），7 个槽一起把屏幕吃光了。
        self.viewport.hide()

        self.btn_retry = QtWidgets.QPushButton("重试")
        self.btn_retry.setObjectName("ghost")
        self.btn_retry.setToolTip("先关闭本槽游戏进程，再按原账号/角色重新进服")
        self.btn_claim = QtWidgets.QPushButton("领取在线")
        self.btn_claim.setObjectName("primary")
        self.btn_claim.setEnabled(False)
        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setObjectName("danger")
        self.btn_close.setToolTip("关闭本槽挂机窗口（不影响其他槽）")
        self.btn_close.setEnabled(False)
        self.btn_verify = QtWidgets.QPushButton("验证")
        self.btn_verify.setObjectName("ghost")
        self.btn_verify.setVisible(False)
        # 按钮独立成组，空闲槽整组隐藏（空闲槽显示"重试/领取在线"没有意义）
        self.btn_row = QtWidgets.QWidget()
        btn_lay = QtWidgets.QHBoxLayout(self.btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(6)
        btn_lay.addWidget(self.btn_retry)
        btn_lay.addWidget(self.btn_claim)
        btn_lay.addWidget(self.btn_close)
        btn_lay.addWidget(self.btn_verify)
        btn_lay.addStretch(1)

        outer.addLayout(top)
        outer.addWidget(self.user)
        outer.addLayout(meta)
        outer.addWidget(self.time_info)
        outer.addWidget(self.pos_info)
        outer.addWidget(self.detail)
        outer.addWidget(self.viewport, 1)
        outer.addWidget(self.btn_row)
        self.btn_row.hide()

    def host_wid(self) -> int:
        self.viewport.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        return int(self.viewport.winId())

    def embed_foreign(self, hwnd: int) -> bool:
        from .win_embed import embed_hwnd

        if not hwnd:
            return False
        # 先让 viewport 真正占位，拿到有效尺寸再嵌（紧凑模式下它默认是隐藏的）
        self.viewport_hint.hide()
        self.viewport.setMinimumHeight(240)
        self.viewport.show()
        self.setMinimumHeight(330)
        QtWidgets.QApplication.processEvents()
        ok = embed_hwnd(int(hwnd), self.host_wid(), self.viewport.width(), self.viewport.height())
        if ok:
            self._embed_hwnd = int(hwnd)
            self.fit_embed()
        else:
            self.viewport.hide()
            self.viewport.setMinimumHeight(140)
            self.setMinimumHeight(112)
        return ok

    def fit_embed(self) -> None:
        from .win_embed import fit_hwnd

        if self._embed_hwnd:
            fit_hwnd(self._embed_hwnd, self.viewport.width(), self.viewport.height())

    def clear_embed(self) -> None:
        from .win_embed import release_hwnd

        if self._embed_hwnd:
            release_hwnd(self._embed_hwnd)
            self._embed_hwnd = 0
        # 回到紧凑态
        self.viewport.hide()
        self.viewport.setMinimumHeight(140)
        self.setMinimumHeight(112)
        self.viewport_hint.setText("无渲染挂机\n（不显示游戏画面）")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_embed()

    def apply(self, data: dict, account_label: str) -> None:
        def _set(lab: QtWidgets.QLabel, text: str) -> None:
            if lab.text() != text:
                lab.setText(text)

        label = account_label or "未分配"
        _set(self.user, label)
        st = data.get("state") or "空闲"
        _set(self.state, st)
        # 大计时旁：有合计时显示「合计」，否则显示阶段
        timer_label = (data.get("timer_label") or data.get("stage_text") or "").strip()
        detail = (data.get("detail") or "").strip()
        _set(self.stage, timer_label)
        self.stage.setVisible(bool(timer_label))
        _set(self.detail, detail)
        self.detail.setVisible(bool(detail))
        _set(self.timer, data.get("timer_text") or "--:--")
        time_text = (data.get("time_text") or "").strip()
        _set(self.time_info, time_text)
        self.time_info.setVisible(bool(time_text))
        pos_text = (data.get("pos_text") or "").strip()
        reward = data.get("reward") or {}
        if reward.get("active") or reward.get("any_claimable"):
            extra = f"{reward.get('stage_line', '')} · {reward.get('mile_progress', '')}"
            pos_text = (pos_text + " · " + extra).strip(" ·") if pos_text else extra
        _set(self.pos_info, pos_text)
        self.pos_info.setVisible(bool(pos_text))
        self.btn_verify.setVisible(bool(data.get("verify_url")))
        can_claim = bool(data.get("can_claim"))
        if self.btn_claim.isEnabled() != can_claim:
            self.btn_claim.setEnabled(can_claim)
        claim_txt = "可领取" if reward.get("any_claimable") else "领取在线"
        if self.btn_claim.text() != claim_txt:
            self.btn_claim.setText(claim_txt)
        can_close = st not in ("空闲",) and st != ""
        if self.btn_close.isEnabled() != can_close:
            self.btn_close.setEnabled(can_close)
        # 空闲槽不显示操作按钮；有账号/在跑的槽才显示
        busy = st not in ("空闲", "") or (account_label or "").strip() not in ("", "未分配")
        if self.btn_row.isVisible() != busy:
            self.btn_row.setVisible(busy)
        if st in ("需验证", "异常"):
            tone = "bad"
        elif st == "空闲":
            tone = "idle"
        else:
            tone = "run"
        self.setProperty("tone", tone)
        self.state.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class CharPickDialog(QtWidgets.QDialog):
    def __init__(self, account_label: str, names: List[str], preselect: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择角色")
        self.setModal(True)
        self.resize(380, 220)
        self.setStyleSheet(STYLE)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("选择角色")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        sub = QtWidgets.QLabel(f"账号：{account_label}")
        sub.setObjectName("muted")
        lay.addWidget(title)
        lay.addWidget(sub)

        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(names)
        if preselect and preselect in names:
            self.combo.setCurrentText(preselect)
        lay.addWidget(self.combo)
        lay.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("确认")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def selected(self) -> str:
        return self.combo.currentText()


class WindowCountDialog(QtWidgets.QDialog):
    """批量代理 / 追加挂机：手动窗口数，或自动检测角色数。"""

    MODE_MANUAL = "manual"
    MODE_AUTO_CHARS = "auto_chars"

    def __init__(
        self,
        account_count: int,
        max_windows: int,
        parent=None,
        *,
        title: str = "批量代理",
        ok_text: str = "开始批量代理",
        hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 320)
        self.setStyleSheet(STYLE)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(12)

        head = QtWidgets.QLabel(title)
        head.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        default_hint = (
            f"已启用账号 {account_count} 个 · 槽位上限 {max_windows}\n"
            "可手动指定窗口数，或自动检测账号下角色数（一角一窗）。"
        )
        sub = QtWidgets.QLabel(hint.strip() or default_hint)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(sub)

        self.rb_manual = QtWidgets.QRadioButton("手动指定窗口数")
        self.rb_auto = QtWidgets.QRadioButton("自动检测角色数（登录拉角色，一角一窗）")
        self.rb_auto.setChecked(True)
        lay.addWidget(self.rb_auto)
        lay.addWidget(self.rb_manual)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("窗口数"))
        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(1, max(1, max_windows))
        default = min(account_count, max_windows) if account_count else 1
        self.spin.setValue(max(1, default))
        row.addWidget(self.spin)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

        self.rb_manual.toggled.connect(self._sync_mode)
        self.rb_auto.toggled.connect(self._sync_mode)
        self._sync_mode()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _sync_mode(self) -> None:
        manual = self.rb_manual.isChecked()
        self.spin.setEnabled(manual)

    def mode(self) -> str:
        return self.MODE_AUTO_CHARS if self.rb_auto.isChecked() else self.MODE_MANUAL

    def window_count(self) -> int:
        return int(self.spin.value())


class AccountPickDialog(QtWidgets.QDialog):
    """窗口数 < 账号数：勾选要用的账号；20 秒未确认则取前 N 个。"""

    def __init__(self, accounts: List[Account], need: int, timeout_sec: int = 20, parent=None) -> None:
        super().__init__(parent)
        self._accounts = list(accounts)
        self._need = max(1, int(need))
        self._timeout = max(1, int(timeout_sec))
        self._left = self._timeout
        self._auto_ids: List[str] = [a.id for a in self._accounts[: self._need]]
        self.setWindowTitle("选择账号")
        self.setModal(True)
        self.resize(520, 420)
        self.setStyleSheet(STYLE)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        title = QtWidgets.QLabel(f"请选择 {self._need} 个账号分配到窗口")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        self.lbl_tip = QtWidgets.QLabel("")
        self.lbl_tip.setObjectName("muted")
        self.lbl_tip.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(self.lbl_tip)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for i, acc in enumerate(self._accounts):
            text = f"{i + 1}. {acc.display()}"
            if acc.character:
                text += f"  / {acc.character}"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, acc.id)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if i < self._need else QtCore.Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list, 1)

        btns = QtWidgets.QHBoxLayout()
        self.btn_ok = QtWidgets.QPushButton(f"确认分配（{self._left}s）")
        self.btn_ok.setObjectName("primary")
        self.btn_ok.clicked.connect(self._confirm)
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setObjectName("ghost")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_ok)
        btns.addStretch(1)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        self._updating = False
        self._refresh_tip()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _checked_ids(self) -> List[str]:
        ids: List[str] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item and item.checkState() == QtCore.Qt.CheckState.Checked:
                ids.append(str(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        return ids

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        if self._updating:
            return
        checked = self._checked_ids()
        if len(checked) > self._need and item.checkState() == QtCore.Qt.CheckState.Checked:
            self._updating = True
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            self._updating = False
        self._refresh_tip()

    def _refresh_tip(self) -> None:
        n = len(self._checked_ids())
        self.lbl_tip.setText(
            f"已选 {n}/{self._need} · {self._left} 秒内未确认将自动使用前 {self._need} 个账号"
        )
        self.btn_ok.setEnabled(n == self._need)
        self.btn_ok.setText(f"确认分配（{self._left}s）")

    def _tick(self) -> None:
        self._left -= 1
        if self._left <= 0:
            self._timer.stop()
            self._auto_accept()
            return
        self._refresh_tip()

    def _confirm(self) -> None:
        ids = self._checked_ids()
        if len(ids) != self._need:
            return
        self._timer.stop()
        self._auto_ids = ids
        self.accept()

    def _auto_accept(self) -> None:
        # 超时：优先前 N 个
        self._auto_ids = [a.id for a in self._accounts[: self._need]]
        self.accept()

    def selected_ids(self) -> List[str]:
        return list(self._auto_ids)


class LoginDialog(QtWidgets.QDialog):
    """AW 风格：选择登录类型 → 登录成功写入账号库。"""

    def __init__(self, store: AccountStore, parent=None, scheduler=None) -> None:
        super().__init__(parent)
        self.store = store
        self.scheduler = scheduler
        self.setWindowTitle("登录入库")
        self.setModal(True)
        self.resize(520, 520)
        self.setStyleSheet(STYLE)
        self._method = "netease_email"

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("登录入库")
        title.setObjectName("brand")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        sub = QtWidgets.QLabel("选择登录类型，成功后写入账号库")
        sub.setObjectName("muted")
        lay.addWidget(title)
        lay.addWidget(sub)

        type_box = QtWidgets.QGroupBox("登录类型")
        method_wrap = QtWidgets.QVBoxLayout(type_box)
        self.method_group = QtWidgets.QButtonGroup(self)
        method_row = QtWidgets.QHBoxLayout()
        method_row.setSpacing(10)
        for i, m in enumerate(METHODS):
            rb = QtWidgets.QRadioButton(METHOD_LABELS.get(m, m))
            rb.setProperty("method", m)
            if i == 0:
                rb.setChecked(True)
            self.method_group.addButton(rb, i)
            method_row.addWidget(rb)
            rb.toggled.connect(self._on_method_toggle)
        method_row.addStretch(1)
        method_wrap.addLayout(method_row)
        lay.addWidget(type_box)

        form_box = QtWidgets.QGroupBox("凭据")
        form = QtWidgets.QFormLayout(form_box)
        form.setSpacing(10)
        self.ed_account = QtWidgets.QLineEdit()
        self.ed_account.setPlaceholderText("邮箱 / 手机号 / 4399 账号")
        self.ed_password = QtWidgets.QLineEdit()
        self.ed_password.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ed_password.setPlaceholderText("密码")
        self.ed_sms = QtWidgets.QLineEdit()
        self.ed_sms.setPlaceholderText("短信验证码")
        self.ed_sauth = QtWidgets.QPlainTextEdit()
        self.ed_sauth.setPlaceholderText("粘贴 SAUTH / Cookie JSON")
        self.ed_sauth.setMaximumHeight(100)
        self.ed_remark = QtWidgets.QLineEdit()
        self.ed_remark.setPlaceholderText("备注（可选）")
        form.addRow("账号", self.ed_account)
        form.addRow("密码", self.ed_password)
        sms_row = QtWidgets.QHBoxLayout()
        sms_row.addWidget(self.ed_sms)
        self.btn_sms = QtWidgets.QPushButton("发验证码")
        self.btn_sms.setObjectName("ghost")
        self.btn_sms.clicked.connect(self._send_sms)
        sms_row.addWidget(self.btn_sms)
        form.addRow("验证码", sms_row)
        form.addRow("SAUTH", self.ed_sauth)
        form.addRow("备注", self.ed_remark)
        lay.addWidget(form_box)

        self.lbl_status = QtWidgets.QLabel("等待登录…")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        btns = QtWidgets.QHBoxLayout()
        self.btn_login = QtWidgets.QPushButton("登录并入库")
        self.btn_login.setObjectName("primary")
        self.btn_login.clicked.connect(self._do_login)
        self.btn_verify = QtWidgets.QPushButton("打开验证链接")
        self.btn_verify.setObjectName("ghost")
        self.btn_verify.setVisible(False)
        self.btn_verify.clicked.connect(self._open_verify)
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.setObjectName("ghost")
        btn_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_login)
        btns.addWidget(self.btn_verify)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        self._verify_url = ""
        self._on_method_toggle()

    def _on_method_toggle(self) -> None:
        btn = self.method_group.checkedButton()
        if btn:
            self._method = str(btn.property("method") or "netease_email")
        m = self._method
        is_sauth = m == "sauth"
        is_sms = m == "netease_phone_sms"
        need_pass = m in ("netease_email", "netease_phone_password", "4399")
        self.ed_account.setVisible(not is_sauth)
        self.ed_password.setVisible(need_pass)
        self.ed_sms.setVisible(is_sms)
        self.btn_sms.setVisible(is_sms)
        self.ed_sauth.setVisible(is_sauth)
        if m == "netease_email":
            self.ed_account.setPlaceholderText("网易邮箱")
        elif m.startswith("netease_phone"):
            self.ed_account.setPlaceholderText("手机号")
        elif m == "4399":
            self.ed_account.setPlaceholderText("4399 账号")

    def _send_sms(self) -> None:
        phone = self.ed_account.text().strip()
        if not phone:
            self.lbl_status.setText("请先填手机号")
            return
        self.lbl_status.setText("发送验证码中…")
        QtWidgets.QApplication.processEvents()
        try:
            b, _ = ensure_bridge_running(timeout_sec=20.0)
            b.send_sms(phone)
            self.lbl_status.setText("验证码已发送，请查收短信")
        except BridgeError as exc:
            self.lbl_status.setText(str(exc))
            if exc.verify_url:
                self._verify_url = exc.verify_url
                self.btn_verify.setVisible(True)

    def _open_verify(self) -> None:
        if self._verify_url:
            webbrowser.open(self._verify_url)

    def _do_login(self) -> None:
        m = self._method
        account = self.ed_account.text().strip()
        password = self.ed_password.text()
        sms = self.ed_sms.text().strip()
        sauth = self.ed_sauth.toPlainText().strip()
        remark = self.ed_remark.text().strip()

        if m == "sauth":
            if not sauth:
                self.lbl_status.setText("请粘贴 SAUTH")
                return
            account = account or "sauth"
        elif not account:
            self.lbl_status.setText("请填写账号")
            return
        if m == "netease_phone_sms" and not sms:
            self.lbl_status.setText("请填写短信验证码")
            return
        if m in ("netease_email", "netease_phone_password", "4399") and not password:
            self.lbl_status.setText("请填写密码")
            return

        self.btn_login.setEnabled(False)
        self.lbl_status.setText("登录中…")
        QtWidgets.QApplication.processEvents()
        try:
            b, msg = ensure_bridge_running(timeout_sec=20.0)
            if "启动" in msg:
                self.lbl_status.setText(msg + "…")
                QtWidgets.QApplication.processEvents()
            if not b.token:
                raise BridgeError("未读到对接层凭据（配置文件缺失）")
            resp = b.login(
                m,
                account="" if m == "sauth" else account,
                password=password if m != "sauth" else "",
                sms_code=sms if m == "netease_phone_sms" else "",
                sauth=sauth if m == "sauth" else "",
            )
            if resp.get("need_captcha"):
                raise BridgeError("4399 需要图片验证码，请稍后再试或改用 SAUTH")
            label = str(resp.get("label") or account)
            saved_account = sauth if m == "sauth" else account
            saved_password = sauth if m == "sauth" else password
            acc = self.store.upsert_login(
                method=m,
                account=saved_account,
                password=saved_password,
                remark=remark,
                session_label=label,
            )
            # 登录成功顺手拉一次角色：否则账号库里「角色」列会是空的
            # （旧版没有这一步，是「账号底下看不到角色」的直接原因）
            probed = ""
            if self.scheduler is not None:
                self.lbl_status.setText("登录成功，正在拉取角色…")
                QtWidgets.QApplication.processEvents()
                try:
                    names = self.scheduler.probe_characters(acc.id)
                    if names:
                        probed = "\n角色：" + "、".join(names)
                    else:
                        probed = "\n该账号下没有角色（可用账号库的「创建角色」建一个）"
                except Exception as exc:  # noqa: BLE001 - 拉角色失败不该让登录算失败
                    probed = f"\n角色拉取失败：{str(exc)[:120]}"
                    self.store.update(acc.id, last_status=f"角色拉取失败：{str(exc)[:100]}")
            self.lbl_status.setText(f"已入库：{acc.display()}")
            QtWidgets.QMessageBox.information(
                self, "成功", f"登录成功，已写入账号库\n{acc.display()}{probed}"
            )
            self.accept()
        except BridgeError as exc:
            from .sauth import RATE_LIMIT_HINT, is_rate_limited

            if is_rate_limited(exc):
                self.lbl_status.setText("4399 登录被限流，请稍后再试")
                QtWidgets.QMessageBox.warning(self, "4399 限流", RATE_LIMIT_HINT)
            else:
                self.lbl_status.setText(str(exc)[:200])
            key_account = account if m != "sauth" else (sauth[:64] or "sauth")
            key_password = password if m != "sauth" else sauth
            acc = self.store.upsert_login(
                method=m,
                account=key_account,
                password=key_password,
                remark=remark,
            )
            if exc.verify_url:
                self._verify_url = exc.verify_url
                self.btn_verify.setVisible(True)
                self.store.update(
                    acc.id,
                    hang_status="需验证",
                    last_status=str(exc)[:120],
                    last_verify_url=exc.verify_url,
                )
            else:
                self.store.update(acc.id, hang_status="异常", last_status=str(exc)[:120])
        finally:
            self.btn_login.setEnabled(True)


class PasteImportDialog(QtWidgets.QDialog):
    """粘贴导入：直接贴多行「账号 密码」，或贴 CSV 文本。

    刻意与「导入（文件）」分开：号商批量号最常见的就是复制一坨文本，
    走文件反而多一步。默认**入库即停用**，密码全程不回显。
    """

    def __init__(self, store: AccountStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.result_stats: Dict[str, int] = {}
        self.setWindowTitle("粘贴导入")
        self.setModal(True)
        self.resize(700, 620)
        self.setStyleSheet(STYLE)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("粘贴导入")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        sub = QtWidgets.QLabel(
            "每行一条：账号 密码（空格 / 制表 / 逗号分隔均可）；"
            "也支持 CSV 文本（可带 method,account,password,remark 表头）；以 # 开头的行忽略。"
            "密码不会回显，报告只给长度与批次前缀。"
        )
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)

        opt = QtWidgets.QHBoxLayout()
        opt.setSpacing(10)
        opt.addWidget(QtWidgets.QLabel("默认类型"))
        self.cb_method = QtWidgets.QComboBox()
        for m in METHODS:
            self.cb_method.addItem(f"{METHOD_LABELS.get(m, m)} ({m})", m)
        if "4399" in METHODS:
            self.cb_method.setCurrentIndex(list(METHODS).index("4399"))
        opt.addWidget(self.cb_method)

        self.ck_enable = QtWidgets.QCheckBox("导入即启用")
        self.ck_enable.setToolTip("不勾选（推荐）：先入库停用，确认无误后再到账号库勾选启用")
        opt.addWidget(self.ck_enable)

        self.ck_update = QtWidgets.QCheckBox("覆盖已存在")
        self.ck_update.setToolTip("库里已有同 method+account 时，覆盖其密码")
        opt.addWidget(self.ck_update)

        self.ck_protect = QtWidgets.QCheckBox("保护库内原有账号")
        self.ck_protect.setToolTip("导入前把库里已有账号全标「游玩保护」，避免主号被卷入调度")
        opt.addWidget(self.ck_protect)
        opt.addStretch(1)
        lay.addLayout(opt)

        self.ed_text = QtWidgets.QPlainTextEdit()
        self.ed_text.setPlaceholderText(
            "在此粘贴，例如：\n\nexampleAcc00001 examplePass0001\nexampleAcc00002 examplePass0002"
        )
        lay.addWidget(self.ed_text, 1)

        self.lbl_preview = QtWidgets.QLabel("等待粘贴…")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_preview.setStyleSheet("color: #e8dcc8; font-size: 12px;")
        lay.addWidget(self.lbl_preview, 0)

        row = QtWidgets.QHBoxLayout()
        b_clip = QtWidgets.QPushButton("从剪贴板读取")
        b_clip.setObjectName("ghost")
        b_clip.clicked.connect(self._from_clipboard)
        b_clear = QtWidgets.QPushButton("清空")
        b_clear.setObjectName("ghost")
        b_clear.clicked.connect(lambda: self.ed_text.setPlainText(""))
        row.addWidget(b_clip)
        row.addWidget(b_clear)
        row.addStretch(1)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("导入")
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        lay.addLayout(row)

        self.ed_text.textChanged.connect(self._refresh_preview)
        self.cb_method.currentIndexChanged.connect(self._refresh_preview)
        self.ck_update.stateChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _method(self) -> str:
        return self.cb_method.currentData() or "4399"

    def _from_clipboard(self) -> None:
        # 优先 Qt 自己的剪贴板：面板里必定有 QApplication，
        # 这样打包时不必把 tkinter + Tcl/Tk 塞进 exe（省体积也更稳）。
        text = ""
        try:
            text = QtWidgets.QApplication.clipboard().text() or ""
        except Exception:  # noqa: BLE001 - Qt 剪贴板异常时回退
            text = ""
        if not text.strip():
            try:
                text = read_clipboard()
            except RuntimeError as exc:
                QtWidgets.QMessageBox.warning(self, "剪贴板", str(exc))
                return
        if not text.strip():
            QtWidgets.QMessageBox.information(self, "剪贴板", "剪贴板里没有文本。")
            return
        self.ed_text.setPlainText(text)

    def _current_plan(self):
        return build_plan(self.ed_text.toPlainText(), self.store, default_method=self._method())

    def _refresh_preview(self) -> None:
        plan = self._current_plan()
        head = " · ".join(
            [
                f"可新增 {len(plan.rows)}",
                f"库内已存在 {len(plan.updates)}",
                f"批内重复 {len(plan.dups_in_file)}",
                f"非法 {len(plan.invalid)}",
            ]
        )
        detail = ""
        if plan.invalid:
            line_no, _raw, reason = plan.invalid[0]
            detail = f"　｜　首条非法：L{line_no} {reason}"
        self.lbl_preview.setText(head + detail)

        will_write = bool(plan.rows) or (self.ck_update.isChecked() and bool(plan.updates))
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(will_write)

    def _on_ok(self) -> None:
        plan = self._current_plan()
        notes = risk_report(plan, self.store)
        if notes:
            msg = "导入前风险体检：\n\n" + "\n\n".join("· " + n for n in notes) + "\n\n继续导入？"
            if QtWidgets.QMessageBox.question(self, "确认导入", msg) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.result_stats = apply_plan(
            self.store,
            plan,
            enabled=self.ck_enable.isChecked(),
            update_existing=self.ck_update.isChecked(),
            protect_existing=self.ck_protect.isChecked(),
        )
        self.accept()


class CaptchaDialog(QtWidgets.QDialog):
    """4399 图片验证码：显示图 + 输入，用于「转 SAUTH」。

    取图是一次小的网络请求，直接同步拉（几百毫秒）；失败就显示占位文案，
    让用户可以「重新转换」再触发一次新的 captcha_id。
    """

    def __init__(self, captcha_id: str, account_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self._code = ""
        self.setWindowTitle("4399 图片验证码")
        self.setModal(True)
        self.resize(320, 300)
        self.setStyleSheet(STYLE)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        title = QtWidgets.QLabel("4399 图片验证码")
        title.setStyleSheet("font-size: 16px; font-weight: 800; color: #f0d2b0;")
        lay.addWidget(title)
        sub = QtWidgets.QLabel(f"账号：{account_label}" if account_label else "")
        sub.setObjectName("muted")
        lay.addWidget(sub)

        self.lbl_img = QtWidgets.QLabel("加载验证码…")
        self.lbl_img.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_img.setMinimumHeight(80)
        self.lbl_img.setStyleSheet("background:#0b1210; border:1px solid #2a3a34;")
        lay.addWidget(self.lbl_img)

        try:
            from .sauth import get_captcha

            img = get_captcha(captcha_id)
            pix = QtGui.QPixmap()
            if pix.loadFromData(img.image_bytes):
                self.lbl_img.setPixmap(pix)
                self.lbl_img.setText("")
            else:
                self.lbl_img.setText("验证码图片解码失败")
        except Exception as exc:  # noqa: BLE001 - 取图失败不该挡住用户看到原因
            self.lbl_img.setText(f"获取验证码失败\n{exc}")

        self.ed_code = QtWidgets.QLineEdit()
        self.ed_code.setPlaceholderText("输入图中字符")
        self.ed_code.returnPressed.connect(self._on_ok)
        lay.addWidget(self.ed_code)

        self.lbl_hint = QtWidgets.QLabel("看不清可直接取消，重新转换会换一张。")
        self.lbl_hint.setObjectName("muted")
        self.lbl_hint.setWordWrap(True)
        lay.addWidget(self.lbl_hint)
        lay.addStretch(1)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("跳过此号")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_ok(self) -> None:
        code = (self.ed_code.text() or "").strip()
        if not code:
            self.lbl_hint.setText("请输入验证码，或点「跳过此号」。")
            return
        self._code = code
        self.accept()

    def code(self) -> str:
        return self._code


class SauthConvertTask(QtCore.QObject):
    """一次 sauth 转换，跑在工作线程里。

    `finished` 从非 Qt 线程 emit，接收方在主线程 → Qt 自动走队列连接，安全。
    """

    finished = QtCore.Signal(str, object, object)  # account_id, sauth_text|None, error|None

    def __init__(
        self,
        account_id: str,
        account: str,
        password: str,
        *,
        captcha_id: str = "",
        captcha_code: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.account_id = account_id
        self.account = account
        self.password = password
        self.captcha_id = captcha_id
        self.captcha_code = captcha_code

    def run(self) -> None:
        try:
            from .gate import shared
            from .sauth import CaptchaRequired, convert, is_rate_limited

            gate = shared()
            d = gate.check()
            if not d.allowed:
                # 冷却期内**不要**再打一次：那只会把冷却拖更长
                self.finished.emit(self.account_id, None, RuntimeError(d.message()))
                return
            try:
                text = convert(
                    self.account,
                    self.password,
                    captcha_id=self.captcha_id,
                    captcha_code=self.captcha_code,
                )
            except CaptchaRequired:
                gate.note_attempt()  # 请求被用掉，但不算限流已过去
                raise
            except BaseException as exc:  # noqa: BLE001
                if is_rate_limited(exc):
                    gate.note_rate_limited(str(exc))
                else:
                    gate.note_failure()
                raise
            gate.note_success()
            self.finished.emit(self.account_id, text, None)
        except BaseException as exc:  # noqa: BLE001 - 异常必须带回 UI 线程展示
            self.finished.emit(self.account_id, None, exc)


class CreateCharacterDialog(QtWidgets.QDialog):
    """建角：输入角色名。多账号时可自动追加序号避免重名。"""

    def __init__(self, count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("创建角色")
        self.setModal(True)
        self.resize(420, 260)
        self.setStyleSheet(STYLE)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)

        title = QtWidgets.QLabel("创建角色")
        title.setStyleSheet("font-size: 16px; font-weight: 800; color: #f0d2b0;")
        lay.addWidget(title)
        sub = QtWidgets.QLabel(f"将为选中的 {count} 个无角色账号建角")
        sub.setObjectName("muted")
        lay.addWidget(sub)

        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setPlaceholderText("角色名")
        lay.addWidget(self.ed_name)

        self.ck_suffix = QtWidgets.QCheckBox("多账号时自动追加序号（角色名1 / 角色名2 / …）")
        self.ck_suffix.setChecked(count > 1)
        self.ck_suffix.setEnabled(count > 1)
        lay.addWidget(self.ck_suffix)

        hint = QtWidgets.QLabel(
            "建角走的是游戏侧 /game-character（与游戏客户端「创建角色」同一条实现），"
            "需要先能正常登录并选中示例服服。角色名可能受游戏侧限制（重名/长度/敏感词）。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("开始创建")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_ok(self) -> None:
        if not (self.ed_name.text() or "").strip():
            return
        self.accept()

    def base_name(self) -> str:
        return (self.ed_name.text() or "").strip()

    def with_suffix(self) -> bool:
        return self.ck_suffix.isEnabled() and self.ck_suffix.isChecked()


class CreateCharTask(QtCore.QObject):
    """一次建角，跑在工作线程。"""

    finished = QtCore.Signal(str, object, object)  # account_id, names|None, error|None

    def __init__(self, scheduler, account_id: str, name: str, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.account_id = account_id
        self.name = name

    def run(self) -> None:
        try:
            names = self.scheduler.create_character(self.account_id, self.name)
            self.finished.emit(self.account_id, list(names or []), None)
        except BaseException as exc:  # noqa: BLE001 - 异常要带回 UI 线程
            self.finished.emit(self.account_id, None, exc)


class ProbeCharTask(QtCore.QObject):
    """一次角色探测，跑在工作线程。"""

    finished = QtCore.Signal(str, object, object)  # account_id, names|None, error|None

    def __init__(self, scheduler, account_id: str, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.account_id = account_id

    def run(self) -> None:
        try:
            names = self.scheduler.probe_characters(self.account_id)
            self.finished.emit(self.account_id, list(names or []), None)
        except BaseException as exc:  # noqa: BLE001
            self.finished.emit(self.account_id, None, exc)


class WarmupTask(QtCore.QObject):
    """后台跑预热（全部账号：登录 + 选服 + 拉角色）。

    调度器侧 `warmup_accounts` 会把卡验证码的账号**收集**成 CAPTCHA 项、
    不阻塞后面的账号 —— 这正是"先全部登录一遍、把要验证码的集中弹出来"。
    """

    progress = QtCore.Signal(int, int, str)  # done, total, label
    finished = QtCore.Signal(object)  # WarmupSummary
    failed = QtCore.Signal(object)  # exception

    def __init__(self, scheduler, account_ids=None, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.account_ids = list(account_ids) if account_ids else None

    def run(self) -> None:
        try:
            summ = self.scheduler.warmup_accounts(
                self.account_ids,
                on_progress=lambda i, n, lb: self.progress.emit(i + 1, n, lb),
            )
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(exc)
            return
        self.finished.emit(summ)


class CaptchaSubmitTask(QtCore.QObject):
    """提交一次验证码，只补这一步登录（不重头再来）。"""

    finished = QtCore.Signal(str, object, object)  # account_id, characters|None, error|None

    def __init__(self, scheduler, account_id, captcha_id, code, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.account_id = account_id
        self.captcha_id = captcha_id
        self.code = code

    def run(self) -> None:
        try:
            names = self.scheduler.finish_captcha_login(
                self.account_id, self.captcha_id, self.code
            )
        except BaseException as exc:  # noqa: BLE001
            self.finished.emit(self.account_id, None, exc)
            return
        self.finished.emit(self.account_id, names, None)


class WarmupDialog(QtWidgets.QDialog):
    """预热结果：分类展示 + **就地填验证码**。

    以前验证码是死结：对接层给了验证码标识 和图片，但客户端把它丢掉了，
    于是同一账号反复撞验证码。现在图片直接显示在这里，填完立刻只补这一步。
    """

    def __init__(self, scheduler, summary, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.summary = summary
        self._rows: dict = {}
        self.setWindowTitle("预热结果")
        self.setModal(True)
        self.resize(780, 640)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        self.lbl_top = QtWidgets.QLabel(summary.text())
        self.lbl_top.setWordWrap(True)
        self.lbl_top.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_top)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        self.body_lay = QtWidgets.QVBoxLayout(body)
        self.body_lay.setContentsMargins(4, 4, 4, 4)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        for item in summary.items:
            self._add_row(item)
        self.body_lay.addStretch(1)

        row = QtWidgets.QHBoxLayout()
        btn_again = QtWidgets.QPushButton("重新预热")
        btn_again.clicked.connect(self.accept)
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(btn_again)
        row.addWidget(btn_close)
        lay.addLayout(row)

    # ---------- 单行 ----------

    def _add_row(self, item) -> None:
        box = QtWidgets.QFrame()
        box.setObjectName("warmRow")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        head = QtWidgets.QHBoxLayout()
        lab_name = QtWidgets.QLabel(item.label)
        lab_name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lab_stat = QtWidgets.QLabel(item.status_text)
        lab_stat.setStyleSheet("color:#d4a574;")
        head.addWidget(lab_name, 1)
        head.addWidget(lab_stat)
        v.addLayout(head)

        detail = QtWidgets.QLabel(item.detail or "，".join(item.characters[:6]))
        detail.setWordWrap(True)
        detail.setStyleSheet("color:#8a8f88;")
        v.addWidget(detail)

        rec = {"item": item, "stat": lab_stat, "detail": detail, "box": box}
        if item.status == "captcha":
            img = QtWidgets.QLabel()
            img.setFixedHeight(64)
            img.setStyleSheet("background:#0c1210;border:1px solid #2a3a34;")
            raw = item.captcha_image_base64
            if raw:
                try:
                    pm = QtGui.QPixmap()
                    pm.loadFromData(base64.b64decode(raw))
                    img.setPixmap(pm.scaledToHeight(62, QtCore.Qt.SmoothTransformation))
                except Exception:  # noqa: BLE001
                    img.setText("（图片解码失败）")
            else:
                img.setText("（没有返回图片）")
            v.addWidget(img)

            line = QtWidgets.QHBoxLayout()
            ed = QtWidgets.QLineEdit()
            ed.setPlaceholderText("输入图片里的验证码，回车提交")
            btn = QtWidgets.QPushButton("提交")
            line.addWidget(ed, 1)
            line.addWidget(btn)
            v.addLayout(line)
            rec.update({"edit": ed, "btn": btn, "img": img})
            ed.returnPressed.connect(lambda: self._submit(item.account_id))
            btn.clicked.connect(lambda: self._submit(item.account_id))

        self._rows[item.account_id] = rec
        self.body_lay.addWidget(box)

    # ---------- 提交验证码 ----------

    def _submit(self, account_id: str) -> None:
        rec = self._rows.get(account_id)
        if not rec or "edit" not in rec:
            return
        code = rec["edit"].text().strip()
        if not code:
            rec["edit"].setFocus()
            return
        rec["btn"].setEnabled(False)
        rec["btn"].setText("提交中…")
        task = CaptchaSubmitTask(
            self.scheduler, account_id, rec["item"].captcha_id, code, self
        )
        task.finished.connect(self._on_submitted)
        threading.Thread(target=task.run, daemon=True, name="aw-captcha").start()

    def _on_submitted(self, account_id: str, names, error) -> None:
        rec = self._rows.get(account_id)
        if not rec:
            return
        btn = rec.get("btn")
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("提交")
        if error is not None:
            rec["stat"].setText("失败")
            rec["detail"].setText(str(error)[:200])
            rec["detail"].setStyleSheet("color:#e06c75;")
            return
        names = list(names or [])
        if names:
            rec["stat"].setText("就绪")
            rec["detail"].setText("角色：" + "、".join(names[:8]))
        else:
            rec["stat"].setText("无角色")
            rec["detail"].setText("账号下没有角色，需要先建角")
        rec["detail"].setStyleSheet("color:#7dceb0;")
        for key in ("edit", "btn", "img"):
            w = rec.get(key)
            if w is not None:
                w.setEnabled(False)
                w.setVisible(False)


class AccountLibraryDialog(QtWidgets.QDialog):
    """账号库：三段式展示 签到次数 / 角色签到 / 登录·挂机·合计。"""

    # 转 SAUTH 时账号之间的间隔。4399 登录接口会限流（HTTP 202），
    # 背靠背连续打会让整批一起失败，所以刻意放慢。
    CONVERT_GAP_MS = 3000

    def __init__(self, store: AccountStore, parent=None, scheduler=None) -> None:
        super().__init__(parent)
        self.store = store
        self.scheduler = scheduler
        self.setWindowTitle("账号库")
        self.resize(1180, 640)
        self.setStyleSheet(STYLE)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(12)

        head = QtWidgets.QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("账号库")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        tip = QtWidgets.QLabel(
            "账号勾选=参与调度；「游玩保护」=永不挂机（避免同号顶掉游玩端）。"
            "下方角色列表可单独启用。检测到游玩端在线角色时，同账号会自动跳过。"
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(tip)
        head.addLayout(title_col, 1)
        self.lbl_count = QtWidgets.QLabel("")
        self.lbl_count.setObjectName("muted")
        head.addWidget(self.lbl_count, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        self.table = QtWidgets.QTableWidget(0, 14)
        self.table.setHorizontalHeaderLabels(
            [
                "启用",
                "游玩保护",
                "登录类型",
                "账号",
                "备注",
                "角色",
                "角色状态",
                "签到次数",
                "角色签到",
                "登录",
                "挂机",
                "合计",
                "登录方式",
                "最近状态",
            ]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(1, 72)
        self.table.setColumnWidth(7, 72)
        self.table.setColumnWidth(8, 180)
        self.table.setColumnWidth(9, 72)
        self.table.setColumnWidth(10, 72)
        self.table.setColumnWidth(11, 72)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._on_sel)
        lay.addWidget(self.table, 1)

        # 三段式详情（随选中行刷新）
        tri = QtWidgets.QHBoxLayout()
        tri.setSpacing(10)
        self.box_sign = self._make_tri_box("① 签到次数")
        self.box_chars = self._make_tri_box("② 角色启用（勾选参与循环挂角）")
        self.box_time = self._make_tri_box("③ 登录时长")
        self.lbl_tri_sign = QtWidgets.QLabel("—")
        self.lbl_tri_time = QtWidgets.QLabel("—")
        for lab in (self.lbl_tri_sign, self.lbl_tri_time):
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #e8dcc8; font-size: 13px;")
        self.char_list = QtWidgets.QListWidget()
        self.char_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.char_list.setMaximumHeight(120)
        self.char_list.itemChanged.connect(self._on_char_item_changed)
        self._char_edit_aid = ""
        self.box_sign.layout().addWidget(self.lbl_tri_sign)
        self.box_chars.layout().addWidget(self.char_list)
        self.box_time.layout().addWidget(self.lbl_tri_time)
        tri.addWidget(self.box_sign, 1)
        tri.addWidget(self.box_chars, 2)
        tri.addWidget(self.box_time, 2)
        lay.addLayout(tri)

        self.btn_sauth: Optional[QtWidgets.QPushButton] = None
        self.btn_sauth_all: Optional[QtWidgets.QPushButton] = None
        self.btn_create_char: Optional[QtWidgets.QPushButton] = None
        self.btn_probe: Optional[QtWidgets.QPushButton] = None

        def _mk(text: str, slot, role: str, tip: str = "") -> QtWidgets.QPushButton:
            b = QtWidgets.QPushButton(text)
            b.setObjectName(role)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        def _cap(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setObjectName("toolCap")
            return lab

        def _sep() -> QtWidgets.QFrame:
            f = QtWidgets.QFrame()
            f.setObjectName("toolSep")
            f.setFixedWidth(1)
            f.setFixedHeight(20)
            return f

        # 第一行：对「选中的账号」做什么
        self.lbl_sauth = QtWidgets.QLabel("")
        self.lbl_sauth.setObjectName("muted")
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(_cap("选中账号"))
        self.btn_probe = _mk("检测角色", self._probe_characters, "primary",
                             "登录选中账号并拉取角色列表，回填到账号库。\n已有角色也能重测。")
        self.btn_create_char = _mk("创建角色", self._create_characters, "primary",
                                   "给选中的「无角色」账号建角。\n空号启用了也排不进挂机队列。")
        self.btn_sauth = _mk("转 SAUTH", self._convert_sauth, "primary",
                            "把**选中**账号转一次 sauth 并缓存（已有缓存也强制重转）。")
        self.btn_sauth_all = _mk("一键更新 SAUTH", self._update_sauth_all, "primary",
                                 "把**库内全部** 4399 账号的 sauth 补一遍 / 刷新一遍，不用逐个勾选。")
        row1.addWidget(self.btn_probe)
        row1.addWidget(self.btn_create_char)
        row1.addWidget(self.btn_sauth)
        row1.addWidget(self.btn_sauth_all)
        row1.addWidget(_sep())
        row1.addWidget(_mk("标已挂完", self._mark_done, "ghost", "把选中账号的角色标为已挂完（跳过调度）"))
        row1.addWidget(_mk("清挂完", self._clear_done, "ghost", "清除选中账号的已挂完标记"))
        row1.addWidget(_sep())
        row1.addWidget(_mk("保存启用/备注", self._save_edits, "primary"))
        row1.addWidget(_mk("删除选中", self._del, "danger"))
        row1.addWidget(self.lbl_sauth)
        row1.addStretch(1)
        lay.addLayout(row1)

        # 第二行：库级数据操作
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(_cap("账号库"))
        row2.addWidget(_mk("导入文件", self._import, "ghost"))
        row2.addWidget(_mk("粘贴导入", self._import_paste, "primary"))
        row2.addWidget(
            _mk(
                "预热全部",
                self._warmup_all,
                "primary",
                "把库内 4399 账号全部过一遍：登录 → 选服 → 拉角色。\n"
                "需要验证码的会集中列出来，你一次填完。\n"
                "优先复用已有会话与 sauth，尽量不打 4399 密码登录。",
            )
        )
        row2.addWidget(_mk("导出 CSV", self._export_csv, "ghost"))
        row2.addWidget(_mk("导出 Excel", self._export_xlsx, "ghost"))
        row2.addWidget(_sep())
        row2.addWidget(_mk("刷新", self.reload, "ghost"))
        row2.addWidget(_mk("关闭", self.accept, "ghost"))
        row2.addStretch(1)
        self.ck_only_empty = QtWidgets.QCheckBox("只看无角色")
        self.ck_only_empty.setToolTip("只显示没有任何角色的账号（这些号需要先建角才能挂机）")
        self.ck_only_empty.stateChanged.connect(lambda _=0: self.reload())
        row2.addWidget(self.ck_only_empty)
        lay.addLayout(row2)

        # 转 SAUTH 状态机（见 _convert_sauth）
        self._sauth_queue: List[Tuple[str, str, str]] = []
        self._sauth_ctx: Dict[str, Tuple[str, str]] = {}
        self._sauth_total = 0
        self._sauth_done = 0
        self._sauth_failed: List[Tuple[str, str]] = []
        self._sauth_skip: List[str] = []
        self._sauth_threads: List[tuple] = []
        self._convert_aborted = False
        self._sauth_running = False
        self._sauth_auto = False

        # 建角状态机（见 _create_characters）
        self._cc_queue: List[Tuple[str, str]] = []  # (account_id, 角色名)
        self._cc_total = 0
        self._cc_done = 0
        self._cc_failed: List[Tuple[str, str]] = []
        self._cc_threads: List[tuple] = []

        # 检测角色状态机（见 _probe_characters）
        self._pc_queue: List[str] = []
        self._pc_total = 0
        self._pc_done = 0
        self._pc_empty: List[str] = []
        self._pc_failed: List[Tuple[str, str]] = []
        self._pc_threads: List[tuple] = []

        self.reload()

    @staticmethod
    def _make_tri_box(title: str) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox { font-weight: 700; color: #f0d2b0; border: 1px solid #3a342c; "
            "border-radius: 8px; margin-top: 10px; padding: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 14, 10, 10)
        return box

    def _on_sel(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self.lbl_tri_sign.setText("选中一行查看三段详情")
            self._fill_char_list(None)
            self.lbl_tri_time.setText("—")
            return
        acc = self.store.get(ids[0])
        if not acc:
            return
        self.lbl_tri_sign.setText(f"当前账号累计签到 / 里程碑次数：{int(acc.sign_count or 0)}")
        self._fill_char_list(acc)
        login, hang, total = acc.time_parts()
        char_time = acc.chars_time_text()
        self.lbl_tri_time.setText(
            f"登录 {login}\n挂机 {hang}\n合计 {total}\n\n各角色合计时长：\n{char_time}"
        )

    def _fill_char_list(self, acc) -> None:
        self.char_list.blockSignals(True)
        self.char_list.clear()
        self._char_edit_aid = acc.id if acc else ""
        if not acc:
            self.char_list.blockSignals(False)
            return
        rows = list(acc.characters_data or [])
        if not rows and acc.character:
            rows = [
                {
                    "name": acc.character,
                    "enabled": True,
                    "online_done": bool(acc.online_done),
                    "hang_status": acc.hang_status or "未挂机",
                    "sign_count": int(acc.sign_count or 0),
                }
            ]
        if not rows:
            item = QtWidgets.QListWidgetItem("暂无角色（检测角色或挂机后写入）")
            item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.char_list.addItem(item)
        else:
            for r in rows:
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                done = bool(r.get("online_done"))
                st = "已挂完" if done else str(r.get("hang_status") or "未挂机")
                text = f"{name}　签到 {int(r.get('sign_count') or 0)}　· {st}"
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, name)
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    | QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                en = str(r.get("enabled", True)).lower() not in ("0", "false", "no", "")
                if isinstance(r.get("enabled"), bool):
                    en = bool(r.get("enabled"))
                item.setCheckState(
                    QtCore.Qt.CheckState.Checked if en else QtCore.Qt.CheckState.Unchecked
                )
                self.char_list.addItem(item)
        self.char_list.blockSignals(False)

    def _on_char_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        aid = self._char_edit_aid
        name = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip()
        if not aid or not name:
            return
        enabled = item.checkState() == QtCore.Qt.CheckState.Checked
        self.store.set_character_flags(aid, name, enabled=enabled)

    def reload(self) -> None:
        self.store.load()
        self.table.setRowCount(0)
        done = running = empty = 0
        only_empty = bool(self.ck_only_empty and self.ck_only_empty.isChecked())
        for acc in self.store.accounts:
            if not acc.has_characters():
                empty += 1
            # 「只看无角色」= 只看需要建角的号
            if only_empty and acc.has_characters():
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(
                QtCore.Qt.CheckState.Checked if acc.enabled else QtCore.Qt.CheckState.Unchecked
            )
            chk.setData(QtCore.Qt.ItemDataRole.UserRole, acc.id)
            self.table.setItem(r, 0, chk)
            prot = QtWidgets.QTableWidgetItem()
            prot.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            prot.setCheckState(
                QtCore.Qt.CheckState.Checked
                if getattr(acc, "play_protect", False)
                else QtCore.Qt.CheckState.Unchecked
            )
            prot.setToolTip("勾选后永不挂机，避免同号顶掉你自己的游玩端")
            self.table.setItem(r, 1, prot)
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(acc.method_label()))
            self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(acc.account[:64]))
            self.table.setItem(r, 4, QtWidgets.QTableWidgetItem(acc.remark))
            self.table.setItem(r, 5, QtWidgets.QTableWidgetItem(acc.character))
            status_txt = acc.char_state_text()
            hang_item = QtWidgets.QTableWidgetItem(status_txt)
            if not acc.has_characters():
                # 空号：提示色 + 说明，这是「启用了也挂不了」的状态
                hang_item.setForeground(QtGui.QColor("#e0a458"))
                hang_item.setToolTip(
                    "该账号没有任何角色，排不进挂机队列（队列按「账号+角色」出单元）。\n"
                    "请用「创建角色」给它建一个，或先在游戏里建好。"
                )
            elif acc.online_done or acc.hang_status == "已挂完" or "已挂完" in status_txt:
                hang_item.setForeground(QtGui.QColor("#7dceb0"))
                if acc.online_done or (
                    acc.characters_data
                    and all(bool(x.get("online_done")) for x in acc.characters_data if x.get("name"))
                ):
                    done += 1
            elif acc.hang_status in ("需验证", "异常") or "异常" in status_txt:
                hang_item.setForeground(QtGui.QColor("#ff9aa3"))
            elif acc.hang_status == "挂机中" or "挂机中" in status_txt:
                hang_item.setForeground(QtGui.QColor("#d4a574"))
                running += 1
            self.table.setItem(r, 6, hang_item)
            self.table.setItem(r, 7, QtWidgets.QTableWidgetItem(str(int(acc.sign_count or 0))))
            self.table.setItem(r, 8, QtWidgets.QTableWidgetItem(acc.chars_sign_text()))
            login, hang, total = acc.time_parts()
            self.table.setItem(r, 9, QtWidgets.QTableWidgetItem(login))
            self.table.setItem(r, 10, QtWidgets.QTableWidgetItem(hang))
            self.table.setItem(r, 11, QtWidgets.QTableWidgetItem(total))
            sauth_txt = acc.sauth_state_text()
            sauth_item = QtWidgets.QTableWidgetItem(sauth_txt)
            if acc.method == "sauth" or (acc.method == "4399" and acc.has_sauth()):
                sauth_item.setForeground(QtGui.QColor("#7dceb0"))
                sauth_item.setToolTip(
                    "已缓存 sauth：挂机时直接复用，不再打 4399 密码登录。"
                    "失效后会自动回退密码登录。"
                )
            elif acc.method == "4399":
                sauth_item.setForeground(QtGui.QColor("#d4a574"))
                sauth_item.setToolTip(
                    "尚未转换：每次挂机都要打一次 4399 密码登录。"
                    "选中后点「转 SAUTH」可缓存下来。"
                )
            self.table.setItem(r, 12, sauth_item)
            self.table.setItem(r, 13, QtWidgets.QTableWidgetItem((acc.last_status or "")[:80]))
        n = len(self.store.accounts)
        shown = self.table.rowCount()
        extra = f" · 显示 {shown}" if only_empty else ""
        empty_txt = f" · ⚠ 无角色 {empty}" if empty else ""
        self.lbl_count.setText(
            f"{n} 账号 · 挂机中 {running} · 已挂完 {done}{empty_txt}{extra}"
        )
        self._on_sel()

    def _selected_ids(self) -> List[str]:
        ids = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), 0)
            if item:
                ids.append(str(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        return ids

    def _save_edits(self) -> None:
        for r in range(self.table.rowCount()):
            item0 = self.table.item(r, 0)
            if not item0:
                continue
            aid = str(item0.data(QtCore.Qt.ItemDataRole.UserRole))
            enabled = item0.checkState() == QtCore.Qt.CheckState.Checked
            prot_item = self.table.item(r, 1)
            play_protect = (
                prot_item.checkState() == QtCore.Qt.CheckState.Checked if prot_item else False
            )
            remark = self.table.item(r, 4).text() if self.table.item(r, 4) else ""
            character = self.table.item(r, 5).text() if self.table.item(r, 5) else ""
            self.store.update(
                aid,
                enabled=enabled,
                play_protect=play_protect,
                remark=remark,
                character=character,
            )
        self.reload()
        QtWidgets.QMessageBox.information(self, "保存", "已保存")

    def _del(self) -> None:
        for aid in self._selected_ids():
            self.store.remove(aid)
        self.reload()

    def _selected_char_names(self) -> List[str]:
        names = []
        for item in self.char_list.selectedItems():
            n = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip()
            if n:
                names.append(n)
        return names

    def _mark_done(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        aid = ids[0]
        names = self._selected_char_names()
        acc = self.store.get(aid)
        if not names and acc:
            names = acc.iter_character_names()
        if names:
            for name in names:
                self.store.set_character_flags(aid, name, online_done=True, hang_status="已挂完")
        else:
            self.store.update(aid, online_done=True, hang_status="已挂完", stage_text="已挂完")
        self.reload()

    def _clear_done(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        aid = ids[0]
        names = self._selected_char_names()
        acc = self.store.get(aid)
        if not names and acc:
            names = acc.iter_character_names()
        if names:
            for name in names:
                self.store.set_character_flags(
                    aid, name, online_done=False, hang_status="未挂机"
                )
        else:
            self.store.update(aid, online_done=False, hang_status="未挂机")
        self.reload()

    def _import(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入账号", "", "表格 (*.csv *.xlsx);;CSV (*.csv);;Excel (*.xlsx)"
        )
        if not path:
            return
        replace = (
            QtWidgets.QMessageBox.question(self, "导入", "清空现有再导入？\n否=追加")
            == QtWidgets.QMessageBox.StandardButton.Yes
        )
        p = Path(path)
        n = self.store.import_xlsx(p, replace=replace) if p.suffix.lower() == ".xlsx" else self.store.import_csv(
            p, replace=replace
        )
        self.reload()
        self._auto_convert_after_import(f"已导入 {n} 条")

    def _import_paste(self) -> None:
        dlg = PasteImportDialog(self.store, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        st = dlg.result_stats or {}
        self.reload()
        self._auto_convert_after_import(
            f"新增 {st.get('added', 0)} · 覆盖 {st.get('updated', 0)} · "
            f"保护 {st.get('protected', 0)} · 批内重复跳过 {st.get('dups_in_file', 0)}"
        )

    def _auto_convert_after_import(self, summary: str) -> None:
        """导入完成后自动给「还没有 sauth」的 4399 账号转一次 sauth。

        只补缺失的（mode=missing），所以重复导入不会把已有的缓存再打一遍。
        账号之间自动留间隔、遇限流立即停批（都在 _sauth_batch 里）。
        """
        if any(
            a.method == "4399" and a.password and not a.has_sauth() for a in self.store.accounts
        ):
            QtWidgets.QMessageBox.information(
                self,
                "导入完成",
                f"{summary}\n\n接下来会自动为尚未转换的 4399 账号转 SAUTH。\n"
                "需要图片验证码时会弹窗；被 4399 限流会自动停批，剩下的可稍后用"
                "「一键更新 SAUTH」补。",
            )
            self._sauth_batch(scope="all", mode="missing", confirm=False, quiet_if_empty=True)
        else:
            QtWidgets.QMessageBox.information(self, "导入完成", summary)

    # ----- 转 SAUTH：先转一次并缓存，之后挂机复用 -----

    # 缓存超过这么多天就认为该刷新了（一键更新时用于判断"过期"）
    SAUTH_STALE_DAYS = 7

    def _warmup_all(self) -> None:
        """预热全部 4399 账号，把需要验证码的集中列出来。"""
        if self.scheduler is None:
            QtWidgets.QMessageBox.warning(self, "预热", "没有调度器实例，无法预热")
            return
        ids = [a.id for a in self.store.accounts if a.method == "4399"]
        if not ids:
            QtWidgets.QMessageBox.information(self, "预热", "库里没有 4399 账号")
            return

        bar = QtWidgets.QProgressDialog("正在预热（登录 + 选服 + 拉角色）…", "取消", 0, len(ids), self)
        bar.setWindowTitle("预热")
        bar.setWindowModality(QtCore.Qt.WindowModal)
        bar.setMinimumDuration(0)
        bar.setAutoClose(False)
        bar.setAutoReset(False)

        box: dict = {"summary": None, "error": None, "cancelled": False}
        task = WarmupTask(self.scheduler, ids, self)

        def _progress(done: int, total: int, label: str) -> None:
            if box["cancelled"]:
                return
            bar.setMaximum(max(1, total))
            bar.setValue(done)
            bar.setLabelText(f"预热中 {done}/{total}：{label}")

        def _done(summ) -> None:
            box["summary"] = summ
            bar.reset()

        def _fail(exc) -> None:
            box["error"] = exc
            bar.reset()

        task.progress.connect(_progress)
        task.finished.connect(_done)
        task.failed.connect(_fail)
        bar.canceled.connect(lambda: box.update({"cancelled": True}))
        threading.Thread(target=task.run, daemon=True, name="aw-warmup").start()
        bar.exec()

        # 进度条关掉后，等结果回填（信号是队列连接，主线程会在下一次事件循环收到）
        deadline = time.time() + 3.0
        while box["summary"] is None and box["error"] is None and time.time() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.05)

        if box["error"] is not None:
            QtWidgets.QMessageBox.critical(self, "预热失败", str(box["error"]))
            return
        summ = box["summary"]
        if summ is None:
            return
        WarmupDialog(self.scheduler, summ, self).exec()
        self.reload()

    def _convert_sauth(self) -> None:
        """转 SAUTH：只处理**选中**的账号（已有缓存也强制重转）。"""
        self._sauth_batch(scope="selected", mode="force", confirm=True)

    def _update_sauth_all(self) -> None:
        """一键更新：库内**全部** 4399 账号，不用逐个勾选。"""
        missing = sum(
            1 for a in self.store.accounts if a.method == "4399" and a.password and not a.has_sauth()
        )
        stale = sum(
            1
            for a in self.store.accounts
            if a.method == "4399" and a.password and a.has_sauth() and self._sauth_is_stale(a)
        )
        total = sum(1 for a in self.store.accounts if a.method == "4399" and a.password)
        if not total:
            QtWidgets.QMessageBox.information(self, "一键更新 SAUTH", "账号库里没有带密码的 4399 账号。")
            return

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("一键更新 SAUTH")
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText(
            f"库内 4399 账号 {total} 个：\n"
            f"  · 尚未转换 {missing} 个\n"
            f"  · 已过期（>{self.SAUTH_STALE_DAYS} 天）{stale} 个\n"
            f"  · 其余较新\n\n"
            "每个账号要打一次 4399 密码登录；账号之间会自动留间隔，"
            "一旦被限流会**立即停批**，剩下的留到下次。"
        )
        yes = box.addButton("只更新缺失/过期的（推荐）", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("全部强制重转", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is yes:
            self._sauth_batch(scope="all", mode="stale_or_missing", confirm=False)
        elif clicked is no:
            self._sauth_batch(scope="all", mode="force", confirm=False)

    def _sauth_is_stale(self, acc: Account) -> bool:
        if not acc.has_sauth():
            return True
        age = time.time() - float(acc.sauth_at or 0.0)
        return age >= self.SAUTH_STALE_DAYS * 86400

    def _sauth_targets(self, ids: List[str], mode: str) -> Tuple[List[Tuple[str, str, str]], List[str]]:
        """按 mode 从 ids 里挑出可转换的 (account_id, 账号, 密码)。

        mode:
          force            全转（已有缓存也重转）
          missing          只补没有缓存的
          stale_or_missing 补缺失 + 刷新过期的
        """
        targets: List[Tuple[str, str, str]] = []
        skipped: List[str] = []
        for aid in ids:
            acc = self.store.get(aid)
            if acc is None:
                continue
            if acc.method != "4399":
                skipped.append(f"{acc.display()}（非 4399，无需转换）")
                continue
            if not (acc.password or "").strip():
                skipped.append(f"{acc.display()}（无密码，无法转换）")
                continue
            if mode == "missing" and acc.has_sauth():
                skipped.append(f"{acc.display()}（已有 SAUTH）")
                continue
            if mode == "stale_or_missing" and acc.has_sauth() and not self._sauth_is_stale(acc):
                skipped.append(f"{acc.display()}（{acc.sauth_age_text()}，未过期）")
                continue
            targets.append((acc.id, acc.account, acc.password))
        return targets, skipped

    def _sauth_batch(
        self,
        *,
        scope: str,
        mode: str,
        confirm: bool,
        quiet_if_empty: bool = False,
    ) -> None:
        """统一的转 SAUTH 入口。scope: selected / all。"""
        if self._sauth_running:
            if not quiet_if_empty:
                QtWidgets.QMessageBox.information(
                    self, "转 SAUTH", "已经有一批在转换中，等它跑完再来。"
                )
            return

        if scope == "selected":
            ids = self._selected_ids()
            if not ids:
                QtWidgets.QMessageBox.information(self, "转 SAUTH", "请先在列表里选中要转换的账号。")
                return
        else:
            ids = [a.id for a in self.store.accounts]

        targets, skipped = self._sauth_targets(ids, mode)
        if not targets:
            if not quiet_if_empty:
                QtWidgets.QMessageBox.information(
                    self,
                    "转 SAUTH",
                    "没有需要转换的账号。\n\n" + "\n".join(skipped[:10]),
                )
            return

        if confirm and (
            QtWidgets.QMessageBox.question(
                self,
                "转 SAUTH",
                f"将为 {len(targets)} 个 4399 账号各打一次密码登录，换取 sauth 并缓存。\n\n"
                "之后挂机直接复用 sauth，不再每次打 4399 密码登录。\n"
                "期间若需要图片验证码会弹窗让你填；sauth 失效时会自动回退密码登录。\n\n继续？",
            )
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return

        self._sauth_queue = list(targets)
        self._sauth_ctx = {aid: (acct, pw) for aid, acct, pw in targets}
        self._sauth_total = len(targets)
        self._sauth_done = 0
        self._sauth_failed = []
        self._sauth_skip = list(skipped)
        self._sauth_threads = []
        self._convert_aborted = False
        self._sauth_running = True
        self._sauth_auto = quiet_if_empty
        for b in (self.btn_sauth, self.btn_sauth_all):
            if b:
                b.setEnabled(False)
        self._convert_schedule(0)

    def _convert_sauth_legacy(self) -> None:  # pragma: no cover - 保留旧入口名
        self._convert_sauth()

    def _convert_next(self) -> None:
        if self._convert_aborted:
            self._convert_finish()
            return
        if not self._sauth_queue:
            self._convert_finish()
            return
        aid, acct, pw = self._sauth_queue.pop(0)
        if self.lbl_sauth:
            self.lbl_sauth.setText(f"转 SAUTH {self._sauth_done + 1}/{self._sauth_total} …")
        self._convert_run(aid, acct, pw)

    def _convert_schedule(self, delay_ms: int = 0) -> None:
        """账号之间留间隔：4399 对登录接口限流，背靠背打会立刻吃 202。"""
        if delay_ms <= 0:
            self._convert_next()
        else:
            QtCore.QTimer.singleShot(int(delay_ms), self._convert_next)

    def _convert_run(
        self, aid: str, acct: str, pw: str, *, captcha_id: str = "", captcha_code: str = ""
    ) -> None:
        task = SauthConvertTask(aid, acct, pw, captcha_id=captcha_id, captcha_code=captcha_code)
        task.finished.connect(self._on_convert_done)
        t = threading.Thread(target=task.run, daemon=True, name=f"sauth-{aid[:8]}")
        # 线程与 task 都要留引用，否则可能被 GC 掉导致信号丢失
        self._sauth_threads.append((task, t))
        t.start()

    def _on_convert_done(self, aid: str, sauth_text, error) -> None:
        from .sauth import CaptchaRequired, is_rate_limited

        acct, pw = self._sauth_ctx.get(aid, ("", ""))
        if sauth_text:
            self.store.set_sauth(aid, sauth_text)
            self._sauth_done += 1
            self._convert_schedule(self.CONVERT_GAP_MS)
            return

        if isinstance(error, CaptchaRequired):
            cid = str(getattr(error, "captcha_id", "") or "")
            dlg = CaptchaDialog(cid, acct, self)
            if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted and dlg.code():
                # 带验证码重试**同一个**账号（不计入间隔）
                self._convert_run(aid, acct, pw, captcha_id=cid, captcha_code=dlg.code())
                return
            self._sauth_failed.append((acct, "已跳过（验证码）"))
            self._convert_schedule(self.CONVERT_GAP_MS)
            return

        if is_rate_limited(error):
            # 继续打只会加深限流：立刻停批，把剩下的号留给下次
            self._convert_aborted = True
            remaining = len(self._sauth_queue)
            self._sauth_failed.append((acct, "4399 限流"))
            if remaining:
                self._sauth_skip.append(f"剩余 {remaining} 个账号未处理（避免加深限流）")
            self._convert_finish()
            return

        self._sauth_failed.append((acct, str(error)[:120]))
        self._convert_schedule(self.CONVERT_GAP_MS)

    def _convert_finish(self) -> None:
        from .sauth import RATE_LIMIT_HINT

        self._sauth_running = False
        was_auto = self._sauth_auto
        self._sauth_auto = False
        for b in (self.btn_sauth, self.btn_sauth_all):
            if b:
                b.setEnabled(True)
        self.reload()
        if self.lbl_sauth:
            tail = f" · 失败/跳过 {len(self._sauth_failed)}" if self._sauth_failed else ""
            prefix = "导入后自动转 SAUTH" if was_auto else "转 SAUTH"
            self.lbl_sauth.setText(f"{prefix} 成功 {self._sauth_done}/{self._sauth_total}{tail}")

        # 导入后自动转：全部成功且没失败时不弹框，避免每次导入都被打断
        if was_auto and self._sauth_done == self._sauth_total and not self._sauth_failed:
            return

        lines = [f"成功 {self._sauth_done} / {self._sauth_total}"]
        if self._sauth_skip:
            lines.append("\n跳过：\n  " + "\n  ".join(self._sauth_skip[:10]))
        if self._sauth_failed:
            lines.append(
                "\n失败：\n  " + "\n  ".join(f"{a}：{e}" for a, e in self._sauth_failed[:10])
            )
        if any("限流" in e for _a, e in self._sauth_failed):
            lines.append("\n" + RATE_LIMIT_HINT)
        lines.append("\n已缓存的账号挂机时会走 sauth 复用；失效自动回退密码登录。")
        QtWidgets.QMessageBox.information(self, "转 SAUTH", "\n".join(lines))

    # ----- 创建角色：只针对无角色账号 -----

    CC_GAP_MS = 1500
    PC_GAP_MS = 1200

    def _probe_characters(self) -> None:
        """登录选中账号并拉取角色列表，回填账号库。"""
        if self.scheduler is None:
            QtWidgets.QMessageBox.warning(self, "检测角色", "面板未就绪（拿不到调度器）。")
            return
        ids = self._selected_ids()
        if not ids:
            QtWidgets.QMessageBox.information(self, "检测角色", "请先在列表里选中要检测的账号。")
            return
        targets = [a for a in (self.store.get(i) for i in ids) if a is not None]
        if not targets:
            return

        self._pc_queue = [a.id for a in targets]
        self._pc_total = len(targets)
        self._pc_done = 0
        self._pc_empty = []
        self._pc_failed = []
        self._pc_threads = []
        if self.btn_probe:
            self.btn_probe.setEnabled(False)
        self._pc_next()

    def _pc_next(self) -> None:
        if not self._pc_queue:
            self._pc_finish()
            return
        aid = self._pc_queue.pop(0)
        acc = self.store.get(aid)
        label = acc.display() if acc else aid[:8]
        if self.lbl_sauth:
            self.lbl_sauth.setText(f"检测角色 {self._pc_done + 1}/{self._pc_total} … {label}")
        task = ProbeCharTask(self.scheduler, aid)
        task.finished.connect(self._on_pc_done)
        t = threading.Thread(target=task.run, daemon=True, name=f"probe-{aid[:8]}")
        self._pc_threads.append((task, t))
        t.start()

    def _on_pc_done(self, aid: str, names, error) -> None:
        acc = self.store.get(aid)
        label = acc.display() if acc else aid[:8]
        if error is not None:
            self._pc_failed.append((label, str(error)[:140]))
        else:
            self._pc_done += 1
            if not names:
                self._pc_empty.append(label)
        QtCore.QTimer.singleShot(self.PC_GAP_MS, self._pc_next)

    def _pc_finish(self) -> None:
        if self.btn_probe:
            self.btn_probe.setEnabled(True)
        self.reload()
        if self.lbl_sauth:
            parts = [f"检测成功 {self._pc_done}/{self._pc_total}"]
            if self._pc_empty:
                parts.append(f"无角色 {len(self._pc_empty)}")
            if self._pc_failed:
                parts.append(f"失败 {len(self._pc_failed)}")
            self.lbl_sauth.setText(" · ".join(parts))

        lines = [f"检测完成：成功 {self._pc_done} / {self._pc_total}"]
        if self._pc_empty:
            lines.append(
                f"\n其中 {len(self._pc_empty)} 个账号没有角色（可用「创建角色」建角）："
                "\n  " + "\n  ".join(self._pc_empty[:10])
            )
        if self._pc_failed:
            lines.append("\n失败：\n  " + "\n  ".join(f"{a}：{e}" for a, e in self._pc_failed[:10]))
            lines.append(
                "\n常见原因：4399 登录被限流（HTTP 202，等几分钟）、密码失效、"
                "或该会话的凭据 已过期（重新登录一次即可）。"
            )
        QtWidgets.QMessageBox.information(self, "检测角色", "\n".join(lines))

    def _create_characters(self) -> None:
        if self.scheduler is None:
            QtWidgets.QMessageBox.warning(self, "创建角色", "面板未就绪（拿不到调度器），无法建角。")
            return

        ids = self._selected_ids()
        if not ids:
            QtWidgets.QMessageBox.information(self, "创建角色", "请先在列表里选中要建角的账号。")
            return

        targets = []
        skipped = []
        for aid in ids:
            acc = self.store.get(aid)
            if acc is None:
                continue
            if acc.has_characters():
                skipped.append(f"{acc.display()}（已有角色）")
                continue
            targets.append(acc)
        if not targets:
            QtWidgets.QMessageBox.information(
                self, "创建角色", "选中的账号都已有角色，无需建角。"
            )
            return

        dlg = CreateCharacterDialog(len(targets), self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        base = dlg.base_name()
        suffix = dlg.with_suffix()

        self._cc_queue = []
        for i, acc in enumerate(targets):
            name = f"{base}{i + 1}" if (suffix and len(targets) > 1) else base
            self._cc_queue.append((acc.id, name))
        self._cc_total = len(self._cc_queue)
        self._cc_done = 0
        self._cc_failed = []
        self._cc_threads = []
        if self.btn_create_char:
            self.btn_create_char.setEnabled(False)
        if skipped and self.lbl_sauth:
            self.lbl_sauth.setText(f"跳过已有角色 {len(skipped)} 个")
        self._cc_next()

    def _cc_next(self) -> None:
        if not self._cc_queue:
            self._cc_finish()
            return
        aid, name = self._cc_queue.pop(0)
        if self.lbl_sauth:
            self.lbl_sauth.setText(f"建角 {self._cc_done + 1}/{self._cc_total} … {name}")
        task = CreateCharTask(self.scheduler, aid, name)
        task.finished.connect(self._on_cc_done)
        t = threading.Thread(target=task.run, daemon=True, name=f"mkchar-{aid[:8]}")
        self._cc_threads.append((task, t))
        t.start()

    def _cc_schedule_next(self) -> None:
        QtCore.QTimer.singleShot(self.CC_GAP_MS, self._cc_next)

    def _on_cc_done(self, aid: str, names, error) -> None:
        acc = self.store.get(aid)
        label = acc.display() if acc else aid[:8]
        if names:
            self._cc_done += 1
            self._cc_schedule_next()
            return
        self._cc_failed.append((label, str(error)[:140]))
        self._cc_schedule_next()

    def _cc_finish(self) -> None:
        if self.btn_create_char:
            self.btn_create_char.setEnabled(True)
        self.reload()
        if self.lbl_sauth:
            tail = f" · 失败 {len(self._cc_failed)}" if self._cc_failed else ""
            self.lbl_sauth.setText(f"建角成功 {self._cc_done}/{self._cc_total}{tail}")
        lines = [f"建角成功 {self._cc_done} / {self._cc_total}"]
        if self._cc_failed:
            lines.append("\n失败：\n  " + "\n  ".join(f"{a}：{e}" for a, e in self._cc_failed[:10]))
            lines.append(
                "\n常见原因：该号登不进去（4399 限流 / 密码失效）、"
                "游戏侧角色名被拒（重名/长度/敏感词）、或实名未过。"
            )
        lines.append("\n建好的角色已回填进账号库，可直接参与挂机调度。")
        QtWidgets.QMessageBox.information(self, "创建角色", "\n".join(lines))

    def _export_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出 CSV", "accounts.csv", "CSV (*.csv)")
        if path:
            self.store.export_csv(Path(path))

    def _export_xlsx(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出 Excel", "accounts.xlsx", "Excel (*.xlsx)")
        if path:
            self.store.export_xlsx(Path(path))


class MainWindow(QtWidgets.QMainWindow):
    status_signal = QtCore.Signal(int, str, str)
    embed_signal = QtCore.Signal(int, int)  # slot_index, hwnd
    log_signal = QtCore.Signal(str, str)  # tag, message

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Box Online · 挂在线")
        self.resize(1380, 860)
        self.setStyleSheet(STYLE)

        self.settings = load_settings()
        self.store = AccountStore()
        self.scheduler = Scheduler(
            self.store,
            self.settings,
            on_status=self._on_status_thread,
            on_pick_char=self._pick_char_blocking,
            on_embed=self._on_embed_thread,
        )
        from .char_loop import CharLoopController

        self.char_loop = CharLoopController(
            self.store,
            self.scheduler,
            self.settings,
            on_log=lambda m: self.log_signal.emit("循环挂角", m),
        )
        self.status_signal.connect(
            self._on_status_ui, QtCore.Qt.ConnectionType.QueuedConnection
        )
        self.embed_signal.connect(self._on_embed_ui)
        self.log_signal.connect(self._append_log_line)
        self._lib_dialog: Optional[AccountLibraryDialog] = None
        self._log_last: dict = {}  # slot -> (state, detail, ts)

        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        root_lay = QtWidgets.QVBoxLayout(root)
        root_lay.setContentsMargins(16, 16, 16, 16)
        root_lay.setSpacing(14)

        hero = QtWidgets.QFrame()
        hero.setObjectName("hero")
        hero.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        hero.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        hero.setAutoFillBackground(False)
        hero_lay = QtWidgets.QVBoxLayout(hero)
        hero_lay.setContentsMargins(18, 16, 18, 14)
        hero_lay.setSpacing(12)

        brand_row = QtWidgets.QHBoxLayout()
        brand_col = QtWidgets.QVBoxLayout()
        brand = QtWidgets.QLabel("AW ONLINE")
        brand.setObjectName("brand")
        brand_sub = QtWidgets.QLabel("示例服 · 多号挂在线控制台")
        brand_sub.setObjectName("brandSub")
        brand_col.addWidget(brand)
        brand_col.addWidget(brand_sub)
        brand_row.addLayout(brand_col, 1)

        self.lbl_bridge = QtWidgets.QLabel("对接层 · 检测中…")
        self.lbl_bridge.setObjectName("muted")
        brand_row.addWidget(self.lbl_bridge, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        hero_lay.addLayout(brand_row)

        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(18)
        self.stat_total = self._make_stat("总槽位", "8")
        self.stat_run = self._make_stat("运行中", "0")
        self.stat_idle = self._make_stat("空闲", "8")
        self.stat_bad = self._make_stat("异常", "0")
        self.stat_acc = self._make_stat("账号库", "0")
        for w in (self.stat_total, self.stat_run, self.stat_idle, self.stat_bad, self.stat_acc):
            stats.addWidget(w)
        stats.addStretch(1)
        hero_lay.addLayout(stats)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        self.spin_slots = QtWidgets.QSpinBox()
        self.spin_slots.setRange(1, int(self.settings.get("max_slots", 32)))
        self.spin_slots.setValue(int(self.settings.get("slot_count", 8)))
        self.spin_slots.valueChanged.connect(self._change_slots)
        btn_login = QtWidgets.QPushButton("登录入库")
        btn_login.setObjectName("primary")
        btn_login.clicked.connect(self._open_login)
        btn_lib = QtWidgets.QPushButton("账号库")
        btn_lib.setObjectName("ghost")
        btn_lib.clicked.connect(self._open_library)

        # 模式切换：手动 | 循环
        self.btn_mode_manual = QtWidgets.QPushButton("手动")
        self.btn_mode_manual.setObjectName("modeBtn")
        self.btn_mode_manual.setCheckable(True)
        self.btn_mode_manual.setToolTip("手动：首次代理 + 追加挂机")
        self.btn_mode_loop = QtWidgets.QPushButton("循环")
        self.btn_mode_loop.setObjectName("modeBtn")
        self.btn_mode_loop.setCheckable(True)
        self.btn_mode_loop.setToolTip("循环：按角色队列自动挂满领奖换角")
        self._mode_group = QtWidgets.QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.btn_mode_manual)
        self._mode_group.addButton(self.btn_mode_loop)
        self.btn_mode_manual.clicked.connect(lambda: self._set_work_mode("manual"))
        self.btn_mode_loop.clicked.connect(lambda: self._set_work_mode("loop"))

        # 挂机引擎：协议 | Forge 客户端
        self.btn_hang_protocol = QtWidgets.QPushButton("协议挂机")
        self.btn_hang_protocol.setObjectName("modeBtn")
        self.btn_hang_protocol.setCheckable(True)
        self.btn_hang_protocol.setToolTip(
            "轻量执行体进服（后续开发）"
        )
        self.btn_hang_client = QtWidgets.QPushButton("Forge客户端")
        self.btn_hang_client.setObjectName("modeBtn")
        self.btn_hang_client.setCheckable(True)
        self.btn_hang_client.setToolTip("挂机端无渲染 Forge + Baritone（更重）")
        self._hang_engine_group = QtWidgets.QButtonGroup(self)
        self._hang_engine_group.setExclusive(True)
        self._hang_engine_group.addButton(self.btn_hang_protocol)
        self._hang_engine_group.addButton(self.btn_hang_client)
        self.btn_hang_protocol.clicked.connect(lambda: self._set_hang_mode("protocol"))
        self.btn_hang_client.clicked.connect(lambda: self._set_hang_mode("client"))

        # 手动模式操作
        self.btn_first_proxy = QtWidgets.QPushButton("首次代理")
        self.btn_first_proxy.setObjectName("primary")
        self.btn_first_proxy.setToolTip("原批量代理：从 SLOT01 重新排号，占用槽会先停再开")
        self.btn_first_proxy.clicked.connect(self._start_all_flow)
        self.btn_append = QtWidgets.QPushButton("追加挂机")
        self.btn_append.setObjectName("primary")
        self.btn_append.setToolTip("只占用空闲槽，不中断已在跑的挂机")
        self.btn_append.clicked.connect(self._append_hang_flow)

        # 循环模式操作
        self.btn_char_loop = QtWidgets.QPushButton("开始循环")
        self.btn_char_loop.setObjectName("primary")
        self.btn_char_loop.setToolTip(
            "循环：设定单次启动数与窗口上限；满分钟领齐 4 阶段后关窗，再接下一个空闲角色"
        )
        self.btn_char_loop.clicked.connect(self._toggle_char_loop)
        self.lbl_loop_hint = QtWidgets.QLabel("")
        self.lbl_loop_hint.setObjectName("muted")

        btn_reward = QtWidgets.QPushButton("在线奖励台")
        btn_reward.setObjectName("primary")
        btn_reward.clicked.connect(self._open_reward_console)
        btn_claim_all = QtWidgets.QPushButton("已开游戏领全部")
        btn_claim_all.setObjectName("primary")
        btn_claim_all.setToolTip("对所有挂机中的游戏一次领取全部阶段+里程碑奖励")
        btn_claim_all.clicked.connect(self._claim_all_running_games)
        btn_stop = QtWidgets.QPushButton("安全停止")
        btn_stop.setObjectName("danger")
        btn_stop.clicked.connect(self._safe_stop)
        btn_exit = QtWidgets.QPushButton("退出程序")
        btn_exit.setObjectName("ghost")
        btn_exit.clicked.connect(self._exit_app)

        # --- 工具栏：按用途分组，组间用竖线分隔 ---
        def _sep() -> QtWidgets.QFrame:
            f = QtWidgets.QFrame()
            f.setObjectName("toolSep")
            f.setFixedWidth(1)
            f.setFixedHeight(22)
            return f

        def _cap(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setObjectName("toolCap")
            return lab

        # 组1：账号入口
        actions.addWidget(_cap("账号"))
        actions.addWidget(btn_login)
        actions.addWidget(btn_lib)
        actions.addWidget(_sep())
        # 组2：调度模式（这组必须看得出选中态）
        actions.addWidget(_cap("调度"))
        actions.addWidget(self.btn_mode_manual)
        actions.addWidget(self.btn_mode_loop)
        actions.addWidget(_sep())
        # 组3：挂机引擎
        actions.addWidget(_cap("引擎"))
        actions.addWidget(self.btn_hang_protocol)
        actions.addWidget(self.btn_hang_client)
        actions.addWidget(_sep())
        # 组4：执行（随模式切换显隐）
        actions.addWidget(_cap("执行"))
        actions.addWidget(self.btn_first_proxy)
        actions.addWidget(self.btn_append)
        actions.addWidget(self.btn_char_loop)
        actions.addWidget(self.lbl_loop_hint)
        actions.addWidget(_sep())
        # 组5：槽位规模
        actions.addWidget(_cap("槽位"))
        actions.addWidget(self.spin_slots)
        actions.addSpacing(6)
        actions.addWidget(_sep())
        actions.addSpacing(4)
        actions.addWidget(btn_reward)
        actions.addWidget(btn_claim_all)
        actions.addStretch(1)
        actions.addWidget(btn_stop)
        actions.addWidget(btn_exit)
        hero_lay.addLayout(actions)
        root_lay.addWidget(hero)

        # 恢复挂机引擎 + 调度模式
        hang0 = str(self.settings.get("hang_mode") or "protocol").lower()
        self._apply_hang_mode_ui(hang0 if hang0 in ("protocol", "client") else "protocol", persist=False)
        mode0 = str(self.settings.get("hang_work_mode") or "manual").lower()
        if mode0 not in ("manual", "loop"):
            mode0 = "manual"
        self._apply_work_mode_ui(mode0, persist=False)

        # keep old labels for refresh helpers
        self.lbl_summary = QtWidgets.QLabel()
        self.lbl_summary.hide()
        self.lbl_accounts = QtWidgets.QLabel()
        self.lbl_accounts.hide()

        slots_wrap = QtWidgets.QFrame()
        slots_wrap.setObjectName("panel")
        slots_wrap.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        slots_wrap.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        slots_wrap.setAutoFillBackground(False)
        slots_outer = QtWidgets.QVBoxLayout(slots_wrap)
        slots_outer.setContentsMargins(0, 0, 0, 0)
        slots_outer.setSpacing(8)
        slots_title = QtWidgets.QLabel("槽位矩阵")
        slots_title.setStyleSheet(
            "color: #d4a574; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        slots_outer.addWidget(slots_title)
        self.slots_box = QtWidgets.QWidget()
        self.slots_layout = QtWidgets.QGridLayout(self.slots_box)
        self.slots_layout.setContentsMargins(0, 0, 0, 0)
        self.slots_layout.setHorizontalSpacing(10)
        self.slots_layout.setVerticalSpacing(10)
        slots_outer.addWidget(self.slots_box, 1)
        self.slot_cards: List[SlotCard] = []

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(slots_wrap, 1)

        log_wrap = QtWidgets.QFrame()
        log_wrap.setObjectName("logPanel")
        log_wrap.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        log_wrap.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        log_wrap.setAutoFillBackground(False)
        log_wrap.setMinimumWidth(340)
        log_wrap.setMaximumWidth(420)
        # 固定宽度：窄屏下槽位区会把日志挤成一条细缝（实测 2048 屏只剩 39px）。
        # 固定住之后日志永远可见，槽位区自己收缩（它的最小宽度很小）。
        log_wrap.setFixedWidth(360)
        log_wrap.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding
        )
        log_lay = QtWidgets.QVBoxLayout(log_wrap)
        log_lay.setContentsMargins(12, 12, 12, 12)
        log_lay.setSpacing(8)
        log_head = QtWidgets.QHBoxLayout()
        log_title = QtWidgets.QLabel("运行日志")
        log_title.setStyleSheet(
            "color: #d4a574; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        btn_clear_log = QtWidgets.QPushButton("清空")
        btn_clear_log.setObjectName("ghost")
        btn_clear_log.clicked.connect(self._clear_log)
        log_head.addWidget(log_title)
        log_head.addStretch(1)
        log_head.addWidget(btn_clear_log)
        log_lay.addLayout(log_head)
        self.event_log = QtWidgets.QPlainTextEdit()
        self.event_log.setObjectName("eventLog")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(2000)
        self.event_log.setPlaceholderText("代理 / 进服 / 进门 / 挂机 / 领奖 过程会显示在这里…")
        log_lay.addWidget(self.event_log, 1)
        body.addWidget(log_wrap, 0)

        root_lay.addLayout(body, 1)
        self._rebuild_slots()
        self._append_log_line("系统", "控制台已启动 · 模式：手动=首次代理/追加 · 循环=角色队列")

        tip = QtWidgets.QLabel(
            "流程：登录入库 → 选「手动/循环」→ 进服挂机"
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        root_lay.addWidget(tip)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_header)
        # 1s 足够刷新计时；过密会拖死 UI（snapshot + 卡片重绘）
        self._timer.start(1000)

        self._char_result: Optional[str] = None
        self._char_lock = QtCore.QMutex()
        self._char_wait = QtCore.QWaitCondition()
        self._refresh_account_count()

    @staticmethod
    def _make_stat(label: str, value: str) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        box.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        box.setAutoFillBackground(False)
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        v = QtWidgets.QLabel(value)
        v.setObjectName("statValue")
        l = QtWidgets.QLabel(label)
        l.setObjectName("statLabel")
        lay.addWidget(v)
        lay.addWidget(l)
        box.value_label = v  # type: ignore[attr-defined]
        return box

    def _open_login(self) -> None:
        dlg = LoginDialog(self.store, self, scheduler=self.scheduler)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._refresh_account_count()
            if self._lib_dialog and self._lib_dialog.isVisible():
                self._lib_dialog.reload()

    def _start_all_flow(self) -> None:
        if self._work_mode() != "manual":
            QtWidgets.QMessageBox.information(
                self, "首次代理", "当前是「循环」模式。请先切换到「手动」再用首次代理。"
            )
            return
        if self.char_loop.running:
            QtWidgets.QMessageBox.information(
                self, "首次代理", "请先停止循环挂角，再使用首次代理。"
            )
            return
        self.store.load()
        accounts = self.store.enabled_accounts()
        try:
            from .procutil import play_client_usernames

            blocked = self.store.account_ids_blocked_by_play(play_client_usernames(timeout_sec=2.0))
            if blocked:
                skipped = [a for a in accounts if a.id in blocked]
                accounts = [a for a in accounts if a.id not in blocked]
                for a in skipped:
                    self._append_log_line("系统", f"跳过游玩端同号/保护：{a.display()}")
        except Exception:
            pass
        if not accounts:
            QtWidgets.QMessageBox.information(
                self,
                "首次代理",
                "没有可挂的账号。\n请检查：启用勾选、游玩保护，以及游玩端是否正用同号。",
            )
            return

        max_cap = int(self.settings.get("max_slots", 32))
        cur_slots = max(1, int(self.spin_slots.value()))

        dlg = WindowCountDialog(
            len(accounts),
            max_cap,
            self,
            title="首次代理",
            ok_text="开始首次代理",
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        assignments: List[tuple] = []

        if dlg.mode() == WindowCountDialog.MODE_AUTO_CHARS:
            # 多账号：勾选要检测的；单账号直接用
            if len(accounts) == 1:
                chosen_accs = accounts
            else:
                pick = AccountPickDialog(accounts, len(accounts), timeout_sec=20, parent=self)
                pick.setWindowTitle("选择要检测角色的账号")
                if pick.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                id_set = set(pick.selected_ids())
                chosen_accs = [a for a in accounts if a.id in id_set] or accounts[:1]

            progress = QtWidgets.QProgressDialog(
                "正在登录并检测角色…", "取消", 0, len(chosen_accs), self
            )
            progress.setWindowTitle("自动检测角色")
            progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            QtWidgets.QApplication.processEvents()

            slot_i = 0
            for ai, acc in enumerate(chosen_accs):
                if progress.wasCanceled():
                    progress.close()
                    return
                progress.setLabelText(f"检测中：{acc.display()}")
                progress.setValue(ai)
                QtWidgets.QApplication.processEvents()
                try:
                    names = self.scheduler.probe_characters(acc.id)
                except Exception as exc:  # pylint: disable=broad-except
                    progress.close()
                    QtWidgets.QMessageBox.warning(
                        self, "自动检测角色", f"{acc.display()}\n检测失败：{exc}"
                    )
                    return
                if not names:
                    progress.close()
                    QtWidgets.QMessageBox.warning(
                        self, "自动检测角色", f"{acc.display()}\n该账号下没有角色"
                    )
                    return
                self._append_log_line(
                    "系统",
                    f"检测到 {acc.display()} 共 {len(names)} 角色：{', '.join(names)}",
                )
                for name in names:
                    if slot_i >= max_cap:
                        break
                    assignments.append((slot_i, acc.id, name))
                    slot_i += 1
                if slot_i >= max_cap:
                    break
            progress.setValue(len(chosen_accs))
            progress.close()
            if not assignments:
                return
            QtWidgets.QMessageBox.information(
                self,
                "自动检测角色",
                f"共检测到 {len(assignments)} 个角色，将开 {len(assignments)} 个代理窗口。",
            )
            # 同号多角：网易通常不能并行在线
            by_acc: Dict[str, int] = {}
            for _, aid, _ in assignments:
                by_acc[aid] = by_acc.get(aid, 0) + 1
            multi = [aid for aid, n in by_acc.items() if n > 1]
            if multi:
                tip_names = []
                for aid in multi:
                    acc = self.store.get(aid)
                    tip_names.append(f"{acc.display() if acc else aid} ×{by_acc[aid]}")
                ret = QtWidgets.QMessageBox.question(
                    self,
                    "同账号多角色",
                    "检测到同一账号下多个角色将同时开窗：\n"
                    + "\n".join(tip_names)
                    + "\n\n网易侧通常一号同时只能进服一条连接，并行会报 login servers 失败。\n"
                    "建议：只挂第一个角色。\n\n选「Yes」= 只开每号第一个角色；「No」= 仍按多窗排队（同号串行，后角需等前角结束）。",
                )
                if ret == QtWidgets.QMessageBox.StandardButton.Yes:
                    seen = set()
                    filtered = []
                    for item in assignments:
                        aid = item[1]
                        if aid in seen:
                            continue
                        seen.add(aid)
                        filtered.append(item)
                    # reindex slots
                    assignments = [(i, a, c) for i, (_, a, c) in enumerate(filtered)]
                    self._append_log_line("系统", f"同号多角已收敛为每号一窗 · 共 {len(assignments)} 窗")
        else:
            windows = min(max(1, dlg.window_count()), max_cap)
            if windows >= len(accounts):
                chosen = [a.id for a in accounts[:windows]]
            else:
                pick = AccountPickDialog(accounts, windows, timeout_sec=20, parent=self)
                if pick.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                chosen = pick.selected_ids()[:windows]
            if not chosen:
                return
            assignments = [(i, aid, "") for i, aid in enumerate(chosen)]

        windows = len(assignments)
        if windows != cur_slots:
            self.spin_slots.blockSignals(True)
            self.spin_slots.setValue(windows)
            self.spin_slots.blockSignals(False)
            self.settings["slot_count"] = windows
            save_settings(self.settings)
            self.scheduler.settings = self.settings
            self._rebuild_slots()

        for i, aid, char in assignments:
            acc = self.store.get(aid)
            if i < len(self.slot_cards) and acc:
                label = acc.display()
                if char:
                    label = f"{label} / {char}"
                self.slot_cards[i].user.setText(label)
                self.slot_cards[i].state.setText("排队中")
        names = []
        for i, aid, char in assignments:
            acc = self.store.get(aid)
            bit = f"SLOT{i + 1:02d}={acc.display() if acc else aid[:8]}"
            if char:
                bit += f"/{char}"
            names.append(bit)
        self._append_log_line("系统", f"首次代理 ×{len(assignments)} · " + " · ".join(names))
        self.scheduler.start_batch(assignments, replace=True)
        self._refresh_header()

    def _grow_slots_to(self, n: int) -> None:
        """只扩容槽位，绝不缩小（避免追加时顶掉已挂机）。"""
        max_cap = int(self.settings.get("max_slots", 32))
        n = max(1, min(max_cap, int(n)))
        cur = max(1, int(self.spin_slots.value()))
        if n <= cur:
            return
        self.spin_slots.blockSignals(True)
        self.spin_slots.setValue(n)
        self.spin_slots.blockSignals(False)
        self.settings["slot_count"] = n
        save_settings(self.settings)
        self.scheduler.settings = self.settings
        self._rebuild_slots()

    def _append_hang_flow(self) -> None:
        """追加挂机：只开空闲槽，不停止已在跑的窗口。"""
        if self._work_mode() != "manual":
            QtWidgets.QMessageBox.information(
                self, "追加挂机", "当前是「循环」模式。请先切换到「手动」再追加。"
            )
            return
        if self.char_loop.running:
            QtWidgets.QMessageBox.information(
                self, "追加挂机", "请先停止循环挂角，再使用追加挂机。"
            )
            return
        self.store.load()
        accounts = self.store.enabled_accounts()
        try:
            from .procutil import play_client_usernames

            blocked = self.store.account_ids_blocked_by_play(play_client_usernames(timeout_sec=2.0))
            if blocked:
                skipped = [a for a in accounts if a.id in blocked]
                accounts = [a for a in accounts if a.id not in blocked]
                for a in skipped:
                    self._append_log_line("系统", f"跳过游玩端同号/保护：{a.display()}")
        except Exception:
            pass
        if not accounts:
            QtWidgets.QMessageBox.information(
                self,
                "追加挂机",
                "没有可挂的账号。\n请检查：启用勾选、游玩保护，以及游玩端是否正用同号。",
            )
            return

        occupied_ids = self.scheduler.occupied_account_ids()
        candidates = [a for a in accounts if a.id not in occupied_ids]
        if not candidates:
            QtWidgets.QMessageBox.information(
                self,
                "追加挂机",
                "已启用账号都在挂机中，没有可追加的新号。\n"
                "若要整批重开，请用「首次代理」。",
            )
            return

        max_cap = int(self.settings.get("max_slots", 32))
        busy_n = sum(1 for i in range(len(self.scheduler.slots)) if self.scheduler.slot_is_busy(i))
        free_now = max_cap - busy_n
        if free_now <= 0:
            QtWidgets.QMessageBox.information(
                self,
                "追加挂机",
                f"槽位已满（占用 {busy_n} / 上限 {max_cap}），无法再追加。\n"
                "可先安全停止部分槽位，或提高 max_slots。",
            )
            return

        dlg = WindowCountDialog(
            len(candidates),
            free_now,
            self,
            title="追加挂机",
            ok_text="开始追加",
            hint=(
                f"可追加账号 {len(candidates)} 个 · 已占用槽 {busy_n} · 还可开 {free_now} 窗（上限 {max_cap}）\n"
                "不会中断已在跑的挂机；空闲不够时会自动扩容槽位。"
            ),
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        raw_assignments: List[tuple] = []

        if dlg.mode() == WindowCountDialog.MODE_AUTO_CHARS:
            if len(candidates) == 1:
                chosen_accs = candidates
            else:
                pick = AccountPickDialog(candidates, len(candidates), timeout_sec=20, parent=self)
                pick.setWindowTitle("选择要追加检测的账号")
                if pick.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                id_set = set(pick.selected_ids())
                chosen_accs = [a for a in candidates if a.id in id_set] or candidates[:1]

            progress = QtWidgets.QProgressDialog(
                "正在登录并检测角色…", "取消", 0, len(chosen_accs), self
            )
            progress.setWindowTitle("追加 · 自动检测角色")
            progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            QtWidgets.QApplication.processEvents()

            occupied_chars = self.scheduler.occupied_account_characters()
            for ai, acc in enumerate(chosen_accs):
                if progress.wasCanceled():
                    progress.close()
                    return
                progress.setLabelText(f"检测中：{acc.display()}")
                progress.setValue(ai)
                QtWidgets.QApplication.processEvents()
                try:
                    names = self.scheduler.probe_characters(acc.id)
                except Exception as exc:  # pylint: disable=broad-except
                    progress.close()
                    QtWidgets.QMessageBox.warning(
                        self, "追加挂机", f"{acc.display()}\n检测失败：{exc}"
                    )
                    return
                if not names:
                    progress.close()
                    QtWidgets.QMessageBox.warning(
                        self, "追加挂机", f"{acc.display()}\n该账号下没有角色"
                    )
                    return
                self._append_log_line(
                    "系统",
                    f"追加检测 {acc.display()} 共 {len(names)} 角色：{', '.join(names)}",
                )
                for name in names:
                    key = (acc.id, name.strip())
                    key_empty = (acc.id, "")
                    if key in occupied_chars or key_empty in occupied_chars:
                        continue
                    if len(raw_assignments) >= free_now:
                        break
                    raw_assignments.append((acc.id, name))
                if len(raw_assignments) >= free_now:
                    break
            progress.setValue(len(chosen_accs))
            progress.close()
            if not raw_assignments:
                QtWidgets.QMessageBox.information(
                    self, "追加挂机", "检测结果均已在挂机中，或没有可追加角色。"
                )
                return
            by_acc: Dict[str, int] = {}
            for aid, _ in raw_assignments:
                by_acc[aid] = by_acc.get(aid, 0) + 1
            multi = [aid for aid, n in by_acc.items() if n > 1]
            if multi:
                tip_names = []
                for aid in multi:
                    acc = self.store.get(aid)
                    tip_names.append(f"{acc.display() if acc else aid} ×{by_acc[aid]}")
                ret = QtWidgets.QMessageBox.question(
                    self,
                    "同账号多角色",
                    "追加时同一账号下多个角色：\n"
                    + "\n".join(tip_names)
                    + "\n\n建议每号只追加一个角色。\n\n选「Yes」= 每号只开第一个；「No」= 仍按多窗排队。",
                )
                if ret == QtWidgets.QMessageBox.StandardButton.Yes:
                    seen = set()
                    filtered = []
                    for aid, ch in raw_assignments:
                        if aid in seen:
                            continue
                        seen.add(aid)
                        filtered.append((aid, ch))
                    raw_assignments = filtered
            QtWidgets.QMessageBox.information(
                self,
                "追加挂机",
                f"将追加 {len(raw_assignments)} 个窗口（不中断已挂机）。",
            )
        else:
            windows = min(max(1, dlg.window_count()), free_now)
            if windows >= len(candidates):
                chosen = [a.id for a in candidates[:windows]]
            else:
                pick = AccountPickDialog(candidates, windows, timeout_sec=20, parent=self)
                pick.setWindowTitle("选择要追加的账号")
                if pick.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                chosen = pick.selected_ids()[:windows]
            if not chosen:
                return
            raw_assignments = [(aid, "") for aid in chosen]

        need = len(raw_assignments)
        if need <= 0:
            return

        free = self.scheduler.free_slot_indices()
        if need > len(free):
            grow_to = len(self.scheduler.slots) + (need - len(free))
            grow_to = min(max_cap, grow_to)
            self._grow_slots_to(grow_to)
            free = self.scheduler.free_slot_indices()
        if need > len(free):
            QtWidgets.QMessageBox.warning(
                self,
                "追加挂机",
                f"空闲槽不足：需要 {need}，当前空闲 {len(free)}。",
            )
            need = len(free)
            raw_assignments = raw_assignments[:need]
        if not raw_assignments:
            return

        assignments = [
            (free[i], aid, char) for i, (aid, char) in enumerate(raw_assignments)
        ]

        for i, aid, char in assignments:
            acc = self.store.get(aid)
            if i < len(self.slot_cards) and acc:
                label = acc.display()
                if char:
                    label = f"{label} / {char}"
                self.slot_cards[i].user.setText(label)
                self.slot_cards[i].state.setText("排队中")
        names = []
        for i, aid, char in assignments:
            acc = self.store.get(aid)
            bit = f"SLOT{i + 1:02d}={acc.display() if acc else aid[:8]}"
            if char:
                bit += f"/{char}"
            names.append(bit)
        self._append_log_line("系统", f"追加挂机 ×{len(assignments)} · " + " · ".join(names))
        self.scheduler.start_batch(assignments, replace=False)
        self._refresh_header()

    def _open_library(self) -> None:
        if self._lib_dialog is None:
            self._lib_dialog = AccountLibraryDialog(self.store, self, scheduler=self.scheduler)
        self._lib_dialog.reload()
        self._lib_dialog.show()
        self._lib_dialog.raise_()
        self._lib_dialog.activateWindow()
        self._refresh_account_count()

    def _refresh_account_count(self) -> None:
        self.store.load()
        n = len(self.store.accounts)
        done = sum(1 for a in self.store.accounts if a.online_done or a.hang_status == "已挂完")
        running = sum(1 for a in self.store.accounts if a.hang_status == "挂机中")
        self.lbl_accounts.setText(f"账号库 {n} · 挂机中 {running} · 已挂完 {done}")
        self.stat_acc.value_label.setText(str(n))  # type: ignore[attr-defined]

    def _rebuild_slots(self) -> None:
        while self.slots_layout.count():
            item = self.slots_layout.takeAt(0)
            w = item.widget()
            if w:
                if hasattr(w, "clear_embed"):
                    try:
                        w.clear_embed()
                    except Exception:
                        pass
                w.deleteLater()
        self.slot_cards = []
        n = int(self.settings.get("slot_count", 8))
        cols = 3 if n <= 6 else 4
        for i in range(n):
            card = SlotCard(i)
            card.btn_retry.clicked.connect(lambda _=False, idx=i: self._retry_slot(idx))
            card.btn_claim.clicked.connect(lambda _=False, idx=i: self._claim_online(idx))
            card.btn_close.clicked.connect(lambda _=False, idx=i: self._close_slot(idx))
            card.btn_verify.clicked.connect(lambda _=False, idx=i: self._open_verify(idx))
            self.slots_layout.addWidget(card, i // cols, i % cols)
            self.slot_cards.append(card)
        # 末行之后留一个可伸展空行，把多余高度吸在底部而不是摊给卡片
        self.slots_layout.setRowStretch((n + cols - 1) // cols, 1)
        self.scheduler.resize(n)

    def _retry_slot(self, index: int) -> None:
        """重试：杀本槽游戏 → 再走登录进服。"""
        label = f"SLOT{index + 1:02d}"
        self._append_log_line(label, "重试：关闭游戏进程后重新进服…")
        if 0 <= index < len(self.slot_cards):
            self.slot_cards[index].btn_retry.setEnabled(False)
            self.slot_cards[index].clear_embed()
        self.scheduler.retry_slot(index)
        QtCore.QTimer.singleShot(0, self._refresh_header)

    def _open_reward_console(self) -> None:
        from .online_console import OnlineRewardConsole

        if getattr(self, "_reward_dlg", None) is None:
            self._reward_dlg = OnlineRewardConsole(
                get_slot_list=self.scheduler.slot_list,
                get_reward=self.scheduler.reward_for,
                poll_rewards=self.scheduler.poll_rewards,
                on_claim=lambda idx, **kw: self.scheduler.request_claim(idx, **kw),
                on_claim_all=lambda: self.scheduler.claim_all_running(everything=True),
                parent=self,
                snapshot_flags=self.scheduler.snapshot_claim_flags,
                verify_claim=self.scheduler.verify_claim_synced,
            )
        self._reward_dlg.show()
        self._reward_dlg.raise_()
        self._reward_dlg.activateWindow()

    def _claim_all_running_games(self) -> None:
        n = self.scheduler.claim_all_running(everything=True)
        if n <= 0:
            QtWidgets.QMessageBox.information(
                self,
                "已开游戏领全部",
                "当前没有挂机中的游戏。\n请先首次代理 / 循环挂角进服后再领。",
            )
            return
        self._append_log_line("系统", f"已开游戏领全部 · 已向 {n} 个挂机槽发送全部领取")
        QtWidgets.QMessageBox.information(
            self,
            "已开游戏领全部",
            f"已向 {n} 个挂机中的游戏发送「领取全部奖励」\n（每槽 4 阶段 + 4 里程碑，共 8 键）",
        )

    def _claim_online(self, index: int) -> None:
        ok = self.scheduler.request_claim(index, claim_everything=True)
        if not ok:
            QtWidgets.QMessageBox.information(
                self,
                "领取在线",
                "当前槽位未在挂机中，无法领取。\n请先启动并进服成功后再点「领取在线」。\n"
                "也可打开顶部「在线奖励台」查看阶段进度。",
            )
            return
        with self.scheduler._lock:  # noqa: SLF001
            if 0 <= index < len(self.scheduler.slots):
                s = self.scheduler.slots[index]
                s.detail = "已请求领取在线…"
        self._append_log_line(f"SLOT{index + 1:02d}", "已发送阶段/里程碑领取指令")

    def _change_slots(self, value: int) -> None:
        self.settings["slot_count"] = int(value)
        save_settings(self.settings)
        self.scheduler.settings = self.settings
        self._rebuild_slots()

    def _clear_log(self) -> None:
        self.event_log.clear()
        self._log_last.clear()
        self._append_log_line("系统", "日志已清空")

    def _append_log_line(self, tag: str, message: str) -> None:
        if not hasattr(self, "event_log") or self.event_log is None:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{tag}] {message}"
        self.event_log.appendPlainText(line)
        sb = self.event_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _work_mode(self) -> str:
        m = str(self.settings.get("hang_work_mode") or "manual").lower()
        return m if m in ("manual", "loop") else "manual"

    def _set_hang_mode(self, mode: str) -> None:
        mode = "client" if mode == "client" else "protocol"
        self._apply_hang_mode_ui(mode, persist=True)
        self.scheduler.settings = self.settings
        label = "Forge 客户端" if mode == "client" else "协议挂机"
        self._append_log_line("系统", f"挂机引擎：{label}")

    def _apply_hang_mode_ui(self, mode: str, *, persist: bool = True) -> None:
        mode = "client" if mode == "client" else "protocol"
        if persist:
            self.settings["hang_mode"] = mode
            save_settings(self.settings)
        self.btn_hang_protocol.blockSignals(True)
        self.btn_hang_client.blockSignals(True)
        self.btn_hang_protocol.setChecked(mode == "protocol")
        self.btn_hang_client.setChecked(mode == "client")
        self.btn_hang_protocol.blockSignals(False)
        self.btn_hang_client.blockSignals(False)

    def _set_work_mode(self, mode: str) -> None:
        mode = "loop" if mode == "loop" else "manual"
        if mode == "manual" and self.char_loop.running:
            ret = QtWidgets.QMessageBox.question(
                self,
                "切换到手动",
                "循环挂角正在运行。切换到手动将停止自动补位/换角（不杀已挂机窗口）。\n是否继续？",
            )
            if ret != QtWidgets.QMessageBox.StandardButton.Yes:
                self._apply_work_mode_ui("loop", persist=False)
                return
            self.char_loop.stop()
            self._sync_loop_button()
            self._append_log_line("系统", "已停循环 · 切换到手动模式")
        self._apply_work_mode_ui(mode, persist=True)
        self._append_log_line("系统", f"调度模式：{'循环' if mode == 'loop' else '手动'}")

    def _apply_work_mode_ui(self, mode: str, *, persist: bool = True) -> None:
        mode = "loop" if mode == "loop" else "manual"
        if persist:
            self.settings["hang_work_mode"] = mode
            save_settings(self.settings)
        self.btn_mode_manual.blockSignals(True)
        self.btn_mode_loop.blockSignals(True)
        self.btn_mode_manual.setChecked(mode == "manual")
        self.btn_mode_loop.setChecked(mode == "loop")
        self.btn_mode_manual.blockSignals(False)
        self.btn_mode_loop.blockSignals(False)
        # 手动：首次代理 + 追加；循环：开始/停止循环
        self.btn_first_proxy.setVisible(mode == "manual")
        self.btn_append.setVisible(mode == "manual")
        self.btn_char_loop.setVisible(mode == "loop")
        self.lbl_loop_hint.setVisible(mode == "loop")
        self._sync_loop_button()
        if mode == "loop":
            self._refresh_loop_hint(running=self.char_loop.running)
        else:
            self.lbl_loop_hint.setText("")

    def _refresh_loop_hint(self, *, running: bool = False) -> None:
        mins = int(self.settings.get("char_loop_min_online_min") or 123)
        batch = int(self.settings.get("char_loop_launch_batch") or 2)
        max_w = int(self.settings.get("char_loop_max_windows") or 4)
        n = len(self.store.enabled_characters(include_done=False))
        tail = " · 运行中" if running else ""
        self.lbl_loop_hint.setText(
            f"门槛 {mins} 分 · 单次 {batch} · 窗口≤{max_w} · 待挂 {n} 角{tail}"
        )

    def _sync_loop_button(self) -> None:
        if self.char_loop.running:
            self.btn_char_loop.setText("停止循环")
        else:
            self.btn_char_loop.setText("开始循环")

    def _toggle_char_loop(self) -> None:
        if self._work_mode() != "loop":
            self._set_work_mode("loop")
        if self.char_loop.running:
            self.char_loop.stop()
            self._sync_loop_button()
            self._refresh_loop_hint(running=False)
            self._append_log_line("系统", "已停止循环挂角")
            return
        self.store.load()
        play_names: set = set()
        play_hint = ""
        try:
            from .procutil import detect_play_clients

            plays = detect_play_clients(timeout_sec=2.5)
            play_names = {str(p.get("username") or "").strip() for p in plays if p.get("username")}
            if plays:
                users = "、".join(sorted(play_names) or ["(未知角色)"])
                play_hint = f"检测到游玩端在线：{users}（同账号已自动跳过挂机）\n"
        except Exception:
            play_names = set()
        blocked = self.store.account_ids_blocked_by_play(play_names)
        queue = self.store.enabled_characters_safe(
            include_done=False, play_usernames=play_names
        )
        if not queue:
            msg = "没有可挂的角色。\n请先在账号库：勾选账号启用，并在角色列表勾选要挂的角色。"
            if blocked:
                msg += "\n\n若角色都在「游玩保护」或正在游玩端同号上，请先关掉保护或换号挂。"
            QtWidgets.QMessageBox.information(self, "循环挂角", msg)
            return
        mins = int(self.settings.get("char_loop_min_online_min") or 123)
        batch0 = int(self.settings.get("char_loop_launch_batch") or 2)
        max_w0 = int(self.settings.get("char_loop_max_windows") or 4)
        max_cap = int(self.settings.get("max_slots", 32))
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("循环挂角")
        dlg.setModal(True)
        dlg.setStyleSheet(STYLE)
        dlg.resize(460, 320)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 16)
        title = QtWidgets.QLabel("按角色队列循环挂机")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #f0d2b0;")
        tip = QtWidgets.QLabel(
            f"{play_hint}"
            f"队列待挂 {len(queue)} 个角色（已跳过游玩保护/游玩端同号）\n"
            "挂机只用「示例服-挂机端」，不会改游玩端文件。\n"
            "单次启动若干号；同时窗口不超过上限。\n"
            "满在线分钟 → 领齐 4 阶段 → 关窗 → 自动接下一个空闲角色。"
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(tip)
        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        spin_min = QtWidgets.QSpinBox()
        spin_min.setRange(30, 24 * 60)
        spin_min.setValue(max(30, mins))
        spin_batch = QtWidgets.QSpinBox()
        spin_batch.setRange(1, max_cap)
        spin_batch.setValue(max(1, min(max_cap, batch0)))
        spin_batch.setToolTip("每次补位最多新开几个号（不会超过窗口上限）")
        spin_max = QtWidgets.QSpinBox()
        spin_max.setRange(1, max_cap)
        spin_max.setValue(max(1, min(max_cap, max_w0)))
        spin_max.setToolTip("一个循环同时最多开几个挂机窗口")

        def _sync_batch_cap(_v: int = 0) -> None:
            cap = int(spin_max.value())
            spin_batch.setMaximum(cap)
            if int(spin_batch.value()) > cap:
                spin_batch.setValue(cap)

        spin_max.valueChanged.connect(_sync_batch_cap)
        _sync_batch_cap()
        form.addRow("在线满（分钟）", spin_min)
        form.addRow("单次启动（个）", spin_batch)
        form.addRow("窗口上限（个）", spin_max)
        lay.addLayout(form)
        preview = QtWidgets.QLabel(
            " · ".join(f"{a.display()}/{c}" for a, c in queue[:6])
            + (" …" if len(queue) > 6 else "")
        )
        preview.setObjectName("muted")
        preview.setWordWrap(True)
        lay.addWidget(preview)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("开始循环")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        max_w = int(spin_max.value())
        batch = max(1, min(max_w, int(spin_batch.value())))
        self.settings["char_loop_min_online_min"] = int(spin_min.value())
        self.settings["char_loop_launch_batch"] = batch
        self.settings["char_loop_max_windows"] = max_w
        if int(self.settings.get("slot_count") or 0) < max_w:
            self.settings["slot_count"] = max_w
            self.spin_slots.blockSignals(True)
            self.spin_slots.setValue(max_w)
            self.spin_slots.blockSignals(False)
            self.scheduler.resize(max_w)
        save_settings(self.settings)
        self.scheduler.settings = self.settings
        self.char_loop.settings = self.settings
        self.char_loop.start()
        self._sync_loop_button()
        self._refresh_loop_hint(running=True)
        self._append_log_line(
            "系统",
            f"循环挂角启动 · 队列 {len(queue)} · 门槛 {int(spin_min.value())} 分"
            f" · 单次 {batch} · 窗口≤{max_w}",
        )

    def _log_slot_event(self, index: int, state: str, detail: str) -> None:
        detail = (detail or "").strip()
        # 抑制「进门中 y=」刷屏：同槽同位同文案 3 秒内只记一次
        key = index
        now = datetime.now().timestamp()
        prev = self._log_last.get(key)
        # 挂机中几乎不刷日志，避免采样/状态刷新卡界面
        if state == "挂机中":
            if prev and prev[0] == state and (now - prev[2]) < 30.0:
                return
        noisy = (
            detail.startswith("进门中 y=")
            or detail.startswith("前进进门")
            or detail.startswith("续走确认")
            or detail.startswith("寻路中")
            or "实时" in detail
        )
        if prev:
            p_state, p_detail, p_ts = prev
            if p_state == state and p_detail == detail and (now - p_ts) < (3.0 if noisy else 0.8):
                return
            if noisy and p_state == state and (now - p_ts) < 4.0:
                return
        self._log_last[key] = (state, detail, now)

        snap = None
        try:
            snap = self.scheduler.snapshot()[index] if index < len(self.scheduler.slots) else None
        except Exception:
            snap = None
        proxy = (snap or {}).get("proxy") or ""
        acc_id = (snap or {}).get("account_id") or ""
        acc = self.store.get(acc_id) if acc_id else None
        who = acc.display() if acc else (snap or {}).get("character") or f"槽{index + 1}"
        parts = [state]
        if detail:
            parts.append(detail)
        if proxy and ("代理" in detail or state in ("进服中", "进门中", "挂机中", "异常")):
            if proxy not in detail:
                parts.append(f"proxy={proxy}")
        self._append_log_line(f"SLOT{index + 1:02d}", " · ".join(parts) + f" · {who}")

    def _on_status_thread(self, index: int, state: SlotState, detail: str) -> None:
        self.status_signal.emit(index, state.value, detail)

    def _on_status_ui(self, index: int, state: str, detail: str) -> None:
        self._log_slot_event(index, state, detail)
        if state in ("空闲", "异常", "需验证", "停止中") and 0 <= index < len(self.slot_cards):
            self.slot_cards[index].clear_embed()
        self._refresh_header()

    def _on_embed_thread(self, index: int, hwnd: int) -> None:
        self.embed_signal.emit(int(index), int(hwnd))

    def _on_embed_ui(self, index: int, hwnd: int) -> None:
        if index < 0 or index >= len(self.slot_cards):
            return
        card = self.slot_cards[index]
        ok = card.embed_foreign(int(hwnd))
        self._append_log_line(
            f"SLOT{index + 1:02d}",
            f"{'已嵌入游戏窗口' if ok else '嵌入失败'} hwnd={hwnd}",
        )
        if ok:
            # 稍后再 fit 一次，等布局稳定
            QtCore.QTimer.singleShot(200, card.fit_embed)
            QtCore.QTimer.singleShot(1000, card.fit_embed)

    def _refresh_header(self) -> None:
        if not hasattr(self, "_bridge_tick"):
            self._bridge_tick = 0
        self._bridge_tick += 1
        # 奖励扫盘：约 5s 一次；对接层检测约 8s（勿跟 UI 同频）
        if self._bridge_tick % 5 == 0:
            try:
                self.scheduler.poll_rewards(force=False)
            except Exception:
                pass
        snap = self.scheduler.snapshot()
        c = self.scheduler.counts()
        summary = f"总槽位 {c['total']} · 运行中 {c['running']} · 空闲 {c['idle']} · 异常 {c['bad']}"
        if self.lbl_summary.text() != summary:
            self.lbl_summary.setText(summary)
        for key, val in (
            ("stat_total", str(c["total"])),
            ("stat_run", str(c["running"])),
            ("stat_idle", str(c["idle"])),
            ("stat_bad", str(c["bad"])),
        ):
            lab = getattr(self, key).value_label  # type: ignore[attr-defined]
            if lab.text() != val:
                lab.setText(val)
        for card in self.slot_cards:
            if card.index >= len(snap):
                continue
            data = snap[card.index]
            acc = self.store.get(data.get("account_id") or "")
            label = acc.display() if acc else (data.get("character") or "未分配")
            if data.get("character"):
                label = f"{label} / {data['character']}"
            card.apply(data, label)
        if self._bridge_tick % 8 == 1:
            self._refresh_bridge()
            self._refresh_account_count()

    def _refresh_bridge(self) -> None:
        try:
            b = load_bridge_client()
            ok = b.health()
            tok = "token✓" if b.token else "无token"
            self.lbl_bridge.setText(f"{'● 对接层在线' if ok else '○ 对接层离线'}  {b.host}:{b.port}  {tok}")
            self.lbl_bridge.setStyleSheet("color: #7dceb0;" if ok else "color: #ff9aa3;")
        except Exception as exc:
            self.lbl_bridge.setText(f"对接层检测失败 ({exc})")
            self.lbl_bridge.setStyleSheet("color: #ff9aa3;")

    def _open_verify(self, index: int) -> None:
        snap = self.scheduler.snapshot()
        if index >= len(snap):
            return
        url = snap[index].get("verify_url") or ""
        if url:
            webbrowser.open(url)

    def _close_slot(self, index: int) -> None:
        """关闭单个槽位的挂机窗口（后台杀进程，不堵 UI）。"""
        import threading

        label = f"SLOT{index + 1:02d}"
        self._append_log_line(label, "关闭本槽挂机窗口…")
        if 0 <= index < len(self.slot_cards):
            self.slot_cards[index].btn_close.setEnabled(False)
            self.slot_cards[index].clear_embed()

        def _work() -> None:
            try:
                n = self.scheduler.stop_slot(index, scan=True)
                self.log_signal.emit(label, f"已关闭（结束进程 {n}）")
            except Exception as exc:
                self.log_signal.emit(label, f"关闭失败: {exc}")

        threading.Thread(target=_work, name=f"ui-close-{index}", daemon=True).start()
        QtCore.QTimer.singleShot(0, self._refresh_header)

    def _safe_stop(self) -> None:
        """停止所有槽位，并只结束挂机 Forge 进程（不碰游玩端）。"""
        if self.char_loop.running:
            self.char_loop.stop()
            self._sync_loop_button()
            if self._work_mode() == "loop":
                self._refresh_loop_hint(running=False)
        self._append_log_line("系统", "安全停止：结束全部挂机端（保留游玩端）…")

        import threading

        def _work() -> None:
            try:
                # 安全停止必须扫杀全部挂机端
                n = self.scheduler.stop_all(scan=True)
                self.log_signal.emit("系统", f"安全停止完成 · 已结束挂机进程约 {n} 个")
            except Exception as exc:
                self.log_signal.emit("系统", f"安全停止异常: {exc}")

        threading.Thread(target=_work, name="ui-safe-stop", daemon=True).start()
        self._refresh_header()
        QtCore.QTimer.singleShot(1500, self._refresh_header)

    def _exit_app(self) -> None:
        """退出面板：立刻藏窗，后台关掉全部挂机端（绝不碰游玩端）。"""
        if getattr(self, "_exiting", False):
            return
        self._exiting = True
        try:
            self.char_loop.stop()
        except Exception:
            pass
        self._append_log_line("系统", "退出程序：正在关闭全部挂机端（保留游玩端）…")
        self.hide()
        QtWidgets.QApplication.processEvents()
        self._shutdown_then_quit()

    def _shutdown_then_quit(self) -> None:
        """Kill hang clients off the UI thread; quit only after cleanup (or deadline)."""
        import threading

        done = {"v": False}

        def _work() -> None:
            n = 0
            try:
                self.char_loop.stop()
            except Exception:
                pass
            try:
                n = int(self.scheduler.shutdown() or 0)
            except Exception:
                pass
            done["v"] = True
            try:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_quit_now",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                )
            except Exception:
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    app.quit()
            _ = n

        # 非 daemon：尽量把杀进程跑完再随进程退出
        threading.Thread(target=_work, name="aw-exit-shutdown", daemon=False).start()
        # 兜底：最长等约 12s，避免卡死不退
        QtCore.QTimer.singleShot(12000, self._quit_now)

    @QtCore.Slot()
    def _quit_now(self) -> None:
        if getattr(self, "_quit_done", False):
            return
        self._quit_done = True
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        # 点窗口 X 等同退出：关挂机端，不关游玩端
        if getattr(self, "_exiting", False):
            event.accept()
            return
        self._exiting = True
        try:
            self.char_loop.stop()
        except Exception:
            pass
        event.accept()
        self.hide()
        QtWidgets.QApplication.processEvents()
        self._shutdown_then_quit()

    def _pick_char_blocking(self, index: int, acc: Account, names: list) -> Optional[str]:
        self._char_lock.lock()
        self._char_result = None
        QtCore.QMetaObject.invokeMethod(
            self,
            "_show_char_dialog",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, acc.display()),
            QtCore.Q_ARG(str, "\n".join(names)),
            QtCore.Q_ARG(str, acc.character or ""),
        )
        self._char_wait.wait(self._char_lock, 120000)
        result = self._char_result
        self._char_lock.unlock()
        if result:
            return result
        return names[0] if names else None

    @QtCore.Slot(str, str, str)
    def _show_char_dialog(self, label: str, names_joined: str, preselect: str) -> None:
        names = [n for n in names_joined.split("\n") if n]
        dlg = CharPickDialog(label, names, preselect, self)
        picked = None
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            picked = dlg.selected()
        elif names:
            picked = names[0]
        self._char_lock.lock()
        self._char_result = picked
        self._char_wait.wakeAll()
        self._char_lock.unlock()


def run_app() -> None:
    import sys

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Box Online")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
