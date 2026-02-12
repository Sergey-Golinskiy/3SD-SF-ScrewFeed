"""
XY Table Controller for Screw Drive System.

Communicates with XY coordinate table either directly via GPIO
or through serial connection to another Raspberry Pi.
"""

import time
import threading
from typing import Optional, Tuple, Callable, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

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
    CONNECTING = "connecting"
    READY = "ready"
    MOVING = "moving"
    HOMING = "homing"
    ERROR = "error"
    ESTOP = "estop"
    TIMEOUT = "timeout"


@dataclass
class XYPosition:
    """XY position data."""
    x: float
    y: float
    x_homed: bool = False
    y_homed: bool = False


@dataclass
class XYEndstops:
    """Endstop states."""
    x_min: bool = False  # True = triggered
    y_min: bool = False  # True = triggered


@dataclass
class XYHealthStatus:
    """Health and communication status."""
    connected: bool = False
    last_ping_ok: bool = False
    last_ping_time: Optional[datetime] = None
    last_ping_latency_ms: float = 0.0
    last_command_time: Optional[datetime] = None
    last_command_ok: bool = False
    last_error: Optional[str] = None
    last_limit_warning: Optional[str] = None  # Soft limit warning from last command
    consecutive_errors: int = 0
    total_commands: int = 0
    failed_commands: int = 0
    service_status: str = "unknown"  # "running", "stopped", "error", "unknown"


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
                 timeout: float = 30.0, health_check_interval: float = 2.0,
                 slave_ssh_host: str = "192.168.1.101",
                 slave_ssh_user: str = "root"):
        """
        Initialize XY table controller.

        Args:
            mode: Communication mode (SERIAL or DIRECT)
            port: Serial port path (for SERIAL mode)
            baud: Serial baud rate (for SERIAL mode)
            timeout: Command timeout in seconds
            health_check_interval: Interval for health checks in seconds
            slave_ssh_host: SSH hostname/IP of slave Raspberry Pi
            slave_ssh_user: SSH username for slave (default: root)
        """
        self._mode = mode
        self._port = port
        self._baud = baud
        self._timeout = timeout
        self._health_check_interval = health_check_interval
        self._slave_ssh_host = slave_ssh_host
        self._slave_ssh_user = slave_ssh_user

        self._serial: Optional[serial.Serial] = None
        self._state = XYTableState.DISCONNECTED
        self._position = XYPosition(x=0.0, y=0.0)
        self._endstops = XYEndstops()
        self._health = XYHealthStatus()
        self._lock = threading.Lock()

        # Callbacks
        self._state_callbacks: list = []
        self._position_callbacks: list = []

        # Configuration
        self._x_max = 220.0
        self._y_max = 500.0

        # Health monitoring thread
        self._health_thread: Optional[threading.Thread] = None
        self._health_running = False

        # Auto-reconnect thread
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnect_running = False
        self._reconnect_interval = 2.0  # Retry every 2 seconds
        self._reconnect_attempts = 0
        self._restart_service_after_attempts = 5  # Restart service after 5 failed attempts

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
            self._health.last_error = "pyserial not available"
            self._health.service_status = "error"
            print("ERROR: pyserial not available. Install with: pip install pyserial")
            return False

        self._state = XYTableState.CONNECTING
        self._notify_state_change()

        try:
            print(f"DEBUG: Opening serial port {self._port} at {self._baud} baud...")

            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0,  # 1 second read timeout
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )

            # Wait for connection to stabilize
            time.sleep(1.0)

            # Clear any pending data (including "ok READY" from xy_cli)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

            # Small delay after clear
            time.sleep(0.1)

            # Test connection with PING
            print("DEBUG: Testing connection with PING...")
            ping_start = time.time()
            response = self._send_command("PING", timeout=5.0)
            ping_latency = (time.time() - ping_start) * 1000
            print(f"DEBUG: PING response: {response!r}")

            if response == "PONG":
                self._state = XYTableState.READY
                self._health.connected = True
                self._health.last_ping_ok = True
                self._health.last_ping_time = datetime.now()
                self._health.last_ping_latency_ms = ping_latency
                self._health.service_status = "running"
                self._health.last_error = None
                self._health.consecutive_errors = 0
                self._notify_state_change()
                print("DEBUG: XY Table connected successfully")

                # Start health monitoring
                self._start_health_monitor()
                return True
            else:
                self._health.last_error = f"PING failed: got {response!r}"
                self._health.service_status = "error"
                self._health.consecutive_errors += 1
                print(f"ERROR: XY table not responding to PING (got: {response!r})")
                self._serial.close()
                self._serial = None
                self._state = XYTableState.ERROR
                self._notify_state_change()
                # Start auto-reconnect loop
                self._start_reconnect_loop()
                return False

        except Exception as e:
            self._health.last_error = str(e)
            self._health.service_status = "error"
            self._health.consecutive_errors += 1
            self._state = XYTableState.ERROR
            self._notify_state_change()
            print(f"ERROR: Cannot connect to XY table on {self._port}: {e}")
            import traceback
            traceback.print_exc()
            # Start auto-reconnect loop
            self._start_reconnect_loop()
            return False

    def _connect_direct(self) -> bool:
        """Connect in direct mode (GPIO on same Pi)."""
        # For direct mode, we'd import and use xy_cli functions directly
        # This is a placeholder for now
        print("DIRECT mode not yet implemented - use SERIAL mode")
        return False

    def disconnect(self) -> None:
        """Disconnect from XY table."""
        # Stop health monitor and reconnect loop first
        self._stop_health_monitor()
        self._stop_reconnect_loop()

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

        self._state = XYTableState.DISCONNECTED
        self._health.connected = False
        self._health.service_status = "disconnected"
        self._notify_state_change()

    def _start_health_monitor(self) -> None:
        """Start the health monitoring thread."""
        if self._health_running:
            return

        self._health_running = True
        self._health_thread = threading.Thread(target=self._health_monitor_loop, daemon=True)
        self._health_thread.start()
        print(f"XY Table health monitor started (interval: {self._health_check_interval}s)")

    def _stop_health_monitor(self) -> None:
        """Stop the health monitoring thread."""
        self._health_running = False
        if self._health_thread:
            self._health_thread.join(timeout=2.0)
            self._health_thread = None
        print("XY Table health monitor stopped")

    def _start_reconnect_loop(self) -> None:
        """Start the auto-reconnect background thread."""
        if self._reconnect_running:
            return

        self._reconnect_running = True
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()
        print(f"XY Table auto-reconnect started (interval: {self._reconnect_interval}s)")

    def _stop_reconnect_loop(self) -> None:
        """Stop the auto-reconnect thread."""
        self._reconnect_running = False
        if self._reconnect_thread:
            self._reconnect_thread.join(timeout=3.0)
            self._reconnect_thread = None
        self._reconnect_attempts = 0
        print("XY Table auto-reconnect stopped")

    def restart_slave_service(self) -> bool:
        """
        Restart xy_table.service on the slave Raspberry Pi via SSH.

        Returns:
            True if restart command was sent successfully.
        """
        import subprocess

        ssh_target = f"{self._slave_ssh_user}@{self._slave_ssh_host}"
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            ssh_target,
            "systemctl restart xy_table.service"
        ]

        print(f"Restarting xy_table.service on slave ({self._slave_ssh_host})...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode == 0:
                print("Slave service restart command sent successfully")
                # Wait for service to start
                time.sleep(3)
                return True
            else:
                print(f"Failed to restart slave service: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print("SSH command timed out")
            return False
        except Exception as e:
            print(f"Error restarting slave service: {e}")
            return False

    def _reconnect_loop(self) -> None:
        """Auto-reconnect loop - tries to connect every 2 seconds until successful."""
        self._reconnect_attempts = 0

        while self._reconnect_running:
            try:
                # Only try to reconnect if not already connected
                if self._state in (XYTableState.DISCONNECTED, XYTableState.ERROR, XYTableState.TIMEOUT):
                    self._reconnect_attempts += 1
                    print(f"Auto-reconnect: Attempt {self._reconnect_attempts} to connect to XY table...")

                    # Check if we should restart the slave service
                    if self._reconnect_attempts > 0 and self._reconnect_attempts % self._restart_service_after_attempts == 0:
                        print(f"Auto-reconnect: {self._reconnect_attempts} failed attempts, restarting slave service...")
                        self.restart_slave_service()
                        # Wait a bit more after restart
                        time.sleep(2)

                    # Close existing serial if any
                    if self._serial is not None:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None

                    # Try to connect
                    if self._try_connect_serial():
                        print(f"Auto-reconnect: Connection successful after {self._reconnect_attempts} attempts!")
                        self._reconnect_attempts = 0
                        self._reconnect_running = False  # Stop reconnect loop
                        return
                    else:
                        print(f"Auto-reconnect: Connection failed, retrying in {self._reconnect_interval}s...")
                else:
                    # Already connected, stop reconnect loop
                    self._reconnect_attempts = 0
                    self._reconnect_running = False
                    return

            except Exception as e:
                print(f"Auto-reconnect error: {e}")

            time.sleep(self._reconnect_interval)

    def _try_connect_serial(self) -> bool:
        """
        Try to connect via serial port (without starting reconnect loop on failure).

        Returns:
            True if connection successful.
        """
        if not SERIAL_AVAILABLE:
            self._health.last_error = "pyserial not available"
            self._health.service_status = "error"
            return False

        self._state = XYTableState.CONNECTING
        self._notify_state_change()

        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )

            time.sleep(1.0)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            time.sleep(0.1)

            # Test connection with PING
            ping_start = time.time()
            response = self._send_command("PING", timeout=5.0)
            ping_latency = (time.time() - ping_start) * 1000

            if response == "PONG":
                self._state = XYTableState.READY
                self._health.connected = True
                self._health.last_ping_ok = True
                self._health.last_ping_time = datetime.now()
                self._health.last_ping_latency_ms = ping_latency
                self._health.service_status = "running"
                self._health.last_error = None
                self._health.consecutive_errors = 0
                self._notify_state_change()

                # Start health monitoring
                self._start_health_monitor()
                return True
            else:
                self._health.last_error = f"PING failed: got {response!r}"
                self._health.service_status = "error"
                self._health.consecutive_errors += 1
                self._serial.close()
                self._serial = None
                self._state = XYTableState.ERROR
                self._notify_state_change()
                return False

        except Exception as e:
            self._health.last_error = str(e)
            self._health.service_status = "error"
            self._health.consecutive_errors += 1
            self._state = XYTableState.ERROR
            self._notify_state_change()
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            return False

    def _health_monitor_loop(self) -> None:
        """Health monitoring loop - periodically ping and get status."""
        while self._health_running:
            try:
                # Ping test
                ping_start = time.time()
                response = self._send_command("PING", timeout=3.0)
                ping_latency = (time.time() - ping_start) * 1000

                if response == "PONG":
                    self._health.last_ping_ok = True
                    self._health.last_ping_time = datetime.now()
                    self._health.last_ping_latency_ms = ping_latency
                    self._health.service_status = "running"
                    self._health.consecutive_errors = 0

                    # Get full status (M114) to update position and homed state
                    # This is critical for detecting E-STOP triggered on Slave Pi
                    status_response = self._send_command("M114", timeout=2.0)
                    if status_response:
                        self._parse_status(status_response)

                    # Also get endstop status
                    endstops = self._send_command("M119", timeout=2.0)
                    if endstops:
                        self._parse_endstops(endstops)

                    # If we were in error/timeout state, recover to ready
                    if self._state in (XYTableState.ERROR, XYTableState.TIMEOUT):
                        self._state = XYTableState.READY
                        self._notify_state_change()
                else:
                    self._health.last_ping_ok = False
                    self._health.consecutive_errors += 1
                    self._health.last_error = f"PING timeout or invalid response"

                    if self._health.consecutive_errors >= 3:
                        self._state = XYTableState.TIMEOUT
                        self._health.service_status = "timeout"
                        self._health.connected = False
                        self._notify_state_change()
                        # Stop health monitor and start reconnect loop
                        print("Health monitor: Connection lost, starting auto-reconnect...")
                        self._health_running = False
                        self._start_reconnect_loop()
                        return

            except Exception as e:
                self._health.last_ping_ok = False
                self._health.consecutive_errors += 1
                self._health.last_error = str(e)

                if self._health.consecutive_errors >= 3:
                    self._state = XYTableState.TIMEOUT
                    self._health.service_status = "error"
                    self._health.connected = False
                    self._notify_state_change()
                    # Stop health monitor and start reconnect loop
                    print("Health monitor: Connection error, starting auto-reconnect...")
                    self._health_running = False
                    self._start_reconnect_loop()
                    return

            time.sleep(self._health_check_interval)

    def _parse_endstops(self, response: str) -> None:
        """Parse M119 endstop response."""
        # Format: X_MIN:open Y_MIN:triggered or similar
        try:
            response_lower = response.lower()
            self._endstops.x_min = "x_min:triggered" in response_lower or "x_min:closed" in response_lower
            self._endstops.y_min = "y_min:triggered" in response_lower or "y_min:closed" in response_lower
        except Exception:
            pass

    def _parse_limit_warnings(self, response: str) -> Optional[str]:
        """
        Parse limit warnings from response.

        Warnings are in format: LIMIT_X_MAX:220.0 LIMIT_Y_MAX:500.0

        Returns:
            Warning message in Ukrainian or None if no warnings.
        """
        if not response:
            return None

        warnings = []
        for part in response.split():
            if part.startswith("LIMIT_X_MIN:"):
                val = part.split(":")[1]
                warnings.append(f"X обмежено мін: {val} мм")
            elif part.startswith("LIMIT_X_MAX:"):
                val = part.split(":")[1]
                warnings.append(f"X обмежено макс: {val} мм")
            elif part.startswith("LIMIT_Y_MIN:"):
                val = part.split(":")[1]
                warnings.append(f"Y обмежено мін: {val} мм")
            elif part.startswith("LIMIT_Y_MAX:"):
                val = part.split(":")[1]
                warnings.append(f"Y обмежено макс: {val} мм")

        if warnings:
            return "; ".join(warnings)
        return None

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
            print(f"DEBUG: Serial is None, cannot send '{cmd}'")
            return None

        timeout = timeout or self._timeout
        cmd_upper = cmd.strip().upper()

        # Commands that return single line responses (for faster handling)
        # PING returns "PONG", GETIP returns "IP x.x.x.x", M17/M18/M999 return "ok" or "err ..."
        single_line_commands = {"PING", "GETIP", "M17", "M18", "M999"}

        with self._lock:
            try:
                # Clear any pending input data
                self._serial.reset_input_buffer()

                # Small delay to ensure buffer is clear
                time.sleep(0.01)

                # Send command
                cmd_bytes = (cmd + "\n").encode('utf-8')
                self._serial.write(cmd_bytes)
                self._serial.flush()

                print(f"DEBUG: Sent command: {cmd!r}")

                # Wait for response using blocking readline with timeout
                # Set a reasonable read timeout
                old_timeout = self._serial.timeout
                self._serial.timeout = min(timeout, 5.0)  # Max 5s per line read

                start = time.time()
                response_lines = []

                try:
                    while time.time() - start < timeout:
                        # Read a line (blocks until newline or timeout)
                        raw_line = self._serial.readline()

                        if raw_line:
                            line = raw_line.decode('utf-8', errors='ignore').strip()
                            print(f"DEBUG: Received line: {line!r}")

                            if line:
                                response_lines.append(line)

                                # For single-line commands, return immediately
                                if cmd_upper in single_line_commands:
                                    return line

                                # Check for completion markers
                                if line.startswith("ok") or line.startswith("err"):
                                    return "\n".join(response_lines)
                        else:
                            # No data received within serial timeout
                            # Check if we already have a response
                            if response_lines:
                                # Maybe we got the response but missed 'ok'
                                last_line = response_lines[-1].lower()
                                if "ok" in last_line or "err" in last_line:
                                    return "\n".join(response_lines)

                            # Small sleep before retry
                            time.sleep(0.01)
                finally:
                    self._serial.timeout = old_timeout

                # If we got any response, return it
                if response_lines:
                    print(f"DEBUG: Returning partial response: {response_lines}")
                    return "\n".join(response_lines)

                print(f"WARNING: Command '{cmd}' timed out (no response in {timeout}s)")
                return None

            except Exception as e:
                print(f"ERROR: Command '{cmd}' failed: {e}")
                import traceback
                traceback.print_exc()
                return None

    def _parse_status(self, response: str) -> None:
        """Parse status response and update internal state."""
        # Format: STATUS X:123.456 Y:789.012 X_MIN:open Y_MIN:open X_HOMED:1 Y_HOMED:1 ESTOP:0
        if "STATUS" not in response:
            return

        try:
            for part in response.split():
                if part.startswith("X:") and not part.startswith("X_MIN") and not part.startswith("X_HOMED"):
                    self._position.x = float(part[2:])
                elif part.startswith("Y:") and not part.startswith("Y_MIN") and not part.startswith("Y_HOMED"):
                    self._position.y = float(part[2:])
                elif part.startswith("X_HOMED:"):
                    self._position.x_homed = (part[8:] == "1")
                elif part.startswith("Y_HOMED:"):
                    self._position.y_homed = (part[8:] == "1")
                elif part.startswith("ESTOP:"):
                    if part[6:] == "1":
                        self._state = XYTableState.ESTOP
                        self._notify_state_change()
                    elif self._state == XYTableState.ESTOP:
                        # E-STOP cleared, but check if homing required
                        self._state = XYTableState.READY
                        self._notify_state_change()
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

        # Build command - send original values, let xy_cli enforce limits
        cmd_parts = ["G"]
        if x is not None:
            cmd_parts.append(f"X{x:.3f}")
        if y is not None:
            cmd_parts.append(f"Y{y:.3f}")
        cmd_parts.append(f"F{feed:.0f}")

        cmd = " ".join(cmd_parts)

        self._state = XYTableState.MOVING
        self._notify_state_change()

        response = self._send_command(cmd)

        if response and "ok" in response.lower():
            # Parse limit warnings from response
            limit_warning = self._parse_limit_warnings(response)
            self._health.last_limit_warning = limit_warning
            self._health.last_error = None  # Clear error on success

            # Update position to clamped values
            if x is not None:
                self._position.x = max(0, min(x, self._x_max))
            if y is not None:
                self._position.y = max(0, min(y, self._y_max))
            self._state = XYTableState.READY
            self._notify_state_change()
            self._notify_position_change()
            return True
        else:
            # Set detailed error for diagnostics
            if response is None:
                self._health.last_error = f"Move command timeout (no response for cmd: {cmd})"
            elif "err" in response.lower():
                self._health.last_error = f"Move error from XY table: {response}"
            else:
                self._health.last_error = f"Move failed, unexpected response: {response!r}"

            self._health.last_limit_warning = None
            self._state = XYTableState.ERROR
            self._notify_state_change()
            print(f"ERROR: move_to failed - {self._health.last_error}")
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
            # Parse limit warnings
            limit_warning = self._parse_limit_warnings(response)
            self._health.last_limit_warning = limit_warning

            # Update position (clamped by xy_cli)
            new_x = self._position.x + distance
            self._position.x = max(0, min(new_x, self._x_max))
            return True
        self._health.last_limit_warning = None
        return False

    def jog_y(self, distance: float, feed: float = 600.0) -> bool:
        """Jog Y axis by specified distance."""
        cmd = f"DY {distance:+.3f} F{feed:.0f}"
        response = self._send_command(cmd)
        if response and "ok" in response.lower():
            # Parse limit warnings
            limit_warning = self._parse_limit_warnings(response)
            self._health.last_limit_warning = limit_warning

            # Update position (clamped by xy_cli)
            new_y = self._position.y + distance
            self._position.y = max(0, min(new_y, self._y_max))
            return True
        self._health.last_limit_warning = None
        return False

    def go_to_zero(self) -> bool:
        """
        Home to zero position (same as HOME command).

        Performs proper homing: Y axis first, then X axis.
        """
        return self.home()  # ZERO now behaves same as HOME

    def home_x(self) -> bool:
        """Home X axis only."""
        return self.home("X")

    def home_y(self) -> bool:
        """Home Y axis only."""
        return self.home("Y")

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
        """
        Trigger emergency stop - BYPASSES LOCK for immediate response.
        This is intentional to allow stopping during movement.
        """
        if self._serial is None:
            return False

        try:
            # Write directly to serial WITHOUT waiting for lock
            # This allows M112 to be sent even while another command is in progress
            self._serial.write(b"M112\n")
            self._serial.flush()
            print("ESTOP: M112 sent directly (bypassing lock)")

            self._state = XYTableState.ESTOP
            self._notify_state_change()
            return True
        except Exception as e:
            print(f"ESTOP error: {e}")
            return False

    def clear_estop(self) -> bool:
        """
        Clear emergency stop state on slave.
        Uses proper locking to avoid serial buffer corruption.
        """
        if self._serial is None:
            return False

        try:
            # Use _send_command with proper locking (M999 is in single_line_commands)
            response = self._send_command("M999", timeout=5.0)
            print(f"CLEAR: M999 response: {response!r}")

            if response and ("clear" in response.lower() or "ok" in response.lower()):
                self._state = XYTableState.READY
                self._notify_state_change()
                return True
            else:
                print(f"Clear ESTOP failed: unexpected response {response!r}")
                return False
        except Exception as e:
            print(f"Clear ESTOP error: {e}")
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

    @property
    def endstops(self) -> XYEndstops:
        """Get endstop states."""
        return self._endstops

    @property
    def health(self) -> XYHealthStatus:
        """Get health status."""
        return self._health

    def get_detailed_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status information for API/UI.

        Returns:
            Dictionary with all status information.
        """
        return {
            'connected': self.is_connected,
            'state': self._state.value,
            'state_name': self._state.name,
            'position': {
                'x': self._position.x,
                'y': self._position.y,
                'x_homed': self._position.x_homed,
                'y_homed': self._position.y_homed
            },
            'endstops': {
                'x_min': self._endstops.x_min,
                'y_min': self._endstops.y_min
            },
            'health': {
                'connected': self._health.connected,
                'last_ping_ok': self._health.last_ping_ok,
                'last_ping_time': self._health.last_ping_time.isoformat() if self._health.last_ping_time else None,
                'last_ping_latency_ms': round(self._health.last_ping_latency_ms, 2),
                'last_command_time': self._health.last_command_time.isoformat() if self._health.last_command_time else None,
                'last_command_ok': self._health.last_command_ok,
                'last_error': self._health.last_error,
                'last_limit_warning': self._health.last_limit_warning,
                'consecutive_errors': self._health.consecutive_errors,
                'total_commands': self._health.total_commands,
                'failed_commands': self._health.failed_commands,
                'service_status': self._health.service_status
            },
            'config': {
                'mode': self._mode.value,
                'port': self._port,
                'baud': self._baud,
                'x_max': self._x_max,
                'y_max': self._y_max
            }
        }

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
