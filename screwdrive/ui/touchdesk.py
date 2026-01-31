#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TouchDesk - PyQt5 Touch Interface for Screw Drive Control System.

Fullscreen touch interface for Raspberry Pi with 3 tabs:
- Work: Main control with START/STOP buttons
- Start: Device selection and program launch
- Service: Sensors, relays, serial terminal
"""

import os
import sys
import time
import json
import socket
import html
from pathlib import Path
from functools import partial

# EGLFS setup for headless Pi
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "eglfs")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal as Signal
from PyQt5.QtGui import QFont, QPixmap, QCursor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QLabel, QPushButton, QFrame, QComboBox, QLineEdit,
    QTextEdit, QSpinBox, QSizePolicy, QScrollArea
)

import requests

# Configuration
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000/api")
POLL_MS = 1000
BORDER_W = 10
EVENTS_LOG_PATH = Path("/tmp/screw_events.jsonl")
UI_STATUS_PATH = Path("/tmp/ui_status.json")


def get_local_ip() -> str:
    """Get local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "Unknown"


def req_get(path: str):
    """HTTP GET request."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    r = requests.get(url, timeout=3)
    r.raise_for_status()
    return r.json()


def req_post(path: str, payload=None):
    """HTTP POST request."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    r = requests.post(url, json=payload or {}, timeout=10)
    r.raise_for_status()
    return r.json()


class ApiClient:
    """API client for communicating with screwdrive backend."""

    def status(self):
        return req_get("status")

    def health(self):
        return req_get("health")

    def devices(self):
        return req_get("devices")

    def relay_set(self, name: str, state: str):
        return req_post(f"relays/{name}", {"state": state})

    def relay_all_off(self):
        return req_post("relays/all/off")

    def cycle_start(self, device_key: str):
        return req_post("cycle/start", {"device": device_key})

    def cycle_stop(self):
        return req_post("cycle/stop")

    def cycle_pause(self):
        return req_post("cycle/pause")

    def cycle_estop(self):
        return req_post("cycle/estop")

    def cycle_clear_estop(self):
        return req_post("cycle/clear_estop")

    def xy_home(self):
        return req_post("xy/home")

    def xy_move(self, x: float, y: float, feed: float = 10000):
        return req_post("xy/move", {"x": x, "y": y, "feed": feed})

    def xy_jog(self, dx: float, dy: float, feed: float = 1000):
        return req_post("xy/jog", {"dx": dx, "dy": dy, "feed": feed})

    def xy_home_x(self):
        return req_post("xy/home/x")

    def xy_home_y(self):
        return req_post("xy/home/y")

    def xy_command(self, command: str):
        return req_post("xy/command", {"command": command})


# Serial reader thread
try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    serial = None
    list_ports = None


class SerialReader(QThread):
    """Background thread for reading serial port."""
    line = Signal(str)
    opened = Signal(bool)

    def __init__(self):
        super().__init__()
        self._ser = None
        self._stop = False

    def open(self, port: str, baud: int):
        if serial is None:
            self.line.emit("pyserial not installed")
            self.opened.emit(False)
            return False
        self.close()
        try:
            self._ser = serial.Serial(port=port, baudrate=baud, timeout=0.1)
            self._stop = False
            if not self.isRunning():
                self.start()
            self.opened.emit(True)
            self.line.emit(f"[OPEN] {port} @ {baud}")
            return True
        except Exception as e:
            self._ser = None
            self.opened.emit(False)
            self.line.emit(f"[ERROR] {e}")
            return False

    def close(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.opened.emit(False)

    def write(self, text: str):
        if self._ser:
            try:
                if not text.endswith("\n"):
                    text += "\n"
                self._ser.write(text.encode("utf-8"))
            except Exception as e:
                self.line.emit(f"[ERROR] write: {e}")

    def run(self):
        while not self._stop:
            if self._ser:
                try:
                    data = self._ser.readline()
                    if data:
                        s = data.decode("utf-8", "ignore").rstrip()
                        self.line.emit(s)
                except Exception as e:
                    self.line.emit(f"[ERROR] read: {e}")
                    time.sleep(0.2)
            else:
                time.sleep(0.1)

    def stop(self):
        self._stop = True
        self.wait(1000)
        self.close()


# UI Helpers
def make_card(title: str) -> QFrame:
    """Create a styled card widget."""
    box = QFrame()
    box.setObjectName("card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(10)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    return box


def big_button(text: str) -> QPushButton:
    """Create a large button."""
    b = QPushButton(text)
    b.setObjectName("bigButton")
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    b.setMinimumHeight(160)
    return b


# === TABS ===

class WorkTab(QWidget):
    """Main work tab with START/STOP buttons."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        # IP Label
        self.ipLabel = QLabel(f"IP: {get_local_ip()}")
        self.ipLabel.setObjectName("muted")
        root.addWidget(self.ipLabel, 0, Qt.AlignLeft)

        # Buttons row
        row = QHBoxLayout()
        row.setSpacing(18)
        self.btnStart = big_button("START CYCLE")
        self.btnStop = big_button("STOP")
        self.btnStop.setObjectName("stopButton")
        row.addWidget(self.btnStart)
        row.addWidget(self.btnStop)
        root.addLayout(row, 1)

        # Status label
        self.lblStatus = QLabel("Status: Ready")
        self.lblStatus.setObjectName("statusBadge")
        self.lblStatus.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lblStatus)

        # XY Position
        self.lblXY = QLabel("X: 0.00  Y: 0.00")
        self.lblXY.setObjectName("state")
        root.addWidget(self.lblXY, 0, Qt.AlignLeft)

        # Connections
        self.btnStart.clicked.connect(self.on_start)
        self.btnStop.clicked.connect(self.on_stop)

    def on_start(self):
        try:
            # Resume if paused, otherwise just log
            self.api.cycle_pause()  # Toggle pause
            self.lblStatus.setText("Cycle resumed")
        except Exception as e:
            self.lblStatus.setText(f"Error: {e}")

    def on_stop(self):
        try:
            self.api.cycle_stop()
            self.lblStatus.setText("Cycle stopped")
        except Exception as e:
            self.lblStatus.setText(f"Error: {e}")

    def render(self, st: dict):
        cycle = st.get("cycle", {})
        xy = st.get("xy_table", {})

        state = cycle.get("state", "IDLE")
        holes = cycle.get("holes_completed", 0)
        total = cycle.get("total_holes", 0)

        is_running = state not in ("IDLE", "COMPLETED", "ERROR", "ESTOP")

        self.lblStatus.setText(f"State: {state} | Holes: {holes}/{total}")
        self.btnStart.setProperty("ok", is_running)
        self.btnStop.setProperty("ok", is_running)

        if xy:
            x = xy.get("x", 0)
            y = xy.get("y", 0)
            self.lblXY.setText(f"X: {x:.2f}  Y: {y:.2f}")

        for w in (self.btnStart, self.btnStop):
            w.style().unpolish(w)
            w.style().polish(w)


class StartTab(QWidget):
    """Start tab with device selection."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._devices = []
        self._selected_key = None
        self._device_buttons = {}
        self._devices_refresh_ts = 0.0  # throttle device refresh
        self.on_started = None

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        # Left: Device list
        left = QVBoxLayout()
        left.setSpacing(12)

        self.devCard = make_card("Devices")
        self.devScroll = QScrollArea()
        self.devScroll.setWidgetResizable(True)
        self.devList = QWidget()
        self.devListLay = QVBoxLayout(self.devList)
        self.devListLay.setContentsMargins(0, 0, 0, 0)
        self.devListLay.setSpacing(10)
        self.devScroll.setWidget(self.devList)
        self.devCard.layout().addWidget(self.devScroll)
        left.addWidget(self.devCard)

        # Right: Start button
        right = QVBoxLayout()
        right.setSpacing(18)

        self.btnStart = big_button("START PROGRAM")
        self.btnStart.setMinimumHeight(220)
        right.addWidget(self.btnStart, 2)

        self.lblStatus = QLabel("Select a device and press START")
        self.lblStatus.setObjectName("state")
        right.addWidget(self.lblStatus, 0, Qt.AlignLeft)

        root.addLayout(left, 3)
        root.addLayout(right, 7)

        self.btnStart.clicked.connect(self.on_start)

    def _rebuild_devices(self, devices: list):
        # Clear old widgets safely
        while self.devListLay.count():
            item = self.devListLay.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()
        self._device_buttons.clear()

        # Safety check for devices
        if not devices:
            return

        for d in devices:
            if not isinstance(d, dict):
                continue
            key = d.get("key")
            if not key:
                continue
            name = d.get("name", key)
            holes = d.get("holes", 0)
            text = f"{name}\n{holes} holes"

            btn = QPushButton(text)
            btn.setObjectName("devButton")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(80)
            btn.clicked.connect(lambda _, k=key: self._select_device(k))

            self.devListLay.addWidget(btn)
            self._device_buttons[key] = btn

        self.devListLay.addStretch(1)
        self._apply_styles()

    def _apply_styles(self):
        for key, btn in self._device_buttons.items():
            btn.setProperty("selected", key == self._selected_key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _select_device(self, key: str):
        self._selected_key = key
        self._apply_styles()
        name = self._device_buttons.get(key)
        if name:
            self.lblStatus.setText(f"Selected: {name.text().splitlines()[0]}")

    def on_start(self):
        if not self._selected_key:
            self.lblStatus.setText("Select a device first!")
            return
        try:
            self.api.cycle_start(self._selected_key)
            self.lblStatus.setText("Cycle started!")
            if callable(self.on_started):
                self.on_started()
        except Exception as e:
            self.lblStatus.setText(f"Start failed: {e}")

    def render(self, st: dict):
        # Refresh devices every 2 seconds (throttled like old code)
        now = time.time()
        if (now - self._devices_refresh_ts) > 2.0 or not self._devices:
            self._devices_refresh_ts = now
            try:
                devices = self.api.devices()
                if devices and isinstance(devices, list) and devices != self._devices:
                    self._devices = devices
                    self._rebuild_devices(devices)
            except Exception as e:
                print(f"[StartTab] devices error: {e}")

        try:
            cycle = st.get("cycle") or {}
            is_running = cycle.get("state", "IDLE") not in ("IDLE", "COMPLETED")
            self.btnStart.setEnabled(not is_running and bool(self._selected_key))

            for btn in self._device_buttons.values():
                btn.setEnabled(not is_running)
        except Exception as e:
            print(f"[StartTab] render error: {e}")


class ServiceTab(QWidget):
    """Service tab with sensors, relays, and serial terminal."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._relay_widgets = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        # Left column
        left = QVBoxLayout()
        left.setSpacing(18)

        # Sensors card
        self.sensorsCard = make_card("Sensors")
        self.sensorsGrid = QGridLayout()
        self.sensorsCard.layout().addLayout(self.sensorsGrid)
        left.addWidget(self.sensorsCard)

        # Relays card
        self.relaysCard = make_card("Relays")
        self.relaysGrid = QGridLayout()
        self.relaysCard.layout().addLayout(self.relaysGrid)
        left.addWidget(self.relaysCard, 1)

        # Right column - Serial
        right = QVBoxLayout()
        right.setSpacing(18)

        # Network card
        self.netCard = make_card("Network")
        self.lblIp = QLabel(f"IP: {get_local_ip()}")
        self.btnIpRefresh = QPushButton("Refresh")
        row = QHBoxLayout()
        row.addWidget(self.lblIp, 1)
        row.addWidget(self.btnIpRefresh)
        self.netCard.layout().addLayout(row)
        right.addWidget(self.netCard)

        # Serial card
        self.serialCard = make_card("Serial Terminal")
        sc = self.serialCard.layout()

        top = QHBoxLayout()
        self.cbPort = QComboBox()
        self.cbBaud = QComboBox()
        for b in (9600, 115200, 230400):
            self.cbBaud.addItem(str(b))
        self.cbBaud.setCurrentText("115200")
        self.btnRefresh = QPushButton("Refresh")
        self.btnOpen = QPushButton("Open")
        self.btnClose = QPushButton("Close")
        top.addWidget(QLabel("Port:"))
        top.addWidget(self.cbPort, 1)
        top.addWidget(QLabel("Baud:"))
        top.addWidget(self.cbBaud)
        top.addWidget(self.btnRefresh)
        top.addWidget(self.btnOpen)
        top.addWidget(self.btnClose)
        sc.addLayout(top)

        self.txtLog = QTextEdit()
        self.txtLog.setReadOnly(True)
        self.txtLog.setMinimumHeight(200)
        sc.addWidget(self.txtLog, 1)

        send = QHBoxLayout()
        self.edSend = QLineEdit()
        self.edSend.setPlaceholderText("Enter command...")
        self.btnSend = QPushButton("Send")
        send.addWidget(self.edSend, 1)
        send.addWidget(self.btnSend)
        sc.addLayout(send)

        right.addWidget(self.serialCard, 1)

        root.addLayout(left, 2)
        root.addLayout(right, 1)

        # Serial backend
        self.reader = SerialReader()
        self.reader.line.connect(self.log_line)
        self.reader.opened.connect(self.serial_opened)

        # Connections
        self.btnIpRefresh.clicked.connect(lambda: self.lblIp.setText(f"IP: {get_local_ip()}"))
        self.btnRefresh.clicked.connect(self.fill_ports)
        self.btnOpen.clicked.connect(self.open_serial)
        self.btnClose.clicked.connect(self.reader.close)
        self.btnSend.clicked.connect(self.send_serial)

        self.fill_ports()

    def fill_ports(self):
        self.cbPort.clear()
        ports = []
        if list_ports:
            try:
                ports = [p.device for p in list_ports.comports()]
            except Exception:
                pass
        for p in ["/dev/ttyAMA0", "/dev/ttyUSB0", "/dev/ttyACM0"]:
            if p not in ports:
                ports.append(p)
        for p in ports:
            self.cbPort.addItem(p)

    def open_serial(self):
        port = self.cbPort.currentText().strip()
        baud = int(self.cbBaud.currentText())
        if port:
            self.reader.open(port, baud)

    def send_serial(self):
        text = self.edSend.text().strip()
        if text:
            self.reader.write(text)
            self.edSend.clear()

    def serial_opened(self, ok: bool):
        self.btnOpen.setEnabled(not ok)
        self.btnClose.setEnabled(ok)
        self.btnSend.setEnabled(ok)

    def log_line(self, s: str):
        self.txtLog.append(s)

    def _relay_cell(self, row: int, name: str):
        lblName = QLabel(name)
        lblName.setObjectName("badge")
        lblState = QLabel("—")
        lblState.setObjectName("stateOnOff")
        btnOn = QPushButton("ON")
        btnOff = QPushButton("OFF")
        btnOn.clicked.connect(lambda: self._relay_cmd(name, "on"))
        btnOff.clicked.connect(lambda: self._relay_cmd(name, "off"))

        self.relaysGrid.addWidget(lblName, row, 0)
        self.relaysGrid.addWidget(lblState, row, 1)
        self.relaysGrid.addWidget(btnOn, row, 2)
        self.relaysGrid.addWidget(btnOff, row, 3)
        self._relay_widgets[name] = (lblState, btnOn, btnOff)

    def _relay_cmd(self, name: str, state: str):
        try:
            self.api.relay_set(name, state)
        except Exception as e:
            self.log_line(f"[ERROR] relay {name}: {e}")

    def render(self, st: dict):
        sensors = st.get("sensors", {})
        relays = st.get("relays", {})

        # Rebuild sensors
        while self.sensorsGrid.count():
            item = self.sensorsGrid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, (name, state) in enumerate(sensors.items()):
            lab = QLabel(name)
            lab.setObjectName("badge")
            is_active = state == "ACTIVE"
            val = QLabel("ACTIVE" if is_active else "INACTIVE")
            val.setObjectName("ok" if is_active else "off")
            self.sensorsGrid.addWidget(lab, i, 0)
            self.sensorsGrid.addWidget(val, i, 1)

        # Rebuild relays if needed
        if set(relays.keys()) != set(self._relay_widgets.keys()):
            while self.relaysGrid.count():
                item = self.relaysGrid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._relay_widgets.clear()
            for i, name in enumerate(relays.keys()):
                self._relay_cell(i, name)

        # Update relay states
        for name, widgets in self._relay_widgets.items():
            lblState, btnOn, btnOff = widgets
            is_on = relays.get(name) == "ON"
            lblState.setText("ON" if is_on else "OFF")
            lblState.setProperty("on", is_on)
            lblState.style().unpolish(lblState)
            lblState.style().polish(lblState)



class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screw Drive TouchDesk")
        self.setObjectName("root")
        self.api = ApiClient()

        # Central widget
        self.frame = QFrame()
        self.frame.setObjectName("rootFrame")
        self.frame.setProperty("state", "idle")
        self.setCentralWidget(self.frame)

        root = QVBoxLayout(self.frame)
        root.setContentsMargins(BORDER_W, BORDER_W, BORDER_W, BORDER_W)

        # Tabs
        tabs = QTabWidget()
        tabs.setObjectName("tabs")
        # Make tabs expand to fill full width and disable scroll arrows
        tabs.tabBar().setExpanding(True)
        tabs.tabBar().setUsesScrollButtons(False)
        root.addWidget(tabs)

        self.tabStart = StartTab(self.api)
        self.tabWork = WorkTab(self.api)
        self.tabService = ServiceTab(self.api)

        # Add ALL tabs at startup to keep geometry stable (prevents touch offset issues)
        tabs.addTab(self.tabStart, "START")
        tabs.addTab(self.tabWork, "WORK")
        tabs.addTab(self.tabService, "SERVICE")

        # Hide SERVICE tab initially
        # Use setTabVisible if available (Qt 5.15+), otherwise use tab removal
        self._use_tab_visible = hasattr(tabs.tabBar(), 'setTabVisible')
        if self._use_tab_visible:
            tabs.tabBar().setTabVisible(2, False)  # SERVICE
        else:
            # Fallback: remove tab but keep reference (will add back on unlock)
            tabs.removeTab(2)  # Remove SERVICE

        self.tabs = tabs
        self._service_tab_visible = False
        self._device_selected = False

        # Block tab changes until device is selected
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._last_valid_tab = 0  # START tab

        # Callback when cycle started - go to WORK tab
        self.tabStart.on_started = self._on_cycle_started

        # Pedal hold tracking for SERVICE tab unlock
        self._pedal_hold_start = None
        self._pedal_was_active = False
        self.PEDAL_HOLD_SECONDS = 4.0

        # Poll timer
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        # Start on START tab
        self.tabs.setCurrentIndex(0)

        # Fullscreen
        self.showFullScreen()

    def _on_tab_changed(self, index: int):
        """Handle tab change - block if device not selected."""
        # Always allow START tab (index 0)
        if index == 0:
            self._last_valid_tab = 0
            return

        # WORK tab (index 1) - only if device selected
        if index == 1:
            if not self._device_selected:
                # Block navigation, return to START
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(0)
                self.tabs.blockSignals(False)
                self.tabStart.lblStatus.setText("Спочатку виберіть девайс!")
                return
            self._last_valid_tab = 1

        # SERVICE tab (index 2) - only if visible
        if index == 2 and self._service_tab_visible:
            self._last_valid_tab = 2

    def _on_cycle_started(self):
        """Called when cycle starts - switch to WORK tab."""
        self._device_selected = True
        self.tabs.setCurrentIndex(1)  # WORK tab

    def _check_pedal_hold(self, sensors: dict):
        """Check if pedal is held for 4 seconds to unlock SERVICE tab."""
        if self._service_tab_visible:
            return  # Already visible

        pedal_active = sensors.get("ped_start") == "ACTIVE"

        if pedal_active:
            if not self._pedal_was_active:
                # Pedal just pressed - start timer
                self._pedal_hold_start = time.time()
                self._pedal_was_active = True
            else:
                # Pedal still held - check duration
                if self._pedal_hold_start:
                    elapsed = time.time() - self._pedal_hold_start
                    if elapsed >= self.PEDAL_HOLD_SECONDS:
                        self._unlock_service_tab()
        else:
            # Pedal released - reset
            self._pedal_hold_start = None
            self._pedal_was_active = False

    def _unlock_service_tab(self):
        """Show SERVICE tab (unlock it)."""
        if self._service_tab_visible:
            return

        if self._use_tab_visible:
            # Qt 5.15+: just show the hidden tab
            self.tabs.tabBar().setTabVisible(2, True)  # SERVICE
        else:
            # Fallback: add tab back
            self.tabs.addTab(self.tabService, "SERVICE")

        self._service_tab_visible = True

        # Force UI update to show new tab immediately
        self.tabs.tabBar().updateGeometry()
        self.tabs.tabBar().update()
        self.tabs.update()

        print("[UI] SERVICE tab unlocked (pedal held 4s)")

    def set_border(self, state: str):
        self.frame.setProperty("state", state)
        self.frame.style().unpolish(self.frame)
        self.frame.style().polish(self.frame)

    def refresh(self):
        try:
            st = self.api.status()
        except Exception:
            self.set_border("alarm")
            return

        # Check pedal hold for SERVICE unlock
        sensors = st.get("sensors", {})
        self._check_pedal_hold(sensors)

        # Track device selection from StartTab
        if self.tabStart._selected_key:
            self._device_selected = True

        # Render all tabs
        for tab in (self.tabWork, self.tabStart, self.tabService):
            try:
                tab.render(st)
            except Exception as e:
                print(f"[UI] render error: {e}")

        # Border state
        cycle = st.get("cycle", {})
        state = cycle.get("state", "IDLE")

        if state == "ESTOP" or state == "ERROR":
            self.set_border("alarm")
        elif state not in ("IDLE", "COMPLETED"):
            self.set_border("ok")
        else:
            self.set_border("idle")


# QSS Stylesheet
APP_QSS = f"""
#root {{ background-color: #0f1115; }}
#rootFrame[state="ok"]    {{ border: {BORDER_W}px solid #1ac06b; }}
#rootFrame[state="idle"]  {{ border: {BORDER_W}px solid #f0b400; }}
#rootFrame[state="alarm"] {{ border: {BORDER_W}px solid #e5484d; }}

#devButton {{
    font-size: 20px; font-weight: 700; text-align: left;
    padding: 14px 16px; border: 2px solid #3a4356; border-radius: 14px;
    background: #2b3342; color: #e8edf8;
}}
#devButton:hover {{ background: #354159; }}
#devButton[selected="true"] {{
    border-color: #1ac06b; background: #153f2c; color: #e9ffee;
}}

#tabs::pane {{ border: none; }}
QTabBar::tab {{
    color: #cfd5e1; background: #1a1f29;
    padding: 18px 32px; margin-right: 6px;
    border-top-left-radius: 10px; border-top-right-radius: 10px;
    font-size: 28px; font-weight: 700;
    min-height: 80px; min-width: 220px;
}}
QTabBar::tab:selected {{ background: #242a36; color: white; border-bottom: 6px solid #1ac06b; }}

#card {{
    background: #1a1f29; border: 1px solid #2a3140;
    border-radius: 16px; color: #d8deea;
}}
#cardTitle {{ font-size: 18px; font-weight: 600; color: #eef3ff; }}

#bigButton {{
    font-size: 32px; font-weight: 700;
    background: #2b3342; color: #e8edf8;
    border: 2px solid #3a4356; border-radius: 18px;
}}
#bigButton[ok="true"] {{ background: #153f2c; border-color: #1ac06b; color: #e9ffee; }}
#stopButton {{ font-size: 32px; font-weight: 700; background: #3a1c1c; border-color: #e5484d; color: #ffe9e9; }}
#bigButton:pressed {{ background: #354159; }}

#badge {{
    background: #2a3140; color: #dbe3f5;
    padding: 4px 10px; border-radius: 999px; font-weight: 600;
}}
#state {{ color: #cbd5e1; font-size: 16px; }}
#statusBadge {{
    padding: 12px 16px; border: 2px solid #3a4356;
    border-radius: 12px; background: #2b3342;
    color: #ffffff; font-size: 18px; font-weight: 600;
}}
QLabel#ok {{ color: #1ac06b; font-weight: 700; }}
QLabel#off {{ color: #e5484d; font-weight: 700; }}

QPushButton {{
    background: #2b3342; color: #e8edf8;
    border: 1px solid #3a4356; border-radius: 10px; padding: 8px 14px;
}}
QPushButton:disabled {{ opacity: .5; }}

QSpinBox, QLineEdit, QComboBox {{
    background: #1f2531; color: #dbe3f5;
    border: 1px solid #334157; border-radius: 8px; padding: 6px 8px;
}}
QTextEdit {{
    background: #0f141c; color: #d3ddf0;
    border: 1px solid #334157; border-radius: 10px;
}}
"""


def main():
    app = QApplication(sys.argv)
    app.setOverrideCursor(QCursor(Qt.BlankCursor))
    app.setStyleSheet(APP_QSS)
    f = QFont()
    f.setPixelSize(16)
    app.setFont(f)

    w = MainWindow()
    w.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
