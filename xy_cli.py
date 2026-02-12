#!/usr/bin/env python3
"""
xy_cli.py - CLI/Serial tool for controlling X/Y coordinate table via GPIO on Raspberry Pi 5.

Part of 3SD-SF-ScrewFeed automated screwdriver project.
Migrated from Arduino/RAMPS version (old_main.ino).

Requires: Python 3.10+, lgpio library, Raspberry Pi with GPIO access.
Optional: pyserial for serial mode.

Usage:
    python3 xy_cli.py              # Interactive CLI mode
    python3 xy_cli.py --serial /dev/ttyUSB0  # Serial mode for remote control
    python3 xy_cli.py --serial /dev/ttyAMA0 --baud 115200
"""
import sys
import os
import time
import argparse
from typing import Optional

# Change to /tmp before importing lgpio to avoid pipe file creation issues
# lgpio creates .lgd-nfy* files in current directory on import
# We stay in /tmp for the entire runtime to avoid issues with lgpio callbacks
os.chdir('/tmp')

# Clean up old lgpio notification files that may have wrong permissions
import glob
for old_file in glob.glob('/tmp/.lgd-nfy*'):
    try:
        os.remove(old_file)
    except Exception:
        pass

try:
    import lgpio
except ImportError:
    print("ERROR: lgpio library not found. Install with: pip install lgpio")
    sys.exit(1)

# =========================
# GPIO mapping (BCM) - Raspberry Pi 5
# =========================
X_MIN_GPIO = 2   # endstop X (GPIO2)
Y_MIN_GPIO = 3   # endstop Y (GPIO3)
ESTOP_GPIO = 13  # Emergency stop button (GPIO13)
ESTOP_ACTIVE_LOW = False  # False = GPIO HIGH means E-STOP triggered (button pressed = HIGH = STOP)

# X axis driver
X_STEP_GPIO = 9
X_DIR_GPIO  = 10
X_ENA_GPIO  = 11

# Y axis driver
Y_STEP_GPIO = 21
Y_DIR_GPIO  = 7
Y_ENA_GPIO  = 8

# =========================
# Logic levels
# =========================
ENDSTOP_ACTIVE_LOW = True   # NPN sensor: triggered -> LOW
ENA_ACTIVE_HIGH    = True   # ENA=1 means driver enabled

STEP_IDLE_LEVEL = 1
STEP_PULSE_ACTIVE_LOW = True  # common-anode wiring

# Direction inversion (adjust for your mechanics)
INVERT_X_DIR = False
INVERT_Y_DIR = False

# =========================
# Motion config
# =========================
STEPS_PER_MM_X = 40.0
STEPS_PER_MM_Y = 40.0

X_MIN_MM = 0.0
X_MAX_MM = 220.0
Y_MIN_MM = 0.0
Y_MAX_MM = 500.0

MAX_FEED_MM_S = 600.0   # Max feed rate mm/s
MAX_STEP_HZ   = 30000
PULSE_US      = 10

# Acceleration parameters
ACCEL_MM_S2 = 3500.0     # Acceleration in mm/s^2 (higher = faster ramp)
START_SPEED_MM_S = 15.0  # Starting speed for ramp (mm/s)

# Homing parameters
SCAN_RANGE_X_MM = 202.0
SCAN_RANGE_Y_MM = 502.0
BACKOFF_MM      = 5.0
SLOW_MM_S       = 10.0
HOMING_FAST_MM_S = 120.0      # Fast approach speed (was 300 mm/s)
HOMING_TIMEOUT_S = 60.0

# Work position (default)
WORK_X_MM = 5.0
WORK_Y_MM = 350.0
WORK_F_MM_MIN = 10000.0

# =========================
# State variables
# =========================
cur_x_mm = 0.0
cur_y_mm = 0.0
cur_feed_mm_min = 1000.0  # Current/default feed rate (mm/min)
estop = False  # Emergency stop flag
cancel_requested = False  # Flag to cancel current motion (checked in step loops)
x_homed = False  # True after successful X axis homing
y_homed = False  # True after successful Y axis homing

# Lazy GPIO initialization
io: Optional["GPIO"] = None

# Serial reader thread for background command processing
_serial_lock = None  # threading.Lock for serial access
_serial_port = None  # Serial port object for background reader


# =========================
# Utility functions
# =========================
def clamp(v: float, lo: float, hi: float) -> float:
    """Clamp value v between lo and hi."""
    return lo if v < lo else hi if v > hi else v


def busy_wait_ns(t_ns: int) -> None:
    """Busy-wait until the given nanosecond timestamp."""
    while time.perf_counter_ns() < t_ns:
        pass


# =========================
# GPIO Class
# =========================
class GPIO:
    """Wrapper for lgpio GPIO operations."""

    def __init__(self):
        try:
            self.h = lgpio.gpiochip_open(0)
        except Exception as e:
            print(f"ERROR: Cannot open GPIO chip: {e}")
            print("Make sure you're running on Raspberry Pi with GPIO access.")
            sys.exit(1)

        # --- Determine safe DIR levels (towards MIN endstop = homing direction) ---
        # set_dir_x/y(False) means "towards MIN", i.e. negative direction.
        # Replicate set_dir logic to compute idle level:
        x_dir_safe = (1 if INVERT_X_DIR else 0)  # False→0, inverted→1
        y_dir_safe = (1 if INVERT_Y_DIR else 0)

        # outputs X (ENA off first, then DIR safe, then STEP idle)
        lgpio.gpio_claim_output(self.h, X_ENA_GPIO, 0)   # disabled
        lgpio.gpio_claim_output(self.h, X_DIR_GPIO, x_dir_safe)
        lgpio.gpio_claim_output(self.h, X_STEP_GPIO, STEP_IDLE_LEVEL)

        # outputs Y (ENA off first, then DIR safe, then STEP idle)
        lgpio.gpio_claim_output(self.h, Y_ENA_GPIO, 0)   # disabled
        lgpio.gpio_claim_output(self.h, Y_DIR_GPIO, y_dir_safe)
        lgpio.gpio_claim_output(self.h, Y_STEP_GPIO, STEP_IDLE_LEVEL)

        # inputs with pull-ups
        pull_up = getattr(lgpio, "SET_PULL_UP", 0)
        lgpio.gpio_claim_input(self.h, X_MIN_GPIO, pull_up)
        lgpio.gpio_claim_input(self.h, Y_MIN_GPIO, pull_up)

        # E-STOP button input with pull-up (for NC button wired to GND)
        lgpio.gpio_claim_input(self.h, ESTOP_GPIO, pull_up)

    def close(self) -> None:
        """Release GPIO resources."""
        lgpio.gpiochip_close(self.h)

    def read(self, gpio: int) -> int:
        """Read GPIO pin state."""
        return lgpio.gpio_read(self.h, gpio)

    def write(self, gpio: int, val: int) -> None:
        """Write value to GPIO pin."""
        lgpio.gpio_write(self.h, gpio, val)


def init_gpio() -> None:
    """Initialize GPIO. Must be called before any GPIO operations."""
    global io
    if io is None:
        io = GPIO()


# =========================
# Low-level GPIO functions
# =========================
def endstop_active(gpio: int) -> bool:
    """Check if endstop is triggered (single read, for status display)."""
    v = io.read(gpio)
    return (v == 0) if ENDSTOP_ACTIVE_LOW else (v == 1)


def endstop_active_debounced(gpio: int, reads: int = 3) -> bool:
    """
    Check if endstop is triggered with debouncing.
    Requires multiple consecutive reads to confirm trigger.
    This helps filter out electrical noise from stepper motors.
    """
    count = 0
    for _ in range(reads):
        v = io.read(gpio)
        is_active = (v == 0) if ENDSTOP_ACTIVE_LOW else (v == 1)
        if is_active:
            count += 1
        else:
            count = 0  # Reset if any read is inactive
    return count >= reads


def estop_gpio_active() -> bool:
    """Check if hardware E-STOP button is triggered (pressed)."""
    if io is None:
        return False
    v = io.read(ESTOP_GPIO)
    return (v == 0) if ESTOP_ACTIVE_LOW else (v == 1)


def trigger_hardware_estop() -> None:
    """Called when hardware E-STOP button is detected - immediately stops everything."""
    global estop, cancel_requested, x_homed, y_homed
    if not estop:  # Only trigger once
        print("HARDWARE E-STOP: Physical button triggered!")
        estop = True
        cancel_requested = True
        x_homed = False  # Invalidate homing
        y_homed = False
        enable_all(False)
        print("E-STOP: Motors disabled, homing invalidated - rehoming required")


def enable_driver_x(en: bool) -> None:
    if ENA_ACTIVE_HIGH:
        io.write(X_ENA_GPIO, 1 if en else 0)
    else:
        io.write(X_ENA_GPIO, 0 if en else 1)


def enable_driver_y(en: bool) -> None:
    if ENA_ACTIVE_HIGH:
        io.write(Y_ENA_GPIO, 1 if en else 0)
    else:
        io.write(Y_ENA_GPIO, 0 if en else 1)


def enable_all(en: bool) -> None:
    enable_driver_x(en)
    enable_driver_y(en)


def set_dir_x(positive: bool) -> None:
    val = 1 if positive else 0
    if INVERT_X_DIR:
        val = 1 - val
    io.write(X_DIR_GPIO, val)


def set_dir_y(positive: bool) -> None:
    val = 1 if positive else 0
    if INVERT_Y_DIR:
        val = 1 - val
    io.write(Y_DIR_GPIO, val)


# =========================
# Step pulse generation
# =========================
def step_pulses(step_gpio: int, steps: int, step_hz: float,
                stop_on_endstop_gpio: Optional[int] = None,
                use_accel: bool = False, steps_per_mm: float = 40.0) -> bool:
    """
    Generates STEP pulses for one axis (blocking).

    Args:
        step_gpio: GPIO pin for STEP signal
        steps: Number of steps to generate
        step_hz: Target step frequency (Hz)
        stop_on_endstop_gpio: Stop if this endstop triggers
        use_accel: Enable acceleration/deceleration ramp
        steps_per_mm: Steps per mm (for acceleration calculation)

    Returns True if finished normally, False if stopped by endstop or cancel.
    """
    global cancel_requested

    if steps <= 0:
        return True

    if step_hz <= 0:
        step_hz = 1.0
    step_hz = min(step_hz, MAX_STEP_HZ)

    hi_ns = int(PULSE_US * 1000)
    min_period_ns = hi_ns * 3  # Minimum period limit

    if not use_accel:
        # Simple constant speed motion
        period_ns = max(int(1e9 / step_hz), min_period_ns)
        t = time.perf_counter_ns()

        for _ in range(steps):
            # Check for hardware E-STOP button
            if estop_gpio_active():
                trigger_hardware_estop()
                return False
            # Check for cancel request
            if cancel_requested:
                return False
            # Use debounced endstop check to filter electrical noise
            if stop_on_endstop_gpio is not None and endstop_active_debounced(stop_on_endstop_gpio, 3):
                return False

            if STEP_PULSE_ACTIVE_LOW:
                io.write(step_gpio, 0)
                t += hi_ns
                busy_wait_ns(t)
                io.write(step_gpio, 1)
                t += (period_ns - hi_ns)
                busy_wait_ns(t)
            else:
                io.write(step_gpio, 1)
                t += hi_ns
                busy_wait_ns(t)
                io.write(step_gpio, 0)
                t += (period_ns - hi_ns)
                busy_wait_ns(t)
        return True

    # === Trapezoidal motion profile with acceleration ===

    # Convert to step frequencies
    start_hz = START_SPEED_MM_S * steps_per_mm
    target_hz = step_hz
    accel_step_hz_per_step = ACCEL_MM_S2 * steps_per_mm / target_hz  # Approximate

    # Calculate steps needed for acceleration and deceleration
    # Using v^2 = v0^2 + 2*a*s => s = (v^2 - v0^2) / (2*a)
    # In steps: n_accel = (f_target^2 - f_start^2) / (2 * accel_in_hz_per_step * f_target)
    if ACCEL_MM_S2 > 0:
        accel_hz2_per_step = 2.0 * ACCEL_MM_S2 * steps_per_mm
        n_accel = int((target_hz * target_hz - start_hz * start_hz) / accel_hz2_per_step)
        n_decel = n_accel
    else:
        n_accel = 0
        n_decel = 0

    # Adjust if path is too short for full accel+decel
    if n_accel + n_decel > steps:
        # Triangular profile - no constant speed phase
        n_accel = steps // 2
        n_decel = steps - n_accel

    n_const = steps - n_accel - n_decel

    t = time.perf_counter_ns()
    current_hz = start_hz
    step_count = 0

    for i in range(steps):
        # Check for hardware E-STOP button
        if estop_gpio_active():
            trigger_hardware_estop()
            return False
        # Check for cancel request
        if cancel_requested:
            return False
        # Use debounced endstop check to filter electrical noise
        if stop_on_endstop_gpio is not None and endstop_active_debounced(stop_on_endstop_gpio, 3):
            return False

        # Calculate current frequency based on phase
        if i < n_accel:
            # Acceleration phase: v^2 = v0^2 + 2*a*s
            current_hz = (start_hz * start_hz + accel_hz2_per_step * (i + 1)) ** 0.5
            current_hz = min(current_hz, target_hz)
        elif i < n_accel + n_const:
            # Constant speed phase
            current_hz = target_hz
        else:
            # Deceleration phase
            steps_remaining = steps - i
            current_hz = (start_hz * start_hz + accel_hz2_per_step * steps_remaining) ** 0.5
            current_hz = min(current_hz, target_hz)

        # Ensure minimum speed
        current_hz = max(current_hz, start_hz)
        current_hz = min(current_hz, MAX_STEP_HZ)

        period_ns = max(int(1e9 / current_hz), min_period_ns)

        # Generate pulse
        if STEP_PULSE_ACTIVE_LOW:
            io.write(step_gpio, 0)
            t += hi_ns
            busy_wait_ns(t)
            io.write(step_gpio, 1)
            t += (period_ns - hi_ns)
            busy_wait_ns(t)
        else:
            io.write(step_gpio, 1)
            t += hi_ns
            busy_wait_ns(t)
            io.write(step_gpio, 0)
            t += (period_ns - hi_ns)
            busy_wait_ns(t)

    return True


# =========================
# Motion functions
# =========================
def move_axis_abs(axis: str, target_mm: float, feed_mm_min: float) -> bool:
    """Move single axis to absolute position with acceleration. Returns True if successful."""
    global cur_x_mm, cur_y_mm

    if estop:
        return False

    # Require homing before movement (safety after E-STOP)
    if axis == "X" and not x_homed:
        print("ERROR: X axis not homed - movement blocked")
        return False
    if axis == "Y" and not y_homed:
        print("ERROR: Y axis not homed - movement blocked")
        return False

    if axis == "X":
        target_mm = float(clamp(target_mm, X_MIN_MM, X_MAX_MM))
        delta = target_mm - cur_x_mm
        if abs(delta) < 1e-6:
            return True
        positive = delta > 0
        set_dir_x(positive)

        steps = int(round(abs(delta) * STEPS_PER_MM_X))
        if steps <= 0:
            cur_x_mm = target_mm
            return True

        mm_per_s = min(max(feed_mm_min / 60.0, 0.1), MAX_FEED_MM_S)
        step_hz = mm_per_s * STEPS_PER_MM_X

        enable_driver_x(True)
        stop_gpio = X_MIN_GPIO if not positive else None
        ok = step_pulses(X_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio,
                        use_accel=True, steps_per_mm=STEPS_PER_MM_X)

        if ok:
            cur_x_mm = target_mm
        elif stop_gpio is not None and endstop_active_debounced(stop_gpio, 3):
            # Only set to 0 if we really hit the MIN endstop (debounced)
            cur_x_mm = 0.0
        # For other failures (E-STOP, cancel), keep current position
        # Position will be re-established after homing
        return ok

    if axis == "Y":
        target_mm = float(clamp(target_mm, Y_MIN_MM, Y_MAX_MM))
        delta = target_mm - cur_y_mm
        if abs(delta) < 1e-6:
            return True
        positive = delta > 0
        set_dir_y(positive)

        steps = int(round(abs(delta) * STEPS_PER_MM_Y))
        if steps <= 0:
            cur_y_mm = target_mm
            return True

        mm_per_s = min(max(feed_mm_min / 60.0, 0.1), MAX_FEED_MM_S)
        step_hz = mm_per_s * STEPS_PER_MM_Y

        enable_driver_y(True)
        stop_gpio = Y_MIN_GPIO if not positive else None
        ok = step_pulses(Y_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio,
                        use_accel=True, steps_per_mm=STEPS_PER_MM_Y)

        if ok:
            cur_y_mm = target_mm
        elif stop_gpio is not None and endstop_active_debounced(stop_gpio, 3):
            # Only set to 0 if we really hit the MIN endstop (debounced)
            cur_y_mm = 0.0
        # For other failures (E-STOP, cancel), keep current position
        # Position will be re-established after homing
        return ok

    raise ValueError("Axis must be X or Y")


def move_xy_abs(x_mm: Optional[float], y_mm: Optional[float], feed_mm_min: float) -> bool:
    """
    Move both axes to absolute position using interleaved stepping with acceleration.
    Uses trapezoidal motion profile for smooth acceleration/deceleration.
    Returns True if move completed successfully.
    """
    global cur_x_mm, cur_y_mm, cancel_requested

    if estop or cancel_requested:
        return False

    # Require homing before movement (safety after E-STOP)
    if not x_homed or not y_homed:
        print(f"ERROR: Axes not homed (X:{x_homed}, Y:{y_homed}) - movement blocked")
        return False

    if x_mm is None:
        x_mm = cur_x_mm
    if y_mm is None:
        y_mm = cur_y_mm

    x_mm = float(clamp(x_mm, X_MIN_MM, X_MAX_MM))
    y_mm = float(clamp(y_mm, Y_MIN_MM, Y_MAX_MM))

    dx = x_mm - cur_x_mm
    dy = y_mm - cur_y_mm

    # Debug output
    print(f"DEBUG move_xy_abs: cur=({cur_x_mm:.2f}, {cur_y_mm:.2f}) -> target=({x_mm:.2f}, {y_mm:.2f})")
    print(f"DEBUG move_xy_abs: dx={dx:.2f}, dy={dy:.2f}, dir_x={'+'if dx>=0 else'-'}, dir_y={'+'if dy>=0 else'-'}")

    sx = int(round(abs(dx) * STEPS_PER_MM_X))
    sy = int(round(abs(dy) * STEPS_PER_MM_Y))
    sx_orig = sx
    sy_orig = sy

    if sx == 0 and sy == 0:
        return True

    set_dir_x(dx >= 0)
    set_dir_y(dy >= 0)

    enable_all(True)

    # Calculate path length in mm for acceleration planning
    path_mm = (dx * dx + dy * dy) ** 0.5
    if path_mm < 1e-6:
        path_mm = max(abs(dx), abs(dy))

    # Target speed
    target_mm_s = min(max(feed_mm_min / 60.0, 0.1), MAX_FEED_MM_S)
    start_mm_s = START_SPEED_MM_S

    # Calculate distance needed for acceleration/deceleration
    # Using v^2 = v0^2 + 2*a*s => s = (v^2 - v0^2) / (2*a)
    if ACCEL_MM_S2 > 0:
        accel_dist_mm = (target_mm_s * target_mm_s - start_mm_s * start_mm_s) / (2.0 * ACCEL_MM_S2)
        decel_dist_mm = accel_dist_mm
    else:
        accel_dist_mm = 0.0
        decel_dist_mm = 0.0

    # Adjust if path is too short for full accel+decel (triangular profile)
    if accel_dist_mm + decel_dist_mm > path_mm:
        accel_dist_mm = path_mm / 2.0
        decel_dist_mm = path_mm - accel_dist_mm

    const_dist_mm = path_mm - accel_dist_mm - decel_dist_mm

    hi_ns = int(PULSE_US * 1000)
    min_period_ns = hi_ns * 3

    stop_x = (dx < 0)
    stop_y = (dy < 0)

    # Debug: check endstop status at start
    x_endstop_at_start = endstop_active(X_MIN_GPIO)
    y_endstop_at_start = endstop_active(Y_MIN_GPIO)
    print(f"DEBUG move_xy_abs: stop_x={stop_x}, stop_y={stop_y}, X_MIN={x_endstop_at_start}, Y_MIN={y_endstop_at_start}")

    done_x = 0
    done_y = 0

    io.write(X_STEP_GPIO, STEP_IDLE_LEVEL)
    io.write(Y_STEP_GPIO, STEP_IDLE_LEVEL)

    hit_x = False
    hit_y = False

    # Track distance traveled for acceleration profile
    dist_traveled_mm = 0.0
    last_x = 0
    last_y = 0

    # Use the longer axis to determine the main timing
    total_steps = max(sx, sy)
    if total_steps == 0:
        return True

    # Calculate step ratios
    ratio_x = sx / total_steps if total_steps > 0 else 0
    ratio_y = sy / total_steps if total_steps > 0 else 0

    # Bresenham-style accumulators for even step distribution
    accum_x = 0.0
    accum_y = 0.0

    t = time.perf_counter_ns()

    for step_i in range(total_steps):
        # Check for hardware E-STOP button
        if estop_gpio_active():
            trigger_hardware_estop()
            # Update position based on steps done so far
            old_x, old_y = cur_x_mm, cur_y_mm
            if done_x > 0:
                cur_x_mm += (done_x / STEPS_PER_MM_X) * (1 if dx >= 0 else -1)
            if done_y > 0:
                cur_y_mm += (done_y / STEPS_PER_MM_Y) * (1 if dy >= 0 else -1)
            print(f"ESTOP: Movement stopped at step {step_i}/{total_steps}")
            print(f"ESTOP: Position updated: ({old_x:.2f}, {old_y:.2f}) -> ({cur_x_mm:.2f}, {cur_y_mm:.2f})")
            return False
        # Check for cancel request
        if cancel_requested:
            # Update position based on steps done so far
            old_x, old_y = cur_x_mm, cur_y_mm
            if done_x > 0:
                cur_x_mm += (done_x / STEPS_PER_MM_X) * (1 if dx >= 0 else -1)
            if done_y > 0:
                cur_y_mm += (done_y / STEPS_PER_MM_Y) * (1 if dy >= 0 else -1)
            print(f"CANCEL: Movement stopped at step {step_i}/{total_steps}")
            print(f"CANCEL: Position updated: ({old_x:.2f}, {old_y:.2f}) -> ({cur_x_mm:.2f}, {cur_y_mm:.2f})")
            print(f"CANCEL: Steps done: X={done_x}/{sx}, Y={done_y}/{sy}")
            return False

        # Calculate distance traveled based on steps done
        actual_dx = done_x / STEPS_PER_MM_X if STEPS_PER_MM_X > 0 else 0
        actual_dy = done_y / STEPS_PER_MM_Y if STEPS_PER_MM_Y > 0 else 0
        dist_traveled_mm = (actual_dx * actual_dx + actual_dy * actual_dy) ** 0.5

        # Determine current speed based on motion phase
        if dist_traveled_mm < accel_dist_mm:
            # Acceleration phase: v^2 = v0^2 + 2*a*s
            current_mm_s = (start_mm_s * start_mm_s + 2.0 * ACCEL_MM_S2 * dist_traveled_mm) ** 0.5
            current_mm_s = min(current_mm_s, target_mm_s)
        elif dist_traveled_mm < accel_dist_mm + const_dist_mm:
            # Constant speed phase
            current_mm_s = target_mm_s
        else:
            # Deceleration phase
            dist_remaining_mm = path_mm - dist_traveled_mm
            current_mm_s = (start_mm_s * start_mm_s + 2.0 * ACCEL_MM_S2 * dist_remaining_mm) ** 0.5
            current_mm_s = min(current_mm_s, target_mm_s)

        # Ensure minimum speed
        current_mm_s = max(current_mm_s, start_mm_s)

        # Calculate period based on current speed
        # Period is for the dominant axis
        if sx >= sy and sx > 0:
            current_hz = current_mm_s * STEPS_PER_MM_X
        elif sy > 0:
            current_hz = current_mm_s * STEPS_PER_MM_Y
        else:
            current_hz = current_mm_s * max(STEPS_PER_MM_X, STEPS_PER_MM_Y)

        current_hz = min(current_hz, MAX_STEP_HZ)
        period_ns = max(int(1e9 / current_hz), min_period_ns)

        # Determine which axes need a step this iteration
        accum_x += ratio_x
        accum_y += ratio_y

        do_step_x = accum_x >= 1.0 and done_x < sx
        do_step_y = accum_y >= 1.0 and done_y < sy

        if do_step_x:
            accum_x -= 1.0

        if do_step_y:
            accum_y -= 1.0

        # Check endstops and generate pulses
        # Use debounced endstop check to filter electrical noise from steppers
        if do_step_x:
            if stop_x and endstop_active_debounced(X_MIN_GPIO, 3):
                cur_x_mm = 0.0
                hit_x = True
                do_step_x = False
            else:
                if STEP_PULSE_ACTIVE_LOW:
                    io.write(X_STEP_GPIO, 0)
                else:
                    io.write(X_STEP_GPIO, 1)
                done_x += 1

        if do_step_y:
            if stop_y and endstop_active_debounced(Y_MIN_GPIO, 3):
                cur_y_mm = 0.0
                hit_y = True
                do_step_y = False
            else:
                if STEP_PULSE_ACTIVE_LOW:
                    io.write(Y_STEP_GPIO, 0)
                else:
                    io.write(Y_STEP_GPIO, 1)
                done_y += 1

        # Hold pulse high time
        if do_step_x or do_step_y:
            t += hi_ns
            busy_wait_ns(t)

            # Return to idle
            if do_step_x:
                io.write(X_STEP_GPIO, STEP_IDLE_LEVEL)
            if do_step_y:
                io.write(Y_STEP_GPIO, STEP_IDLE_LEVEL)

        # Wait for period
        t += (period_ns - hi_ns) if (do_step_x or do_step_y) else period_ns
        busy_wait_ns(t)

        # Early exit if both axes hit endstops
        if hit_x and hit_y:
            break

    print(f"DEBUG move_xy_abs: done_x={done_x}/{sx_orig}, done_y={done_y}/{sy_orig}, hit_x={hit_x}, hit_y={hit_y}")

    # Allow 1-step tolerance due to floating point rounding in Bresenham algorithm
    # Position should update if we completed at least 99.9% of steps and didn't hit endstop
    x_complete = (done_x >= sx_orig - 1) if sx_orig > 0 else True
    y_complete = (done_y >= sy_orig - 1) if sy_orig > 0 else True

    if x_complete and not hit_x:
        cur_x_mm = x_mm
    else:
        print(f"DEBUG move_xy_abs: X position NOT updated (done_x={done_x}, sx={sx_orig}, hit_x={hit_x})")
    if y_complete and not hit_y:
        cur_y_mm = y_mm
    else:
        print(f"DEBUG move_xy_abs: Y position NOT updated (done_y={done_y}, sy={sy_orig}, hit_y={hit_y})")

    print(f"DEBUG move_xy_abs: final pos=({cur_x_mm:.2f}, {cur_y_mm:.2f})")

    return not (hit_x or hit_y)


# =========================
# Circular interpolation (G2/G3)
# =========================
import math

def arc_move(x_end: float, y_end: float, i_offset: float, j_offset: float,
             clockwise: bool, feed_mm_min: float, passes: int = 1) -> bool:
    """
    Perform circular arc interpolation (G2/G3).

    Args:
        x_end: Target X position (absolute)
        y_end: Target Y position (absolute)
        i_offset: X offset from current position to arc center
        j_offset: Y offset from current position to arc center
        clockwise: True for G2 (CW), False for G3 (CCW)
        feed_mm_min: Feed rate in mm/min
        passes: Number of full circles (P parameter), default 1

    Returns:
        True if arc completed successfully.
    """
    global cur_x_mm, cur_y_mm

    if estop:
        return False

    # Calculate arc center
    cx = cur_x_mm + i_offset
    cy = cur_y_mm + j_offset

    # Calculate radius from start and end points
    r_start = math.sqrt((cur_x_mm - cx)**2 + (cur_y_mm - cy)**2)
    r_end = math.sqrt((x_end - cx)**2 + (y_end - cy)**2)

    # Check radius consistency (allow 0.1mm tolerance)
    if abs(r_start - r_end) > 0.1:
        print(f"WARNING: Arc radius mismatch: start={r_start:.3f}, end={r_end:.3f}")

    radius = r_start

    if radius < 0.01:
        # Degenerate arc, just move to end point
        return move_xy_abs(x_end, y_end, feed_mm_min)

    # Calculate start and end angles
    start_angle = math.atan2(cur_y_mm - cy, cur_x_mm - cx)
    end_angle = math.atan2(y_end - cy, x_end - cx)

    # Calculate angular distance
    if clockwise:
        # CW: angle decreases
        angle_diff = start_angle - end_angle
        if angle_diff <= 0:
            angle_diff += 2 * math.pi
    else:
        # CCW: angle increases
        angle_diff = end_angle - start_angle
        if angle_diff <= 0:
            angle_diff += 2 * math.pi

    # Check if this is a full circle (start == end)
    is_full_circle = (abs(x_end - cur_x_mm) < 0.01 and abs(y_end - cur_y_mm) < 0.01)

    if is_full_circle:
        # For full circles, use P parameter for number of rotations
        # If P not specified (passes=1) and start==end, do one full circle
        angle_diff = 2 * math.pi * passes
    elif passes > 1:
        # Add extra full rotations before the final arc
        angle_diff += 2 * math.pi * (passes - 1)

    # Calculate arc length
    arc_length = radius * angle_diff

    # Determine number of segments (aim for ~0.5mm per segment for smoothness)
    segment_length = 0.5  # mm
    num_segments = max(int(arc_length / segment_length), 4)

    # Calculate angle step
    angle_step = angle_diff / num_segments
    if clockwise:
        angle_step = -angle_step

    print(f"DEBUG arc_move: center=({cx:.2f}, {cy:.2f}), r={radius:.2f}, "
          f"passes={passes}, full_circle={is_full_circle}, "
          f"angle={math.degrees(start_angle):.1f}° total={math.degrees(angle_diff):.1f}°, "
          f"segments={num_segments}")

    # Execute arc as series of linear moves
    current_angle = start_angle

    for i in range(num_segments):
        # Check for hardware E-STOP button
        if estop_gpio_active():
            trigger_hardware_estop()
            return False
        if estop:
            return False

        current_angle += angle_step

        # Calculate next point on arc
        if i == num_segments - 1:
            # Last segment: use exact end point to avoid accumulation errors
            next_x = x_end
            next_y = y_end
        else:
            next_x = cx + radius * math.cos(current_angle)
            next_y = cy + radius * math.sin(current_angle)

        # Clamp to bounds
        next_x = float(clamp(next_x, X_MIN_MM, X_MAX_MM))
        next_y = float(clamp(next_y, Y_MIN_MM, Y_MAX_MM))

        # Move to next point
        if not move_xy_abs(next_x, next_y, feed_mm_min):
            return False

    return True


def arc_move_radius(x_end: float, y_end: float, radius: float,
                    clockwise: bool, feed_mm_min: float) -> bool:
    """
    Perform circular arc using radius notation (R parameter).

    Args:
        x_end: Target X position (absolute)
        y_end: Target Y position (absolute)
        radius: Arc radius (positive = minor arc, negative = major arc)
        clockwise: True for G2 (CW), False for G3 (CCW)
        feed_mm_min: Feed rate in mm/min

    Returns:
        True if arc completed successfully.
    """
    global cur_x_mm, cur_y_mm

    # Calculate chord
    dx = x_end - cur_x_mm
    dy = y_end - cur_y_mm
    chord = math.sqrt(dx*dx + dy*dy)

    if chord < 0.001:
        # Start and end are the same point
        return True

    if abs(radius) < chord / 2:
        print(f"ERROR: Radius {radius} too small for chord length {chord}")
        return False

    # Calculate distance from chord midpoint to arc center
    h = math.sqrt(radius*radius - (chord/2)**2)

    # Midpoint of chord
    mx = (cur_x_mm + x_end) / 2
    my = (cur_y_mm + y_end) / 2

    # Perpendicular vector to chord (normalized)
    px = -dy / chord
    py = dx / chord

    # Determine which side of chord the center is on
    # For CW with positive R, or CCW with negative R: use one side
    # For CW with negative R, or CCW with positive R: use other side
    if (clockwise and radius > 0) or (not clockwise and radius < 0):
        h = -h

    # Arc center
    cx = mx + h * px
    cy = my + h * py

    # Calculate I, J offsets
    i_offset = cx - cur_x_mm
    j_offset = cy - cur_y_mm

    return arc_move(x_end, y_end, i_offset, j_offset, clockwise, feed_mm_min)


# =========================
# Motor Music (stepper singing)
# =========================

# Musical note frequencies (Hz) - higher octaves for motor singing
NOTES = {
    # Octave 5 (mid-high)
    'C5': 523.25, 'D5': 587.33, 'E5': 659.25, 'F5': 698.46, 'G5': 783.99, 'A5': 880.00, 'B5': 987.77,
    # Octave 6 (high - best for motor singing)
    'C6': 1046.50, 'D6': 1174.66, 'E6': 1318.51, 'F6': 1396.91, 'G6': 1567.98, 'A6': 1760.00, 'B6': 1975.53,
    # Octave 7 (very high)
    'C7': 2093.00, 'D7': 2349.32, 'E7': 2637.02, 'F7': 2793.83, 'G7': 3135.96, 'A7': 3520.00, 'B7': 3951.07,
    'REST': 0,
}


def play_tone(freq_hz: float, duration_ms: int, axis: str = "X") -> None:
    """Play a tone using stepper motor at given frequency."""
    if freq_hz <= 0:
        time.sleep(duration_ms / 1000.0)
        return

    if axis == "X":
        step_gpio = X_STEP_GPIO
        enable_driver_x(True)
    else:
        step_gpio = Y_STEP_GPIO
        enable_driver_y(True)

    # Calculate steps needed for duration
    steps = int(freq_hz * duration_ms / 1000.0)
    if steps <= 0:
        return

    hi_ns = int(PULSE_US * 1000)
    period_ns = int(1e9 / freq_hz)
    if period_ns < hi_ns * 3:
        period_ns = hi_ns * 3

    t = time.perf_counter_ns()

    for _ in range(steps):
        if STEP_PULSE_ACTIVE_LOW:
            io.write(step_gpio, 0)
            t += hi_ns
            busy_wait_ns(t)
            io.write(step_gpio, 1)
        else:
            io.write(step_gpio, 1)
            t += hi_ns
            busy_wait_ns(t)
            io.write(step_gpio, 0)
        t += (period_ns - hi_ns)
        busy_wait_ns(t)


def play_melody(melody: list, axis: str = "X") -> None:
    """Play a melody. Each item is (note_name, duration_ms)."""
    for note, duration in melody:
        freq = NOTES.get(note, 0)
        play_tone(freq, duration, axis)


def play_rock_riff() -> None:
    """Play a simple scale: do-re-mi-fa-sol-la-si at high frequencies."""
    # До-Ре-Ми-Фа-Соль-Ля-Си (C-D-E-F-G-A-B) in octave 6 for clear motor singing
    melody = [
        # Scale up
        ('C6', 300),  # До
        ('D6', 300),  # Ре
        ('E6', 300),  # Ми
        ('F6', 300),  # Фа
        ('G6', 300),  # Соль
        ('A6', 300),  # Ля
        ('B6', 300),  # Си
        ('C7', 500),  # До (высокое)
        ('REST', 200),
        # Scale down
        ('C7', 300),
        ('B6', 300),
        ('A6', 300),
        ('G6', 300),
        ('F6', 300),
        ('E6', 300),
        ('D6', 300),
        ('C6', 500),
    ]

    enable_all(True)
    for note, duration in melody:
        freq = NOTES.get(note, 0)
        play_tone(freq, duration, "X")


# =========================
# Homing functions
# =========================
def home_axis(axis: str) -> bool:
    """
    Home specified axis to MIN endstop.
    Returns True if homing successful, False if timeout or failure.
    """
    global cur_x_mm, cur_y_mm, x_homed, y_homed

    if estop:
        return False

    start_time = time.time()

    def check_timeout() -> bool:
        return time.time() - start_time > HOMING_TIMEOUT_S

    if axis == "X":
        enable_driver_x(True)
        set_dir_x(False)  # safe direction (towards MIN endstop)
        time.sleep(0.01)  # let driver latch direction after power-on

        fast_hz = min(HOMING_FAST_MM_S * STEPS_PER_MM_X, MAX_STEP_HZ)
        slow_hz = SLOW_MM_S * STEPS_PER_MM_X

        # if already on endstop -> quick backoff
        if endstop_active(X_MIN_GPIO):
            set_dir_x(True)
            step_pulses(X_STEP_GPIO, int(BACKOFF_MM * STEPS_PER_MM_X), fast_hz, None)
            time.sleep(0.05)

        # fast approach until endstop (no distance limit, only timeout)
        set_dir_x(False)
        chunk = int(STEPS_PER_MM_X * 10)

        while True:
            if check_timeout():
                return False
            # Check hardware E-STOP
            if estop_gpio_active():
                trigger_hardware_estop()
                return False
            ok = step_pulses(X_STEP_GPIO, chunk, fast_hz, stop_on_endstop_gpio=X_MIN_GPIO)
            if not ok:
                break

        # backoff
        set_dir_x(True)
        step_pulses(X_STEP_GPIO, int(BACKOFF_MM * STEPS_PER_MM_X), slow_hz, None)
        time.sleep(0.05)

        # slow touch
        set_dir_x(False)
        while not endstop_active(X_MIN_GPIO):
            if check_timeout():
                return False
            # Check hardware E-STOP
            if estop_gpio_active():
                trigger_hardware_estop()
                return False
            step_pulses(X_STEP_GPIO, int(STEPS_PER_MM_X * 0.5), slow_hz, stop_on_endstop_gpio=X_MIN_GPIO)

        cur_x_mm = 0.0
        x_homed = True
        print("X axis homed successfully")
        return True

    if axis == "Y":
        enable_driver_y(True)
        set_dir_y(False)  # safe direction (towards MIN endstop)
        time.sleep(0.01)  # let driver latch direction after power-on

        fast_hz = min(HOMING_FAST_MM_S * STEPS_PER_MM_Y, MAX_STEP_HZ)
        slow_hz = SLOW_MM_S * STEPS_PER_MM_Y

        if endstop_active(Y_MIN_GPIO):
            set_dir_y(True)
            step_pulses(Y_STEP_GPIO, int(BACKOFF_MM * STEPS_PER_MM_Y), fast_hz, None)
            time.sleep(0.05)

        # fast approach until endstop (no distance limit, only timeout)
        set_dir_y(False)
        chunk = int(STEPS_PER_MM_Y * 10)

        while True:
            if check_timeout():
                return False
            # Check hardware E-STOP
            if estop_gpio_active():
                trigger_hardware_estop()
                return False
            ok = step_pulses(Y_STEP_GPIO, chunk, fast_hz, stop_on_endstop_gpio=Y_MIN_GPIO)
            if not ok:
                break

        set_dir_y(True)
        step_pulses(Y_STEP_GPIO, int(BACKOFF_MM * STEPS_PER_MM_Y), slow_hz, None)
        time.sleep(0.05)

        set_dir_y(False)
        while not endstop_active(Y_MIN_GPIO):
            if check_timeout():
                return False
            # Check hardware E-STOP
            if estop_gpio_active():
                trigger_hardware_estop()
                return False
            step_pulses(Y_STEP_GPIO, int(STEPS_PER_MM_Y * 0.5), slow_hz, stop_on_endstop_gpio=Y_MIN_GPIO)

        cur_y_mm = 0.0
        y_homed = True
        print("Y axis homed successfully")
        return True

    raise ValueError("Axis must be X or Y")


def home_all() -> bool:
    """Home both axes (Y first, then X)."""
    ok_y = home_axis("Y")
    ok_x = home_axis("X")
    return ok_x and ok_y


# =========================
# Status and reporting
# =========================
def get_status_str() -> str:
    """Get status string in format compatible with Arduino version."""
    x_end = "TRIG" if endstop_active(X_MIN_GPIO) else "open"
    y_end = "TRIG" if endstop_active(Y_MIN_GPIO) else "open"
    hw_estop = "TRIG" if estop_gpio_active() else "open"
    return f"STATUS X:{cur_x_mm:.3f} Y:{cur_y_mm:.3f} X_MIN:{x_end} Y_MIN:{y_end} X_HOMED:{'1' if x_homed else '0'} Y_HOMED:{'1' if y_homed else '0'} ESTOP:{'1' if estop else '0'} HW_ESTOP:{hw_estop}"


def get_endstop_str() -> str:
    """Get endstop status string (M119 format)."""
    x_end = "TRIGGERED" if endstop_active(X_MIN_GPIO) else "open"
    y_end = "TRIGGERED" if endstop_active(Y_MIN_GPIO) else "open"
    return f"X_MIN:{x_end} Y_MIN:{y_end}"


# =========================
# Limit checking helper
# =========================
def check_limits(x: Optional[float], y: Optional[float]) -> tuple:
    """
    Check if coordinates exceed soft limits and return clamped values with warnings.

    Returns:
        tuple: (clamped_x, clamped_y, warnings_list)
    """
    warnings = []

    if x is not None:
        if x < X_MIN_MM:
            warnings.append(f"LIMIT_X_MIN:{X_MIN_MM:.1f}")
            x = X_MIN_MM
        elif x > X_MAX_MM:
            warnings.append(f"LIMIT_X_MAX:{X_MAX_MM:.1f}")
            x = X_MAX_MM

    if y is not None:
        if y < Y_MIN_MM:
            warnings.append(f"LIMIT_Y_MIN:{Y_MIN_MM:.1f}")
            y = Y_MIN_MM
        elif y > Y_MAX_MM:
            warnings.append(f"LIMIT_Y_MAX:{Y_MAX_MM:.1f}")
            y = Y_MAX_MM

    return x, y, warnings


# =========================
# Command handler
# =========================
def handle_command(line: str) -> str:
    """
    Handle a single command line and return response.
    Compatible with Arduino version command set.
    """
    global cur_x_mm, cur_y_mm, cur_feed_mm_min, estop, x_homed, y_homed
    global STEPS_PER_MM_X, STEPS_PER_MM_Y
    global X_MIN_MM, X_MAX_MM, Y_MIN_MM, Y_MAX_MM
    global WORK_X_MM, WORK_Y_MM, WORK_F_MM_MIN

    line = line.strip()
    if not line:
        return ""

    up = line.upper()

    try:
        # === Connection test ===
        if up == "PING":
            return "PONG"

        # === Get IP address ===
        if up == "GETIP":
            import subprocess
            try:
                result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
                ip = result.stdout.strip().split()[0] if result.stdout.strip() else "NO_IP"
                return f"IP {ip}"
            except Exception as e:
                return "IP NO_IP"

        # === Status commands ===
        if up == "M114":
            return get_status_str() + "\nok"

        if up == "M119":
            return get_endstop_str() + "\nok"

        # === E-STOP commands ===
        if up == "M112":
            estop = True
            x_homed = False  # Invalidate homing - position may be lost
            y_homed = False
            enable_all(False)
            print("E-STOP: Homing invalidated - rehoming required before movement")
            return "ok ESTOP"

        if up == "M999":
            estop = False
            enable_all(True)
            return "ok CLEAR"

        # === Driver enable/disable ===
        if up == "M17":
            if estop:
                return "err ESTOP"
            enable_all(True)
            return "ok"

        if up == "M18":
            enable_all(False)
            return "ok"

        # === Homing commands (G28 style) ===
        if up == "G28":
            if estop:
                return "err ESTOP"
            return "ok IN_HOME_POS" if home_all() else "err HOME_NOT_FOUND"

        if up == "G28 X":
            if estop:
                return "err ESTOP"
            return "ok IN_X_HOME_POS" if home_axis("X") else "err HOME_X_NOT_FOUND"

        if up == "G28 Y":
            if estop:
                return "err ESTOP"
            return "ok IN_Y_HOME_POS" if home_axis("Y") else "err HOME_Y_NOT_FOUND"

        # === Legacy HOME commands ===
        if up == "HOME":
            if estop:
                return "err ESTOP"
            return "ok IN_HOME_POS" if home_all() else "err HOME_NOT_FOUND"

        if up == "HOME X":
            if estop:
                return "err ESTOP"
            return "ok IN_X_HOME_POS" if home_axis("X") else "err HOME_X_NOT_FOUND"

        if up == "HOME Y":
            if estop:
                return "err ESTOP"
            return "ok IN_Y_HOME_POS" if home_axis("Y") else "err HOME_Y_NOT_FOUND"

        # === CAL (home + go to zero) ===
        if up == "CAL":
            if estop:
                return "err ESTOP"
            if home_all():
                move_xy_abs(0.0, 0.0, 195.0)
                return "ok"
            return "err HOME_NOT_FOUND"

        # === ZERO (go to 0,0) ===
        if up == "ZERO":
            if estop:
                return "err ESTOP"
            # ZERO now works same as HOME - proper homing Y first, then X
            return "ok IN_HOME_POS" if home_all() else "err HOME_NOT_FOUND"

        # === WORK command (go to work position) ===
        if up == "WORK" or up.startswith("WORK "):
            if estop:
                return "err ESTOP"
            if not x_homed or not y_homed:
                return "err NOT_HOMED"
            x, y, f = WORK_X_MM, WORK_Y_MM, WORK_F_MM_MIN
            if len(up) > 4:
                for tok in line.split()[1:]:
                    t = tok.upper()
                    if t.startswith("X"):
                        x = float(tok[1:])
                    elif t.startswith("Y"):
                        y = float(tok[1:])
                    elif t.startswith("F"):
                        f = float(tok[1:])
            move_xy_abs(x, y, f)
            return "ok"

        # === SET commands ===
        if up.startswith("SET "):
            parts = line.split()

            # SET LIM X<val> Y<val>
            if len(parts) >= 2 and parts[1].upper() == "LIM":
                for tok in parts[2:]:
                    t = tok.upper()
                    if t.startswith("X"):
                        X_MAX_MM = float(tok[1:])
                    elif t.startswith("Y"):
                        Y_MAX_MM = float(tok[1:])
                X_MIN_MM = 0.0
                Y_MIN_MM = 0.0
                return "ok"

            # SET STEPS X<val> Y<val>
            if len(parts) >= 2 and parts[1].upper() == "STEPS":
                for tok in parts[2:]:
                    t = tok.upper()
                    if t.startswith("X"):
                        STEPS_PER_MM_X = float(tok[1:])
                    elif t.startswith("Y"):
                        STEPS_PER_MM_Y = float(tok[1:])
                return "ok"

            # SET SPMM (legacy) X<val> Y<val>
            if len(parts) >= 2 and parts[1].upper() == "SPMM":
                if len(parts) == 3 and parts[2][0].upper() not in ("X", "Y"):
                    v = float(parts[2])
                    STEPS_PER_MM_X = v
                    STEPS_PER_MM_Y = v
                else:
                    for tok in parts[2:]:
                        t = tok.upper()
                        if t.startswith("X"):
                            STEPS_PER_MM_X = float(tok[1:])
                        elif t.startswith("Y"):
                            STEPS_PER_MM_Y = float(tok[1:])
                return f"STEPS_PER_MM_X={STEPS_PER_MM_X} STEPS_PER_MM_Y={STEPS_PER_MM_Y}"

            # SET WORK X<val> Y<val> F<val>
            if len(parts) >= 2 and parts[1].upper() == "WORK":
                for tok in parts[2:]:
                    t = tok.upper()
                    if t.startswith("X"):
                        WORK_X_MM = float(tok[1:])
                    elif t.startswith("Y"):
                        WORK_Y_MM = float(tok[1:])
                    elif t.startswith("F"):
                        WORK_F_MM_MIN = float(tok[1:])
                return "ok"

            # SET X0 / SET Y0 / SET XY0
            if len(parts) == 2:
                if parts[1].upper() == "X0":
                    cur_x_mm = 0.0
                    return "ok"
                if parts[1].upper() == "Y0":
                    cur_y_mm = 0.0
                    return "ok"
                if parts[1].upper() == "XY0":
                    cur_x_mm = 0.0
                    cur_y_mm = 0.0
                    return "ok"

            return "err BAD_SET"

        # === DX / DY jog commands ===
        if up.startswith("DX "):
            if estop:
                return "err ESTOP"
            if not x_homed:
                return "err NOT_HOMED_X"
            d, f = 0.0, 600.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("+") or t.startswith("-") or t[0].isdigit():
                    d = float(tok)
                elif t.startswith("F"):
                    f = float(tok[1:])
            target_x = cur_x_mm + d
            x_clamped, _, warnings = check_limits(target_x, None)
            move_axis_abs("X", x_clamped, f)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        if up.startswith("DY "):
            if estop:
                return "err ESTOP"
            if not y_homed:
                return "err NOT_HOMED_Y"
            d, f = 0.0, 600.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("+") or t.startswith("-") or t[0].isdigit():
                    d = float(tok)
                elif t.startswith("F"):
                    f = float(tok[1:])
            target_y = cur_y_mm + d
            _, y_clamped, warnings = check_limits(None, target_y)
            move_axis_abs("Y", y_clamped, f)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        # === Legacy JX/JY jog commands ===
        if up.startswith("JX "):
            if estop:
                return "err ESTOP"
            if not x_homed:
                return "err NOT_HOMED_X"
            toks = line.split()
            if len(toks) < 2:
                return "err BAD_ARGS"
            delta = float(toks[1])
            feed = 300.0
            for t in toks[2:]:
                if t.upper().startswith("F"):
                    feed = float(t[1:])
            target_x = cur_x_mm + delta
            x_clamped, _, warnings = check_limits(target_x, None)
            move_axis_abs("X", x_clamped, feed)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        if up.startswith("JY "):
            if estop:
                return "err ESTOP"
            if not y_homed:
                return "err NOT_HOMED_Y"
            toks = line.split()
            if len(toks) < 2:
                return "err BAD_ARGS"
            delta = float(toks[1])
            feed = 300.0
            for t in toks[2:]:
                if t.upper().startswith("F"):
                    feed = float(t[1:])
            target_y = cur_y_mm + delta
            _, y_clamped, warnings = check_limits(None, target_y)
            move_axis_abs("Y", y_clamped, feed)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        # === GF command (fast move, no PED - same as G for us) ===
        if up.startswith("GF "):
            if estop:
                return "err ESTOP"
            if not x_homed or not y_homed:
                return "err NOT_HOMED"
            x, y, f = None, None, 1200.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("X"):
                    x = float(tok[1:])
                elif t.startswith("Y"):
                    y = float(tok[1:])
                elif t.startswith("F"):
                    f = float(tok[1:])
            if x is None and y is None:
                return "err BAD_ARGS"
            # Check and apply soft limits
            x_clamped, y_clamped, warnings = check_limits(x, y)
            move_xy_abs(x_clamped, y_clamped, f)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        # === G command (guarded move) ===
        if up.startswith("G "):
            if estop:
                return "err ESTOP"
            if not x_homed or not y_homed:
                return "err NOT_HOMED"
            x, y, f = None, None, 1200.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("X"):
                    x = float(tok[1:])
                elif t.startswith("Y"):
                    y = float(tok[1:])
                elif t.startswith("F"):
                    f = float(tok[1:])
            if x is None and y is None:
                return "err BAD_ARGS"
            # Check and apply soft limits
            x_clamped, y_clamped, warnings = check_limits(x, y)
            move_xy_abs(x_clamped, y_clamped, f)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        # === Standalone F command (set feed rate) ===
        if up.startswith("F") and len(up) > 1 and up[1:].replace('.', '').isdigit():
            cur_feed_mm_min = float(line[1:])
            return f"ok FEED={cur_feed_mm_min:.0f}"

        # === G0/G1 move commands ===
        if up.startswith("G0") or up.startswith("G1"):
            if estop:
                return "err ESTOP"
            if not x_homed or not y_homed:
                return "err NOT_HOMED"
            x, y, f = None, None, cur_feed_mm_min
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("X"):
                    x = float(tok[1:])
                elif t.startswith("Y"):
                    y = float(tok[1:])
                elif t.startswith("F"):
                    f = float(tok[1:])
                    cur_feed_mm_min = f  # Update default feed rate
            if x is None and y is None:
                return "err BAD_ARGS"
            # Check and apply soft limits
            x_clamped, y_clamped, warnings = check_limits(x, y)
            move_xy_abs(x_clamped, y_clamped, f)
            if warnings:
                return "ok " + " ".join(warnings)
            return "ok"

        # === G2/G3 arc commands (circular interpolation) ===
        # G2 = clockwise, G3 = counter-clockwise
        # Format: G2 X... Y... I... J... F... P...  (I,J = offset to center, P = passes)
        #     or: G2 X... Y... R... F... P...       (R = radius)
        if up.startswith("G2") or up.startswith("G3"):
            if estop:
                return "err ESTOP"
            if not x_homed or not y_homed:
                return "err NOT_HOMED"
            clockwise = up.startswith("G2")
            x, y, i, j, r, f, p = None, None, None, None, None, cur_feed_mm_min, 1
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("X"):
                    x = float(tok[1:])
                elif t.startswith("Y"):
                    y = float(tok[1:])
                elif t.startswith("I"):
                    i = float(tok[1:])
                elif t.startswith("J"):
                    j = float(tok[1:])
                elif t.startswith("R"):
                    r = float(tok[1:])
                elif t.startswith("F"):
                    f = float(tok[1:])
                    cur_feed_mm_min = f  # Update default feed rate
                elif t.startswith("P"):
                    p = int(float(tok[1:]))  # Number of full circles/passes

            # Default end position to current if not specified
            if x is None:
                x = cur_x_mm
            if y is None:
                y = cur_y_mm

            # Use radius or I/J notation
            if r is not None:
                # Radius notation (P not typically used with R)
                if arc_move_radius(x, y, r, clockwise, f):
                    return "ok"
                return "err ARC_FAILED"
            elif i is not None or j is not None:
                # I/J notation (default to 0 if not specified)
                if i is None:
                    i = 0.0
                if j is None:
                    j = 0.0
                if arc_move(x, y, i, j, clockwise, f, p):
                    return "ok"
                return "err ARC_FAILED"
            else:
                return "err BAD_ARGS (need I/J or R for arc)"

        # === MUSIC command (motor singing) ===
        if up == "MUSIC" or up == "PLAY" or up == "SONG":
            if estop:
                return "err ESTOP"
            play_rock_riff()
            return "ok ROCK!"

        # === HELP ===
        if up == "HELP":
            return get_help_text()

        # === QUIT/EXIT ===
        if up in ("QUIT", "EXIT"):
            return "QUIT"

        return "err UNKNOWN"

    except ValueError as e:
        return f"err INVALID_NUMBER: {e}"
    except Exception as e:
        return f"err EXCEPTION: {e}"


def get_help_text() -> str:
    """Return help text."""
    return """Commands (compatible with Arduino version):
  PING                        - connection test -> PONG
  M114                        - full status
  M119                        - endstop status
  M112                        - E-STOP (emergency stop)
  M999                        - clear E-STOP
  M17 / M18                   - enable / disable drivers

  G28                         - home all (Y then X)
  G28 X / G28 Y               - home single axis
  HOME / HOME X / HOME Y      - same as G28

  CAL                         - home + go to zero
  ZERO                        - go to position 0,0

  F<mm/min>                  - set feed rate (e.g. F50000)

  G X<mm> Y<mm> F<mm/min>     - guarded move
  GF X<mm> Y<mm> F<mm/min>    - fast move
  G0 X<mm> Y<mm> F<mm/min>    - linear move (G-code style)
  G1 X<mm> Y<mm> F<mm/min>    - same as G0

  G2 X<mm> Y<mm> I<mm> J<mm> P<n> F<mm/min>  - CW arc (P=full circles)
  G3 X<mm> Y<mm> I<mm> J<mm> P<n> F<mm/min>  - CCW arc
  G2 X<mm> Y<mm> R<mm> F<mm/min>             - CW arc (R=radius)
  G3 X<mm> Y<mm> R<mm> F<mm/min>             - CCW arc

  DX <+/-mm> F<mm/min>        - jog X axis
  DY <+/-mm> F<mm/min>        - jog Y axis
  JX <+/-mm> F<mm/min>        - jog X (legacy)
  JY <+/-mm> F<mm/min>        - jog Y (legacy)

  WORK                        - go to work position
  WORK X<mm> Y<mm> F<mm/min>  - go to specified work pos

  SET LIM X<mm> Y<mm>         - set travel limits
  SET STEPS X<val> Y<val>     - set steps/mm
  SET SPMM X<val> Y<val>      - set steps/mm (legacy)
  SET WORK X<mm> Y<mm> F<val> - set work position
  SET X0 / SET Y0 / SET XY0   - zero current position

  MUSIC / PLAY / SONG         - play rock riff (motor singing)

  QUIT / EXIT                 - exit program
"""


# =========================
# Main entry points
# =========================
def run_cli_mode() -> None:
    """Run interactive CLI mode."""
    print("xy_cli ready (Pi 5 / lgpio). Type HELP for commands.")
    print(f"Limits: X=0..{X_MAX_MM}mm, Y=0..{Y_MAX_MM}mm")
    print(f"Steps/mm: X={STEPS_PER_MM_X}, Y={STEPS_PER_MM_Y}")
    # Do NOT enable motors automatically — user sends M17 or G28 explicitly
    enable_all(False)

    try:
        while True:
            # Show status
            x_end = "TRIG" if endstop_active(X_MIN_GPIO) else "open"
            y_end = "TRIG" if endstop_active(Y_MIN_GPIO) else "open"
            estop_str = " [ESTOP]" if estop else ""
            print(f"X={cur_x_mm:.3f}mm Y={cur_y_mm:.3f}mm | X_MIN={x_end} Y_MIN={y_end}{estop_str}")

            try:
                line = input("> ").strip()
            except EOFError:
                break

            if not line:
                continue

            response = handle_command(line)
            if response == "QUIT":
                break
            if response:
                print(response)

    except KeyboardInterrupt:
        print("\nInterrupted by user")


def run_serial_mode(port: str, baud: int) -> None:
    """Run serial mode for remote control with background command reading."""
    global cancel_requested, estop, _serial_port, _serial_lock

    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not found. Install with: pip install pyserial")
        sys.exit(1)

    import threading
    import queue

    print(f"Opening serial port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=0.01)  # Short timeout for responsive reading
    except Exception as e:
        print(f"ERROR: Cannot open serial port: {e}")
        sys.exit(1)

    _serial_port = ser
    _serial_lock = threading.Lock()

    # Flush any garbage in serial buffers after power-on
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.1)

    print(f"Serial mode active. Listening on {port}")
    # Do NOT enable motors here — master sends M17 explicitly before homing.
    # This prevents any movement before direction pins are properly set.
    enable_all(False)

    # Send ready message
    ser.write(b"ok READY\n")

    # Command queue for non-emergency commands
    cmd_queue = queue.Queue()
    reader_running = True

    def serial_reader():
        """Background thread to read serial commands and monitor hardware E-STOP."""
        global cancel_requested, estop, x_homed, y_homed
        nonlocal reader_running

        buffer = ""

        while reader_running:
            try:
                # Check hardware E-STOP button
                if estop_gpio_active() and not estop:
                    trigger_hardware_estop()
                    # Clear pending commands from queue
                    while not cmd_queue.empty():
                        try:
                            cmd_queue.get_nowait()
                        except:
                            break
                    print("E-STOP: Command queue cleared")
                    # Notify master about hardware E-STOP
                    with _serial_lock:
                        ser.write(b"!! HARDWARE_ESTOP\n")

                # Auto-clear E-STOP when button is released (but motors stay DISABLED)
                if estop and not estop_gpio_active():
                    estop = False
                    cancel_requested = False
                    # Motors stay DISABLED - require explicit M17 to enable
                    # This prevents unexpected movement after E-STOP
                    print("E-STOP CLEARED: Button released. Motors DISABLED - send M17 to enable, then HOME.")
                    with _serial_lock:
                        ser.write(b"!! ESTOP_CLEARED (motors disabled, M17 then HOME required)\n")

                with _serial_lock:
                    if ser.in_waiting > 0:
                        data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                        buffer += data

                # Process complete lines
                while '\n' in buffer or '\r' in buffer:
                    idx = -1
                    for i, c in enumerate(buffer):
                        if c in '\n\r':
                            idx = i
                            break

                    if idx >= 0:
                        line = buffer[:idx].strip().upper()
                        buffer = buffer[idx+1:].lstrip('\n\r')

                        if line:
                            # Check for emergency commands - handle immediately
                            if line == 'M112' or line == 'CANCEL':
                                print(f"EMERGENCY: {line} received - stopping immediately")
                                cancel_requested = True
                                estop = True
                                x_homed = False  # Invalidate homing
                                y_homed = False
                                enable_all(False)
                                # Clear pending commands from queue
                                while not cmd_queue.empty():
                                    try:
                                        cmd_queue.get_nowait()
                                    except:
                                        break
                                print("E-STOP: Homing invalidated, queue cleared - rehoming required")
                                # Send response immediately
                                with _serial_lock:
                                    ser.write(b"ok ESTOP\n")
                            elif line == 'M999':
                                print("CLEAR: M999 received - clearing cancel/estop")
                                cancel_requested = False
                                estop = False
                                enable_all(True)
                                with _serial_lock:
                                    ser.write(b"ok CLEAR\n")
                            else:
                                # Queue other commands for main thread
                                cmd_queue.put(line)

                time.sleep(0.001)
            except Exception as e:
                print(f"Serial reader error: {e}")
                time.sleep(0.01)

    # Start background reader thread
    reader_thread = threading.Thread(target=serial_reader, daemon=True)
    reader_thread.start()
    print("Background serial reader started")

    try:
        while True:
            # Process commands from queue
            try:
                line = cmd_queue.get(timeout=0.01)

                # Check if E-STOP button is released - auto-clear before processing
                # Motors stay DISABLED - user must send M17 explicitly
                if estop and not estop_gpio_active():
                    estop = False
                    cancel_requested = False
                    # DO NOT enable motors here - they stay disabled for safety
                    print("E-STOP CLEARED: Button released. Motors DISABLED - send M17 to enable, then HOME.")
                    with _serial_lock:
                        ser.write(b"!! ESTOP_CLEARED (motors disabled, M17 then HOME required)\n")

                # Skip if we're in cancel/estop state (button still pressed)
                if cancel_requested or estop:
                    with _serial_lock:
                        ser.write(b"err ESTOP\n")
                    continue

                response = handle_command(line)
                if response == "QUIT":
                    with _serial_lock:
                        ser.write(b"ok BYE\n")
                    return
                if response:
                    with _serial_lock:
                        ser.write((response + "\n").encode('utf-8'))

            except queue.Empty:
                pass

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nSerial mode interrupted")
    finally:
        reader_running = False
        reader_thread.join(timeout=1.0)
        ser.close()
        print("Serial port closed")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="X/Y coordinate table controller for Raspberry Pi 5"
    )
    parser.add_argument(
        "--serial", "-s",
        metavar="PORT",
        help="Run in serial mode on specified port (e.g., /dev/ttyUSB0)"
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200)"
    )
    args = parser.parse_args()

    # Initialize GPIO
    init_gpio()

    try:
        if args.serial:
            run_serial_mode(args.serial, args.baud)
        else:
            run_cli_mode()
    finally:
        enable_all(False)
        if io is not None:
            io.close()
        print("GPIO released. Goodbye.")


if __name__ == "__main__":
    main()
