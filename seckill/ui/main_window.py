"""PySide6 界面层。

全部手写，界面结构与业务逻辑解耦，加字段直接改这里。

线程安全：核心层通过事件总线推消息，这里用一个 QObject 桥接成 Qt 信号，
信号跨线程自动排队，所有控件操作都发生在主线程。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from PySide6.QtCore import QDateTime, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..adapters.base import BaseAdapter
from ..adapters.registry import detect
from ..core.clock import Clock
from ..core.events import Event, EventBus, EventKind
from ..core.models import SeckillTask, TaskStatus
from ..core.scheduler import SeckillScheduler
from ..core.session import SessionManager
from ..utils.logger import current_log_file, get_logger
from ..utils.paths import asset

logger = get_logger(__name__)

_LEVEL_COLORS = {
    "debug": "#6b7280",
    "info": "#111827",
    "warning": "#b45309",
    "error": "#b91c1c",
    "success": "#047857",
}


class BusBridge(QObject):
    """把任意线程的事件转成 Qt 信号。"""

    eventReceived = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._unsub = bus.subscribe(self._forward)

    def _forward(self, event: Event) -> None:
        # 在抢单线程里 emit，Qt 会以队列方式交给主线程处理
        self.eventReceived.emit(event)

    def dispose(self) -> None:
        if self._unsub:
            self._unsub()


class MainWindow(QMainWindow):
    def __init__(
        self,
        adapters: dict[str, BaseAdapter],
        bus: EventBus,
        clock: Clock,
    ) -> None:
        super().__init__()
        self.adapters = adapters
        self.bus = bus
        self.clock = clock
        self.sessions: dict[str, SessionManager] = {
            pid: SessionManager(pid) for pid in adapters
        }
        self.scheduler: SeckillScheduler | None = None
        self._platform = ""

        self.setWindowTitle("SecKill 2.0")
        self.setWindowIcon(QIcon(asset("icons", "icon.png")))
        self.resize(980, 640)

        self._build_ui()
        self.bridge = BusBridge(bus, self)
        self.bridge.eventReceived.connect(self._on_event)

        self._sync_clock_async()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_countdown)
        self._tick.start(1000)

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar(self))

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # --- 平台 ---
        plat_box = QGroupBox("1. 选择平台（粘贴链接会自动识别）")
        plat_layout = QHBoxLayout(plat_box)
        self.platform_buttons: dict[str, QToolButton] = {}
        for pid, adapter in self.adapters.items():
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIcon(QIcon(asset(adapter.icon or f"icons/{pid}.png")))
            btn.setIconSize(QSize(72, 36))
            btn.setText(adapter.name)
            btn.setMinimumSize(120, 84)
            btn.clicked.connect(lambda _=False, p=pid: self._pick_platform(p))
            plat_layout.addWidget(btn)
            self.platform_buttons[pid] = btn
        plat_layout.addStretch(1)
        layout.addWidget(plat_box)

        # --- 商品 ---
        item_box = QGroupBox("2. 商品信息")
        form = QFormLayout(item_box)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴商品链接，例如 https://item.jd.com/100014219124.html")
        self.url_edit.textChanged.connect(self._auto_detect)
        form.addRow("商品链接", self.url_edit)

        self.sku_label = QLabel("—")
        self.sku_label.setStyleSheet("color:#6b7280")
        form.addRow("识别结果", self.sku_label)

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 99)
        self.qty_spin.setValue(1)
        form.addRow("购买数量", self.qty_spin)
        layout.addWidget(item_box)

        # --- 时间 ---
        time_box = QGroupBox("3. 抢购时机")
        time_layout = QVBoxLayout(time_box)
        self.schedule_check = QCheckBox("定时抢购（不选则立即开始）")
        self.schedule_check.toggled.connect(self._on_schedule_toggled)
        time_layout.addWidget(self.schedule_check)

        self.datetime_edit = QDateTimeEdit()
        self.datetime_edit.setCalendarPopup(True)
        self.datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.datetime_edit.setDateTime(QDateTime.currentDateTime().addSecs(300))
        self.datetime_edit.setEnabled(False)
        time_layout.addWidget(self.datetime_edit)
        layout.addWidget(time_box)

        # --- 高级 ---
        adv_box = QGroupBox("4. 策略")
        adv = QGridLayout(adv_box)
        self.attempts_spin = QSpinBox()
        self.attempts_spin.setRange(1, 500)
        self.attempts_spin.setValue(60)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 3600)
        self.timeout_spin.setValue(180)
        self.timeout_spin.setSuffix(" 秒")
        self.prefer_api = QCheckBox("优先接口直调（需先抓包配置）")
        self.prefer_api.setChecked(True)
        self.headless_check = QCheckBox("无头模式（不推荐，风控更严）")
        adv.addWidget(QLabel("最大尝试次数"), 0, 0)
        adv.addWidget(self.attempts_spin, 0, 1)
        adv.addWidget(QLabel("开抢后时限"), 1, 0)
        adv.addWidget(self.timeout_spin, 1, 1)
        adv.addWidget(self.prefer_api, 2, 0, 1, 2)
        adv.addWidget(self.headless_check, 3, 0, 1, 2)
        layout.addWidget(adv_box)

        # --- 按钮 ---
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始抢单")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        session_row = QHBoxLayout()
        login_btn = QPushButton("重新扫码登录")
        login_btn.clicked.connect(self._clear_session)
        self.session_label = QLabel("登录态：未知")
        self.session_label.setStyleSheet("color:#6b7280")
        session_row.addWidget(login_btn)
        session_row.addWidget(self.session_label)
        session_row.addStretch(1)
        layout.addLayout(session_row)

        layout.addStretch(1)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 16, 16, 16)
        layout.setSpacing(10)

        status_card = QFrame()
        status_card.setFrameShape(QFrame.StyledPanel)
        status_layout = QVBoxLayout(status_card)
        self.phase_label = QLabel("待命")
        self.phase_label.setStyleSheet("font-size:15px;font-weight:500")
        self.countdown_label = QLabel("—")
        self.countdown_label.setStyleSheet("font-size:34px;font-weight:500;color:#185FA5")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.clock_label = QLabel("正在校时…")
        self.clock_label.setStyleSheet("color:#6b7280;font-size:12px")
        status_layout.addWidget(self.phase_label)
        status_layout.addWidget(self.countdown_label)
        status_layout.addWidget(self.clock_label)
        layout.addWidget(status_card)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("运行日志"))
        log_header.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(clear_btn)
        layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        font = QFont("Consolas" if _is_windows() else "Menlo", 10)
        self.log_view.setFont(font)
        layout.addWidget(self.log_view, 1)

        logfile = current_log_file()
        tip = QLabel(f"完整日志：{logfile}" if logfile else "")
        tip.setStyleSheet("color:#9ca3af;font-size:11px")
        tip.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(tip)
        return panel

    # ------------------------------------------------------------------ 交互

    def _pick_platform(self, pid: str) -> None:
        self._platform = pid
        for other, btn in self.platform_buttons.items():
            btn.setChecked(other == pid)
        self._refresh_session_label()

    def _auto_detect(self, text: str) -> None:
        pid = detect(self.adapters, text)
        if pid:
            self._pick_platform(pid)
            sku = self.adapters[pid].extract_sku(text)
            self.sku_label.setText(f"{self.adapters[pid].name}｜SKU {sku or '未识别'}")
        else:
            self.sku_label.setText("未识别平台")

    def _on_schedule_toggled(self, checked: bool) -> None:
        self.datetime_edit.setEnabled(checked)

    def _clear_session(self) -> None:
        if not self._platform:
            QMessageBox.information(self, "提示", "请先选择平台")
            return
        self.sessions[self._platform].clear()
        self._refresh_session_label()
        self._append_log("登录态已清除，下次抢单会要求重新扫码", "warning")

    def _refresh_session_label(self) -> None:
        if not self._platform:
            self.session_label.setText("登录态：未知")
            return
        mgr = self.sessions[self._platform]
        last = mgr.last_login()
        if last:
            local = last.astimezone()
            self.session_label.setText(f"登录态：{local:%m-%d %H:%M} 已登录")
        else:
            self.session_label.setText("登录态：未登录（首次抢单需扫码）")

    # ------------------------------------------------------------------ 任务

    def _build_task(self) -> SeckillTask | None:
        if not self._platform:
            QMessageBox.warning(self, "缺少信息", "请选择或粘贴一个平台链接")
            return None
        target: datetime | None = None
        if self.schedule_check.isChecked():
            naive = self.datetime_edit.dateTime().toPython()
            target = naive.astimezone()  # 附本地时区，变成 aware datetime
        task = SeckillTask(
            platform=self._platform,
            url=self.url_edit.text().strip(),
            target_time=target,
            quantity=self.qty_spin.value(),
            max_attempts=self.attempts_spin.value(),
            timeout_seconds=self.timeout_spin.value(),
            prefer_api=self.prefer_api.isChecked(),
            headless=self.headless_check.isChecked(),
        )
        errors = task.validate()
        if errors:
            QMessageBox.warning(self, "参数有误", "\n".join(errors))
            return None
        return task

    def _start(self) -> None:
        task = self._build_task()
        if task is None:
            return
        adapter = self.adapters[task.platform]
        self.scheduler = SeckillScheduler(
            task=task,
            adapter=adapter,
            session=self.sessions[task.platform],
            bus=self.bus,
            clock=self.clock,
        )
        self.scheduler.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._append_log("任务已启动", "info")

    def _stop(self) -> None:
        if self.scheduler and self.scheduler.is_alive():
            self.scheduler.stop()
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止中…")
        else:
            self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("停止")

    # ------------------------------------------------------------------ 事件

    def _on_event(self, event: Event) -> None:
        """运行在主线程，可以安全地操作控件。"""
        if event.kind == EventKind.LOG:
            self._append_log(event.message, event.level)
        elif event.kind == EventKind.PHASE:
            self.phase_label.setText(event.message)
            status = event.data.get("status")
            if status in (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                self._reset_buttons()
                self.countdown_label.setText("—")
            self._append_log(f"== {event.message} ==", "info")
        elif event.kind == EventKind.COUNTDOWN:
            self._last_remain = float(event.data.get("seconds", 0))
            self._refresh_countdown()
        elif event.kind == EventKind.CLOCK:
            self.clock_label.setText(event.message)
        elif event.kind == EventKind.SESSION:
            self._append_log(event.message, event.level)
            self._refresh_session_label()
        elif event.kind == EventKind.SUCCESS:
            self._append_log(event.message, "success")
            QMessageBox.information(self, "抢购成功", event.message)
        elif event.kind == EventKind.FAILURE:
            self._append_log(event.message, "error")
        elif event.kind == EventKind.ERROR:
            self._append_log(event.message, "error")
            self._reset_buttons()
        elif event.kind == EventKind.CANCELLED:
            self._append_log(event.message, "warning")
            self._reset_buttons()
        elif event.kind == EventKind.QRCODE:
            self._append_log(event.message, "warning")
            self.statusBar().showMessage(event.message, 0)

    def _refresh_countdown(self) -> None:
        remain = getattr(self, "_last_remain", None)
        if remain is None:
            return
        if remain <= 0:
            self.countdown_label.setText("开抢！")
            return
        if remain >= 60:
            self.countdown_label.setText(f"T-{int(remain)}s")
        else:
            self.countdown_label.setText(f"T-{remain:06.3f}s")

    def _append_log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        color = _LEVEL_COLORS.get(level, "#111827")
        html = (
            f'<span style="color:#9ca3af">{ts}</span> '
            f'<span style="color:{color}">{_escape(message)}</span>'
        )
        self.log_view.appendHtml(html)
        self.log_view.moveCursor(QTextCursor.End)

    # ------------------------------------------------------------------ 校时

    def _sync_clock_async(self) -> None:
        def work() -> None:
            result = self.clock.sync()
            if result.ok:
                self.bus.emit(EventKind.CLOCK, str(result), "success")
            else:
                self.bus.emit(
                    EventKind.CLOCK,
                    f"校时失败，将使用本机时间（{result.message}）",
                    "warning",
                )

        threading.Thread(target=work, daemon=True, name="clock-sync").start()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 的命名约定
        if self.scheduler and self.scheduler.is_alive():
            self.scheduler.stop()
            self.scheduler.join(timeout=5)
        self.bridge.dispose()
        super().closeEvent(event)


def _is_windows() -> bool:
    import os

    return os.name == "nt"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
