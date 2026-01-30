"""
Sensor Controller for Screw Drive System.

Manages all sensor inputs including safety sensors, position feedback,
and screwdriver status signals.
"""

import time
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum
import threading

from .gpio_controller import GPIOController, get_gpio


class SensorState(Enum):
    """Sensor state enumeration."""
    INACTIVE = 0
    ACTIVE = 1
    UNKNOWN = -1


@dataclass
class SensorConfig:
    """Configuration for a single sensor."""
    gpio: int
    active_low: bool = True
    pull_up: bool = True
    description: str = ""
    debounce_ms: int = 10


class SensorController:
    """
    Controller for managing multiple sensors.

    Supports:
    - Named sensor access
    - Active high/low configuration
    - Debouncing
    - Callback registration for state changes
    - Continuous monitoring thread
    """

    # Default sensor configuration
    # Pin assignments based on actual hardware connections
    DEFAULT_SENSORS = {
        # Світлова завіса - HIGH=вільно, LOW=заблоковано
        'area_sensor': SensorConfig(
            gpio=17, active_low=True, pull_up=True,
            description="Світлова завіса безпеки", debounce_ms=100
        ),
        # Педаль старту - HIGH=відпущена, LOW=натиснута
        'ped_start': SensorConfig(
            gpio=18, active_low=True, pull_up=True,
            description="Педаль старту циклу", debounce_ms=50
        ),
        # Циліндр вгорі - ACTIVE=циліндр у верхньому положенні
        # Геркони Festo N/C (normally closed) - замкнуті без магніту
        'ger_c2_up': SensorConfig(
            gpio=22, active_low=False, pull_up=True,
            description="Циліндр вгорі", debounce_ms=10
        ),
        # Циліндр внизу - АВАРІЯ! ACTIVE=досяг низу, вимкнути R04_C2
        # Геркони Festo N/C (normally closed) - замкнуті без магніту
        'ger_c2_down': SensorConfig(
            gpio=23, active_low=False, pull_up=True,
            description="Циліндр внизу - АВАРІЯ!", debounce_ms=10
        ),
        # Індуктивний датчик гвинта - HIGH=немає, LOW=гвинт пройшов
        'ind_scrw': SensorConfig(
            gpio=12, active_low=True, pull_up=True,
            description="Індуктивний датчик гвинта", debounce_ms=10
        ),
        # Сигнал досягнення моменту - HIGH=не досягнуто, LOW=момент OK
        'do2_ok': SensorConfig(
            gpio=25, active_low=True, pull_up=True,
            description="Момент досягнуто - OK", debounce_ms=10
        ),
    }

    def __init__(self, gpio: Optional[GPIOController] = None,
                 sensors: Optional[Dict[str, SensorConfig]] = None):
        """
        Initialize sensor controller.

        Args:
            gpio: GPIO controller instance (uses global if None)
            sensors: Dictionary of sensor configurations
        """
        self._gpio = gpio or get_gpio()
        self._sensors = sensors or self.DEFAULT_SENSORS.copy()
        self._states: Dict[str, SensorState] = {}
        self._last_raw: Dict[str, int] = {}
        self._last_change: Dict[str, float] = {}
        self._callbacks: Dict[str, list] = {}
        self._initialized = False

        # Monitoring thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._monitor_interval = 0.01  # 10ms

    def init(self) -> bool:
        """
        Initialize all sensors as inputs.

        Returns:
            True if all sensors initialized successfully.
        """
        if self._initialized:
            return True

        if not self._gpio.is_initialized:
            if not self._gpio.init():
                return False

        success = True
        for name, config in self._sensors.items():
            if self._gpio.setup_input(config.gpio, pull_up=config.pull_up):
                self._states[name] = SensorState.UNKNOWN
                self._last_change[name] = 0
                self._callbacks[name] = []
            else:
                print(f"ERROR: Failed to initialize sensor '{name}' on GPIO {config.gpio}")
                success = False

        self._initialized = success

        # Initial read
        if success:
            self._update_all_sensors()

        return success

    def read(self, name: str) -> SensorState:
        """
        Read sensor state with debouncing.

        Args:
            name: Sensor name

        Returns:
            SensorState (ACTIVE, INACTIVE, or UNKNOWN)
        """
        if name not in self._sensors:
            print(f"ERROR: Unknown sensor '{name}'")
            return SensorState.UNKNOWN

        config = self._sensors[name]
        raw = self._gpio.read(config.gpio)

        if raw is None:
            return SensorState.UNKNOWN

        # Apply debouncing
        now = time.time()
        if name in self._last_raw:
            if raw != self._last_raw[name]:
                # Value changed, check debounce time
                if now - self._last_change.get(name, 0) < config.debounce_ms / 1000.0:
                    # Still in debounce period, return old state
                    return self._states.get(name, SensorState.UNKNOWN)
                self._last_change[name] = now

        self._last_raw[name] = raw

        # Apply active logic
        if config.active_low:
            active = raw == 0
        else:
            active = raw == 1

        new_state = SensorState.ACTIVE if active else SensorState.INACTIVE

        # Check for state change and trigger callbacks
        old_state = self._states.get(name)
        if old_state != new_state:
            self._states[name] = new_state
            self._trigger_callbacks(name, new_state, old_state)

        return new_state

    def is_active(self, name: str) -> bool:
        """Check if sensor is active."""
        return self.read(name) == SensorState.ACTIVE

    def is_inactive(self, name: str) -> bool:
        """Check if sensor is inactive."""
        return self.read(name) == SensorState.INACTIVE

    def read_raw(self, name: str) -> Optional[int]:
        """Read raw GPIO value (0 or 1)."""
        if name not in self._sensors:
            return None
        return self._gpio.read(self._sensors[name].gpio)

    def get_all_states(self) -> Dict[str, str]:
        """Get all sensor states as dictionary."""
        self._update_all_sensors()
        return {name: state.name for name, state in self._states.items()}

    def _update_all_sensors(self) -> None:
        """Update all sensor states."""
        for name in self._sensors:
            self.read(name)

    def register_callback(self, name: str,
                          callback: Callable[[str, SensorState, SensorState], None]) -> bool:
        """
        Register callback for sensor state changes.

        Args:
            name: Sensor name
            callback: Function(sensor_name, new_state, old_state)

        Returns:
            True if registered successfully.
        """
        if name not in self._sensors:
            return False
        self._callbacks[name].append(callback)
        return True

    def unregister_callback(self, name: str,
                            callback: Callable[[str, SensorState, SensorState], None]) -> bool:
        """Unregister a callback."""
        if name not in self._callbacks:
            return False
        try:
            self._callbacks[name].remove(callback)
            return True
        except ValueError:
            return False

    def _trigger_callbacks(self, name: str, new_state: SensorState,
                           old_state: Optional[SensorState]) -> None:
        """Trigger all callbacks for a sensor."""
        for callback in self._callbacks.get(name, []):
            try:
                callback(name, new_state, old_state)
            except Exception as e:
                print(f"ERROR: Callback error for sensor '{name}': {e}")

    def start_monitoring(self, interval_s: float = 0.01) -> None:
        """
        Start background monitoring thread.

        Args:
            interval_s: Polling interval in seconds
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return

        self._monitor_interval = interval_s
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop background monitoring thread."""
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while not self._monitor_stop.is_set():
            self._update_all_sensors()
            time.sleep(self._monitor_interval)

    @property
    def sensor_names(self) -> list:
        """Get list of available sensor names."""
        return list(self._sensors.keys())

    # Convenience methods for common checks

    def is_safe(self) -> bool:
        """Check if all safety sensors allow operation."""
        area_ok = self.is_inactive('area_sensor')  # Area should be clear (no obstruction)
        cylinder_emergency_ok = self.is_inactive('ger_c2_down')  # Cylinder not at emergency position
        return area_ok and cylinder_emergency_ok

    def is_area_blocked(self) -> bool:
        """Check if safety area is blocked (obstruction detected)."""
        return self.is_active('area_sensor')

    def is_area_clear(self) -> bool:
        """Check if safety area is clear (no obstruction)."""
        return self.is_inactive('area_sensor')

    def is_pedal_pressed(self) -> bool:
        """Check if start pedal is pressed."""
        return self.is_active('ped_start')

    def is_pedal_released(self) -> bool:
        """Check if start pedal is released."""
        return self.is_inactive('ped_start')

    def is_cylinder_up(self) -> bool:
        """Check if cylinder is in UP (retracted) position."""
        return self.is_active('ger_c2_up')

    def is_cylinder_down_emergency(self) -> bool:
        """
        Check if cylinder reached DOWN position (EMERGENCY!).
        If True, must immediately turn off R04_C2!
        """
        return self.is_active('ger_c2_down')

    def is_screw_detected(self) -> bool:
        """Check if screw passed through tube (inductive sensor)."""
        return self.is_active('ind_scrw')

    def is_screw_absent(self) -> bool:
        """Check if no screw detected (tube empty)."""
        return self.is_inactive('ind_scrw')

    def is_torque_reached(self) -> bool:
        """Check if torque limit was reached (screw tightened)."""
        return self.is_active('do2_ok')

    def is_torque_not_reached(self) -> bool:
        """Check if torque not yet reached."""
        return self.is_inactive('do2_ok')

    def wait_for(self, name: str, state: SensorState,
                 timeout_s: float = 10.0) -> bool:
        """
        Wait for sensor to reach specified state.

        Args:
            name: Sensor name
            state: Target state
            timeout_s: Timeout in seconds

        Returns:
            True if state reached, False if timeout.
        """
        start = time.time()
        while time.time() - start < timeout_s:
            if self.read(name) == state:
                return True
            time.sleep(0.01)
        return False

    def wait_for_active(self, name: str, timeout_s: float = 10.0) -> bool:
        """Wait for sensor to become active."""
        return self.wait_for(name, SensorState.ACTIVE, timeout_s)

    def wait_for_inactive(self, name: str, timeout_s: float = 10.0) -> bool:
        """Wait for sensor to become inactive."""
        return self.wait_for(name, SensorState.INACTIVE, timeout_s)
