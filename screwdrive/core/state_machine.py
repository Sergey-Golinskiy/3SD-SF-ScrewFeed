"""
State Machine for Screw Drive Automation Cycle.

Implements the main automation logic with states for:
- Idle, Ready, Running
- Screw operations (pickup, approach, drive, verify)
- Safety interlocks and error handling
"""

import time
import threading
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum, auto
import logging

from .relays import RelayController
from .sensors import SensorController, SensorState
from .xy_table import XYTableController, XYTableState


class CycleState(Enum):
    """Automation cycle states."""
    IDLE = auto()           # System idle, not ready
    READY = auto()          # System ready, waiting for start
    HOMING = auto()         # Homing XY table
    MOVING_FREE = auto()    # Moving to free position
    MOVING_WORK = auto()    # Moving to work position
    LOWERING = auto()       # Cylinder extending
    SCREWING = auto()       # Driving screw
    RAISING = auto()        # Cylinder retracting
    VERIFYING = auto()      # Verifying screw seated
    PAUSED = auto()         # Cycle paused (safety)
    ERROR = auto()          # Error state
    ESTOP = auto()          # Emergency stop active
    COMPLETED = auto()      # Cycle completed


class CycleError(Enum):
    """Cycle error types."""
    NONE = auto()
    HOMING_FAILED = auto()
    MOVE_FAILED = auto()
    CYLINDER_TIMEOUT = auto()
    TORQUE_TIMEOUT = auto()
    SCREW_MISSING = auto()
    SAFETY_VIOLATION = auto()
    XY_TABLE_ERROR = auto()
    COMMUNICATION_ERROR = auto()


@dataclass
class ProgramStep:
    """Single step in a device program."""
    step_type: str  # "free" or "work"
    x: float
    y: float
    feed: float = 60000.0


@dataclass
class DeviceProgram:
    """Device program definition."""
    key: str
    name: str
    holes: int
    steps: List[ProgramStep] = field(default_factory=list)
    what: str = ""  # What we're screwing (description)
    screw_size: str = ""  # Screw size (e.g., "M3x10")
    task: str = ""  # Task number
    torque: float = None  # Torque value (0-1 Nm), None if not set
    work_x: float = None  # Work position X coordinate
    work_y: float = None  # Work position Y coordinate
    work_feed: float = 5000  # Work position feed rate
    group: str = ""  # Device group name
    fixture: str = ""  # Fixture code this device is linked to
    coord_source: str = ""  # Key of device to copy coordinates from (same group)


@dataclass
class CycleStatus:
    """Current cycle status."""
    state: CycleState
    error: CycleError = CycleError.NONE
    error_message: str = ""
    current_device: str = ""
    current_step: int = 0
    total_steps: int = 0
    holes_completed: int = 0
    total_holes: int = 0
    position_x: float = 0.0
    position_y: float = 0.0
    cycle_count: int = 0


class CycleStateMachine:
    """
    State machine for screw drive automation cycle.

    Controls the complete cycle:
    1. Home XY table
    2. For each hole in program:
       a. Move to position (free or work moves)
       b. Lower cylinder
       c. Drive screw
       d. Raise cylinder
       e. Verify torque reached
    3. Return to home position

    Safety interlocks:
    - Area sensor blocks movement
    - E-STOP stops all operations
    - Timeouts on all operations
    """

    def __init__(self,
                 relays: RelayController,
                 sensors: SensorController,
                 xy_table: XYTableController,
                 config: Optional[Dict[str, Any]] = None):
        """
        Initialize state machine.

        Args:
            relays: Relay controller instance
            sensors: Sensor controller instance
            xy_table: XY table controller instance
            config: Configuration dictionary
        """
        self._relays = relays
        self._sensors = sensors
        self._xy = xy_table
        self._config = config or {}

        # State
        self._state = CycleState.IDLE
        self._error = CycleError.NONE
        self._error_message = ""

        # Program execution
        self._program: Optional[DeviceProgram] = None
        self._current_step = 0
        self._holes_completed = 0
        self._cycle_count = 0

        # Threading
        self._cycle_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially

        # Callbacks
        self._state_callbacks: List[Callable[[CycleStatus], None]] = []
        self._log_callbacks: List[Callable[[str, str], None]] = []

        # Timing config
        self._cylinder_down_timeout = self._config.get('cylinder_down_timeout_s', 3.0)
        self._cylinder_up_timeout = self._config.get('cylinder_up_timeout_s', 3.0)
        self._torque_timeout = self._config.get('torque_timeout_s', 15.0)

        # Logger
        self._logger = logging.getLogger('cycle')

    # === State Management ===

    def _set_state(self, state: CycleState, error: CycleError = CycleError.NONE,
                   message: str = "") -> None:
        """Set current state and notify callbacks."""
        self._state = state
        self._error = error
        self._error_message = message

        self._log('INFO', f"State: {state.name}" + (f" - {message}" if message else ""))
        self._notify_state_change()

    def _set_error(self, error: CycleError, message: str = "") -> None:
        """Set error state."""
        self._set_state(CycleState.ERROR, error, message)

    # === Cycle Control ===

    def start(self, program: DeviceProgram) -> bool:
        """
        Start automation cycle with given program.

        Args:
            program: Device program to execute

        Returns:
            True if cycle started successfully.
        """
        if self._state not in (CycleState.IDLE, CycleState.READY, CycleState.COMPLETED):
            self._log('WARNING', f"Cannot start from state {self._state.name}")
            return False

        if not self._xy.is_connected:
            self._set_error(CycleError.COMMUNICATION_ERROR, "XY table not connected")
            return False

        self._program = program
        self._current_step = 0
        self._holes_completed = 0
        self._stop_event.clear()
        self._pause_event.set()

        self._cycle_thread = threading.Thread(target=self._run_cycle, daemon=True)
        self._cycle_thread.start()

        return True

    def stop(self) -> None:
        """Stop the current cycle."""
        self._stop_event.set()
        self._pause_event.set()  # Unpause to allow thread to exit

        if self._cycle_thread is not None:
            self._cycle_thread.join(timeout=5.0)
            self._cycle_thread = None

        self._safe_stop()
        self._set_state(CycleState.IDLE)

    def pause(self) -> None:
        """Pause the current cycle."""
        if self._state in (CycleState.MOVING_FREE, CycleState.MOVING_WORK):
            self._pause_event.clear()
            self._set_state(CycleState.PAUSED)

    def resume(self) -> None:
        """Resume paused cycle."""
        if self._state == CycleState.PAUSED:
            self._pause_event.set()

    def emergency_stop(self) -> None:
        """Trigger emergency stop."""
        self._stop_event.set()
        self._relays.emergency_stop()  # Keeps brakes ON
        self._xy.estop()
        self._xy.disable_motors()  # Explicitly disable XY motors
        self._set_state(CycleState.ESTOP)

    def clear_estop(self) -> bool:
        """Clear emergency stop and return to idle."""
        if self._state == CycleState.ESTOP:
            self._xy.clear_estop()
            # Pulse R05 for 300ms to reset screwdriver controller
            self._relays.estop_clear_pulse()
            self._set_state(CycleState.IDLE)
            return True
        return False

    def _safe_stop(self) -> None:
        """Safely stop all actuators."""
        self._relays.screwdriver_stop()
        self._relays.cylinder_stop()
        self._relays.vacuum_off()
        self._relays.blow_off()

    # === Main Cycle Logic ===

    def _run_cycle(self) -> None:
        """Main cycle execution (runs in thread)."""
        try:
            self._log('INFO', f"Starting cycle for {self._program.name}")

            # Home first
            if not self._do_homing():
                return

            # Execute program steps
            for i, step in enumerate(self._program.steps):
                if self._stop_event.is_set():
                    break

                self._current_step = i
                self._notify_state_change()

                # Check safety before each move
                if not self._check_safety():
                    self._wait_for_safety()
                    if self._stop_event.is_set():
                        break

                # Execute step
                if step.step_type == "free":
                    if not self._do_free_move(step.x, step.y, step.feed):
                        break
                elif step.step_type == "work":
                    if not self._do_work_cycle(step.x, step.y, step.feed):
                        break

            # Return home after completion
            if not self._stop_event.is_set():
                self._xy.go_to_zero()
                self._cycle_count += 1
                self._set_state(CycleState.COMPLETED)

        except Exception as e:
            self._set_error(CycleError.COMMUNICATION_ERROR, str(e))
            self._logger.exception("Cycle error")

        finally:
            self._safe_stop()

    def _do_homing(self) -> bool:
        """Perform homing sequence."""
        self._set_state(CycleState.HOMING)

        if not self._xy.calibrate():
            self._set_error(CycleError.HOMING_FAILED, "XY table homing failed")
            return False

        return True

    def _do_free_move(self, x: float, y: float, feed: float) -> bool:
        """Perform free (rapid) move."""
        self._set_state(CycleState.MOVING_FREE)

        # Wait if paused
        self._pause_event.wait()

        if self._stop_event.is_set():
            return False

        if not self._xy.move_to(x, y, feed):
            self._set_error(CycleError.MOVE_FAILED, f"Move to ({x}, {y}) failed")
            return False

        return True

    def _do_work_cycle(self, x: float, y: float, feed: float) -> bool:
        """Perform complete work cycle at position."""
        # Move to position
        self._set_state(CycleState.MOVING_WORK)

        if not self._xy.move_to(x, y, feed):
            self._set_error(CycleError.MOVE_FAILED, f"Move to work position failed")
            return False

        # Lower cylinder
        if not self._do_lower_cylinder():
            return False

        # Drive screw
        if not self._do_drive_screw():
            return False

        # Raise cylinder
        if not self._do_raise_cylinder():
            return False

        self._holes_completed += 1
        self._log('INFO', f"Hole {self._holes_completed}/{self._program.holes} completed")

        return True

    def _do_lower_cylinder(self) -> bool:
        """Lower the cylinder."""
        self._set_state(CycleState.LOWERING)

        self._relays.cylinder_extend()

        if not self._sensors.wait_for_active('cylinder_down', self._cylinder_down_timeout):
            self._relays.cylinder_stop()
            self._set_error(CycleError.CYLINDER_TIMEOUT, "Cylinder down timeout")
            return False

        return True

    def _do_raise_cylinder(self) -> bool:
        """Raise the cylinder."""
        self._set_state(CycleState.RAISING)

        self._relays.cylinder_retract()

        if not self._sensors.wait_for_active('cylinder_up', self._cylinder_up_timeout):
            self._relays.cylinder_stop()
            self._set_error(CycleError.CYLINDER_TIMEOUT, "Cylinder up timeout")
            return False

        self._relays.cylinder_stop()
        return True

    def _do_drive_screw(self) -> bool:
        """Drive screw until torque reached."""
        self._set_state(CycleState.SCREWING)

        # Start screwdriver (clockwise)
        self._relays.screwdriver_start(clockwise=True)

        # Wait for torque
        start = time.time()
        while time.time() - start < self._torque_timeout:
            if self._stop_event.is_set():
                self._relays.screwdriver_stop()
                return False

            if self._sensors.is_torque_reached():
                self._relays.screwdriver_stop()
                self._set_state(CycleState.VERIFYING)
                time.sleep(0.1)  # Brief settle time
                return True

            # Check for safety violations during screw
            if self._sensors.is_estop_pressed():
                self._relays.screwdriver_stop()
                self.emergency_stop()
                return False

            time.sleep(0.01)

        self._relays.screwdriver_stop()
        self._set_error(CycleError.TORQUE_TIMEOUT, "Torque not reached")
        return False

    # === Safety ===

    def _check_safety(self) -> bool:
        """Check if it's safe to operate."""
        if self._sensors.is_estop_pressed():
            self.emergency_stop()
            return False

        if self._sensors.is_area_blocked():
            return False

        return True

    def _wait_for_safety(self) -> None:
        """Wait for safety conditions to be met."""
        self._set_state(CycleState.PAUSED, message="Waiting for safety area to clear")

        while not self._stop_event.is_set():
            if self._sensors.is_estop_pressed():
                self.emergency_stop()
                return

            if not self._sensors.is_area_blocked():
                return

            time.sleep(0.1)

    # === Status and Callbacks ===

    def get_status(self) -> CycleStatus:
        """Get current cycle status."""
        return CycleStatus(
            state=self._state,
            error=self._error,
            error_message=self._error_message,
            current_device=self._program.name if self._program else "",
            current_step=self._current_step,
            total_steps=len(self._program.steps) if self._program else 0,
            holes_completed=self._holes_completed,
            total_holes=self._program.holes if self._program else 0,
            position_x=self._xy.x,
            position_y=self._xy.y,
            cycle_count=self._cycle_count
        )

    def on_state_change(self, callback: Callable[[CycleStatus], None]) -> None:
        """Register state change callback."""
        self._state_callbacks.append(callback)

    def on_log(self, callback: Callable[[str, str], None]) -> None:
        """Register log callback (level, message)."""
        self._log_callbacks.append(callback)

    def _notify_state_change(self) -> None:
        """Notify all state change callbacks."""
        status = self.get_status()
        for cb in self._state_callbacks:
            try:
                cb(status)
            except Exception as e:
                self._logger.error(f"State callback error: {e}")

    def _log(self, level: str, message: str) -> None:
        """Log message and notify callbacks."""
        getattr(self._logger, level.lower(), self._logger.info)(message)
        for cb in self._log_callbacks:
            try:
                cb(level, message)
            except Exception:
                pass

    # === Properties ===

    @property
    def state(self) -> CycleState:
        """Get current state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if cycle is running."""
        return self._state in (
            CycleState.HOMING, CycleState.MOVING_FREE, CycleState.MOVING_WORK,
            CycleState.LOWERING, CycleState.SCREWING, CycleState.RAISING,
            CycleState.VERIFYING
        )

    @property
    def is_paused(self) -> bool:
        """Check if cycle is paused."""
        return self._state == CycleState.PAUSED

    @property
    def is_error(self) -> bool:
        """Check if in error state."""
        return self._state == CycleState.ERROR
