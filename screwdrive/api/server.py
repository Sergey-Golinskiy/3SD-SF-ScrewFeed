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

from flask import Flask, jsonify, request, Response
from flask_cors import CORS

from core import (
    GPIOController, RelayController, SensorController,
    XYTableController, CycleStateMachine, CycleState
)
from core.xy_table import XYTableMode
from core.state_machine import DeviceProgram, ProgramStep


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
            steps=steps
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

        name = data.get('name', app.devices[key].name)
        holes = int(data.get('holes', app.devices[key].holes))
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
            steps = app.devices[key].steps

        # Remove old key if changed
        if new_key != key:
            del app.devices[key]

        app.devices[new_key] = DeviceProgram(
            key=new_key,
            name=name,
            holes=holes,
            steps=steps
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

    # === Web UI ===
    @app.route('/', methods=['GET'])
    def index():
        """Main web UI."""
        return Response(WEB_UI_HTML, mimetype='text/html')

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
            'program': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'f': s.feed}
                for s in prog.steps
            ]
        }
        devices_list.append(device_data)

    data = {'devices': devices_list}

    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# === Web UI HTML ===
WEB_UI_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Панель керування відкруткою</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
  h1 { margin: 0 0 10px; color: #333; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .status-badge { padding: 6px 12px; border-radius: 20px; font-size: 14px; font-weight: 600; }
  .status-badge.ok { background: #d4edda; color: #155724; }
  .status-badge.error { background: #f8d7da; color: #721c24; }
  .status-badge.warning { background: #fff3cd; color: #856404; }
  .row { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 20px; }
  .card { background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); flex: 1; min-width: 300px; }
  .card h3 { margin: 0 0 15px; color: #444; border-bottom: 2px solid #eee; padding-bottom: 10px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 10px; text-align: left; border-bottom: 1px solid #eee; }
  th { background: #f8f9fa; font-weight: 600; }
  .ok { color: #28a745; font-weight: 600; }
  .off { color: #dc3545; font-weight: 600; }
  .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; transition: all 0.2s; }
  .btn-primary { background: #007bff; color: white; }
  .btn-primary:hover { background: #0056b3; }
  .btn-success { background: #28a745; color: white; }
  .btn-success:hover { background: #1e7e34; }
  .btn-danger { background: #dc3545; color: white; }
  .btn-danger:hover { background: #c82333; }
  .btn-warning { background: #ffc107; color: #212529; }
  .btn-warning:hover { background: #e0a800; }
  .btn-secondary { background: #6c757d; color: white; }
  .btn-secondary:hover { background: #545b62; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
  input[type=number], input[type=text], select { padding: 8px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; }
  .form-row { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
  .form-row label { min-width: 80px; font-weight: 500; }
  .muted { color: #6c757d; font-size: 13px; }
  .badge { display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .badge-success { background: #d4edda; color: #155724; }
  .badge-danger { background: #f8d7da; color: #721c24; }
  .badge-info { background: #d1ecf1; color: #0c5460; }
  .badge-warning { background: #fff3cd; color: #856404; }
  .xy-display { font-size: 24px; font-weight: bold; color: #333; margin: 15px 0; }
  .xy-display span { color: #007bff; }
  .jog-grid { display: grid; grid-template-columns: repeat(3, 60px); gap: 5px; justify-content: center; margin: 15px 0; }
  .jog-grid .btn { width: 60px; height: 50px; font-size: 18px; }
  .cycle-status { padding: 15px; background: #f8f9fa; border-radius: 8px; margin-bottom: 15px; }
  .progress-bar { height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; margin: 10px 0; }
  .progress-bar-fill { height: 100%; background: #28a745; transition: width 0.3s; }
  #log { height: 150px; overflow-y: auto; background: #1e1e1e; color: #0f0; font-family: monospace; padding: 10px; border-radius: 8px; font-size: 12px; }
</style>
</head>
<body>

<div class="header">
  <h1>Панель керування відкруткою</h1>
  <div>
    <span id="connectionStatus" class="status-badge warning">Підключення...</span>
    <span id="updateTime" class="muted" style="margin-left: 10px;"></span>
  </div>
</div>

<div class="row">
  <!-- Cycle Control -->
  <div class="card" style="flex: 2;">
    <h3>Керування циклом</h3>
    <div class="form-row">
      <label>Пристрій:</label>
      <select id="deviceSelect" style="flex: 1;"></select>
    </div>
    <div class="cycle-status" id="cycleStatus">
      <div>Стан: <span id="cycleState" class="badge badge-info">ОЧІКУВАННЯ</span></div>
      <div style="margin-top: 8px;">Отвори: <span id="holesProgress">0 / 0</span></div>
      <div class="progress-bar"><div class="progress-bar-fill" id="progressBar" style="width: 0%;"></div></div>
    </div>
    <div class="btn-group">
      <button class="btn btn-success" id="btnStart">СТАРТ</button>
      <button class="btn btn-warning" id="btnPause">ПАУЗА</button>
      <button class="btn btn-danger" id="btnStop">СТОП</button>
      <button class="btn btn-danger" id="btnEstop">АВАРІЯ</button>
      <button class="btn btn-secondary" id="btnClearEstop">Скинути аварію</button>
    </div>
  </div>

  <!-- XY Table -->
  <div class="card">
    <h3>XY Стіл</h3>
    <div id="xyStatus">
      <span class="badge badge-warning" id="xyState">DISCONNECTED</span>
    </div>
    <div class="xy-display">
      X: <span id="posX">0.00</span> mm<br>
      Y: <span id="posY">0.00</span> mm
    </div>
    <div class="jog-grid">
      <div></div>
      <button class="btn btn-secondary" onclick="jog(0, 10)">Y+</button>
      <div></div>
      <button class="btn btn-secondary" onclick="jog(-10, 0)">X-</button>
      <button class="btn btn-primary" onclick="homeXY()">H</button>
      <button class="btn btn-secondary" onclick="jog(10, 0)">X+</button>
      <div></div>
      <button class="btn btn-secondary" onclick="jog(0, -10)">Y-</button>
      <div></div>
    </div>
    <div class="form-row">
      <input type="number" id="jogStep" value="10" min="1" max="100" style="width: 80px;">
      <span class="muted">мм крок</span>
    </div>
    <div class="btn-group" style="margin-top: 10px;">
      <button class="btn btn-primary" onclick="homeXY()">НУЛЬ</button>
      <button class="btn btn-warning" onclick="homeY()">НУЛЬ Y</button>
      <button class="btn btn-warning" onclick="homeX()">НУЛЬ X</button>
      <button class="btn btn-secondary" id="btnXYConnect" onclick="connectXY()">Підключити</button>
    </div>
  </div>
</div>

<div class="row">
  <!-- Sensors -->
  <div class="card">
    <h3>Датчики</h3>
    <table id="sensorsTable">
      <thead><tr><th>Назва</th><th>Стан</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <!-- Relays -->
  <div class="card">
    <h3>Реле</h3>
    <table id="relaysTable">
      <thead><tr><th>Назва</th><th>Стан</th><th>Керування</th><th>Імпульс</th></tr></thead>
      <tbody></tbody>
    </table>
    <div style="margin-top: 10px;">
      <button class="btn btn-danger" onclick="allRelaysOff()">Все ВИКЛ</button>
    </div>
  </div>
</div>

<div class="row">
  <div class="card" style="flex: 1;">
    <h3>Ручне переміщення</h3>
    <div class="form-row">
      <label>X:</label>
      <input type="number" id="moveX" value="0" step="0.1" style="width: 100px;">
      <label>Y:</label>
      <input type="number" id="moveY" value="0" step="0.1" style="width: 100px;">
      <label>Швидкість:</label>
      <input type="number" id="moveFeed" value="10000" step="100" style="width: 100px;">
    </div>
    <button class="btn btn-primary" onclick="manualMove()">Рухати</button>

    <h4 style="margin-top: 15px; margin-bottom: 10px;">G-code консоль</h4>
    <textarea id="gcodeInput" rows="5" placeholder="Введіть G-code (одна команда на рядок)
Приклад:
G28
G0 X100 Y200 F1000
G0 X50 Y100 F1000" style="width: 100%; font-family: monospace; font-size: 13px; padding: 8px; border: 1px solid #ddd; border-radius: 8px; resize: vertical;"></textarea>
    <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;">
      <button class="btn btn-primary" onclick="sendGcode()">Запустити</button>
      <button class="btn btn-secondary" onclick="document.getElementById('gcodeInput').value=''">Очистити</button>
      <span class="muted">Команди: G0/G1, G2/G3, G28, HOME, ZERO, M114, M119</span>
    </div>
  </div>

  <div class="card" style="flex: 1;">
    <h3>Журнал</h3>
    <div id="log"></div>
    <button class="btn btn-secondary" onclick="clearLog()" style="margin-top: 10px;">Очистити журнал</button>
  </div>
</div>

<!-- Device Management -->
<details class="card" style="margin-top: 20px;">
  <summary style="cursor: pointer; font-weight: 600; font-size: 18px; padding: 10px;">
    📦 Управління девайсами
  </summary>
  <div style="padding: 15px;">
    <div class="row">
      <div style="flex: 1; min-width: 250px;">
        <h4>Існуючі девайси</h4>
        <div id="deviceList" style="max-height: 200px; overflow-y: auto; border: 1px solid #ddd; border-radius: 8px; padding: 10px;"></div>
        <div style="margin-top: 10px;">
          <button class="btn btn-primary" onclick="newDevice()">+ Новий</button>
          <button class="btn btn-secondary" onclick="editDevice()">Редагувати</button>
          <button class="btn btn-danger" onclick="deleteDevice()">Видалити</button>
        </div>
      </div>
      <div style="flex: 2; min-width: 400px;">
        <h4 id="editorTitle">Редактор девайсу</h4>
        <div class="form-row">
          <label>Key:</label>
          <input type="text" id="devKey" placeholder="device_key" style="flex: 1;">
          <label style="margin-left: 15px;">Name:</label>
          <input type="text" id="devName" placeholder="Device Name" style="flex: 1;">
          <label style="margin-left: 15px;">Holes:</label>
          <input type="number" id="devHoles" value="0" min="0" style="width: 60px;">
        </div>
        <div style="margin-top: 10px;">
          <label>Кроки програми (type, x, y, feed):</label>
          <textarea id="devSteps" rows="6" placeholder="free, 0, 0, 60000
work, 10, 20, 30000
free, 10, 30, 60000" style="width: 100%; font-family: monospace; font-size: 12px;"></textarea>
        </div>
        <div style="margin-top: 10px;">
          <button class="btn btn-success" onclick="saveDevice()">💾 Зберегти</button>
          <button class="btn btn-secondary" onclick="cancelEdit()">Скасувати</button>
        </div>
      </div>
    </div>
  </div>
</details>

<script>
const API = '';
let selectedDevice = null;

// Ukrainian names for relays and sensors
const RELAY_NAMES = {
  'r01_pit': 'Живильник гвинтів',
  'r02_di7_tsk2': 'Вибір задачі 2',
  'r04_c2': 'Циліндр відкрутки',
  'r05_di4_free': 'Вільний хід',
  'r06_di1_pot': 'Режим по моменту',
  'r07_di5_tsk0': 'Вибір задачі 0',
  'r08_di6_tsk1': 'Вибір задачі 1'
};

const SENSOR_NAMES = {
  'area_sensor': 'Світлова завіса',
  'ped_start': 'Педаль старту',
  'ger_c2_up': 'Циліндр вгорі',
  'ger_c2_down': 'Циліндр внизу - АВАРІЯ!',
  'ind_scrw': 'Датчик гвинта',
  'do2_ok': 'Момент OK',
  'emergency_stop': 'Аварійна кнопка (грибок)'
};

// Default pulse durations in ms
const RELAY_PULSE_DEFAULTS = {
  'r01_pit': 200,        // Живильник - 200мс
  'r02_di7_tsk2': 700,   // Задача 2 - 700мс
  'r04_c2': 500,         // Циліндр - 500мс
  'r05_di4_free': 500,   // Вільний хід - 500мс
  'r06_di1_pot': 500,    // По моменту - 500мс
  'r07_di5_tsk0': 700,   // Задача 0 - 700мс
  'r08_di6_tsk1': 700    // Задача 1 - 700мс
};

function getRelayName(key) {
  return RELAY_NAMES[key] || key;
}

function getSensorName(key) {
  return SENSOR_NAMES[key] || key;
}

async function api(endpoint, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(API + endpoint, opts);
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch (e) {
      console.error('API response not JSON:', text.substring(0, 200));
      throw new Error('Server error: ' + res.status);
    }
  } catch (e) {
    console.error('API call failed:', endpoint, e);
    throw e;
  }
}

function log(msg) {
  const el = document.getElementById('log');
  const time = new Date().toLocaleTimeString();
  el.innerHTML += `[${time}] ${msg}<br>`;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById('log').innerHTML = '';
}

async function sendGcode() {
  const input = document.getElementById('gcodeInput');
  const text = input.value.trim();
  if (!text) return;

  // Split into lines and filter empty/comment lines
  const lines = text.split('\\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith(';') && !l.startsWith('#'));

  if (lines.length === 0) return;

  log('--- Running program (' + lines.length + ' commands) ---');

  for (let i = 0; i < lines.length; i++) {
    const cmd = lines[i];
    try {
      log('> [' + (i+1) + '/' + lines.length + '] ' + cmd);
      const result = await api('/api/xy/command', 'POST', { command: cmd });
      if (result.response) {
        result.response.split('\\n').forEach(line => {
          if (line.trim()) log('< ' + line);
        });
      }
    } catch (e) {
      log('Error at line ' + (i+1) + ': ' + e.message);
      log('--- Program stopped ---');
      refreshStatus();
      return;
    }
  }

  log('--- Program complete ---');
  refreshStatus();
}

let selectedDeviceKey = null;
let editingDevice = null;

async function loadDevices() {
  try {
    const devices = await api('/api/devices');

    // Populate cycle control select
    const sel = document.getElementById('deviceSelect');
    sel.innerHTML = '<option value="">-- Select Device --</option>';
    devices.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.key;
      opt.textContent = `${d.name} (${d.holes} holes)`;
      sel.appendChild(opt);
    });

    // Populate device management list
    const list = document.getElementById('deviceList');
    if (list) {
      list.innerHTML = '';
      devices.forEach(d => {
        const div = document.createElement('div');
        div.style.cssText = 'padding: 8px; margin: 4px 0; border-radius: 6px; cursor: pointer; background: ' + (d.key === selectedDeviceKey ? '#d4edda' : '#f8f9fa');
        div.textContent = `${d.name} (${d.holes} holes)`;
        div.onclick = () => selectDeviceForEdit(d.key);
        list.appendChild(div);
      });
    }
  } catch (e) {
    log('Error loading devices: ' + e.message);
  }
}

function selectDeviceForEdit(key) {
  selectedDeviceKey = key;
  loadDevices(); // Refresh to show selection
}

function newDevice() {
  editingDevice = null;
  document.getElementById('devKey').value = '';
  document.getElementById('devKey').disabled = false;
  document.getElementById('devName').value = '';
  document.getElementById('devHoles').value = 0;
  document.getElementById('devSteps').value = '';
  document.getElementById('editorTitle').textContent = 'Новий девайс';
}

async function editDevice() {
  if (!selectedDeviceKey) {
    log('Спочатку виберіть девайс!');
    return;
  }
  try {
    const dev = await api('/api/devices/' + selectedDeviceKey);
    editingDevice = selectedDeviceKey;
    document.getElementById('devKey').value = dev.key;
    document.getElementById('devKey').disabled = true; // Can't change key when editing
    document.getElementById('devName').value = dev.name;
    document.getElementById('devHoles').value = dev.holes;

    // Format steps as text
    let stepsText = '';
    dev.steps.forEach(s => {
      stepsText += `${s.type}, ${s.x}, ${s.y}, ${s.feed}\\n`;
    });
    document.getElementById('devSteps').value = stepsText.trim();
    document.getElementById('editorTitle').textContent = 'Редагування: ' + dev.name;
  } catch (e) {
    log('Помилка завантаження девайсу: ' + e.message);
  }
}

async function deleteDevice() {
  if (!selectedDeviceKey) {
    log('Спочатку виберіть девайс!');
    return;
  }
  if (!confirm('Видалити девайс ' + selectedDeviceKey + '?')) {
    return;
  }
  try {
    await fetch(API + '/api/devices/' + selectedDeviceKey, { method: 'DELETE' });
    log('Девайс видалено: ' + selectedDeviceKey);
    selectedDeviceKey = null;
    cancelEdit();
    loadDevices();
  } catch (e) {
    log('Помилка видалення: ' + e.message);
  }
}

async function saveDevice() {
  const key = document.getElementById('devKey').value.trim();
  const name = document.getElementById('devName').value.trim() || key;
  const holes = parseInt(document.getElementById('devHoles').value) || 0;
  const stepsText = document.getElementById('devSteps').value.trim();

  if (!key) {
    log('Key обов\\'язковий!');
    return;
  }

  // Parse steps
  const steps = [];
  stepsText.split('\\n').forEach(line => {
    line = line.trim();
    if (!line) return;
    const parts = line.split(',').map(p => p.trim());
    if (parts.length >= 4) {
      steps.push({
        type: parts[0],
        x: parseFloat(parts[1]),
        y: parseFloat(parts[2]),
        feed: parseFloat(parts[3])
      });
    }
  });

  const data = { key, name, holes, steps };

  try {
    if (editingDevice) {
      // Update existing
      await fetch(API + '/api/devices/' + editingDevice, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      log('Девайс оновлено: ' + key);
    } else {
      // Create new
      await fetch(API + '/api/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      log('Девайс створено: ' + key);
    }
    cancelEdit();
    loadDevices();
  } catch (e) {
    log('Помилка збереження: ' + e.message);
  }
}

function cancelEdit() {
  editingDevice = null;
  document.getElementById('devKey').value = '';
  document.getElementById('devKey').disabled = false;
  document.getElementById('devName').value = '';
  document.getElementById('devHoles').value = 0;
  document.getElementById('devSteps').value = '';
  document.getElementById('editorTitle').textContent = 'Редактор девайсу';
}

async function refreshStatus() {
  try {
    const data = await api('/api/status');

    // Connection status
    document.getElementById('connectionStatus').className = 'status-badge ok';
    document.getElementById('connectionStatus').textContent = "Під'єднано";
    document.getElementById('updateTime').textContent = new Date().toLocaleTimeString();

    // Sensors
    const sensorsBody = document.querySelector('#sensorsTable tbody');
    sensorsBody.innerHTML = '';
    // Emergency sensors - ACTIVE state is danger (red)
    const emergencySensors = ['emergency_stop', 'ger_c2_down'];
    for (const [name, state] of Object.entries(data.sensors || {})) {
      const tr = document.createElement('tr');
      const isActive = state === 'ACTIVE';
      const displayName = getSensorName(name);
      const isEmergency = emergencySensors.includes(name);
      // For emergency sensors: ACTIVE = danger (red), INACTIVE = ok (green)
      // For normal sensors: ACTIVE = ok (green), INACTIVE = off (gray)
      let stateClass, stateText;
      if (isEmergency) {
        stateClass = isActive ? 'error' : 'ok';
        stateText = isActive ? 'АВАРІЯ!' : 'НІ';
      } else {
        stateClass = isActive ? 'ok' : 'off';
        stateText = isActive ? 'ТАК' : 'НІ';
      }
      tr.innerHTML = `<td title="${name}">${displayName}</td><td class="${stateClass}">${stateText}</td>`;
      sensorsBody.appendChild(tr);
    }

    // Relays
    const relaysBody = document.querySelector('#relaysTable tbody');
    relaysBody.innerHTML = '';
    for (const [name, state] of Object.entries(data.relays || {})) {
      const tr = document.createElement('tr');
      const isOn = state === 'ON';
      const displayName = getRelayName(name);
      const defaultPulse = RELAY_PULSE_DEFAULTS[name] || 500;
      tr.innerHTML = `
        <td title="${name}">${displayName}</td>
        <td class="${isOn ? 'ok' : 'off'}">${isOn ? 'ВКЛ' : 'ВИКЛ'}</td>
        <td>
          <button class="btn btn-success" onclick="setRelay('${name}', 'on')" style="padding: 4px 8px;">ВКЛ</button>
          <button class="btn btn-danger" onclick="setRelay('${name}', 'off')" style="padding: 4px 8px;">ВИКЛ</button>
        </td>
        <td>
          <input type="number" id="pulse_${name}" value="${defaultPulse}" min="50" max="5000" style="width: 70px; padding: 4px;" title="мс">
          <button class="btn btn-warning" onclick="pulseRelay('${name}')" style="padding: 4px 8px;">ІМПУЛЬС</button>
        </td>
      `;
      relaysBody.appendChild(tr);
    }

    // XY Table
    if (data.xy_table) {
      document.getElementById('xyState').textContent = data.xy_table.state;
      document.getElementById('xyState').className = 'badge ' +
        (data.xy_table.connected ? 'badge-success' : 'badge-danger');
      document.getElementById('posX').textContent = data.xy_table.x.toFixed(2);
      document.getElementById('posY').textContent = data.xy_table.y.toFixed(2);
    }

    // Cycle
    if (data.cycle) {
      document.getElementById('cycleState').textContent = data.cycle.state;
      const stateClass = {
        'IDLE': 'badge-info', 'READY': 'badge-success', 'COMPLETED': 'badge-success',
        'ERROR': 'badge-danger', 'ESTOP': 'badge-danger', 'PAUSED': 'badge-warning'
      }[data.cycle.state] || 'badge-info';
      document.getElementById('cycleState').className = 'badge ' + stateClass;

      const holes = data.cycle.holes_completed;
      const total = data.cycle.total_holes;
      document.getElementById('holesProgress').textContent = `${holes} / ${total}`;
      const pct = total > 0 ? (holes / total * 100) : 0;
      document.getElementById('progressBar').style.width = pct + '%';
    }

  } catch (e) {
    document.getElementById('connectionStatus').className = 'status-badge error';
    document.getElementById('connectionStatus').textContent = "Від'єднано";
  }
}

async function setRelay(name, state) {
  try {
    await api(`/api/relays/${name}`, 'POST', { state });
    log(`Реле ${getRelayName(name)}: ${state === 'on' ? 'ВКЛ' : 'ВИКЛ'}`);
    refreshStatus();
  } catch (e) {
    log('Помилка: ' + e.message);
  }
}

async function pulseRelay(name) {
  const inputEl = document.getElementById('pulse_' + name);
  const durationMs = parseInt(inputEl.value) || 500;
  const durationSec = durationMs / 1000;
  try {
    log(`Імпульс ${getRelayName(name)}: ${durationMs} мс...`);
    await api(`/api/relays/${name}`, 'POST', { state: 'pulse', duration: durationSec });
    log(`Імпульс ${getRelayName(name)}: завершено`);
    refreshStatus();
  } catch (e) {
    log('Помилка: ' + e.message);
  }
}

async function allRelaysOff() {
  try {
    await api('/api/relays/all/off', 'POST');
    log('Всі реле ВИКЛ');
    refreshStatus();
  } catch (e) {
    log('Помилка: ' + e.message);
  }
}

async function jog(dx, dy) {
  const step = parseFloat(document.getElementById('jogStep').value) || 10;
  try {
    await api('/api/xy/jog', 'POST', { dx: dx * step / 10, dy: dy * step / 10, feed: 1000 });
    refreshStatus();
  } catch (e) {
    log('Jog error: ' + e.message);
  }
}

async function homeXY() {
  try {
    log('Homing XY (Y first, then X)...');
    await api('/api/xy/home', 'POST');
    log('Homing complete');
    refreshStatus();
  } catch (e) {
    log('Home error: ' + e.message);
  }
}

async function homeX() {
  try {
    log('Homing X axis...');
    await api('/api/xy/home/x', 'POST');
    log('X axis homed');
    refreshStatus();
  } catch (e) {
    log('Home X error: ' + e.message);
  }
}

async function homeY() {
  try {
    log('Homing Y axis...');
    await api('/api/xy/home/y', 'POST');
    log('Y axis homed');
    refreshStatus();
  } catch (e) {
    log('Home Y error: ' + e.message);
  }
}

async function goToZero() {
  // ZERO now works same as HOME
  return homeXY();
}

async function connectXY() {
  try {
    await api('/api/xy/connect', 'POST');
    log('XY Table connected');
    refreshStatus();
  } catch (e) {
    log('Connect error: ' + e.message);
  }
}

async function manualMove() {
  const x = parseFloat(document.getElementById('moveX').value);
  const y = parseFloat(document.getElementById('moveY').value);
  const feed = parseFloat(document.getElementById('moveFeed').value);
  try {
    await api('/api/xy/move', 'POST', { x, y, feed });
    log(`Moved to X:${x} Y:${y}`);
    refreshStatus();
  } catch (e) {
    log('Move error: ' + e.message);
  }
}

// Cycle control
document.getElementById('btnStart').onclick = async () => {
  const device = document.getElementById('deviceSelect').value;
  if (!device) { alert('Select a device first'); return; }
  try {
    await api('/api/cycle/start', 'POST', { device });
    log('Cycle started: ' + device);
    refreshStatus();
  } catch (e) {
    log('Start error: ' + e.message);
  }
};

document.getElementById('btnPause').onclick = async () => {
  await api('/api/cycle/pause', 'POST');
  log('Cycle paused');
  refreshStatus();
};

document.getElementById('btnStop').onclick = async () => {
  await api('/api/cycle/stop', 'POST');
  log('Cycle stopped');
  refreshStatus();
};

document.getElementById('btnEstop').onclick = async () => {
  await api('/api/cycle/estop', 'POST');
  log('E-STOP ACTIVATED');
  refreshStatus();
};

document.getElementById('btnClearEstop').onclick = async () => {
  await api('/api/cycle/clear_estop', 'POST');
  log('E-STOP cleared');
  refreshStatus();
};

// Initialize
window.addEventListener('load', async () => {
  await loadDevices();
  await refreshStatus();
  setInterval(refreshStatus, 1000);
  log('Панель ініціалізовано');
});
</script>

</body>
</html>
"""


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
