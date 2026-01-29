"""
Relay Controller for Screw Drive System.

Manages all relay outputs for screwdriver, pneumatics, and other actuators.
"""

import time
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from .gpio_controller import GPIOController, get_gpio


class RelayState(Enum):
    """Relay state enumeration."""
    OFF = 0
    ON = 1
    UNKNOWN = -1


@dataclass
class RelayConfig:
    """Configuration for a single relay."""
    gpio: int
    active_high: bool = True
    description: str = ""
    initial_state: RelayState = RelayState.OFF


class RelayController:
    """
    Controller for managing multiple relays.

    Supports:
    - Named relay access
    - Active high/low configuration
    - Pulse generation
    - State tracking
    """

    # Default relay configuration
    # NOTE: Relay pins must NOT conflict with sensor pins (17, 18, 22, 23, 12, 25)
    # R01 - Screw feeder (pulse to release screw)
    # R02 - Task 2 selection (700ms pulse)
    # R04 - Cylinder control (ON=down, OFF=up)
    # R05 - Free run mode (hold ON = screwdriver spins)
    # R06 - Torque mode (hold ON until DO2_OK)
    # R07 - Task 0 selection (700ms pulse)
    # R08 - Task 1 selection (700ms pulse)
    # Relays are active_low: GPIO LOW = relay ON, GPIO HIGH = relay OFF
    DEFAULT_RELAYS = {
        'r01_pit': RelayConfig(gpio=5, active_high=False, description="Живильник гвинтів"),
        'r02_di7_tsk2': RelayConfig(gpio=6, active_high=False, description="Вибір задачі 2"),
        'r04_c2': RelayConfig(gpio=16, active_high=False, description="Циліндр відкрутки"),
        'r05_di4_free': RelayConfig(gpio=19, active_high=False, description="Вільний хід"),
        'r06_di1_pot': RelayConfig(gpio=20, active_high=False, description="Режим по моменту"),
        'r07_di5_tsk0': RelayConfig(gpio=21, active_high=False, description="Вибір задачі 0"),
        'r08_di6_tsk1': RelayConfig(gpio=26, active_high=False, description="Вибір задачі 1"),
    }

    # Pulse durations in seconds
    TASK_PULSE_DURATION = 0.7  # 700ms for task selection
    FEEDER_PULSE_DURATION = 0.2  # 200ms for screw feeder

    def __init__(self, gpio: Optional[GPIOController] = None,
                 relays: Optional[Dict[str, RelayConfig]] = None):
        """
        Initialize relay controller.

        Args:
            gpio: GPIO controller instance (uses global if None)
            relays: Dictionary of relay configurations
        """
        self._gpio = gpio or get_gpio()
        self._relays = relays or self.DEFAULT_RELAYS.copy()
        self._states: Dict[str, RelayState] = {}
        self._initialized = False

    def init(self) -> bool:
        """
        Initialize all relays as outputs.

        Returns:
            True if all relays initialized successfully.
        """
        if self._initialized:
            return True

        if not self._gpio.is_initialized:
            if not self._gpio.init():
                return False

        success = True
        for name, config in self._relays.items():
            # Calculate initial GPIO level based on active_high and initial state
            initial_on = config.initial_state == RelayState.ON
            if config.active_high:
                level = 1 if initial_on else 0
            else:
                level = 0 if initial_on else 1

            if self._gpio.setup_output(config.gpio, level):
                self._states[name] = config.initial_state
            else:
                print(f"ERROR: Failed to initialize relay '{name}' on GPIO {config.gpio}")
                success = False
                self._states[name] = RelayState.UNKNOWN

        self._initialized = success
        return success

    def set(self, name: str, state: bool) -> bool:
        """
        Set relay state.

        Args:
            name: Relay name
            state: True for ON, False for OFF

        Returns:
            True if successful.
        """
        if name not in self._relays:
            print(f"ERROR: Unknown relay '{name}'")
            return False

        config = self._relays[name]

        # Calculate GPIO level
        if config.active_high:
            level = 1 if state else 0
        else:
            level = 0 if state else 1

        if self._gpio.write(config.gpio, level):
            self._states[name] = RelayState.ON if state else RelayState.OFF
            return True

        return False

    def on(self, name: str) -> bool:
        """Turn relay ON."""
        return self.set(name, True)

    def off(self, name: str) -> bool:
        """Turn relay OFF."""
        return self.set(name, False)

    def toggle(self, name: str) -> bool:
        """Toggle relay state."""
        current = self.get_state(name)
        if current == RelayState.UNKNOWN:
            return False
        return self.set(name, current == RelayState.OFF)

    def pulse(self, name: str, duration_s: float = 0.5) -> bool:
        """
        Pulse relay ON for specified duration.

        Args:
            name: Relay name
            duration_s: Pulse duration in seconds

        Returns:
            True if successful.
        """
        if not self.on(name):
            return False
        time.sleep(duration_s)
        return self.off(name)

    def get_state(self, name: str) -> RelayState:
        """Get current relay state."""
        return self._states.get(name, RelayState.UNKNOWN)

    def is_on(self, name: str) -> bool:
        """Check if relay is ON."""
        return self.get_state(name) == RelayState.ON

    def all_off(self) -> bool:
        """Turn all relays OFF."""
        success = True
        for name in self._relays:
            if not self.off(name):
                success = False
        return success

    def get_all_states(self) -> Dict[str, str]:
        """Get all relay states as dictionary."""
        return {name: state.name for name, state in self._states.items()}

    @property
    def relay_names(self) -> list:
        """Get list of available relay names."""
        return list(self._relays.keys())

    # ==========================================
    # Convenience methods for common operations
    # ==========================================

    def feed_screw(self) -> bool:
        """
        Feed a screw from the feeder.
        Pulses R01_PIT to release one screw.
        """
        return self.pulse('r01_pit', self.FEEDER_PULSE_DURATION)

    def select_task(self, task: int) -> bool:
        """
        Select screwdriver task/program (0, 1, or 2).
        Sends 700ms pulse to corresponding relay.

        Args:
            task: Task number (0, 1, or 2)

        Returns:
            True if successful.
        """
        task_relays = {
            0: 'r07_di5_tsk0',
            1: 'r08_di6_tsk1',
            2: 'r02_di7_tsk2',
        }
        if task not in task_relays:
            print(f"ERROR: Invalid task number {task}. Must be 0, 1, or 2.")
            return False
        return self.pulse(task_relays[task], self.TASK_PULSE_DURATION)

    def cylinder_down(self) -> bool:
        """
        Move cylinder DOWN (extend).
        R04_C2 ON = cylinder goes down.
        """
        return self.on('r04_c2')

    def cylinder_up(self) -> bool:
        """
        Move cylinder UP (retract).
        R04_C2 OFF = cylinder goes up automatically.
        """
        return self.off('r04_c2')

    def screwdriver_free_start(self) -> bool:
        """
        Start screwdriver in FREE RUN mode.
        Screwdriver spins while R05_DI4_FREE is ON.
        """
        return self.on('r05_di4_free')

    def screwdriver_free_stop(self) -> bool:
        """
        Stop screwdriver FREE RUN mode.
        """
        return self.off('r05_di4_free')

    def screwdriver_torque_start(self) -> bool:
        """
        Start screwdriver in TORQUE mode.
        Screwdriver runs until torque is reached (DO2_OK signal).
        R06_DI1_POT should be held ON until torque OK.
        """
        return self.on('r06_di1_pot')

    def screwdriver_torque_stop(self) -> bool:
        """
        Stop screwdriver TORQUE mode.
        Call this when DO2_OK signal is received.
        """
        return self.off('r06_di1_pot')

    def emergency_stop(self) -> bool:
        """
        Emergency stop - turn off all relays.
        Cylinder will automatically retract (R04 OFF = up).
        """
        return self.all_off()

    def is_cylinder_down(self) -> bool:
        """Check if cylinder relay is active (cylinder going down)."""
        return self.is_on('r04_c2')

    def is_screwdriver_running(self) -> bool:
        """Check if screwdriver is running in any mode."""
        return self.is_on('r05_di4_free') or self.is_on('r06_di1_pot')
