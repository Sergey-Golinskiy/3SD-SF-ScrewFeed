"""
Flask REST API Server for Screw Drive Control System.

Provides endpoints for:
- System status and health check
- Relay control
- Sensor reading
- XY table control
- Cycle execution and monitoring
"""

import os
import yaml
import threading
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import asdict

from flask import Flask, jsonify, request
from flask_cors import CORS

from ..core import (
    GPIOController, RelayController, SensorController,
    XYTableController, CycleStateMachine, CycleState
)
from ..core.xy_table import XYTableMode
from ..core.state_machine import DeviceProgram, ProgramStep


def create_app(
    gpio: Optional[GPIOController] = None,
    relays: Optional[RelayController] = None,
    sensors: Optional[SensorController] = None,
    xy_table: Optional[XYTableController] = None,
    cycle: Optional[CycleStateMachine] = None,
    config: Optional[Dict[str, Any]] = None
) -> Flask:
    """
    Create and configure Flask application.

    Args:
        gpio: GPIO controller instance
        relays: Relay controller instance
        sensors: Sensor controller instance
        xy_table: XY table controller instance
        cycle: Cycle state machine instance
        config: Application configuration

    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)
    CORS(app)

    # Store instances in app context
    app.gpio = gpio
    app.relays = relays
    app.sensors = sensors
    app.xy_table = xy_table
    app.cycle = cycle
    app.config_data = config or {}
    app.devices = {}

    # Load devices configuration
    _load_devices(app)

    # === Health and Status ===

    @app.route('/api/health', methods=['GET'])
    def health():
        """Health check endpoint."""
        return jsonify({
            'status': 'ok',
            'gpio_initialized': app.gpio.is_initialized if app.gpio else False,
            'xy_connected': app.xy_table.is_connected if app.xy_table else False,
            'cycle_state': app.cycle.state.name if app.cycle else 'N/A'
        })

    @app.route('/api/status', methods=['GET'])
    def status():
        """Get full system status."""
        result = {
            'relays': app.relays.get_all_states() if app.relays else {},
            'sensors': app.sensors.get_all_states() if app.sensors else {},
            'xy_table': {
                'connected': app.xy_table.is_connected if app.xy_table else False,
                'state': app.xy_table.state.name if app.xy_table else 'N/A',
                'x': app.xy_table.x if app.xy_table else 0,
                'y': app.xy_table.y if app.xy_table else 0
            } if app.xy_table else None,
            'cycle': None
        }

        if app.cycle:
            cycle_status = app.cycle.get_status()
            result['cycle'] = {
                'state': cycle_status.state.name,
                'error': cycle_status.error.name,
                'error_message': cycle_status.error_message,
                'current_device': cycle_status.current_device,
                'current_step': cycle_status.current_step,
                'total_steps': cycle_status.total_steps,
                'holes_completed': cycle_status.holes_completed,
                'total_holes': cycle_status.total_holes,
                'cycle_count': cycle_status.cycle_count
            }

        return jsonify(result)

    # === Relay Control ===

    @app.route('/api/relays', methods=['GET'])
    def get_relays():
        """Get all relay states."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503
        return jsonify(app.relays.get_all_states())

    @app.route('/api/relays/<name>', methods=['GET'])
    def get_relay(name):
        """Get single relay state."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503
        state = app.relays.get_state(name)
        return jsonify({'name': name, 'state': state.name})

    @app.route('/api/relays/<name>', methods=['POST'])
    def set_relay(name):
        """Set relay state."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503

        data = request.get_json() or {}
        state = data.get('state', 'toggle')

        if state == 'on':
            success = app.relays.on(name)
        elif state == 'off':
            success = app.relays.off(name)
        elif state == 'toggle':
            success = app.relays.toggle(name)
        elif state == 'pulse':
            duration = data.get('duration', 0.5)
            success = app.relays.pulse(name, duration)
        else:
            return jsonify({'error': f'Invalid state: {state}'}), 400

        if success:
            return jsonify({'name': name, 'state': app.relays.get_state(name).name})
        else:
            return jsonify({'error': f'Failed to set relay {name}'}), 500

    @app.route('/api/relays/all/off', methods=['POST'])
    def all_relays_off():
        """Turn all relays off."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503
        app.relays.all_off()
        return jsonify({'status': 'ok'})

    # === Sensor Reading ===

    @app.route('/api/sensors', methods=['GET'])
    def get_sensors():
        """Get all sensor states."""
        if not app.sensors:
            return jsonify({'error': 'Sensors not initialized'}), 503
        return jsonify(app.sensors.get_all_states())

    @app.route('/api/sensors/<name>', methods=['GET'])
    def get_sensor(name):
        """Get single sensor state."""
        if not app.sensors:
            return jsonify({'error': 'Sensors not initialized'}), 503
        state = app.sensors.read(name)
        return jsonify({'name': name, 'state': state.name, 'active': state.name == 'ACTIVE'})

    @app.route('/api/sensors/safety', methods=['GET'])
    def get_safety_status():
        """Get safety sensor status."""
        if not app.sensors:
            return jsonify({'error': 'Sensors not initialized'}), 503
        return jsonify({
            'safe': app.sensors.is_safe(),
            'estop_pressed': app.sensors.is_estop_pressed(),
            'area_blocked': app.sensors.is_area_blocked()
        })

    # === XY Table Control ===

    @app.route('/api/xy/status', methods=['GET'])
    def xy_status():
        """Get XY table status."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        return jsonify({
            'connected': app.xy_table.is_connected,
            'state': app.xy_table.state.name,
            'ready': app.xy_table.is_ready,
            'position': {
                'x': app.xy_table.x,
                'y': app.xy_table.y
            }
        })

    @app.route('/api/xy/connect', methods=['POST'])
    def xy_connect():
        """Connect to XY table."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.connect():
            return jsonify({'status': 'connected'})
        return jsonify({'error': 'Connection failed'}), 500

    @app.route('/api/xy/disconnect', methods=['POST'])
    def xy_disconnect():
        """Disconnect from XY table."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        app.xy_table.disconnect()
        return jsonify({'status': 'disconnected'})

    @app.route('/api/xy/home', methods=['POST'])
    def xy_home():
        """Home XY table."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        data = request.get_json() or {}
        axis = data.get('axis')  # None for both axes

        if app.xy_table.home(axis):
            return jsonify({'status': 'homed', 'axis': axis or 'all'})
        return jsonify({'error': 'Homing failed'}), 500

    @app.route('/api/xy/move', methods=['POST'])
    def xy_move():
        """Move XY table to position."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        data = request.get_json() or {}
        x = data.get('x')
        y = data.get('y')
        feed = data.get('feed', 10000.0)

        if x is None and y is None:
            return jsonify({'error': 'x or y required'}), 400

        if app.xy_table.move_to(x, y, feed):
            return jsonify({
                'status': 'ok',
                'position': {'x': app.xy_table.x, 'y': app.xy_table.y}
            })
        return jsonify({'error': 'Move failed'}), 500

    @app.route('/api/xy/jog', methods=['POST'])
    def xy_jog():
        """Jog XY table by offset."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        data = request.get_json() or {}
        dx = data.get('dx', 0)
        dy = data.get('dy', 0)
        feed = data.get('feed', 600.0)

        success = True
        if dx != 0:
            success = app.xy_table.jog_x(dx, feed) and success
        if dy != 0:
            success = app.xy_table.jog_y(dy, feed) and success

        if success:
            return jsonify({
                'status': 'ok',
                'position': {'x': app.xy_table.x, 'y': app.xy_table.y}
            })
        return jsonify({'error': 'Jog failed'}), 500

    @app.route('/api/xy/zero', methods=['POST'])
    def xy_zero():
        """Move XY table to zero."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.go_to_zero():
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Move to zero failed'}), 500

    @app.route('/api/xy/estop', methods=['POST'])
    def xy_estop():
        """Trigger XY table E-STOP."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        app.xy_table.estop()
        return jsonify({'status': 'estop_active'})

    @app.route('/api/xy/clear_estop', methods=['POST'])
    def xy_clear_estop():
        """Clear XY table E-STOP."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.clear_estop():
            return jsonify({'status': 'estop_cleared'})
        return jsonify({'error': 'Clear ESTOP failed'}), 500

    # === Cycle Control ===

    @app.route('/api/cycle/status', methods=['GET'])
    def cycle_status():
        """Get cycle status."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        status = app.cycle.get_status()
        return jsonify({
            'state': status.state.name,
            'error': status.error.name,
            'error_message': status.error_message,
            'current_device': status.current_device,
            'current_step': status.current_step,
            'total_steps': status.total_steps,
            'holes_completed': status.holes_completed,
            'total_holes': status.total_holes,
            'cycle_count': status.cycle_count,
            'is_running': app.cycle.is_running,
            'is_paused': app.cycle.is_paused
        })

    @app.route('/api/cycle/start', methods=['POST'])
    def cycle_start():
        """Start automation cycle."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503

        data = request.get_json() or {}
        device_key = data.get('device')

        if not device_key:
            return jsonify({'error': 'device key required'}), 400

        if device_key not in app.devices:
            return jsonify({'error': f'Unknown device: {device_key}'}), 404

        program = app.devices[device_key]
        if app.cycle.start(program):
            return jsonify({'status': 'started', 'device': device_key})
        return jsonify({'error': 'Failed to start cycle'}), 500

    @app.route('/api/cycle/stop', methods=['POST'])
    def cycle_stop():
        """Stop current cycle."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        app.cycle.stop()
        return jsonify({'status': 'stopped'})

    @app.route('/api/cycle/pause', methods=['POST'])
    def cycle_pause():
        """Pause current cycle."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        app.cycle.pause()
        return jsonify({'status': 'paused'})

    @app.route('/api/cycle/resume', methods=['POST'])
    def cycle_resume():
        """Resume paused cycle."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        app.cycle.resume()
        return jsonify({'status': 'resumed'})

    @app.route('/api/cycle/estop', methods=['POST'])
    def cycle_estop():
        """Trigger emergency stop."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        app.cycle.emergency_stop()
        return jsonify({'status': 'estop_active'})

    @app.route('/api/cycle/clear_estop', methods=['POST'])
    def cycle_clear_estop():
        """Clear emergency stop."""
        if not app.cycle:
            return jsonify({'error': 'Cycle not initialized'}), 503
        if app.cycle.clear_estop():
            return jsonify({'status': 'estop_cleared'})
        return jsonify({'error': 'Clear ESTOP failed'}), 500

    # === Devices ===

    @app.route('/api/devices', methods=['GET'])
    def get_devices():
        """Get list of available devices."""
        return jsonify([
            {
                'key': key,
                'name': prog.name,
                'holes': prog.holes,
                'steps': len(prog.steps)
            }
            for key, prog in app.devices.items()
        ])

    @app.route('/api/devices/<key>', methods=['GET'])
    def get_device(key):
        """Get device program details."""
        if key not in app.devices:
            return jsonify({'error': f'Unknown device: {key}'}), 404

        prog = app.devices[key]
        return jsonify({
            'key': prog.key,
            'name': prog.name,
            'holes': prog.holes,
            'steps': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'feed': s.feed}
                for s in prog.steps
            ]
        })

    return app


def _load_devices(app: Flask) -> None:
    """Load device programs from configuration."""
    # Try to load from devices.yaml
    config_paths = [
        Path(__file__).parent.parent / 'config' / 'devices.yaml',
        Path('/etc/screwdrive/devices.yaml'),
        Path('devices.yaml')
    ]

    for path in config_paths:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    data = yaml.safe_load(f)

                for dev in data.get('devices', []):
                    key = dev.get('key', '')
                    if not key:
                        continue

                    steps = []
                    for step in dev.get('program', []):
                        steps.append(ProgramStep(
                            step_type=step.get('type', 'free'),
                            x=float(step.get('x', 0)),
                            y=float(step.get('y', 0)),
                            feed=float(step.get('f', 60000))
                        ))

                    app.devices[key] = DeviceProgram(
                        key=key,
                        name=dev.get('name', key),
                        holes=int(dev.get('holes', 0)),
                        steps=steps
                    )

                print(f"Loaded {len(app.devices)} devices from {path}")
                return

            except Exception as e:
                print(f"WARNING: Failed to load devices from {path}: {e}")


class APIServer:
    """
    Wrapper for running Flask API server.

    Provides threaded server start/stop functionality.
    """

    def __init__(self, app: Flask, host: str = '0.0.0.0', port: int = 5000):
        """
        Initialize API server.

        Args:
            app: Flask application instance
            host: Host address to bind
            port: Port number
        """
        self._app = app
        self._host = host
        self._port = port
        self._thread: Optional[threading.Thread] = None

    def start(self, threaded: bool = True) -> None:
        """
        Start the API server.

        Args:
            threaded: Run in background thread if True
        """
        if threaded:
            self._thread = threading.Thread(
                target=self._run,
                daemon=True
            )
            self._thread.start()
        else:
            self._run()

    def _run(self) -> None:
        """Run the Flask application."""
        self._app.run(
            host=self._host,
            port=self._port,
            debug=False,
            use_reloader=False
        )

    def stop(self) -> None:
        """Stop the API server (only works in debug mode)."""
        # Note: Flask doesn't have a clean way to stop from code
        # In production, use a proper WSGI server like gunicorn
        pass
