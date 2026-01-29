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
    DEFAULT_RELAYS = {
        'screwdriver_power': RelayConfig(gpio=4, active_high=True, description="Screwdriver motor"),
        'screwdriver_direction': RelayConfig(gpio=27, active_high=True, description="Direction CW/CCW"),
        'cylinder_down': RelayConfig(gpio=5, active_high=True, description="Cylinder extend"),
        'cylinder_up': RelayConfig(gpio=6, active_high=True, description="Cylinder retract"),
        'vacuum': RelayConfig(gpio=24, active_high=True, description="Vacuum gripper"),
        'blow': RelayConfig(gpio=13, active_high=True, description="Air blow"),
    }

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

    # Convenience methods for common operations
    def screwdriver_start(self, clockwise: bool = True) -> bool:
        """Start screwdriver motor."""
        self.set('screwdriver_direction', clockwise)
        time.sleep(0.05)  # Direction setup delay
        return self.on('screwdriver_power')

    def screwdriver_stop(self) -> bool:
        """Stop screwdriver motor."""
        return self.off('screwdriver_power')

    def cylinder_extend(self) -> bool:
        """Extend pneumatic cylinder."""
        self.off('cylinder_up')
        time.sleep(0.05)
        return self.on('cylinder_down')

    def cylinder_retract(self) -> bool:
        """Retract pneumatic cylinder."""
        self.off('cylinder_down')
        time.sleep(0.05)
        return self.on('cylinder_up')

    def cylinder_stop(self) -> bool:
        """Stop cylinder (both valves off)."""
        self.off('cylinder_down')
        return self.off('cylinder_up')

    def vacuum_on(self) -> bool:
        """Enable vacuum gripper."""
        self.off('blow')
        return self.on('vacuum')

    def vacuum_off(self) -> bool:
        """Disable vacuum gripper."""
        return self.off('vacuum')

    def blow_on(self) -> bool:
        """Enable air blow."""
        self.off('vacuum')
        return self.on('blow')

    def blow_off(self) -> bool:
        """Disable air blow."""
        return self.off('blow')
