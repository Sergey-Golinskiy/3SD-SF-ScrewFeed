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
import time
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import asdict

from flask import Flask, jsonify, request, Response, render_template, send_from_directory
from flask_cors import CORS

from core import (
    GPIOController, RelayController, SensorController,
    XYTableController, CycleStateMachine, CycleState
)
from core.xy_table import XYTableMode
from core.state_machine import DeviceProgram, ProgramStep


class EstopMonitor:
    """
    Background monitor for physical E-STOP button.

    Polls the emergency_stop sensor and triggers XY table estop/clear
    commands immediately when button state changes.
    """

    def __init__(self, sensors: SensorController, xy_table: XYTableController,
                 cycle: Optional[CycleStateMachine] = None, poll_interval: float = 0.05):
        """
        Initialize E-STOP monitor.

        Args:
            sensors: Sensor controller to read E-STOP button
            xy_table: XY table controller to send estop/clear commands
            cycle: Optional cycle state machine for cycle estop
            poll_interval: Polling interval in seconds (default 50ms)
        """
        self._sensors = sensors
        self._xy_table = xy_table
        self._cycle = cycle
        self._poll_interval = poll_interval
        self._last_state: Optional[bool] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the E-STOP monitoring thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("E-STOP monitor started (polling every 50ms)")

    def stop(self) -> None:
        """Stop the E-STOP monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        print("E-STOP monitor stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                # Read emergency stop button state
                estop_pressed = self._sensors.is_emergency_stop_pressed()

                # Check if state changed
                if self._last_state is not None and self._last_state != estop_pressed:
                    if estop_pressed:
                        # Button pressed - trigger E-STOP immediately
                        print("E-STOP BUTTON PRESSED - sending M112 to XY table")
                        self._xy_table.estop()
                        if self._cycle:
                            self._cycle.emergency_stop()
                    else:
                        # Button released - clear E-STOP
                        print("E-STOP BUTTON RELEASED - sending M999 to XY table")
                        self._xy_table.clear_estop()
                        if self._cycle:
                            self._cycle.clear_estop()

                self._last_state = estop_pressed

            except Exception as e:
                print(f"E-STOP monitor error: {e}")

            time.sleep(self._poll_interval)


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
    # Setup paths for templates and static files
    base_dir = Path(__file__).parent.parent
    template_dir = base_dir / 'templates'
    static_dir = base_dir / 'static'

    app = Flask(__name__,
                template_folder=str(template_dir),
                static_folder=str(static_dir),
                static_url_path='/static')
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

    # Start E-STOP hardware monitor (polls every 50ms for immediate response)
    app.estop_monitor = None
    if sensors and xy_table:
        app.estop_monitor = EstopMonitor(sensors, xy_table, cycle, poll_interval=0.05)
        app.estop_monitor.start()

    # === Web UI Routes ===

    @app.route('/')
    def index():
        """Serve main Web UI page."""
        return render_template('index.html')

    @app.route('/static/<path:filename>')
    def serve_static(filename):
        """Serve static files."""
        return send_from_directory(app.static_folder, filename)

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
        # Build XY table status with health info
        xy_status = None
        if app.xy_table:
            health = app.xy_table.health
            xy_status = {
                'connected': app.xy_table.is_connected,
                'state': app.xy_table.state.name,
                'x': app.xy_table.x,
                'y': app.xy_table.y,
                'x_homed': app.xy_table.position.x_homed,
                'y_homed': app.xy_table.position.y_homed,
                'endstops': {
                    'x_min': app.xy_table.endstops.x_min,
                    'y_min': app.xy_table.endstops.y_min
                },
                'health': {
                    'service_status': health.service_status,
                    'last_ping_ok': health.last_ping_ok,
                    'last_ping_latency_ms': round(health.last_ping_latency_ms, 1),
                    'consecutive_errors': health.consecutive_errors,
                    'last_error': health.last_error,
                    'last_limit_warning': health.last_limit_warning
                }
            }

        result = {
            'relays': app.relays.get_all_states() if app.relays else {},
            'sensors': app.sensors.get_all_states() if app.sensors else {},
            'xy_table': xy_status,
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

    # === Legacy API Compatibility (for old touchdesk.py) ===

    # Store selected device key
    app.selected_device = None

    @app.route('/api/config', methods=['GET'])
    def get_config():
        """Legacy: Get devices config with selected device."""
        devices = [
            {'key': key, 'name': prog.name, 'holes': prog.holes}
            for key, prog in app.devices.items()
        ]
        return jsonify({
            'devices': devices,
            'selected': app.selected_device
        })

    @app.route('/api/select', methods=['POST'])
    def select_device():
        """Legacy: Select a device."""
        data = request.get_json() or {}
        key = data.get('key')
        if key and key in app.devices:
            app.selected_device = key
            return jsonify({'ok': True, 'selected': key})
        return jsonify({'ok': False, 'error': 'Invalid device key'}), 400

    @app.route('/api/ext/start', methods=['POST'])
    def ext_start():
        """Legacy: Start cycle with selected device."""
        if not app.selected_device:
            return jsonify({'ok': False, 'error': 'No device selected'}), 400
        if app.cycle:
            try:
                program = app.devices.get(app.selected_device)
                if program:
                    app.cycle.start(program)
                    return jsonify({'ok': True, 'started': app.selected_device})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500
        return jsonify({'ok': True, 'external_running': True})

    @app.route('/api/ext/stop', methods=['POST'])
    def ext_stop():
        """Legacy: Stop cycle."""
        if app.cycle:
            app.cycle.stop()
        return jsonify({'ok': True, 'external_running': False})

    @app.route('/api/relay', methods=['POST'])
    def legacy_relay():
        """Legacy: Relay control with old format."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503

        data = request.get_json() or {}
        name = data.get('name')
        action = data.get('action', 'toggle')
        ms = data.get('ms', 500)

        if not name:
            return jsonify({'error': 'Relay name required'}), 400

        if action == 'on':
            app.relays.on(name)
        elif action == 'off':
            app.relays.off(name)
        elif action == 'pulse':
            app.relays.pulse(name, ms / 1000.0)
        elif action == 'toggle':
            app.relays.toggle(name)

        return jsonify({'ok': True, 'relay': name, 'action': action})

    @app.route('/api/pedal', methods=['POST'])
    def pedal_pulse():
        """Legacy: Pedal pulse."""
        data = request.get_json() or {}
        ms = data.get('ms', 120)
        # Try to pulse PEDAL relay if exists
        if app.relays:
            try:
                app.relays.pulse('PEDAL', ms / 1000.0)
            except Exception:
                pass
        return jsonify({'ok': True})

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
        """Get detailed XY table status including health info."""
        if not app.xy_table:
            return jsonify({
                'error': 'XY table not initialized',
                'connected': False,
                'state': 'not_initialized',
                'health': {
                    'service_status': 'not_initialized',
                    'last_error': 'XY table controller not created'
                }
            }), 503
        return jsonify(app.xy_table.get_detailed_status())

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
        """Home XY table (all axes: Y first, then X)."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        try:
            data = request.get_json(silent=True) or {}
            axis = data.get('axis')  # None for both axes

            if app.xy_table.home(axis):
                return jsonify({'status': 'homed', 'axis': axis or 'all'})
            return jsonify({'error': 'Homing failed'}), 500
        except Exception as e:
            print(f"ERROR in xy_home: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/xy/home/x', methods=['POST'])
    def xy_home_x():
        """Home X axis only."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.home_x():
            return jsonify({'status': 'homed', 'axis': 'X'})
        return jsonify({'error': 'Homing X failed'}), 500

    @app.route('/api/xy/home/y', methods=['POST'])
    def xy_home_y():
        """Home Y axis only."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.home_y():
            return jsonify({'status': 'homed', 'axis': 'Y'})
        return jsonify({'error': 'Homing Y failed'}), 500

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

    @app.route('/api/xy/command', methods=['POST'])
    def xy_command():
        """Send raw G-code command to XY table."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        data = request.get_json(silent=True) or {}
        cmd = data.get('command', '').strip()

        if not cmd:
            return jsonify({'error': 'No command provided'}), 400

        try:
            response = app.xy_table._send_command(cmd, timeout=60.0)
            return jsonify({
                'status': 'ok',
                'command': cmd,
                'response': response or 'No response'
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

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

    @app.route('/api/xy/cancel', methods=['POST'])
    def xy_cancel():
        """
        Cancel all XY table commands - immediate stop without persistent E-STOP.
        Sends M112 (stop) followed by M999 (clear) for instant halt.
        """
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        errors = []

        try:
            # Stop XY table immediately
            app.xy_table.estop()
        except Exception as e:
            errors.append(f'estop: {str(e)}')

        # Small delay to ensure command is processed
        import time
        time.sleep(0.05)

        try:
            # Clear E-STOP to allow new commands
            app.xy_table.clear_estop()
        except Exception as e:
            errors.append(f'clear_estop: {str(e)}')

        # Also stop any running cycle
        try:
            if app.cycle:
                app.cycle.stop()
        except Exception as e:
            errors.append(f'cycle_stop: {str(e)}')

        if errors:
            return jsonify({'status': 'cancelled_with_errors', 'errors': errors})

        return jsonify({'status': 'cancelled'})

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
            'what': prog.what,
            'screw_size': prog.screw_size,
            'task': prog.task,
            'steps': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'feed': s.feed}
                for s in prog.steps
            ]
        })

    @app.route('/api/devices', methods=['POST'])
    def create_device():
        """Create a new device program."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        key = data.get('key', '').strip()
        if not key:
            return jsonify({'error': 'Device key is required'}), 400
        if key in app.devices:
            return jsonify({'error': f'Device {key} already exists'}), 400

        name = data.get('name', key)
        holes = int(data.get('holes', 0))
        what = data.get('what', '')
        screw_size = data.get('screw_size', '')
        task = data.get('task', '')
        steps_data = data.get('steps', [])

        steps = []
        for s in steps_data:
            steps.append(ProgramStep(
                step_type=s.get('type', 'free'),
                x=float(s.get('x', 0)),
                y=float(s.get('y', 0)),
                feed=float(s.get('feed', 60000))
            ))

        app.devices[key] = DeviceProgram(
            key=key,
            name=name,
            holes=holes,
            steps=steps,
            what=what,
            screw_size=screw_size,
            task=task
        )

        _save_devices(app)
        return jsonify({'success': True, 'key': key})

    @app.route('/api/devices/<key>', methods=['PUT'])
    def update_device(key):
        """Update an existing device program."""
        if key not in app.devices:
            return jsonify({'error': f'Unknown device: {key}'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Check if key is being changed
        new_key = data.get('key', key).strip()
        if new_key != key and new_key in app.devices:
            return jsonify({'error': f'Device {new_key} already exists'}), 400

        old_dev = app.devices[key]
        name = data.get('name', old_dev.name)
        holes = int(data.get('holes', old_dev.holes))
        what = data.get('what', old_dev.what)
        screw_size = data.get('screw_size', old_dev.screw_size)
        task = data.get('task', old_dev.task)
        steps_data = data.get('steps', None)

        if steps_data is not None:
            steps = []
            for s in steps_data:
                steps.append(ProgramStep(
                    step_type=s.get('type', 'free'),
                    x=float(s.get('x', 0)),
                    y=float(s.get('y', 0)),
                    feed=float(s.get('feed', 60000))
                ))
        else:
            steps = old_dev.steps

        # Remove old key if changed
        if new_key != key:
            del app.devices[key]

        app.devices[new_key] = DeviceProgram(
            key=new_key,
            name=name,
            holes=holes,
            steps=steps,
            what=what,
            screw_size=screw_size,
            task=task
        )

        _save_devices(app)
        return jsonify({'success': True, 'key': new_key})

    @app.route('/api/devices/<key>', methods=['DELETE'])
    def delete_device(key):
        """Delete a device program."""
        if key not in app.devices:
            return jsonify({'error': f'Unknown device: {key}'}), 404

        del app.devices[key]
        _save_devices(app)
        return jsonify({'success': True, 'deleted': key})

    return app


def _save_devices(app: Flask) -> None:
    """Save device programs to devices.yaml."""
    config_path = Path(__file__).parent.parent / 'config' / 'devices.yaml'

    devices_list = []
    for key, prog in app.devices.items():
        device_data = {
            'key': prog.key,
            'name': prog.name,
            'holes': prog.holes,
            'what': prog.what,
            'screw_size': prog.screw_size,
            'task': prog.task,
            'program': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'f': s.feed}
                for s in prog.steps
            ]
        }
        devices_list.append(device_data)

    data = {'devices': devices_list}

    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)



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
                        steps=steps,
                        what=dev.get('what', ''),
                        screw_size=dev.get('screw_size', ''),
                        task=dev.get('task', '')
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
