#!/usr/bin/env python3
import time
import math
import lgpio

# =========================
# GPIO mapping (BCM)
# =========================
X_MIN_GPIO = 2   # endstop X (GPIO2)
Y_MIN_GPIO = 3   # endstop Y (GPIO3)

# X axis driver
X_STEP_GPIO = 9
X_DIR_GPIO  = 10
X_ENA_GPIO  = 11

# Y axis driver (как ты дал)
Y_STEP_GPIO = 21
Y_DIR_GPIO  = 7
Y_ENA_GPIO  = 8

# =========================
# Logic levels
# =========================
ENDSTOP_ACTIVE_LOW = True   # NPN sensor: triggered -> LOW
ENA_ACTIVE_HIGH    = True   # у тебя: ENA=1 means enabled

STEP_IDLE_LEVEL = 1
STEP_PULSE_ACTIVE_LOW = True  # common-anode wiring: pulse on "-" line is usually active-low

# =========================
# Motion config
# =========================
STEPS_PER_MM_X = 10.0
STEPS_PER_MM_Y = 10.0

X_MIN_MM = 0.0
X_MAX_MM = 165.0
Y_MIN_MM = 0.0
Y_MAX_MM = 350.0

MAX_STEP_HZ  = 30000
PULSE_US     = 10

cur_x_mm = 0.0
cur_y_mm = 0.0


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def busy_wait_ns(t_ns: int):
    while time.perf_counter_ns() < t_ns:
        pass


class GPIO:
    def __init__(self):
        self.h = lgpio.gpiochip_open(0)

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

    def close(self):
        lgpio.gpiochip_close(self.h)

    def read(self, gpio: int) -> int:
        return lgpio.gpio_read(self.h, gpio)

    def write(self, gpio: int, val: int):
        lgpio.gpio_write(self.h, gpio, val)


io = GPIO()


def endstop_active(gpio: int) -> bool:
    v = io.read(gpio)
    return (v == 0) if ENDSTOP_ACTIVE_LOW else (v == 1)


def enable_driver_x(en: bool):
    if ENA_ACTIVE_HIGH:
        io.write(X_ENA_GPIO, 1 if en else 0)
    else:
        io.write(X_ENA_GPIO, 0 if en else 1)


def enable_driver_y(en: bool):
    if ENA_ACTIVE_HIGH:
        io.write(Y_ENA_GPIO, 1 if en else 0)
    else:
        io.write(Y_ENA_GPIO, 0 if en else 1)


def enable_all(en: bool):
    enable_driver_x(en)
    enable_driver_y(en)


def set_dir_x(positive: bool):
    io.write(X_DIR_GPIO, 1 if positive else 0)


def set_dir_y(positive: bool):
    io.write(Y_DIR_GPIO, 1 if positive else 0)


def step_pulses(step_gpio: int, steps: int, step_hz: float, stop_on_endstop_gpio: int | None = None) -> bool:
    """
    Generates STEP pulses for one axis (blocking).
    stop_on_endstop_gpio: if provided, stops when that endstop becomes active.
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


def move_axis_abs(axis: str, target_mm: float, feed_mm_min: float):
    global cur_x_mm, cur_y_mm, STEPS_PER_MM_X, STEPS_PER_MM_Y

    if axis == "X":
        target_mm = float(clamp(target_mm, X_MIN_MM, X_MAX_MM))
        delta = target_mm - cur_x_mm
        if abs(delta) < 1e-6:
            return
        positive = delta > 0
        set_dir_x(positive)

        steps = int(round(abs(delta) * STEPS_PER_MM_X))
        if steps <= 0:
            cur_x_mm = target_mm
            return

        mm_per_s = max(feed_mm_min / 60.0, 0.1)
        step_hz = mm_per_s * STEPS_PER_MM_X

        enable_driver_x(True)
        stop_gpio = X_MIN_GPIO if not positive else None
        ok = step_pulses(X_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio)

        if ok:
            cur_x_mm = target_mm
        else:
            cur_x_mm = 0.0
            print("HIT X_MIN -> X set to 0.0")
        return

    if axis == "Y":
        target_mm = float(clamp(target_mm, Y_MIN_MM, Y_MAX_MM))
        delta = target_mm - cur_y_mm
        if abs(delta) < 1e-6:
            return
        positive = delta > 0
        set_dir_y(positive)

        steps = int(round(abs(delta) * STEPS_PER_MM_Y))
        if steps <= 0:
            cur_y_mm = target_mm
            return

        mm_per_s = max(feed_mm_min / 60.0, 0.1)
        step_hz = mm_per_s * STEPS_PER_MM_Y

        enable_driver_y(True)
        stop_gpio = Y_MIN_GPIO if not positive else None
        ok = step_pulses(Y_STEP_GPIO, steps, step_hz, stop_on_endstop_gpio=stop_gpio)

        if ok:
            cur_y_mm = target_mm
        else:
            cur_y_mm = 0.0
            print("HIT Y_MIN -> Y set to 0.0")
        return

    raise ValueError("Axis must be X or Y")


def move_xy_abs(x_mm: float | None, y_mm: float | None, feed_mm_min: float):
    """
    Simple "both axes" move.
    To keep it simple and robust (without threads), we do interleaved stepping by time.
    This is NOT perfect synchronized planner, but works well for CNC-ish moves.

    If you prefer, we can later implement true Bresenham.
    """
    global cur_x_mm, cur_y_mm

    # Compute deltas and setup directions/targets
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

    if sx == 0 and sy == 0:
        return

    set_dir_x(dx >= 0)
    set_dir_y(dy >= 0)

    enable_all(True)

    # Convert feed to step rates (same mm/s for both)
    mm_per_s = max(feed_mm_min / 60.0, 0.1)
    step_hz_x = min(mm_per_s * STEPS_PER_MM_X, MAX_STEP_HZ)
    step_hz_y = min(mm_per_s * STEPS_PER_MM_Y, MAX_STEP_HZ)

    # Determine time periods
    period_x_ns = int(1e9 / step_hz_x) if sx > 0 else None
    period_y_ns = int(1e9 / step_hz_y) if sy > 0 else None
    hi_ns = int(PULSE_US * 1000)

    # Ensure period > hi
    if period_x_ns is not None and hi_ns * 2 >= period_x_ns:
        period_x_ns = hi_ns * 3
    if period_y_ns is not None and hi_ns * 2 >= period_y_ns:
        period_y_ns = hi_ns * 3

    # Stop conditions if moving toward MIN
    stop_x = (dx < 0)
    stop_y = (dy < 0)

    # Interleaved scheduler
    now = time.perf_counter_ns()
    next_x = now
    next_y = now

    done_x = 0
    done_y = 0

    # Pre-set idle HIGH
    io.write(X_STEP_GPIO, STEP_IDLE_LEVEL)
    io.write(Y_STEP_GPIO, STEP_IDLE_LEVEL)

    while done_x < sx or done_y < sy:
        t = time.perf_counter_ns()

        # X pulse
        if sx > 0 and done_x < sx and period_x_ns is not None and t >= next_x:
            if stop_x and endstop_active(X_MIN_GPIO):
                cur_x_mm = 0.0
                sx = done_x  # stop further X
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
                sy = done_y  # stop further Y
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

    # Update positions if completed
    cur_x_mm = x_mm if done_x == int(round(abs(dx) * STEPS_PER_MM_X)) else cur_x_mm
    cur_y_mm = y_mm if done_y == int(round(abs(dy) * STEPS_PER_MM_Y)) else cur_y_mm


def home_axis(axis: str, fast_mm_min: float = 18000.0, slow_mm_min: float = 600.0, backoff_mm: float = 3.0):
    global cur_x_mm, cur_y_mm

    if axis == "X":
        enable_driver_x(True)

        # if already on endstop -> backoff
        if endstop_active(X_MIN_GPIO):
            set_dir_x(True)
            step_pulses(X_STEP_GPIO, int(backoff_mm * STEPS_PER_MM_X), (slow_mm_min/60.0)*STEPS_PER_MM_X, None)
            time.sleep(0.05)

        # fast approach
        set_dir_x(False)
        fast_hz = (max(fast_mm_min, 60.0)/60.0) * STEPS_PER_MM_X
        chunk = int(STEPS_PER_MM_X * 10)
        while True:
            ok = step_pulses(X_STEP_GPIO, chunk, fast_hz, stop_on_endstop_gpio=X_MIN_GPIO)
            if not ok:
                break

        # backoff
        set_dir_x(True)
        slow_hz = (max(slow_mm_min, 60.0)/60.0) * STEPS_PER_MM_X
        step_pulses(X_STEP_GPIO, int(backoff_mm * STEPS_PER_MM_X), slow_hz, None)
        time.sleep(0.05)

        # slow touch
        set_dir_x(False)
        while not endstop_active(X_MIN_GPIO):
            step_pulses(X_STEP_GPIO, int(STEPS_PER_MM_X * 0.5), slow_hz, stop_on_endstop_gpio=X_MIN_GPIO)

        cur_x_mm = 0.0
        return

    if axis == "Y":
        enable_driver_y(True)

        if endstop_active(Y_MIN_GPIO):
            set_dir_y(True)
            step_pulses(Y_STEP_GPIO, int(backoff_mm * STEPS_PER_MM_Y), (slow_mm_min/60.0)*STEPS_PER_MM_Y, None)
            time.sleep(0.05)

        set_dir_y(False)
        fast_hz = (max(fast_mm_min, 60.0)/60.0) * STEPS_PER_MM_Y
        chunk = int(STEPS_PER_MM_Y * 10)
        while True:
            ok = step_pulses(Y_STEP_GPIO, chunk, fast_hz, stop_on_endstop_gpio=Y_MIN_GPIO)
            if not ok:
                break

        set_dir_y(True)
        slow_hz = (max(slow_mm_min, 60.0)/60.0) * STEPS_PER_MM_Y
        step_pulses(Y_STEP_GPIO, int(backoff_mm * STEPS_PER_MM_Y), slow_hz, None)
        time.sleep(0.05)

        set_dir_y(False)
        while not endstop_active(Y_MIN_GPIO):
            step_pulses(Y_STEP_GPIO, int(STEPS_PER_MM_Y * 0.5), slow_hz, stop_on_endstop_gpio=Y_MIN_GPIO)

        cur_y_mm = 0.0
        return

    raise ValueError("Axis must be X or Y")


def status():
    print(
        f"X={cur_x_mm:.3f}mm Y={cur_y_mm:.3f}mm | "
        f"X_MIN={'TRIG' if endstop_active(X_MIN_GPIO) else 'open'} "
        f"Y_MIN={'TRIG' if endstop_active(Y_MIN_GPIO) else 'open'}"
    )


def help_text():
    print("""Commands:
  HELP
  M114                      - status
  M17 / M18                 - enable / disable BOTH drivers
  HOME                      - home Y then X
  HOME X / HOME Y           - home one axis
  G0 X<mm> Y<mm> F<mm/min>  - move (example: G0 X50 Y200 F12000)
  JX <+/-mm> F<mm/min>      - jog X (example: JX -5 F1200)
  JY <+/-mm> F<mm/min>      - jog Y (example: JY +10 F6000)
  SET SPMM <val>            - set steps/mm for both
  SET SPMM X<val> Y<val>    - set separately (example: SET SPMM X10 Y10)
  SET X0 / SET Y0 / SET XY0 - set current pos
  QUIT
""")


def main():
    global STEPS_PER_MM_X, STEPS_PER_MM_Y, cur_x_mm, cur_y_mm

    print("xy_cli ready (Pi 5 / lgpio). Type HELP.")
    enable_all(True)

    try:
        while True:
            status()
            line = input("> ").strip()
            if not line:
                continue

            up = line.upper()

            if up == "HELP":
                help_text()
                continue
            if up in ("QUIT", "EXIT"):
                break

            if up == "M114":
                status()
                continue

            if up == "M17":
                enable_all(True)
                print("Drivers ENABLED")
                continue
            if up == "M18":
                enable_all(False)
                print("Drivers DISABLED")
                continue

            if up == "HOME":
                home_axis("Y")
                home_axis("X")
                print("ok HOME")
                continue
            if up == "HOME X":
                home_axis("X")
                print("ok HOME X")
                continue
            if up == "HOME Y":
                home_axis("Y")
                print("ok HOME Y")
                continue

            if up.startswith("SET "):
                parts = line.split()
                if len(parts) >= 3 and parts[1].upper() == "SPMM":
                    # SET SPMM 10   OR  SET SPMM X10 Y10
                    if len(parts) == 3 and (parts[2][0].upper() not in ("X", "Y")):
                        v = float(parts[2])
                        STEPS_PER_MM_X = v
                        STEPS_PER_MM_Y = v
                        print(f"STEPS_PER_MM_X={STEPS_PER_MM_X} STEPS_PER_MM_Y={STEPS_PER_MM_Y}")
                    else:
                        for tok in parts[2:]:
                            t = tok.upper()
                            if t.startswith("X"):
                                STEPS_PER_MM_X = float(tok[1:])
                            elif t.startswith("Y"):
                                STEPS_PER_MM_Y = float(tok[1:])
                        print(f"STEPS_PER_MM_X={STEPS_PER_MM_X} STEPS_PER_MM_Y={STEPS_PER_MM_Y}")
                    continue

                if len(parts) == 2 and parts[1].upper() == "X0":
                    cur_x_mm = 0.0
                    print("X set to 0.0")
                    continue
                if len(parts) == 2 and parts[1].upper() == "Y0":
                    cur_y_mm = 0.0
                    print("Y set to 0.0")
                    continue
                if len(parts) == 2 and parts[1].upper() == "XY0":
                    cur_x_mm = 0.0
                    cur_y_mm = 0.0
                    print("X,Y set to 0.0")
                    continue

                print("err BAD_SET")
                continue

            # Jog X: JX -5 F1200
            if up.startswith("JX "):
                toks = line.split()
                if len(toks) < 2:
                    print("err BAD_ARGS")
                    continue
                delta = float(toks[1])
                feed = 300.0
                for t in toks[2:]:
                    if t.upper().startswith("F"):
                        feed = float(t[1:])
                move_axis_abs("X", cur_x_mm + delta, feed)
                print("ok")
                continue

            # Jog Y: JY +10 F6000
            if up.startswith("JY "):
                toks = line.split()
                if len(toks) < 2:
                    print("err BAD_ARGS")
                    continue
                delta = float(toks[1])
                feed = 300.0
                for t in toks[2:]:
                    if t.upper().startswith("F"):
                        feed = float(t[1:])
                move_axis_abs("Y", cur_y_mm + delta, feed)
                print("ok")
                continue

            # Move: G0 X50 Y200 F12000 (X or Y can be omitted)
            if up.startswith("G0") or up.startswith("G1"):
                x = None
                y = None
                f = 300.0
                for tok in line.split()[1:]:
                    tt = tok.upper()
                    if tt.startswith("X"):
                        x = float(tok[1:])
                    elif tt.startswith("Y"):
                        y = float(tok[1:])
                    elif tt.startswith("F"):
                        f = float(tok[1:])
                if x is None and y is None:
                    print("err BAD_ARGS")
                    continue
                move_xy_abs(x, y, f)
                print("ok")
                continue

            print("err UNKNOWN")

    finally:
        enable_all(False)
        io.close()


if __name__ == "__main__":
    main()
