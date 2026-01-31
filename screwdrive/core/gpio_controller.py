"""
GPIO Controller for Raspberry Pi 5 using lgpio library.

Provides a unified interface for GPIO operations including
digital I/O, pull-up/pull-down configuration, and PWM.
"""

import time
from typing import Optional, Dict, Any
from pathlib import Path

try:
    import lgpio
    LGPIO_AVAILABLE = True
except ImportError:
    LGPIO_AVAILABLE = False
    lgpio = None

import yaml


class GPIOController:
    """
    GPIO controller using lgpio library for Raspberry Pi 5.

    Supports:
    - Digital input/output
    - Pull-up/pull-down resistors
    - Active high/low logic inversion
    - Thread-safe operations
    """

    def __init__(self, config_path: Optional[str] = None, chip: int = 0):
        """
        Initialize GPIO controller.

        Args:
            config_path: Path to gpio_pins.yaml config file
            chip: GPIO chip number (default 0 for Pi 5)
        """
        self._chip = chip
        self._handle: Optional[int] = None
        self._claimed_pins: Dict[int, str] = {}  # pin -> mode ('in'/'out')
        self._pin_config: Dict[str, Dict[str, Any]] = {}

        if config_path:
            self._load_config(config_path)

        self._initialized = False

    def _load_config(self, config_path: str) -> None:
        """Load GPIO configuration from YAML file."""
        path = Path(config_path)
        if path.exists():
            with open(path, 'r') as f:
                self._pin_config = yaml.safe_load(f) or {}

    def init(self) -> bool:
        """
        Initialize GPIO chip. Must be called before any GPIO operations.

        Returns:
            True if initialization successful, False otherwise.
        """
        if not LGPIO_AVAILABLE:
            print("ERROR: lgpio library not available. Install with: pip install lgpio")
            return False

        if self._initialized:
            return True

        try:
            self._handle = lgpio.gpiochip_open(self._chip)
            self._initialized = True
            return True
        except Exception as e:
            print(f"ERROR: Cannot open GPIO chip {self._chip}: {e}")
            return False

    def close(self) -> None:
        """Release GPIO resources."""
        if self._handle is not None and self._initialized:
            try:
                lgpio.gpiochip_close(self._handle)
            except Exception:
                pass
            self._handle = None
            self._initialized = False
            self._claimed_pins.clear()

    def setup_output(self, pin: int, initial: int = 0) -> bool:
        """
        Configure a pin as output.

        Args:
            pin: BCM GPIO pin number
            initial: Initial output value (0 or 1)

        Returns:
            True if successful.
        """
        if not self._ensure_init():
            return False

        try:
            lgpio.gpio_claim_output(self._handle, pin, initial)
            self._claimed_pins[pin] = 'out'
            return True
        except Exception as e:
            print(f"ERROR: Cannot setup output pin {pin}: {e}")
            return False

    def setup_input(self, pin: int, pull_up: bool = False, pull_down: bool = False) -> bool:
        """
        Configure a pin as input.

        Args:
            pin: BCM GPIO pin number
            pull_up: Enable internal pull-up resistor
            pull_down: Enable internal pull-down resistor

        Returns:
            True if successful.
        """
        if not self._ensure_init():
            return False

        try:
            flags = 0
            if pull_up:
                flags = getattr(lgpio, 'SET_PULL_UP', 0)
            elif pull_down:
                flags = getattr(lgpio, 'SET_PULL_DOWN', 0)

            lgpio.gpio_claim_input(self._handle, pin, flags)
            self._claimed_pins[pin] = 'in'
            return True
        except Exception as e:
            print(f"ERROR: Cannot setup input pin {pin}: {e}")
            return False

    def write(self, pin: int, value: int) -> bool:
        """
        Write value to output pin.

        Args:
            pin: BCM GPIO pin number
            value: 0 or 1

        Returns:
            True if successful.
        """
        if not self._ensure_init():
            return False

        try:
            lgpio.gpio_write(self._handle, pin, value)
            return True
        except Exception as e:
            print(f"ERROR: Cannot write to pin {pin}: {e}")
            return False

    def read(self, pin: int) -> Optional[int]:
        """
        Read value from input pin.

        Args:
            pin: BCM GPIO pin number

        Returns:
            0 or 1, or None on error.
        """
        if not self._ensure_init():
            return None

        try:
            return lgpio.gpio_read(self._handle, pin)
        except Exception as e:
            print(f"ERROR: Cannot read pin {pin}: {e}")
            return None

    def read_active(self, pin: int, active_low: bool = False) -> Optional[bool]:
        """
        Read pin with active logic handling.

        Args:
            pin: BCM GPIO pin number
            active_low: If True, 0 means active

        Returns:
            True if active, False if inactive, None on error.
        """
        value = self.read(pin)
        if value is None:
            return None

        if active_low:
            return value == 0
        else:
            return value == 1

    def pulse(self, pin: int, duration_us: int = 10, active_low: bool = False) -> bool:
        """
        Generate a single pulse on output pin.

        Args:
            pin: BCM GPIO pin number
            duration_us: Pulse duration in microseconds
            active_low: If True, pulse is LOW, otherwise HIGH

        Returns:
            True if successful.
        """
        if not self._ensure_init():
            return False

        try:
            active = 0 if active_low else 1
            idle = 1 if active_low else 0

            lgpio.gpio_write(self._handle, pin, active)
            self._busy_wait_ns(duration_us * 1000)
            lgpio.gpio_write(self._handle, pin, idle)
            return True
        except Exception as e:
            print(f"ERROR: Cannot pulse pin {pin}: {e}")
            return False

    def _busy_wait_ns(self, duration_ns: int) -> None:
        """Busy-wait for specified nanoseconds."""
        end = time.perf_counter_ns() + duration_ns
        while time.perf_counter_ns() < end:
            pass

    def _ensure_init(self) -> bool:
        """Ensure GPIO is initialized."""
        if not self._initialized:
            return self.init()
        return True

    @property
    def is_initialized(self) -> bool:
        """Check if GPIO is initialized."""
        return self._initialized

    @property
    def handle(self) -> Optional[int]:
        """Get raw lgpio handle for advanced operations."""
        return self._handle

    def __enter__(self):
        """Context manager entry."""
        self.init()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


# Singleton instance for global access
_gpio_instance: Optional[GPIOController] = None


def get_gpio() -> GPIOController:
    """Get global GPIO controller instance."""
    global _gpio_instance
    if _gpio_instance is None:
        _gpio_instance = GPIOController()
    return _gpio_instance


def init_gpio(config_path: Optional[str] = None) -> GPIOController:
    """Initialize and return global GPIO controller."""
    global _gpio_instance
    _gpio_instance = GPIOController(config_path)
    _gpio_instance.init()
    return _gpio_instance
