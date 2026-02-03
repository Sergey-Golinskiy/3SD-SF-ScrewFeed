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

# ================== Colors (matching web UI) ==================
COLORS = {
    'bg_dark': '#0f1115',
    'bg_card': '#1a1f29',
    'bg_input': '#1f2531',
    'border': '#2a3140',
    'border_light': '#3a4356',
    'text': '#e8edf8',
    'text_muted': '#9aa7be',
    'green': '#1ac06b',
    'green_bg': '#153f2c',
    'red': '#e5484d',
    'red_bg': '#3a1c1c',
    'yellow': '#f0b400',
    'blue': '#3aa0ff',
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

    # Cycle
    def cycle_estop(self):
        return self._post("cycle/estop")

    def cycle_clear_estop(self):
        return self._post("cycle/clear_estop")

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

    def _check_and_reset_alarms(self) -> list:
        """
        Check motor driver alarms and reset if needed.
        Returns list of warning messages for operator.

        If alarm is active on X or Y axis:
        - Pulse corresponding power relay for 700ms to reset driver
        - Driver will restart when power is cycled
        """
        warnings = []
        try:
            sensors = self.api.sensors()

            # Check X axis alarm (GPIO 2)
            alarm_x = sensors.get("alarm_x")
            if alarm_x == "ACTIVE":
                self.progress.emit("⚠️ Аларм драйвера X! Скидання...", 3)
                self._sync_progress("⚠️ Аларм драйвера X! Скидання...", 3)
                # Pulse R09 (X driver power) for 700ms to reset
                # Relay ON = power OFF, then OFF = power ON
                self.api.relay_set("r09_pwr_x", "on")  # Power OFF
                time.sleep(0.7)  # 700ms
                self.api.relay_set("r09_pwr_x", "off")  # Power ON
                time.sleep(0.5)  # Wait for driver to initialize
                warnings.append("Драйвер X було скинуто через аларм")

            # Check Y axis alarm (GPIO 3)
            alarm_y = sensors.get("alarm_y")
            if alarm_y == "ACTIVE":
                self.progress.emit("⚠️ Аларм драйвера Y! Скидання...", 3)
                self._sync_progress("⚠️ Аларм драйвера Y! Скидання...", 3)
                # Pulse R10 (Y driver power) for 700ms to reset
                self.api.relay_set("r10_pwr_y", "on")  # Power OFF
                time.sleep(0.7)  # 700ms
                self.api.relay_set("r10_pwr_y", "off")  # Power ON
                time.sleep(0.5)  # Wait for driver to initialize
                warnings.append("Драйвер Y було скинуто через аларм")

        except Exception as e:
            warnings.append(f"Не вдалося перевірити аларми: {e}")

        return warnings

    def run(self):
        try:
            # Step 0: Check and reset motor driver alarms
            self.progress.emit("Перевірка алармів драйверів...", 2)
            self._sync_progress("Перевірка алармів драйверів...", 2)
            alarm_warnings = self._check_and_reset_alarms()

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

            # Step 2: Homing
            self.progress.emit("Виконується хомінг XY столу...", 25)
            self._sync_progress("Виконується хомінг XY столу...", 25)
            home_resp = self.api.xy_home()
            if home_resp.get("status") != "homed":
                raise Exception("Не вдалося запустити хомінг")

            # Wait for homing to complete (15 seconds timeout)
            start_time = time.time()
            while time.time() - start_time < 15:
                if self._abort:
                    return
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

            # Step 7: Move to work position
            self.progress.emit("Виїзд до оператора...", 85)
            self._sync_progress("Виїзд до оператора...", 85)
            work_x = self.device.get("work_x")
            work_y = self.device.get("work_y")
            work_feed = self.device.get("work_feed", 5000)

            if work_x is None or work_y is None:
                raise Exception("Робоча позиція не задана для цього девайсу")

            move_resp = self.api.xy_move(work_x, work_y, work_feed)
            if move_resp.get("status") != "ok":
                raise Exception("Не вдалося виїхати до робочої позиції")

            # Wait for move to complete
            time.sleep(0.5)

            # Build final message with warnings if any
            if alarm_warnings:
                final_msg = "Ініціалізація завершена з попередженнями:\n" + "\n".join(alarm_warnings)
            else:
                final_msg = ""

            self.progress.emit("Ініціалізація завершена!", 100)
            self.finished_ok.emit(final_msg)

        except Exception as e:
            # Safety: turn off cylinder relay
            try:
                self.api.relay_set("r04_c2", "off")
            except:
                pass
            self.finished_error.emit(str(e))


# ================== Cycle Worker ==================
class CycleWorker(QThread):
    """Worker thread for screwing cycle execution."""
    progress = pyqtSignal(str, int, int, int)  # message, holes_completed, total_holes, progress_percent
    finished_ok = pyqtSignal(int)  # holes_completed
    finished_error = pyqtSignal(str)

    # Special error for driver alarm - requires device removal and reinit
    DRIVER_ALARM_ERROR = "DRIVER_ALARM"

    def __init__(self, api: ApiClient, device: dict):
        super().__init__()
        self.api = api
        self.device = device
        self._abort = False

    def abort(self):
        self._abort = True

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
        Also checks for driver alarms during movement.
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
            time.sleep(0.1)
        return False

    def _wait_for_sensor(self, sensor: str, expected: str, timeout: float = 10.0) -> bool:
        """
        Wait for sensor to reach expected state.
        Also checks for driver alarms while waiting.
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

    def run(self):
        try:
            steps = self.device.get("steps", [])
            if not steps:
                raise Exception("Девайс не має координат")

            work_steps = [s for s in steps if (s.get("type") or "").lower() == "work"]
            total_holes = len(work_steps)
            holes_completed = 0

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

                if step_type == "free":
                    # Free movement - just move
                    self.progress.emit(f"Переміщення X:{step_x:.1f} Y:{step_y:.1f}", holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)

                    # Check alarm before sending move command
                    self._check_alarm_and_raise()

                    resp = self.api.xy_move(step_x, step_y, step_feed)
                    if resp.get("status") != "ok":
                        raise Exception("Помилка переміщення")

                    # _wait_for_move also checks alarms
                    self._wait_for_move()

                elif step_type == "work":
                    # Work position - move and screw
                    msg = f"Закручування ({holes_completed + 1}/{total_holes}) X:{step_x:.1f} Y:{step_y:.1f}"
                    self.progress.emit(msg, holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)
                    self._sync_progress(msg, holes_completed, total_holes)

                    # Check alarm before move
                    self._check_alarm_and_raise()

                    # Move to position
                    resp = self.api.xy_move(step_x, step_y, step_feed)
                    if resp.get("status") != "ok":
                        raise Exception("Помилка переміщення")

                    # _wait_for_move also checks alarms
                    self._wait_for_move()

                    # _perform_screwing has alarm checks inside
                    self._perform_screwing()

                    holes_completed += 1
                    msg = f"Закручено: {holes_completed} / {total_holes}"
                    self.progress.emit(msg, holes_completed, total_holes,
                                      int((holes_completed / total_holes) * 100) if total_holes > 0 else 0)
                    self._sync_progress(msg, holes_completed, total_holes)

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
            else:
                self._safety_shutdown()
                self.finished_error.emit(error_str)


# ================== Control Tab ==================
class ControlTab(QWidget):
    """Main control tab - device selection, init, start, stop."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._devices = []
        self._selected_device = None
        self._cycle_state = "IDLE"
        self._initialized = False
        self._last_server_state_time = 0
        self._total_cycles = 0
        self._init_worker = None
        self._cycle_worker = None

        self._setup_ui()

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(20)

        # Left column - Device selection
        left = QVBoxLayout()
        left.setSpacing(12)

        # Device card
        self.devCard = make_card("Вибір девайсу")
        dev_lay = self.devCard.layout()

        self.devScroll = QScrollArea()
        self.devScroll.setWidgetResizable(True)
        self.devScroll.setMinimumWidth(280)
        self.devList = QWidget()
        self.devListLay = QVBoxLayout(self.devList)
        self.devListLay.setContentsMargins(0, 0, 0, 0)
        self.devListLay.setSpacing(8)
        self.devScroll.setWidget(self.devList)
        dev_lay.addWidget(self.devScroll)

        left.addWidget(self.devCard)

        # Right column - Controls
        right = QVBoxLayout()
        right.setSpacing(12)

        # Status card
        self.statusCard = make_card("Статус циклу")
        status_lay = self.statusCard.layout()

        status_grid = QGridLayout()
        status_grid.setSpacing(12)

        status_grid.addWidget(QLabel("Стан:"), 0, 0)
        self.lblState = QLabel("IDLE")
        self.lblState.setObjectName("statusValue")
        status_grid.addWidget(self.lblState, 0, 1)

        status_grid.addWidget(QLabel("Девайс:"), 1, 0)
        self.lblDevice = QLabel("-")
        self.lblDevice.setObjectName("statusValue")
        status_grid.addWidget(self.lblDevice, 1, 1)

        status_grid.addWidget(QLabel("Прогрес:"), 2, 0)
        self.lblProgress = QLabel("0 / 0")
        self.lblProgress.setObjectName("statusValue")
        status_grid.addWidget(self.lblProgress, 2, 1)

        status_lay.addLayout(status_grid)

        # Progress bar
        self.progressBar = QProgressBar()
        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(100)
        self.progressBar.setValue(0)
        status_lay.addWidget(self.progressBar)

        # Status message
        self.lblMessage = QLabel("Виберіть девайс для початку роботи")
        self.lblMessage.setObjectName("statusMessage")
        self.lblMessage.setWordWrap(True)
        status_lay.addWidget(self.lblMessage)

        right.addWidget(self.statusCard)

        # Control buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self.btnInit = big_button("ІНІЦІАЛІЗАЦІЯ", "info")
        self.btnStart = big_button("START", "primary")
        self.btnStop = big_button("STOP", "danger")

        self.btnInit.clicked.connect(self.on_init)
        self.btnStart.clicked.connect(self.on_start)
        self.btnStop.clicked.connect(self.on_stop)

        self.btnStart.setEnabled(False)

        btn_row.addWidget(self.btnInit)
        btn_row.addWidget(self.btnStart)
        btn_row.addWidget(self.btnStop)

        right.addLayout(btn_row, 1)

        # E-STOP button
        self.btnEstop = big_button("E-STOP", "estop")
        self.btnEstop.setMinimumHeight(80)
        self.btnEstop.clicked.connect(self.on_estop)
        right.addWidget(self.btnEstop)

        # Layout ratio
        root.addLayout(left, 3)
        root.addLayout(right, 7)

        # Device buttons dict
        self._device_buttons = {}

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

            btn = QPushButton(f"{key}\n{holes} отворів")
            btn.setObjectName("devButton")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(70)
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

    def _select_device(self, key: str):
        """Select a device."""
        self._selected_device = key
        self._initialized = False
        self._cycle_state = "IDLE"
        self._update_device_styles()

        self.lblDevice.setText(key)
        self.lblState.setText("IDLE")
        self.lblMessage.setText(f"Девайс {key} вибрано. Натисніть ІНІЦІАЛІЗАЦІЯ.")
        self.btnStart.setEnabled(False)
        self.btnInit.setEnabled(True)

        # Sync to server for web UI
        try:
            self.api.select_device(key)
        except Exception as e:
            print(f"Device selection sync failed: {e}")

    def _sync_state_to_server(self, cycle_state: str, message: str = "", progress_percent: int = 0, current_step: str = ""):
        """Sync current state to server for web UI."""
        try:
            # Get total holes from selected device
            total_holes = 0
            for dev in self._devices:
                if dev.get("key") == self._selected_device:
                    total_holes = dev.get("holes", 0)
                    break

            self.api.set_ui_state({
                "selected_device": self._selected_device,
                "cycle_state": cycle_state,
                "initialized": self._initialized,
                "holes_completed": 0,
                "total_holes": total_holes,
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
            self.lblMessage.setText("Спочатку виберіть девайс!")
            return

        # Check if web is already operating
        try:
            server_state = self.api.get_ui_state()
            if server_state.get("operator") == "web":
                self.lblMessage.setText("Web UI виконує операцію. Зачекайте...")
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
            self.lblMessage.setText("Девайс не знайдено!")
            return

        # Try to get full device data from API
        try:
            device = self.api.device(self._selected_device)
        except Exception as e:
            print(f"Failed to load device details: {e}")

        self._cycle_state = "INITIALIZING"
        self.lblState.setText("INITIALIZING")
        self.lblMessage.setText("Ініціалізація...")
        self.progressBar.setValue(0)
        self.btnInit.setEnabled(False)
        self.btnStart.setEnabled(False)

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
        self.lblMessage.setText(message)
        self.progressBar.setValue(progress)

    def _on_init_success(self, warnings: str):
        """Called when initialization completes successfully."""
        self._initialized = True
        self._cycle_state = "READY"
        self.lblState.setText("READY")

        # Show warnings if any driver alarms were reset
        if warnings:
            self.lblMessage.setText(warnings)
        else:
            self.lblMessage.setText("Ініціалізація завершена. Натисніть START для запуску циклу.")

        self.progressBar.setValue(100)
        self.btnInit.setEnabled(True)
        self.btnStart.setEnabled(True)
        self._init_worker = None

        # Sync state to server
        self._sync_state_to_server("READY", "Готово до запуску")

    def _on_init_error(self, error_msg: str):
        """Called when initialization fails."""
        self._initialized = False
        self._cycle_state = "INIT_ERROR"
        self.lblState.setText("INIT_ERROR")
        self.lblMessage.setText(f"ПОМИЛКА: {error_msg}")
        self.progressBar.setValue(0)
        self.btnInit.setEnabled(True)
        self.btnStart.setEnabled(False)
        self._init_worker = None

        # Sync state to server
        self._sync_state_to_server("INIT_ERROR", f"Помилка: {error_msg}")

    def on_start(self):
        """Handle START button."""
        if not self._selected_device or not self._initialized:
            self.lblMessage.setText("Спочатку виконайте ініціалізацію!")
            return

        # Check if web is already operating
        try:
            server_state = self.api.get_ui_state()
            if server_state.get("operator") == "web":
                self.lblMessage.setText("Web UI виконує операцію. Зачекайте...")
                return
        except Exception:
            pass

        # Get device data with steps
        device = None
        try:
            device = self.api.device(self._selected_device)
        except Exception as e:
            self.lblMessage.setText(f"Не вдалося завантажити девайс: {e}")
            return

        if not device or not device.get("steps"):
            self.lblMessage.setText("Девайс не має координат для закручування!")
            return

        self._cycle_state = "RUNNING"
        self.lblState.setText("RUNNING")
        self.lblMessage.setText("Цикл виконується...")
        self.progressBar.setValue(0)
        self.btnStart.setEnabled(False)
        self.btnInit.setEnabled(False)

        # Sync state to server
        self._sync_state_to_server("RUNNING", "Цикл виконується", 0, "Запуск циклу")

        # Start cycle worker
        self._cycle_worker = CycleWorker(self.api, device)
        self._cycle_worker.progress.connect(self._on_cycle_progress)
        self._cycle_worker.finished_ok.connect(self._on_cycle_success)
        self._cycle_worker.finished_error.connect(self._on_cycle_error)
        self._cycle_worker.start()

    def _on_cycle_progress(self, message: str, holes: int, total: int, pct: int):
        """Handle cycle progress updates."""
        self.lblMessage.setText(message)
        self.lblProgress.setText(f"{holes} / {total}")
        self.progressBar.setValue(pct)

    def _on_cycle_success(self, holes_completed: int):
        """Called when cycle completes successfully."""
        self._total_cycles += 1
        self._cycle_state = "COMPLETED"
        self.lblState.setText("COMPLETED")
        self.lblMessage.setText(f"Цикл завершено! Закручено {holes_completed} гвинтів. Натисніть START для наступного.")
        self.progressBar.setValue(100)
        self.btnStart.setEnabled(True)
        self.btnInit.setEnabled(True)
        self._cycle_worker = None

        # Sync state to server
        self._sync_state_to_server("COMPLETED", f"Цикл завершено! Закручено {holes_completed} гвинтів.", 100, "Цикл завершено")

    def _on_cycle_error(self, error_msg: str):
        """Called when cycle fails."""
        self._cycle_state = "ERROR"
        self.lblState.setText("ERROR")
        self.lblMessage.setText(f"ПОМИЛКА: {error_msg}")
        self.progressBar.setValue(0)
        self.btnStart.setEnabled(True)
        self.btnInit.setEnabled(True)
        self._cycle_worker = None

        # Special handling for torque error
        if error_msg == "TORQUE_NOT_REACHED":
            self._cycle_state = "PAUSED"
            self.lblState.setText("PAUSED")
            self.lblMessage.setText("Момент не досягнуто. Перевірте гвинт та натисніть START.")
            self._sync_state_to_server("PAUSED", "Момент не досягнуто", 0, "Помилка моменту")
        else:
            self._sync_state_to_server("ERROR", f"Помилка: {error_msg}", 0, "Помилка циклу")

    def on_stop(self):
        """Handle STOP button."""
        # Abort init worker if running
        if self._init_worker and self._init_worker.isRunning():
            self._init_worker.abort()
            self._init_worker = None

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
        self._initialized = False
        self.lblState.setText("STOPPED")
        self.lblMessage.setText("Цикл зупинено оператором.")
        self.progressBar.setValue(0)
        self.btnStart.setEnabled(False)
        self.btnInit.setEnabled(True)

        # Sync state to server
        self._sync_state_to_server("STOPPED", "Цикл зупинено")

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
        self.lblState.setText("E-STOP")
        self.lblMessage.setText("АВАРІЙНА ЗУПИНКА! Натисніть Clear E-Stop для продовження.")
        self.progressBar.setValue(0)
        self.btnStart.setEnabled(False)
        self.btnInit.setEnabled(False)

        # Sync state to server
        self._sync_state_to_server("E-STOP", "Аварійна зупинка")

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

        # Update cycle status from status dict
        cycle = status.get("cycle", {})
        xy = status.get("xy_table", {})
        sensors = status.get("sensors", {})

        # Check E-STOP
        estop = sensors.get("emergency_stop") == "ACTIVE"
        if estop:
            self.lblState.setText("E-STOP")
            self.lblState.setProperty("state", "error")

        # XY Table state
        xy_state = xy.get("state", "DISCONNECTED")

    def _check_server_ui_state(self):
        """Check if server UI state was updated by web client."""
        try:
            server_state = self.api.get_ui_state()

            # Check if web is actively operating
            web_is_operating = server_state.get("operator") == "web"

            # Always update if web is operating (to show live progress)
            if web_is_operating or (server_state.get("updated_at", 0) > self._last_server_state_time and
                                    server_state.get("updated_by") == "web"):

                self._last_server_state_time = server_state.get("updated_at", 0)

                # Update device selection from web
                new_device = server_state.get("selected_device")
                if new_device != self._selected_device:
                    self._selected_device = new_device
                    self._update_device_styles()
                    self.lblDevice.setText(new_device or "-")

                # Update cycle state from web
                new_state = server_state.get("cycle_state", "IDLE")
                self._cycle_state = new_state
                self.lblState.setText(new_state)

                # Update progress bar and message when web is operating
                if web_is_operating:
                    progress_pct = server_state.get("progress_percent", 0)
                    current_step = server_state.get("current_step", "")
                    message = server_state.get("message", "")

                    self.progressBar.setValue(progress_pct)
                    self.lblMessage.setText(current_step or message or "Web UI виконує операцію...")

                    # Disable buttons while web is operating
                    self.btnInit.setEnabled(False)
                    self.btnStart.setEnabled(False)
                else:
                    # Update buttons based on state when web is not operating
                    if new_state in ("IDLE", "STOPPED", "ERROR", "INIT_ERROR"):
                        self.btnInit.setEnabled(bool(self._selected_device))
                        self.btnStart.setEnabled(False)
                        self._initialized = False
                    elif new_state == "READY":
                        self.btnInit.setEnabled(True)
                        self.btnStart.setEnabled(True)
                        self._initialized = True
                    elif new_state in ("RUNNING", "INITIALIZING", "RETURNING"):
                        self.btnInit.setEnabled(False)
                        self.btnStart.setEnabled(False)
                    elif new_state == "COMPLETED":
                        self.btnInit.setEnabled(True)
                        self.btnStart.setEnabled(True)
                        self._initialized = True

                # Update progress
                holes = server_state.get("holes_completed", 0)
                total = server_state.get("total_holes", 0)
                self.lblProgress.setText(f"{holes} / {total}")

                # Update cycles count
                cycles = server_state.get("cycles_completed", 0)
                if cycles > self._total_cycles:
                    self._total_cycles = cycles

            # Update our timestamp if we're the latest
            if server_state.get("updated_by") == "desktop":
                self._last_server_state_time = server_state.get("updated_at", 0)

        except Exception as e:
            # Ignore errors - server might not have endpoint yet
            pass


# ================== Service Tab ==================
class ServiceTab(QWidget):
    """Service tab - sensors and relay control."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._relay_widgets = {}

        self._setup_ui()

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(20)

        # Left - Sensors
        left = QVBoxLayout()
        left.setSpacing(12)

        self.sensorsCard = make_card("Сенсори")
        self.sensorsGrid = QGridLayout()
        self.sensorsGrid.setSpacing(8)
        self.sensorsCard.layout().addLayout(self.sensorsGrid)
        left.addWidget(self.sensorsCard)
        left.addStretch(1)

        # Right - Relays
        right = QVBoxLayout()
        right.setSpacing(12)

        self.relaysCard = make_card("Реле (ON / OFF / PULSE)")
        self.relaysGrid = QGridLayout()
        self.relaysGrid.setSpacing(8)
        self.relaysCard.layout().addLayout(self.relaysGrid)
        right.addWidget(self.relaysCard)

        # Network info
        self.netCard = make_card("Мережа")
        net_lay = self.netCard.layout()
        self.lblIp = QLabel(f"IP: {get_local_ip()}")
        self.lblIp.setObjectName("statusValue")
        net_lay.addWidget(self.lblIp)
        right.addWidget(self.netCard)

        right.addStretch(1)

        root.addLayout(left, 1)
        root.addLayout(right, 1)

    def _create_relay_row(self, row: int, name: str, state: str):
        """Create relay control row."""
        # Name label
        lblName = QLabel(name)
        lblName.setObjectName("badge")

        # State label
        is_on = state == "ON"
        lblState = QLabel("ON" if is_on else "OFF")
        lblState.setObjectName("relayState")
        lblState.setProperty("on", is_on)

        # Duration spinner
        spin = QSpinBox()
        spin.setRange(50, 5000)
        spin.setValue(200)
        spin.setSuffix(" ms")
        spin.setFixedWidth(100)

        # Buttons
        btnOn = QPushButton("ON")
        btnOff = QPushButton("OFF")
        btnPulse = QPushButton("PULSE")

        btnOn.clicked.connect(lambda: self._relay_cmd(name, "on"))
        btnOff.clicked.connect(lambda: self._relay_cmd(name, "off"))
        btnPulse.clicked.connect(lambda: self._relay_cmd(name, "pulse", spin.value() / 1000.0))

        self.relaysGrid.addWidget(lblName, row, 0)
        self.relaysGrid.addWidget(lblState, row, 1)
        self.relaysGrid.addWidget(btnOn, row, 2)
        self.relaysGrid.addWidget(btnOff, row, 3)
        self.relaysGrid.addWidget(spin, row, 4)
        self.relaysGrid.addWidget(btnPulse, row, 5)

        self._relay_widgets[name] = (lblState, spin, btnOn, btnOff, btnPulse)

    def _relay_cmd(self, name: str, action: str, duration: float = None):
        """Send relay command."""
        try:
            self.api.relay_set(name, action, duration)
        except Exception as e:
            print(f"Relay command error: {e}")

    def render(self, status: dict):
        """Update UI from status."""
        sensors = status.get("sensors", {})
        relays = status.get("relays", {})

        # Update sensors grid
        while self.sensorsGrid.count():
            item = self.sensorsGrid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        row = 0
        for name, value in sensors.items():
            lblName = QLabel(name)
            lblName.setObjectName("badge")

            is_active = value == "ACTIVE" or value == True
            lblValue = QLabel("ACTIVE" if is_active else "INACTIVE")
            lblValue.setObjectName("sensorState")
            lblValue.setProperty("active", is_active)
            lblValue.style().unpolish(lblValue)
            lblValue.style().polish(lblValue)

            self.sensorsGrid.addWidget(lblName, row, 0)
            self.sensorsGrid.addWidget(lblValue, row, 1)
            row += 1

        # Update relays
        relay_names = list(relays.keys())

        if set(relay_names) != set(self._relay_widgets.keys()):
            # Rebuild relay grid
            while self.relaysGrid.count():
                item = self.relaysGrid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._relay_widgets.clear()

            for i, name in enumerate(relay_names):
                self._create_relay_row(i, name, relays.get(name, "OFF"))
        else:
            # Update states
            for name, widgets in self._relay_widgets.items():
                lblState = widgets[0]
                is_on = relays.get(name) == "ON"
                lblState.setText("ON" if is_on else "OFF")
                lblState.setProperty("on", is_on)
                lblState.style().unpolish(lblState)
                lblState.style().polish(lblState)


# ================== XY Tab ==================
class XYTab(QWidget):
    """XY Table control tab."""

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(20)

        # Status card
        self.statusCard = make_card("XY Стіл - Статус")
        status_lay = self.statusCard.layout()

        grid = QGridLayout()
        grid.setSpacing(12)

        grid.addWidget(QLabel("Стан:"), 0, 0)
        self.lblState = QLabel("-")
        self.lblState.setObjectName("statusValue")
        grid.addWidget(self.lblState, 0, 1)

        grid.addWidget(QLabel("Позиція:"), 1, 0)
        self.lblPosition = QLabel("X: ?.?? Y: ?.??")
        self.lblPosition.setObjectName("statusValue")
        grid.addWidget(self.lblPosition, 1, 1)

        grid.addWidget(QLabel("Homed:"), 2, 0)
        self.lblHomed = QLabel("X: ? Y: ?")
        self.lblHomed.setObjectName("statusValue")
        grid.addWidget(self.lblHomed, 2, 1)

        grid.addWidget(QLabel("Endstops:"), 3, 0)
        self.lblEndstops = QLabel("-")
        self.lblEndstops.setObjectName("statusValue")
        grid.addWidget(self.lblEndstops, 3, 1)

        status_lay.addLayout(grid)
        root.addWidget(self.statusCard)

        # Control buttons
        btn_card = make_card("Керування")
        btn_lay = btn_card.layout()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self.btnHomeAll = QPushButton("HOME ALL")
        self.btnHomeX = QPushButton("HOME X")
        self.btnHomeY = QPushButton("HOME Y")
        self.btnStop = QPushButton("STOP")
        self.btnStop.setObjectName("btn_danger")

        self.btnHomeAll.clicked.connect(lambda: self._do_home(None))
        self.btnHomeX.clicked.connect(lambda: self._do_home("X"))
        self.btnHomeY.clicked.connect(lambda: self._do_home("Y"))
        self.btnStop.clicked.connect(self._do_stop)

        btn_row.addWidget(self.btnHomeAll)
        btn_row.addWidget(self.btnHomeX)
        btn_row.addWidget(self.btnHomeY)
        btn_row.addWidget(self.btnStop)

        btn_lay.addLayout(btn_row)
        root.addWidget(btn_card)

        root.addStretch(1)

    def _do_home(self, axis: str = None):
        """Execute homing."""
        try:
            self.api.xy_home(axis)
        except Exception as e:
            print(f"Home error: {e}")

    def _do_stop(self):
        """Stop XY table."""
        try:
            self.api.xy_stop()
        except Exception as e:
            print(f"Stop error: {e}")

    def render(self, status: dict):
        """Update UI from status."""
        xy = status.get("xy_table", {})
        sensors = status.get("sensors", {})

        # State
        state = xy.get("state", "DISCONNECTED")
        self.lblState.setText(state)

        # Position
        x_homed = xy.get("x_homed", False)
        y_homed = xy.get("y_homed", False)
        estop = sensors.get("emergency_stop") == "ACTIVE"

        if x_homed and not estop:
            x_pos = f"{xy.get('x', 0):.2f}"
        else:
            x_pos = "?.??"

        if y_homed and not estop:
            y_pos = f"{xy.get('y', 0):.2f}"
        else:
            y_pos = "?.??"

        self.lblPosition.setText(f"X: {x_pos}  Y: {y_pos}")

        # Homed status
        x_h = "YES" if x_homed else "NO"
        y_h = "YES" if y_homed else "NO"
        self.lblHomed.setText(f"X: {x_h}  Y: {y_h}")

        # Endstops
        endstops = xy.get("endstops", {})
        x_min = "TRIG" if endstops.get("x_min") else "open"
        y_min = "TRIG" if endstops.get("y_min") else "open"
        self.lblEndstops.setText(f"X_MIN: {x_min}  Y_MIN: {y_min}")


# ================== Main Window ==================
class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScrewDrive TouchDesk")
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
        root.addWidget(self.tabs)

        # Create tabs
        self.tabControl = ControlTab(self.api)
        self.tabService = ServiceTab(self.api)
        self.tabXY = XYTab(self.api)

        self.tabs.addTab(self.tabControl, "КЕРУВАННЯ")
        self.tabs.addTab(self.tabXY, "XY СТІЛ")
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

        # Update tabs
        for tab in (self.tabControl, self.tabService, self.tabXY):
            try:
                tab.render(status)
            except Exception as e:
                print(f"Render error: {e}")

        # Border state
        sensors = status.get("sensors", {})
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
#root {{ background-color: {COLORS['bg_dark']}; }}

/* Border states */
#rootFrame[state="ok"]    {{ border: {BORDER_W}px solid {COLORS['green']}; }}
#rootFrame[state="idle"]  {{ border: {BORDER_W}px solid {COLORS['yellow']}; }}
#rootFrame[state="alarm"] {{ border: {BORDER_W}px solid {COLORS['red']}; }}

/* Cards */
#card {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 16px;
    color: {COLORS['text']};
}}
#cardTitle {{
    font-size: 20px;
    font-weight: 600;
    color: {COLORS['text']};
}}

/* Tabs */
#tabs::pane {{ border: none; }}
QTabBar::tab {{
    color: {COLORS['text_muted']};
    background: {COLORS['bg_card']};
    padding: 14px 32px;
    margin-right: 4px;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    font-size: 24px;
    font-weight: 700;
    min-height: 60px;
    min-width: 200px;
    border: 1px solid {COLORS['border']};
}}
QTabBar::tab:selected {{
    background: {COLORS['bg_input']};
    color: white;
    border-bottom: 4px solid {COLORS['green']};
}}

/* Device buttons */
#devButton {{
    font-size: 20px;
    font-weight: 600;
    text-align: left;
    padding: 12px 16px;
    border: 2px solid {COLORS['border_light']};
    border-radius: 12px;
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
}}
#devButton:hover {{ background: {COLORS['bg_card']}; }}
#devButton[selected="true"] {{
    border-color: {COLORS['green']};
    background: {COLORS['green_bg']};
    color: #e9ffee;
}}

/* Big buttons */
#btn_primary {{
    font-size: 32px;
    font-weight: 700;
    background: {COLORS['green_bg']};
    color: #e9ffee;
    border: 3px solid {COLORS['green']};
    border-radius: 16px;
}}
#btn_primary:hover {{ background: #1a5235; }}
#btn_primary:disabled {{ opacity: 0.5; }}

#btn_info {{
    font-size: 32px;
    font-weight: 700;
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 3px solid {COLORS['blue']};
    border-radius: 16px;
}}
#btn_info:hover {{ background: #1a3050; }}

#btn_danger {{
    font-size: 32px;
    font-weight: 700;
    background: {COLORS['red_bg']};
    color: #ffe9e9;
    border: 3px solid {COLORS['red']};
    border-radius: 16px;
}}
#btn_danger:hover {{ background: #4a2525; }}

#btn_estop {{
    font-size: 36px;
    font-weight: 900;
    background: {COLORS['red']};
    color: white;
    border: 4px solid #ff0000;
    border-radius: 16px;
}}
#btn_estop:hover {{ background: #ff3333; }}

/* Status values */
#statusValue {{
    font-size: 22px;
    font-weight: 600;
    color: {COLORS['text']};
}}
#statusMessage {{
    font-size: 18px;
    color: {COLORS['text_muted']};
    padding: 10px;
}}

/* Badges */
#badge {{
    background: {COLORS['border']};
    color: {COLORS['text']};
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 18px;
    font-weight: 600;
}}

/* Sensor states */
#sensorState {{
    font-size: 18px;
    font-weight: 700;
    padding: 6px 10px;
}}
#sensorState[active="true"] {{ color: {COLORS['green']}; }}
#sensorState[active="false"] {{ color: {COLORS['red']}; }}

/* Relay states */
#relayState {{
    font-size: 18px;
    font-weight: 700;
    padding: 6px 10px;
}}
#relayState[on="true"] {{ color: {COLORS['green']}; }}
#relayState[on="false"] {{ color: {COLORS['text_muted']}; }}

/* Generic buttons */
QPushButton {{
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border_light']};
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 18px;
}}
QPushButton:hover {{ background: {COLORS['bg_card']}; }}
QPushButton:disabled {{ opacity: 0.5; }}

/* Inputs */
QSpinBox, QLineEdit, QComboBox {{
    background: {COLORS['bg_input']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border_light']};
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 18px;
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
    border-radius: 10px;
    background: {COLORS['bg_input']};
    height: 28px;
    text-align: center;
    font-size: 16px;
}}
QProgressBar::chunk {{
    background: {COLORS['green']};
    border-radius: 8px;
}}

/* Labels */
QLabel {{
    color: {COLORS['text']};
    font-size: 18px;
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
