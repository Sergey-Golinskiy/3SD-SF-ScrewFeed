/**
 * ScrewDrive Web UI - Main Application
 */

const API_BASE = '/api';
const POLL_INTERVAL = 1000;

// Application State
const state = {
    connected: false,
    status: null,
    devices: [],
    selectedDevice: null,
    editingDevice: null,
    coordRows: [],
    lastEstopState: null,  // Track emergency stop button state
    estopActive: false,    // Current E-STOP sensor state (for immediate UI update)
    brakeX: false,  // true = brake released (relay ON), false = brake engaged (relay OFF)
    brakeY: false   // true = brake released (relay ON), false = brake engaged (relay OFF)
};

// Ukrainian pluralization for "гвинт" (screw)
function pluralizeGvynt(n) {
    n = Math.abs(n);
    const lastTwo = n % 100;
    const lastOne = n % 10;

    if (lastTwo >= 11 && lastTwo <= 19) {
        return 'гвинтів';  // 11-19: гвинтів
    }
    if (lastOne === 1) {
        return 'гвинт';    // 1, 21, 31...: гвинт
    }
    if (lastOne >= 2 && lastOne <= 4) {
        return 'гвинти';   // 2-4, 22-24...: гвинти
    }
    return 'гвинтів';      // 0, 5-20, 25-30...: гвинтів
}

// API Functions
const api = {
    async get(path) {
        const response = await fetch(`${API_BASE}${path}`);
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return response.json();
    },

    async post(path, data = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return response.json();
    },

    async put(path, data = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return response.json();
    },

    async delete(path) {
        const response = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return response.json();
    }
};

// DOM Elements
const $ = (id) => document.getElementById(id);
const $$ = (selector) => document.querySelectorAll(selector);

// Tab Management
function initTabs() {
    $$('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;

            // Update buttons
            $$('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Update panels
            $$('.tab-panel').forEach(p => p.classList.remove('active'));
            $(tabId).classList.add('active');

            // Load tab-specific data
            if (tabId === 'settings') {
                loadDevices();
            }
        });
    });
}

// Status Updates
async function updateStatus() {
    try {
        const status = await api.get('/status');
        state.status = status;
        state.connected = true;

        updateConnectionIndicator(true);
        updateStatusTab(status);
        updateControlTab(status);
        updateXYTab(status);
        updateSettingsXYPos(status);

        // Monitor emergency stop button
        checkEmergencyStopButton(status);

    } catch (error) {
        state.connected = false;
        updateConnectionIndicator(false);
        console.error('Status update failed:', error);
    }
}

// Emergency Stop Button Monitor
async function checkEmergencyStopButton(status) {
    const sensors = status.sensors || {};
    const estopActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;

    // Update global E-STOP state for immediate UI updates
    state.estopActive = estopActive;

    // Check if state changed
    if (state.lastEstopState !== null && state.lastEstopState !== estopActive) {
        if (estopActive) {
            // Button pressed - trigger E-STOP
            // NOTE: XY table (Slave Pi) handles E-STOP directly via GPIO
            // We only need to handle cycle E-STOP on Master side
            console.log('Emergency stop button PRESSED - Slave Pi handles via GPIO');
            try {
                await api.post('/cycle/estop');
            } catch (error) {
                console.error('Cycle E-STOP trigger failed:', error);
            }
        } else {
            // Button released - clear E-STOP
            // NOTE: XY table (Slave Pi) auto-clears E-STOP via GPIO
            console.log('Emergency stop button RELEASED - Slave Pi auto-clears via GPIO');
            try {
                await api.post('/cycle/clear_estop');
            } catch (error) {
                console.error('Cycle clear E-STOP failed:', error);
            }
        }
    }

    state.lastEstopState = estopActive;
}

function updateConnectionIndicator(connected) {
    const indicator = $('connectionStatus');
    indicator.className = `status-indicator ${connected ? 'connected' : 'error'}`;
    indicator.querySelector('.text').textContent = connected ? 'Підключено' : 'Відключено';
}

function updateStatusTab(status) {
    // Cycle status
    const cycle = status.cycle || {};
    $('cycleState').textContent = cycle.state || '-';
    $('currentDevice').textContent = cycle.current_device || '-';
    $('cycleProgress').textContent = `${cycle.holes_completed || 0} / ${cycle.total_holes || 0}`;
    $('cycleCount').textContent = cycle.cycle_count || 0;

    // XY Table
    const xy = status.xy_table || {};
    const sensors = status.sensors || {};
    $('xyState').textContent = xy.state || '-';
    // Check E-STOP sensor directly for immediate response
    const estopSensorActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;
    // Show position as invalid when E-STOP active or not homed
    const xPos = (xy.x_homed && !estopSensorActive) ? (xy.x || 0).toFixed(2) : '?.??';
    const yPos = (xy.y_homed && !estopSensorActive) ? (xy.y || 0).toFixed(2) : '?.??';
    $('xyPosition').textContent = `X: ${xPos}  Y: ${yPos}`;

    // Sensors
    updateSensors(status.sensors || {});

    // Relays
    updateRelays(status.relays || {});
}

function updateSensors(sensors) {
    const grid = $('sensorGrid');
    grid.innerHTML = '';

    for (const [name, value] of Object.entries(sensors)) {
        const isActive = value === 'ACTIVE' || value === true;
        grid.innerHTML += `
            <div class="sensor-item">
                <span class="indicator ${isActive ? 'active' : ''}"></span>
                <span class="name">${formatName(name)}</span>
            </div>
        `;
    }
}

function updateRelays(relays) {
    const grid = $('relayGrid');
    grid.innerHTML = '';

    for (const [name, value] of Object.entries(relays)) {
        const isOn = value === 'ON' || value === true;
        grid.innerHTML += `
            <div class="relay-item">
                <span class="indicator ${isOn ? 'on' : ''}"></span>
                <span class="name">${formatName(name)}</span>
            </div>
        `;
    }
}

function formatName(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Control Tab
function updateControlTab(status) {
    // Update relay controls
    const grid = $('relayControlGrid');
    const relays = status.relays || {};

    if (grid.children.length === 0) {
        // Initial render with compact card layout
        for (const [name, value] of Object.entries(relays)) {
            const isOn = value === 'ON' || value === true;
            grid.innerHTML += `
                <div class="relay-control-new" data-relay-name="${name}">
                    <div class="relay-header">
                        <span class="relay-name">${formatName(name)}</span>
                        <span class="relay-status ${isOn ? 'on' : 'off'}">${isOn ? 'ON' : 'OFF'}</span>
                    </div>
                    <div class="relay-buttons">
                        <button class="btn-relay btn-on" onclick="relayOn('${name}')">ON</button>
                        <button class="btn-relay btn-off" onclick="relayOff('${name}')">OFF</button>
                    </div>
                    <div class="pulse-row">
                        <input type="number" class="pulse-duration" value="500" min="50" max="5000" step="50">
                        <span class="pulse-unit">мс</span>
                    </div>
                    <button class="btn-relay btn-pulse btn-pulse-full" onclick="relayPulse('${name}')">PULSE</button>
                </div>
            `;
        }
    } else {
        // Update existing status indicators only
        for (const [name, value] of Object.entries(relays)) {
            const isOn = value === 'ON' || value === true;
            const control = grid.querySelector(`[data-relay-name="${name}"]`);
            if (control) {
                const statusEl = control.querySelector('.relay-status');
                if (statusEl) {
                    statusEl.className = `relay-status ${isOn ? 'on' : 'off'}`;
                    statusEl.textContent = isOn ? 'ON' : 'OFF';
                }
            }
        }
    }
}

async function relayOn(name) {
    // Optimistic UI update
    const control = $('relayControlGrid').querySelector(`[data-relay-name="${name}"]`);
    if (control) {
        const statusEl = control.querySelector('.relay-status');
        statusEl.className = 'relay-status on';
        statusEl.textContent = 'ON';
    }
    try {
        await api.post(`/relays/${name}`, { state: 'on' });
    } catch (error) {
        console.error('Relay ON failed:', error);
        updateStatus(); // Refresh on error
    }
}

async function relayOff(name) {
    // Optimistic UI update
    const control = $('relayControlGrid').querySelector(`[data-relay-name="${name}"]`);
    if (control) {
        const statusEl = control.querySelector('.relay-status');
        statusEl.className = 'relay-status off';
        statusEl.textContent = 'OFF';
    }
    try {
        await api.post(`/relays/${name}`, { state: 'off' });
    } catch (error) {
        console.error('Relay OFF failed:', error);
        updateStatus(); // Refresh on error
    }
}

async function relayPulse(name) {
    const control = $('relayControlGrid').querySelector(`[data-relay-name="${name}"]`);
    const durationInput = control.querySelector('.pulse-duration');
    const durationMs = parseInt(durationInput.value) || 500;
    const durationSec = durationMs / 1000; // API expects seconds

    // Visual feedback - show ON briefly
    const statusEl = control.querySelector('.relay-status');
    statusEl.className = 'relay-status on';
    statusEl.textContent = 'PULSE';

    try {
        await api.post(`/relays/${name}`, { state: 'pulse', duration: durationSec });
        // Show OFF after pulse duration
        setTimeout(() => {
            statusEl.className = 'relay-status off';
            statusEl.textContent = 'OFF';
        }, durationMs);
    } catch (error) {
        console.error('Relay PULSE failed:', error);
        updateStatus();
    }
}

async function toggleRelay(name) {
    try {
        await api.post(`/relays/${name}`, { state: 'toggle' });
        updateStatus(); // Immediate refresh
    } catch (error) {
        console.error('Toggle relay failed:', error);
    }
}

function initControlTab() {
    // Initialization button
    $('btnInit').addEventListener('click', runInitialization);

    // Device select
    $('btnCycleStart').addEventListener('click', async () => {
        const device = $('deviceSelect').value;
        if (!device) {
            alert('Please select a device');
            return;
        }
        try {
            await api.post('/cycle/start', { device });
        } catch (error) {
            alert('Failed to start cycle: ' + error.message);
        }
    });

    $('btnCycleStop').addEventListener('click', () => api.post('/cycle/stop'));
    $('btnCyclePause').addEventListener('click', () => api.post('/cycle/pause'));
    $('btnEstop').addEventListener('click', () => api.post('/cycle/estop'));
    $('btnClearEstop').addEventListener('click', () => api.post('/cycle/clear_estop'));
}

// ========== INITIALIZATION SEQUENCE ==========

let initializationInProgress = false;

function updateInitStatus(text, progress, statusClass = '') {
    const card = $('initStatusCard');
    const statusText = $('initStatusText');
    const progressBar = $('initProgressBar');

    card.style.display = 'block';
    card.className = 'card' + (statusClass ? ' ' + statusClass : '');
    statusText.textContent = text;
    statusText.className = 'init-status-text' + (statusClass ? ' ' + statusClass : '');
    progressBar.style.width = progress + '%';
    progressBar.className = 'init-progress-bar' + (statusClass ? ' ' + statusClass : '');
}

function hideInitStatus() {
    $('initStatusCard').style.display = 'none';
}

async function waitForSensor(sensorName, expectedState, timeout = 10000, pollInterval = 100) {
    const startTime = Date.now();
    while (Date.now() - startTime < timeout) {
        const response = await api.get(`/sensors/${sensorName}`);
        if (response.state === expectedState) {
            return true;
        }
        await new Promise(resolve => setTimeout(resolve, pollInterval));
    }
    return false;
}

async function waitForHoming(timeout = 10000) {
    const startTime = Date.now();
    while (Date.now() - startTime < timeout) {
        const status = await api.get('/xy/status');
        const pos = status.position || {};
        if (pos.x_homed && pos.y_homed) {
            return true;
        }
        if (status.state === 'ERROR' || status.state === 'ESTOP') {
            throw new Error('Homing error: ' + (status.last_error || status.state));
        }
        await new Promise(resolve => setTimeout(resolve, 200));
    }
    return false;
}

async function runInitialization() {
    if (initializationInProgress) {
        alert('Ініціалізація вже виконується');
        return;
    }

    const deviceKey = $('deviceSelect').value;
    if (!deviceKey) {
        alert('Виберіть девайс для ініціалізації');
        return;
    }

    // Get selected device details
    const device = state.devices.find(d => d.key === deviceKey);
    if (!device) {
        alert('Девайс не знайдено');
        return;
    }

    initializationInProgress = true;
    $('btnInit').disabled = true;
    $('btnCycleStart').disabled = true;

    try {
        // Step 0: Check E-STOP
        updateInitStatus('Перевірка аварійної кнопки...', 5);
        const safety = await api.get('/sensors/safety');
        if (safety.estop_pressed) {
            throw new Error('Аварійна кнопка натиснута! Відпустіть її перед ініціалізацією.');
        }

        // Step 0.1: Check Slave Pi connection
        updateInitStatus('Перевірка підключення XY столу...', 10);
        const xyStatus = await api.get('/xy/status');
        if (!xyStatus.connected) {
            throw new Error('XY стіл не підключено! Перевірте з\'єднання з Raspberry Pi.');
        }

        // Step 1: Check and release brakes
        updateInitStatus('Перевірка та відпускання гальм...', 15);
        const relays = await api.get('/relays');

        // Release brake X if engaged (relay OFF = brake engaged)
        if (relays.r02_brake_x !== 'ON') {
            await api.post('/relays/r02_brake_x', { state: 'on' });
            await new Promise(resolve => setTimeout(resolve, 300));
        }

        // Release brake Y if engaged
        if (relays.r03_brake_y !== 'ON') {
            await api.post('/relays/r03_brake_y', { state: 'on' });
            await new Promise(resolve => setTimeout(resolve, 300));
        }

        // Step 1.1: Homing
        updateInitStatus('Виконується хомінг XY столу...', 25);
        const homeResponse = await api.post('/xy/home');
        if (homeResponse.status !== 'homed') {
            throw new Error('Не вдалося запустити хомінг');
        }

        // Wait for homing to complete with 15 second timeout
        const homingComplete = await waitForHoming(15000);
        if (!homingComplete) {
            throw new Error('Хомінг не завершено за 15 секунд. Перевірте датчики.');
        }

        // Step 2: Check cylinder sensors
        updateInitStatus('Перевірка датчиків циліндра...', 40);
        const gerUp = await api.get('/sensors/ger_c2_up');
        const gerDown = await api.get('/sensors/ger_c2_down');

        // GER_C2_UP should be ACTIVE (cylinder is up), GER_C2_DOWN should be INACTIVE
        if (gerUp.state !== 'ACTIVE') {
            throw new Error('Датчик GER_C2_UP не активний. Циліндр не у верхньому положенні.');
        }
        if (gerDown.state === 'ACTIVE') {
            throw new Error('Датчик GER_C2_DOWN активний. Можливо проблема з пневматикою.');
        }

        // Step 3: Lower cylinder (turn on R04_C2) and wait for GER_C2_DOWN
        updateInitStatus('Опускання циліндра...', 50);
        await api.post('/relays/r04_c2', { state: 'on' });

        const cylinderLowered = await waitForSensor('ger_c2_down', 'ACTIVE', 5000);
        if (!cylinderLowered) {
            await api.post('/relays/r04_c2', { state: 'off' });
            throw new Error('Циліндр не опустився за 5 секунд. Перевірте пневматику.');
        }

        // Step 4: Raise cylinder (turn off R04_C2) and wait for GER_C2_UP
        updateInitStatus('Піднімання циліндра...', 60);
        await api.post('/relays/r04_c2', { state: 'off' });

        const cylinderRaised = await waitForSensor('ger_c2_up', 'ACTIVE', 5000);
        if (!cylinderRaised) {
            throw new Error('Циліндр не піднявся за 5 секунд. Перевірте пневматику.');
        }

        // Step 5: Select task (pulse R07 or R08)
        updateInitStatus('Вибір задачі для закручування...', 75);
        const taskRelay = device.task === '1' ? 'r08_di6_tsk1' : 'r07_di5_tsk0';
        await api.post(`/relays/${taskRelay}`, { state: 'pulse', duration: 0.7 });
        await new Promise(resolve => setTimeout(resolve, 800)); // Wait for pulse to complete

        // Step 6: Move to work position
        updateInitStatus('Виїзд до оператора...', 85);

        const workX = device.work_x;
        const workY = device.work_y;
        const workFeed = device.work_feed || 5000;

        if (workX === null || workX === undefined || workY === null || workY === undefined) {
            throw new Error('Робоча позиція не задана для цього девайсу. Вкажіть Робоча X та Робоча Y в налаштуваннях.');
        }

        const moveResponse = await api.post('/xy/move', { x: workX, y: workY, feed: workFeed });
        if (moveResponse.status !== 'ok') {
            throw new Error('Не вдалося виїхати до робочої позиції');
        }

        // Wait a bit for the move to start
        await new Promise(resolve => setTimeout(resolve, 500));

        // Success!
        updateInitStatus('Ініціалізація завершена. Очікування натискання START...', 100, 'success');

        // Enable START button
        $('btnCycleStart').disabled = false;

    } catch (error) {
        console.error('Initialization error:', error);
        updateInitStatus('ПОМИЛКА: ' + error.message, 100, 'error');

        // Turn off cylinder relay for safety
        try {
            await api.post('/relays/r04_c2', { state: 'off' });
        } catch (e) {}
    } finally {
        initializationInProgress = false;
        $('btnInit').disabled = false;
    }
}

// XY Table Tab
function updateXYTab(status) {
    const xy = status.xy_table || {};
    const health = xy.health || {};
    const endstops = xy.endstops || {};
    const sensors = status.sensors || {};

    // Check E-STOP sensor directly for immediate response
    const estopSensorActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;

    // Position - show as invalid when E-STOP active OR not homed
    // E-STOP sensor check provides immediate visual feedback
    const xHomed = xy.x_homed && !estopSensorActive;
    const yHomed = xy.y_homed && !estopSensorActive;
    const xPos = xHomed ? (xy.x || 0).toFixed(2) : '?.??';
    const yPos = yHomed ? (xy.y || 0).toFixed(2) : '?.??';
    $('xyPosDisplay').textContent = `X: ${xPos}  Y: ${yPos}`;

    // Add warning class when not homed or E-STOP active
    const posDisplay = $('xyPosDisplay');
    if (!xHomed || !yHomed || estopSensorActive) {
        posDisplay.classList.add('position-invalid');
    } else {
        posDisplay.classList.remove('position-invalid');
    }

    // Connection status
    const serviceStatus = health.service_status || 'unknown';
    const serviceEl = $('xyServiceStatus');
    serviceEl.textContent = getServiceStatusText(serviceStatus);
    serviceEl.className = `value ${getStatusClass(serviceStatus)}`;

    const connStatus = xy.connected ? 'connected' : 'disconnected';
    const connEl = $('xyConnStatus');
    connEl.textContent = xy.connected ? 'Підключено' : 'Відключено';
    connEl.className = `value ${xy.connected ? 'status-ok' : 'status-error'}`;

    // State
    const stateEl = $('xyStateDisplay');
    stateEl.textContent = getStateText(xy.state);
    stateEl.className = `value ${getStateClass(xy.state)}`;

    // Ping latency
    const pingEl = $('xyPingLatency');
    if (health.last_ping_ok) {
        pingEl.textContent = `${health.last_ping_latency_ms || 0} ms`;
        pingEl.className = 'value status-ok';
    } else {
        pingEl.textContent = 'timeout';
        pingEl.className = 'value status-error';
    }

    // Endstops
    const endstopXEl = $('xyEndstopX');
    endstopXEl.textContent = endstops.x_min ? 'TRIGGERED' : 'open';
    endstopXEl.className = `value ${endstops.x_min ? 'status-warning' : ''}`;

    const endstopYEl = $('xyEndstopY');
    endstopYEl.textContent = endstops.y_min ? 'TRIGGERED' : 'open';
    endstopYEl.className = `value ${endstops.y_min ? 'status-warning' : ''}`;

    // Limit warning
    const limitWarningRow = $('xyLimitWarningRow');
    const limitWarningEl = $('xyLimitWarning');
    if (health.last_limit_warning) {
        limitWarningEl.textContent = health.last_limit_warning;
        limitWarningRow.style.display = 'flex';
    } else {
        limitWarningRow.style.display = 'none';
    }

    // Error
    const errorEl = $('xyLastError');
    if (health.last_error) {
        errorEl.textContent = health.last_error;
        errorEl.className = 'value';
    } else {
        errorEl.textContent = '-';
        errorEl.className = 'value';
    }

    // Homed status - show as not homed when E-STOP sensor active
    const homedX = $('xyHomedX');
    const xHomedDisplay = xy.x_homed && !estopSensorActive;
    homedX.textContent = xHomedDisplay ? 'X: захомлено' : 'X: не захомлено';
    homedX.className = `homed-indicator ${xHomedDisplay ? 'homed' : 'not-homed'}`;

    const homedY = $('xyHomedY');
    const yHomedDisplay = xy.y_homed && !estopSensorActive;
    homedY.textContent = yHomedDisplay ? 'Y: захомлено' : 'Y: не захомлено';
    homedY.className = `homed-indicator ${yHomedDisplay ? 'homed' : 'not-homed'}`;

    // E-STOP indicator - show when E-STOP active OR not homed
    const estopIndicator = $('xyEstopIndicator');
    if (estopIndicator) {
        const needsHoming = !xy.x_homed || !xy.y_homed || estopSensorActive;
        estopIndicator.style.display = needsHoming ? 'block' : 'none';
    }

    // Update brake states from relays
    updateBrakeStatus(status.relays || {});
}

// Brake status update
function updateBrakeStatus(relays) {
    // r02_brake_x: ON = brake released (can move), OFF = brake engaged (blocked)
    // r03_brake_y: ON = brake released (can move), OFF = brake engaged (blocked)
    state.brakeX = relays.r02_brake_x === 'ON';
    state.brakeY = relays.r03_brake_y === 'ON';

    // Update brake X button (XY Tab)
    const brakeXBtn = $('btnBrakeX');
    const brakeXStatus = $('brakeXStatus');
    if (brakeXBtn && brakeXStatus) {
        brakeXStatus.textContent = state.brakeX ? 'ВІДПУЩЕНО' : 'ЗАТИСНУТО';
        brakeXBtn.className = `btn btn-brake ${state.brakeX ? 'brake-released' : 'brake-engaged'}`;
    }

    // Update brake Y button (XY Tab)
    const brakeYBtn = $('btnBrakeY');
    const brakeYStatus = $('brakeYStatus');
    if (brakeYBtn && brakeYStatus) {
        brakeYStatus.textContent = state.brakeY ? 'ВІДПУЩЕНО' : 'ЗАТИСНУТО';
        brakeYBtn.className = `btn btn-brake ${state.brakeY ? 'brake-released' : 'brake-engaged'}`;
    }

    // Update brake X button (Settings Tab)
    const brakeXBtnSettings = $('btnBrakeXSettings');
    const brakeXStatusSettings = $('brakeXStatusSettings');
    if (brakeXBtnSettings && brakeXStatusSettings) {
        brakeXStatusSettings.textContent = state.brakeX ? 'ВІДПУЩ' : 'ЗАТИСН';
        brakeXBtnSettings.className = `btn btn-brake-grid ${state.brakeX ? 'brake-released' : 'brake-engaged'}`;
    }

    // Update brake Y button (Settings Tab)
    const brakeYBtnSettings = $('btnBrakeYSettings');
    const brakeYStatusSettings = $('brakeYStatusSettings');
    if (brakeYBtnSettings && brakeYStatusSettings) {
        brakeYStatusSettings.textContent = state.brakeY ? 'ВІДПУЩ' : 'ЗАТИСН';
        brakeYBtnSettings.className = `btn btn-brake-grid ${state.brakeY ? 'brake-released' : 'brake-engaged'}`;
    }
}

function getServiceStatusText(status) {
    const texts = {
        'running': 'Працює',
        'stopped': 'Зупинено',
        'error': 'Помилка',
        'timeout': 'Таймаут',
        'disconnected': 'Відключено',
        'unknown': 'Невідомо',
        'not_initialized': 'Не ініціалізовано'
    };
    return texts[status] || status;
}

function getStatusClass(status) {
    if (status === 'running') return 'status-ok';
    if (status === 'error' || status === 'stopped' || status === 'not_initialized') return 'status-error';
    if (status === 'timeout') return 'status-warning';
    return '';
}

function getStateText(state) {
    const texts = {
        'READY': 'Готовий',
        'MOVING': 'Рух',
        'HOMING': 'Хомінг',
        'ERROR': 'Помилка',
        'ESTOP': '⚠️ АВАРІЙНА ЗУПИНКА',
        'TIMEOUT': 'Таймаут',
        'DISCONNECTED': 'Відключено',
        'CONNECTING': 'Підключення...'
    };
    return texts[state] || state || '-';
}

function getStateClass(state) {
    if (state === 'READY') return 'status-ok';
    if (state === 'MOVING' || state === 'HOMING' || state === 'CONNECTING') return 'status-warning';
    if (state === 'ERROR' || state === 'ESTOP' || state === 'TIMEOUT' || state === 'DISCONNECTED') return 'status-error';
    return '';
}

function initXYTab() {
    // Connection buttons
    $('btnXYConnect').addEventListener('click', async () => {
        try {
            await api.post('/xy/connect');
            updateStatus();
        } catch (error) {
            alert('Помилка підключення: ' + error.message);
        }
    });

    $('btnXYDisconnect').addEventListener('click', async () => {
        try {
            await api.post('/xy/disconnect');
            updateStatus();
        } catch (error) {
            console.error('Disconnect failed:', error);
        }
    });

    $('btnXYPing').addEventListener('click', async () => {
        try {
            const response = await api.get('/xy/status');
            if (response.health && response.health.last_ping_ok) {
                alert(`Ping OK! Latency: ${response.health.last_ping_latency_ms} ms`);
            } else {
                alert('Ping failed: ' + (response.health?.last_error || 'Unknown error'));
            }
        } catch (error) {
            alert('Ping failed: ' + error.message);
        }
    });

    // Jog buttons
    $$('[data-jog]').forEach(btn => {
        btn.addEventListener('click', () => {
            const dir = btn.dataset.jog;
            const step = parseFloat($('jogStep').value);
            const feed = parseFloat($('jogFeed').value);

            let dx = 0, dy = 0;
            if (dir === 'x+') dx = step;
            if (dir === 'x-') dx = -step;
            if (dir === 'y+') dy = step;
            if (dir === 'y-') dy = -step;

            // Check brakes before jogging
            if (dx !== 0 && !checkBrakeX()) return;
            if (dy !== 0 && !checkBrakeY()) return;

            api.post('/xy/jog', { dx, dy, feed });
        });
    });

    // Home buttons - only check brakes, NOT homed status (we're homing!)
    $('btnXYHome').addEventListener('click', () => {
        if (!checkBothBrakesOnly()) return;
        api.post('/xy/home');
    });
    $('btnHomeX').addEventListener('click', () => {
        if (!checkBrakeXOnly()) return;
        api.post('/xy/home/x');
    });
    $('btnHomeY').addEventListener('click', () => {
        if (!checkBrakeYOnly()) return;
        api.post('/xy/home/y');
    });
    $('btnZero').addEventListener('click', () => {
        if (!checkBothBrakes()) return;
        if (!checkEndstopsForZero()) return;  // Only allow ZERO at home position
        api.post('/xy/zero');
    });

    // Motor control
    $('btnEnableMotors').addEventListener('click', async () => {
        try {
            await api.post('/xy/command', { command: 'M17' });
        } catch (error) {
            console.error('Enable motors failed:', error);
        }
    });

    $('btnDisableMotors').addEventListener('click', async () => {
        try {
            await api.post('/xy/command', { command: 'M18' });
        } catch (error) {
            console.error('Disable motors failed:', error);
        }
    });

    // Move to position
    $('btnMoveTo').addEventListener('click', () => {
        const x = parseFloat($('moveX').value);
        const y = parseFloat($('moveY').value);
        const feed = parseFloat($('moveFeed').value);

        // Check brakes before moving
        if (!checkBrakesForMove(x, y)) {
            return;
        }
        api.post('/xy/move', { x, y, feed });
    });

    // Brake control buttons
    $('btnBrakeX').addEventListener('click', toggleBrakeX);
    $('btnBrakeY').addEventListener('click', toggleBrakeY);
}

// Brake toggle functions
async function toggleBrakeX() {
    try {
        const newState = state.brakeX ? 'off' : 'on';
        await api.post('/relays/r02_brake_x', { state: newState });
        // Status will be updated on next poll
    } catch (error) {
        console.error('Toggle brake X failed:', error);
        alert('Помилка перемикання гальма X: ' + error.message);
    }
}

async function toggleBrakeY() {
    try {
        const newState = state.brakeY ? 'off' : 'on';
        await api.post('/relays/r03_brake_y', { state: newState });
        // Status will be updated on next poll
    } catch (error) {
        console.error('Toggle brake Y failed:', error);
        alert('Помилка перемикання гальма Y: ' + error.message);
    }
}

// Check if axes are homed (required after E-STOP)
function checkHomedForMove() {
    const xy = state.status?.xy_table || {};
    const xHomed = xy.x_homed;
    const yHomed = xy.y_homed;

    if (!xHomed && !yHomed) {
        alert('УВАГА! Після аварійної зупинки потрібно виконати калібрування!\n\nНатисніть "Home" для калібрування обох осей.');
        return false;
    }
    if (!xHomed) {
        alert('УВАГА! Вісь X не відкалібрована!\n\nНатисніть "Home X" для калібрування.');
        return false;
    }
    if (!yHomed) {
        alert('УВАГА! Вісь Y не відкалібрована!\n\nНатисніть "Home Y" для калібрування.');
        return false;
    }
    return true;
}

function checkHomedX() {
    const xHomed = state.status?.xy_table?.x_homed;
    if (!xHomed) {
        alert('УВАГА! Вісь X не відкалібрована!\n\nНатисніть "Home X" для калібрування.');
        return false;
    }
    return true;
}

function checkHomedY() {
    const yHomed = state.status?.xy_table?.y_homed;
    if (!yHomed) {
        alert('УВАГА! Вісь Y не відкалібрована!\n\nНатисніть "Home Y" для калібрування.');
        return false;
    }
    return true;
}

// Check brakes before movement
function checkBrakesForMove(targetX, targetY) {
    // First check if homed
    if (!checkHomedForMove()) {
        return false;
    }

    const currentX = state.status?.xy_table?.x || 0;
    const currentY = state.status?.xy_table?.y || 0;

    const movingX = Math.abs(targetX - currentX) > 0.01;
    const movingY = Math.abs(targetY - currentY) > 0.01;

    if (movingX && !state.brakeX) {
        alert('Гальмо X затиснуто! Відпустіть гальмо X перед рухом по осі X.');
        return false;
    }

    if (movingY && !state.brakeY) {
        alert('Гальмо Y затиснуто! Відпустіть гальмо Y перед рухом по осі Y.');
        return false;
    }

    return true;
}

function checkBrakeX() {
    // First check if X is homed
    if (!checkHomedX()) {
        return false;
    }
    if (!state.brakeX) {
        alert('Гальмо X затиснуто! Відпустіть гальмо X для цієї операції.');
        return false;
    }
    return true;
}

function checkBrakeY() {
    // First check if Y is homed
    if (!checkHomedY()) {
        return false;
    }
    if (!state.brakeY) {
        alert('Гальмо Y затиснуто! Відпустіть гальмо Y для цієї операції.');
        return false;
    }
    return true;
}

function checkBothBrakes() {
    // First check if both axes are homed
    if (!checkHomedForMove()) {
        return false;
    }
    if (!state.brakeX) {
        alert('Гальмо X затиснуто! Відпустіть гальмо X для цієї операції.');
        return false;
    }
    if (!state.brakeY) {
        alert('Гальмо Y затиснуто! Відпустіть гальмо Y для цієї операції.');
        return false;
    }
    return true;
}

// Brake checks WITHOUT homed check (for homing commands)
function checkBrakeXOnly() {
    if (!state.brakeX) {
        alert('Гальмо X затиснуто! Відпустіть гальмо X для цієї операції.');
        return false;
    }
    return true;
}

function checkBrakeYOnly() {
    if (!state.brakeY) {
        alert('Гальмо Y затиснуто! Відпустіть гальмо Y для цієї операції.');
        return false;
    }
    return true;
}

function checkBothBrakesOnly() {
    if (!state.brakeX) {
        alert('Гальмо X затиснуто! Відпустіть гальмо X для цієї операції.');
        return false;
    }
    if (!state.brakeY) {
        alert('Гальмо Y затиснуто! Відпустіть гальмо Y для цієї операції.');
        return false;
    }
    return true;
}

// Check if table is at home position (both endstops triggered)
function checkEndstopsForZero() {
    const xy = state.status?.xy_table || {};
    const endstops = xy.endstops || {};

    if (!endstops.x_min || !endstops.y_min) {
        alert('УВАГА! Обнулення координат дозволено тільки в позиції HOME!\n\nСтіл повинен бути на концевиках X та Y.\nСпочатку виконайте команду HOME.');
        return false;
    }
    return true;
}

// Settings Tab - Device Management
async function loadDevices() {
    try {
        const devices = await api.get('/devices');
        state.devices = devices;
        renderDeviceList();
        updateDeviceSelect();
    } catch (error) {
        console.error('Failed to load devices:', error);
    }
}

function renderDeviceList() {
    const list = $('deviceList');
    list.innerHTML = '';

    for (const device of state.devices) {
        const isSelected = state.selectedDevice === device.key;
        // Display key directly - format: Назва_Щокрутим_Розмір(Кількість)
        list.innerHTML += `
            <div class="device-item ${isSelected ? 'selected' : ''}"
                 data-key="${device.key}"
                 onclick="selectDevice('${device.key}')">
                <div class="device-name">${device.key}</div>
            </div>
        `;
    }
}

function updateDeviceSelect() {
    const select = $('deviceSelect');
    select.innerHTML = '<option value="">-- Select Device --</option>';

    for (const device of state.devices) {
        // Display key directly - format: Назва_Щокрутим_Розмір(Кількість)
        select.innerHTML += `<option value="${device.key}">${device.key}</option>`;
    }
}

async function selectDevice(key) {
    state.selectedDevice = key;
    renderDeviceList();

    try {
        const device = await api.get(`/devices/${key}`);
        loadDeviceToEditor(device);
    } catch (error) {
        console.error('Failed to load device:', error);
    }
}

function loadDeviceToEditor(device) {
    state.editingDevice = device.key;

    $('editDeviceKey').value = device.key || '';
    $('editName').value = (device.name || '').toUpperCase();  // Force uppercase
    $('editWhat').value = device.what || '';
    $('editHoles').value = device.holes || '1';
    $('editScrewSize').value = device.screw_size || 'M3x8';
    $('editTask').value = device.task !== undefined ? device.task : '0';

    // Work position fields
    $('editWorkX').value = device.work_x !== undefined ? device.work_x : '';
    $('editWorkY').value = device.work_y !== undefined ? device.work_y : '';
    $('editWorkFeed').value = device.work_feed || '5000';

    // Load coordinates
    clearCoordRows();
    for (const step of device.steps || []) {
        addCoordRow(step.x, step.y, step.type, step.feed);
    }
}

function newDevice() {
    state.selectedDevice = null;
    state.editingDevice = null;
    renderDeviceList();

    $('editDeviceKey').value = '';
    $('editName').value = '';
    $('editWhat').value = '';
    $('editHoles').value = '';        // Placeholder: Виберіть...
    $('editScrewSize').value = '';    // Placeholder: Виберіть...
    $('editTask').value = '';         // Placeholder: Виберіть...

    // Work position defaults
    $('editWorkX').value = '';
    $('editWorkY').value = '';
    $('editWorkFeed').value = '5000';

    clearCoordRows();
    addCoordRow();
}

async function saveDevice() {
    const key = $('editDeviceKey').value.trim();
    const name = $('editName').value.trim().toUpperCase();  // Force uppercase

    if (!name) {
        alert('Назва девайсу обов\'язкова');
        return;
    }

    if (name.length > 4) {
        alert('Назва девайсу максимум 4 символи');
        return;
    }

    const what = $('editWhat').value.trim();
    if (what.length > 4) {
        alert('Що крутим максимум 4 символи');
        return;
    }

    const holes = $('editHoles').value;
    const screwSize = $('editScrewSize').value;
    const task = $('editTask').value;

    // Validate dropdowns
    if (!holes) {
        alert('Виберіть кількість винтів');
        return;
    }
    if (!screwSize) {
        alert('Виберіть розмір винтів');
        return;
    }
    if (!task) {
        alert('Виберіть номер таски');
        return;
    }

    // Generate key from fields: Назва_Щокрутим_Розмір(Кількість гвинтів)
    // Example: ABCD_КРУТ_M3x8(4 гвинти)
    let deviceKey = key;
    if (!deviceKey) {
        deviceKey = name;
        if (what) {
            deviceKey += '_' + what.toUpperCase();
        }
        const holesNum = parseInt(holes);
        deviceKey += '_' + screwSize + '(' + holesNum + ' ' + pluralizeGvynt(holesNum) + ')';
    }

    // Validate and get work position
    const workX = parseFloat($('editWorkX').value);
    const workY = parseFloat($('editWorkY').value);
    const workFeed = parseFloat($('editWorkFeed').value) || 5000;

    if (!isNaN(workX) && (workX < 0 || workX > 220)) {
        alert('Робоча позиція X повинна бути від 0 до 220 мм');
        return;
    }
    if (!isNaN(workY) && (workY < 0 || workY > 500)) {
        alert('Робоча позиція Y повинна бути від 0 до 500 мм');
        return;
    }

    // Collect coordinates
    const steps = [];
    const rows = $('coordsList').querySelectorAll('.coord-row-new');
    rows.forEach(row => {
        const x = parseFloat(row.querySelector('.coord-x').value) || 0;
        const y = parseFloat(row.querySelector('.coord-y').value) || 0;
        const type = row.querySelector('.coord-type').value;
        // Parse feed value, removing "F " prefix if present
        const feedStr = row.querySelector('.coord-feed').value || '5000';
        const feed = parseFloat(feedStr.replace(/[Ff]\s*/g, '')) || 5000;
        steps.push({ x, y, type, feed });
    });

    const data = {
        key: deviceKey,
        name: name,
        what: what,
        holes: parseInt(holes),
        screw_size: screwSize,
        task: task,
        work_x: isNaN(workX) ? null : workX,
        work_y: isNaN(workY) ? null : workY,
        work_feed: workFeed,
        steps: steps
    };

    try {
        if (state.editingDevice) {
            // Update existing
            await api.put(`/devices/${state.editingDevice}`, data);
        } else {
            // Create new
            await api.post('/devices', data);
        }

        await loadDevices();
        selectDevice(deviceKey);

    } catch (error) {
        alert('Failed to save device: ' + error.message);
    }
}

async function deleteDevice() {
    if (!state.editingDevice) return;

    if (!confirm(`Delete device "${state.editingDevice}"?`)) return;

    try {
        await api.delete(`/devices/${state.editingDevice}`);
        newDevice();
        await loadDevices();
    } catch (error) {
        alert('Failed to delete device: ' + error.message);
    }
}

function cancelEdit() {
    if (state.selectedDevice) {
        selectDevice(state.selectedDevice);
    } else {
        newDevice();
    }
}

// Coordinate Rows
function clearCoordRows() {
    $('coordsList').innerHTML = '';
    state.coordRows = [];
}

function addCoordRow(x = '', y = '', type = 'FREE', feed = 5000) {
    const list = $('coordsList');
    const rowNum = list.children.length + 1;

    // Normalize type to uppercase
    const typeUpper = (type || 'FREE').toUpperCase();

    const row = document.createElement('div');
    row.className = 'coord-row-new';
    row.innerHTML = `
        <span class="row-num">${rowNum}.</span>
        <input type="text" class="coord-x" value="${x}" placeholder="X">
        <input type="text" class="coord-y" value="${y}" placeholder="Y">
        <select class="coord-type ${typeUpper === 'WORK' ? 'coord-type-work' : 'coord-type-free'}" onchange="updateTypeStyle(this)">
            <option value="FREE" ${typeUpper === 'FREE' ? 'selected' : ''}>FREE</option>
            <option value="WORK" ${typeUpper === 'WORK' ? 'selected' : ''}>WORK</option>
        </select>
        <input type="text" class="coord-feed" value="F ${feed}" placeholder="F 5000">
        <button class="btn-del-new" onclick="removeCoordRow(this)">−</button>
    `;

    list.appendChild(row);
    state.coordRows.push(row);
}

function updateTypeStyle(select) {
    if (select.value === 'WORK') {
        select.classList.remove('coord-type-free');
        select.classList.add('coord-type-work');
    } else {
        select.classList.remove('coord-type-work');
        select.classList.add('coord-type-free');
    }
}

function removeCoordRow(btn) {
    const row = btn.closest('.coord-row-new');
    row.remove();
    renumberCoordRows();
}

function renumberCoordRows() {
    const rows = $('coordsList').querySelectorAll('.coord-row-new');
    rows.forEach((row, i) => {
        row.querySelector('.row-num').textContent = (i + 1) + '.';
    });
}

async function goToCoord(btn) {
    const row = btn.closest('.coord-row-new');
    const x = parseFloat(row.querySelector('.coord-x').value) || 0;
    const y = parseFloat(row.querySelector('.coord-y').value) || 0;
    // Parse feed value, removing "F " prefix if present
    const feedStr = row.querySelector('.coord-feed').value || '5000';
    const feed = parseFloat(feedStr.replace(/[Ff]\s*/g, '')) || 5000;

    // Check brakes before moving
    if (!checkBrakesForMove(x, y)) {
        return;
    }

    try {
        await api.post('/xy/move', { x, y, feed });
    } catch (error) {
        alert('Помилка переміщення: ' + error.message);
    }
}

function initSettingsTab() {
    $('btnNewDevice').addEventListener('click', newDevice);
    $('btnAddCoord').addEventListener('click', () => addCoordRow());
    $('btnSaveDevice').addEventListener('click', saveDevice);
    $('btnCancelEdit').addEventListener('click', cancelEdit);
    $('btnDeleteDevice').addEventListener('click', deleteDevice);

    // XY Control in Settings Tab
    $$('[data-jog-settings]').forEach(btn => {
        btn.addEventListener('click', () => {
            const dir = btn.dataset.jogSettings;
            const step = parseFloat($('jogStepSettings').value);
            const feed = 5000;

            let dx = 0, dy = 0;
            if (dir === 'x+') dx = step;
            if (dir === 'x-') dx = -step;
            if (dir === 'y+') dy = step;
            if (dir === 'y-') dy = -step;

            // Check brakes before jogging
            if (dx !== 0 && !checkBrakeX()) return;
            if (dy !== 0 && !checkBrakeY()) return;

            api.post('/xy/jog', { dx, dy, feed });
        });
    });

    // Home buttons in settings - only check brakes, NOT homed status
    $('btnHomeSettings').addEventListener('click', () => {
        if (!checkBothBrakesOnly()) return;
        api.post('/xy/home');
    });
    $('btnHomeXSettings').addEventListener('click', () => {
        if (!checkBrakeXOnly()) return;
        api.post('/xy/home/x');
    });
    $('btnHomeYSettings').addEventListener('click', () => {
        if (!checkBrakeYOnly()) return;
        api.post('/xy/home/y');
    });

    // Brake control buttons in Settings
    $('btnBrakeXSettings').addEventListener('click', toggleBrakeX);
    $('btnBrakeYSettings').addEventListener('click', toggleBrakeY);

    // Enforce uppercase for device name
    $('editName').addEventListener('input', (e) => {
        e.target.value = e.target.value.toUpperCase();
    });
}

// Update XY position on settings tab
function updateSettingsXYPos(status) {
    const xy = status.xy_table || {};
    const sensors = status.sensors || {};
    // Check E-STOP sensor directly for immediate response
    const estopSensorActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;
    // Show position as invalid when E-STOP active or not homed
    const xPos = (xy.x_homed && !estopSensorActive) ? (xy.x || 0).toFixed(2) : '?.??';
    const yPos = (xy.y_homed && !estopSensorActive) ? (xy.y || 0).toFixed(2) : '?.??';
    const posDisplay = $('settingsXYPos');
    if (posDisplay) {
        posDisplay.textContent = `X: ${xPos}  Y: ${yPos}`;
        // Add warning class when E-STOP active or not homed
        if (!xy.x_homed || !xy.y_homed || estopSensorActive) {
            posDisplay.classList.add('position-invalid');
        } else {
            posDisplay.classList.remove('position-invalid');
        }
    }
}

// Initialize Application
function init() {
    initTabs();
    initControlTab();
    initXYTab();
    initSettingsTab();

    // Start polling
    updateStatus();
    setInterval(updateStatus, POLL_INTERVAL);

    // Load initial data
    loadDevices();
}

// Start when DOM ready
document.addEventListener('DOMContentLoaded', init);

// Export for inline handlers
window.selectDevice = selectDevice;
window.toggleRelay = toggleRelay;
window.relayOn = relayOn;
window.relayOff = relayOff;
window.relayPulse = relayPulse;
window.goToCoord = goToCoord;
window.removeCoordRow = removeCoordRow;
window.updateTypeStyle = updateTypeStyle;
