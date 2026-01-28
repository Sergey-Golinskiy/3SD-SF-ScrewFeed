# 3SD-SF-ScrewFeed

CLI tool for controlling X/Y coordinate table on Raspberry Pi 5 — part of the automated screwdriver project.

## Description

`xy_cli.py` is a command-line tool for controlling two axes (X and Y) via GPIO on Raspberry Pi using the `lgpio` library. The script accepts simple commands for homing, movement, and jogging, and also allows changing steps/mm and zeroing the current position.

Core functionality:
- Direct STEP pulse generation for X/Y axis drivers
- Endstop checking (MIN endstops)
- Interleaved dual-axis motion
- Configurable steps per mm

## Requirements

- **Raspberry Pi 5** with GPIO access
- **Python 3.10+**
- **lgpio** library

### Installation

```bash
# Install lgpio
pip install lgpio

# Clone repository
git clone <repository-url>
cd 3SD-SF-ScrewFeed
```

## GPIO Pinout

| Function    | GPIO (BCM) |
|-------------|------------|
| X_STEP      | 9          |
| X_DIR       | 10         |
| X_ENA       | 11         |
| X_MIN (endstop) | 2      |
| Y_STEP      | 21         |
| Y_DIR       | 7          |
| Y_ENA       | 8          |
| Y_MIN (endstop) | 3      |

### Signal Logic

| Signal      | Active Level |
|-------------|--------------|
| Endstop     | LOW (NPN sensor) |
| ENA (enable)| HIGH         |
| STEP pulse  | LOW (common-anode) |

## How It Works

### 1. STEP Pulse Generation
The `step_pulses` function generates blocking STEP pulses with configurable frequency (`MAX_STEP_HZ`) and pulse duration (`PULSE_US`). Movement stops automatically when endstop is triggered.

### 2. Single Axis Movement
`move_axis_abs` moves one axis to an absolute coordinate (mm). Steps per mm multiplied by delta gives the step count. When moving toward MIN, endstop is checked and position resets to 0.0 mm on trigger.

### 3. Dual Axis Movement
`move_xy_abs` performs time-interleaved stepping for X and Y axes. This simple scheduler (not Bresenham) allows both axes to move approximately simultaneously at the same mm/s speed.

### 4. Homing
`home_axis` performs:
1. Back off from endstop (if already triggered)
2. Fast approach until endstop triggers
3. Back off by `backoff_mm` (15mm default)
4. Slow approach for precise positioning
5. Set axis coordinate to 0.0 mm

**Safety**: Homing has a 60-second timeout to prevent infinite loops if mechanics jam.

## Configuration

Key constants in `xy_cli.py`:

```python
# Steps per mm for each axis
STEPS_PER_MM_X = 10.0
STEPS_PER_MM_Y = 10.0

# Travel limits (mm)
X_MIN_MM = 0.0
X_MAX_MM = 165.0
Y_MIN_MM = 0.0
Y_MAX_MM = 350.0

# Motion parameters
MAX_STEP_HZ = 30000   # Maximum step frequency
PULSE_US = 10         # Step pulse duration (microseconds)

# Homing timeout (seconds)
HOMING_TIMEOUT_S = 60.0
```

## Usage

```bash
python3 xy_cli.py
```

After startup, a prompt appears showing current position and endstop status. Drivers are enabled automatically.

### CLI Commands

| Command | Description |
|---------|-------------|
| `HELP` | Show help text |
| `M114` | Show current status/coordinates |
| `M17` | Enable both drivers |
| `M18` | Disable both drivers |
| `HOME` | Home Y then X |
| `HOME X` | Home X axis only |
| `HOME Y` | Home Y axis only |
| `G0 X<mm> Y<mm> F<mm/min>` | Absolute move (X or Y optional) |
| `G1 X<mm> Y<mm> F<mm/min>` | Same as G0 |
| `JX <+/-mm> F<mm/min>` | Jog X axis |
| `JY <+/-mm> F<mm/min>` | Jog Y axis |
| `SET SPMM <val>` | Set steps/mm for both axes |
| `SET SPMM X<val> Y<val>` | Set steps/mm separately |
| `SET X0` | Zero X position |
| `SET Y0` | Zero Y position |
| `SET XY0` | Zero both positions |
| `QUIT` / `EXIT` | Exit program |

### Examples

```text
> HOME
ok HOME
> G0 X50 Y200 F12000
ok
> JX -5 F1200
ok
> SET SPMM X10 Y10
STEPS_PER_MM_X=10.0 STEPS_PER_MM_Y=10.0
> M114
X=45.000mm Y=200.000mm | X_MIN=open Y_MIN=open
```

## Error Messages

| Error | Description |
|-------|-------------|
| `err UNKNOWN` | Unknown command |
| `err BAD_ARGS` | Missing or invalid arguments |
| `err BAD_SET` | Invalid SET command format |
| `err INVALID_NUMBER` | Cannot parse number |
| `err HOME_FAILED` | Homing timeout |
| `HIT X_MIN` / `HIT Y_MIN` | Endstop triggered during move |

## Safety Notes

- The script directly controls stepper drivers via GPIO. Verify all connections and signal logic before running.
- Software limits (`X_MAX_MM`, `Y_MAX_MM`) restrict coordinates, but **mechanical endstops must still be connected** as hardware safety.
- Homing timeout (60s) prevents infinite movement if endstop fails.
- Always run `HOME` after power-up to establish known position.
- Press `Ctrl+C` to safely stop and disable drivers.

## Project Structure

```
3SD-SF-ScrewFeed/
├── README.md       # This file
└── xy_cli.py       # Main CLI tool
```

## License

Part of 3SD-SF-ScrewFeed automated screwdriver project.
