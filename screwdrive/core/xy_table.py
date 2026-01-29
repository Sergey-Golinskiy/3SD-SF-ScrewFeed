"""
XY Table Controller for Screw Drive System.

Communicates with XY coordinate table either directly via GPIO
or through serial connection to another Raspberry Pi.
"""

import time
import threading
from typing import Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    serial = None


class XYTableMode(Enum):
    """XY Table communication mode."""
    DIRECT = "direct"    # GPIO on same Pi
    SERIAL = "serial"    # UART to another Pi


class XYTableState(Enum):
    """XY Table state enumeration."""
    DISCONNECTED = "disconnected"
    READY = "ready"
    MOVING = "moving"
    HOMING = "homing"
    ERROR = "error"
    ESTOP = "estop"


@dataclass
class XYPosition:
    """XY position data."""
    x: float
    y: float
    x_homed: bool = False
    y_homed: bool = False


class XYTableController:
    """
    Controller for XY coordinate table.

    Supports two modes:
    - SERIAL: Communication via UART to another Raspberry Pi running xy_cli.py
    - DIRECT: Direct GPIO control (uses xy_cli.py functions)

    Commands sent in G-code compatible format.
    """

    def __init__(self, mode: XYTableMode = XYTableMode.SERIAL,
                 port: str = "/dev/ttyAMA0", baud: int = 115200,
                 timeout: float = 30.0):
        """
        Initialize XY table controller.

        Args:
            mode: Communication mode (SERIAL or DIRECT)
            port: Serial port path (for SERIAL mode)
            baud: Serial baud rate (for SERIAL mode)
            timeout: Command timeout in seconds
        """
        self._mode = mode
        self._port = port
        self._baud = baud
        self._timeout = timeout

        self._serial: Optional[serial.Serial] = None
        self._state = XYTableState.DISCONNECTED
        self._position = XYPosition(x=0.0, y=0.0)
        self._lock = threading.Lock()

        # Callbacks
        self._state_callbacks: list = []
        self._position_callbacks: list = []

        # Configuration
        self._x_max = 220.0
        self._y_max = 500.0

    def connect(self) -> bool:
        """
        Connect to XY table.

        Returns:
            True if connection successful.
        """
        if self._mode == XYTableMode.SERIAL:
            return self._connect_serial()
        else:
            return self._connect_direct()

    def _connect_serial(self) -> bool:
        """Connect via serial port."""
        if not SERIAL_AVAILABLE:
            print("ERROR: pyserial not available. Install with: pip install pyserial")
            return False

        try:
            self._serial = serial.Serial(
                self._port,
                self._baud,
                timeout=0.1
            )
            time.sleep(0.5)  # Wait for connection to stabilize

            # Clear any pending data
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

            # Test connection
            if self._send_command("PING") == "PONG":
                self._state = XYTableState.READY
                self._notify_state_change()
                return True
            else:
                print("ERROR: XY table not responding to PING")
                self._serial.close()
                self._serial = None
                return False

        except Exception as e:
            print(f"ERROR: Cannot connect to XY table on {self._port}: {e}")
            return False

    def _connect_direct(self) -> bool:
        """Connect in direct mode (GPIO on same Pi)."""
        # For direct mode, we'd import and use xy_cli functions directly
        # This is a placeholder for now
        print("DIRECT mode not yet implemented - use SERIAL mode")
        return False

    def disconnect(self) -> None:
        """Disconnect from XY table."""
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self._state = XYTableState.DISCONNECTED
        self._notify_state_change()

    def _send_command(self, cmd: str, timeout: Optional[float] = None) -> Optional[str]:
        """
        Send command and wait for response.

        Args:
            cmd: Command string
            timeout: Response timeout (uses default if None)

        Returns:
            Response string or None on error.
        """
        if self._serial is None:
            return None

        timeout = timeout or self._timeout
        cmd_upper = cmd.strip().upper()

        # Commands that return single line without "ok" suffix
        single_line_commands = {"PING", "M119"}

        with self._lock:
            try:
                # Clear buffers before sending
                self._serial.reset_input_buffer()

                # Send command
                self._serial.write((cmd + "\n").encode('utf-8'))
                self._serial.flush()

                # Wait for response
                start = time.time()
                response_lines = []

                while time.time() - start < timeout:
                    if self._serial.in_waiting > 0:
                        line = self._serial.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            response_lines.append(line)

                            # For single-line commands, return immediately
                            if cmd_upper in single_line_commands:
                                return line

                            # Check for completion markers
                            if line.startswith("ok") or line.startswith("err"):
                                return "\n".join(response_lines)
                    time.sleep(0.001)

                # If we got any response but no "ok", return what we have
                if response_lines:
                    return "\n".join(response_lines)

                print(f"WARNING: Command '{cmd}' timed out")
                return None

            except Exception as e:
                print(f"ERROR: Command '{cmd}' failed: {e}")
                return None

    def _parse_status(self, response: str) -> None:
        """Parse status response and update internal state."""
        # Format: STATUS X:123.456 Y:789.012 X_MIN:open Y_MIN:open ESTOP:0
        if "STATUS" not in response:
            return

        try:
            for part in response.split():
                if part.startswith("X:") and not part.startswith("X_MIN"):
                    self._position.x = float(part[2:])
                elif part.startswith("Y:") and not part.startswith("Y_MIN"):
                    self._position.y = float(part[2:])
                elif part.startswith("ESTOP:"):
                    if part[6:] == "1":
                        self._state = XYTableState.ESTOP
        except ValueError:
            pass

    # === Movement Commands ===

    def home(self, axis: Optional[str] = None) -> bool:
        """
        Home axis or all axes.

        Args:
            axis: "X", "Y", or None for both

        Returns:
            True if homing successful.
        """
        self._state = XYTableState.HOMING
        self._notify_state_change()

        if axis:
            cmd = f"G28 {axis.upper()}"
        else:
            cmd = "G28"

        response = self._send_command(cmd, timeout=120.0)

        if response and "ok" in response.lower():
            if axis is None or axis.upper() == "X":
                self._position.x = 0.0
                self._position.x_homed = True
            if axis is None or axis.upper() == "Y":
                self._position.y = 0.0
                self._position.y_homed = True
            self._state = XYTableState.READY
            self._notify_state_change()
            return True
        else:
            self._state = XYTableState.ERROR
            self._notify_state_change()
            return False

    def move_to(self, x: Optional[float] = None, y: Optional[float] = None,
                feed: float = 10000.0) -> bool:
        """
        Move to absolute position.

        Args:
            x: Target X position (mm)
            y: Target Y position (mm)
            feed: Feed rate (mm/min)

        Returns:
            True if move completed successfully.
        """
        if x is None and y is None:
            return True

        # Build command
        cmd_parts = ["G"]
        if x is not None:
            x = max(0, min(x, self._x_max))
            cmd_parts.append(f"X{x:.3f}")
        if y is not None:
            y = max(0, min(y, self._y_max))
            cmd_parts.append(f"Y{y:.3f}")
        cmd_parts.append(f"F{feed:.0f}")

        cmd = " ".join(cmd_parts)

        self._state = XYTableState.MOVING
        self._notify_state_change()

        response = self._send_command(cmd)

        if response and "ok" in response.lower():
            if x is not None:
                self._position.x = x
            if y is not None:
                self._position.y = y
            self._state = XYTableState.READY
            self._notify_state_change()
            self._notify_position_change()
            return True
        else:
            self._state = XYTableState.ERROR
            self._notify_state_change()
            return False

    def move_relative(self, dx: float = 0, dy: float = 0, feed: float = 10000.0) -> bool:
        """
        Move relative to current position.

        Args:
            dx: X offset (mm)
            dy: Y offset (mm)
            feed: Feed rate (mm/min)

        Returns:
            True if move completed successfully.
        """
        new_x = self._position.x + dx if dx != 0 else None
        new_y = self._position.y + dy if dy != 0 else None
        return self.move_to(new_x, new_y, feed)

    def jog_x(self, distance: float, feed: float = 600.0) -> bool:
        """Jog X axis by specified distance."""
        cmd = f"DX {distance:+.3f} F{feed:.0f}"
        response = self._send_command(cmd)
        if response and "ok" in response.lower():
            self._position.x += distance
            return True
        return False

    def jog_y(self, distance: float, feed: float = 600.0) -> bool:
        """Jog Y axis by specified distance."""
        cmd = f"DY {distance:+.3f} F{feed:.0f}"
        response = self._send_command(cmd)
        if response and "ok" in response.lower():
            self._position.y += distance
            return True
        return False

    def go_to_zero(self) -> bool:
        """Move to zero position."""
        response = self._send_command("ZERO")
        if response and "ok" in response.lower():
            self._position.x = 0.0
            self._position.y = 0.0
            return True
        return False

    def calibrate(self) -> bool:
        """Home and go to zero."""
        response = self._send_command("CAL", timeout=120.0)
        if response and "ok" in response.lower():
            self._position.x = 0.0
            self._position.y = 0.0
            self._position.x_homed = True
            self._position.y_homed = True
            return True
        return False

    # === Control Commands ===

    def estop(self) -> bool:
        """Trigger emergency stop."""
        response = self._send_command("M112")
        if response and "ok" in response.lower():
            self._state = XYTableState.ESTOP
            self._notify_state_change()
            return True
        return False

    def clear_estop(self) -> bool:
        """Clear emergency stop."""
        response = self._send_command("M999")
        if response and "ok" in response.lower():
            self._state = XYTableState.READY
            self._notify_state_change()
            return True
        return False

    def enable_motors(self) -> bool:
        """Enable stepper motors."""
        response = self._send_command("M17")
        return response is not None and "ok" in response.lower()

    def disable_motors(self) -> bool:
        """Disable stepper motors."""
        response = self._send_command("M18")
        return response is not None and "ok" in response.lower()

    # === Status Commands ===

    def get_status(self) -> Optional[str]:
        """Get full status string."""
        response = self._send_command("M114")
        if response:
            self._parse_status(response)
        return response

    def get_endstops(self) -> Optional[str]:
        """Get endstop status."""
        return self._send_command("M119")

    def ping(self) -> bool:
        """Test connection."""
        response = self._send_command("PING")
        return response == "PONG"

    # === Properties ===

    @property
    def position(self) -> XYPosition:
        """Get current position."""
        return self._position

    @property
    def x(self) -> float:
        """Get current X position."""
        return self._position.x

    @property
    def y(self) -> float:
        """Get current Y position."""
        return self._position.y

    @property
    def state(self) -> XYTableState:
        """Get current state."""
        return self._state

    @property
    def is_ready(self) -> bool:
        """Check if table is ready for commands."""
        return self._state == XYTableState.READY

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._state != XYTableState.DISCONNECTED

    # === Callbacks ===

    def on_state_change(self, callback: Callable[[XYTableState], None]) -> None:
        """Register state change callback."""
        self._state_callbacks.append(callback)

    def on_position_change(self, callback: Callable[[XYPosition], None]) -> None:
        """Register position change callback."""
        self._position_callbacks.append(callback)

    def _notify_state_change(self) -> None:
        """Notify all state change callbacks."""
        for cb in self._state_callbacks:
            try:
                cb(self._state)
            except Exception as e:
                print(f"ERROR: State callback failed: {e}")

    def _notify_position_change(self) -> None:
        """Notify all position change callbacks."""
        for cb in self._position_callbacks:
            try:
                cb(self._position)
            except Exception as e:
                print(f"ERROR: Position callback failed: {e}")

    # === Context Manager ===

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
