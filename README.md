# 3SD-SF-ScrewFeed

CLI/Serial tool for controlling X/Y coordinate table on Raspberry Pi 5 — part of the automated screwdriver project.

Migrated from Arduino/RAMPS version (`old_main.ino`) with full command compatibility.

## Features

- **Dual operation modes**: Interactive CLI or Serial (for remote control from another RPi)
- **Full G-code compatibility**: G0, G1, G28 commands
- **E-STOP support**: Emergency stop with M112/M999
- **Configurable parameters**: Steps/mm, travel limits, work position
- **Endstop protection**: Automatic stop on MIN endstop trigger

## Requirements

- **Raspberry Pi 5** with GPIO access
- **Python 3.10+**
- **lgpio** library (required)
- **pyserial** library (optional, for serial mode)

### Installation

```bash
# Install required libraries
pip install lgpio

# For serial mode (optional)
pip install pyserial

# Clone repository
git clone <repository-url>
cd 3SD-SF-ScrewFeed
```

## GPIO Pinout

| Function        | GPIO (BCM) |
|-----------------|------------|
| X_STEP          | 9          |
| X_DIR           | 10         |
| X_ENA           | 11         |
| X_MIN (endstop) | 2          |
| Y_STEP          | 21         |
| Y_DIR           | 7          |
| Y_ENA           | 8          |
| Y_MIN (endstop) | 3          |

### Signal Logic

| Signal      | Active Level         |
|-------------|----------------------|
| Endstop     | LOW (NPN sensor)     |
| ENA (enable)| HIGH                 |
| STEP pulse  | LOW (common-anode)   |

## Usage

### Interactive CLI Mode

```bash
python3 xy_cli.py
```

### Serial Mode (for remote control)

```bash
# Via USB serial adapter
python3 xy_cli.py --serial /dev/ttyUSB0

# Via GPIO UART
python3 xy_cli.py --serial /dev/ttyAMA0 --baud 115200
```

## Command Reference

### Connection & Status

| Command | Response | Description |
|---------|----------|-------------|
| `PING` | `PONG` | Connection test |
| `M114` | `STATUS X:... Y:... X_MIN:... Y_MIN:... ESTOP:...` | Full status |
| `M119` | `X_MIN:... Y_MIN:...` | Endstop status only |

### Emergency Stop

| Command | Response | Description |
|---------|----------|-------------|
| `M112` | `ok ESTOP` | Activate E-STOP, disable drivers |
| `M999` | `ok CLEAR` | Clear E-STOP, enable drivers |

### Driver Control

| Command | Description |
|---------|-------------|
| `M17` | Enable both drivers |
| `M18` | Disable both drivers |

### Homing

| Command | Response | Description |
|---------|----------|-------------|
| `G28` | `ok IN_HOME_POS` | Home all axes (Y then X) |
| `G28 X` | `ok IN_X_HOME_POS` | Home X axis only |
| `G28 Y` | `ok IN_Y_HOME_POS` | Home Y axis only |
| `HOME` | same as G28 | Legacy alias |
| `CAL` | `ok` | Home all + go to position 0,0 |
| `ZERO` | `ok` | Go to position 0,0 (without homing) |

### Movement

| Command | Description |
|---------|-------------|
| `G X<mm> Y<mm> F<mm/min>` | Guarded move (with endstop protection) |
| `GF X<mm> Y<mm> F<mm/min>` | Fast move |
| `G0 X<mm> Y<mm> F<mm/min>` | G-code style move |
| `G1 X<mm> Y<mm> F<mm/min>` | Same as G0 |

### Jogging

| Command | Description |
|---------|-------------|
| `DX <+/-mm> F<mm/min>` | Jog X axis by delta |
| `DY <+/-mm> F<mm/min>` | Jog Y axis by delta |
| `JX <+/-mm> F<mm/min>` | Jog X (legacy syntax) |
| `JY <+/-mm> F<mm/min>` | Jog Y (legacy syntax) |

### Work Position

| Command | Description |
|---------|-------------|
| `WORK` | Go to saved work position |
| `WORK X<mm> Y<mm> F<mm/min>` | Go to specified position |
| `SET WORK X<mm> Y<mm> F<mm/min>` | Save work position |

### Configuration

| Command | Description |
|---------|-------------|
| `SET LIM X<mm> Y<mm>` | Set travel limits (MAX values) |
| `SET STEPS X<val> Y<val>` | Set steps per mm |
| `SET SPMM X<val> Y<val>` | Set steps per mm (legacy) |
| `SET X0` | Zero X position |
| `SET Y0` | Zero Y position |
| `SET XY0` | Zero both positions |

## Configuration Constants

Edit these in `xy_cli.py` header:

```python
# Steps per mm
STEPS_PER_MM_X = 20.0
STEPS_PER_MM_Y = 20.0

# Travel limits (mm)
X_MAX_MM = 165.0
Y_MAX_MM = 350.0

# Max feed rate (mm/s)
MAX_FEED_MM_S = 600.0

# Homing parameters
SCAN_RANGE_X_MM = 170.0
SCAN_RANGE_Y_MM = 355.0
BACKOFF_MM = 5.0
SLOW_MM_S = 2.0

# Work position defaults
WORK_X_MM = 5.0
WORK_Y_MM = 350.0
WORK_F_MM_MIN = 60000.0

# Direction inversion
INVERT_X_DIR = False
INVERT_Y_DIR = False
```

## Examples

```text
# Check connection
> PING
PONG

# Home and calibrate
> G28
ok IN_HOME_POS

# Move to position
> G X50 Y200 F12000
ok

# Jog X axis
> DX +10 F6000
ok

# Check status
> M114
STATUS X:60.000 Y:200.000 X_MIN:open Y_MIN:open ESTOP:0
ok

# Emergency stop
> M112
ok ESTOP

# Clear E-STOP
> M999
ok CLEAR
```

## Error Messages

| Error | Description |
|-------|-------------|
| `err ESTOP` | E-STOP is active, clear with M999 |
| `err UNKNOWN` | Unknown command |
| `err BAD_ARGS` | Missing or invalid arguments |
| `err BAD_SET` | Invalid SET command format |
| `err INVALID_NUMBER` | Cannot parse number |
| `err HOME_NOT_FOUND` | Homing failed (endstop not found) |
| `err HOME_X_NOT_FOUND` | X homing failed |
| `err HOME_Y_NOT_FOUND` | Y homing failed |

## Serial Protocol

When running in serial mode (`--serial`):

- Baud rate: 115200 (default, configurable with `--baud`)
- Line ending: `\n` or `\r\n`
- On startup sends: `ok READY`
- Each command response ends with `\n`

### Example Serial Session

```
< ok READY
> PING
< PONG
> G28
< ok IN_HOME_POS
> M114
< STATUS X:0.000 Y:0.000 X_MIN:TRIG Y_MIN:TRIG ESTOP:0
< ok
```

## Migration from Arduino Version

This Python version is fully compatible with the Arduino/RAMPS version (`old_main.ino`). Key differences:

| Feature | Arduino | Python |
|---------|---------|--------|
| Platform | RAMPS 1.4 / Mega2560 | Raspberry Pi 5 |
| GPIO library | Arduino | lgpio |
| Stepper library | AccelStepper | Direct pulse generation |
| Communication | Serial only | CLI + Serial |
| ALM/PED signals | Supported | Not implemented (no hardware) |
| Motor relays | Supported | Not implemented |

## Safety Notes

- Always run `G28` (homing) after power-up
- E-STOP (`M112`) immediately disables drivers
- Endstops are checked during movement toward MIN
- Homing has 60-second timeout
- Press `Ctrl+C` to safely exit in CLI mode

## Project Structure

```
3SD-SF-ScrewFeed/
├── README.md        # This file
├── xy_cli.py        # Main Python controller
├── old_main.ino     # Original Arduino version (reference)
└── .gitignore       # Git ignore rules
```

## License

Part of 3SD-SF-ScrewFeed automated screwdriver project.
