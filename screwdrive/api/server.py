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
import io
import yaml
import threading
import time
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import asdict

from flask import Flask, jsonify, request, Response, render_template, send_from_directory, send_file, redirect, url_for, session
from flask_cors import CORS
from datetime import timedelta

from core import (
    GPIOController, RelayController, SensorController,
    XYTableController, CycleStateMachine, CycleState
)
from core.xy_table import XYTableMode
from core.state_machine import DeviceProgram, ProgramStep
from core.scanner import BarcodeScanner
from core.camera import USBCamera
from core.usb_storage import USBStorage

# Import authentication module
from api.auth import (
    get_secret_key, authenticate_user, login_user, logout_user,
    is_logged_in, get_current_user, login_required, admin_required,
    get_user_tabs, get_all_users, create_user, update_user, delete_user,
    get_available_tabs, load_auth_config
)

# Import logging module
from api.logger import (
    get_logger, LogCategory, LogLevel, log_exception,
    get_log_categories, get_log_levels
)


class EstopMonitor:
    """
    Background monitor for physical E-STOP button.

    Polls the emergency_stop sensor and triggers XY table estop/clear
    commands immediately when button state changes.
    """

    def __init__(self, sensors: SensorController, xy_table: XYTableController,
                 cycle: Optional[CycleStateMachine] = None,
                 relays: Optional[RelayController] = None,
                 poll_interval: float = 0.05):
        """
        Initialize E-STOP monitor.

        Args:
            sensors: Sensor controller to read E-STOP button
            xy_table: XY table controller to send estop/clear commands
            cycle: Optional cycle state machine for cycle estop
            relays: Optional relay controller to save/restore brake states
            poll_interval: Polling interval in seconds (default 50ms)
        """
        self._sensors = sensors
        self._xy_table = xy_table
        self._cycle = cycle
        self._relays = relays
        self._poll_interval = poll_interval
        self._last_state: Optional[bool] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Saved brake states (to restore after E-STOP cleared)
        self._saved_brake_x: Optional[bool] = None
        self._saved_brake_y: Optional[bool] = None

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
                    syslog = get_logger()
                    if estop_pressed:
                        # Button pressed - save brake states and trigger E-STOP
                        # NOTE: XY table (Slave Pi) has its own GPIO monitoring for E-STOP
                        # so we don't send M112 here - it handles E-STOP directly via GPIO
                        syslog.sensor("АВАРІЙНА ЗУПИНКА НАТИСНУТА", level=LogLevel.CRITICAL, source="e-stop")
                        print("E-STOP BUTTON PRESSED - Slave Pi handles via GPIO")

                        # Save current brake states before E-STOP
                        if self._relays:
                            try:
                                states = self._relays.get_all_states()
                                self._saved_brake_x = states.get('r02_brake_x') == 'ON'
                                self._saved_brake_y = states.get('r03_brake_y') == 'ON'
                                syslog.relay(f"Збережено стан гальм: X={self._saved_brake_x}, Y={self._saved_brake_y}", source="e-stop")
                            except Exception as e:
                                syslog.error(LogCategory.RELAY, f"Не вдалося зберегти стан гальм: {e}", source="e-stop")

                        # Only handle cycle E-STOP on Master side
                        if self._cycle:
                            self._cycle.emergency_stop()
                            syslog.cycle("Цикл зупинено через E-STOP", level=LogLevel.WARNING, source="e-stop")
                    else:
                        # Button released - restore brake states
                        # NOTE: XY table (Slave Pi) auto-clears E-STOP when GPIO shows button released
                        syslog.sensor("Аварійна зупинка відпущена", level=LogLevel.WARNING, source="e-stop")
                        print("E-STOP BUTTON RELEASED - Slave Pi auto-clears via GPIO")

                        # Only handle cycle clear on Master side
                        if self._cycle:
                            self._cycle.clear_estop()

                        # Restore brake states after E-STOP cleared
                        if self._relays:
                            try:
                                if self._saved_brake_x is not None:
                                    if self._saved_brake_x:
                                        self._relays.turn_on('r02_brake_x')
                                    else:
                                        self._relays.turn_off('r02_brake_x')
                                    syslog.relay(f"Відновлено гальмо X: {'ON' if self._saved_brake_x else 'OFF'}", source="e-stop")

                                if self._saved_brake_y is not None:
                                    if self._saved_brake_y:
                                        self._relays.turn_on('r03_brake_y')
                                    else:
                                        self._relays.turn_off('r03_brake_y')
                                    syslog.relay(f"Відновлено гальмо Y: {'ON' if self._saved_brake_y else 'OFF'}", source="e-stop")
                            except Exception as e:
                                syslog.error(LogCategory.RELAY, f"Не вдалося відновити стан гальм: {e}", source="e-stop")

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
    CORS(app, supports_credentials=True)

    # Configure session
    app.secret_key = get_secret_key()
    auth_config = load_auth_config()
    session_config = auth_config.get("session", {})
    app.permanent_session_lifetime = timedelta(minutes=session_config.get("timeout_minutes", 480))

    # Store instances in app context
    app.gpio = gpio
    app.relays = relays
    app.sensors = sensors
    app.xy_table = xy_table
    app.cycle = cycle
    app.config_data = config or {}
    app.devices = {}
    app.device_groups = []  # List of device group names
    app.fixtures = {}  # Fixture (оснастка) programs: key -> dict
    app.global_cycle_count = _load_global_cycles()  # Total devices screwed (persisted to file)

    # Pedal latch: captures short presses between slow UI polls
    app.pedal_latch = False
    # Start as True so GPIO startup transients don't cause a false rising edge
    app._pedal_prev = True
    app._pedal_lock = threading.Lock()

    def _pedal_monitor():
        """Fast-poll pedal GPIO (20ms) and latch rising edge."""
        import time as _t
        # Let GPIO settle before monitoring
        _t.sleep(0.5)
        # Sync to actual state before detecting edges
        if app.sensors:
            try:
                with app._pedal_lock:
                    app._pedal_prev = app.sensors.is_active('ped_start')
            except Exception:
                pass
        while True:
            if app.sensors:
                try:
                    pressed = app.sensors.is_active('ped_start')
                    with app._pedal_lock:
                        if pressed and not app._pedal_prev:
                            app.pedal_latch = True  # rising edge
                        app._pedal_prev = pressed
                except Exception:
                    pass
            _t.sleep(0.02)

    _pedal_thread = threading.Thread(target=_pedal_monitor, daemon=True)
    _pedal_thread.start()
    app.cycle_history = _load_cycle_history()  # Cycle history records

    # Initialize logger
    syslog = get_logger()
    syslog.system("Сервер запускається...", source="server")

    # Load devices configuration
    _load_devices(app)

    # Load fixtures configuration
    _load_fixtures(app)

    # Start barcode scanner reader
    scanner_path = '/dev/input/by-id/usb-Symbol_Technologies__Inc__2008_Symbol_Bar_Code_Scanner::EA_25048525100165-event-kbd'
    app.scanner = BarcodeScanner(scanner_path)
    app.scanner.start()
    syslog.system(f"Сканер штрих-кодів: {scanner_path}", source="server")

    # Start USB camera
    recordings_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'recordings')
    app.camera = USBCamera(device_index=-1, recordings_dir=recordings_dir)
    app.camera.start()
    syslog.system("USB камера ініціалізована (auto-detect)", source="server")

    # USB Storage for recordings — mount at service start
    app.usb_storage = USBStorage()
    usb_rec = app.usb_storage.recordings_dir  # already mounted?
    if not usb_rec:
        # Try to auto-mount
        usb_dev = app.usb_storage.detect()
        if usb_dev:
            syslog.system(f"USB знайдено ({usb_dev['device']}), монтування...", source="server")
            mount_result = app.usb_storage.mount(usb_dev['device'])
            if mount_result.get('status') in ('mounted', 'already_mounted'):
                usb_rec = app.usb_storage.recordings_dir

    if usb_rec:
        app.camera.set_recordings_dir(usb_rec, allow_recording=True)
        syslog.system(f"USB-накопичувач готовий, записи → {usb_rec}", source="server")
    else:
        # No USB — block recording to protect system drive
        app.camera._recording_allowed = False
        syslog.system("USB-накопичувач не знайдено — ЗАПИС ВИМКНЕНО (захист системної флешки)",
                      source="server", level=LogLevel.WARNING)

    # Start E-STOP hardware monitor (polls every 50ms for immediate response)
    app.estop_monitor = None
    if sensors and xy_table:
        app.estop_monitor = EstopMonitor(sensors, xy_table, cycle, relays, poll_interval=0.05)
        app.estop_monitor.start()

    # === Web UI Routes ===

    @app.route('/login')
    def login_page():
        """Serve login page."""
        if is_logged_in():
            return redirect(url_for('index'))
        return render_template('login.html')

    @app.route('/')
    @login_required
    def index():
        """Serve main Web UI page."""
        user = get_current_user()
        return render_template('index.html')

    @app.route('/static/<path:filename>')
    def serve_static(filename):
        """Serve static files."""
        return send_from_directory(app.static_folder, filename)

    # === Authentication API ===

    @app.route('/api/auth/login', methods=['POST'])
    def api_login():
        """Login API endpoint."""
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'success': False, 'error': 'Введіть логін та пароль'}), 400

        user = authenticate_user(username, password)
        if user:
            login_user(user)
            syslog.auth(f"Успішний вхід: {username}", source="login", details={"role": user['role']})
            return jsonify({
                'success': True,
                'user': {
                    'username': user['username'],
                    'role': user['role'],
                    'allowed_tabs': user['allowed_tabs']
                }
            })
        else:
            syslog.auth(f"Невдала спроба входу: {username}", level=LogLevel.WARNING, source="login")
            return jsonify({'success': False, 'error': 'Невірний логін або пароль'}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def api_logout():
        """Logout API endpoint."""
        user = get_current_user()
        if user:
            syslog.auth(f"Вихід: {user['username']}", source="logout")
        logout_user()
        return jsonify({'success': True})

    @app.route('/api/auth/status', methods=['GET'])
    def api_auth_status():
        """Get current authentication status."""
        user = get_current_user()
        if user:
            return jsonify({
                'logged_in': True,
                'user': {
                    'username': user['username'],
                    'role': user['role'],
                    'allowed_tabs': user['allowed_tabs']
                }
            })
        else:
            return jsonify({'logged_in': False})

    # === Admin User Management API ===

    @app.route('/api/admin/users', methods=['GET'])
    @admin_required
    def api_get_users():
        """Get all users (admin only)."""
        users = get_all_users()
        available = get_available_tabs()
        return jsonify({'users': users, 'available_tabs': available})

    @app.route('/api/admin/users', methods=['POST'])
    @admin_required
    def api_create_user():
        """Create new user (admin only)."""
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        role = data.get('role', 'user')
        allowed_tabs = data.get('allowed_tabs', ['status'])

        if not username or not password:
            return jsonify({'success': False, 'error': 'Логін та пароль обов\'язкові'}), 400

        if len(password) < 4:
            return jsonify({'success': False, 'error': 'Пароль має бути не менше 4 символів'}), 400

        if create_user(username, password, role, allowed_tabs):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Користувач вже існує'}), 400

    @app.route('/api/admin/users/<username>', methods=['PUT'])
    @admin_required
    def api_update_user(username):
        """Update user (admin only)."""
        data = request.get_json() or {}
        password = data.get('password')
        role = data.get('role')
        allowed_tabs = data.get('allowed_tabs')

        # Don't allow empty password if provided
        if password is not None and len(password) > 0 and len(password) < 4:
            return jsonify({'success': False, 'error': 'Пароль має бути не менше 4 символів'}), 400

        # Only update password if non-empty string provided
        pwd = password if password and len(password) >= 4 else None

        if update_user(username, pwd, role, allowed_tabs):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Користувача не знайдено'}), 404

    @app.route('/api/admin/users/<username>', methods=['DELETE'])
    @admin_required
    def api_delete_user(username):
        """Delete user (admin only)."""
        current = get_current_user()
        if current and current['username'] == username:
            return jsonify({'success': False, 'error': 'Не можна видалити себе'}), 400

        if delete_user(username):
            syslog.auth(f"Користувача '{username}' видалено", source="admin")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Не вдалося видалити користувача'}), 400

    # === Logging API ===

    @app.route('/api/logs', methods=['GET'])
    @login_required
    def api_get_logs():
        """Get logs with optional filters. Supports multiple categories via comma-separated list."""
        level = request.args.get('level')
        category = request.args.get('category')
        categories = request.args.get('categories')  # comma-separated list
        since_id = request.args.get('since_id', type=int)
        search = request.args.get('search')
        limit = request.args.get('limit', 500, type=int)

        # Support multiple categories
        if categories:
            cat_list = [c.strip() for c in categories.split(',')]
            all_logs = []
            for cat in cat_list:
                try:
                    logs = syslog.get_logs(level, cat, since_id, search, min(limit, 1000))
                    all_logs.extend(logs)
                except Exception:
                    pass  # Skip invalid categories
            # Sort by timestamp descending and limit
            all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return jsonify({'logs': all_logs[:limit]})
        else:
            logs = syslog.get_logs(level, category, since_id, search, min(limit, 1000))
            return jsonify({'logs': logs})

    @app.route('/api/logs/categories', methods=['GET'])
    @login_required
    def api_get_log_categories():
        """Get available log categories."""
        return jsonify({'categories': get_log_categories()})

    @app.route('/api/logs/levels', methods=['GET'])
    @login_required
    def api_get_log_levels():
        """Get available log levels."""
        return jsonify({'levels': get_log_levels()})

    @app.route('/api/logs/stats', methods=['GET'])
    @login_required
    def api_get_log_stats():
        """Get logging statistics."""
        return jsonify(syslog.get_stats())

    @app.route('/api/logs/clear', methods=['POST'])
    @admin_required
    def api_clear_logs():
        """Clear log buffer (admin only)."""
        syslog.clear()
        syslog.system("Буфер логів очищено", source="admin")
        return jsonify({'success': True})

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

        sensor_states = app.sensors.get_all_states() if app.sensors else {}

        # Inject latched pedal press (captures short presses between polls)
        with app._pedal_lock:
            if app.pedal_latch:
                sensor_states['ped_start'] = 'ACTIVE'
                app.pedal_latch = False

        result = {
            'relays': app.relays.get_all_states() if app.relays else {},
            'sensors': sensor_states,
            'xy_table': xy_status,
            'cycle': None,
            'global_cycle_count': app.global_cycle_count
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
            new_state = app.relays.get_state(name).name
            syslog.relay(f"Реле {name}: {state} -> {new_state}", source="api", details={"relay": name, "action": state})
            return jsonify({'name': name, 'state': new_state})
        else:
            syslog.relay(f"Помилка керування реле {name}", level=LogLevel.ERROR, source="api")
            return jsonify({'error': f'Failed to set relay {name}'}), 500

    @app.route('/api/relays/all/off', methods=['POST'])
    def all_relays_off():
        """Turn all relays off."""
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503
        app.relays.all_off()
        syslog.relay("Всі реле вимкнено", source="api")
        return jsonify({'status': 'ok'})

    # === Motor Driver Alarm Reset ===

    @app.route('/api/drivers/reset', methods=['POST'])
    def reset_driver_alarms():
        """
        Reset motor driver alarms by power cycling the drivers.

        If axis is specified ('x' or 'y'), reset only that axis.
        If no axis specified, reset both.

        Power cycle: turn relay ON (power OFF) for 700ms, then OFF (power ON).
        """
        if not app.relays:
            return jsonify({'error': 'Relays not initialized'}), 503

        data = request.get_json() or {}
        axis = data.get('axis')  # 'x', 'y', or None for both
        reset_results = []

        import time

        if axis in (None, 'x', 'X'):
            # Reset X axis driver - R09_PWR_X
            # Relay ON = power OFF, wait 700ms, Relay OFF = power ON
            app.relays.on('r09_pwr_x')  # Power OFF
            time.sleep(0.7)              # Wait 700ms
            app.relays.off('r09_pwr_x') # Power ON
            reset_results.append({'axis': 'x', 'status': 'reset', 'relay': 'r09_pwr_x'})

        if axis in (None, 'y', 'Y'):
            # Reset Y axis driver - R10_PWR_Y
            app.relays.on('r10_pwr_y')  # Power OFF
            time.sleep(0.7)              # Wait 700ms
            app.relays.off('r10_pwr_y') # Power ON
            reset_results.append({'axis': 'y', 'status': 'reset', 'relay': 'r10_pwr_y'})

        # Wait for drivers to initialize
        time.sleep(0.5)

        return jsonify({
            'status': 'ok',
            'message': 'Driver alarms reset by power cycle',
            'reset': reset_results
        })

    @app.route('/api/drivers/status', methods=['GET'])
    def get_driver_status():
        """
        Get motor driver alarm status.

        Returns:
            alarm_x: True if X driver alarm is active
            alarm_y: True if Y driver alarm is active
            power_x: True if X driver has power (relay OFF)
            power_y: True if Y driver has power (relay OFF)
        """
        result = {
            'alarm_x': False,
            'alarm_y': False,
            'power_x': True,
            'power_y': True
        }

        if app.sensors:
            sensors_state = app.sensors.get_all_states()
            result['alarm_x'] = sensors_state.get('alarm_x') == 'ACTIVE'
            result['alarm_y'] = sensors_state.get('alarm_y') == 'ACTIVE'

        if app.relays:
            # Relay OFF = power ON (inverted logic)
            result['power_x'] = app.relays.get_state('r09_pwr_x').name == 'OFF'
            result['power_y'] = app.relays.get_state('r10_pwr_y').name == 'OFF'

        return jsonify(result)

    # === Legacy API Compatibility (for old touchdesk.py) ===

    # Shared UI state (synchronized between Web UI and Desktop UI)
    # This is the SINGLE SOURCE OF TRUTH - both UIs should mirror this state
    app.ui_state = {
        'selected_device': None,
        'cycle_state': 'IDLE',  # IDLE, INITIALIZING, READY, RUNNING, STOPPED, ERROR, E-STOP, INIT_ERROR
        'initialized': False,
        'holes_completed': 0,
        'total_holes': 0,
        'cycles_completed': 0,
        'message': '',
        'progress_percent': 0,      # 0-100 progress for init/cycle
        'current_step': '',         # Current operation step description
        'operator': None,           # Who is currently operating: 'web', 'desktop', or None
        'operation_started_at': 0,  # Timestamp when current operation started
        'updated_by': None,         # 'web' or 'desktop' - who last updated
        'updated_at': 0             # Timestamp of last update
    }

    # Legacy compatibility
    app.selected_device = None

    # === Shared UI State API ===

    @app.route('/api/ui/state', methods=['GET'])
    def get_ui_state():
        """Get shared UI state for synchronization between Web and Desktop UI."""
        return jsonify(app.ui_state)

    @app.route('/api/ui/state', methods=['POST'])
    def set_ui_state():
        """Update shared UI state (partial updates supported)."""
        data = request.get_json() or {}

        # Update only provided fields
        allowed_fields = ['selected_device', 'cycle_state', 'initialized', 'holes_completed',
                         'total_holes', 'cycles_completed', 'message', 'progress_percent',
                         'current_step', 'operator', 'operation_started_at']
        for key in allowed_fields:
            if key in data:
                app.ui_state[key] = data[key]

        # Track who updated and when
        app.ui_state['updated_by'] = data.get('source', 'unknown')
        app.ui_state['updated_at'] = time.time()

        # Auto-set operator when starting an operation
        if data.get('cycle_state') in ('INITIALIZING', 'RUNNING'):
            if not app.ui_state['operator']:
                app.ui_state['operator'] = data.get('source', 'unknown')
                app.ui_state['operation_started_at'] = time.time()

        # Clear operator when operation ends
        if data.get('cycle_state') in ('IDLE', 'READY', 'STOPPED', 'ERROR', 'E-STOP', 'INIT_ERROR', 'COMPLETED'):
            app.ui_state['operator'] = None
            app.ui_state['operation_started_at'] = 0

        # Keep legacy selected_device in sync
        if 'selected_device' in data:
            app.selected_device = data['selected_device']

        return jsonify(app.ui_state)

    @app.route('/api/ui/select-device', methods=['POST'])
    def ui_select_device():
        """Select device and sync across all UIs."""
        data = request.get_json() or {}
        device_key = data.get('device')

        if device_key and device_key not in app.devices:
            return jsonify({'error': f'Unknown device: {device_key}'}), 404

        app.ui_state['selected_device'] = device_key
        app.ui_state['cycle_state'] = 'IDLE'
        app.ui_state['initialized'] = False
        app.ui_state['holes_completed'] = 0
        app.ui_state['progress_percent'] = 0
        app.ui_state['current_step'] = ''
        app.ui_state['operator'] = None
        app.ui_state['operation_started_at'] = 0
        app.ui_state['message'] = f'Девайс {device_key} вибрано' if device_key else ''
        app.ui_state['updated_by'] = data.get('source', 'unknown')
        app.ui_state['updated_at'] = time.time()

        # Get total holes from device
        if device_key and device_key in app.devices:
            app.ui_state['total_holes'] = app.devices[device_key].holes

        # Legacy sync
        app.selected_device = device_key

        return jsonify(app.ui_state)

    # === Global Cycle Counter (persisted to file) ===

    @app.route('/api/stats/global_cycles', methods=['GET'])
    def get_global_cycles():
        """Get total number of completed cycles."""
        return jsonify({'global_cycle_count': app.global_cycle_count})

    @app.route('/api/stats/global_cycles/increment', methods=['POST'])
    def increment_global_cycles():
        """Increment global cycle counter by 1 and persist to disk."""
        app.global_cycle_count += 1
        _save_global_cycles(app.global_cycle_count)
        return jsonify({'global_cycle_count': app.global_cycle_count})

    # === Cycle History API ===

    @app.route('/api/stats/history', methods=['GET'])
    def get_cycle_history():
        """Get cycle history records."""
        return jsonify({'history': app.cycle_history})

    @app.route('/api/stats/history', methods=['POST'])
    def add_cycle_history():
        """Add a cycle history record. JSON fields:
        device, device_name, group, screws, total_screws, cycle_time, status, video_file,
        what, screw_size, torque, task, fixture
        """
        data = request.get_json(silent=True) or {}
        from datetime import datetime as _dt
        record = {
            'id': len(app.cycle_history) + 1,
            'timestamp': _dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            'device': data.get('device', ''),
            'device_name': data.get('device_name', ''),
            'group': data.get('group', ''),
            'what': data.get('what', ''),
            'screw_size': data.get('screw_size', ''),
            'torque': data.get('torque', ''),
            'task': data.get('task', ''),
            'fixture': data.get('fixture', ''),
            'screws': data.get('screws', 0),
            'total_screws': data.get('total_screws', 0),
            'cycle_time': round(data.get('cycle_time', 0), 1),
            'status': data.get('status', 'unknown'),
            'video_file': data.get('video_file', ''),
        }
        app.cycle_history.append(record)
        _save_cycle_history(app.cycle_history)
        return jsonify({'status': 'ok', 'record': record})

    # === Device Groups API ===

    @app.route('/api/device-groups', methods=['GET'])
    def get_device_groups():
        """Get list of device groups."""
        return jsonify({'groups': app.device_groups})

    @app.route('/api/device-groups', methods=['POST'])
    def create_device_group():
        """Create a new device group."""
        data = request.get_json() or {}
        name = data.get('name', '').strip()

        if not name:
            return jsonify({'error': 'Group name is required'}), 400

        if name in app.device_groups:
            return jsonify({'error': f'Group "{name}" already exists'}), 400

        app.device_groups.append(name)
        _save_devices(app)
        return jsonify({'success': True, 'groups': app.device_groups})

    @app.route('/api/device-groups/<name>', methods=['PUT'])
    def update_device_group(name):
        """Rename a device group."""
        if name not in app.device_groups:
            return jsonify({'error': f'Group "{name}" not found'}), 404

        data = request.get_json() or {}
        new_name = data.get('name', '').strip()
        if not new_name:
            return jsonify({'error': 'New group name is required'}), 400
        if new_name != name and new_name in app.device_groups:
            return jsonify({'error': f'Group "{new_name}" already exists'}), 400

        # Rename in group list
        idx = app.device_groups.index(name)
        app.device_groups[idx] = new_name

        # Update devices that reference this group
        for key, prog in app.devices.items():
            if prog.group == name:
                prog.group = new_name
        _save_devices(app)
        return jsonify({'success': True, 'groups': app.device_groups})

    @app.route('/api/device-groups/<name>/devices', methods=['GET'])
    def get_device_group_devices(name):
        """Get devices in a group and devices not in any group."""
        if name not in app.device_groups:
            return jsonify({'error': f'Group "{name}" not found'}), 404

        in_group = []
        available = []
        for key, prog in app.devices.items():
            dev = {'key': key, 'name': prog.name}
            if prog.group == name:
                in_group.append(dev)
            elif not prog.group:
                available.append(dev)
        return jsonify({'in_group': in_group, 'available': available})

    @app.route('/api/device-groups/<name>/devices', methods=['POST'])
    def add_device_to_group(name):
        """Add a device to a group."""
        if name not in app.device_groups:
            return jsonify({'error': f'Group "{name}" not found'}), 404
        data = request.get_json() or {}
        device_key = data.get('device_key', '').strip()
        if not device_key or device_key not in app.devices:
            return jsonify({'error': 'Invalid device key'}), 400
        app.devices[device_key].group = name
        _save_devices(app)
        return jsonify({'success': True})

    @app.route('/api/device-groups/<name>/devices/<device_key>', methods=['DELETE'])
    def remove_device_from_group(name, device_key):
        """Remove a device from a group."""
        if device_key not in app.devices:
            return jsonify({'error': 'Device not found'}), 404
        if app.devices[device_key].group == name:
            app.devices[device_key].group = ""
            _save_devices(app)
        return jsonify({'success': True})

    @app.route('/api/device-groups/<name>', methods=['DELETE'])
    def delete_device_group(name):
        """Delete a device group."""
        if name not in app.device_groups:
            return jsonify({'error': f'Group "{name}" not found'}), 404

        app.device_groups.remove(name)
        # Clear group from devices that used it
        for key, prog in app.devices.items():
            if prog.group == name:
                prog.group = ""
        _save_devices(app)
        return jsonify({'success': True, 'groups': app.device_groups})

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

    # === Work Offset API (G92-like) ===

    @app.route('/api/offsets', methods=['GET'])
    def get_offsets():
        """Get current work offsets."""
        offsets = _load_offsets()
        return jsonify(offsets)

    @app.route('/api/offsets', methods=['POST'])
    def set_offsets():
        """Set work offsets."""
        data = request.get_json() or {}
        x = data.get('x')
        y = data.get('y')

        if x is None and y is None:
            return jsonify({'error': 'x or y required'}), 400

        try:
            offsets = _load_offsets()
            if x is not None:
                offsets['x'] = float(x)
            if y is not None:
                offsets['y'] = float(y)
            _save_offsets(offsets)
            return jsonify({'success': True, 'offsets': offsets})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/offsets/set-current', methods=['POST'])
    def set_current_position_as_offset():
        """Set current XY position as work offset (like G92)."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        try:
            # Get current physical position
            x = app.xy_table.x
            y = app.xy_table.y

            if x is None or y is None:
                return jsonify({'error': 'Position unknown - home first'}), 400

            offsets = {'x': float(x), 'y': float(y)}
            _save_offsets(offsets)
            return jsonify({'success': True, 'offsets': offsets})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

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
                    # Resolve coord_source
                    if program.coord_source and program.coord_source in app.devices:
                        from copy import copy
                        program = copy(program)
                        program.steps = app.devices[program.coord_source].steps
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
            'estop_pressed': app.sensors.is_emergency_stop_pressed(),
            'area_blocked': app.sensors.is_area_blocked()
        })

    # === XY Table Control ===

    @app.route('/api/xy/ping', methods=['POST'])
    def xy_ping():
        """Send PING to slave, expect PONG. Verifies serial link is alive."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized', 'pong': False}), 503
        ok = app.xy_table.ping()
        return jsonify({'pong': ok})

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
            syslog.xy("XY table не ініціалізовано", level=LogLevel.ERROR, source="api")
            return jsonify({'error': 'XY table not initialized'}), 503
        syslog.xy("Підключення до Slave...", source="api")
        if app.xy_table.connect():
            syslog.xy("Підключено до Slave успішно", source="api")
            return jsonify({'status': 'connected'})
        syslog.xy("Не вдалося підключитися до Slave", level=LogLevel.ERROR, source="api")
        return jsonify({'error': 'Connection failed'}), 500

    @app.route('/api/xy/disconnect', methods=['POST'])
    def xy_disconnect():
        """Disconnect from XY table."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        syslog.xy("Відключення від Slave", source="api")
        app.xy_table.disconnect()
        return jsonify({'status': 'disconnected'})

    @app.route('/api/xy/restart-service', methods=['POST'])
    def xy_restart_service():
        """Restart xy_table.service on slave Raspberry Pi via SSH."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        syslog.xy("Перезапуск сервісу Slave...", source="api")
        if app.xy_table.restart_slave_service():
            syslog.xy("Сервіс Slave перезапущено успішно", source="api")
            return jsonify({'status': 'service restarted'})
        syslog.xy("Не вдалося перезапустити сервіс Slave", level=LogLevel.ERROR, source="api")
        return jsonify({'error': 'Failed to restart service'}), 500

    @app.route('/api/xy/home', methods=['POST'])
    def xy_home():
        """Home XY table (all axes: Y first, then X)."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        try:
            data = request.get_json(silent=True) or {}
            axis = data.get('axis')  # None for both axes

            axis_name = axis or 'all'
            syslog.xy(f"Хомінг запущено: {axis_name}", source="api", details={"axis": axis_name})
            if app.xy_table.home(axis):
                syslog.xy(f"Хомінг завершено: {axis_name}", source="api")
                return jsonify({'status': 'homed', 'axis': axis_name})
            syslog.xy(f"Хомінг не вдався: {axis_name}", level=LogLevel.ERROR, source="api")
            return jsonify({'error': 'Homing failed'}), 500
        except Exception as e:
            syslog.error(LogCategory.XY, f"Помилка хомінгу: {e}", source="api")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/xy/home/x', methods=['POST'])
    def xy_home_x():
        """Home X axis only."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        syslog.xy("Хомінг X запущено", source="api")
        if app.xy_table.home_x():
            syslog.xy("Хомінг X завершено", source="api")
            return jsonify({'status': 'homed', 'axis': 'X'})
        syslog.xy("Хомінг X не вдався", level=LogLevel.ERROR, source="api")
        return jsonify({'error': 'Homing X failed'}), 500

    @app.route('/api/xy/home/y', methods=['POST'])
    def xy_home_y():
        """Home Y axis only."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        syslog.xy("Хомінг Y запущено", source="api")
        if app.xy_table.home_y():
            syslog.xy("Хомінг Y завершено", source="api")
            return jsonify({'status': 'homed', 'axis': 'Y'})
        syslog.xy("Хомінг Y не вдався", level=LogLevel.ERROR, source="api")
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

        try:
            syslog.xy(f"Рух до X={x}, Y={y}, Feed={feed}", source="api", details={"x": x, "y": y, "feed": feed})
            if app.xy_table.move_to(x, y, feed):
                syslog.xy(f"Рух завершено: X={app.xy_table.x}, Y={app.xy_table.y}", source="api")
                return jsonify({
                    'status': 'ok',
                    'position': {'x': app.xy_table.x, 'y': app.xy_table.y}
                })
            syslog.xy(f"Рух не вдався: {app.xy_table._health.last_error}", level=LogLevel.ERROR, source="api")
            return jsonify({'error': 'Move failed', 'details': app.xy_table._health.last_error}), 500
        except Exception as e:
            syslog.error(LogCategory.XY, f"Помилка руху: {e}", source="api")
            return jsonify({'error': f'Move exception: {str(e)}'}), 500

    @app.route('/api/xy/move_seq', methods=['POST'])
    def xy_move_sequential():
        """Move XY table: X axis first, then Y axis (sequential)."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503

        data = request.get_json() or {}
        x = data.get('x')
        y = data.get('y')
        feed = data.get('feed', 10000.0)

        if x is None and y is None:
            return jsonify({'error': 'x or y required'}), 400

        try:
            syslog.xy(f"Послідовний рух: X={x}, Y={y}, Feed={feed}", source="api",
                       details={"x": x, "y": y, "feed": feed})

            # Step 1: Move X only
            if x is not None:
                if not app.xy_table.move_to(x, None, feed):
                    syslog.xy(f"Рух X не вдався: {app.xy_table._health.last_error}",
                              level=LogLevel.ERROR, source="api")
                    return jsonify({'error': 'Move X failed',
                                    'details': app.xy_table._health.last_error}), 500

            # Step 2: Move Y only
            if y is not None:
                if not app.xy_table.move_to(None, y, feed):
                    syslog.xy(f"Рух Y не вдався: {app.xy_table._health.last_error}",
                              level=LogLevel.ERROR, source="api")
                    return jsonify({'error': 'Move Y failed',
                                    'details': app.xy_table._health.last_error}), 500

            syslog.xy(f"Послідовний рух завершено: X={app.xy_table.x}, Y={app.xy_table.y}",
                       source="api")
            return jsonify({
                'status': 'ok',
                'position': {'x': app.xy_table.x, 'y': app.xy_table.y}
            })
        except Exception as e:
            syslog.error(LogCategory.XY, f"Помилка послідовного руху: {e}", source="api")
            return jsonify({'error': f'Move exception: {str(e)}'}), 500

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
        syslog.xy("E-STOP активовано (API)", level=LogLevel.WARNING, source="api")
        app.xy_table.estop()
        return jsonify({'status': 'estop_active'})

    @app.route('/api/xy/clear_estop', methods=['POST'])
    def xy_clear_estop():
        """Clear XY table E-STOP."""
        if not app.xy_table:
            return jsonify({'error': 'XY table not initialized'}), 503
        if app.xy_table.clear_estop():
            syslog.xy("E-STOP знято", source="api")
            return jsonify({'status': 'estop_cleared'})
        syslog.xy("Не вдалося зняти E-STOP", level=LogLevel.ERROR, source="api")
        return jsonify({'error': 'Clear ESTOP failed'}), 500

    @app.route('/api/xy/cancel', methods=['POST'])
    def xy_cancel():
        """
        Cancel all XY table commands - immediate stop without persistent E-STOP.
        Sends M112 (stop) followed by M999 (clear) for instant halt.
        Returns actual position after stop.
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

        # Wait a bit for xy_cli to update its position
        time.sleep(0.1)

        # Get actual position after cancel
        actual_position = {'x': 0, 'y': 0}
        try:
            # Request current status to get actual position
            app.xy_table.get_status()
            actual_position = {
                'x': app.xy_table.position.x,
                'y': app.xy_table.position.y
            }
        except Exception as e:
            errors.append(f'get_position: {str(e)}')

        # Also stop any running cycle
        try:
            if app.cycle:
                app.cycle.stop()
        except Exception as e:
            errors.append(f'cycle_stop: {str(e)}')

        if errors:
            return jsonify({
                'status': 'cancelled_with_errors',
                'errors': errors,
                'position': actual_position
            })

        return jsonify({
            'status': 'cancelled',
            'position': actual_position
        })

    @app.route('/api/xy/logs', methods=['GET'])
    def xy_logs():
        """Get XY table related logs (XY, COMM, GCODE categories). No auth required for desktop app."""
        limit = request.args.get('limit', 50, type=int)
        since_id = request.args.get('since_id', type=int)

        # Get logs from XY, COMM, and GCODE categories
        all_logs = []
        for cat in ['XY', 'COMM', 'GCODE']:
            try:
                logs = syslog.get_logs(None, cat, since_id, None, min(limit, 200))
                all_logs.extend(logs)
            except Exception:
                pass

        # Sort by timestamp descending and limit
        all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return jsonify({'logs': all_logs[:limit]})

    @app.route('/api/desktop/logs', methods=['GET'])
    def desktop_logs():
        """Get all logs for desktop app. No auth required."""
        level = request.args.get('level')
        category = request.args.get('category')
        since_id = request.args.get('since_id', type=int)
        search = request.args.get('search')
        limit = request.args.get('limit', 100, type=int)

        # Get logs with optional filters
        logs = syslog.get_logs(level, category, since_id, search, min(limit, 500))
        return jsonify({'logs': logs})

    @app.route('/api/desktop/logs/categories', methods=['GET'])
    def desktop_log_categories():
        """Get available log categories. No auth required for desktop app."""
        return jsonify({'categories': get_log_categories()})

    @app.route('/api/desktop/logs/levels', methods=['GET'])
    def desktop_log_levels():
        """Get available log levels. No auth required for desktop app."""
        return jsonify({'levels': get_log_levels()})

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
        # Resolve coord_source: use steps from source device if set
        if program.coord_source and program.coord_source in app.devices:
            from copy import copy
            program = copy(program)
            program.steps = app.devices[program.coord_source].steps
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
        """Get list of available devices with full details."""
        return jsonify([
            {
                'key': key,
                'name': prog.name,
                'holes': prog.holes,
                'what': prog.what,
                'screw_size': prog.screw_size,
                'task': prog.task,
                'torque': prog.torque,
                'work_x': prog.work_x,
                'work_y': prog.work_y,
                'work_feed': prog.work_feed,
                'group': prog.group,
                'fixture': prog.fixture,
                'coord_source': prog.coord_source,
                'steps_count': len(prog.steps)
            }
            for key, prog in app.devices.items()
        ])

    @app.route('/api/devices/<key>', methods=['GET'])
    def get_device(key):
        """Get device program details."""
        if key not in app.devices:
            return jsonify({'error': f'Unknown device: {key}'}), 404

        prog = app.devices[key]
        # Resolve steps from coord_source if set
        steps = prog.steps
        if prog.coord_source and prog.coord_source in app.devices:
            steps = app.devices[prog.coord_source].steps
        return jsonify({
            'key': prog.key,
            'name': prog.name,
            'holes': prog.holes,
            'what': prog.what,
            'screw_size': prog.screw_size,
            'task': prog.task,
            'torque': prog.torque,
            'work_x': prog.work_x,
            'work_y': prog.work_y,
            'work_feed': prog.work_feed,
            'group': prog.group,
            'fixture': prog.fixture,
            'coord_source': prog.coord_source,
            'steps': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'feed': s.feed}
                for s in steps
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
        torque_val = data.get('torque')
        torque = float(torque_val) if torque_val is not None else None
        work_x = data.get('work_x')
        work_y = data.get('work_y')
        work_feed = data.get('work_feed', 5000)
        group = data.get('group', '')
        fixture = data.get('fixture', '')
        coord_source = data.get('coord_source', '')
        steps_data = data.get('steps', [])

        # Convert work position values to float or None
        if work_x is not None:
            work_x = float(work_x)
        if work_y is not None:
            work_y = float(work_y)
        if work_feed is not None:
            work_feed = float(work_feed)

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
            task=task,
            torque=torque,
            work_x=work_x,
            work_y=work_y,
            work_feed=work_feed,
            group=group,
            fixture=fixture,
            coord_source=coord_source
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
        torque_val = data.get('torque', old_dev.torque)
        torque = float(torque_val) if torque_val is not None else None
        group = data.get('group', old_dev.group)
        fixture = data.get('fixture', old_dev.fixture)
        coord_source = data.get('coord_source', old_dev.coord_source)
        steps_data = data.get('steps', None)

        # Handle work position fields
        work_x = data.get('work_x', old_dev.work_x)
        work_y = data.get('work_y', old_dev.work_y)
        work_feed = data.get('work_feed', old_dev.work_feed)

        # Convert work position values to float or None
        if work_x is not None:
            work_x = float(work_x)
        if work_y is not None:
            work_y = float(work_y)
        if work_feed is not None:
            work_feed = float(work_feed)

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
            task=task,
            torque=torque,
            work_x=work_x,
            work_y=work_y,
            work_feed=work_feed,
            group=group,
            fixture=fixture,
            coord_source=coord_source
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

    @app.route('/api/devices/export', methods=['GET'])
    def export_devices():
        """Export all devices, groups and fixtures as YAML file download."""
        devices_list = []
        for key, prog in app.devices.items():
            device_data = {
                'key': prog.key,
                'name': prog.name,
                'holes': prog.holes,
                'what': prog.what,
                'screw_size': prog.screw_size,
                'task': prog.task,
                'torque': prog.torque,
                'work_x': prog.work_x,
                'work_y': prog.work_y,
                'work_feed': prog.work_feed,
                'group': prog.group,
                'fixture': prog.fixture,
                'coord_source': prog.coord_source,
                'program': [
                    {'type': s.step_type, 'x': s.x, 'y': s.y, 'f': s.feed}
                    for s in prog.steps
                ]
            }
            devices_list.append(device_data)

        fixtures_list = []
        for key, fix in app.fixtures.items():
            fixtures_list.append({
                'code': fix.get('code', ''),
                'base_group': fix.get('base_group', ''),
                'qr_code': fix.get('qr_code', ''),
                'scan_x': fix.get('scan_x', 0),
                'scan_y': fix.get('scan_y', 0),
                'scan_feed': fix.get('scan_feed', 50000),
            })

        data = {
            'groups': app.device_groups,
            'devices': devices_list,
            'fixtures': fixtures_list
        }

        yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return Response(
            yaml_str,
            mimetype='application/x-yaml',
            headers={'Content-Disposition': 'attachment; filename=config.yaml'}
        )

    @app.route('/api/devices/import', methods=['POST'])
    def import_devices():
        """Import devices, groups and fixtures from uploaded YAML file (full overwrite)."""
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'Empty filename'}), 400

        try:
            content = file.read().decode('utf-8')
            data = yaml.safe_load(content)
        except Exception as e:
            return jsonify({'error': f'Invalid YAML file: {e}'}), 400

        if not isinstance(data, dict) or 'devices' not in data:
            return jsonify({'error': 'Invalid format: missing "devices" section'}), 400

        # Full overwrite: clear existing data
        app.devices.clear()
        app.device_groups.clear()
        app.fixtures.clear()

        # Import groups
        imported_groups = data.get('groups', [])
        if isinstance(imported_groups, list):
            app.device_groups = [g for g in imported_groups if g]

        # Import devices
        for dev in data.get('devices', []):
            key = dev.get('key', '').strip()
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

            work_x = dev.get('work_x')
            work_y = dev.get('work_y')
            work_feed = dev.get('work_feed', 5000)
            if work_x is not None:
                work_x = float(work_x)
            if work_y is not None:
                work_y = float(work_y)
            if work_feed is not None:
                work_feed = float(work_feed)

            torque_val = dev.get('torque')
            torque = float(torque_val) if torque_val is not None else None

            app.devices[key] = DeviceProgram(
                key=key,
                name=dev.get('name', key),
                holes=int(dev.get('holes', 0)),
                steps=steps,
                what=dev.get('what', ''),
                screw_size=dev.get('screw_size', ''),
                task=dev.get('task', ''),
                torque=torque,
                work_x=work_x,
                work_y=work_y,
                work_feed=work_feed,
                group=dev.get('group', ''),
                fixture=dev.get('fixture', ''),
                coord_source=dev.get('coord_source', '')
            )

        # Import fixtures
        for fix in data.get('fixtures', []):
            code = fix.get('code', '').strip()
            if not code:
                continue
            app.fixtures[code] = {
                'code': code,
                'base_group': fix.get('base_group', ''),
                'qr_code': fix.get('qr_code', ''),
                'scan_x': fix.get('scan_x', 0),
                'scan_y': fix.get('scan_y', 0),
                'scan_feed': fix.get('scan_feed', 50000),
            }

        _save_devices(app)
        _save_fixtures(app)
        return jsonify({
            'success': True,
            'devices': len(app.devices),
            'groups': len(app.device_groups),
            'fixtures': len(app.fixtures)
        })

    # ==================== Fixtures (Оснастки) API ====================

    @app.route('/api/fixtures', methods=['GET'])
    @login_required
    def get_fixtures():
        """Get list of all fixtures."""
        return jsonify([
            {
                'key': key,
                'code': fix.get('code', ''),
                'base_group': fix.get('base_group', ''),
                'qr_code': fix.get('qr_code', ''),
                'scan_x': fix.get('scan_x', 0),
                'scan_y': fix.get('scan_y', 0),
                'scan_feed': fix.get('scan_feed', 50000),
            }
            for key, fix in app.fixtures.items()
        ])

    @app.route('/api/fixtures/<key>', methods=['GET'])
    def get_fixture(key):
        """Get fixture details."""
        if key not in app.fixtures:
            return jsonify({'error': f'Unknown fixture: {key}'}), 404
        fix = app.fixtures[key]
        return jsonify({
            'key': key,
            'code': fix.get('code', ''),
            'base_group': fix.get('base_group', ''),
            'qr_code': fix.get('qr_code', ''),
            'scan_x': fix.get('scan_x', 0),
            'scan_y': fix.get('scan_y', 0),
            'scan_feed': fix.get('scan_feed', 50000),
        })

    @app.route('/api/fixtures', methods=['POST'])
    @login_required
    def create_fixture():
        """Create a new fixture."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        code = data.get('code', '').strip().upper()
        if not code:
            return jsonify({'error': 'Код оснастки обов\'язковий'}), 400

        qr_code = data.get('qr_code', '').strip().upper()
        if not qr_code:
            return jsonify({'error': 'QR код обов\'язковий'}), 400

        # Check uniqueness of code (used as key)
        if code in app.fixtures:
            return jsonify({'error': f'Оснастка з кодом "{code}" вже існує'}), 400

        # Check uniqueness of qr_code across all fixtures
        for k, f in app.fixtures.items():
            if f.get('qr_code', '') == qr_code:
                return jsonify({'error': f'QR код "{qr_code}" вже використовується в оснастці "{k}"'}), 400

        app.fixtures[code] = {
            'code': code,
            'base_group': data.get('base_group', '').strip(),
            'qr_code': qr_code,
            'scan_x': float(data.get('scan_x', 0)),
            'scan_y': float(data.get('scan_y', 0)),
            'scan_feed': float(data.get('scan_feed', 50000)),
        }

        _save_fixtures(app)
        return jsonify({'success': True, 'key': code})

    @app.route('/api/fixtures/<key>', methods=['PUT'])
    @login_required
    def update_fixture(key):
        """Update an existing fixture."""
        if key not in app.fixtures:
            return jsonify({'error': f'Unknown fixture: {key}'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        new_code = data.get('code', '').strip().upper()
        if not new_code:
            return jsonify({'error': 'Код оснастки обов\'язковий'}), 400

        qr_code = data.get('qr_code', '').strip().upper()
        if not qr_code:
            return jsonify({'error': 'QR код обов\'язковий'}), 400

        # Check uniqueness of new code if changed
        if new_code != key and new_code in app.fixtures:
            return jsonify({'error': f'Оснастка з кодом "{new_code}" вже існує'}), 400

        # Check uniqueness of qr_code (exclude current fixture)
        for k, f in app.fixtures.items():
            if k != key and f.get('qr_code', '') == qr_code:
                return jsonify({'error': f'QR код "{qr_code}" вже використовується в оснастці "{k}"'}), 400

        fix = {
            'code': new_code,
            'base_group': data.get('base_group', '').strip(),
            'qr_code': qr_code,
            'scan_x': float(data.get('scan_x', 0)),
            'scan_y': float(data.get('scan_y', 0)),
            'scan_feed': float(data.get('scan_feed', 50000)),
        }

        if new_code != key:
            del app.fixtures[key]

        app.fixtures[new_code] = fix
        _save_fixtures(app)
        return jsonify({'success': True, 'key': new_code})

    @app.route('/api/fixtures/<key>', methods=['DELETE'])
    @login_required
    def delete_fixture(key):
        """Delete a fixture."""
        if key not in app.fixtures:
            return jsonify({'error': f'Unknown fixture: {key}'}), 404

        del app.fixtures[key]
        _save_fixtures(app)
        return jsonify({'success': True, 'deleted': key})

    # ==================== Barcode Scanner API ====================

    @app.route('/api/scanner/status', methods=['GET'])
    def scanner_status():
        """Get barcode scanner status and last scan."""
        return jsonify(app.scanner.get_status())

    @app.route('/api/scanner/reset', methods=['POST'])
    def scanner_reset():
        """Reset scanner scan count and history."""
        app.scanner.reset_scan_count()
        return jsonify({'success': True})

    # ==================== USB Camera API ====================

    @app.route('/api/camera/status', methods=['GET'])
    def camera_status():
        """Get camera status."""
        return jsonify(app.camera.get_status())

    @app.route('/api/camera/stream')
    def camera_stream():
        """MJPEG video stream for live preview."""
        return Response(
            app.camera.generate_mjpeg(),
            mimetype='multipart/x-mixed-replace; boundary=frame',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'close'
            }
        )

    @app.route('/api/camera/snapshot')
    def camera_snapshot():
        """Single JPEG frame snapshot."""
        frame = app.camera.get_frame()
        if frame is None:
            return Response(status=204)
        return Response(frame, mimetype='image/jpeg', headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache'
        })

    @app.route('/api/camera/record/start', methods=['POST'])
    def camera_record_start():
        """Start video recording. Optional JSON: {prefix: "device_name"}."""
        data = request.get_json(silent=True) or {}
        prefix = data.get('prefix')
        result = app.camera.start_recording(prefix=prefix)
        return jsonify(result)

    @app.route('/api/camera/record/stop', methods=['POST'])
    def camera_record_stop():
        """Stop video recording."""
        result = app.camera.stop_recording()
        return jsonify(result)

    @app.route('/api/camera/record/rename', methods=['POST'])
    def camera_record_rename():
        """Rename a recording file. JSON: {file: "rel/path.avi", new_name: "base_name"}."""
        data = request.get_json(silent=True) or {}
        old_file = data.get('file')
        new_name = data.get('new_name')
        if not old_file or not new_name:
            return jsonify({'status': 'error', 'error': 'file and new_name required'}), 400
        result = app.camera.rename_recording(old_file, new_name)
        return jsonify(result)

    @app.route('/api/camera/recordings', methods=['GET'])
    def camera_recordings():
        """List all recordings."""
        return jsonify({'recordings': app.camera.list_recordings()})

    @app.route('/api/camera/storage', methods=['GET'])
    def camera_storage():
        """Get disk and recordings folder usage info."""
        return jsonify(app.camera.get_storage_info())

    @app.route('/api/camera/recordings/<path:filename>', methods=['GET'])
    def camera_download_recording(filename):
        """Download a recording file. filename can be 'date/file.avi'."""
        recordings_dir = app.camera._recordings_dir
        filepath = os.path.join(recordings_dir, filename)
        if not os.path.abspath(filepath).startswith(os.path.abspath(recordings_dir)):
            return jsonify({'error': 'Invalid path'}), 400
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        return send_file(filepath, as_attachment=True)

    @app.route('/api/camera/recordings/<path:filename>', methods=['DELETE'])
    @login_required
    def camera_delete_recording(filename):
        """Delete a recording file. filename can be 'date/file.avi'."""
        if app.camera.delete_recording(filename):
            return jsonify({'success': True})
        return jsonify({'error': 'File not found'}), 404

    # === USB Storage API ===

    @app.route('/api/usb/status', methods=['GET'])
    def usb_status():
        """Get USB storage status."""
        return jsonify(app.usb_storage.get_status())

    @app.route('/api/usb/devices', methods=['GET'])
    def usb_devices():
        """List all USB block devices."""
        return jsonify({'devices': app.usb_storage.list_usb_block_devices()})

    @app.route('/api/usb/mount', methods=['POST'])
    @login_required
    def usb_mount():
        """Mount USB drive and switch recordings to it."""
        data = request.get_json() or {}
        device = data.get('device')  # optional, auto-detect if None
        result = app.usb_storage.mount(device)
        if result.get('status') in ('mounted', 'already_mounted'):
            rec_dir = app.usb_storage.recordings_dir
            if rec_dir:
                app.camera.set_recordings_dir(rec_dir, allow_recording=True)
                result['recordings_switched'] = True
                result['recordings_dir'] = rec_dir
        return jsonify(result)

    @app.route('/api/usb/unmount', methods=['POST'])
    @login_required
    def usb_unmount():
        """Unmount USB drive and disable recording (protect system drive)."""
        # Stop recording if active
        app.camera.stop_recording()
        # Disable recording — no USB means no writing
        local_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'recordings')
        app.camera.set_recordings_dir(local_dir, allow_recording=False)
        result = app.usb_storage.unmount()
        result['recordings_dir'] = local_dir
        result['recording_disabled'] = True
        return jsonify(result)

    @app.route('/api/usb/format', methods=['POST'])
    @admin_required
    def usb_format():
        """Format USB device as ext4. Requires admin."""
        data = request.get_json() or {}
        device = data.get('device')
        label = data.get('label', 'REC_USB')
        if not device:
            return jsonify({'status': 'error', 'error': 'Вкажіть пристрій (device)'}), 400
        result = app.usb_storage.format_device(device, label)
        return jsonify(result)

    # === Backup API ===

    _BACKUP_SETTINGS_PATH = Path(__file__).parent.parent / 'config' / 'backup_settings.json'
    _CONFIG_DIR = Path(__file__).parent.parent / 'config'
    _LOCAL_BACKUP_DIR = Path(__file__).parent.parent / 'backups'

    # Files to include in backup
    _BACKUP_FILES = [
        'settings.yaml',       # work offsets, motion params
        'devices.yaml',        # devices + device groups
        'fixtures.yaml',       # fixtures (оснастки)
        'auth.yaml',           # users and passwords
        'cycle_history.json',  # cycle statistics
        'global_cycles.txt',   # global cycle counter
    ]

    def _get_backup_settings():
        try:
            if _BACKUP_SETTINGS_PATH.exists():
                import json as _json
                return _json.loads(_BACKUP_SETTINGS_PATH.read_text())
        except Exception:
            pass
        return {'auto_enabled': False, 'interval_hours': 24, 'max_backups': 10}

    def _save_backup_settings(settings):
        import json as _json
        _BACKUP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BACKUP_SETTINGS_PATH.write_text(_json.dumps(settings, ensure_ascii=False, indent=2))

    def _get_backup_dir():
        """Return backup directory on USB if mounted, else local."""
        usb_status = app.usb_storage.get_status()
        if usb_status.get('mounted') and usb_status.get('mount_point'):
            return Path(usb_status['mount_point']) / 'backups'
        return _LOCAL_BACKUP_DIR

    @app.route('/api/backup/settings', methods=['GET'])
    @admin_required
    def get_backup_settings():
        """Get backup settings."""
        settings = _get_backup_settings()
        backup_dir = _get_backup_dir()
        settings['backup_dir'] = str(backup_dir)
        settings['usb_mounted'] = app.usb_storage.get_status().get('mounted', False)
        return jsonify(settings)

    @app.route('/api/backup/settings', methods=['POST'])
    @admin_required
    def save_backup_settings_api():
        """Save backup settings."""
        data = request.get_json(silent=True) or {}
        settings = _get_backup_settings()
        if 'auto_enabled' in data:
            settings['auto_enabled'] = bool(data['auto_enabled'])
        if 'interval_hours' in data:
            settings['interval_hours'] = max(1, min(720, int(data['interval_hours'])))
        if 'max_backups' in data:
            settings['max_backups'] = max(1, min(100, int(data['max_backups'])))
        _save_backup_settings(settings)
        return jsonify({'status': 'ok', **settings})

    @app.route('/api/backup/create', methods=['POST'])
    @admin_required
    def create_backup():
        """Create a backup archive of all config files."""
        import tarfile
        import io
        from datetime import datetime as _dt

        backup_dir = _get_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = _dt.now().strftime('%Y%m%d_%H%M%S')
        archive_name = f"screwdrive_backup_{timestamp}.tar.gz"
        archive_path = backup_dir / archive_name

        try:
            with tarfile.open(str(archive_path), 'w:gz') as tar:
                for fname in _BACKUP_FILES:
                    fpath = _CONFIG_DIR / fname
                    if fpath.exists():
                        tar.add(str(fpath), arcname=fname)

            # Cleanup old backups
            settings = _get_backup_settings()
            max_backups = settings.get('max_backups', 10)
            existing = sorted(backup_dir.glob('screwdrive_backup_*.tar.gz'))
            while len(existing) > max_backups:
                oldest = existing.pop(0)
                oldest.unlink()

            size = archive_path.stat().st_size
            return jsonify({
                'status': 'ok',
                'file': archive_name,
                'path': str(archive_path),
                'size': size,
                'size_str': f"{size / 1024:.1f} КБ" if size < 1024 * 1024 else f"{size / (1024*1024):.1f} МБ",
            })
        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e)}), 500

    @app.route('/api/backup/list', methods=['GET'])
    @admin_required
    def list_backups():
        """List available backups from USB and local directories."""
        backups = []

        for source_label, bdir in [('usb', _get_backup_dir()), ('local', _LOCAL_BACKUP_DIR)]:
            if not bdir.exists():
                continue
            for f in sorted(bdir.glob('screwdrive_backup_*.tar.gz'), reverse=True):
                stat = f.stat()
                # Parse timestamp from filename
                name = f.stem.replace('.tar', '')  # screwdrive_backup_20260211_143025
                ts_part = name.replace('screwdrive_backup_', '')
                try:
                    from datetime import datetime as _dt
                    dt = _dt.strptime(ts_part, '%Y%m%d_%H%M%S')
                    created = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    created = ''

                size = stat.st_size
                backups.append({
                    'file': f.name,
                    'path': str(f),
                    'source': source_label,
                    'created': created,
                    'size': size,
                    'size_str': f"{size / 1024:.1f} КБ" if size < 1024 * 1024 else f"{size / (1024*1024):.1f} МБ",
                })

        # Deduplicate by filename (USB takes priority)
        seen = set()
        unique = []
        for b in backups:
            if b['file'] not in seen:
                seen.add(b['file'])
                unique.append(b)

        return jsonify({'backups': unique})

    @app.route('/api/backup/restore', methods=['POST'])
    @admin_required
    def restore_backup():
        """Restore config from a backup archive.

        Accepts JSON {path: "/abs/path/to.tar.gz"} or multipart file upload.
        """
        import tarfile

        archive_path = None

        # Option 1: path from list
        data = request.get_json(silent=True) or {}
        if data.get('path'):
            archive_path = Path(data['path'])
            if not archive_path.exists():
                return jsonify({'status': 'error', 'error': 'Архів не знайдено'}), 404

        # Option 2: uploaded file
        if archive_path is None and 'file' in request.files:
            uploaded = request.files['file']
            if uploaded.filename:
                # Save to temp location
                import tempfile
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz')
                uploaded.save(tmp.name)
                archive_path = Path(tmp.name)

        if archive_path is None:
            return jsonify({'status': 'error', 'error': 'Вкажіть архів для відновлення'}), 400

        try:
            restored = []
            with tarfile.open(str(archive_path), 'r:gz') as tar:
                # Security: only extract known filenames
                for member in tar.getmembers():
                    if member.name in _BACKUP_FILES:
                        member_f = tar.extractfile(member)
                        if member_f:
                            dest = _CONFIG_DIR / member.name
                            dest.write_bytes(member_f.read())
                            restored.append(member.name)

            # Reload configs in memory
            _load_devices(app)
            _load_fixtures(app)
            app.cycle_history = _load_cycle_history()
            app.global_cycle_count = _load_global_cycles()

            return jsonify({
                'status': 'ok',
                'restored': restored,
                'message': f'Відновлено {len(restored)} файлів. Перезавантажте сторінку.'
            })
        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e)}), 500

    @app.route('/api/backup/download/<path:filename>', methods=['GET'])
    @admin_required
    def download_backup(filename):
        """Download a backup archive file."""
        # Check USB dir first, then local
        for bdir in [_get_backup_dir(), _LOCAL_BACKUP_DIR]:
            fpath = bdir / filename
            if fpath.exists() and fpath.name.startswith('screwdrive_backup_'):
                return send_file(str(fpath), as_attachment=True)
        return jsonify({'error': 'Файл не знайдено'}), 404

    @app.route('/api/backup/delete', methods=['POST'])
    @admin_required
    def delete_backup():
        """Delete a backup archive."""
        data = request.get_json(silent=True) or {}
        path = data.get('path', '')
        if not path:
            return jsonify({'status': 'error', 'error': 'Вкажіть шлях'}), 400

        fpath = Path(path)
        if not fpath.exists():
            return jsonify({'status': 'error', 'error': 'Файл не знайдено'}), 404

        # Security: only allow deleting backup archives
        if not fpath.name.startswith('screwdrive_backup_') or not fpath.name.endswith('.tar.gz'):
            return jsonify({'status': 'error', 'error': 'Недопустимий файл'}), 400

        fpath.unlink()
        return jsonify({'status': 'ok'})

    # --- Auto-backup scheduler ---
    def _auto_backup_tick():
        """Run periodic auto-backup if enabled."""
        import time as _time
        while True:
            try:
                settings = _get_backup_settings()
                if settings.get('auto_enabled'):
                    interval_sec = settings.get('interval_hours', 24) * 3600
                    backup_dir = _get_backup_dir()
                    # Check last backup time
                    existing = sorted(backup_dir.glob('screwdrive_backup_*.tar.gz'))
                    should_backup = True
                    if existing:
                        last_mtime = existing[-1].stat().st_mtime
                        if _time.time() - last_mtime < interval_sec:
                            should_backup = False

                    if should_backup:
                        with app.app_context():
                            import tarfile
                            from datetime import datetime as _dt
                            backup_dir.mkdir(parents=True, exist_ok=True)
                            timestamp = _dt.now().strftime('%Y%m%d_%H%M%S')
                            archive_name = f"screwdrive_backup_{timestamp}.tar.gz"
                            archive_path = backup_dir / archive_name
                            with tarfile.open(str(archive_path), 'w:gz') as tar:
                                for fname in _BACKUP_FILES:
                                    fpath = _CONFIG_DIR / fname
                                    if fpath.exists():
                                        tar.add(str(fpath), arcname=fname)
                            # Cleanup
                            max_backups = settings.get('max_backups', 10)
                            all_bk = sorted(backup_dir.glob('screwdrive_backup_*.tar.gz'))
                            while len(all_bk) > max_backups:
                                all_bk.pop(0).unlink()
            except Exception:
                pass
            _time.sleep(600)  # check every 10 minutes

    import threading as _threading
    _backup_thread = _threading.Thread(target=_auto_backup_tick, daemon=True)
    _backup_thread.start()

    return app


# ================== Global Cycle Counter Persistence ==================

_GLOBAL_CYCLES_PATH = Path(__file__).parent.parent / 'config' / 'global_cycles.txt'
_CYCLE_HISTORY_PATH = Path(__file__).parent.parent / 'config' / 'cycle_history.json'
_CYCLE_HISTORY_MAX = 5000  # max records to keep


def _load_cycle_history() -> list:
    """Load cycle history from JSON file."""
    try:
        if _CYCLE_HISTORY_PATH.exists():
            import json
            return json.loads(_CYCLE_HISTORY_PATH.read_text())
    except Exception as e:
        print(f"WARNING: Failed to load cycle history: {e}")
    return []


def _save_cycle_history(history: list) -> None:
    """Save cycle history to JSON file (keep last N records)."""
    try:
        import json
        _CYCLE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        trimmed = history[-_CYCLE_HISTORY_MAX:]
        _CYCLE_HISTORY_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=None))
    except Exception as e:
        print(f"WARNING: Failed to save cycle history: {e}")


def _load_global_cycles() -> int:
    """Load global cycle counter from file. Returns 0 if file doesn't exist."""
    try:
        if _GLOBAL_CYCLES_PATH.exists():
            text = _GLOBAL_CYCLES_PATH.read_text().strip()
            if text:
                return int(text)
    except Exception as e:
        print(f"WARNING: Failed to load global cycle counter: {e}")
    return 0


def _save_global_cycles(count: int) -> None:
    """Save global cycle counter to file."""
    try:
        _GLOBAL_CYCLES_PATH.write_text(str(count))
    except Exception as e:
        print(f"WARNING: Failed to save global cycle counter: {e}")


def _load_offsets() -> dict:
    """Load work offsets from settings.yaml."""
    settings_path = Path(__file__).parent.parent / 'config' / 'settings.yaml'

    try:
        if settings_path.exists():
            with open(settings_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            work_offset = data.get('work_offset', {})
            return {
                'x': float(work_offset.get('x_mm', 0.0)),
                'y': float(work_offset.get('y_mm', 0.0))
            }
    except Exception as e:
        print(f"WARNING: Failed to load offsets: {e}")

    return {'x': 0.0, 'y': 0.0}


def _save_offsets(offsets: dict) -> None:
    """Save work offsets to settings.yaml."""
    settings_path = Path(__file__).parent.parent / 'config' / 'settings.yaml'

    try:
        # Load existing settings
        if settings_path.exists():
            with open(settings_path, 'r') as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Update work_offset section
        if 'work_offset' not in data:
            data['work_offset'] = {}

        data['work_offset']['x_mm'] = float(offsets.get('x', 0.0))
        data['work_offset']['y_mm'] = float(offsets.get('y', 0.0))

        # Save back to file
        with open(settings_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        print(f"Work offsets saved: X={offsets.get('x', 0)}, Y={offsets.get('y', 0)}")

    except Exception as e:
        print(f"ERROR: Failed to save offsets: {e}")
        raise


def _save_devices(app: Flask) -> None:
    """Save device programs and groups to devices.yaml."""
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
            'torque': prog.torque,
            'work_x': prog.work_x,
            'work_y': prog.work_y,
            'work_feed': prog.work_feed,
            'group': prog.group,
            'fixture': prog.fixture,
            'coord_source': prog.coord_source,
            'program': [
                {'type': s.step_type, 'x': s.x, 'y': s.y, 'f': s.feed}
                for s in prog.steps
            ]
        }
        devices_list.append(device_data)

    data = {
        'groups': app.device_groups,
        'devices': devices_list
    }

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

                # Load device groups
                app.device_groups = data.get('groups', [])

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

                    # Load work position fields
                    work_x = dev.get('work_x')
                    work_y = dev.get('work_y')
                    work_feed = dev.get('work_feed', 5000)
                    if work_x is not None:
                        work_x = float(work_x)
                    if work_y is not None:
                        work_y = float(work_y)
                    if work_feed is not None:
                        work_feed = float(work_feed)

                    app.devices[key] = DeviceProgram(
                        key=key,
                        name=dev.get('name', key),
                        holes=int(dev.get('holes', 0)),
                        steps=steps,
                        what=dev.get('what', ''),
                        screw_size=dev.get('screw_size', ''),
                        task=dev.get('task', ''),
                        torque=float(dev['torque']) if dev.get('torque') is not None else None,
                        work_x=work_x,
                        work_y=work_y,
                        work_feed=work_feed,
                        group=dev.get('group', ''),
                        fixture=dev.get('fixture', ''),
                        coord_source=dev.get('coord_source', '')
                    )

                print(f"Loaded {len(app.devices)} devices and {len(app.device_groups)} groups from {path}")
                return

            except Exception as e:
                print(f"WARNING: Failed to load devices from {path}: {e}")


# ================== Fixtures (Оснастки) Persistence ==================

_FIXTURES_PATH = Path(__file__).parent.parent / 'config' / 'fixtures.yaml'


def _save_fixtures(app: Flask) -> None:
    """Save fixtures to fixtures.yaml."""
    fixtures_list = []
    for key, fix in app.fixtures.items():
        fixtures_list.append({
            'code': fix.get('code', ''),
            'base_group': fix.get('base_group', ''),
            'qr_code': fix.get('qr_code', ''),
            'scan_x': fix.get('scan_x', 0),
            'scan_y': fix.get('scan_y', 0),
            'scan_feed': fix.get('scan_feed', 50000),
        })

    with open(_FIXTURES_PATH, 'w') as f:
        yaml.dump({'fixtures': fixtures_list}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _load_fixtures(app: Flask) -> None:
    """Load fixtures from fixtures.yaml."""
    if not _FIXTURES_PATH.exists():
        return

    try:
        with open(_FIXTURES_PATH, 'r') as f:
            data = yaml.safe_load(f) or {}

        for fix in data.get('fixtures', []):
            code = fix.get('code', '').strip()
            if not code:
                continue
            app.fixtures[code] = {
                'code': code,
                'base_group': fix.get('base_group', ''),
                'qr_code': fix.get('qr_code', ''),
                'scan_x': fix.get('scan_x', 0),
                'scan_y': fix.get('scan_y', 0),
                'scan_feed': fix.get('scan_feed', 50000),
            }

        print(f"Loaded {len(app.fixtures)} fixtures from {_FIXTURES_PATH}")
    except Exception as e:
        print(f"WARNING: Failed to load fixtures: {e}")


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
