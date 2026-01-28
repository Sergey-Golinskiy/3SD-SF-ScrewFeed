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
import time
import argparse
from typing import Optional

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
X_MAX_MM = 165.0
Y_MIN_MM = 0.0
Y_MAX_MM = 350.0

MAX_FEED_MM_S = 600.0   # Max feed rate mm/s
MAX_STEP_HZ   = 30000
PULSE_US      = 10

# Homing parameters
SCAN_RANGE_X_MM = 170.0
SCAN_RANGE_Y_MM = 355.0
BACKOFF_MM      = 5.0
SLOW_MM_S       = 2.0
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
estop = False  # Emergency stop flag

# Lazy GPIO initialization
io: Optional["GPIO"] = None


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

        # outputs X
        lgpio.gpio_claim_output(self.h, X_STEP_GPIO, STEP_IDLE_LEVEL)
        lgpio.gpio_claim_output(self.h, X_DIR_GPIO, 0)
        lgpio.gpio_claim_output(self.h, X_ENA_GPIO, 0)

        # outputs Y
        lgpio.gpio_claim_output(self.h, Y_STEP_GPIO, STEP_IDLE_LEVEL)
        lgpio.gpio_claim_output(self.h, Y_DIR_GPIO, 0)
        lgpio.gpio_claim_output(self.h, Y_ENA_GPIO, 0)

        # inputs with pull-ups
        pull_up = getattr(lgpio, "SET_PULL_UP", 0)
        lgpio.gpio_claim_input(self.h, X_MIN_GPIO, pull_up)
        lgpio.gpio_claim_input(self.h, Y_MIN_GPIO, pull_up)

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
    """Check if endstop is triggered."""
    v = io.read(gpio)
    return (v == 0) if ENDSTOP_ACTIVE_LOW else (v == 1)


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
                stop_on_endstop_gpio: Optional[int] = None) -> bool:
    """
    Generates STEP pulses for one axis (blocking).
    Returns True if finished normally, False if stopped by endstop.
    """
    if steps <= 0:
        return True

    if step_hz <= 0:
        step_hz = 1.0
    step_hz = min(step_hz, MAX_STEP_HZ)

    period_ns = int(1e9 / step_hz)
    hi_ns = int(PULSE_US * 1000)

    if hi_ns * 2 >= period_ns:
        period_ns = hi_ns * 3

    t = time.perf_counter_ns()

    for _ in range(steps):
        if stop_on_endstop_gpio is not None and endstop_active(stop_on_endstop_gpio):
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


# =========================
# Motion functions
# =========================
def move_axis_abs(axis: str, target_mm: float, feed_mm_min: float) -> bool:
    """Move single axis to absolute position. Returns True if successful."""
    global cur_x_mm, cur_y_mm

    if estop:
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
        ok = step_pulses(X_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio)

        if ok:
            cur_x_mm = target_mm
        else:
            cur_x_mm = 0.0
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
        ok = step_pulses(Y_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio)

        if ok:
            cur_y_mm = target_mm
        else:
            cur_y_mm = 0.0
        return ok

    raise ValueError("Axis must be X or Y")


def move_xy_abs(x_mm: Optional[float], y_mm: Optional[float], feed_mm_min: float) -> bool:
    """
    Move both axes to absolute position using interleaved stepping.
    Returns True if move completed successfully.
    """
    global cur_x_mm, cur_y_mm

    if estop:
        return False

    if x_mm is None:
        x_mm = cur_x_mm
    if y_mm is None:
        y_mm = cur_y_mm

    x_mm = float(clamp(x_mm, X_MIN_MM, X_MAX_MM))
    y_mm = float(clamp(y_mm, Y_MIN_MM, Y_MAX_MM))

    dx = x_mm - cur_x_mm
    dy = y_mm - cur_y_mm

    sx = int(round(abs(dx) * STEPS_PER_MM_X))
    sy = int(round(abs(dy) * STEPS_PER_MM_Y))
    sx_orig = sx
    sy_orig = sy

    if sx == 0 and sy == 0:
        return True

    set_dir_x(dx >= 0)
    set_dir_y(dy >= 0)

    enable_all(True)

    mm_per_s = min(max(feed_mm_min / 60.0, 0.1), MAX_FEED_MM_S)
    step_hz_x = min(mm_per_s * STEPS_PER_MM_X, MAX_STEP_HZ)
    step_hz_y = min(mm_per_s * STEPS_PER_MM_Y, MAX_STEP_HZ)

    period_x_ns = int(1e9 / step_hz_x) if sx > 0 else None
    period_y_ns = int(1e9 / step_hz_y) if sy > 0 else None
    hi_ns = int(PULSE_US * 1000)

    if period_x_ns is not None and hi_ns * 2 >= period_x_ns:
        period_x_ns = hi_ns * 3
    if period_y_ns is not None and hi_ns * 2 >= period_y_ns:
        period_y_ns = hi_ns * 3

    stop_x = (dx < 0)
    stop_y = (dy < 0)

    now = time.perf_counter_ns()
    next_x = now
    next_y = now

    done_x = 0
    done_y = 0

    io.write(X_STEP_GPIO, STEP_IDLE_LEVEL)
    io.write(Y_STEP_GPIO, STEP_IDLE_LEVEL)

    hit_x = False
    hit_y = False

    while done_x < sx or done_y < sy:
        t = time.perf_counter_ns()

        # X pulse
        if sx > 0 and done_x < sx and period_x_ns is not None and t >= next_x:
            if stop_x and endstop_active(X_MIN_GPIO):
                cur_x_mm = 0.0
                sx = done_x
                hit_x = True
            else:
                if STEP_PULSE_ACTIVE_LOW:
                    io.write(X_STEP_GPIO, 0)
                    busy_wait_ns(t + hi_ns)
                    io.write(X_STEP_GPIO, 1)
                else:
                    io.write(X_STEP_GPIO, 1)
                    busy_wait_ns(t + hi_ns)
                    io.write(X_STEP_GPIO, 0)
                done_x += 1
            next_x += period_x_ns

        # Y pulse
        t2 = time.perf_counter_ns()
        if sy > 0 and done_y < sy and period_y_ns is not None and t2 >= next_y:
            if stop_y and endstop_active(Y_MIN_GPIO):
                cur_y_mm = 0.0
                sy = done_y
                hit_y = True
            else:
                if STEP_PULSE_ACTIVE_LOW:
                    io.write(Y_STEP_GPIO, 0)
                    busy_wait_ns(t2 + hi_ns)
                    io.write(Y_STEP_GPIO, 1)
                else:
                    io.write(Y_STEP_GPIO, 1)
                    busy_wait_ns(t2 + hi_ns)
                    io.write(Y_STEP_GPIO, 0)
                done_y += 1
            next_y += period_y_ns

    if done_x == sx_orig and not hit_x:
        cur_x_mm = x_mm
    if done_y == sy_orig and not hit_y:
        cur_y_mm = y_mm

    return not (hit_x or hit_y)


# =========================
# Homing functions
# =========================
def home_axis(axis: str) -> bool:
    """
    Home specified axis to MIN endstop.
    Returns True if homing successful, False if timeout or failure.
    """
    global cur_x_mm, cur_y_mm

    if estop:
        return False

    start_time = time.time()

    def check_timeout() -> bool:
        return time.time() - start_time > HOMING_TIMEOUT_S

    if axis == "X":
        enable_driver_x(True)

        fast_hz = min(300.0 * STEPS_PER_MM_X, MAX_STEP_HZ)  # 300 mm/s for fast approach
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
            step_pulses(X_STEP_GPIO, int(STEPS_PER_MM_X * 0.5), slow_hz, stop_on_endstop_gpio=X_MIN_GPIO)

        cur_x_mm = 0.0
        return True

    if axis == "Y":
        enable_driver_y(True)

        fast_hz = min(300.0 * STEPS_PER_MM_Y, MAX_STEP_HZ)  # 300 mm/s for fast approach
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
            step_pulses(Y_STEP_GPIO, int(STEPS_PER_MM_Y * 0.5), slow_hz, stop_on_endstop_gpio=Y_MIN_GPIO)

        cur_y_mm = 0.0
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
    return f"STATUS X:{cur_x_mm:.3f} Y:{cur_y_mm:.3f} X_MIN:{x_end} Y_MIN:{y_end} ESTOP:{'1' if estop else '0'}"


def get_endstop_str() -> str:
    """Get endstop status string (M119 format)."""
    x_end = "TRIGGERED" if endstop_active(X_MIN_GPIO) else "open"
    y_end = "TRIGGERED" if endstop_active(Y_MIN_GPIO) else "open"
    return f"X_MIN:{x_end} Y_MIN:{y_end}"


# =========================
# Command handler
# =========================
def handle_command(line: str) -> str:
    """
    Handle a single command line and return response.
    Compatible with Arduino version command set.
    """
    global cur_x_mm, cur_y_mm, estop
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

        # === Status commands ===
        if up == "M114":
            return get_status_str() + "\nok"

        if up == "M119":
            return get_endstop_str() + "\nok"

        # === E-STOP commands ===
        if up == "M112":
            estop = True
            enable_all(False)
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
            move_xy_abs(0.0, 0.0, 195.0)
            return "ok"

        # === WORK command (go to work position) ===
        if up == "WORK" or up.startswith("WORK "):
            if estop:
                return "err ESTOP"
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
            d, f = 0.0, 600.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("+") or t.startswith("-") or t[0].isdigit():
                    d = float(tok)
                elif t.startswith("F"):
                    f = float(tok[1:])
            move_axis_abs("X", cur_x_mm + d, f)
            return "ok"

        if up.startswith("DY "):
            if estop:
                return "err ESTOP"
            d, f = 0.0, 600.0
            for tok in line.split()[1:]:
                t = tok.upper()
                if t.startswith("+") or t.startswith("-") or t[0].isdigit():
                    d = float(tok)
                elif t.startswith("F"):
                    f = float(tok[1:])
            move_axis_abs("Y", cur_y_mm + d, f)
            return "ok"

        # === Legacy JX/JY jog commands ===
        if up.startswith("JX "):
            if estop:
                return "err ESTOP"
            toks = line.split()
            if len(toks) < 2:
                return "err BAD_ARGS"
            delta = float(toks[1])
            feed = 300.0
            for t in toks[2:]:
                if t.upper().startswith("F"):
                    feed = float(t[1:])
            move_axis_abs("X", cur_x_mm + delta, feed)
            return "ok"

        if up.startswith("JY "):
            if estop:
                return "err ESTOP"
            toks = line.split()
            if len(toks) < 2:
                return "err BAD_ARGS"
            delta = float(toks[1])
            feed = 300.0
            for t in toks[2:]:
                if t.upper().startswith("F"):
                    feed = float(t[1:])
            move_axis_abs("Y", cur_y_mm + delta, feed)
            return "ok"

        # === GF command (fast move, no PED - same as G for us) ===
        if up.startswith("GF "):
            if estop:
                return "err ESTOP"
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
            move_xy_abs(x, y, f)
            return "ok"

        # === G command (guarded move) ===
        if up.startswith("G "):
            if estop:
                return "err ESTOP"
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
            move_xy_abs(x, y, f)
            return "ok"

        # === G0/G1 move commands ===
        if up.startswith("G0") or up.startswith("G1"):
            if estop:
                return "err ESTOP"
            x, y, f = None, None, 300.0
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
            move_xy_abs(x, y, f)
            return "ok"

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

  G X<mm> Y<mm> F<mm/min>     - guarded move
  GF X<mm> Y<mm> F<mm/min>    - fast move
  G0 X<mm> Y<mm> F<mm/min>    - move (G-code style)

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
    enable_all(True)

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
    """Run serial mode for remote control."""
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not found. Install with: pip install pyserial")
        sys.exit(1)

    print(f"Opening serial port {port} at {baud} baud...")
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:
        print(f"ERROR: Cannot open serial port: {e}")
        sys.exit(1)

    print(f"Serial mode active. Listening on {port}")
    enable_all(True)

    # Send ready message
    ser.write(b"ok READY\n")

    buffer = ""

    try:
        while True:
            # Read available data
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                buffer += data

                # Process complete lines
                while '\n' in buffer or '\r' in buffer:
                    # Find line ending
                    idx = -1
                    for i, c in enumerate(buffer):
                        if c in '\n\r':
                            idx = i
                            break

                    if idx >= 0:
                        line = buffer[:idx].strip()
                        buffer = buffer[idx+1:].lstrip('\n\r')

                        if line:
                            response = handle_command(line)
                            if response == "QUIT":
                                ser.write(b"ok BYE\n")
                                return
                            if response:
                                ser.write((response + "\n").encode('utf-8'))

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nSerial mode interrupted")
    finally:
        ser.close()


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
