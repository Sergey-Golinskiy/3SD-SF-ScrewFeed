#!/usr/bin/env python3
"""
Main entry point for Screw Drive Control System.

Usage:
    python main.py                    # Start full system
    python main.py --api-only         # Start API server only
    python main.py --cli              # Start CLI mode
    python main.py --test-gpio        # Test GPIO connections
"""

import sys
import os
import argparse
import signal
import time
import logging
from pathlib import Path
from typing import Optional

# Add screwdrive directory to path for imports
SCRIPT_DIR = Path(__file__).parent.absolute()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import yaml

from core import (
    GPIOController, RelayController, SensorController,
    XYTableController, CycleStateMachine
)
from core.xy_table import XYTableMode
from api import create_app, APIServer


# Global instances for cleanup
gpio: Optional[GPIOController] = None
xy_table: Optional[XYTableController] = None
api_server: Optional[APIServer] = None


def setup_logging(config: dict) -> None:
    """Configure logging."""
    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO').upper())

    # Create log directory if needed
    log_file = log_config.get('file')
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file) if log_file else logging.NullHandler()
        ]
    )


def load_config() -> dict:
    """Load configuration from YAML files."""
    config = {}

    # Config paths to search
    config_paths = [
        Path(__file__).parent / 'config' / 'settings.yaml',
        Path('/etc/screwdrive/settings.yaml'),
        Path('settings.yaml')
    ]

    for path in config_paths:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    config = yaml.safe_load(f) or {}
                print(f"Loaded config from {path}")
                break
            except Exception as e:
                print(f"WARNING: Failed to load config from {path}: {e}")

    return config


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    print("\nShutdown requested...")
    cleanup()
    sys.exit(0)


def cleanup():
    """Clean up resources."""
    global gpio, xy_table, api_server

    print("Cleaning up...")

    if xy_table:
        xy_table.disconnect()

    if gpio:
        gpio.close()

    print("Cleanup complete.")


def init_system(config: dict) -> tuple:
    """
    Initialize all system components.

    Returns:
        Tuple of (gpio, relays, sensors, xy_table, cycle)
    """
    global gpio, xy_table

    print("Initializing system...")

    # GPIO Controller
    gpio_config_path = Path(__file__).parent / 'config' / 'gpio_pins.yaml'
    gpio = GPIOController(str(gpio_config_path) if gpio_config_path.exists() else None)
    if not gpio.init():
        print("ERROR: Failed to initialize GPIO")
        return None, None, None, None, None

    print("  GPIO initialized")

    # Relay Controller
    relays = RelayController(gpio)
    if not relays.init():
        print("ERROR: Failed to initialize relays")
        return None, None, None, None, None

    print("  Relays initialized")

    # Sensor Controller
    sensors = SensorController(gpio)
    if not sensors.init():
        print("ERROR: Failed to initialize sensors")
        return None, None, None, None, None

    sensors.start_monitoring()
    print("  Sensors initialized and monitoring")

    # XY Table Controller
    xy_config = config.get('xy_table', {})
    mode = XYTableMode.SERIAL if xy_config.get('mode') == 'serial' else XYTableMode.DIRECT
    port = xy_config.get('serial_port', '/dev/ttyAMA0')
    baud = xy_config.get('serial_baud', 115200)

    xy_table = XYTableController(mode=mode, port=port, baud=baud)

    if xy_table.connect():
        print(f"  XY Table connected on {port}")
    else:
        print(f"  WARNING: XY Table connection failed (will retry)")

    # Cycle State Machine
    timing_config = config.get('timing', {})
    cycle = CycleStateMachine(relays, sensors, xy_table, timing_config)
    print("  Cycle state machine initialized")

    print("System initialization complete.")
    return gpio, relays, sensors, xy_table, cycle


def run_api_server(config: dict, gpio, relays, sensors, xy_table, cycle):
    """Run the API server."""
    global api_server

    api_config = config.get('api', {})
    host = api_config.get('host', '0.0.0.0')
    port = api_config.get('port', 5000)

    print(f"Starting API server on {host}:{port}...")

    app = create_app(gpio, relays, sensors, xy_table, cycle, config)
    api_server = APIServer(app, host, port)
    api_server.start(threaded=False)


def run_cli_mode(relays, sensors, xy_table, cycle):
    """Run interactive CLI mode."""
    print("\n=== Screw Drive Control CLI ===")
    print("Commands: status, home, move <x> <y>, jog <dx> <dy>, relay <name> <on|off>")
    print("          sensor <name>, start <device>, stop, quit")
    print()

    while True:
        try:
            line = input("> ").strip().lower()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0]

            if cmd == 'quit' or cmd == 'exit':
                break

            elif cmd == 'status':
                print(f"XY Table: {xy_table.state.name} @ ({xy_table.x:.2f}, {xy_table.y:.2f})")
                print(f"Cycle: {cycle.state.name}")
                print(f"Safety: {'OK' if sensors.is_safe() else 'BLOCKED'}")

            elif cmd == 'home':
                print("Homing...")
                if xy_table.home():
                    print("Homing complete")
                else:
                    print("Homing failed!")

            elif cmd == 'move' and len(parts) >= 3:
                x = float(parts[1])
                y = float(parts[2])
                feed = float(parts[3]) if len(parts) > 3 else 10000
                print(f"Moving to ({x}, {y})...")
                if xy_table.move_to(x, y, feed):
                    print(f"Moved to ({xy_table.x:.2f}, {xy_table.y:.2f})")
                else:
                    print("Move failed!")

            elif cmd == 'jog' and len(parts) >= 3:
                dx = float(parts[1])
                dy = float(parts[2])
                feed = float(parts[3]) if len(parts) > 3 else 600
                xy_table.move_relative(dx, dy, feed)
                print(f"Position: ({xy_table.x:.2f}, {xy_table.y:.2f})")

            elif cmd == 'relay' and len(parts) >= 3:
                name = parts[1]
                state = parts[2] == 'on'
                if relays.set(name, state):
                    print(f"Relay {name}: {'ON' if state else 'OFF'}")
                else:
                    print(f"Failed to set relay {name}")

            elif cmd == 'sensor' and len(parts) >= 2:
                name = parts[1]
                state = sensors.read(name)
                print(f"Sensor {name}: {state.name}")

            elif cmd == 'relays':
                for name, state in relays.get_all_states().items():
                    print(f"  {name}: {state}")

            elif cmd == 'sensors':
                for name, state in sensors.get_all_states().items():
                    print(f"  {name}: {state}")

            elif cmd == 'estop':
                xy_table.estop()
                cycle.emergency_stop()
                print("E-STOP ACTIVATED")

            elif cmd == 'clear':
                xy_table.clear_estop()
                cycle.clear_estop()
                print("E-STOP cleared")

            else:
                print(f"Unknown command: {cmd}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


def test_gpio(gpio):
    """Test GPIO connections."""
    print("\n=== GPIO Test Mode ===")
    print("Testing GPIO pin states...")

    # Test reading some pins
    test_pins = [2, 3, 5, 6, 12, 13, 19, 26]
    for pin in test_pins:
        gpio.setup_input(pin, pull_up=True)
        value = gpio.read(pin)
        print(f"  GPIO {pin}: {value}")

    print("\nGPIO test complete.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Screw Drive Control System')
    parser.add_argument('--api-only', action='store_true',
                        help='Start API server only')
    parser.add_argument('--cli', action='store_true',
                        help='Start in CLI mode')
    parser.add_argument('--test-gpio', action='store_true',
                        help='Test GPIO connections')
    parser.add_argument('--host', default='0.0.0.0',
                        help='API server host')
    parser.add_argument('--port', type=int, default=5000,
                        help='API server port')
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Load configuration
    config = load_config()
    setup_logging(config)

    if args.host:
        config.setdefault('api', {})['host'] = args.host
    if args.port:
        config.setdefault('api', {})['port'] = args.port

    try:
        # Initialize system
        gpio, relays, sensors, xy_table, cycle = init_system(config)

        if gpio is None:
            print("System initialization failed!")
            sys.exit(1)

        if args.test_gpio:
            test_gpio(gpio)

        elif args.cli:
            run_cli_mode(relays, sensors, xy_table, cycle)

        else:
            # Start API server (blocking)
            run_api_server(config, gpio, relays, sensors, xy_table, cycle)

    finally:
        cleanup()


if __name__ == '__main__':
    main()
