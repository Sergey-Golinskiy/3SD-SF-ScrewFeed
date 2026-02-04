#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScrewDrive TouchDesk - PyQt5 Desktop UI
Matches the web UI style and uses the screwdrive API.
"""
import os
import sys
import socket
import time
import requests
from functools import partial

# EGLFS setup for Raspberry Pi without X11
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "eglfs")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PyQt5.QtCore import Qt, QTimer, QCoreApplication, QThread, pyqtSignal
from PyQt5.QtWidgets import QStackedWidget
QCoreApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
from PyQt5.QtGui import QFont, QCursor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QLabel, QPushButton, QFrame, QComboBox, QSpinBox, QSizePolicy,
    QScrollArea, QProgressBar
)

# ================== Config ==================
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000/api")
POLL_MS = 1000
BORDER_W = 8


def pluralize_gvynt(n: int) -> str:
    """Ukrainian pluralization for 'гвинт' (screw)."""
    n = abs(n)
    if 11 <= n % 100 <= 19:
        return "гвинтів"
    last_digit = n % 10
    if last_digit == 1:
        return "гвинт"
    elif 2 <= last_digit <= 4:
        return "гвинти"
    else:
        return "гвинтів"


# ================== Colors (matching web UI) ==================
COLORS = {
    'bg_primary': '#121212',
    'bg_secondary': '#1e1e1e',
    'bg_card': '#252525',
    'bg_input': '#2a2a2a',
    'border': '#3a3a3a',
    'border_light': '#4a4a4a',
    'text': '#e0e0e0',
    'text_secondary': '#b0b0b0',
    'text_muted': '#808080',
    'blue': '#5a9fd4',
    'blue_hover': '#4a8fc4',
    'green': '#6fcf97',
    'green_bg': '#1a3a2a',
    'red': '#eb5757',
    'red_bg': '#3a1a1a',
    'yellow': '#f2c94c',
    'orange': '#f2994a',
}

# ================== HTTP Client ==================
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ApiClient:
    """HTTP client for screwdrive API."""

    def _get(self, path: str, timeout: int = 5):
        url = f"{API_BASE}/{path.lstrip('/')}"
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload=None, timeout: int = 10):
        url = f"{API_BASE}/{path.lstrip('/')}"
        r = requests.post(url, json=payload or {}, timeout=timeout)
        # Better error handling - include API error details in exception
        if not r.ok:
            try:
                error_data = r.json()
                error_msg = error_data.get('error', 'Unknown error')
                details = error_data.get('details', '')
                if details:
                    raise requests.HTTPError(f"{error_msg}: {details}", response=r)
                raise requests.HTTPError(error_msg, response=r)
            except ValueError:
                # JSON parsing failed, use standard error
                r.raise_for_status()
        return r.json()

    # Status
    def status(self):
        return self._get("status")

    # Devices
    def devices(self):
        return self._get("devices")

    def device(self, key: str):
        return self._get(f"devices/{key}")

    # Relays
    def relays(self):
        return self._get("relays")

    def relay_set(self, name: str, state: str, duration: float = None):
        data = {"state": state}
        if duration:
            data["duration"] = duration
        return self._post(f"relays/{name}", data)

    # Sensors
    def sensors(self):
        return self._get("sensors")

    def sensor(self, name: str):
        return self._get(f"sensors/{name}")

    def sensors_safety(self):
        return self._get("sensors/safety")

    # XY Table
    def xy_status(self):
        return self._get("xy/status")

    def xy_home(self, axis: str = None):
        data = {"axis": axis} if axis else {}
        return self._post("xy/home", data, timeout=30)

    def xy_move(self, x: float, y: float, feed: float = 5000):
        return self._post("xy/move", {"x": x, "y": y, "feed": feed}, timeout=30)

    def xy_stop(self):
        return self._post("xy/stop")

    def xy_estop(self):
        return self._post("xy/estop")

    def xy_command(self, command: str):
        """Send raw G-code command to XY table."""
        return self._post("xy/command", {"command": command}, timeout=10)

    def xy_disable_motors(self):
        """Disable stepper motors (M18)."""
        return self.xy_command("M18")

    def xy_enable_motors(self):
        """Enable stepper motors (M17)."""
        return self.xy_command("M17")

    def xy_jog(self, dx: float = 0, dy: float = 0, feed: float = 5000):
        """Jog XY table by offset."""
        return self._post("xy/jog", {"dx": dx, "dy": dy, "feed": feed}, timeout=30)

    # Cycle
    def cycle_estop(self):
        return self._post("cycle/estop")

    def cycle_clear_estop(self):
        return self._post("cycle/clear_estop")

    # Work Offsets (G92-like)
    def get_offsets(self):
        """Get current work offsets."""
        return self._get("offsets")

    def set_offsets(self, x: float = None, y: float = None):
        """Set work offsets."""
        data = {}
        if x is not None:
            data["x"] = x
        if y is not None:
            data["y"] = y
        return self._post("offsets", data)

    # UI State Sync
    def get_ui_state(self):
        return self._get("ui/state")

    def set_ui_state(self, state_data: dict):
        state_data["source"] = "desktop"
        return self._post("ui/state", state_data)

    def select_device(self, device_key: str):
        return self._post("ui/select-device", {"device": device_key, "source": "desktop"})


# ================== UI Helpers ==================
def make_card(title: str = None) -> QFrame:
    """Create a styled card frame."""
    box = QFrame()
    box.setObjectName("card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)
    if title:
        t = QLabel(title)
        t.setObjectName("cardTitle")
        lay.addWidget(t)
    return box


def big_button(text: str, style: str = "primary") -> QPushButton:
    """Create a large styled button."""
    btn = QPushButton(text)
    btn.setObjectName(f"btn_{style}")
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    btn.setMinimumHeight(120)
    return btn


# ================== Initialization Worker ==================
class InitWorker(QThread):
    """Worker thread for initialization sequence."""
    progress = pyqtSignal(str, int)  # message, progress percent
    finished_ok = pyqtSignal(str)  # warnings string (empty if none)
    finished_error = pyqtSignal(str)

    def __init__(self, api: ApiClient, device: dict):
        super().__init__()
        self.api = api
        self.device = device
        self._abort = False

    def abort(self):
        self._abort = True

    def _sync_progress(self, message: str, progress_percent: int):
        """Sync progress to server for Web UI to see."""
        try:
            self.api.set_ui_state({
                "cycle_state": "INITIALIZING",
                "message": message,
                "progress_percent": progress_percent,
                "current_step": message
            })
        except Exception:
            pass  # Don't fail init if sync fails

    def _check_driver_alarms(self) -> tuple:
        """
        Check if any motor driver alarm is active.
        Returns (alarm_x: bool, alarm_y: bool) tuple.
        """
        alarm_x = False
        alarm_y = False
        try:
            sensors = self.api.sensors()
            alarm_x = sensors.get("alarm_x") == "ACTIVE"
            alarm_y = sensors.get("alarm_y") == "ACTIVE"
        except Exception as e:
            print(f"WARNING: Failed to check driver alarms: {e}")
        return alarm_x, alarm_y

    def _power_cycle_drivers(self, reset_x: bool = True, reset_y: bool = True) -> None:
        """
        Power cycle motor drivers by toggling power relays.
        Turns power OFF for 1 second, then back ON.

        Args:
            reset_x: Reset X axis driver
            reset_y: Reset Y axis driver
        """
        msg_parts = []
        if reset_x:
            msg_parts.append("X")
        if reset_y:
            msg_parts.append("Y")

        if not msg_parts:
            return

        axis_str = " та ".join(msg_parts)
        self.progress.emit(f"Перезапуск драйвера {axis_str}...", 0)
        self._sync_progress(f"Перезапуск драйвера {axis_str}...", 0)

        try:
            # Turn power OFF (relay ON due to inverted logic)
            if reset_x:
                self.api.relay_set("r09_pwr_x", "on")
            if reset_y:
                self.api.relay_set("r10_pwr_y", "on")

            time.sleep(1.0)  # Wait 1 second with power off

            # Turn power ON (relay OFF)
            if reset_x:
                self.api.relay_set("r09_pwr_x", "off")
            if reset_y:
                self.api.relay_set("r10_pwr_y", "off")

            time.sleep(0.5)  # Wait for driver to stabilize

        except Exception as e:
            print(f"WARNING: Failed to power cycle drivers: {e}")

    def _check_and_reset_alarms(self) -> bool:
        """
        Check for driver alarms and reset them by power cycling.
        Returns True if alarm was found (and reset attempted), False if no alarm.
        """
        alarm_x, alarm_y = self._check_driver_alarms()

        if alarm_x or alarm_y:
            self._power_cycle_drivers(reset_x=alarm_x, reset_y=alarm_y)
            return True

        return False

    def run(self):
        """
        Run initialization with automatic driver alarm recovery.

        If alarm is detected at any point:
        - Power cycle the affected driver(s) for 1 second
        - Restart initialization from the beginning
        - Maximum 3 retry attempts before giving up
        """
        MAX_RETRIES = 3
        retry_count = 0

        while retry_count < MAX_RETRIES:
            try:
                if self._abort:
                    return

                # Step 0: Check and reset driver alarms if needed
                self.progress.emit("Перевірка алармів драйверів...", 2)
                self._sync_progress("Перевірка алармів драйверів...", 2)

                if self._check_and_reset_alarms():
                    # Alarm was reset, notify and continue
                    self.progress.emit("Аларм скинуто, продовжуємо...", 3)
                    time.sleep(0.5)

                # Step 0.1: Check E-STOP
                self.progress.emit("Перевірка аварійної кнопки...", 5)
                self._sync_progress("Перевірка аварійної кнопки...", 5)
                safety = self.api.sensors_safety()
                if safety.get("estop_pressed"):
                    raise Exception("Аварійна кнопка натиснута! Відпустіть її.")

                if self._abort:
                    return

                # Step 0.2: Check XY connection
                self.progress.emit("Перевірка підключення XY столу...", 10)
                self._sync_progress("Перевірка підключення XY столу...", 10)
                xy_status = self.api.xy_status()
                if not xy_status.get("connected"):
                    raise Exception("XY стіл не підключено!")

                if self._abort:
                    return

                # Step 0.3: Enable stepper motors
                self.progress.emit("Увімкнення моторів...", 12)
                self._sync_progress("Увімкнення моторів...", 12)
                try:
                    self.api.xy_enable_motors()
                except Exception as e:
                    print(f"WARNING: Failed to enable motors: {e}")
                time.sleep(0.2)

                if self._abort:
                    return

                # Step 1: Release brakes
                self.progress.emit("Відпускання гальм...", 15)
                self._sync_progress("Відпускання гальм...", 15)
                relays = self.api.relays()

                if relays.get("r02_brake_x") != "ON":
                    self.api.relay_set("r02_brake_x", "on")
                    time.sleep(0.3)

                if relays.get("r03_brake_y") != "ON":
                    self.api.relay_set("r03_brake_y", "on")
                    time.sleep(0.3)

                if self._abort:
                    return

                # Check alarms before homing
                if self._check_and_reset_alarms():
                    retry_count += 1
                    self.progress.emit(f"Аларм виявлено, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                    continue

                # Step 2: Homing
                self.progress.emit("Виконується хомінг XY столу...", 25)
                self._sync_progress("Виконується хомінг XY столу...", 25)
                home_resp = self.api.xy_home()
                if home_resp.get("status") != "homed":
                    raise Exception("Не вдалося запустити хомінг")

                # Wait for homing to complete (15 seconds timeout)
                start_time = time.time()
                homing_alarm = False
                while time.time() - start_time < 15:
                    if self._abort:
                        return

                    # Check for alarms during homing
                    alarm_x, alarm_y = self._check_driver_alarms()
                    if alarm_x or alarm_y:
                        self._power_cycle_drivers(reset_x=alarm_x, reset_y=alarm_y)
                        homing_alarm = True
                        break

                    xy = self.api.xy_status()
                    pos = xy.get("position", xy)  # Handle both formats
                    if pos.get("x_homed") and pos.get("y_homed"):
                        break
                    state = (xy.get("state") or "").lower()
                    if state in ("error", "estop"):
                        raise Exception(f"Помилка хомінгу: {xy.get('last_error', state)}")
                    time.sleep(0.2)
                else:
                    raise Exception("Хомінг не завершено за 15 секунд")

                # If alarm during homing, restart
                if homing_alarm:
                    retry_count += 1
                    self.progress.emit(f"Аларм під час хомінгу, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                    continue

                # Short delay after homing for motor driver stabilization
                time.sleep(0.5)

                # Check alarms after homing
                if self._check_and_reset_alarms():
                    retry_count += 1
                    self.progress.emit(f"Аларм після хомінгу, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                    continue

                if self._abort:
                    return

                # Step 3: Check cylinder sensors
                self.progress.emit("Перевірка датчиків циліндра...", 40)
                self._sync_progress("Перевірка датчиків циліндра...", 40)
                ger_up = self.api.sensor("ger_c2_up")
                ger_down = self.api.sensor("ger_c2_down")

                if ger_up.get("state") != "ACTIVE":
                    raise Exception("Датчик GER_C2_UP не активний")
                if ger_down.get("state") == "ACTIVE":
                    raise Exception("Датчик GER_C2_DOWN активний")

                if self._abort:
                    return

                # Step 4: Lower cylinder test
                self.progress.emit("Опускання циліндра...", 50)
                self._sync_progress("Опускання циліндра...", 50)
                self.api.relay_set("r04_c2", "on")

                # Wait for cylinder down (5 seconds)
                start_time = time.time()
                while time.time() - start_time < 5:
                    if self._abort:
                        self.api.relay_set("r04_c2", "off")
                        return
                    sensor = self.api.sensor("ger_c2_down")
                    if sensor.get("state") == "ACTIVE":
                        break
                    time.sleep(0.1)
                else:
                    self.api.relay_set("r04_c2", "off")
                    raise Exception("Циліндр не опустився за 5 секунд")

                if self._abort:
                    self.api.relay_set("r04_c2", "off")
                    return

                # Step 5: Raise cylinder
                self.progress.emit("Піднімання циліндра...", 60)
                self._sync_progress("Піднімання циліндра...", 60)
                self.api.relay_set("r04_c2", "off")

                # Wait for cylinder up (5 seconds)
                start_time = time.time()
                while time.time() - start_time < 5:
                    if self._abort:
                        return
                    sensor = self.api.sensor("ger_c2_up")
                    if sensor.get("state") == "ACTIVE":
                        break
                    time.sleep(0.1)
                else:
                    raise Exception("Циліндр не піднявся за 5 секунд")

                if self._abort:
                    return

                # Step 6: Set task relays
                self.progress.emit("Вибір задачі для закручування...", 75)
                self._sync_progress("Вибір задачі для закручування...", 75)
                task = self.device.get("task", "0")

                if task == "0":
                    self.api.relay_set("r07_di5_tsk0", "off")
                    self.api.relay_set("r08_di6_tsk1", "off")
                elif task == "1":
                    self.api.relay_set("r08_di6_tsk1", "off")
                    self.api.relay_set("r07_di5_tsk0", "on")
                elif task == "2":
                    self.api.relay_set("r07_di5_tsk0", "off")
                    self.api.relay_set("r08_di6_tsk1", "on")
                elif task == "3":
                    self.api.relay_set("r07_di5_tsk0", "on")
                    self.api.relay_set("r08_di6_tsk1", "on")
                time.sleep(0.3)

                if self._abort:
                    return

                # Check alarms before move
                if self._check_and_reset_alarms():
                    retry_count += 1
                    self.progress.emit(f"Аларм перед рухом, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                    continue

                # Step 7: Move to operator position (physical coordinates)
                # Device's work_x/work_y are stored as physical coordinates (relative to limit switches)
                self.progress.emit("Виїзд до оператора...", 85)
                self._sync_progress("Виїзд до оператора...", 85)
                work_x = self.device.get("work_x")
                work_y = self.device.get("work_y")
                work_feed = self.device.get("work_feed", 5000)

                if work_x is None or work_y is None:
                    raise Exception("Робоча позиція не задана для цього девайсу")

                # Use physical coordinates directly (no offset applied)
                move_resp = self.api.xy_move(work_x, work_y, work_feed)
                if move_resp.get("status") != "ok":
                    # Check if alarm caused the failure
                    if self._check_and_reset_alarms():
                        retry_count += 1
                        self.progress.emit(f"Аларм під час руху, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                        continue
                    raise Exception("Не вдалося виїхати до робочої позиції")

                # Wait for move to complete
                time.sleep(0.5)

                # Final alarm check
                if self._check_and_reset_alarms():
                    retry_count += 1
                    self.progress.emit(f"Аларм після руху, перезапуск (спроба {retry_count}/{MAX_RETRIES})...", 0)
                    continue

                # Success!
                self.progress.emit("Ініціалізація завершена!", 100)
                self.finished_ok.emit("")
                return

            except Exception as e:
                # Safety: turn off cylinder relay
                try:
                    self.api.relay_set("r04_c2", "off")
                except:
                    pass
                self.finished_error.emit(str(e))
                return

        # Max retries exceeded
        self.finished_error.emit(f"Не вдалося завершити ініціалізацію після {MAX_RETRIES} спроб скидання алармів драйверів.")


# ================== Cycle Worker ==================
class CycleWorker(QThread):
    """Worker thread for screwing cycle execution."""
    progress = pyqtSignal(str, int, int, int)  # message, holes_completed, total_holes, progress_percent
    finished_ok = pyqtSignal(int)  # holes_completed
    finished_error = pyqtSignal(str)

    # Special error for driver alarm - requires device removal and reinit
    DRIVER_ALARM_ERROR = "DRIVER_ALARM"
    # Special error for area sensor (light barrier) triggered
    AREA_BLOCKED_ERROR = "AREA_BLOCKED"

    def __init__(self, api: ApiClient, device: dict):
        super().__init__()
        self.api = api
        self.device = device
        self._abort = False
        self._area_monitoring_active = False  # Light barrier monitoring

    def abort(self):
        self._abort = True

    def _check_area_sensor(self) -> bool:
        """
        Check if light barrier (area_sensor) is clear.
        Returns True if clear, False if blocked.
        Only checks if area monitoring is active.
        """
        if not self._area_monitoring_active:
            return True
        try:
            resp = self.api.sensor("area_sensor")
            if resp.get("state") == "ACTIVE":
                # Barrier blocked - someone in work area
                return False
            return True
        except Exception as e:
            print(f"WARNING: Area sensor check failed: {e}")
            return False  # Fail-safe: assume blocked if can't check

    def _check_driver_alarms(self) -> str:
        """
        Check if any motor driver alarm is active.
        Returns alarm message if alarm is active, empty string if OK.

        Called during cycle execution to detect driver failures.
        IMPORTANT: This must be called frequently during cycle to detect alarms quickly.
        """
        try:
            sensors = self.api.sensors()

            # Check X axis alarm (GPIO 2) - ACTIVE means alarm triggered
            if sensors.get("alarm_x") == "ACTIVE":
                return "АВАРІЯ: Аларм драйвера осі X!"

            # Check Y axis alarm (GPIO 3) - ACTIVE means alarm triggered
            if sensors.get("alarm_y") == "ACTIVE":
                return "АВАРІЯ: Аларм драйвера осі Y!"

        except Exception as e:
            print(f"WARNING: Failed to check driver alarms: {e}")
            pass  # If we can't check, continue operation

        return ""

    def _check_alarm_and_raise(self):
        """
        Check for driver alarms and raise exception if detected.
        Used to add alarm checks between operations.
        """
        alarm = self._check_driver_alarms()
        if alarm:
            self._full_emergency_shutdown(alarm)
            raise Exception(f"{self.DRIVER_ALARM_ERROR}:{alarm}")

    def _emergency_stop_xy(self):
        """
        Emergency stop for XY table.
        Cancels all commands on Raspberry Pi Slave.
        Sends multiple stop commands to ensure it's received.
        """
        try:
            # Send E-STOP to XY table (Slave Pi) - try multiple times
            for _ in range(3):
                try:
                    self.api._post("/api/xy/estop", {})
                    break
                except Exception:
                    time.sleep(0.1)
        except Exception as e:
            print(f"WARNING: Failed to send E-STOP to Slave Pi: {e}")

        # Also try to stop any movement
        try:
            self.api._post("/api/emergency_stop", {})
        except Exception:
            pass

    def _full_emergency_shutdown(self, alarm_msg: str):
        """
        Full emergency shutdown when driver alarm detected.

        1. Stop XY table (cancel all commands on Slave Pi)
        2. Turn off all dangerous relays
        3. Set error state
        """
        # 1. Stop XY table immediately
        self._emergency_stop_xy()

        # 2. Safety shutdown - turn off dangerous relays
        self._safety_shutdown()

        # 3. Notify about alarm
        self._sync_progress(alarm_msg, 0, 0)

    def _sync_progress(self, message: str, holes: int, total: int):
        """Sync progress to server."""
        pct = int((holes / total) * 100) if total > 0 else 0
        try:
            self.api.set_ui_state({
                "cycle_state": "RUNNING",
                "message": message,
                "holes_completed": holes,
                "total_holes": total,
                "progress_percent": pct,
                "current_step": message
            })
        except Exception:
            pass

    def _wait_for_move(self, timeout: float = 30.0) -> bool:
        """
        Wait for XY table to finish moving.
        Also checks for driver alarms and area sensor during movement.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._abort:
                return False

            # Check for driver alarms during movement
            alarm = self._check_driver_alarms()
            if alarm:
                self._full_emergency_shutdown(alarm)
                raise Exception(f"{self.DRIVER_ALARM_ERROR}:{alarm}")

            # Check area sensor (light barrier)
            if not self._check_area_sensor():
                raise Exception(self.AREA_BLOCKED_ERROR)

            try:
                status = self.api.xy_status()
                state = (status.get("state") or "").lower()
                if state == "ready":
                    return True
                if state in ("error", "estop"):
                    raise Exception(f"XY error: {state}")
            except Exception as e:
                if "error" in str(e).lower() or "estop" in str(e).lower():
                    raise
                if self.AREA_BLOCKED_ERROR in str(e):
                    raise
            time.sleep(0.1)
        return False

    def _wait_for_sensor(self, sensor: str, expected: str, timeout: float = 10.0) -> bool:
        """
        Wait for sensor to reach expected state.
        Also checks for driver alarms and area sensor while waiting.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._abort:
                return False

            # Check for driver alarms while waiting
            alarm = self._check_driver_alarms()
            if alarm:
                self._full_emergency_shutdown(alarm)
                raise Exception(f"{self.DRIVER_ALARM_ERROR}:{alarm}")

            # Check area sensor (light barrier)
            if not self._check_area_sensor():
                raise Exception(self.AREA_BLOCKED_ERROR)

            try:
                resp = self.api.sensor(sensor)
                if resp.get("state") == expected:
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def _perform_screwing(self) -> bool:
        """
        Perform single screw operation.
        Checks for driver alarms between each step to detect failures quickly.
        """
        # Check for alarms before starting screwing
        self._check_alarm_and_raise()

        # 1. Feed screw with retry logic (max 3 attempts)
        screw_detected = False
        for attempt in range(3):
            if self._abort:
                return False

            # Check for alarms before each attempt
            self._check_alarm_and_raise()

            # Pulse R01 (200ms) to feed screw
            self.api.relay_set("r01_pit", "pulse", 0.2)
            # Wait for screw sensor (also checks alarms)
            screw_detected = self._wait_for_sensor("ind_scrw", "ACTIVE", 1.0)
            if screw_detected:
                break

        if not screw_detected:
            raise Exception("Гвинт не виявлено після 3 спроб")

        # Check for alarms before torque mode
        self._check_alarm_and_raise()

        # 2. Turn ON R06 (torque mode)
        self.api.relay_set("r06_di1_pot", "on")

        # Check for alarms before lowering cylinder
        self._check_alarm_and_raise()

        # 3. Lower cylinder (R04 ON)
        self.api.relay_set("r04_c2", "on")

        # 4. Wait for DO2_OK (torque reached) with 2 second timeout
        # _wait_for_sensor already checks for alarms
        torque_reached = self._wait_for_sensor("do2_ok", "ACTIVE", 2.0)

        if not torque_reached:
            # Safe shutdown and return to operator
            self.api.relay_set("r04_c2", "off")
            self.api.relay_set("r06_di1_pot", "off")
            self._wait_for_sensor("ger_c2_up", "ACTIVE", 5.0)
            self.api.relay_set("r05_di4_free", "pulse", 0.2)
            raise Exception("TORQUE_NOT_REACHED")

        # Check for alarms after torque reached
        self._check_alarm_and_raise()

        # SUCCESS PATH:
        # 5. Turn OFF R06 (torque mode)
        self.api.relay_set("r06_di1_pot", "off")

        # 6. Raise cylinder (R04 OFF)
        self.api.relay_set("r04_c2", "off")

        # 7. Free run pulse - R05 (200ms)
        self.api.relay_set("r05_di4_free", "pulse", 0.2)

        # 8. Wait for cylinder to go up (also checks alarms)
        if not self._wait_for_sensor("ger_c2_up", "ACTIVE", 5.0):
            raise Exception("Циліндр не піднявся за 5 секунд")

        # Final alarm check after screwing complete
        self._check_alarm_and_raise()

        return True

    def _safety_shutdown(self):
        """Turn off dangerous relays."""
        try:
            self.api.relay_set("r04_c2", "off")
        except:
            pass
        try:
            self.api.relay_set("r06_di1_pot", "off")
        except:
            pass

    def _area_barrier_shutdown(self):
        """
        Shutdown for light barrier trigger.
        - R04 OFF (cylinder up)
        - R06 OFF (screwdriver motor off)
        - R05 pulse (free run to stop spindle)
        - Disable stepper motors (M18)
        """
        try:
            self.api.relay_set("r04_c2", "off")
        except:
            pass
        try:
            self.api.relay_set("r06_di1_pot", "off")
        except:
            pass
        try:
            self.api.relay_set("r05_di4_free", "pulse", 0.3)
        except:
            pass
        # Disable stepper motors
        try:
            self.api.xy_disable_motors()
        except:
            pass

    def run(self):
        try:
            steps = self.device.get("steps", [])
            if not steps:
                raise Exception("Девайс не має координат")

            work_steps = [s for s in steps if (s.get("type") or "").lower() == "work"]
            total_holes = len(work_steps)
            holes_completed = 0

            # Load work offsets (G92-like) - device coordinates are relative to work zero
            try:
                offsets = self.api.get_offsets()
                offset_x = offsets.get("x", 0.0)
                offset_y = offsets.get("y", 0.0)
            except Exception:
                offset_x = 0.0
                offset_y = 0.0

            # Check E-STOP before starting
            safety = self.api.sensors_safety()
            if safety.get("estop_pressed"):
                raise Exception("Аварійна кнопка натиснута!")

            # Check for driver alarms before starting cycle
            # If alarm is active, stop immediately - device must be removed and machine reinitialized
            alarm = self._check_driver_alarms()
            if alarm:
                raise Exception(f"{self.DRIVER_ALARM_ERROR}:{alarm}\n"
                               "Вийміть деталь та виконайте переініціалізацію машини.")

            self.progress.emit(f"Цикл запущено. Винтів: 0 / {total_holes}", 0, total_holes, 0)
            self._sync_progress(f"Цикл запущено. Винтів: 0 / {total_holes}", 0, total_holes)

            # Process each step
            for i, step in enumerate(steps):
                if self._abort:
                    raise Exception("Цикл перервано")

                # Check for alarms at the start of each step
                self._check_alarm_and_raise()

                step_type = (step.get("type") or "free").lower()
                step_x = float(step.get("x", 0))
                step_y = float(step.get("y", 0))
                step_feed = float(step.get("feed", 5000))

                # Apply offset: device coords are relative to work zero
                physical_x = step_x + offset_x
                physical_y = step_y + offset_y

                if step_type == "free":
                    # Free movement - just move
                    self.progress.emit(f"Переміщення X:{step_x:.1f} Y:{step_y:.1f}", holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)

                    # Check alarm before sending move command
                    self._check_alarm_and_raise()

                    resp = self.api.xy_move(physical_x, physical_y, step_feed)
                    if resp.get("status") != "ok":
                        raise Exception("Помилка переміщення")

                    # _wait_for_move also checks alarms
                    self._wait_for_move()

                elif step_type == "work":
                    # Work position - move and screw

                    # Enable area monitoring on first work step
                    if not self._area_monitoring_active:
                        self._area_monitoring_active = True
                        self.progress.emit("Контроль світлової завіси увімкнено", holes_completed, total_holes,
                                          int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)

                    msg = f"Закручування ({holes_completed + 1}/{total_holes}) X:{step_x:.1f} Y:{step_y:.1f}"
                    self.progress.emit(msg, holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)
                    self._sync_progress(msg, holes_completed, total_holes)

                    # Check alarm before move
                    self._check_alarm_and_raise()

                    # Check area sensor before move
                    if not self._check_area_sensor():
                        raise Exception(self.AREA_BLOCKED_ERROR)

                    # Move to position (with offset)
                    resp = self.api.xy_move(physical_x, physical_y, step_feed)
                    if resp.get("status") != "ok":
                        raise Exception("Помилка переміщення")

                    # _wait_for_move also checks alarms and area sensor
                    self._wait_for_move()

                    # _perform_screwing has alarm checks inside
                    self._perform_screwing()

                    holes_completed += 1
                    msg = f"Закручено: {holes_completed} / {total_holes}"
                    self.progress.emit(msg, holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)
                    self._sync_progress(msg, holes_completed, total_holes)

            # Disable area monitoring before returning to operator
            self._area_monitoring_active = False

            # Cycle complete - return to operator
            self.progress.emit("Повернення до оператора...", holes_completed, total_holes, 100)

            work_x = self.device.get("work_x")
            work_y = self.device.get("work_y")
            work_feed = self.device.get("work_feed", 5000)

            if work_x is not None and work_y is not None:
                self.api.xy_move(work_x, work_y, work_feed)
                self._wait_for_move()

            self.finished_ok.emit(holes_completed)

        except Exception as e:
            error_str = str(e)

            # Disable area monitoring on any error
            self._area_monitoring_active = False

            # Special handling for driver alarm errors
            if self.DRIVER_ALARM_ERROR in error_str:
                # Full emergency shutdown already done in _check_driver_alarms
                # Add instruction for operator
                error_msg = (
                    "🚨 АВАРІЯ ДРАЙВЕРА МОТОРА!\n"
                    f"{error_str.split(':', 1)[-1] if ':' in error_str else error_str}\n\n"
                    "Дії оператора:\n"
                    "1. Вийміть деталь з робочої зони\n"
                    "2. Перевірте стан машини\n"
                    "3. Виконайте переініціалізацію"
                )
                self.finished_error.emit(error_msg)

            # Special handling for area sensor (light barrier) blocked
            elif self.AREA_BLOCKED_ERROR in error_str:
                # Safety shutdown with R05 pulse
                self._area_barrier_shutdown()

                # Log the event
                try:
                    from screwdrive.core.logger import get_logger, LogLevel
                    syslog = get_logger()
                    syslog.sensor("Світлова завіса спрацювала! Закручування зупинено.",
                                 level=LogLevel.WARNING, source="area_sensor")
                except Exception:
                    pass

                # Don't auto-return - stay in place, UI will show dialog
                self.finished_error.emit(self.AREA_BLOCKED_ERROR)

            else:
                self._safety_shutdown()
                self.finished_error.emit(error_str)


# ================== Start/Work Tab ==================
class StartWorkTab(QWidget):
    """Combined Start/Work tab - switches between modes after initialization."""

    MODE_START = 0
    MODE_WORK = 1

    # Signal to notify MainWindow to change tab name
    tabNameChanged = pyqtSignal(str)

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._devices = []
        self._selected_device = None
        self._cycle_state = "IDLE"
        self._initialized = False
        self._last_server_state_time = 0
        self._total_cycles = 0
        self._holes_completed = 0
        self._total_holes = 0
        self._device_task = "-"
        self._device_torque = None
        self._init_worker = None
        self._cycle_worker = None
        self._current_mode = self.MODE_START
        self._pedal_was_pressed = False  # Track pedal state for edge detection
        self._state_restored = False  # Track if state was restored from server
        self._cycle_start_time = None  # Track cycle start time
        self._cycle_times = []  # List of cycle times for average calculation
        self._estop_dialog = None  # E-STOP fullscreen dialog

        self._setup_ui()
        self._restore_state_from_server()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stacked widget for mode switching
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # Create both mode widgets
        self._setup_start_mode()
        self._setup_work_mode()

        self.stack.addWidget(self.start_widget)
        self.stack.addWidget(self.work_widget)
        self.stack.setCurrentIndex(self.MODE_START)

    def _setup_start_mode(self):
        """Setup START mode - device selection + init button."""
        self.start_widget = QWidget()
        layout = QHBoxLayout(self.start_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # Left column (33%) - Device list
        left = QVBoxLayout()
        left.setSpacing(12)

        self.devCard = make_card("Вибір девайсу")
        dev_lay = self.devCard.layout()

        self.devScroll = QScrollArea()
        self.devScroll.setWidgetResizable(True)
        self.devScroll.setMinimumWidth(250)
        self.devList = QWidget()
        self.devListLay = QVBoxLayout(self.devList)
        self.devListLay.setContentsMargins(0, 0, 0, 0)
        self.devListLay.setSpacing(8)
        self.devScroll.setWidget(self.devList)
        dev_lay.addWidget(self.devScroll)

        left.addWidget(self.devCard)

        # Right column (66%) - Init button and status
        right = QVBoxLayout()
        right.setSpacing(16)

        # Status area at top
        self.startStatusCard = make_card("Статус")
        status_lay = self.startStatusCard.layout()

        self.lblStartDevice = QLabel("Девайс: не вибрано")
        self.lblStartDevice.setObjectName("statusValue")
        self.lblStartDevice.setAlignment(Qt.AlignCenter)
        status_lay.addWidget(self.lblStartDevice)

        # Task and Torque row
        task_torque_row = QHBoxLayout()
        task_torque_row.setSpacing(40)

        self.lblStartTask = QLabel("Таска: -")
        self.lblStartTask.setObjectName("statusTaskTorque")
        self.lblStartTask.setAlignment(Qt.AlignCenter)
        task_torque_row.addWidget(self.lblStartTask)

        self.lblStartTorque = QLabel("Момент: - Nm")
        self.lblStartTorque.setObjectName("statusTaskTorque")
        self.lblStartTorque.setAlignment(Qt.AlignCenter)
        task_torque_row.addWidget(self.lblStartTorque)

        status_lay.addLayout(task_torque_row)

        self.lblStartMessage = QLabel("Виберіть девайс зі списку зліва")
        self.lblStartMessage.setObjectName("statusMessage")
        self.lblStartMessage.setWordWrap(True)
        self.lblStartMessage.setAlignment(Qt.AlignCenter)
        status_lay.addWidget(self.lblStartMessage)

        # Progress bar
        self.startProgressBar = QProgressBar()
        self.startProgressBar.setMinimum(0)
        self.startProgressBar.setMaximum(100)
        self.startProgressBar.setValue(0)
        self.startProgressBar.setMinimumHeight(40)
        status_lay.addWidget(self.startProgressBar)

        right.addWidget(self.startStatusCard)

        # Big INIT button - takes all remaining space
        self.btnInit = QPushButton("ІНІЦІАЛІЗАЦІЯ")
        self.btnInit.setObjectName("btn_init_big")
        self.btnInit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btnInit.setMinimumHeight(300)
        self.btnInit.clicked.connect(self.on_init)
        self.btnInit.setEnabled(False)
        right.addWidget(self.btnInit, 1)

        # Layout ratio 33/66
        layout.addLayout(left, 1)
        layout.addLayout(right, 2)

        # Device buttons dict
        self._device_buttons = {}

    def _setup_work_mode(self):
        """Setup WORK mode - two big buttons for start/stop cycle."""
        self.work_widget = QWidget()
        layout = QVBoxLayout(self.work_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Top status bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(20)

        self.lblWorkDevice = QLabel("Девайс: -")
        self.lblWorkDevice.setObjectName("workStatusLabel")
        top_bar.addWidget(self.lblWorkDevice)

        top_bar.addStretch(1)

        self.lblWorkCounter = QLabel("Циклів: 0")
        self.lblWorkCounter.setObjectName("workCounterLabel")
        top_bar.addWidget(self.lblWorkCounter)

        top_bar.addStretch(1)

        self.lblWorkHoles = QLabel("Гвинтів: 0 / 0")
        self.lblWorkHoles.setObjectName("workStatusLabel")
        top_bar.addWidget(self.lblWorkHoles)

        top_bar.addStretch(1)

        self.lblWorkTask = QLabel("Таска: -")
        self.lblWorkTask.setObjectName("workStatusLabel")
        top_bar.addWidget(self.lblWorkTask)

        top_bar.addStretch(1)

        self.lblWorkTorque = QLabel("Момент: - Nm")
        self.lblWorkTorque.setObjectName("workStatusLabel")
        top_bar.addWidget(self.lblWorkTorque)

        layout.addLayout(top_bar)

        # Status message
        self.lblWorkMessage = QLabel("Готово до роботи. Натисніть СТАРТ ЗАКРУЧУВАННЯ.")
        self.lblWorkMessage.setObjectName("workMessage")
        self.lblWorkMessage.setWordWrap(True)
        self.lblWorkMessage.setAlignment(Qt.AlignCenter)
        self.lblWorkMessage.setMinimumHeight(60)
        layout.addWidget(self.lblWorkMessage)

        # Progress bar
        self.workProgressBar = QProgressBar()
        self.workProgressBar.setMinimum(0)
        self.workProgressBar.setMaximum(100)
        self.workProgressBar.setValue(0)
        self.workProgressBar.setMinimumHeight(50)
        self.workProgressBar.setObjectName("workProgressBar")
        layout.addWidget(self.workProgressBar)

        # Two big buttons row - take all remaining space
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        # START button (green, left)
        self.btnStartCycle = QPushButton("СТАРТ\nЗАКРУЧУВАННЯ")
        self.btnStartCycle.setObjectName("btn_work_start")
        self.btnStartCycle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btnStartCycle.setMinimumHeight(350)
        self.btnStartCycle.clicked.connect(self.on_start)
        btn_row.addWidget(self.btnStartCycle, 1)

        # STOP button (red, right)
        self.btnStopCycle = QPushButton("ЗУПИНИТИ\nЗАКРУЧУВАННЯ")
        self.btnStopCycle.setObjectName("btn_work_stop")
        self.btnStopCycle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btnStopCycle.setMinimumHeight(350)
        self.btnStopCycle.clicked.connect(self.on_stop_and_return)
        btn_row.addWidget(self.btnStopCycle, 1)

        layout.addLayout(btn_row, 1)

    def _get_counter_text(self) -> str:
        """Get counter text with average cycle time."""
        avg_time_str = ""
        if self._cycle_times:
            avg_time = sum(self._cycle_times) / len(self._cycle_times)
            avg_time_str = f" ({avg_time:.1f}с)"
        return f"Циклів: {self._total_cycles}{avg_time_str}"

    def switch_to_work_mode(self):
        """Switch to WORK mode after successful initialization."""
        self._current_mode = self.MODE_WORK
        self.stack.setCurrentIndex(self.MODE_WORK)
        self.tabNameChanged.emit("РОБОТА")

        # Update work mode labels
        self.lblWorkDevice.setText(f"Девайс: {self._selected_device}")
        self.lblWorkCounter.setText(self._get_counter_text())
        self.lblWorkHoles.setText(f"Гвинтів: 0 / {self._total_holes}")
        self.lblWorkTask.setText(f"Таска: {self._device_task}")
        torque_str = f"{self._device_torque} Nm" if self._device_torque is not None else "-"
        self.lblWorkTorque.setText(f"Момент: {torque_str}")
        self.lblWorkMessage.setText("Готово. Натисніть СТАРТ ЗАКРУЧУВАННЯ для початку циклу.")
        self.workProgressBar.setValue(0)

        # Enable start button
        self.btnStartCycle.setEnabled(True)

    def switch_to_start_mode(self):
        """Switch back to START mode."""
        self._current_mode = self.MODE_START
        self._initialized = False
        self.stack.setCurrentIndex(self.MODE_START)
        self.tabNameChanged.emit("СТАРТ")

        # Reset start mode
        self.btnInit.setEnabled(bool(self._selected_device))
        self.startProgressBar.setValue(0)
        self.lblStartMessage.setText("Виберіть девайс та натисніть ІНІЦІАЛІЗАЦІЯ")

    def _rebuild_devices(self, devices: list):
        """Rebuild device list buttons."""
        # Clear existing
        for i in reversed(range(self.devListLay.count())):
            w = self.devListLay.itemAt(i).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._device_buttons.clear()

        # Create buttons
        for dev in devices:
            key = dev.get("key", "")
            name = dev.get("name", key)
            holes = dev.get("holes", 1)

            btn = QPushButton(f"{key}\n{holes} {pluralize_gvynt(holes)}")
            btn.setObjectName("devButton")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(80)
            btn.clicked.connect(lambda _, k=key: self._select_device(k))

            self.devListLay.addWidget(btn)
            self._device_buttons[key] = btn

        self.devListLay.addStretch(1)
        self._update_device_styles()

    def _update_device_styles(self):
        """Update device button selection styles."""
        for key, btn in self._device_buttons.items():
            is_selected = key == self._selected_device
            btn.setProperty("selected", is_selected)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _restore_state_from_server(self):
        """Restore state from server on startup."""
        try:
            server_state = self.api.get_ui_state()
            if not server_state:
                return

            # Get saved state
            saved_device = server_state.get("selected_device")
            saved_initialized = server_state.get("initialized", False)
            saved_cycle_state = server_state.get("cycle_state", "IDLE")

            # If there's a saved device and it was initialized
            if saved_device and saved_initialized:
                # Load devices first
                try:
                    self._devices = self.api.devices()
                    self._rebuild_devices(self._devices)
                except Exception:
                    return

                # Check if device still exists
                device_exists = any(d.get("key") == saved_device for d in self._devices)
                if not device_exists:
                    return

                # Restore state
                self._selected_device = saved_device
                self._initialized = True
                self._cycle_state = saved_cycle_state
                self._total_cycles = server_state.get("cycles_completed", 0)
                self._holes_completed = server_state.get("holes_completed", 0)
                self._total_holes = server_state.get("total_holes", 0)

                # Get task and torque from device
                for dev in self._devices:
                    if dev.get("key") == saved_device:
                        self._device_task = dev.get("task", "-") or "-"
                        self._device_torque = dev.get("torque")
                        break

                # Update UI
                self._update_device_styles()
                self.lblStartDevice.setText(f"Девайс: {saved_device}")
                self.lblStartTask.setText(f"Таска: {self._device_task}")
                torque_str = f"{self._device_torque} Nm" if self._device_torque is not None else "-"
                self.lblStartTorque.setText(f"Момент: {torque_str}")

                # Switch to WORK mode if in working states
                # RUNNING is included - if app restarted during cycle, show WORK mode with paused state
                if saved_cycle_state in ("READY", "COMPLETED", "PAUSED", "RUNNING", "ERROR"):
                    # If was running when restarted, set to paused
                    if saved_cycle_state == "RUNNING":
                        self._cycle_state = "PAUSED"

                    self.switch_to_work_mode()
                    self.lblWorkDevice.setText(f"Девайс: {saved_device}")
                    self.lblWorkCounter.setText(self._get_counter_text())
                    self.lblWorkHoles.setText(f"Гвинтів: {self._holes_completed} / {self._total_holes}")

                    if saved_cycle_state == "COMPLETED":
                        self.lblWorkMessage.setText("Готово. Натисніть СТАРТ для нового циклу.")
                    elif saved_cycle_state == "PAUSED" or saved_cycle_state == "RUNNING":
                        # RUNNING state means app restarted during cycle - treat as paused
                        self.lblWorkMessage.setText("Пауза. Натисніть СТАРТ для продовження.")
                    elif saved_cycle_state == "ERROR":
                        self.lblWorkMessage.setText("Помилка. Натисніть СТАРТ для повторення.")
                    else:
                        self.lblWorkMessage.setText("Готово до запуску.")

                    self.workProgressBar.setValue(server_state.get("progress_percent", 0))
                    self.btnStartCycle.setEnabled(True)

                self._state_restored = True
                print(f"State restored: device={saved_device}, initialized={saved_initialized}, state={saved_cycle_state}")

        except Exception as e:
            print(f"Failed to restore state from server: {e}")

    def _select_device(self, key: str):
        """Select a device."""
        self._selected_device = key
        self._initialized = False
        self._cycle_state = "IDLE"
        self._update_device_styles()

        # Get device info
        for dev in self._devices:
            if dev.get("key") == key:
                self._total_holes = dev.get("holes", 0)
                self._device_task = dev.get("task", "-") or "-"
                self._device_torque = dev.get("torque")
                break

        self.lblStartDevice.setText(f"Девайс: {key}")
        self.lblStartTask.setText(f"Таска: {self._device_task}")
        torque_str = f"{self._device_torque} Nm" if self._device_torque is not None else "-"
        self.lblStartTorque.setText(f"Момент: {torque_str}")
        self.lblStartMessage.setText(f"Девайс {key} вибрано. Натисніть ІНІЦІАЛІЗАЦІЯ.")
        self.btnInit.setEnabled(True)

        # Sync to server for web UI
        try:
            self.api.select_device(key)
        except Exception as e:
            print(f"Device selection sync failed: {e}")

    def _sync_state_to_server(self, cycle_state: str, message: str = "", progress_percent: int = 0, current_step: str = ""):
        """Sync current state to server for web UI."""
        try:
            self.api.set_ui_state({
                "source": "desktop",  # Important: identifies this as desktop update
                "selected_device": self._selected_device,
                "cycle_state": cycle_state,
                "initialized": self._initialized,
                "holes_completed": self._holes_completed,
                "total_holes": self._total_holes,
                "cycles_completed": self._total_cycles,
                "message": message,
                "progress_percent": progress_percent,
                "current_step": current_step or message
            })
        except Exception as e:
            print(f"State sync failed: {e}")

    def on_init(self):
        """Handle initialization button."""
        if not self._selected_device:
            self.lblStartMessage.setText("Спочатку виберіть девайс!")
            return

        # Check if web is already operating
        try:
            server_state = self.api.get_ui_state()
            if server_state.get("operator") == "web":
                self.lblStartMessage.setText("Web UI виконує операцію. Зачекайте...")
                return
        except Exception:
            pass

        # Get device data
        device = None
        for dev in self._devices:
            if dev.get("key") == self._selected_device:
                device = dev
                break

        if not device:
            self.lblStartMessage.setText("Девайс не знайдено!")
            return

        # Try to get full device data from API
        try:
            device = self.api.device(self._selected_device)
            self._total_holes = len([s for s in device.get("steps", []) if s.get("type", "").lower() == "work"])
        except Exception as e:
            print(f"Failed to load device details: {e}")

        self._cycle_state = "INITIALIZING"
        self.lblStartMessage.setText("Ініціалізація...")
        self.startProgressBar.setValue(0)
        self.btnInit.setEnabled(False)

        # Sync state to server
        self._sync_state_to_server("INITIALIZING", "Ініціалізація...")

        # Start initialization worker
        self._init_worker = InitWorker(self.api, device)
        self._init_worker.progress.connect(self._on_init_progress)
        self._init_worker.finished_ok.connect(self._on_init_success)
        self._init_worker.finished_error.connect(self._on_init_error)
        self._init_worker.start()

    def _on_init_progress(self, message: str, progress: int):
        """Handle initialization progress updates."""
        self.lblStartMessage.setText(message)
        self.startProgressBar.setValue(progress)

    def _on_init_success(self, warnings: str):
        """Called when initialization completes successfully."""
        self._initialized = True
        self._cycle_state = "READY"

        if warnings:
            self.lblStartMessage.setText(warnings)
        else:
            self.lblStartMessage.setText("Ініціалізація завершена!")

        self.startProgressBar.setValue(100)
        self._init_worker = None

        # Sync state to server
        self._sync_state_to_server("READY", "Готово до запуску")

        # Switch to work mode
        self.switch_to_work_mode()

    def _on_init_error(self, error_msg: str):
        """Called when initialization fails."""
        self._initialized = False
        self._cycle_state = "INIT_ERROR"
        self.lblStartMessage.setText(f"ПОМИЛКА: {error_msg}")
        self.startProgressBar.setValue(0)
        self.btnInit.setEnabled(True)
        self._init_worker = None

        # Sync state to server
        self._sync_state_to_server("INIT_ERROR", f"Помилка: {error_msg}")

    def on_start(self):
        """Handle START CYCLE button in WORK mode."""
        if not self._selected_device or not self._initialized:
            self.lblWorkMessage.setText("Помилка: машина не ініціалізована!")
            return

        # Check if web is already operating
        try:
            server_state = self.api.get_ui_state()
            if server_state.get("operator") == "web":
                self.lblWorkMessage.setText("Web UI виконує операцію. Зачекайте...")
                return
        except Exception:
            pass

        # Get device data with steps
        device = None
        try:
            device = self.api.device(self._selected_device)
        except Exception as e:
            self.lblWorkMessage.setText(f"Не вдалося завантажити девайс: {e}")
            return

        if not device or not device.get("steps"):
            self.lblWorkMessage.setText("Девайс не має координат для закручування!")
            return

        self._cycle_state = "RUNNING"
        self._holes_completed = 0
        self.lblWorkMessage.setText("Цикл виконується...")
        self.workProgressBar.setValue(0)
        self.btnStartCycle.setEnabled(False)

        # Sync state to server
        self._sync_state_to_server("RUNNING", "Цикл виконується", 0, "Запуск циклу")

        # Record cycle start time
        self._cycle_start_time = time.time()

        # Start cycle worker
        self._cycle_worker = CycleWorker(self.api, device)
        self._cycle_worker.progress.connect(self._on_cycle_progress)
        self._cycle_worker.finished_ok.connect(self._on_cycle_success)
        self._cycle_worker.finished_error.connect(self._on_cycle_error)
        self._cycle_worker.start()

    def _on_cycle_progress(self, message: str, holes: int, total: int, pct: int):
        """Handle cycle progress updates."""
        self._holes_completed = holes
        self.lblWorkMessage.setText(message)
        self.lblWorkHoles.setText(f"Гвинтів: {holes} / {total}")
        self.workProgressBar.setValue(pct)

    def _on_cycle_success(self, holes_completed: int):
        """Called when cycle completes successfully."""
        self._total_cycles += 1
        self._cycle_state = "COMPLETED"
        self._holes_completed = holes_completed

        # Calculate cycle time and add to list
        if self._cycle_start_time is not None:
            cycle_time = time.time() - self._cycle_start_time
            self._cycle_times.append(cycle_time)
            self._cycle_start_time = None

        self.lblWorkMessage.setText(f"Цикл завершено! Закручено {holes_completed} гвинтів.")
        self.lblWorkCounter.setText(self._get_counter_text())
        self.lblWorkHoles.setText(f"Гвинтів: {holes_completed} / {self._total_holes}")
        self.workProgressBar.setValue(100)
        self.btnStartCycle.setEnabled(True)
        self._cycle_worker = None

        # Sync state to server
        self._sync_state_to_server("COMPLETED", f"Цикл завершено! Закручено {holes_completed} гвинтів.", 100, "Цикл завершено")

    def _on_cycle_error(self, error_msg: str):
        """Called when cycle fails."""
        self._cycle_state = "ERROR"
        self.lblWorkMessage.setText(f"ПОМИЛКА: {error_msg}")
        self.workProgressBar.setValue(0)
        self.btnStartCycle.setEnabled(True)
        self._cycle_worker = None

        # Special handling for torque error
        if error_msg == "TORQUE_NOT_REACHED":
            self._cycle_state = "PAUSED"
            self.lblWorkMessage.setText("Момент не досягнуто. Перевірте гвинт та натисніть СТАРТ.")
            self._sync_state_to_server("PAUSED", "Момент не досягнуто", 0, "Помилка моменту")
        # Special handling for light barrier (area sensor)
        elif error_msg == "AREA_BLOCKED":
            self._cycle_state = "AREA_BLOCKED"
            self.lblWorkMessage.setText("СВІТЛОВА ЗАВІСА!")
            self._sync_state_to_server("AREA_BLOCKED", "Світлова завіса спрацювала", 0, "Світлова завіса")
            # Show dialog with ВИЇЗД button
            self._show_area_blocked_dialog()
        else:
            self._sync_state_to_server("ERROR", f"Помилка: {error_msg}", 0, "Помилка циклу")

    def _show_area_blocked_dialog(self):
        """Show fullscreen dialog when light barrier is triggered."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QApplication
        from PyQt5.QtCore import Qt

        # Get screen size
        screen = QApplication.primaryScreen().geometry()

        dialog = QDialog(self)
        dialog.setWindowTitle("Світлова завіса")
        dialog.setModal(True)
        dialog.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        dialog.setGeometry(screen)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(30)
        layout.setContentsMargins(50, 60, 50, 60)

        # Warning icon - triangle with exclamation
        icon_lbl = QLabel("!")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("""
            color: #1a1a1a;
            font-size: 80px;
            font-weight: bold;
            background-color: #ffeb3b;
            border: 6px solid #f57f17;
            border-radius: 50px;
            min-width: 100px;
            max-width: 100px;
            min-height: 100px;
            max-height: 100px;
        """)
        layout.addWidget(icon_lbl, alignment=Qt.AlignCenter)

        # Warning text
        lbl = QLabel("СВІТЛОВА ЗАВІСА!")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("""
            color: #f44336;
            font-size: 48px;
            font-weight: bold;
        """)
        layout.addWidget(lbl)

        # Instruction text
        instr_lbl = QLabel("Закручування зупинено.\nПриберіть руки з робочої зони.")
        instr_lbl.setAlignment(Qt.AlignCenter)
        instr_lbl.setStyleSheet("""
            color: #ffffff;
            font-size: 28px;
        """)
        layout.addWidget(instr_lbl)

        # Reinit required message
        reinit_lbl = QLabel("Потрібна переініціалізація!")
        reinit_lbl.setAlignment(Qt.AlignCenter)
        reinit_lbl.setStyleSheet("""
            color: #ff9800;
            font-size: 24px;
            font-weight: bold;
        """)
        layout.addWidget(reinit_lbl)

        layout.addStretch()

        # "Зона безпечна" button
        self._area_dialog = dialog
        btn = QPushButton("ЗОНА БЕЗПЕЧНА")
        btn.setFixedSize(450, 120)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: #ffffff;
                font-size: 36px;
                font-weight: bold;
                border: none;
                border-radius: 16px;
            }
            QPushButton:pressed {
                background-color: #388E3C;
            }
        """)
        btn.clicked.connect(self._on_area_safe_button_clicked)
        layout.addWidget(btn, alignment=Qt.AlignCenter)

        layout.addStretch()

        dialog.exec_()

    def _on_area_safe_button_clicked(self):
        """Handle 'Зона безпечна' button - close dialog and go to START for reinit."""
        # Close dialog
        if hasattr(self, '_area_dialog') and self._area_dialog:
            self._area_dialog.done(0)
            self._area_dialog = None

        # Reset state and switch to START mode for reinitialization
        self._initialized = False
        self._cycle_state = "IDLE"
        self.switch_to_start_mode()
        self.lblStartMessage.setText("Потрібна переініціалізація після спрацювання завіси.")

    def on_stop_and_return(self):
        """Handle STOP button in WORK mode - stop and return to START mode."""
        # Abort cycle worker if running
        if self._cycle_worker and self._cycle_worker.isRunning():
            self._cycle_worker.abort()
            self._cycle_worker = None

        try:
            self.api.xy_stop()
        except Exception:
            pass

        # Safety: turn off dangerous relays
        try:
            self.api.relay_set("r04_c2", "off")
        except Exception:
            pass
        try:
            self.api.relay_set("r06_di1_pot", "off")
        except Exception:
            pass

        self._cycle_state = "STOPPED"

        # Sync state to server
        self._sync_state_to_server("STOPPED", "Цикл зупинено")

        # Return to start mode
        self.switch_to_start_mode()

    def on_estop(self):
        """Handle E-STOP button."""
        # Abort init worker if running
        if self._init_worker and self._init_worker.isRunning():
            self._init_worker.abort()
            self._init_worker = None

        # Abort cycle worker if running
        if self._cycle_worker and self._cycle_worker.isRunning():
            self._cycle_worker.abort()
            self._cycle_worker = None

        # Safety: turn off dangerous relays first
        try:
            self.api.relay_set("r04_c2", "off")
        except Exception:
            pass
        try:
            self.api.relay_set("r06_di1_pot", "off")
        except Exception:
            pass

        try:
            self.api.xy_estop()
            self.api.cycle_estop()
        except Exception:
            pass

        self._cycle_state = "E-STOP"
        self._initialized = False

        # Sync state to server
        self._sync_state_to_server("E-STOP", "Аварійна зупинка")

        # Show E-STOP dialog (non-blocking)
        if not self._estop_dialog:
            self._show_estop_dialog()

    def _show_estop_dialog(self):
        """Show fullscreen E-STOP dialog."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QApplication
        from PyQt5.QtCore import Qt

        # Get screen size
        screen = QApplication.primaryScreen().geometry()

        dialog = QDialog(self)
        dialog.setWindowTitle("Аварійна зупинка")
        dialog.setModal(False)  # Non-modal so render() can still update
        dialog.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        dialog.setGeometry(screen)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #b71c1c;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(30)
        layout.setContentsMargins(50, 60, 50, 60)

        # Warning icon - text-based STOP sign
        icon_lbl = QLabel("STOP")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("""
            color: #ffffff;
            font-size: 80px;
            font-weight: bold;
            background-color: #d32f2f;
            border: 8px solid #ffffff;
            border-radius: 20px;
            padding: 20px 40px;
        """)
        layout.addWidget(icon_lbl, alignment=Qt.AlignCenter)

        # Warning text
        lbl = QLabel("АВАРІЙНА ЗУПИНКА!")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("""
            color: #ffffff;
            font-size: 52px;
            font-weight: bold;
        """)
        layout.addWidget(lbl)

        layout.addStretch()

        # Instruction text
        instr_lbl = QLabel("Відпустіть кнопку для продовження")
        instr_lbl.setAlignment(Qt.AlignCenter)
        instr_lbl.setStyleSheet("""
            color: #ffcdd2;
            font-size: 28px;
        """)
        layout.addWidget(instr_lbl)

        # Reinit message
        reinit_lbl = QLabel("Потрібна переініціалізація!")
        reinit_lbl.setAlignment(Qt.AlignCenter)
        reinit_lbl.setStyleSheet("""
            color: #ffeb3b;
            font-size: 24px;
            font-weight: bold;
        """)
        layout.addWidget(reinit_lbl)

        layout.addStretch()

        self._estop_dialog = dialog
        dialog.show()

    def _close_estop_dialog(self):
        """Close E-STOP dialog and switch to START mode."""
        if self._estop_dialog:
            self._estop_dialog.close()
            self._estop_dialog = None

        # Reset state and switch to START mode for reinitialization
        self._cycle_state = "IDLE"
        self.switch_to_start_mode()
        self.lblStartMessage.setText("Аварійна зупинка знята. Потрібна переініціалізація.")

    def render(self, status: dict):
        """Update UI from status."""
        # Load devices if needed
        if not self._devices:
            try:
                self._devices = self.api.devices()
                self._rebuild_devices(self._devices)
            except Exception:
                pass

        # Check for UI state changes from web UI
        self._check_server_ui_state()

        # Check E-STOP from sensors
        sensors = status.get("sensors", {})
        estop = sensors.get("emergency_stop") == "ACTIVE"
        if estop and self._cycle_state != "E-STOP":
            self.on_estop()
        # Close E-STOP dialog when button is released
        elif not estop and self._estop_dialog:
            self._close_estop_dialog()

        # Check pedal press (ped_start) - trigger start on rising edge
        pedal_pressed = sensors.get("ped_start") == "ACTIVE"
        if pedal_pressed and not self._pedal_was_pressed:
            # Pedal just pressed - trigger start if in WORK mode and ready
            can_start = self._cycle_state in ("IDLE", "READY", "COMPLETED", "PAUSED")
            if self._current_mode == self.MODE_WORK and can_start and self._initialized:
                self.on_start()
        self._pedal_was_pressed = pedal_pressed

    def _check_server_ui_state(self):
        """Check if server UI state was updated by web client."""
        try:
            server_state = self.api.get_ui_state()

            # Check if web is actively operating
            web_is_operating = server_state.get("operator") == "web"

            if web_is_operating or (server_state.get("updated_at", 0) > self._last_server_state_time and
                                    server_state.get("updated_by") == "web"):

                self._last_server_state_time = server_state.get("updated_at", 0)

                # Update device selection from web
                new_device = server_state.get("selected_device")
                if new_device and new_device != self._selected_device:
                    self._selected_device = new_device
                    self._update_device_styles()
                    if self._current_mode == self.MODE_START:
                        self.lblStartDevice.setText(f"Девайс: {new_device}")
                    else:
                        self.lblWorkDevice.setText(f"Девайс: {new_device}")

                # Update initialized flag from web
                web_initialized = server_state.get("initialized", False)
                if web_initialized and not self._initialized:
                    self._initialized = True

                # Sync cycles count from server (web may have completed cycles)
                server_cycles = server_state.get("cycles_completed", 0)
                if server_cycles > self._total_cycles:
                    self._total_cycles = server_cycles
                    self.lblWorkCounter.setText(self._get_counter_text())

                # Sync holes progress from server
                server_holes = server_state.get("holes_completed", 0)
                server_total_holes = server_state.get("total_holes", 0)
                if server_holes != self._holes_completed or server_total_holes != self._total_holes:
                    self._holes_completed = server_holes
                    self._total_holes = server_total_holes
                    self.lblWorkHoles.setText(f"Гвинтів: {self._holes_completed} / {self._total_holes}")

                # Update cycle state from web
                new_state = server_state.get("cycle_state", "IDLE")
                old_state = self._cycle_state
                self._cycle_state = new_state

                # Switch to WORK mode when web completes initialization
                if new_state == "READY" and self._initialized and self._current_mode == self.MODE_START:
                    self.switch_to_work_mode()

                # Update progress when web is operating
                if web_is_operating:
                    progress_pct = server_state.get("progress_percent", 0)
                    message = server_state.get("message", "")
                    holes = server_state.get("holes_completed", 0)
                    total = server_state.get("total_holes", 0)

                    # Switch to WORK mode if cycle is running and we're in START mode
                    if new_state == "RUNNING" and self._current_mode == self.MODE_START:
                        self.switch_to_work_mode()

                    if self._current_mode == self.MODE_START:
                        self.startProgressBar.setValue(progress_pct)
                        self.lblStartMessage.setText(message or "Web UI виконує операцію...")
                        self.btnInit.setEnabled(False)
                    else:
                        self.workProgressBar.setValue(progress_pct)
                        self.lblWorkMessage.setText(message or "Web UI виконує операцію...")
                        self.lblWorkHoles.setText(f"Гвинтів: {holes} / {total}")
                        self.btnStartCycle.setEnabled(False)

            if server_state.get("updated_by") == "desktop":
                self._last_server_state_time = server_state.get("updated_at", 0)

        except Exception:
            pass


# ================== Service Tab ==================
class ServiceTab(QWidget):
    """Service tab - sensors and relay control (compact layout, no scroll)."""

    # Ukrainian names for relays (short)
    RELAY_NAMES = {
        'r01_pit': 'Подача',
        'r02_brake_x': 'Гальмо X',
        'r03_brake_y': 'Гальмо Y',
        'r04_c2': 'Циліндр',
        'r05_di4_free': 'Вільн.хід',
        'r06_di1_pot': 'Момент',
        'r07_di5_tsk0': 'Задача 0',
        'r08_di6_tsk1': 'Задача 1',
        'r09_pwr_x': 'Живл. X',
        'r10_pwr_y': 'Живл. Y',
    }

    # Ukrainian names for sensors (short)
    SENSOR_NAMES = {
        'emergency_stop': 'E-STOP',
        'ger_c2_up': 'Цил.вгорі',
        'ger_c2_down': 'Цил.внизу',
        'ind_scrw': 'Гвинт',
        'do2_ok': 'Момент OK',
        'alarm_x': 'Аларм X',
        'alarm_y': 'Аларм Y',
        'ped_start': 'Педаль',
        'area_sensor': 'Завіса',
    }

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._relay_widgets = {}
        self._sensor_widgets = {}
        self._last_ip_update = 0

        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Top row: Network info card
        self.netCard = make_card("Мережа")
        net_lay = self.netCard.layout()

        net_row = QHBoxLayout()
        net_row.setSpacing(32)

        # IP section
        ip_box = QHBoxLayout()
        ip_box.setSpacing(12)
        ip_label = QLabel("IP адреса:")
        ip_label.setObjectName("serviceLabelLarge")
        ip_box.addWidget(ip_label)

        self.lblIp = QLabel(get_local_ip())
        self.lblIp.setObjectName("serviceIpValue")
        ip_box.addWidget(self.lblIp)

        btnRefreshIp = QPushButton("⟳")
        btnRefreshIp.setObjectName("btn_refresh")
        btnRefreshIp.setFixedSize(44, 44)
        btnRefreshIp.clicked.connect(self._update_ip)
        ip_box.addWidget(btnRefreshIp)

        net_row.addLayout(ip_box)
        net_row.addStretch(1)

        # API status section
        api_box = QHBoxLayout()
        api_box.setSpacing(12)
        api_label = QLabel("API:")
        api_label.setObjectName("serviceLabelLarge")
        api_box.addWidget(api_label)

        self.lblApiStatus = QLabel("● Онлайн")
        self.lblApiStatus.setObjectName("serviceStatusOnline")
        api_box.addWidget(self.lblApiStatus)

        net_row.addLayout(api_box)

        net_lay.addLayout(net_row)
        root.addWidget(self.netCard)

        # Main content row
        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        # Left - Sensors card
        self.sensorsCard = make_card("Сенсори")
        sensors_lay = self.sensorsCard.layout()

        self.sensorsGrid = QGridLayout()
        self.sensorsGrid.setSpacing(12)
        sensors_lay.addLayout(self.sensorsGrid, 1)

        content_row.addWidget(self.sensorsCard, 2)

        # Right - Relays card
        self.relaysCard = make_card("Реле керування")
        relays_lay = self.relaysCard.layout()

        self.relaysGrid = QGridLayout()
        self.relaysGrid.setSpacing(12)
        relays_lay.addLayout(self.relaysGrid, 1)

        content_row.addWidget(self.relaysCard, 3)

        root.addLayout(content_row, 1)

    def _get_relay_name(self, key: str) -> str:
        """Get Ukrainian name for relay."""
        return self.RELAY_NAMES.get(key, key)

    def _get_sensor_name(self, key: str) -> str:
        """Get Ukrainian name for sensor."""
        return self.SENSOR_NAMES.get(key, key)

    def _create_sensor_widget(self, col: int, row: int, name: str, value):
        """Create sensor widget - entire box changes color based on state."""
        is_active = value == "ACTIVE" or value == True

        # Container widget with colored background
        container = QFrame()
        container.setObjectName("sensorBoxActive" if is_active else "sensorBoxInactive")
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box_lay = QVBoxLayout(container)
        box_lay.setContentsMargins(12, 20, 12, 20)
        box_lay.setSpacing(0)

        # Name label (centered)
        display_name = self._get_sensor_name(name)
        lblName = QLabel(display_name)
        lblName.setObjectName("sensorNameActive" if is_active else "sensorNameInactive")
        lblName.setAlignment(Qt.AlignCenter)
        lblName.setWordWrap(True)
        box_lay.addWidget(lblName, 1, Qt.AlignCenter)

        self.sensorsGrid.addWidget(container, row, col)
        self._sensor_widgets[name] = (container, lblName)

    def _create_relay_widget(self, col: int, row: int, name: str, state: str):
        """Create relay widget with single toggle button."""
        is_on = state == "ON"

        # Container widget
        container = QFrame()
        container.setObjectName("relayBox")
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box_lay = QVBoxLayout(container)
        box_lay.setContentsMargins(10, 12, 10, 12)
        box_lay.setSpacing(10)

        # Name label (centered at top)
        display_name = self._get_relay_name(name)
        lblName = QLabel(display_name)
        lblName.setObjectName("relayNameCompact")
        lblName.setAlignment(Qt.AlignCenter)
        box_lay.addWidget(lblName)

        # Toggle button - shows state and toggles on click
        btnToggle = QPushButton("ON" if is_on else "OFF")
        btnToggle.setObjectName("btn_relay_toggle_on" if is_on else "btn_relay_toggle_off")
        btnToggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btnToggle.setMinimumHeight(50)
        btnToggle.clicked.connect(lambda _, n=name: self._relay_toggle(n))
        box_lay.addWidget(btnToggle, 1)

        self.relaysGrid.addWidget(container, row, col)
        self._relay_widgets[name] = btnToggle

    def _relay_toggle(self, name: str):
        """Toggle relay state."""
        btn = self._relay_widgets.get(name)
        if btn:
            current_state = btn.text()
            new_state = "off" if current_state == "ON" else "on"
            self._relay_cmd(name, new_state)

    def _relay_cmd(self, name: str, action: str, duration: float = None):
        """Send relay command."""
        try:
            self.api.relay_set(name, action, duration)
        except Exception as e:
            print(f"Relay command error: {e}")

    def _update_ip(self):
        """Update IP address."""
        self.lblIp.setText(get_local_ip())

    def render(self, status: dict):
        """Update UI from status."""
        sensors = status.get("sensors", {})
        relays = status.get("relays", {})

        # Update IP every 10 seconds
        import time
        current_time = time.time()
        if current_time - self._last_ip_update > 10:
            self._last_ip_update = current_time
            self._update_ip()

        # Update API status
        self.lblApiStatus.setText("● Онлайн")

        # Update sensors - grid layout (4 columns)
        sensor_names = list(sensors.keys())
        if set(sensor_names) != set(self._sensor_widgets.keys()):
            # Rebuild sensors grid
            while self.sensorsGrid.count():
                item = self.sensorsGrid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._sensor_widgets.clear()

            cols = 3
            for i, name in enumerate(sensor_names):
                col = i % cols
                row = i // cols
                self._create_sensor_widget(col, row, name, sensors.get(name))
        else:
            # Update states - change box and label styles
            for name, widgets in self._sensor_widgets.items():
                container, lblName = widgets
                value = sensors.get(name)
                is_active = value == "ACTIVE" or value == True
                container.setObjectName("sensorBoxActive" if is_active else "sensorBoxInactive")
                lblName.setObjectName("sensorNameActive" if is_active else "sensorNameInactive")
                container.style().unpolish(container)
                container.style().polish(container)
                lblName.style().unpolish(lblName)
                lblName.style().polish(lblName)

        # Update relays - grid layout (5 columns, 2 rows)
        relay_names = list(relays.keys())
        if set(relay_names) != set(self._relay_widgets.keys()):
            # Rebuild relay grid
            while self.relaysGrid.count():
                item = self.relaysGrid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._relay_widgets.clear()

            cols = 5
            for i, name in enumerate(relay_names):
                col = i % cols
                row = i // cols
                self._create_relay_widget(col, row, name, relays.get(name, "OFF"))
        else:
            # Update states - change button text and style
            for name, btnToggle in self._relay_widgets.items():
                is_on = relays.get(name) == "ON"
                btnToggle.setText("ON" if is_on else "OFF")
                btnToggle.setObjectName("btn_relay_toggle_on" if is_on else "btn_relay_toggle_off")
                btnToggle.style().unpolish(btnToggle)
                btnToggle.style().polish(btnToggle)


# ================== Platform Tab ==================
class PlatformTab(QWidget):
    """Platform (XY Table) status and logs tab."""

    MAX_LOG_LINES = 100

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._current_x = 0.0
        self._current_y = 0.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._log_lines = []
        self._last_log_fetch = 0
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Top row: Status + Position
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # Status card
        self.statusCard = make_card("Статус XY Столу")
        status_lay = self.statusCard.layout()

        status_grid = QGridLayout()
        status_grid.setSpacing(12)

        status_grid.addWidget(QLabel("Стан:"), 0, 0)
        self.lblState = QLabel("-")
        self.lblState.setObjectName("statusValue")
        status_grid.addWidget(self.lblState, 0, 1)

        status_grid.addWidget(QLabel("Підключення:"), 1, 0)
        self.lblConnection = QLabel("-")
        self.lblConnection.setObjectName("statusValue")
        status_grid.addWidget(self.lblConnection, 1, 1)

        status_grid.addWidget(QLabel("Homed:"), 2, 0)
        self.lblHomed = QLabel("X: ? Y: ?")
        self.lblHomed.setObjectName("statusValue")
        status_grid.addWidget(self.lblHomed, 2, 1)

        status_grid.addWidget(QLabel("Endstops:"), 3, 0)
        self.lblEndstops = QLabel("-")
        self.lblEndstops.setObjectName("statusValue")
        status_grid.addWidget(self.lblEndstops, 3, 1)

        status_lay.addLayout(status_grid)
        top_row.addWidget(self.statusCard, 1)

        # Position card
        self.posCard = make_card("Позиція")
        pos_lay = self.posCard.layout()

        pos_grid = QGridLayout()
        pos_grid.setSpacing(12)

        pos_grid.addWidget(QLabel("Фізична:"), 0, 0)
        self.lblPhysPos = QLabel("X: ?.??  Y: ?.??")
        self.lblPhysPos.setObjectName("positionValue")
        pos_grid.addWidget(self.lblPhysPos, 0, 1)

        pos_grid.addWidget(QLabel("Робоча:"), 1, 0)
        self.lblWorkPos = QLabel("X: ?.??  Y: ?.??")
        self.lblWorkPos.setObjectName("positionValue")
        pos_grid.addWidget(self.lblWorkPos, 1, 1)

        pos_grid.addWidget(QLabel("Offset:"), 2, 0)
        self.lblOffset = QLabel("X: 0.00  Y: 0.00")
        self.lblOffset.setObjectName("statusValue")
        pos_grid.addWidget(self.lblOffset, 2, 1)

        pos_lay.addLayout(pos_grid)
        top_row.addWidget(self.posCard, 1)

        # Slave Raspberry Pi status card
        self.slaveCard = make_card("Slave Raspberry Pi")
        slave_lay = self.slaveCard.layout()

        slave_grid = QGridLayout()
        slave_grid.setSpacing(12)

        slave_grid.addWidget(QLabel("Статус:"), 0, 0)
        self.lblSlaveStatus = QLabel("-")
        self.lblSlaveStatus.setObjectName("statusValue")
        slave_grid.addWidget(self.lblSlaveStatus, 0, 1)

        slave_grid.addWidget(QLabel("Останній зв'язок:"), 1, 0)
        self.lblSlaveLastComm = QLabel("-")
        self.lblSlaveLastComm.setObjectName("statusValue")
        slave_grid.addWidget(self.lblSlaveLastComm, 1, 1)

        slave_grid.addWidget(QLabel("Помилка:"), 2, 0)
        self.lblSlaveError = QLabel("-")
        self.lblSlaveError.setObjectName("statusValue")
        slave_grid.addWidget(self.lblSlaveError, 2, 1)

        slave_lay.addLayout(slave_grid)
        top_row.addWidget(self.slaveCard, 1)

        root.addLayout(top_row)

        # Log card - takes remaining space
        self.logCard = make_card("Лог XY Столу та Slave")
        log_lay = self.logCard.layout()

        # Log text area
        from PyQt5.QtWidgets import QTextEdit
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)
        self.logText.setObjectName("logTextArea")
        self.logText.setMinimumHeight(300)
        log_lay.addWidget(self.logText)

        root.addWidget(self.logCard, 1)

    def _fetch_logs(self):
        """Fetch logs from API for XY and COMM categories."""
        try:
            # Fetch XY-specific logs (no auth required)
            response = self.api._get("xy/logs?limit=50")
            logs = response.get("logs", [])

            # Format log entries
            new_lines = []
            for log in logs:
                timestamp = log.get("timestamp", "")[:19]  # Trim to seconds
                level = log.get("level", "INFO")
                category = log.get("category", "")
                message = log.get("message", "")
                new_lines.append(f"[{timestamp}] [{level}] [{category}] {message}")

            # Update log display if changed
            if new_lines != self._log_lines:
                self._log_lines = new_lines
                self.logText.setPlainText("\n".join(reversed(new_lines)))
                # Scroll to bottom
                scrollbar = self.logText.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

        except Exception as e:
            pass  # Silently fail if logs endpoint not available

    def render(self, status: dict):
        """Update UI from status."""
        xy = status.get("xy_table", {})
        sensors = status.get("sensors", {})

        # State
        state = xy.get("state", "DISCONNECTED")
        self.lblState.setText(state)

        # Connection status
        connected = xy.get("connected", False)
        self.lblConnection.setText("Підключено" if connected else "Відключено")

        # Position
        x_homed = xy.get("x_homed", False)
        y_homed = xy.get("y_homed", False)
        estop = sensors.get("emergency_stop") == "ACTIVE"

        if x_homed and not estop:
            self._current_x = xy.get("x", 0)
            x_pos = f"{self._current_x:.2f}"
        else:
            x_pos = "?.??"

        if y_homed and not estop:
            self._current_y = xy.get("y", 0)
            y_pos = f"{self._current_y:.2f}"
        else:
            y_pos = "?.??"

        self.lblPhysPos.setText(f"X: {x_pos}  Y: {y_pos}")

        # Load offsets
        try:
            offsets = self.api.get_offsets()
            self._offset_x = offsets.get("x", 0.0)
            self._offset_y = offsets.get("y", 0.0)
        except Exception:
            pass

        self.lblOffset.setText(f"X: {self._offset_x:.2f}  Y: {self._offset_y:.2f}")

        # Work position (physical - offset)
        if x_homed and y_homed and not estop:
            work_x = self._current_x - self._offset_x
            work_y = self._current_y - self._offset_y
            self.lblWorkPos.setText(f"X: {work_x:.2f}  Y: {work_y:.2f}")
        else:
            self.lblWorkPos.setText("X: ?.??  Y: ?.??")

        # Homed status
        x_h = "ТАК" if x_homed else "НІ"
        y_h = "ТАК" if y_homed else "НІ"
        self.lblHomed.setText(f"X: {x_h}  Y: {y_h}")

        # Endstops
        endstops = xy.get("endstops", {})
        x_min = "TRIG" if endstops.get("x_min") else "open"
        y_min = "TRIG" if endstops.get("y_min") else "open"
        self.lblEndstops.setText(f"X_MIN: {x_min}  Y_MIN: {y_min}")

        # Slave Raspberry Pi status
        slave_connected = xy.get("connected", False)
        self.lblSlaveStatus.setText("Онлайн" if slave_connected else "Офлайн")

        last_error = xy.get("last_error", "")
        self.lblSlaveError.setText(last_error if last_error else "Немає")

        # Update last communication time
        if slave_connected:
            self.lblSlaveLastComm.setText("Зараз")
        else:
            self.lblSlaveLastComm.setText("-")

        # Fetch logs periodically (every 2 seconds)
        import time
        current_time = time.time()
        if current_time - self._last_log_fetch > 2:
            self._last_log_fetch = current_time
            self._fetch_logs()


# ================== Logs Tab ==================
class LogsTab(QWidget):
    """Logs tab - displays all system logs like web UI."""

    MAX_LOG_LINES = 200

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._log_lines = []
        self._last_log_fetch = 0
        self._auto_refresh = True
        self._categories = ['']  # Empty = all
        self._levels = ['']  # Empty = all
        self._selected_category = ''
        self._selected_level = ''
        self._setup_ui()
        self._load_filters()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Top row: Filters
        filter_row = QHBoxLayout()
        filter_row.setSpacing(16)

        # Category filter
        cat_label = QLabel("Категорія:")
        cat_label.setObjectName("filterLabel")
        filter_row.addWidget(cat_label)

        self.cmbCategory = QComboBox()
        self.cmbCategory.setMinimumWidth(180)
        self.cmbCategory.addItem("Всі", "")
        self.cmbCategory.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.cmbCategory)

        filter_row.addSpacing(20)

        # Level filter
        lvl_label = QLabel("Рівень:")
        lvl_label.setObjectName("filterLabel")
        filter_row.addWidget(lvl_label)

        self.cmbLevel = QComboBox()
        self.cmbLevel.setMinimumWidth(150)
        self.cmbLevel.addItem("Всі", "")
        self.cmbLevel.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.cmbLevel)

        filter_row.addStretch(1)

        # Auto-refresh toggle
        self.btnAutoRefresh = QPushButton("Авто-оновлення")
        self.btnAutoRefresh.setObjectName("btn_toggle")
        self.btnAutoRefresh.setCheckable(True)
        self.btnAutoRefresh.setChecked(True)
        self.btnAutoRefresh.clicked.connect(self._on_auto_refresh_toggled)
        filter_row.addWidget(self.btnAutoRefresh)

        # Refresh button
        self.btnRefresh = QPushButton("Оновити")
        self.btnRefresh.setObjectName("btn_info")
        self.btnRefresh.setMinimumWidth(120)
        self.btnRefresh.clicked.connect(self._fetch_logs)
        filter_row.addWidget(self.btnRefresh)

        root.addLayout(filter_row)

        # Stats row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(20)

        self.lblLogCount = QLabel("Записів: 0")
        self.lblLogCount.setObjectName("statusValue")
        stats_row.addWidget(self.lblLogCount)

        stats_row.addStretch(1)

        self.lblLastUpdate = QLabel("Оновлено: -")
        self.lblLastUpdate.setObjectName("statusValue")
        stats_row.addWidget(self.lblLastUpdate)

        root.addLayout(stats_row)

        # Log text area
        from PyQt5.QtWidgets import QTextEdit
        self.logText = QTextEdit()
        self.logText.setReadOnly(True)
        self.logText.setObjectName("logTextArea")
        self.logText.setMinimumHeight(400)
        root.addWidget(self.logText, 1)

    def _load_filters(self):
        """Load available categories and levels from API."""
        try:
            # Load categories
            resp = self.api._get("desktop/logs/categories")
            cats = resp.get("categories", [])
            for cat in cats:
                self.cmbCategory.addItem(cat, cat)
            self._categories = [''] + cats
        except Exception:
            # Add default categories if API fails
            for cat in ['XY', 'COMM', 'GCODE', 'SYSTEM', 'CYCLE', 'RELAY', 'SENSOR']:
                self.cmbCategory.addItem(cat, cat)

        try:
            # Load levels
            resp = self.api._get("desktop/logs/levels")
            lvls = resp.get("levels", [])
            for lvl in lvls:
                self.cmbLevel.addItem(lvl, lvl)
            self._levels = [''] + lvls
        except Exception:
            # Add default levels if API fails
            for lvl in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
                self.cmbLevel.addItem(lvl, lvl)

    def _on_filter_changed(self):
        """Handle filter change."""
        self._selected_category = self.cmbCategory.currentData() or ''
        self._selected_level = self.cmbLevel.currentData() or ''
        self._fetch_logs()

    def _on_auto_refresh_toggled(self, checked):
        """Handle auto-refresh toggle."""
        self._auto_refresh = checked

    def _fetch_logs(self):
        """Fetch logs from API."""
        try:
            # Build query params
            params = ["limit=200"]
            if self._selected_category:
                params.append(f"category={self._selected_category}")
            if self._selected_level:
                params.append(f"level={self._selected_level}")

            query = "&".join(params)
            response = self.api._get(f"desktop/logs?{query}")
            logs = response.get("logs", [])

            # Format log entries with colors
            new_lines = []
            for log in logs:
                timestamp = log.get("timestamp", "")[:19]  # Trim to seconds
                level = log.get("level", "INFO")
                category = log.get("category", "")
                message = log.get("message", "")
                source = log.get("source", "")

                # Color coding based on level
                if level == "ERROR":
                    color = COLORS['red']
                elif level == "WARNING":
                    color = COLORS['yellow']
                elif level == "DEBUG":
                    color = COLORS['text_muted']
                else:
                    color = COLORS['text']

                src_str = f" [{source}]" if source else ""
                line = f'<span style="color:{color}">[{timestamp}] [{level}] [{category}]{src_str} {message}</span>'
                new_lines.append(line)

            # Update display
            self._log_lines = new_lines
            self.logText.setHtml("<br>".join(new_lines))
            self.lblLogCount.setText(f"Записів: {len(logs)}")

            # Update last update time
            import time
            from datetime import datetime
            now = datetime.now().strftime("%H:%M:%S")
            self.lblLastUpdate.setText(f"Оновлено: {now}")

        except Exception as e:
            self.logText.setPlainText(f"Помилка завантаження логів: {e}")

    def render(self, status: dict):
        """Update UI from status - called periodically."""
        # Auto-refresh logs every 3 seconds
        import time
        current_time = time.time()
        if self._auto_refresh and current_time - self._last_log_fetch > 3:
            self._last_log_fetch = current_time
            self._fetch_logs()


# ================== Control Tab ==================
class ControlTab(QWidget):
    """Control tab - XY table manual control (jog, homing, brakes)."""

    STEP_OPTIONS = [0.1, 0.5, 1, 5, 10, 50]
    FEED_OPTIONS = [1000, 3000, 5000, 10000, 20000, 30000, 50000]

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._current_x = 0.0
        self._current_y = 0.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._brake_x_on = False
        self._brake_y_on = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Main horizontal layout
        main_row = QHBoxLayout()
        main_row.setSpacing(16)

        # Left: Position and Jog controls
        left_col = QVBoxLayout()
        left_col.setSpacing(16)

        # Position display card
        pos_card = make_card("ПОЗИЦІЯ")
        pos_lay = pos_card.layout()

        self.lblPosition = QLabel("X: 0.00   Y: 0.00")
        self.lblPosition.setObjectName("xyPositionLarge")
        self.lblPosition.setAlignment(Qt.AlignCenter)
        pos_lay.addWidget(self.lblPosition)

        self.lblWorkPosition = QLabel("Робочі: X: 0.00   Y: 0.00")
        self.lblWorkPosition.setObjectName("xyPositionSmall")
        self.lblWorkPosition.setAlignment(Qt.AlignCenter)
        pos_lay.addWidget(self.lblWorkPosition)

        left_col.addWidget(pos_card)

        # Jog controls card
        jog_card = make_card("КЕРУВАННЯ")
        jog_lay = jog_card.layout()

        # Jog grid - 3x3 layout (without center button)
        jog_grid = QGridLayout()
        jog_grid.setSpacing(16)

        # Y- button (top center)
        self.btnYMinus = QPushButton("Y-")
        self.btnYMinus.setObjectName("jogButton")
        self.btnYMinus.setMinimumSize(80, 60)
        self.btnYMinus.clicked.connect(lambda: self._jog(0, -1))
        jog_grid.addWidget(self.btnYMinus, 0, 1)

        # X+ button (middle left)
        self.btnXPlus = QPushButton("X+")
        self.btnXPlus.setObjectName("jogButton")
        self.btnXPlus.setMinimumSize(80, 60)
        self.btnXPlus.clicked.connect(lambda: self._jog(1, 0))
        jog_grid.addWidget(self.btnXPlus, 1, 0)

        # X- button (middle right)
        self.btnXMinus = QPushButton("X-")
        self.btnXMinus.setObjectName("jogButton")
        self.btnXMinus.setMinimumSize(80, 60)
        self.btnXMinus.clicked.connect(lambda: self._jog(-1, 0))
        jog_grid.addWidget(self.btnXMinus, 1, 2)

        # Y+ button (bottom center)
        self.btnYPlus = QPushButton("Y+")
        self.btnYPlus.setObjectName("jogButton")
        self.btnYPlus.setMinimumSize(80, 60)
        self.btnYPlus.clicked.connect(lambda: self._jog(0, 1))
        jog_grid.addWidget(self.btnYPlus, 2, 1)

        # Step selector (bottom left)
        self.cmbStep = QComboBox()
        self.cmbStep.setObjectName("controlCombo")
        for step in self.STEP_OPTIONS:
            self.cmbStep.addItem(str(step), step)
        self.cmbStep.setCurrentIndex(4)  # Default 10
        jog_grid.addWidget(self.cmbStep, 2, 0)

        # Feed selector (bottom right)
        self.cmbFeed = QComboBox()
        self.cmbFeed.setObjectName("controlCombo")
        for feed in self.FEED_OPTIONS:
            self.cmbFeed.addItem(str(feed), feed)
        self.cmbFeed.setCurrentIndex(2)  # Default 5000
        jog_grid.addWidget(self.cmbFeed, 2, 2)

        jog_lay.addLayout(jog_grid)

        left_col.addWidget(jog_card)
        main_row.addLayout(left_col, 1)

        # Right: Homing and Brakes
        right_col = QVBoxLayout()
        right_col.setSpacing(16)

        # Homing card
        home_card = make_card("ХОМІНГ")
        home_lay = home_card.layout()

        home_grid = QGridLayout()
        home_grid.setSpacing(12)

        self.btnHomingAll = QPushButton("ХОМІНГ")
        self.btnHomingAll.setObjectName("homeButton")
        self.btnHomingAll.setMinimumHeight(50)
        self.btnHomingAll.clicked.connect(self._do_homing)
        home_grid.addWidget(self.btnHomingAll, 0, 0, 1, 2)

        self.btnHomingX = QPushButton("ХОМ X")
        self.btnHomingX.setObjectName("homeButtonSmall")
        self.btnHomingX.setMinimumHeight(45)
        self.btnHomingX.clicked.connect(lambda: self._do_homing_axis("x"))
        home_grid.addWidget(self.btnHomingX, 1, 0)

        self.btnHomingY = QPushButton("ХОМ Y")
        self.btnHomingY.setObjectName("homeButtonSmall")
        self.btnHomingY.setMinimumHeight(45)
        self.btnHomingY.clicked.connect(lambda: self._do_homing_axis("y"))
        home_grid.addWidget(self.btnHomingY, 1, 1)

        self.btnWorkZero = QPushButton("В роб. 0")
        self.btnWorkZero.setObjectName("homeButtonSmall")
        self.btnWorkZero.setMinimumHeight(45)
        self.btnWorkZero.clicked.connect(self._go_to_work_zero)
        home_grid.addWidget(self.btnWorkZero, 2, 0, 1, 2)

        self.btnToOperator = QPushButton("До оператора")
        self.btnToOperator.setObjectName("homeButtonSmall")
        self.btnToOperator.setMinimumHeight(45)
        self.btnToOperator.clicked.connect(self._go_to_operator)
        home_grid.addWidget(self.btnToOperator, 3, 0, 1, 2)

        home_lay.addLayout(home_grid)
        right_col.addWidget(home_card)

        # Brakes card
        brake_card = make_card("ГАЛЬМА")
        brake_lay = brake_card.layout()

        brake_grid = QGridLayout()
        brake_grid.setSpacing(12)

        brake_grid.addWidget(QLabel("Гальмо X:"), 0, 0)
        self.btnBrakeX = QPushButton("--")
        self.btnBrakeX.setObjectName("brakeButton")
        self.btnBrakeX.setMinimumHeight(45)
        self.btnBrakeX.clicked.connect(self._toggle_brake_x)
        brake_grid.addWidget(self.btnBrakeX, 0, 1)

        brake_grid.addWidget(QLabel("Гальмо Y:"), 1, 0)
        self.btnBrakeY = QPushButton("--")
        self.btnBrakeY.setObjectName("brakeButton")
        self.btnBrakeY.setMinimumHeight(45)
        self.btnBrakeY.clicked.connect(self._toggle_brake_y)
        brake_grid.addWidget(self.btnBrakeY, 1, 1)

        brake_lay.addLayout(brake_grid)
        right_col.addWidget(brake_card)

        # Stop button
        self.btnStop = QPushButton("СТОП")
        self.btnStop.setObjectName("stopButton")
        self.btnStop.setMinimumHeight(60)
        self.btnStop.clicked.connect(self._do_stop)
        right_col.addWidget(self.btnStop)

        right_col.addStretch()
        main_row.addLayout(right_col, 1)

        root.addLayout(main_row)

    def _check_brakes(self) -> bool:
        """Check if both brakes are released (ON). Returns True if movement allowed."""
        if not self._brake_x_on or not self._brake_y_on:
            from PyQt5.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Гальма")
            msg.setText("Вимкніть гальма X та Y\nперед переміщенням!")
            msg.setStyleSheet("""
                QMessageBox {
                    background-color: #2b2b2b;
                    border: 3px solid #f44336;
                    border-radius: 12px;
                }
                QMessageBox QLabel {
                    color: #ffffff;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 20px;
                }
                QMessageBox QPushButton {
                    background-color: #ff9800;
                    color: #000000;
                    font-size: 16px;
                    font-weight: bold;
                    padding: 10px 30px;
                    border: none;
                    border-radius: 6px;
                    min-width: 100px;
                    margin: 10px auto;
                }
                QMessageBox QPushButton:hover {
                    background-color: #ffa726;
                }
                QMessageBox QDialogButtonBox {
                    qproperty-centerButtons: true;
                }
            """)
            msg.exec_()
            return False
        return True

    def _jog(self, dx_mult: int, dy_mult: int):
        """Jog in direction by step amount."""
        if not self._check_brakes():
            return
        step = self.cmbStep.currentData()
        feed = self.cmbFeed.currentData()
        dx = dx_mult * step
        dy = dy_mult * step
        try:
            self.api.xy_jog(dx=dx, dy=dy, feed=feed)
        except Exception as e:
            print(f"Jog failed: {e}")

    def _do_homing(self):
        """Full homing sequence."""
        if not self._check_brakes():
            return
        try:
            self.api.xy_home()
        except Exception as e:
            print(f"Homing failed: {e}")

    def _do_homing_axis(self, axis: str):
        """Home single axis."""
        if not self._check_brakes():
            return
        try:
            self.api.xy_home(axis=axis)
        except Exception as e:
            print(f"Homing {axis} failed: {e}")

    def _go_to_work_zero(self):
        """Move to work zero position."""
        if not self._check_brakes():
            return
        try:
            self.api.xy_move(self._offset_x, self._offset_y, 5000)
        except Exception as e:
            print(f"Move to work zero failed: {e}")

    def _go_to_operator(self):
        """Move to operator position (X=110, Y=500)."""
        if not self._check_brakes():
            return
        try:
            self.api.xy_move(110, 500, 5000)
        except Exception as e:
            print(f"Move to operator failed: {e}")

    def _toggle_brake_x(self):
        """Toggle X brake."""
        try:
            new_state = "off" if self._brake_x_on else "on"
            self.api.relay_set("r02_brake_x", new_state)
        except Exception as e:
            print(f"Brake X toggle failed: {e}")

    def _toggle_brake_y(self):
        """Toggle Y brake."""
        try:
            new_state = "off" if self._brake_y_on else "on"
            self.api.relay_set("r03_brake_y", new_state)
        except Exception as e:
            print(f"Brake Y toggle failed: {e}")

    def _do_stop(self):
        """Emergency stop XY movement."""
        try:
            self.api.xy_stop()
        except Exception as e:
            print(f"Stop failed: {e}")

    def render(self, status: dict):
        """Update UI from status."""
        # Update position from XY table status
        xy = status.get("xy_table", {})
        sensors = status.get("sensors", {})

        # Get homing status and emergency stop
        x_homed = xy.get("x_homed", False)
        y_homed = xy.get("y_homed", False)
        estop = sensors.get("emergency_stop") == "ACTIVE"

        # Get physical coordinates
        if x_homed and not estop:
            self._current_x = xy.get("x", 0.0)
        if y_homed and not estop:
            self._current_y = xy.get("y", 0.0)

        # Get offsets for work position
        try:
            offsets = self.api.get_offsets()
            self._offset_x = offsets.get("x", 0.0)
            self._offset_y = offsets.get("y", 0.0)
        except Exception:
            pass

        # Main display shows WORK coordinates (matching web UI)
        if x_homed and not estop:
            work_x = f"{self._current_x - self._offset_x:.2f}"
        else:
            work_x = "?.??"

        if y_homed and not estop:
            work_y = f"{self._current_y - self._offset_y:.2f}"
        else:
            work_y = "?.??"

        self.lblPosition.setText(f"X: {work_x}   Y: {work_y}")

        # Secondary display shows physical coordinates
        if x_homed and not estop:
            phys_x = f"{self._current_x:.2f}"
        else:
            phys_x = "?.??"

        if y_homed and not estop:
            phys_y = f"{self._current_y:.2f}"
        else:
            phys_y = "?.??"

        self.lblWorkPosition.setText(f"Фізичні: X: {phys_x}   Y: {phys_y}")

        # Update brake status from relays
        relays = status.get("relays", {})
        brake_x_state = relays.get("r02_brake_x", "OFF")
        brake_y_state = relays.get("r03_brake_y", "OFF")

        self._brake_x_on = brake_x_state == "ON"
        self._brake_y_on = brake_y_state == "ON"

        self.btnBrakeX.setText("ON" if self._brake_x_on else "OFF")
        self.btnBrakeX.setProperty("active", self._brake_x_on)
        self.btnBrakeX.style().unpolish(self.btnBrakeX)
        self.btnBrakeX.style().polish(self.btnBrakeX)

        self.btnBrakeY.setText("ON" if self._brake_y_on else "OFF")
        self.btnBrakeY.setProperty("active", self._brake_y_on)
        self.btnBrakeY.style().unpolish(self.btnBrakeY)
        self.btnBrakeY.style().polish(self.btnBrakeY)


# ================== Main Window ==================
class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("3SD-SF Screw Feed")
        self.setObjectName("root")

        self.api = ApiClient()

        # Central widget
        self.frame = QFrame()
        self.frame.setObjectName("rootFrame")
        self.frame.setProperty("state", "idle")
        self.setCentralWidget(self.frame)

        # Main layout
        root = QVBoxLayout(self.frame)
        root.setContentsMargins(BORDER_W, BORDER_W, BORDER_W, BORDER_W)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        self.tabs.setDocumentMode(True)  # Cleaner look, tabs expand better
        root.addWidget(self.tabs)

        # Hide tab bar by default - show/hide with 5s pedal hold
        self._tabs_visible = False
        self._pedal_hold_start = None  # Time when pedal press started
        self.tabs.tabBar().setVisible(False)
        self.tabs.tabBar().setExpanding(True)  # Tabs fill available width equally
        self.tabs.tabBar().setUsesScrollButtons(False)  # No scroll buttons

        # Create tabs - new structure
        self.tabStartWork = StartWorkTab(self.api)
        self.tabControl = ControlTab(self.api)
        self.tabPlatform = PlatformTab(self.api)
        self.tabLogs = LogsTab(self.api)
        self.tabService = ServiceTab(self.api)

        # Connect tab name change signal
        self.tabStartWork.tabNameChanged.connect(self._on_tab_name_changed)

        self.tabs.addTab(self.tabStartWork, "СТАРТ")
        self.tabs.addTab(self.tabControl, "КЕРУВАННЯ")
        self.tabs.addTab(self.tabPlatform, "СТІЛ")
        self.tabs.addTab(self.tabLogs, "ЛОГИ")
        self.tabs.addTab(self.tabService, "СЕРВІС")

        # Timer for status polling
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        # Fullscreen on Raspberry Pi
        self.showFullScreen()
        screen = QApplication.primaryScreen()
        if screen:
            self.setFixedSize(screen.size())

    def _on_tab_name_changed(self, new_name: str):
        """Handle tab name change from StartWorkTab."""
        self.tabs.setTabText(0, new_name)

    def set_border(self, state: str):
        """Set border state (ok/idle/alarm)."""
        self.frame.setProperty("state", state)
        self.frame.style().unpolish(self.frame)
        self.frame.style().polish(self.frame)

    def refresh(self):
        """Poll API and update UI."""
        try:
            status = self.api.status()
        except Exception:
            self.set_border("alarm")
            return

        # Check pedal hold for tab visibility toggle
        # Condition: must be in START mode (not WORK) + pedal held for 5 seconds
        sensors = status.get("sensors", {})
        pedal_pressed = sensors.get("ped_start") == "ACTIVE"
        in_start_mode = self.tabStartWork._current_mode == self.tabStartWork.MODE_START

        if in_start_mode and pedal_pressed:
            if self._pedal_hold_start is None:
                # Pedal just pressed in START mode - start tracking
                self._pedal_hold_start = time.time()
            else:
                # Pedal still held - check duration
                hold_duration = time.time() - self._pedal_hold_start
                if hold_duration >= 5.0:
                    # Toggle tab visibility
                    self._tabs_visible = not self._tabs_visible
                    self.tabs.tabBar().setVisible(self._tabs_visible)
                    # Reset timer to avoid repeated toggling
                    self._pedal_hold_start = None
        else:
            # Not in START mode or pedal released - reset timer
            self._pedal_hold_start = None

        # Update tabs
        for tab in (self.tabStartWork, self.tabControl, self.tabPlatform, self.tabLogs, self.tabService):
            try:
                tab.render(status)
            except Exception as e:
                print(f"Render error: {e}")

        # Border state (sensors already fetched above for pedal check)
        estop = sensors.get("emergency_stop") == "ACTIVE"

        if estop:
            self.set_border("alarm")
        else:
            xy = status.get("xy_table", {})
            if xy.get("state") == "READY":
                self.set_border("ok")
            else:
                self.set_border("idle")


# ================== Stylesheet ==================
APP_QSS = f"""
/* Main background */
#root {{ background-color: {COLORS['bg_primary']}; }}

/* Border states */
#rootFrame[state="ok"]    {{ border: {BORDER_W}px solid {COLORS['green']}; }}
#rootFrame[state="idle"]  {{ border: {BORDER_W}px solid {COLORS['yellow']}; }}
#rootFrame[state="alarm"] {{ border: {BORDER_W}px solid {COLORS['red']}; }}

/* Cards */
#card {{
    background: {COLORS['bg_secondary']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
    color: {COLORS['text']};
}}
#cardTitle {{
    font-size: 18px;
    font-weight: 600;
    color: {COLORS['text']};
    padding-bottom: 12px;
    border-bottom: 1px solid {COLORS['border']};
}}

/* Tabs - matching web UI style */
#tabs::pane {{ border: none; background: transparent; }}
QTabWidget::pane {{ background: transparent; border: none; }}
QTabWidget {{
    border: none;
    background: transparent;
}}
QTabWidget::tab-bar {{
    alignment: center;
    border: none;
}}
QTabBar {{
    background: {COLORS['bg_secondary']};
    border-radius: 12px;
    padding: 4px;
    border: none;
}}
QTabBar::scroller {{
    width: 0px;
}}
QTabBar::tab {{
    color: {COLORS['text_secondary']};
    background: {COLORS['bg_card']};
    padding: 14px 20px;
    margin: 3px;
    border-radius: 8px;
    font-size: 17px;
    font-weight: 600;
    min-height: 45px;
    min-width: 120px;
}}
QTabBar::tab:hover {{
    background: {COLORS['border_light']};
    color: {COLORS['text']};
}}
QTabBar::tab:selected {{
    background: {COLORS['blue']};
    color: white;
}}

/* Device buttons */
#devButton {{
    font-size: 18px;
    font-weight: 500;
    text-align: left;
    padding: 14px 16px;
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
}}
#devButton:hover {{ background: {COLORS['bg_card']}; }}
#devButton[selected="true"] {{
    border-color: {COLORS['blue']};
    background: {COLORS['blue']};
    color: white;
}}

/* Big buttons */
#btn_primary {{
    font-size: 28px;
    font-weight: 600;
    background: {COLORS['green']};
    color: white;
    border: none;
    border-radius: 8px;
}}
#btn_primary:hover {{ background: #5ab887; }}
#btn_primary:disabled {{ opacity: 0.5; }}

#btn_info {{
    font-size: 28px;
    font-weight: 600;
    background: {COLORS['blue']};
    color: white;
    border: none;
    border-radius: 8px;
}}
#btn_info:hover {{ background: {COLORS['blue_hover']}; }}

#btn_danger {{
    font-size: 28px;
    font-weight: 600;
    background: {COLORS['red']};
    color: white;
    border: none;
    border-radius: 8px;
}}
#btn_danger:hover {{ background: #d64545; }}

#btn_estop {{
    font-size: 32px;
    font-weight: 700;
    background: {COLORS['red']};
    color: white;
    border: 3px solid #ff4444;
    border-radius: 12px;
}}
#btn_estop:hover {{ background: #d64545; }}

/* Big INIT button */
#btn_init_big {{
    font-size: 56px;
    font-weight: 600;
    background: {COLORS['blue']};
    color: white;
    border: none;
    border-radius: 16px;
}}
#btn_init_big:hover {{ background: {COLORS['blue_hover']}; }}
#btn_init_big:disabled {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
}}

/* WORK mode buttons */
#btn_work_start {{
    font-size: 48px;
    font-weight: 600;
    background: {COLORS['green']};
    color: white;
    border: none;
    border-radius: 16px;
}}
#btn_work_start:hover {{ background: #5ab887; }}
#btn_work_start:disabled {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
}}

#btn_work_stop {{
    font-size: 48px;
    font-weight: 600;
    background: {COLORS['red']};
    color: white;
    border: none;
    border-radius: 16px;
}}
#btn_work_stop:hover {{ background: #d64545; }}

/* Platform tab buttons */
#btn_home {{
    font-size: 18px;
    font-weight: 500;
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['yellow']};
    border-radius: 8px;
}}
#btn_home:hover {{ background: {COLORS['bg_card']}; }}

#btn_offset {{
    font-size: 18px;
    font-weight: 500;
    background: {COLORS['blue']};
    color: white;
    border: none;
    border-radius: 8px;
}}
#btn_offset:hover {{ background: {COLORS['blue_hover']}; }}

#btn_move {{
    font-size: 20px;
    font-weight: 500;
    background: {COLORS['green']};
    color: white;
    border: none;
    border-radius: 8px;
}}
#btn_move:hover {{ background: #5ab887; }}

#btn_toggle {{
    font-size: 16px;
    font-weight: 500;
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border_light']};
    border-radius: 8px;
}}
#btn_toggle:checked {{
    background: {COLORS['green']};
    color: white;
    border: none;
}}

/* Work mode labels */
#workStatusLabel {{
    font-size: 22px;
    font-weight: 500;
    color: {COLORS['text']};
}}

#workCounterLabel {{
    font-size: 28px;
    font-weight: 600;
    color: {COLORS['green']};
}}

#workMessage {{
    font-size: 24px;
    font-weight: 500;
    color: {COLORS['text']};
    padding: 16px;
    background: {COLORS['bg_secondary']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
}}

/* Work progress bar */
#workProgressBar {{
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    background: {COLORS['bg_input']};
    height: 40px;
    text-align: center;
    font-size: 18px;
    font-weight: 500;
}}
#workProgressBar::chunk {{
    background: {COLORS['green']};
    border-radius: 6px;
}}

/* Position value */
#positionValue {{
    font-size: 22px;
    font-weight: 600;
    color: {COLORS['green']};
}}

/* Status values */
#statusValue {{
    font-size: 20px;
    font-weight: 500;
    color: {COLORS['text']};
}}
#statusMessage {{
    font-size: 16px;
    color: {COLORS['text_secondary']};
    padding: 8px;
}}
#statusTaskTorque {{
    font-size: 18px;
    font-weight: 500;
    color: {COLORS['blue']};
    padding: 4px 0;
}}

/* Badges */
#badge {{
    background: {COLORS['bg_card']};
    color: {COLORS['text']};
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 14px;
    font-weight: 500;
}}

/* Sensor states */
#sensorState {{
    font-size: 16px;
    font-weight: 600;
    padding: 6px 10px;
}}
#sensorState[active="true"] {{ color: {COLORS['green']}; }}
#sensorState[active="false"] {{ color: {COLORS['red']}; }}

/* Relay states */
#relayState {{
    font-size: 16px;
    font-weight: 600;
    padding: 6px 10px;
}}
#relayState[on="true"] {{ color: {COLORS['green']}; }}
#relayState[on="false"] {{ color: {COLORS['text_muted']}; }}

/* Generic buttons */
QPushButton {{
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 12px 20px;
    font-size: 16px;
}}
QPushButton:hover {{ background: {COLORS['bg_card']}; }}
QPushButton:disabled {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
}}

/* Inputs */
QSpinBox, QLineEdit, QComboBox {{
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 16px;
}}
QSpinBox:focus, QLineEdit:focus, QComboBox:focus {{
    border-color: {COLORS['blue']};
}}

/* ComboBox dropdown styling */
QComboBox::drop-down {{
    border: none;
    width: 30px;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS['text']};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {COLORS['bg_secondary']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {COLORS['blue']};
    selection-color: white;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 10px 14px;
    min-height: 30px;
    background: transparent;
    color: {COLORS['text']};
}}
QComboBox QAbstractItemView::item:hover {{
    background: {COLORS['bg_card']};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {COLORS['blue']};
    color: white;
}}

/* Scroll area */
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* Progress bar */
QProgressBar {{
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    background: {COLORS['bg_input']};
    height: 32px;
    text-align: center;
    font-size: 14px;
    color: {COLORS['text']};
}}
QProgressBar::chunk {{
    background: {COLORS['green']};
    border-radius: 6px;
}}

/* Labels */
QLabel {{
    color: {COLORS['text']};
    font-size: 16px;
}}

/* Log text area */
#logTextArea {{
    background: {COLORS['bg_primary']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 12px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 13px;
    line-height: 1.4;
}}

/* Filter label */
#filterLabel {{
    font-size: 18px;
    font-weight: 500;
    color: {COLORS['text']};
}}

/* Service tab styles */
#serviceLabelLarge {{
    font-size: 18px;
    font-weight: 500;
    color: {COLORS['text_secondary']};
}}
#serviceIpValue {{
    font-size: 22px;
    font-weight: 600;
    color: {COLORS['blue']};
}}
#serviceStatusOnline {{
    font-size: 18px;
    font-weight: 600;
    color: {COLORS['green']};
}}

/* Refresh button */
#btn_refresh {{
    font-size: 20px;
    font-weight: 500;
    background: {COLORS['bg_card']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 0;
}}
#btn_refresh:hover {{ background: {COLORS['blue']}; color: white; }}

/* Sensor box - Active state (green) */
#sensorBoxActive {{
    background: {COLORS['green_bg']};
    border: 2px solid {COLORS['green']};
    border-radius: 12px;
}}
#sensorNameActive {{
    font-size: 16px;
    font-weight: 600;
    color: {COLORS['green']};
}}

/* Sensor box - Inactive state (red) */
#sensorBoxInactive {{
    background: {COLORS['red_bg']};
    border: 2px solid {COLORS['red']};
    border-radius: 12px;
}}
#sensorNameInactive {{
    font-size: 16px;
    font-weight: 600;
    color: {COLORS['red']};
}}

/* Relay box */
#relayBox {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
}}
#relayNameCompact {{
    font-size: 14px;
    font-weight: 600;
    color: {COLORS['text']};
}}

/* Relay toggle button - ON state */
#btn_relay_toggle_on {{
    font-size: 18px;
    font-weight: 700;
    background: {COLORS['green']};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px;
}}
#btn_relay_toggle_on:hover {{ background: #5ab887; }}
#btn_relay_toggle_on:pressed {{ background: #4a9877; }}

/* Relay toggle button - OFF state */
#btn_relay_toggle_off {{
    font-size: 18px;
    font-weight: 700;
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
    border: 2px solid {COLORS['border']};
    border-radius: 8px;
    padding: 10px;
}}
#btn_relay_toggle_off:hover {{ background: {COLORS['red']}; color: white; border-color: {COLORS['red']}; }}
#btn_relay_toggle_off:pressed {{ background: #d64545; }}

/* Scrollbar */
QScrollBar:vertical {{
    background: {COLORS['bg_input']};
    width: 12px;
    border-radius: 6px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border_light']};
    border-radius: 6px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['text_muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* Control Tab - XY Position display */
#xyPositionLarge {{
    font-size: 48px;
    font-weight: 700;
    color: {COLORS['green']};
    padding: 20px;
}}
#xyPositionSmall {{
    font-size: 20px;
    font-weight: 500;
    color: {COLORS['text_secondary']};
    padding: 8px;
}}

/* Control Tab - Jog buttons */
#jogButton {{
    font-size: 24px;
    font-weight: 700;
    background: {COLORS['blue']};
    color: white;
    border: none;
    border-radius: 12px;
    min-width: 100px;
    min-height: 70px;
}}
#jogButton:hover {{ background: {COLORS['blue_hover']}; }}
#jogButton:pressed {{ background: #2563eb; }}

#jogButtonHome {{
    font-size: 32px;
    font-weight: 700;
    background: {COLORS['yellow']};
    color: {COLORS['bg_primary']};
    border: none;
    border-radius: 12px;
    min-width: 100px;
    min-height: 70px;
}}
#jogButtonHome:hover {{ background: #d4a021; }}
#jogButtonHome:pressed {{ background: #c4901a; }}

/* Control Tab - Home buttons */
#homeButton {{
    font-size: 22px;
    font-weight: 600;
    background: {COLORS['yellow']};
    color: {COLORS['bg_primary']};
    border: none;
    border-radius: 10px;
}}
#homeButton:hover {{ background: #d4a021; }}
#homeButton:pressed {{ background: #c4901a; }}

#homeButtonSmall {{
    font-size: 18px;
    font-weight: 600;
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 2px solid {COLORS['yellow']};
    border-radius: 8px;
}}
#homeButtonSmall:hover {{ background: {COLORS['yellow']}; color: {COLORS['bg_primary']}; }}

/* Control Tab - Brake buttons */
#brakeButton {{
    font-size: 20px;
    font-weight: 700;
    background: {COLORS['bg_input']};
    color: {COLORS['text_muted']};
    border: 2px solid {COLORS['border']};
    border-radius: 10px;
    min-width: 100px;
}}
#brakeButton[active="true"] {{
    background: {COLORS['green']};
    color: white;
    border-color: {COLORS['green']};
}}
#brakeButton:hover {{ border-color: {COLORS['yellow']}; }}

/* Control Tab - Stop button */
#stopButton {{
    font-size: 28px;
    font-weight: 700;
    background: {COLORS['red']};
    color: white;
    border: 3px solid #ff4444;
    border-radius: 12px;
}}
#stopButton:hover {{ background: #d64545; }}
#stopButton:pressed {{ background: #c53535; }}

/* Control Tab - ComboBox */
#controlCombo {{
    font-size: 18px;
    font-weight: 500;
    padding: 12px 16px;
    min-width: 100px;
}}
"""


def main():
    app = QApplication(sys.argv)

    # Hide cursor for touch screen
    app.setOverrideCursor(QCursor(Qt.BlankCursor))

    # Apply stylesheet
    app.setStyleSheet(APP_QSS)

    # Set default font
    font = QFont()
    font.setPixelSize(16)
    app.setFont(font)

    # Create and show window
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
