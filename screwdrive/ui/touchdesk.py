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
        # Clear old
        for i in reversed(range(self.devListLay.count())):
            w = self.devListLay.itemAt(i).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._device_buttons.clear()

        for d in devices:
            key = d.get("key")
            name = d.get("name", key or "?")
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
        # Refresh devices periodically
        try:
            devices = self.api.devices()
            if devices != self._devices:
                self._devices = devices
                self._rebuild_devices(devices)
        except Exception:
            pass

        cycle = st.get("cycle", {})
        is_running = cycle.get("state", "IDLE") not in ("IDLE", "COMPLETED")
        self.btnStart.setEnabled(not is_running and bool(self._selected_key))

        for btn in self._device_buttons.values():
            btn.setEnabled(not is_running)


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


class SettingsTab(QWidget):
    """Settings tab with device management and XY table controls."""

    # Style constants
    CARD_STYLE = """
        QFrame {
            background: #1e1e1e;
            border: 1px solid #3a3a3a;
            border-radius: 12px;
        }
    """
    HEADER_STYLE = "color: #e0e0e0; font-size: 16px; font-weight: bold;"
    LABEL_STYLE = "color: #b0b0b0; font-size: 13px;"
    INPUT_STYLE = """
        QLineEdit, QSpinBox, QComboBox {
            background: #2a2a2a;
            border: 1px solid #4a4a4a;
            border-radius: 6px;
            padding: 8px 12px;
            color: #e0e0e0;
            font-size: 14px;
        }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
            border: 1px solid #5a9fd4;
        }
        QComboBox::drop-down {
            border: none;
            padding-right: 8px;
        }
    """
    BTN_PRIMARY = """
        QPushButton {
            background: #5a9fd4;
            border: none;
            border-radius: 8px;
            color: white;
            font-size: 15px;
            font-weight: bold;
            padding: 12px 20px;
        }
        QPushButton:pressed {
            background: #4a8fc4;
        }
    """
    BTN_SECONDARY = """
        QPushButton {
            background: #3a3a3a;
            border: 1px solid #5a5a5a;
            border-radius: 8px;
            color: #d0d0d0;
            font-size: 15px;
            font-weight: bold;
            padding: 12px 20px;
        }
        QPushButton:pressed {
            background: #4a4a4a;
        }
    """
    BTN_JOG = """
        QPushButton {
            background: #5a9fd4;
            border: none;
            border-radius: 10px;
            color: white;
            font-size: 18px;
            font-weight: bold;
        }
        QPushButton:pressed {
            background: #4a8fc4;
        }
    """
    BTN_ADD = """
        QPushButton {
            background: #2d5a3d;
            border: none;
            border-radius: 6px;
            color: #6fcf97;
            font-size: 24px;
            font-weight: bold;
        }
        QPushButton:pressed {
            background: #3d6a4d;
        }
    """
    BTN_DEL = """
        QPushButton {
            background: #5a2d2d;
            border: none;
            border-radius: 6px;
            color: #eb5757;
            font-size: 20px;
            font-weight: bold;
        }
        QPushButton:pressed {
            background: #6a3d3d;
        }
    """
    DEVICE_BTN = """
        QPushButton {
            background: #2a2a2a;
            border: 1px solid #3a3a3a;
            border-radius: 8px;
            color: #d0d0d0;
            font-size: 14px;
            text-align: left;
            padding: 10px 14px;
        }
        QPushButton:checked {
            background: #3a5a7a;
            border: 1px solid #5a9fd4;
            color: white;
        }
        QPushButton:pressed {
            background: #3a4a5a;
        }
    """
    SCROLL_STYLE = """
        QScrollArea {
            background: transparent;
            border: none;
        }
        QScrollBar:vertical {
            background: #2a2a2a;
            width: 8px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #5a5a5a;
            border-radius: 4px;
            min-height: 30px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
    """

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._devices = []
        self._editing_device = None
        self._selected_device_key = None
        self._device_buttons = {}
        self._coord_rows = []

        # Apply global input style
        self.setStyleSheet(self.INPUT_STYLE + self.SCROLL_STYLE)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ========== LEFT COLUMN ==========
        leftFrame = QFrame()
        leftFrame.setStyleSheet(self.CARD_STYLE)
        left = QVBoxLayout(leftFrame)
        left.setContentsMargins(16, 16, 16, 16)
        left.setSpacing(14)

        # --- ДЕВАЙСИ section ---
        devHeader = QHBoxLayout()
        lblDevices = QLabel("ДЕВАЙСИ")
        lblDevices.setStyleSheet(self.HEADER_STYLE)
        devHeader.addWidget(lblDevices)
        devHeader.addStretch(1)
        self.btnAddDevice = QPushButton("+")
        self.btnAddDevice.setFixedSize(36, 36)
        self.btnAddDevice.setStyleSheet(self.BTN_ADD)
        self.btnAddDevice.clicked.connect(self._new_device)
        devHeader.addWidget(self.btnAddDevice)
        left.addLayout(devHeader)

        # Device list
        self.devScroll = QScrollArea()
        self.devScroll.setWidgetResizable(True)
        self.devScroll.setFixedHeight(120)
        self.devScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.devListWidget = QWidget()
        self.devListWidget.setStyleSheet("background: transparent;")
        self.devListLay = QVBoxLayout(self.devListWidget)
        self.devListLay.setContentsMargins(0, 0, 0, 0)
        self.devListLay.setSpacing(6)
        self.devScroll.setWidget(self.devListWidget)
        left.addWidget(self.devScroll)

        # Separator
        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet("background: #3a3a3a;")
        left.addWidget(sep1)

        # --- XY TABLE CONTROL ---
        xyHeader = QLabel("XY TABLE CONTROL")
        xyHeader.setStyleSheet(self.HEADER_STYLE)
        left.addWidget(xyHeader)

        # Position display
        self.lblPos = QLabel("X: 0.00   Y: 0.00")
        self.lblPos.setStyleSheet("color: #5a9fd4; font-size: 20px; font-weight: bold; padding: 6px 0;")
        self.lblPos.setAlignment(Qt.AlignCenter)
        left.addWidget(self.lblPos)

        # Jog buttons
        jogContainer = QWidget()
        jogGrid = QGridLayout(jogContainer)
        jogGrid.setContentsMargins(10, 0, 10, 0)
        jogGrid.setSpacing(8)

        self.btnYMinus = QPushButton("Y −")
        self.btnYPlus = QPushButton("Y +")
        self.btnXMinus = QPushButton("X −")
        self.btnXPlus = QPushButton("X +")

        for btn in [self.btnYPlus, self.btnYMinus, self.btnXPlus, self.btnXMinus]:
            btn.setFixedSize(80, 60)
            btn.setStyleSheet(self.BTN_JOG)

        jogGrid.addWidget(self.btnYMinus, 0, 1, Qt.AlignCenter)
        jogGrid.addWidget(self.btnXMinus, 1, 0, Qt.AlignCenter)
        jogGrid.addWidget(self.btnXPlus, 1, 2, Qt.AlignCenter)
        jogGrid.addWidget(self.btnYPlus, 2, 1, Qt.AlignCenter)

        self.btnYMinus.clicked.connect(lambda: self._jog(0, -1))
        self.btnYPlus.clicked.connect(lambda: self._jog(0, 1))
        self.btnXMinus.clicked.connect(lambda: self._jog(-1, 0))
        self.btnXPlus.clicked.connect(lambda: self._jog(1, 0))

        left.addWidget(jogContainer)

        # Step selector
        stepLbl = QLabel("Крок переміщення:")
        stepLbl.setStyleSheet(self.LABEL_STYLE)
        left.addWidget(stepLbl)

        self.cbStep = QComboBox()
        self.cbStep.addItem("Вільне", 0)
        for step in ["0.1", "0.5", "1", "5", "10", "50", "100"]:
            self.cbStep.addItem(f"{step} mm", float(step))
        self.cbStep.setCurrentIndex(5)
        self.cbStep.setFixedHeight(44)
        left.addWidget(self.cbStep)

        # HOME buttons
        homeRow = QHBoxLayout()
        homeRow.setSpacing(8)
        self.btnHome = QPushButton("HOME")
        self.btnHomeY = QPushButton("Y")
        self.btnHomeX = QPushButton("X")

        self.btnHome.setFixedHeight(48)
        self.btnHome.setStyleSheet(self.BTN_PRIMARY)
        self.btnHomeY.setFixedSize(50, 48)
        self.btnHomeY.setStyleSheet(self.BTN_SECONDARY)
        self.btnHomeX.setFixedSize(50, 48)
        self.btnHomeX.setStyleSheet(self.BTN_SECONDARY)

        self.btnHome.clicked.connect(self._on_home)
        self.btnHomeY.clicked.connect(self._on_home_y)
        self.btnHomeX.clicked.connect(self._on_home_x)

        homeRow.addWidget(self.btnHome, 1)
        homeRow.addWidget(self.btnHomeY)
        homeRow.addWidget(self.btnHomeX)
        left.addLayout(homeRow)

        left.addStretch(1)

        # ========== RIGHT COLUMN ==========
        rightFrame = QFrame()
        rightFrame.setStyleSheet(self.CARD_STYLE)
        right = QVBoxLayout(rightFrame)
        right.setContentsMargins(16, 16, 16, 16)
        right.setSpacing(12)

        # --- КООРДИНАТИ ПЕРЕМІЩЕННЯ ---
        coordHeader = QHBoxLayout()
        lblCoords = QLabel("КООРДИНАТИ ПЕРЕМІЩЕННЯ")
        lblCoords.setStyleSheet(self.HEADER_STYLE)
        coordHeader.addWidget(lblCoords)
        coordHeader.addStretch(1)
        self.btnAddCoord = QPushButton("+")
        self.btnAddCoord.setFixedSize(36, 36)
        self.btnAddCoord.setStyleSheet(self.BTN_ADD)
        self.btnAddCoord.clicked.connect(self._add_coord_row)
        coordHeader.addWidget(self.btnAddCoord)
        right.addLayout(coordHeader)

        # Column headers
        colHeaders = QHBoxLayout()
        colHeaders.setSpacing(6)
        colHeaders.addWidget(QLabel(""), 0)  # Number placeholder
        for txt, w in [("X", 70), ("Y", 70), ("Тип", 90), ("F", 70)]:
            lbl = QLabel(txt)
            lbl.setStyleSheet(self.LABEL_STYLE)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedWidth(w)
            colHeaders.addWidget(lbl)
        colHeaders.addWidget(QLabel(""), 0)  # Delete btn placeholder
        colHeaders.addStretch(1)
        right.addLayout(colHeaders)

        # Coordinate list
        self.coordScroll = QScrollArea()
        self.coordScroll.setWidgetResizable(True)
        self.coordScroll.setMinimumHeight(200)
        self.coordScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.coordListWidget = QWidget()
        self.coordListWidget.setStyleSheet("background: transparent;")
        self.coordListLay = QVBoxLayout(self.coordListWidget)
        self.coordListLay.setContentsMargins(0, 0, 0, 0)
        self.coordListLay.setSpacing(6)
        self.coordScroll.setWidget(self.coordListWidget)
        right.addWidget(self.coordScroll, 1)

        # Separator
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: #3a3a3a;")
        right.addWidget(sep2)

        # --- РЕДАКТОР ДЕВАЙСУ ---
        editorHeader = QLabel("РЕДАКТОР ДЕВАЙСУ")
        editorHeader.setStyleSheet(self.HEADER_STYLE)
        editorHeader.setAlignment(Qt.AlignCenter)
        right.addWidget(editorHeader)

        # Row 1: Name, What
        paramRow1 = QHBoxLayout()
        paramRow1.setSpacing(10)

        col1 = QVBoxLayout()
        col1.setSpacing(4)
        lbl1 = QLabel("Назва (код)")
        lbl1.setStyleSheet(self.LABEL_STYLE)
        col1.addWidget(lbl1)
        self.edName = QLineEdit()
        self.edName.setPlaceholderText("MCO_BACK")
        self.edName.setFixedHeight(42)
        col1.addWidget(self.edName)
        paramRow1.addLayout(col1, 1)

        col2 = QVBoxLayout()
        col2.setSpacing(4)
        lbl2 = QLabel("Опис")
        lbl2.setStyleSheet(self.LABEL_STYLE)
        col2.addWidget(lbl2)
        self.edWhat = QLineEdit()
        self.edWhat.setPlaceholderText("Що крутим")
        self.edWhat.setFixedHeight(42)
        col2.addWidget(self.edWhat)
        paramRow1.addLayout(col2, 1)

        right.addLayout(paramRow1)

        # Row 2: Holes, Size, Task
        paramRow2 = QHBoxLayout()
        paramRow2.setSpacing(10)

        col3 = QVBoxLayout()
        col3.setSpacing(4)
        lbl3 = QLabel("Кількість гвинтів")
        lbl3.setStyleSheet(self.LABEL_STYLE)
        col3.addWidget(lbl3)
        self.spHoles = QSpinBox()
        self.spHoles.setRange(0, 100)
        self.spHoles.setFixedHeight(42)
        col3.addWidget(self.spHoles)
        paramRow2.addLayout(col3, 1)

        col4 = QVBoxLayout()
        col4.setSpacing(4)
        lbl4 = QLabel("Розмір гвинтів")
        lbl4.setStyleSheet(self.LABEL_STYLE)
        col4.addWidget(lbl4)
        self.edSize = QLineEdit()
        self.edSize.setPlaceholderText("M3x10")
        self.edSize.setFixedHeight(42)
        col4.addWidget(self.edSize)
        paramRow2.addLayout(col4, 1)

        col5 = QVBoxLayout()
        col5.setSpacing(4)
        lbl5 = QLabel("Номер таски")
        lbl5.setStyleSheet(self.LABEL_STYLE)
        col5.addWidget(lbl5)
        self.edTask = QLineEdit()
        self.edTask.setPlaceholderText("TASK-123")
        self.edTask.setFixedHeight(42)
        col5.addWidget(self.edTask)
        paramRow2.addLayout(col5, 1)

        # Hidden key field
        self.edKey = QLineEdit()
        self.edKey.setVisible(False)

        right.addLayout(paramRow2)

        # Save/Cancel buttons
        btnRow = QHBoxLayout()
        btnRow.setSpacing(12)

        self.btnSave = QPushButton("Зберегти")
        self.btnSave.setFixedHeight(52)
        self.btnSave.setStyleSheet(self.BTN_PRIMARY)
        self.btnSave.clicked.connect(self._save_device)
        btnRow.addWidget(self.btnSave, 1)

        self.btnCancel = QPushButton("Скасувати")
        self.btnCancel.setFixedHeight(52)
        self.btnCancel.setStyleSheet(self.BTN_SECONDARY)
        self.btnCancel.clicked.connect(self._cancel_edit)
        btnRow.addWidget(self.btnCancel, 1)

        right.addLayout(btnRow)

        # Add columns to root
        root.addWidget(leftFrame, 4)
        root.addWidget(rightFrame, 6)

    def _jog(self, dx: int, dy: int):
        """Jog XY table by selected step."""
        try:
            step = self.cbStep.currentData()
            if step == 0:
                # Free movement - continuous jog (use larger step)
                step = 1000
            self.api.xy_jog(dx * step, dy * step, 5000)
        except Exception as e:
            print(f"[Settings] Jog error: {e}")

    def _on_home(self):
        """Home both axes."""
        try:
            self.api.xy_home()
        except Exception as e:
            print(f"[Settings] Home error: {e}")

    def _on_home_y(self):
        """Home Y axis only."""
        try:
            self.api.xy_home_y()
        except Exception as e:
            print(f"[Settings] Home Y error: {e}")

    def _on_home_x(self):
        """Home X axis only."""
        try:
            self.api.xy_home_x()
        except Exception as e:
            print(f"[Settings] Home X error: {e}")

    def _add_coord_row(self, x="", y="", coord_type="FREE", feed="5000"):
        """Add a coordinate row to the list."""
        row_num = len(self._coord_rows) + 1

        rowWidget = QWidget()
        rowWidget.setStyleSheet("background: #252525; border-radius: 6px;")
        rowLay = QHBoxLayout(rowWidget)
        rowLay.setContentsMargins(8, 6, 8, 6)
        rowLay.setSpacing(6)

        # Row number
        lblNum = QLabel(f"{row_num}.")
        lblNum.setFixedWidth(24)
        lblNum.setStyleSheet("color: #808080; font-size: 13px; font-weight: bold; background: transparent;")
        rowLay.addWidget(lblNum)

        # X coordinate
        edX = QLineEdit(str(x))
        edX.setPlaceholderText("0")
        edX.setFixedSize(70, 38)
        edX.setAlignment(Qt.AlignCenter)
        rowLay.addWidget(edX)

        # Y coordinate
        edY = QLineEdit(str(y))
        edY.setPlaceholderText("0")
        edY.setFixedSize(70, 38)
        edY.setAlignment(Qt.AlignCenter)
        rowLay.addWidget(edY)

        # Type dropdown (FREE/WORK)
        cbType = QComboBox()
        cbType.addItems(["FREE", "WORK"])
        cbType.setCurrentText(coord_type.upper())
        cbType.setFixedSize(90, 38)
        rowLay.addWidget(cbType)

        # Feed rate
        edFeed = QLineEdit(str(feed))
        edFeed.setPlaceholderText("5000")
        edFeed.setFixedSize(70, 38)
        edFeed.setAlignment(Qt.AlignCenter)
        rowLay.addWidget(edFeed)

        # Delete button
        btnDel = QPushButton("−")
        btnDel.setFixedSize(36, 36)
        btnDel.setStyleSheet(self.BTN_DEL)
        btnDel.clicked.connect(lambda _, w=rowWidget: self._remove_coord_row(w))
        rowLay.addWidget(btnDel)

        rowLay.addStretch(1)

        # Store references
        rowWidget.edX = edX
        rowWidget.edY = edY
        rowWidget.cbType = cbType
        rowWidget.edFeed = edFeed
        rowWidget.lblNum = lblNum

        self._coord_rows.append(rowWidget)
        self.coordListLay.addWidget(rowWidget)

    def _remove_coord_row(self, rowWidget):
        """Remove a coordinate row."""
        if rowWidget in self._coord_rows:
            self._coord_rows.remove(rowWidget)
            rowWidget.setParent(None)
            rowWidget.deleteLater()
            self._renumber_coord_rows()

    def _renumber_coord_rows(self):
        """Renumber coordinate rows after deletion."""
        for i, row in enumerate(self._coord_rows):
            if hasattr(row, 'lblNum'):
                row.lblNum.setText(f"{i + 1}.")

    def _clear_coord_rows(self):
        """Clear all coordinate rows."""
        for row in self._coord_rows:
            row.setParent(None)
            row.deleteLater()
        self._coord_rows.clear()

    def _rebuild_device_list(self, devices: list):
        """Rebuild device list."""
        # Clear
        for i in reversed(range(self.devListLay.count())):
            item = self.devListLay.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
        self._device_buttons.clear()

        for d in devices:
            key = d.get("key", "")
            name = d.get("name", key)
            holes = d.get("holes", 0)

            btn = QPushButton(f"{name} ({holes} holes)")
            btn.setFixedHeight(40)
            btn.setCheckable(True)
            btn.setStyleSheet(self.DEVICE_BTN)
            btn.clicked.connect(lambda _, k=key: self._select_device(k))
            self.devListLay.addWidget(btn)
            self._device_buttons[key] = btn

        self.devListLay.addStretch(1)
        self._apply_device_styles()

    def _apply_device_styles(self):
        for key, btn in self._device_buttons.items():
            is_selected = (key == self._selected_device_key)
            btn.setChecked(is_selected)

    def _select_device(self, key: str):
        """Select device and load its data for editing."""
        self._selected_device_key = key
        self._apply_device_styles()
        self._load_device_for_edit(key)

    def _load_device_for_edit(self, key: str):
        """Load device data into editor."""
        try:
            full = req_get(f"devices/{key}")
            self._editing_device = key
            self.edKey.setText(full.get("key", ""))
            self.edName.setText(full.get("name", ""))
            self.edWhat.setText(full.get("what", ""))
            self.spHoles.setValue(full.get("holes", 0))
            self.edSize.setText(full.get("screw_size", ""))
            self.edTask.setText(full.get("task", ""))

            # Load coordinate steps
            self._clear_coord_rows()
            for s in full.get("steps", []):
                self._add_coord_row(
                    x=s.get("x", ""),
                    y=s.get("y", ""),
                    coord_type=s.get("type", "FREE"),
                    feed=s.get("feed", 5000)
                )
        except Exception as e:
            print(f"[Settings] Load device error: {e}")

    def _new_device(self):
        """Start creating new device."""
        self._editing_device = None
        self._selected_device_key = None
        self._apply_device_styles()

        # Clear editor
        self.edKey.setText("")
        self.edName.setText("")
        self.edWhat.setText("")
        self.spHoles.setValue(0)
        self.edSize.setText("")
        self.edTask.setText("")
        self._clear_coord_rows()

        # Add one empty coordinate row
        self._add_coord_row()

    def _save_device(self):
        """Save device (create or update)."""
        name = self.edName.text().strip()
        if not name:
            return

        # Generate key from name if new device
        key = self.edKey.text().strip() or name.upper().replace(" ", "_")

        # Collect steps from coordinate rows
        steps = []
        for row in self._coord_rows:
            try:
                x_val = float(row.edX.text()) if row.edX.text() else 0
                y_val = float(row.edY.text()) if row.edY.text() else 0
                feed_val = float(row.edFeed.text()) if row.edFeed.text() else 5000
                step_type = row.cbType.currentText().lower()
                steps.append({
                    "type": step_type,
                    "x": x_val,
                    "y": y_val,
                    "feed": feed_val
                })
            except ValueError:
                continue

        data = {
            "key": key,
            "name": name,
            "what": self.edWhat.text().strip(),
            "holes": self.spHoles.value(),
            "screw_size": self.edSize.text().strip(),
            "task": self.edTask.text().strip(),
            "steps": steps
        }

        try:
            import requests
            if self._editing_device:
                # Update existing
                requests.put(f"{API_BASE}/devices/{self._editing_device}",
                            json=data, timeout=5)
            else:
                # Create new
                requests.post(f"{API_BASE}/devices", json=data, timeout=5)

            self._editing_device = None
            # Refresh device list
            self._devices = []
        except Exception as e:
            print(f"[Settings] Save error: {e}")

    def _cancel_edit(self):
        """Cancel editing and clear editor."""
        self._editing_device = None
        self.edKey.setText("")
        self.edName.setText("")
        self.edWhat.setText("")
        self.spHoles.setValue(0)
        self.edSize.setText("")
        self.edTask.setText("")
        self._clear_coord_rows()

    def render(self, st: dict):
        """Update display with current status."""
        # Update XY position
        xy = st.get("xy_table", {})
        if xy:
            x = xy.get("x", 0)
            y = xy.get("y", 0)
            self.lblPos.setText(f"X: {x:.2f}  Y: {y:.2f}")

        # Refresh device list
        try:
            devices = self.api.devices()
            if devices != self._devices:
                self._devices = devices
                self._rebuild_device_list(devices)
        except Exception:
            pass


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
        self.tabSettings = SettingsTab(self.api)

        # Add ALL tabs at startup to keep geometry stable (prevents touch offset issues)
        tabs.addTab(self.tabStart, "START")
        tabs.addTab(self.tabWork, "WORK")
        tabs.addTab(self.tabService, "SERVICE")
        tabs.addTab(self.tabSettings, "SETTINGS")

        # Hide SERVICE and SETTINGS tabs initially
        # Use setTabVisible if available (Qt 5.15+), otherwise use tab removal
        self._use_tab_visible = hasattr(tabs.tabBar(), 'setTabVisible')
        if self._use_tab_visible:
            tabs.tabBar().setTabVisible(2, False)  # SERVICE
            tabs.tabBar().setTabVisible(3, False)  # SETTINGS
        else:
            # Fallback: remove tabs but keep references (will add back on unlock)
            tabs.removeTab(3)  # Remove SETTINGS first (higher index)
            tabs.removeTab(2)  # Remove SERVICE

        self.tabs = tabs
        self._service_tab_visible = False
        self._settings_tab_visible = False
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
        """Show SERVICE and SETTINGS tabs (unlock them)."""
        if self._service_tab_visible:
            return

        if self._use_tab_visible:
            # Qt 5.15+: just show the hidden tabs
            self.tabs.tabBar().setTabVisible(2, True)  # SERVICE
            self.tabs.tabBar().setTabVisible(3, True)  # SETTINGS
        else:
            # Fallback: add tabs back
            self.tabs.addTab(self.tabService, "SERVICE")
            self.tabs.addTab(self.tabSettings, "SETTINGS")

        self._service_tab_visible = True
        self._settings_tab_visible = True

        # Force UI update to show new tabs immediately
        self.tabs.tabBar().updateGeometry()
        self.tabs.tabBar().update()
        self.tabs.update()

        print("[UI] SERVICE and SETTINGS tabs unlocked (pedal held 4s)")

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
        for tab in (self.tabWork, self.tabStart, self.tabService, self.tabSettings):
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
