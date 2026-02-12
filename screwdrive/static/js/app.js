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
    brakeY: false,  // true = brake released (relay ON), false = brake engaged (relay OFF)
    // Auth state
    user: null,
    allowedTabs: [],
    editingUser: null,  // Username being edited (null = creating new user)
    deviceGroups: [],  // List of device group names
    editingGroup: null,  // Group name being edited
    fixtures: [],      // List of fixtures (оснастки)
    selectedFixture: null,
    editingFixture: null,
    settingsMode: 'devices'  // 'devices' or 'fixtures'
};

// Cycle state flags (need to be at top for updateStatusTab to access)
let cycleInProgress = false;
let initializationInProgress = false;
let cycleAborted = false;
let areaMonitoringActive = false;
let totalCyclesCompleted = 0;

// Last known server UI state timestamp (for sync detection)
let lastServerStateTime = 0;

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

// ========== UI STATE SYNC (Web <-> Desktop) ==========

async function syncUIStateToServer(cycleState, message = '', progressPercent = 0, currentStep = '') {
    // Sync local UI state to server for desktop UI to pick up
    try {
        await api.post('/ui/state', {
            selected_device: state.selectedDevice,
            cycle_state: cycleState,
            initialized: cycleState === 'READY' || cycleState === 'RUNNING' || cycleState === 'COMPLETED',
            holes_completed: parseInt($('cycleProgress').textContent.split('/')[0].trim()) || 0,
            total_holes: parseInt($('cycleProgress').textContent.split('/')[1]?.trim()) || 0,
            cycles_completed: totalCyclesCompleted,
            message: message,
            progress_percent: progressPercent,
            current_step: currentStep,
            source: 'web'
        });
    } catch (e) {
        console.error('UI state sync failed:', e);
    }
}

async function checkServerUIState() {
    // Check if server UI state was updated by another client (desktop)
    try {
        const serverState = await api.get('/ui/state');

        // Always update if desktop is operating (regardless of timestamp)
        const desktopIsOperating = serverState.operator === 'desktop';

        // If server state is newer and was updated by desktop, OR desktop is actively operating
        if ((serverState.updated_at > lastServerStateTime && serverState.updated_by === 'desktop') || desktopIsOperating) {
            lastServerStateTime = serverState.updated_at;

            // Update local state from server
            if (serverState.selected_device !== state.selectedDevice) {
                state.selectedDevice = serverState.selected_device;
                $('deviceSelect').value = serverState.selected_device || '';
                renderDeviceList();
            }

            // Update cycle status panel from server
            $('cycleState').textContent = serverState.cycle_state || 'IDLE';
            $('currentDevice').textContent = serverState.selected_device || '-';
            $('cycleProgress').textContent = `${serverState.holes_completed || 0} / ${serverState.total_holes || 0}`;

            // Update init status card if desktop is running initialization or cycle
            if (desktopIsOperating && (serverState.cycle_state === 'INITIALIZING' || serverState.cycle_state === 'RUNNING')) {
                const card = $('initStatusCard');
                const statusText = $('initStatusText');
                const progressBar = $('initProgressBar');

                card.style.display = 'block';
                card.className = 'card';
                statusText.textContent = serverState.current_step || serverState.message || 'Операція виконується...';
                statusText.className = 'init-status-text';
                progressBar.style.width = (serverState.progress_percent || 0) + '%';
                progressBar.className = 'init-progress-bar';

                // Disable buttons while desktop is operating
                $('btnInit').disabled = true;
                $('btnCycleStart').disabled = true;
            }

            // Update cycles count from server
            if (serverState.cycles_completed > totalCyclesCompleted) {
                totalCyclesCompleted = serverState.cycles_completed;
                $('cycleCount').textContent = totalCyclesCompleted;
            }

            // Re-enable buttons if desktop finished
            if (!desktopIsOperating && serverState.cycle_state === 'READY') {
                $('btnInit').disabled = false;
                $('btnCycleStart').disabled = false;
            }
        }

        // Update our timestamp if we're the latest
        if (serverState.updated_by === 'web') {
            lastServerStateTime = serverState.updated_at;
        }

        // Store server state for conflict checking
        state.serverUIState = serverState;

    } catch (e) {
        // Ignore - server might not have the endpoint yet
    }
}

async function syncDeviceSelection(deviceKey) {
    // Sync device selection to server
    try {
        await api.post('/ui/select-device', {
            device: deviceKey,
            source: 'web'
        });
    } catch (e) {
        console.error('Device selection sync failed:', e);
    }
}

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
                loadFixtures();
            } else if (tabId === 'stats') {
                loadCycleHistory();
                populateStatsDropdowns();
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

        // Check for UI state changes from desktop app (only if not in active cycle)
        if (!cycleInProgress && !initializationInProgress) {
            checkServerUIState();
        }

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
    // Cycle status - frontend manages this entirely via updateCycleStatusPanel()
    // Server cycle state is not used because cycles run on frontend
    // We only update if NO device is selected locally (initial page state)
    if (!state.selectedDevice && !cycleInProgress && !initializationInProgress) {
        const cycle = status.cycle || {};
        $('cycleState').textContent = cycle.state || 'IDLE';
        $('currentDevice').textContent = cycle.current_device || '-';
        $('cycleProgress').textContent = `${cycle.holes_completed || 0} / ${cycle.total_holes || 0}`;
    }
    // Cycles count is always managed by frontend
    $('cycleCount').textContent = totalCyclesCompleted;

    // Global cycle count from server (RAM only, resets on reboot)
    if (status.global_cycle_count !== undefined) {
        $('globalCycleCount').textContent = status.global_cycle_count;
    }

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

// Sensor names mapping with state-dependent labels
const SENSOR_NAMES = {
    'alarm_x': { active: '⚠️ Аларм драйвера X', inactive: '✓ Драйвер X OK' },
    'alarm_y': { active: '⚠️ Аларм драйвера Y', inactive: '✓ Драйвер Y OK' },
    'area_sensor': { active: '🚫 Завіса заблокована', inactive: '✓ Завіса вільна' },
    'ped_start': { active: '⏺ Педаль натиснута', inactive: '○ Педаль вільна' },
    'ger_c2_up': { active: '▲ Циліндр вгорі', inactive: '▼ Циліндр внизу' },
    'ger_c2_down': { active: '🛑 Циліндр внизу!', inactive: '✓ Циліндр не внизу' },
    'ind_scrw': { active: '● Гвинт є', inactive: '○ Гвинта немає' },
    'do2_ok': { active: '✓ Момент OK', inactive: '○ Момент не досягнуто' },
    'emergency_stop': { active: '🛑 АВАРІЯ!', inactive: '✓ Аварійна OK' }
};

// Relay names mapping with state-dependent labels (for Status tab)
const RELAY_NAMES = {
    'r01_pit': { on: '● Живильник ВКЛ', off: '○ Живильник ВИКЛ' },
    'r02_brake_x': { on: '● Гальмо X відпущено', off: '○ Гальмо X затиснуто' },
    'r03_brake_y': { on: '● Гальмо Y відпущено', off: '○ Гальмо Y затиснуто' },
    'r04_c2': { on: '▼ Циліндр опускається', off: '▲ Циліндр піднято' },
    'r05_di4_free': { on: '● Вільний хід ВКЛ', off: '○ Вільний хід ВИКЛ' },
    'r06_di1_pot': { on: '● Режим моменту', off: '○ Режим швидкості' },
    'r07_di5_tsk0': { on: '● Задача біт 0', off: '○ Задача біт 0' },
    'r08_di6_tsk1': { on: '● Задача біт 1', off: '○ Задача біт 1' },
    'r09_pwr_x': { on: '🔴 Живлення X ВИКЛ', off: '🟢 Живлення X ВКЛ' },
    'r10_pwr_y': { on: '🔴 Живлення Y ВИКЛ', off: '🟢 Живлення Y ВКЛ' }
};

// Relay control names (compact, for Control tab)
const RELAY_CONTROL_NAMES = {
    'r01_pit': 'Живильник',
    'r02_brake_x': 'Гальмо X',
    'r03_brake_y': 'Гальмо Y',
    'r04_c2': 'Циліндр',
    'r05_di4_free': 'Вільний хід',
    'r06_di1_pot': 'Режим моменту',
    'r07_di5_tsk0': 'Задача 0',
    'r08_di6_tsk1': 'Задача 1',
    'r09_pwr_x': 'Живлення X',
    'r10_pwr_y': 'Живлення Y'
};

function getRelayControlName(name) {
    return RELAY_CONTROL_NAMES[name] || formatName(name);
}

function getSensorLabel(name, isActive) {
    const mapping = SENSOR_NAMES[name];
    if (mapping) {
        return isActive ? mapping.active : mapping.inactive;
    }
    return formatName(name);
}

function getRelayLabel(name, isOn) {
    const mapping = RELAY_NAMES[name];
    if (mapping) {
        return isOn ? mapping.on : mapping.off;
    }
    return formatName(name);
}

function updateSensors(sensors) {
    const grid = $('sensorGrid');
    grid.innerHTML = '';

    for (const [name, value] of Object.entries(sensors)) {
        const isActive = value === 'ACTIVE' || value === true;
        const label = getSensorLabel(name, isActive);
        grid.innerHTML += `
            <div class="sensor-item">
                <span class="indicator ${isActive ? 'active' : ''}"></span>
                <span class="name">${label}</span>
            </div>
        `;
    }
}

function updateRelays(relays) {
    const grid = $('relayGrid');
    grid.innerHTML = '';

    for (const [name, value] of Object.entries(relays)) {
        const isOn = value === 'ON' || value === true;
        const label = getRelayLabel(name, isOn);
        grid.innerHTML += `
            <div class="relay-item">
                <span class="indicator ${isOn ? 'on' : ''}"></span>
                <span class="name">${label}</span>
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
            const displayName = getRelayControlName(name);
            grid.innerHTML += `
                <div class="relay-control-new" data-relay-name="${name}">
                    <div class="relay-header">
                        <span class="relay-name">${displayName}</span>
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

    // START button - run screwing cycle
    $('btnCycleStart').addEventListener('click', runCycle);

    // Device select dropdown - sync selection with state and update status panel
    $('deviceSelect').addEventListener('change', (e) => {
        const selectedKey = e.target.value;
        if (selectedKey) {
            state.selectedDevice = selectedKey;
            renderDeviceList();
            // Update Cycle Status panel to show selected device
            updateCycleStatusPanel('IDLE', selectedKey, 0, 0);
            // Sync to server for desktop UI
            syncDeviceSelection(selectedKey);
        } else {
            // No device selected
            state.selectedDevice = null;
            updateCycleStatusPanel('IDLE', '-', 0, 0);
            syncDeviceSelection(null);
        }
    });

    // STOP button - abort cycle, safety shutdown, and reset device selection
    $('btnCycleStop').addEventListener('click', async () => {
        cycleAborted = true;
        areaMonitoringActive = false;
        await safetyShutdown();
        try {
            await api.post('/xy/estop');
        } catch (e) {}
        updateCycleStatus('Цикл зупинено оператором', 'error');
        updateCycleStatusPanel('STOPPED', '-', 0, 0);
        $('btnCycleStart').disabled = false;
        $('btnInit').disabled = false;
        cycleInProgress = false;

        // Reset device selection
        state.selectedDevice = null;
        $('deviceSelect').value = '';
        renderDeviceList();

        // Sync stopped state to server
        syncUIStateToServer('STOPPED', 'Цикл зупинено оператором');
        syncDeviceSelection(null);
    });

    // E-STOP button
    $('btnEstop').addEventListener('click', async () => {
        cycleAborted = true;
        areaMonitoringActive = false;
        await safetyShutdown();
        try {
            await api.post('/xy/estop');
        } catch (e) {}
        try {
            await api.post('/cycle/estop');
        } catch (e) {}
    });

    $('btnClearEstop').addEventListener('click', () => api.post('/cycle/clear_estop'));
}

// ========== INITIALIZATION SEQUENCE ==========

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
        const state = (status.state || '').toLowerCase();
        if (state === 'error' || state === 'estop') {
            throw new Error('Homing error: ' + (status.last_error || status.state));
        }
        await new Promise(resolve => setTimeout(resolve, 200));
    }
    return false;
}

/**
 * Check driver alarms.
 * @returns {Promise<{alarm_x: boolean, alarm_y: boolean}>} Alarm status
 */
async function checkDriverAlarms() {
    try {
        const sensors = await api.get('/sensors');
        return {
            alarm_x: sensors.alarm_x === 'ACTIVE',
            alarm_y: sensors.alarm_y === 'ACTIVE'
        };
    } catch (e) {
        console.error('Failed to check driver alarms:', e);
        return { alarm_x: false, alarm_y: false };
    }
}

/**
 * Power cycle motor drivers by toggling power relays.
 * Turns power OFF for 1 second, then back ON.
 * @param {boolean} resetX - Reset X axis driver
 * @param {boolean} resetY - Reset Y axis driver
 */
async function powerCycleDrivers(resetX = true, resetY = true) {
    const axisParts = [];
    if (resetX) axisParts.push('X');
    if (resetY) axisParts.push('Y');

    if (axisParts.length === 0) return;

    const axisStr = axisParts.join(' та ');
    updateInitStatus(`Перезапуск драйвера ${axisStr}...`, 0);

    try {
        // Turn power OFF by turning relay ON (inverted logic)
        if (resetX) {
            await api.post('/relays/r09_pwr_x', { state: 'on' });
        }
        if (resetY) {
            await api.post('/relays/r10_pwr_y', { state: 'on' });
        }

        await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second

        // Turn power ON by turning relay OFF
        if (resetX) {
            await api.post('/relays/r09_pwr_x', { state: 'off' });
        }
        if (resetY) {
            await api.post('/relays/r10_pwr_y', { state: 'off' });
        }

        await new Promise(resolve => setTimeout(resolve, 500)); // Wait for stabilization
    } catch (e) {
        console.error('Failed to power cycle drivers:', e);
    }
}

/**
 * Check for driver alarms and reset them by power cycling.
 * @returns {Promise<boolean>} True if alarm was found and reset attempted
 */
async function checkAndResetAlarms() {
    const { alarm_x, alarm_y } = await checkDriverAlarms();
    if (alarm_x || alarm_y) {
        await powerCycleDrivers(alarm_x, alarm_y);
        return true;
    }
    return false;
}

/**
 * Check if any driver alarm is active during cycle.
 * @returns {Promise<string>} Alarm message if active, empty string if OK
 */
async function checkDriverAlarmsDuringCycle() {
    try {
        const sensors = await api.get('/sensors');

        if (sensors.alarm_x === 'ACTIVE') {
            return 'АВАРІЯ: Аларм драйвера осі X!';
        }
        if (sensors.alarm_y === 'ACTIVE') {
            return 'АВАРІЯ: Аларм драйвера осі Y!';
        }
    } catch (e) {
        // If we can't check, continue operation
    }
    return '';
}

/**
 * Emergency stop XY table - cancels all commands on Slave Pi.
 * Sends multiple attempts to ensure it's received.
 */
async function emergencyStopXY() {
    // Try multiple times to ensure command is received
    for (let i = 0; i < 3; i++) {
        try {
            await api.post('/xy/estop', {});
            break;
        } catch (e) {
            console.error('Failed to emergency stop XY, attempt ' + (i + 1) + ':', e);
            await new Promise(resolve => setTimeout(resolve, 100));
        }
    }

    // Also try general emergency stop
    try {
        await api.post('/emergency_stop', {});
    } catch (e) {
        // Ignore
    }
}

/**
 * Check for driver alarms and throw if detected.
 * Used to add alarm checks between operations.
 */
async function checkAlarmAndThrow() {
    const alarm = await checkDriverAlarmsDuringCycle();
    if (alarm) {
        // Full emergency shutdown
        await emergencyStopXY();
        await safetyShutdown();
        throw new Error('DRIVER_ALARM:' + alarm);
    }
}

async function runInitialization() {
    if (initializationInProgress) {
        alert('Ініціалізація вже виконується');
        return;
    }

    // Check if desktop is already operating
    if (state.serverUIState && state.serverUIState.operator === 'desktop') {
        alert('Desktop UI виконує операцію. Зачекайте завершення.');
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

    // Update cycle status panel
    updateCycleStatusPanel('INITIALIZING', deviceKey, 0, 0);

    // Helper to sync progress to server
    const syncProgress = (msg, pct) => {
        syncUIStateToServer('INITIALIZING', msg, pct, msg);
    };

    const MAX_RETRIES = 3;
    let retryCount = 0;

    // Retry loop for automatic alarm recovery
    while (retryCount < MAX_RETRIES) {
        try {
            // Step 0: Check and reset driver alarms if needed
            updateInitStatus('Перевірка алармів драйверів...', 2);
            syncProgress('Перевірка алармів драйверів...', 2);

            if (await checkAndResetAlarms()) {
                // Alarm was reset, notify and continue
                updateInitStatus('Аларм скинуто, продовжуємо...', 3);
                await new Promise(resolve => setTimeout(resolve, 500));
            }

            // Step 0.1: Check E-STOP
            updateInitStatus('Перевірка аварійної кнопки...', 5);
            syncProgress('Перевірка аварійної кнопки...', 5);
            const safety = await api.get('/sensors/safety');
            if (safety.estop_pressed) {
                throw new Error('Аварійна кнопка натиснута! Відпустіть її перед ініціалізацією.');
            }

            // Step 0.2: Check Slave Pi connection
            updateInitStatus('Перевірка підключення XY столу...', 10);
            syncProgress('Перевірка підключення XY столу...', 10);
            const xyStatus = await api.get('/xy/status');
            if (!xyStatus.connected) {
                throw new Error('XY стіл не підключено! Перевірте з\'єднання з Raspberry Pi.');
            }

            // Step 1: Check and release brakes
            updateInitStatus('Перевірка та відпускання гальм...', 15);
            syncProgress('Перевірка та відпускання гальм...', 15);
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

            // Check alarms before homing
            if (await checkAndResetAlarms()) {
                retryCount++;
                updateInitStatus(`Аларм виявлено, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                continue;
            }

            // Step 1.1: Homing
            updateInitStatus('Виконується хомінг XY столу...', 25);
            syncProgress('Виконується хомінг XY столу...', 25);
            const homeResponse = await api.post('/xy/home');
            if (homeResponse.status !== 'homed') {
                throw new Error('Не вдалося запустити хомінг');
            }

            // Wait for homing to complete with 15 second timeout, checking alarms
            let homingAlarm = false;
            const homingStart = Date.now();
            while (Date.now() - homingStart < 15000) {
                // Check for alarms during homing
                const { alarm_x, alarm_y } = await checkDriverAlarms();
                if (alarm_x || alarm_y) {
                    await powerCycleDrivers(alarm_x, alarm_y);
                    homingAlarm = true;
                    break;
                }

                const xyStatus = await api.get('/xy/status');
                const pos = xyStatus.position || xyStatus;
                if (pos.x_homed && pos.y_homed) {
                    break;
                }
                const state = (xyStatus.state || '').toLowerCase();
                if (state === 'error' || state === 'estop') {
                    throw new Error('Помилка хомінгу: ' + (xyStatus.last_error || state));
                }
                await new Promise(resolve => setTimeout(resolve, 200));
            }

            // If alarm during homing, restart
            if (homingAlarm) {
                retryCount++;
                updateInitStatus(`Аларм під час хомінгу, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                continue;
            }

            // Short delay after homing
            await new Promise(resolve => setTimeout(resolve, 500));

            // Check alarms after homing
            if (await checkAndResetAlarms()) {
                retryCount++;
                updateInitStatus(`Аларм після хомінгу, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                continue;
            }

            // Step 2: Cylinder pulse test (700ms impulse, then wait for return)
            updateInitStatus('Тест циліндра...', 45);
            syncProgress('Тест циліндра...', 45);
            await api.post('/relays/r04_c2', { state: 'on' });
            await new Promise(resolve => setTimeout(resolve, 700));
            await api.post('/relays/r04_c2', { state: 'off' });

            // Wait for cylinder to return up
            updateInitStatus('Очікування повернення циліндра...', 55);
            syncProgress('Очікування повернення циліндра...', 55);
            const cylinderRaised = await waitForSensor('ger_c2_up', 'ACTIVE', 5000);
            if (!cylinderRaised) {
                throw new Error('Циліндр не піднявся за 5 секунд. Перевірте пневматику.');
            }

            // Step 5: Select task by setting R07 and R08 relays
            updateInitStatus('Вибір задачі для закручування...', 75);
            syncProgress('Вибір задачі для закручування...', 75);
            const task = device.task;

            if (task === '0') {
                await api.post('/relays/r07_di5_tsk0', { state: 'off' });
                await api.post('/relays/r08_di6_tsk1', { state: 'off' });
            } else if (task === '1') {
                await api.post('/relays/r08_di6_tsk1', { state: 'off' });
                await api.post('/relays/r07_di5_tsk0', { state: 'on' });
            } else if (task === '2') {
                await api.post('/relays/r07_di5_tsk0', { state: 'off' });
                await api.post('/relays/r08_di6_tsk1', { state: 'on' });
            } else if (task === '3') {
                await api.post('/relays/r07_di5_tsk0', { state: 'on' });
                await api.post('/relays/r08_di6_tsk1', { state: 'on' });
            }
            await new Promise(resolve => setTimeout(resolve, 300));

            // Check alarms before move
            if (await checkAndResetAlarms()) {
                retryCount++;
                updateInitStatus(`Аларм перед рухом, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                continue;
            }

            // Load work offsets (G92-like)
            let offsetX = 0, offsetY = 0;
            try {
                const offsets = await api.get('/offsets');
                offsetX = offsets.x || 0;
                offsetY = offsets.y || 0;
            } catch (e) {
                console.warn('Failed to load offsets, using 0,0');
            }

            // Step 6: Move to work position (physical coordinates)
            // Device's work_x/work_y are stored as physical coordinates (relative to limit switches)
            updateInitStatus('Виїзд до оператора...', 85);
            syncProgress('Виїзд до оператора...', 85);

            const workX = device.work_x;
            const workY = device.work_y;
            const workFeed = device.work_feed || 5000;

            if (workX === null || workX === undefined || workY === null || workY === undefined) {
                throw new Error('Робоча позиція не задана для цього девайсу. Вкажіть Робоча X та Робоча Y в налаштуваннях.');
            }

            // Use physical coordinates directly (no offset applied)
            // Sequential move: X first, then Y (safe for initialization)
            const moveResponse = await api.post('/xy/move_seq', { x: workX, y: workY, feed: workFeed });
            if (moveResponse.status !== 'ok') {
                // Check if alarm caused the failure
                if (await checkAndResetAlarms()) {
                    retryCount++;
                    updateInitStatus(`Аларм під час руху, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                    continue;
                }
                throw new Error('Не вдалося виїхати до робочої позиції');
            }

            // Wait a bit for the move to complete
            await new Promise(resolve => setTimeout(resolve, 500));

            // Final alarm check
            if (await checkAndResetAlarms()) {
                retryCount++;
                updateInitStatus(`Аларм після руху, перезапуск (спроба ${retryCount}/${MAX_RETRIES})...`, 0);
                continue;
            }

            // Success!
            updateInitStatus('Ініціалізація завершена. Очікування натискання START...', 100, 'success');
            syncUIStateToServer('READY', 'Готово до запуску', 100, 'Ініціалізація завершена');
            updateCycleStatusPanel('READY', deviceKey, 0, 0);

            // Enable START button
            $('btnCycleStart').disabled = false;

            // Exit the retry loop on success
            initializationInProgress = false;
            $('btnInit').disabled = false;
            return;

        } catch (error) {
            console.error('Initialization error:', error);
            updateInitStatus('ПОМИЛКА: ' + error.message, 100, 'error');
            updateCycleStatusPanel('INIT_ERROR', deviceKey, 0, 0);
            syncUIStateToServer('INIT_ERROR', error.message, 0, 'Помилка ініціалізації');

            // Turn off cylinder relay for safety
            try {
                await api.post('/relays/r04_c2', { state: 'off' });
            } catch (e) {}

            // Exit on non-alarm errors
            initializationInProgress = false;
            $('btnInit').disabled = false;
            return;
        }
    }

    // Max retries exceeded
    updateInitStatus(`Не вдалося завершити ініціалізацію після ${MAX_RETRIES} спроб скидання алармів.`, 100, 'error');
    updateCycleStatusPanel('INIT_ERROR', deviceKey, 0, 0);
    syncUIStateToServer('INIT_ERROR', 'Перевищено ліміт спроб', 0, 'Помилка ініціалізації');
    initializationInProgress = false;
    $('btnInit').disabled = false;
}

// ========== CYCLE (SCREWING) ==========

// Update Cycle Status panel (State, Device, Progress, Cycles)
function updateCycleStatusPanel(cycleState, deviceName, holesCompleted, totalHoles) {
    $('cycleState').textContent = cycleState || '-';
    $('currentDevice').textContent = deviceName || '-';
    $('cycleProgress').textContent = `${holesCompleted || 0} / ${totalHoles || 0}`;
    $('cycleCount').textContent = totalCyclesCompleted;

    // Sync state to server for desktop UI (don't await to avoid blocking)
    syncUIStateToServer(cycleState);
}

function updateCycleStatus(text, statusClass = '') {
    const statusText = $('initStatusText');
    const card = $('initStatusCard');

    card.style.display = 'block';
    card.className = 'card' + (statusClass ? ' ' + statusClass : '');
    statusText.textContent = text;
    statusText.className = 'init-status-text' + (statusClass ? ' ' + statusClass : '');
}

async function checkAreaSensor() {
    if (!areaMonitoringActive) return true;

    try {
        const response = await api.get('/sensors/area_sensor');
        if (response.state === 'ACTIVE') {
            // Light barrier triggered - someone in work area
            return false;
        }
        return true;
    } catch (e) {
        console.error('Area sensor check failed:', e);
        return false;
    }
}

async function waitForSensorWithAreaCheck(sensorName, expectedState, timeout = 10000, pollInterval = 100) {
    // Wait for sensor to reach expected state
    // Also checks for driver alarms and area sensor during wait
    const startTime = Date.now();
    while (Date.now() - startTime < timeout) {
        // Check for driver alarms during sensor wait
        const alarm = await checkDriverAlarmsDuringCycle();
        if (alarm) {
            // Emergency stop XY table and turn off dangerous relays
            await emergencyStopXY();
            await safetyShutdown();
            throw new Error('DRIVER_ALARM:' + alarm);
        }

        // Check area sensor
        if (!await checkAreaSensor()) {
            throw new Error('AREA_BLOCKED');
        }

        const response = await api.get(`/sensors/${sensorName}`);
        if (response.state === expectedState) {
            return true;
        }
        await new Promise(resolve => setTimeout(resolve, pollInterval));
    }
    return false;
}

async function waitForMove(timeout = 30000) {
    // Wait for XY table to finish moving
    // Also checks for driver alarms and area sensor during movement
    const startTime = Date.now();
    while (Date.now() - startTime < timeout) {
        // Check for driver alarms during movement
        const alarm = await checkDriverAlarmsDuringCycle();
        if (alarm) {
            // Emergency stop XY table and turn off dangerous relays
            await emergencyStopXY();
            await safetyShutdown();
            throw new Error('DRIVER_ALARM:' + alarm);
        }

        // Check area sensor
        if (!await checkAreaSensor()) {
            throw new Error('AREA_BLOCKED');
        }

        const status = await api.get('/xy/status');
        const state = (status.state || '').toLowerCase();
        if (state === 'ready') {
            return true;
        }
        if (state === 'error' || state === 'estop') {
            throw new Error('XY table error: ' + status.state);
        }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    return false;
}

async function performScrewing() {
    // Check for alarms before starting screwing
    await checkAlarmAndThrow();

    // 1. Feed screw with retry logic (max 3 attempts)
    let screwDetected = false;
    const maxAttempts = 3;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        // Check for alarms before each attempt
        await checkAlarmAndThrow();

        // Pulse R01 (200ms) to feed screw
        await api.post('/relays/r01_pit', { state: 'pulse', duration: 0.2 });

        // Wait 1 second for screw to pass sensor (also checks alarms)
        screwDetected = await waitForSensorWithAreaCheck('ind_scrw', 'ACTIVE', 1000, 50);

        if (screwDetected) {
            break; // Screw detected, continue with screwing
        }

        if (attempt < maxAttempts) {
            console.log(`Screw not detected, retry ${attempt + 1}/${maxAttempts}`);
        }
    }

    if (!screwDetected) {
        throw new Error(`Гвинт не виявлено після ${maxAttempts} спроб. Перевірте живильник.`);
    }

    // Check for alarms before torque mode
    await checkAlarmAndThrow();

    // 3. Turn ON R06 (torque mode)
    await api.post('/relays/r06_di1_pot', { state: 'on' });

    // Check for alarms before lowering cylinder
    await checkAlarmAndThrow();

    // 4. Lower cylinder (R04 ON)
    await api.post('/relays/r04_c2', { state: 'on' });

    // 5. Wait for DO2_OK (torque reached) with 2 second timeout
    // waitForSensorWithAreaCheck already checks for alarms
    const torqueReached = await waitForSensorWithAreaCheck('do2_ok', 'ACTIVE', 2000, 50);

    // If torque not reached - safe shutdown and return to operator
    if (!torqueReached) {
        // Raise cylinder (R04 OFF)
        await api.post('/relays/r04_c2', { state: 'off' });

        // Turn OFF R06
        await api.post('/relays/r06_di1_pot', { state: 'off' });

        // Wait for cylinder to go up
        await waitForSensorWithAreaCheck('ger_c2_up', 'ACTIVE', 5000, 50);

        // Free run pulse - R05 (200ms) before returning to operator
        await api.post('/relays/r05_di4_free', { state: 'pulse', duration: 0.2 });

        // Throw special error to trigger return to operator
        throw new Error('TORQUE_NOT_REACHED');
    }

    // Check for alarms after torque reached
    await checkAlarmAndThrow();

    // SUCCESS PATH:
    // 6. Turn OFF R06 (torque mode) first
    await api.post('/relays/r06_di1_pot', { state: 'off' });

    // 7. Raise cylinder (R04 OFF)
    await api.post('/relays/r04_c2', { state: 'off' });

    // 8. Free run pulse - R05 (200ms)
    await api.post('/relays/r05_di4_free', { state: 'pulse', duration: 0.2 });

    // 9. Wait for cylinder to go up (GER_C2_UP) - also checks alarms
    const cylinderUp = await waitForSensorWithAreaCheck('ger_c2_up', 'ACTIVE', 5000, 50);

    if (!cylinderUp) {
        throw new Error('Циліндр не піднявся за 5 секунд');
    }

    // Final alarm check after screwing complete
    await checkAlarmAndThrow();

    return true;
}

async function returnToOperator() {
    const deviceKey = $('deviceSelect').value;
    const device = state.devices.find(d => d.key === deviceKey);

    if (device && device.work_x !== null && device.work_y !== null) {
        await api.post('/xy/move', {
            x: device.work_x,
            y: device.work_y,
            feed: device.work_feed || 5000
        });
    }
}

async function safetyShutdown() {
    // Turn off all potentially dangerous relays
    try { await api.post('/relays/r04_c2', { state: 'off' }); } catch (e) {}
    try { await api.post('/relays/r06_di1_pot', { state: 'off' }); } catch (e) {}
}

async function areaBarrierShutdown() {
    // Shutdown for light barrier trigger
    // R04 OFF (cylinder up), R06 OFF (motor off), R05 pulse (stop spindle)
    try { await api.post('/relays/r04_c2', { state: 'off' }); } catch (e) {}
    try { await api.post('/relays/r06_di1_pot', { state: 'off' }); } catch (e) {}
    try { await api.post('/relays/r05_di4_free', { state: 'pulse', duration: 0.3 }); } catch (e) {}
}

function showAreaBlockedDialog() {
    // Create modal overlay
    const overlay = document.createElement('div');
    overlay.id = 'areaBlockedOverlay';
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 10000;
    `;

    // Create dialog
    const dialog = document.createElement('div');
    dialog.style.cssText = `
        background: #2b2b2b;
        border: 3px solid #f44336;
        border-radius: 12px;
        padding: 30px;
        text-align: center;
        min-width: 350px;
    `;

    // Warning text
    const text = document.createElement('div');
    text.innerHTML = `
        <div style="color: #ffffff; font-size: 24px; font-weight: bold; margin-bottom: 15px;">
            ⚠️ СВІТЛОВА ЗАВІСА!
        </div>
        <div style="color: #ffffff; font-size: 16px; margin-bottom: 25px;">
            Закручування зупинено.<br>
            Приберіть руки з робочої зони.
        </div>
    `;
    dialog.appendChild(text);

    // ВИЇЗД button
    const btn = document.createElement('button');
    btn.textContent = 'ВИЇЗД';
    btn.style.cssText = `
        background: #4CAF50;
        color: #ffffff;
        font-size: 20px;
        font-weight: bold;
        border: none;
        border-radius: 8px;
        padding: 15px 50px;
        cursor: pointer;
    `;
    btn.onclick = async () => {
        overlay.remove();
        updateCycleStatus('Виїзд до оператора...', 'info');
        try {
            await returnToOperator();
            updateCycleStatus('Натисніть START для повторного циклу.', 'info');
        } catch (e) {
            updateCycleStatus('Помилка виїзду: ' + e.message, 'error');
        }
        $('btnCycleStart').disabled = false;
    };
    dialog.appendChild(btn);

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
}

async function runCycle() {
    if (cycleInProgress) {
        alert('Цикл вже виконується');
        return;
    }

    // Check if desktop is already operating
    if (state.serverUIState && state.serverUIState.operator === 'desktop') {
        alert('Desktop UI виконує операцію. Зачекайте завершення.');
        return;
    }

    const deviceKey = $('deviceSelect').value;
    if (!deviceKey) {
        alert('Виберіть девайс');
        return;
    }

    // Get device with full steps data
    let device;
    try {
        device = await api.get(`/devices/${deviceKey}`);
    } catch (e) {
        alert('Не вдалося завантажити дані девайсу');
        return;
    }

    if (!device.steps || device.steps.length === 0) {
        alert('Девайс не має координат для закручування');
        return;
    }

    cycleInProgress = true;
    cycleAborted = false;
    areaMonitoringActive = false;

    $('btnCycleStart').disabled = true;
    $('btnInit').disabled = true;

    let holesCompleted = 0;
    const totalHoles = device.steps.filter(s => (s.type || '').toLowerCase() === 'work').length;

    // Helper to sync cycle progress
    const syncCycleProgress = (msg, holes) => {
        const pct = totalHoles > 0 ? Math.round((holes / totalHoles) * 100) : 0;
        syncUIStateToServer('RUNNING', msg, pct, msg);
    };

    try {
        // Check E-STOP before starting
        const safety = await api.get('/sensors/safety');
        if (safety.estop_pressed) {
            throw new Error('Аварійна кнопка натиснута!');
        }

        // Check for driver alarms before starting cycle
        // If alarm is active, stop immediately - device must be removed and machine reinitialized
        const alarmBeforeStart = await checkDriverAlarmsDuringCycle();
        if (alarmBeforeStart) {
            throw new Error('DRIVER_ALARM:' + alarmBeforeStart + '\nВийміть деталь та виконайте переініціалізацію машини.');
        }

        // Load work offsets (G92-like) - device coordinates are relative to work zero
        let cycleOffsetX = 0, cycleOffsetY = 0;
        try {
            const offsets = await api.get('/offsets');
            cycleOffsetX = offsets.x || 0;
            cycleOffsetY = offsets.y || 0;
            console.log('Cycle offsets loaded:', cycleOffsetX, cycleOffsetY);
        } catch (e) {
            console.warn('Failed to load offsets for cycle, using 0,0');
        }

        updateCycleStatus(`Цикл запущено. Винтів: 0 / ${totalHoles}`);
        updateCycleStatusPanel('RUNNING', deviceKey, 0, totalHoles);
        syncCycleProgress(`Цикл запущено. Винтів: 0 / ${totalHoles}`, 0);

        // Process each step
        for (let i = 0; i < device.steps.length; i++) {
            if (cycleAborted) {
                throw new Error('Цикл перервано');
            }

            // Check for alarms at the start of each step
            await checkAlarmAndThrow();

            const step = device.steps[i];
            const stepNum = i + 1;
            const stepType = (step.type || 'free').toLowerCase();  // Normalize to lowercase

            // Parse coordinates as floats to ensure correct format
            const stepX = parseFloat(step.x);
            const stepY = parseFloat(step.y);
            const stepFeed = parseFloat(step.feed) || 5000;

            // Apply offset: device coords are relative to work zero, add offset to get physical coords
            const physicalX = stepX + cycleOffsetX;
            const physicalY = stepY + cycleOffsetY;

            console.log(`Step ${stepNum}: type=${stepType}, x=${stepX}, y=${stepY}, physical=(${physicalX}, ${physicalY}), feed=${stepFeed}`);

            // Validate coordinates are within machine limits (check physical coords)
            if (isNaN(stepX) || isNaN(stepY)) {
                throw new Error(`Крок ${stepNum}: некоректні координати (X=${step.x}, Y=${step.y})`);
            }
            if (physicalX < 0 || physicalX > 220) {
                throw new Error(`Крок ${stepNum}: X=${physicalX} (з офсетом) за межами (0-220 мм)`);
            }
            if (physicalY < 0 || physicalY > 500) {
                throw new Error(`Крок ${stepNum}: Y=${physicalY} (з офсетом) за межами (0-500 мм)`);
            }

            if (stepType === 'free') {
                // Free movement - just move
                updateCycleStatus(`Крок ${stepNum}: Переміщення (free) X:${stepX} Y:${stepY}`);

                // Check alarm before sending move command
                await checkAlarmAndThrow();

                const moveResp = await api.post('/xy/move', { x: physicalX, y: physicalY, feed: stepFeed });
                if (moveResp.status !== 'ok') {
                    throw new Error('Помилка переміщення');
                }

                // waitForMove also checks alarms
                await waitForMove();

            } else if (stepType === 'work') {
                // Work position - move and screw

                // Enable area monitoring on first work step
                if (!areaMonitoringActive) {
                    areaMonitoringActive = true;
                    updateCycleStatus(`Контроль світлової завіси увімкнено`);
                    await new Promise(resolve => setTimeout(resolve, 300));
                }

                // Check alarm before move
                await checkAlarmAndThrow();

                // Check area sensor before moving
                if (!await checkAreaSensor()) {
                    throw new Error('AREA_BLOCKED');
                }

                updateCycleStatus(`Крок ${stepNum}: Закручування X:${stepX} Y:${stepY} (${holesCompleted + 1}/${totalHoles})`);

                // Move to position (with offset applied)
                const moveResp = await api.post('/xy/move', { x: physicalX, y: physicalY, feed: stepFeed });
                if (moveResp.status !== 'ok') {
                    throw new Error('Помилка переміщення');
                }

                await waitForMove();

                // Perform screwing operation
                await performScrewing();

                holesCompleted++;
                updateCycleStatus(`Закручено: ${holesCompleted} / ${totalHoles}`);
                updateCycleStatusPanel('RUNNING', deviceKey, holesCompleted, totalHoles);
                syncCycleProgress(`Закручено: ${holesCompleted} / ${totalHoles}`, holesCompleted);
            }
        }

        // Disable area monitoring after last screw
        areaMonitoringActive = false;

        // Cycle complete - return to operator
        updateCycleStatus('Цикл завершено. Повернення до оператора...', 'success');
        updateCycleStatusPanel('RETURNING', deviceKey, holesCompleted, totalHoles);
        syncUIStateToServer('RETURNING', 'Повернення до оператора...', 100, 'Повернення');

        await returnToOperator();
        await waitForMove();

        // Increment total cycles completed
        totalCyclesCompleted++;
        // Increment global counter on server
        try { await api.post('/stats/global_cycles/increment'); } catch(e) {}
        updateCycleStatus(`Цикл завершено! Закручено ${holesCompleted} винтів. Натисніть START для наступного.`, 'success');
        updateCycleStatusPanel('COMPLETED', deviceKey, holesCompleted, totalHoles);
        syncUIStateToServer('COMPLETED', `Цикл завершено! Закручено ${holesCompleted} винтів.`, 100, 'Цикл завершено');

        // Re-enable START for next cycle
        $('btnCycleStart').disabled = false;

    } catch (error) {
        console.error('Cycle error:', error);
        areaMonitoringActive = false;

        if (error.message === 'AREA_BLOCKED') {
            // Special shutdown for light barrier - includes R05 pulse
            await areaBarrierShutdown();

            updateCycleStatus('⚠️ СВІТЛОВА ЗАВІСА!', 'error');
            updateCycleStatusPanel('AREA_BLOCKED', deviceKey, holesCompleted, totalHoles);
            syncUIStateToServer('AREA_BLOCKED', 'Світлова завіса спрацювала', 0, 'Світлова завіса');

            // Stay in place - show dialog with ВИЇЗД button
            showAreaBlockedDialog();
        } else if (error.message === 'TORQUE_NOT_REACHED') {
            // Safety shutdown for other errors
            await safetyShutdown();
            updateCycleStatus('УВАГА: Момент не досягнуто! Повернення до оператора...', 'error');
            updateCycleStatusPanel('TORQUE_ERROR', deviceKey, holesCompleted, totalHoles);
            syncUIStateToServer('TORQUE_ERROR', 'Момент не досягнуто', 0, 'Помилка моменту');

            // Return to operator position
            try {
                await returnToOperator();
                await waitForMove();
            } catch (e) {}

            updateCycleStatus('Момент не досягнуто за 2 сек. Перевірте гвинт та повторіть.', 'error');
            updateCycleStatusPanel('PAUSED', deviceKey, holesCompleted, totalHoles);
            syncUIStateToServer('PAUSED', 'Момент не досягнуто. Перевірте гвинт.', 0, 'Пауза');
            $('btnCycleStart').disabled = false;
        } else if (error.message.startsWith('DRIVER_ALARM:')) {
            // Safety shutdown for driver alarm
            await safetyShutdown();
            // Motor driver alarm - critical error requiring device removal and reinit
            const alarmMsg = error.message.replace('DRIVER_ALARM:', '');
            const fullMsg = '🚨 АВАРІЯ ДРАЙВЕРА МОТОРА!\n' + alarmMsg +
                '\n\nДії оператора:\n1. Вийміть деталь з робочої зони\n2. Перевірте стан машини\n3. Виконайте переініціалізацію';

            updateCycleStatus(fullMsg, 'error');
            updateCycleStatusPanel('DRIVER_ALARM', deviceKey, holesCompleted, totalHoles);
            syncUIStateToServer('DRIVER_ALARM', alarmMsg, 0, 'Аварія драйвера');

            // Do NOT re-enable buttons - machine requires reinit
            // Keep START disabled, only Init should be available
            $('btnInit').disabled = false;
            $('btnCycleStart').disabled = true;
        } else {
            // Safety shutdown for generic errors
            await safetyShutdown();
            updateCycleStatus('ПОМИЛКА: ' + error.message, 'error');
            updateCycleStatusPanel('ERROR', deviceKey, holesCompleted, totalHoles);
            syncUIStateToServer('ERROR', error.message, 0, 'Помилка');
        }
    } finally {
        cycleInProgress = false;
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
    const physicalX = xy.x || 0;
    const physicalY = xy.y || 0;
    const xPos = xHomed ? physicalX.toFixed(2) : '?.??';
    const yPos = yHomed ? physicalY.toFixed(2) : '?.??';

    // Calculate work coordinates (physical - offset)
    const workX = xHomed ? (physicalX - workOffsets.x).toFixed(2) : '?.??';
    const workY = yHomed ? (physicalY - workOffsets.y).toFixed(2) : '?.??';

    // Update physical coordinates display (large)
    $('xyPosDisplay').textContent = `X: ${xPos}  Y: ${yPos}`;

    // Update work coordinates display (smaller)
    const workPosDisplay = $('xyPosWorkDisplay');
    if (workPosDisplay) {
        workPosDisplay.textContent = `X: ${workX}  Y: ${workY}`;
    }

    // Add warning class when not homed or E-STOP active
    const posDisplay = $('xyPosDisplay');
    if (!xHomed || !yHomed || estopSensorActive) {
        posDisplay.classList.add('position-invalid');
        if (workPosDisplay) workPosDisplay.classList.add('position-invalid');
    } else {
        posDisplay.classList.remove('position-invalid');
        if (workPosDisplay) workPosDisplay.classList.remove('position-invalid');
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
        let x = parseFloat($('moveX').value);
        let y = parseFloat($('moveY').value);
        const feed = parseFloat($('moveFeed').value);
        const coordType = $('moveCoordType').value;

        // If work coordinates selected, add offset to get physical coordinates
        if (coordType === 'work') {
            x += workOffsets.x;
            y += workOffsets.y;
        }

        // Check brakes before moving
        if (!checkBrakesForMove(x, y)) {
            return;
        }
        api.post('/xy/move', { x, y, feed });
    });

    // Brake control buttons
    $('btnBrakeX').addEventListener('click', toggleBrakeX);
    $('btnBrakeY').addEventListener('click', toggleBrakeY);

    // Work Offset controls
    $('btnSaveOffset').addEventListener('click', saveWorkOffset);
    $('btnSetCurrentAsOffset').addEventListener('click', setCurrentPositionAsOffset);

    // Load offsets on startup
    loadWorkOffsets();
}

// ========== WORK OFFSET FUNCTIONS ==========

// Global variable to store current offsets
let workOffsets = { x: 0, y: 0 };

/**
 * Load work offsets from server and populate input fields.
 */
async function loadWorkOffsets() {
    try {
        const offsets = await api.get('/offsets');
        workOffsets.x = offsets.x || 0;
        workOffsets.y = offsets.y || 0;

        // Update input fields
        $('offsetX').value = workOffsets.x;
        $('offsetY').value = workOffsets.y;

        console.log('Work offsets loaded:', workOffsets);
    } catch (error) {
        console.error('Failed to load work offsets:', error);
    }
}

/**
 * Save work offsets to server.
 */
async function saveWorkOffset() {
    const x = parseFloat($('offsetX').value) || 0;
    const y = parseFloat($('offsetY').value) || 0;

    try {
        await api.post('/offsets', { x, y });
        workOffsets.x = x;
        workOffsets.y = y;
        alert(`Офсет збережено: X=${x}, Y=${y}`);
    } catch (error) {
        console.error('Failed to save work offset:', error);
        alert('Помилка збереження офсету: ' + error.message);
    }
}

/**
 * Set current XY position as work offset.
 */
async function setCurrentPositionAsOffset() {
    try {
        const result = await api.post('/offsets/set-current');
        if (result.success && result.offsets) {
            workOffsets.x = result.offsets.x;
            workOffsets.y = result.offsets.y;

            // Update input fields
            $('offsetX').value = workOffsets.x;
            $('offsetY').value = workOffsets.y;

            alert(`Поточну позицію встановлено як офсет: X=${workOffsets.x}, Y=${workOffsets.y}`);
        }
    } catch (error) {
        console.error('Failed to set current position as offset:', error);
        alert('Помилка: ' + error.message);
    }
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

    // Group devices by their group field
    const grouped = {};
    const ungrouped = [];
    for (const device of state.devices) {
        if (device.group) {
            if (!grouped[device.group]) grouped[device.group] = [];
            grouped[device.group].push(device);
        } else {
            ungrouped.push(device);
        }
    }

    // Render each group (in order of deviceGroups list)
    const renderedGroups = new Set();
    for (const groupName of (state.deviceGroups || [])) {
        const devices = grouped[groupName];
        if (!devices || devices.length === 0) continue;
        renderedGroups.add(groupName);
        list.innerHTML += renderDeviceGroup(groupName, devices);
    }
    // Render any groups not in deviceGroups list (safety)
    for (const groupName of Object.keys(grouped)) {
        if (renderedGroups.has(groupName)) continue;
        list.innerHTML += renderDeviceGroup(groupName, grouped[groupName]);
    }

    // Render ungrouped devices
    if (ungrouped.length > 0) {
        list.innerHTML += renderDeviceGroup('Без групи', ungrouped);
    }
}

function renderDeviceGroup(groupName, devices) {
    let html = `<div class="device-group">`;
    html += `<div class="device-group-header" onclick="toggleDeviceGroup(this)">
        <span class="device-group-arrow">&#9660;</span>
        <span class="device-group-title">${groupName}</span>
        <span class="device-group-count">${devices.length}</span>
    </div>`;
    html += `<div class="device-group-items">`;
    for (const device of devices) {
        const isSelected = state.selectedDevice === device.key;
        html += `
            <div class="device-item ${isSelected ? 'selected' : ''}"
                 data-key="${device.key}"
                 onclick="selectDevice('${device.key}')">
                <div class="device-name">${device.key}</div>
            </div>
        `;
    }
    html += `</div></div>`;
    return html;
}

function toggleDeviceGroup(header) {
    const group = header.parentElement;
    group.classList.toggle('collapsed');
}

function updateDeviceSelect() {
    const select = $('deviceSelect');
    select.innerHTML = '<option value="">-- Select Device --</option>';

    for (const device of state.devices) {
        // Display key directly - format: Назва_Щокрутим_Розмір(Кількість)
        select.innerHTML += `<option value="${device.key}">${device.key}</option>`;
    }

    // Restore selection if device was previously selected
    if (state.selectedDevice) {
        select.value = state.selectedDevice;
    }
}

async function selectDevice(key) {
    state.selectedDevice = key;
    renderDeviceList();

    // Update the cycle control dropdown
    const select = $('deviceSelect');
    if (select) {
        select.value = key;
    }

    // Update Cycle Status panel to show selected device
    updateCycleStatusPanel('IDLE', key, 0, 0);

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
    $('editTorque').value = device.torque !== undefined && device.torque !== null ? device.torque : '';
    $('editGroup').value = device.group || '';
    populateFixtureDropdown();
    $('editFixture').value = device.fixture || '';

    // Work position fields
    $('editWorkX').value = device.work_x !== undefined ? device.work_x : '';
    $('editWorkY').value = device.work_y !== undefined ? device.work_y : '';
    $('editWorkFeed').value = device.work_feed || '5000';

    // Coord source dropdown
    populateCoordSourceDropdown();
    const coordSrc = device.coord_source || '';
    $('editCoordSource').value = coordSrc;

    // Load coordinates
    clearCoordRows();
    if (coordSrc) {
        // Load from source device
        api.get(`/devices/${coordSrc}`).then(srcDevice => {
            clearCoordRows();
            for (const step of srcDevice.steps || []) {
                addCoordRow(step.x, step.y, step.type, step.feed);
            }
            setCoordsReadonly(true, coordSrc);
        }).catch(() => {
            // Source not found — fall back to own steps
            for (const step of device.steps || []) {
                addCoordRow(step.x, step.y, step.type, step.feed);
            }
            setCoordsReadonly(false, '');
            $('editCoordSource').value = '';
        });
    } else {
        for (const step of device.steps || []) {
            addCoordRow(step.x, step.y, step.type, step.feed);
        }
        setCoordsReadonly(false, '');
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
    $('editTorque').value = '';       // Empty by default
    $('editGroup').value = '';        // No group selected
    populateFixtureDropdown();
    $('editFixture').value = '';

    // Work position defaults (physical coordinates)
    $('editWorkX').value = '110';
    $('editWorkY').value = '500';
    $('editWorkFeed').value = '5000';

    // Reset coord source
    populateCoordSourceDropdown();
    $('editCoordSource').value = '';
    setCoordsReadonly(false, '');

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

    if (name.length > 10) {
        alert('Назва девайсу максимум 10 символів');
        return;
    }

    const what = $('editWhat').value.trim();
    if (what.length > 10) {
        alert('Що крутим максимум 10 символів');
        return;
    }

    const holes = $('editHoles').value;
    const screwSize = $('editScrewSize').value;
    const task = $('editTask').value;
    const torqueVal = $('editTorque').value.trim();
    const torque = torqueVal ? parseFloat(torqueVal) : null;

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

    // Always generate key from fields: Назва_Щокрутим_Розмір
    // Example: ABCD_КРУТ_M3x8
    let deviceKey = name;
    if (what) {
        deviceKey += '_' + what.toUpperCase();
    }
    deviceKey += '_' + screwSize;

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

    const group = $('editGroup').value;
    const coordSource = $('editCoordSource').value;

    // Collect coordinates (only if own coords, not from source)
    const steps = [];
    if (!coordSource) {
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
    }

    const data = {
        key: deviceKey,
        name: name,
        what: what,
        holes: parseInt(holes),
        screw_size: screwSize,
        task: task,
        torque: torque,
        work_x: isNaN(workX) ? null : workX,
        work_y: isNaN(workY) ? null : workY,
        work_feed: workFeed,
        group: group,
        fixture: $('editFixture').value,
        coord_source: coordSource,
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

// === Device Groups ===

async function loadDeviceGroups() {
    try {
        const data = await api.get('/device-groups');
        state.deviceGroups = data.groups || [];
        populateGroupDropdown();
        populateFixtureBaseGroupDropdown();
        renderDeviceGroupsList();
    } catch (error) {
        console.error('Failed to load device groups:', error);
    }
}

function populateGroupDropdown() {
    const select = $('editGroup');
    if (!select) return;
    const currentValue = select.value;
    select.innerHTML = '<option value="">-- Без групи --</option>';
    for (const group of (state.deviceGroups || [])) {
        select.innerHTML += `<option value="${group}">${group}</option>`;
    }
    select.value = currentValue;
}

function renderDeviceGroupsList() {
    const list = $('deviceGroupsList');
    if (!list) return;
    if (!state.deviceGroups || state.deviceGroups.length === 0) {
        list.innerHTML = '<div class="empty-message">Немає груп</div>';
        return;
    }
    list.innerHTML = '';
    for (const group of state.deviceGroups) {
        const isActive = state.editingGroup === group;
        const escapedGroup = group.replace(/'/g, "\\'");
        list.innerHTML += `
            <div class="device-group-item ${isActive ? 'active' : ''}" onclick="selectGroup('${escapedGroup}')">
                <span class="group-name">${group}</span>
                <button class="btn btn-del-group" onclick="event.stopPropagation(); deleteDeviceGroup('${escapedGroup}')" title="Видалити групу">&times;</button>
            </div>
        `;
    }
}

async function addDeviceGroup() {
    const input = $('newGroupName');
    const name = input.value.trim();
    if (!name) {
        alert('Введіть назву групи');
        return;
    }
    try {
        await api.post('/device-groups', { name });
        input.value = '';
        await loadDeviceGroups();
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function deleteDeviceGroup(name) {
    if (!confirm(`Видалити групу "${name}"?`)) return;
    try {
        await api.delete(`/device-groups/${encodeURIComponent(name)}`);
        if (state.editingGroup === name) {
            state.editingGroup = null;
            const editor = $('groupEditorCard');
            if (editor) editor.style.display = 'none';
        }
        await loadDeviceGroups();
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function selectGroup(name) {
    state.editingGroup = name;
    renderDeviceGroupsList();

    const editor = $('groupEditorCard');
    if (editor) editor.style.display = '';

    $('groupEditorTitle').textContent = `Група: ${name}`;
    $('editGroupName').value = name;

    await loadGroupDevices(name);
}

async function loadGroupDevices(name) {
    try {
        const data = await api.get(`/device-groups/${encodeURIComponent(name)}/devices`);
        renderGroupDevices(data.in_group || [], data.available || []);
    } catch (error) {
        console.error('Failed to load group devices:', error);
    }
}

function renderGroupDevices(inGroup, available) {
    const list = $('groupDevicesInList');
    if (!list) return;

    if (inGroup.length === 0) {
        list.innerHTML = '<div class="empty-message">Немає девайсів у групі</div>';
    } else {
        list.innerHTML = '';
        for (const dev of inGroup) {
            const escapedGroup = (state.editingGroup || '').replace(/'/g, "\\'");
            const escapedKey = dev.key.replace(/'/g, "\\'");
            list.innerHTML += `
                <div class="group-device-item">
                    <span class="group-device-name">${dev.name} <span class="group-device-key">(${dev.key})</span></span>
                    <button class="btn btn-del-group" onclick="removeDeviceFromGroup('${escapedGroup}', '${escapedKey}')" title="Видалити з групи">&times;</button>
                </div>
            `;
        }
    }

    const select = $('groupAvailableDevices');
    if (!select) return;
    select.innerHTML = '<option value="">-- Виберіть девайс --</option>';
    for (const dev of available) {
        select.innerHTML += `<option value="${dev.key}">${dev.name} (${dev.key})</option>`;
    }
}

async function renameDeviceGroup() {
    const oldName = state.editingGroup;
    if (!oldName) return;
    const newName = $('editGroupName').value.trim();
    if (!newName) {
        alert('Введіть назву групи');
        return;
    }
    try {
        await api.put(`/device-groups/${encodeURIComponent(oldName)}`, { name: newName });
        state.editingGroup = newName;
        await loadDeviceGroups();
        selectGroup(newName);
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function addDeviceToGroup() {
    const name = state.editingGroup;
    if (!name) return;
    const select = $('groupAvailableDevices');
    const deviceKey = select.value;
    if (!deviceKey) {
        alert('Виберіть девайс');
        return;
    }
    try {
        await api.post(`/device-groups/${encodeURIComponent(name)}/devices`, { device_key: deviceKey });
        await loadGroupDevices(name);
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function removeDeviceFromGroup(groupName, deviceKey) {
    try {
        await api.delete(`/device-groups/${encodeURIComponent(groupName)}/devices/${encodeURIComponent(deviceKey)}`);
        await loadGroupDevices(groupName);
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

// === Coordinate Source (shared coords from another device in same group) ===

function populateCoordSourceDropdown() {
    const select = $('editCoordSource');
    if (!select) return;

    const currentValue = select.value;
    const currentGroup = $('editGroup').value;
    const currentKey = state.editingDevice || '';

    select.innerHTML = '<option value="">Власні координати</option>';

    if (!currentGroup) return;  // No group — no sources available

    // Find same-group devices (exclude self)
    for (const device of state.devices) {
        if (device.group === currentGroup && device.key !== currentKey) {
            // Only show devices that have their own coords (not themselves referencing another)
            if (!device.coord_source) {
                select.innerHTML += `<option value="${device.key}">${device.key}</option>`;
            }
        }
    }

    select.value = currentValue;
}

async function onCoordSourceChange() {
    const source = $('editCoordSource').value;
    if (source) {
        // Load source device's coordinates
        try {
            const srcDevice = await api.get(`/devices/${source}`);
            clearCoordRows();
            for (const step of srcDevice.steps || []) {
                addCoordRow(step.x, step.y, step.type, step.feed);
            }
            setCoordsReadonly(true, source);
        } catch (error) {
            alert('Помилка завантаження координат: ' + error.message);
            $('editCoordSource').value = '';
            setCoordsReadonly(false, '');
        }
    } else {
        // Switched back to own coords — clear and add empty row
        clearCoordRows();
        addCoordRow();
        setCoordsReadonly(false, '');
    }
}

function setCoordsReadonly(readonly, sourceName) {
    const notice = $('coordSourceNotice');
    const addBtn = $('btnAddCoord');

    if (readonly) {
        notice.style.display = 'block';
        $('coordSourceName').textContent = sourceName;
        addBtn.style.display = 'none';
        // Disable all coord inputs and buttons
        $('coordsList').querySelectorAll('input, select, button').forEach(el => {
            el.disabled = true;
            el.style.opacity = '0.5';
            el.style.pointerEvents = 'none';
        });
    } else {
        notice.style.display = 'none';
        $('coordSourceName').textContent = '';
        addBtn.style.display = '';
        // Re-enable all coord inputs and buttons
        $('coordsList').querySelectorAll('input, select, button').forEach(el => {
            el.disabled = false;
            el.style.opacity = '';
            el.style.pointerEvents = '';
        });
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
        <button class="btn-move-up" onclick="moveCoordUp(this)" title="Перемістити вгору">↑</button>
        <button class="btn-move-down" onclick="moveCoordDown(this)" title="Перемістити вниз">↓</button>
        <button class="btn-set-current" onclick="setCurrentCoord(this)" title="Задати поточні координати">⊕</button>
        <button class="btn-go-coord" onclick="goToCoord(this)" title="Виїхати в координати">▶</button>
        <button class="btn-del-new" onclick="removeCoordRow(this)">−</button>
    `;

    list.appendChild(row);
    state.coordRows.push(row);
}

/**
 * Set current work coordinates to the coord row inputs.
 */
function setCurrentCoord(btn) {
    const row = btn.closest('.coord-row-new');
    if (!row) return;

    // Get current position from status
    if (!state.status || !state.status.xy_table) {
        alert('Немає даних про поточну позицію');
        return;
    }

    const xy = state.status.xy_table;
    const sensors = state.status.sensors || {};
    const estopActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;

    // Check if homed
    if (!xy.x_homed || !xy.y_homed || estopActive) {
        alert('Стіл не захомлений або активна аварійна зупинка');
        return;
    }

    // Calculate work coordinates (physical - offset)
    const physicalX = xy.x || 0;
    const physicalY = xy.y || 0;
    const workX = (physicalX - workOffsets.x).toFixed(2);
    const workY = (physicalY - workOffsets.y).toFixed(2);

    // Set values to inputs
    row.querySelector('.coord-x').value = workX;
    row.querySelector('.coord-y').value = workY;
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

function moveCoordUp(btn) {
    const row = btn.closest('.coord-row-new');
    const prevRow = row.previousElementSibling;
    if (prevRow && prevRow.classList.contains('coord-row-new')) {
        row.parentNode.insertBefore(row, prevRow);
        renumberCoordRows();
    }
}

function moveCoordDown(btn) {
    const row = btn.closest('.coord-row-new');
    const nextRow = row.nextElementSibling;
    if (nextRow && nextRow.classList.contains('coord-row-new')) {
        row.parentNode.insertBefore(nextRow, row);
        renumberCoordRows();
    }
}

async function goToCoord(btn) {
    const row = btn.closest('.coord-row-new');
    const workX = parseFloat(row.querySelector('.coord-x').value) || 0;
    const workY = parseFloat(row.querySelector('.coord-y').value) || 0;
    // Parse feed value, removing "F " prefix if present
    const feedStr = row.querySelector('.coord-feed').value || '5000';
    const feed = parseFloat(feedStr.replace(/[Ff]\s*/g, '')) || 5000;

    // Convert work coordinates to physical: physical = work + offset
    let offsetX = 0, offsetY = 0;
    try {
        const offsets = await api.get('/offsets');
        offsetX = offsets.x || 0;
        offsetY = offsets.y || 0;
    } catch (e) {
        console.warn('Failed to load offsets, using 0,0');
    }
    const x = workX + offsetX;
    const y = workY + offsetY;

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

function exportConfig() {
    window.location.href = `${API_BASE}/devices/export`;
}

async function importConfig(file) {
    if (!confirm('Увага! Імпорт повністю перезапише поточний конфіг (девайси, групи, оснастки). Продовжити?')) {
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE}/devices/import`, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        if (!response.ok) {
            alert('Помилка імпорту: ' + (result.error || 'Unknown error'));
            return;
        }
        alert(`Імпорт завершено!\nДевайсів: ${result.devices}\nГруп: ${result.groups}\nОснасток: ${result.fixtures}`);
        await loadDeviceGroups();
        await loadDevices();
        await loadFixtures();
    } catch (error) {
        alert('Помилка імпорту: ' + error.message);
    }
}

function initSettingsTab() {
    $('btnNewDevice').addEventListener('click', newDevice);
    $('btnAddCoord').addEventListener('click', () => addCoordRow());
    $('btnSaveDevice').addEventListener('click', saveDevice);
    $('btnCancelEdit').addEventListener('click', cancelEdit);
    $('btnDeleteDevice').addEventListener('click', deleteDevice);

    // Export / Import
    $('btnExportDevices').addEventListener('click', exportConfig);
    $('btnImportDevices').addEventListener('click', () => $('fileImportDevices').click());
    $('fileImportDevices').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            importConfig(file);
            e.target.value = '';  // Reset so same file can be re-imported
        }
    });

    // Re-populate coord source and fixture dropdown when group changes
    $('editGroup').addEventListener('change', () => {
        populateCoordSourceDropdown();
        populateFixtureDropdown();
        // If current source is no longer in same group, reset it
        const src = $('editCoordSource').value;
        if (src) {
            const opt = $('editCoordSource').querySelector(`option[value="${src}"]`);
            if (!opt) {
                $('editCoordSource').value = '';
                onCoordSourceChange();
            }
        }
    });

    // Re-render device checkboxes when fixture group changes
    $('editFixtureBaseGroup').addEventListener('change', () => {
        renderFixtureDevicesList();
    });

    // XY Control in Settings Tab
    $$('[data-jog-settings]').forEach(btn => {
        btn.addEventListener('click', () => {
            const dir = btn.dataset.jogSettings;
            const step = parseFloat($('jogStepSettings').value);
            const feed = parseFloat($('jogFeedSettings').value);

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

    // Go to work zero (physical position = offset)
    $('btnWorkZeroSettings').addEventListener('click', () => {
        if (!checkBothBrakes()) return;
        // Work zero = physical position where work coordinates are 0,0
        // This means moving to the offset position
        const x = workOffsets.x;
        const y = workOffsets.y;
        api.post('/xy/move_seq', { x, y, feed: 5000 });
    });

    // Brake control buttons in Settings
    $('btnBrakeXSettings').addEventListener('click', toggleBrakeX);
    $('btnBrakeYSettings').addEventListener('click', toggleBrakeY);

    // Enforce uppercase for device name
    $('editName').addEventListener('input', (e) => {
        e.target.value = e.target.value.toUpperCase();
    });

    // Fixtures buttons
    $('btnNewFixture').addEventListener('click', newFixture);
    $('btnSaveFixture').addEventListener('click', saveFixture);
    $('btnCancelFixture').addEventListener('click', cancelFixtureEdit);
    $('btnDeleteFixture').addEventListener('click', deleteFixture);
}

// ========== SETTINGS MODE SWITCH (Devices / Fixtures) ==========

function switchSettingsMode(mode) {
    state.settingsMode = mode;

    // Toggle buttons
    $('btnModeDevices').classList.toggle('active', mode === 'devices');
    $('btnModeFixtures').classList.toggle('active', mode === 'fixtures');

    // Toggle areas visibility
    const devAreas = document.querySelectorAll('.settings-devices-area');
    const fixAreas = document.querySelectorAll('.settings-fixtures-area');

    devAreas.forEach(el => el.style.display = mode === 'devices' ? '' : 'none');
    fixAreas.forEach(el => el.style.display = mode === 'fixtures' ? '' : 'none');

    // Load fixtures and device groups when switching to fixtures mode
    if (mode === 'fixtures') {
        loadDeviceGroups().then(() => loadFixtures());
    }
}

// ========== FIXTURES (ОСНАСТКИ) ==========

async function loadFixtures() {
    try {
        state.fixtures = await api.get('/fixtures');
        renderFixtureList();
        renderFixtureCatalog();
    } catch (error) {
        console.error('Failed to load fixtures:', error);
    }
}

function populateFixtureBaseGroupDropdown() {
    const sel = $('editFixtureBaseGroup');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">-- Виберіть групу --</option>';
    for (const g of (state.deviceGroups || [])) {
        sel.innerHTML += `<option value="${g}">${g}</option>`;
    }
    sel.value = currentVal;
}

// Populate fixture dropdown in device editor based on selected group
function populateFixtureDropdown() {
    const sel = $('editFixture');
    if (!sel) return;
    const currentVal = sel.value;
    const group = $('editGroup').value;
    sel.innerHTML = '<option value="">-- Без оснастки --</option>';
    if (!group) return;
    for (const fix of (state.fixtures || [])) {
        if (fix.base_group === group) {
            sel.innerHTML += `<option value="${fix.code}">${fix.code}${fix.qr_code ? ' (' + fix.qr_code + ')' : ''}</option>`;
        }
    }
    sel.value = currentVal;
}

// Render device checkboxes in fixture editor for selected group
function renderFixtureDevicesList() {
    const container = $('fixtureDevicesList');
    const row = $('fixtureDevicesRow');
    if (!container || !row) return;

    const group = $('editFixtureBaseGroup').value;
    if (!group) {
        row.style.display = 'none';
        container.innerHTML = '';
        return;
    }

    const fixtureCode = $('editFixtureCode').value.trim().toUpperCase();
    const groupDevices = (state.devices || []).filter(d => d.group === group);

    if (groupDevices.length === 0) {
        row.style.display = '';
        container.innerHTML = '<span class="fixture-devices-empty">Немає девайсів у цій групі</span>';
        return;
    }

    row.style.display = '';
    let html = '';
    for (const dev of groupDevices) {
        const isLinked = fixtureCode && dev.fixture === fixtureCode;
        html += `<label><input type="checkbox" value="${dev.key}" ${isLinked ? 'checked' : ''}> ${dev.name || dev.key}</label>`;
    }
    container.innerHTML = html;
}

function renderFixtureList() {
    const list = $('fixtureList');
    list.innerHTML = '';

    if (state.fixtures.length === 0) {
        list.innerHTML = '<div class="fixture-empty">Оснастки не додані</div>';
        return;
    }

    for (const fix of state.fixtures) {
        const isSelected = state.selectedFixture === fix.key;
        list.innerHTML += `
            <div class="fixture-item ${isSelected ? 'selected' : ''}"
                 data-key="${fix.key}"
                 onclick="selectFixture('${fix.key}')">
                <div class="fixture-name">${fix.code}</div>
                <div class="fixture-sub">${fix.base_group || '-'}</div>
            </div>
        `;
    }
}

function renderFixtureCatalog() {
    const catalog = $('fixtureCatalog');
    if (state.fixtures.length === 0) {
        catalog.innerHTML = '<div class="fixture-catalog-empty">Оснастки не додані</div>';
        return;
    }

    let html = '<div class="fixture-catalog-header"><span class="col-fix-code">Код оснастки</span><span class="col-fix-group">Група</span><span class="col-fix-qr">QR код</span><span class="col-fix-devices">Девайси</span></div>';
    for (const fix of state.fixtures) {
        const isSelected = state.selectedFixture === fix.key;
        const linkedDevices = (state.devices || [])
            .filter(d => d.fixture === fix.code)
            .map(d => d.name || d.key)
            .join(', ');
        html += `<div class="fixture-catalog-row ${isSelected ? 'selected' : ''}" onclick="selectFixture('${fix.key}')">
            <span class="col-fix-code">${fix.code}</span>
            <span class="col-fix-group">${fix.base_group || '-'}</span>
            <span class="col-fix-qr">${fix.qr_code || '-'}</span>
            <span class="col-fix-devices">${linkedDevices || '-'}</span>
        </div>`;
    }
    catalog.innerHTML = html;
}

async function selectFixture(key) {
    state.selectedFixture = key;
    renderFixtureList();
    renderFixtureCatalog();

    try {
        const fix = await api.get(`/fixtures/${key}`);
        loadFixtureToEditor(fix);
    } catch (error) {
        console.error('Failed to load fixture:', error);
    }
}

function loadFixtureToEditor(fix) {
    state.editingFixture = fix.key;
    $('editFixtureKey').value = fix.key || '';
    $('editFixtureCode').value = fix.code || '';
    $('editFixtureQR').value = fix.qr_code || '';
    populateFixtureBaseGroupDropdown();
    $('editFixtureBaseGroup').value = fix.base_group || '';
    $('editFixtureScanX').value = fix.scan_x ?? 0;
    $('editFixtureScanY').value = fix.scan_y ?? 0;
    $('editFixtureScanFeed').value = fix.scan_feed ?? 50000;
    renderFixtureDevicesList();
}

function newFixture() {
    state.selectedFixture = null;
    state.editingFixture = null;
    renderFixtureList();
    renderFixtureCatalog();

    $('editFixtureKey').value = '';
    $('editFixtureCode').value = '';
    $('editFixtureQR').value = '';
    populateFixtureBaseGroupDropdown();
    $('editFixtureBaseGroup').value = '';
    $('editFixtureScanX').value = 220;
    $('editFixtureScanY').value = 500;
    $('editFixtureScanFeed').value = 50000;
    renderFixtureDevicesList();
}

async function saveFixture() {
    const key = $('editFixtureKey').value.trim();
    const code = $('editFixtureCode').value.trim().toUpperCase();
    const qrCode = $('editFixtureQR').value.trim().toUpperCase();

    if (!code) {
        alert('Код оснастки обов\'язковий');
        return;
    }

    if (!qrCode) {
        alert('QR код обов\'язковий');
        return;
    }

    const data = {
        code: code,
        qr_code: qrCode,
        base_group: $('editFixtureBaseGroup').value.trim(),
        scan_x: parseFloat($('editFixtureScanX').value) || 0,
        scan_y: parseFloat($('editFixtureScanY').value) || 0,
        scan_feed: parseFloat($('editFixtureScanFeed').value) || 50000,
    };

    // Collect checked device keys from checkboxes
    const checkedDevices = [];
    const checkboxes = $('fixtureDevicesList').querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => {
        if (cb.checked) checkedDevices.push(cb.value);
    });

    try {
        if (key) {
            await api.put(`/fixtures/${key}`, data);
        } else {
            await api.post('/fixtures', data);
        }

        // Update fixture field in devices: set for checked, clear for unchecked
        const group = data.base_group;
        if (group) {
            for (const dev of (state.devices || [])) {
                if (dev.group !== group) continue;
                const shouldLink = checkedDevices.includes(dev.key);
                const currentlyLinked = dev.fixture === code;
                if (shouldLink && !currentlyLinked) {
                    await api.put(`/devices/${dev.key}`, { fixture: code });
                    dev.fixture = code;
                } else if (!shouldLink && currentlyLinked) {
                    await api.put(`/devices/${dev.key}`, { fixture: '' });
                    dev.fixture = '';
                }
            }
        }

        await loadFixtures();
        // Select the saved fixture
        state.selectedFixture = code;
        state.editingFixture = code;
        renderFixtureList();
        renderFixtureCatalog();
        $('editFixtureKey').value = code;
        renderFixtureDevicesList();
    } catch (error) {
        alert('Помилка збереження: ' + error.message);
    }
}

async function deleteFixture() {
    const key = $('editFixtureKey').value.trim();
    if (!key) return;

    if (!confirm(`Видалити оснастку "${key}"?`)) return;

    try {
        await api.delete(`/fixtures/${key}`);
        newFixture();
        await loadFixtures();
    } catch (error) {
        alert('Помилка видалення: ' + error.message);
    }
}

function cancelFixtureEdit() {
    if (state.selectedFixture) {
        selectFixture(state.selectedFixture);
    } else {
        newFixture();
    }
}

// Update XY position on settings tab (shows work coordinates)
function updateSettingsXYPos(status) {
    const xy = status.xy_table || {};
    const sensors = status.sensors || {};
    // Check E-STOP sensor directly for immediate response
    const estopSensorActive = sensors.emergency_stop === 'ACTIVE' || sensors.emergency_stop === true;
    const xHomed = xy.x_homed && !estopSensorActive;
    const yHomed = xy.y_homed && !estopSensorActive;

    // Calculate work coordinates (physical - offset)
    const physicalX = xy.x || 0;
    const physicalY = xy.y || 0;
    const workX = xHomed ? (physicalX - workOffsets.x).toFixed(2) : '?.??';
    const workY = yHomed ? (physicalY - workOffsets.y).toFixed(2) : '?.??';

    const posDisplay = $('settingsXYPos');
    if (posDisplay) {
        posDisplay.textContent = `X: ${workX}  Y: ${workY}`;
        // Add warning class when E-STOP active or not homed
        if (!xHomed || !yHomed) {
            posDisplay.classList.add('position-invalid');
        } else {
            posDisplay.classList.remove('position-invalid');
        }
    }
}

// ========== AUTHENTICATION ==========

async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/status');
        const data = await response.json();

        if (data.logged_in && data.user) {
            state.user = data.user;
            state.allowedTabs = data.user.allowed_tabs || [];

            // Update UI
            $('userName').textContent = data.user.username;
            $('userInfo').style.display = 'flex';

            // Show/hide tabs based on permissions
            updateTabVisibility();

            return true;
        }
    } catch (e) {
        console.error('Auth status check failed:', e);
    }
    return false;
}

function updateTabVisibility() {
    const user = state.user;
    if (!user) return;

    const isAdmin = user.role === 'admin';
    const allowedTabs = user.allowed_tabs || [];

    // All tab buttons except status (always visible)
    const tabConfigs = [
        { tab: 'control', btn: null },
        { tab: 'xy', btn: null },
        { tab: 'settings', btn: null },
        { tab: 'stats', btn: $('tabStats') },
        { tab: 'admin', btn: $('tabAdmin') },
        { tab: 'logs', btn: $('tabLogs') }
    ];

    tabConfigs.forEach(({ tab, btn }) => {
        const tabBtn = btn || document.querySelector(`.tab-btn[data-tab="${tab}"]`);
        if (tabBtn) {
            const hasAccess = isAdmin || allowedTabs.includes(tab);
            tabBtn.style.display = hasAccess ? '' : 'none';
        }
    });
}

async function handleLogout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (e) {
        console.error('Logout failed:', e);
        window.location.href = '/login';
    }
}

// ========== ADMIN PANEL ==========

async function loadUsers() {
    try {
        const response = await api.get('/admin/users');
        renderUsersList(response.users || {});
    } catch (e) {
        console.error('Failed to load users:', e);
    }
}

function renderUsersList(users) {
    const container = $('usersList');
    if (!container) return;

    if (Object.keys(users).length === 0) {
        container.innerHTML = '<p class="empty-message">Немає користувачів</p>';
        return;
    }

    const tabLabels = {
        'status': 'Статус',
        'control': 'Керування',
        'xy': 'XY Стіл',
        'settings': 'Налаштування',
        'admin': 'Адмін',
        'logs': 'Логи'
    };

    let html = '<div class="users-table">';
    html += '<div class="users-header">';
    html += '<span>Логін</span>';
    html += '<span>Роль</span>';
    html += '<span>Вкладки</span>';
    html += '<span>Дії</span>';
    html += '</div>';

    for (const [username, userData] of Object.entries(users)) {
        const roleLabel = userData.role === 'admin' ? 'Адмін' : 'Користувач';
        const tabs = (userData.allowed_tabs || []).map(t => tabLabels[t] || t).join(', ');
        const isCurrentUser = state.user && state.user.username === username;

        html += '<div class="users-row">';
        html += `<span class="user-username">${username}${isCurrentUser ? ' (ви)' : ''}</span>`;
        html += `<span class="user-role ${userData.role}">${roleLabel}</span>`;
        html += `<span class="user-tabs">${tabs || '-'}</span>`;
        html += '<span class="user-actions">';
        html += `<button class="btn btn-small btn-edit" onclick="editUser('${username}')">Редагувати</button>`;
        if (!isCurrentUser) {
            html += `<button class="btn btn-small btn-danger" onclick="deleteUser('${username}')">Видалити</button>`;
        }
        html += '</span>';
        html += '</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

function editUser(username) {
    state.editingUser = username;

    // Switch to users sub-tab to show the form
    switchAdminSubtab('admin-users');

    // Load user data
    api.get('/admin/users').then(response => {
        const userData = response.users[username];
        if (!userData) return;

        $('userFormTitle').textContent = 'Редагувати користувача';
        $('editUserUsername').value = username;
        $('editUserUsername').disabled = true;
        $('editUserPassword').value = '';
        $('passwordHint').textContent = '(залишити порожнім)';
        $('editUserRole').value = userData.role || 'user';
        $('btnCancelUser').style.display = '';

        // Set tab checkboxes
        const checkboxes = $$('#tabsCheckboxes input[type="checkbox"]');
        checkboxes.forEach(cb => {
            if (cb.value === 'status') return; // Always checked
            cb.checked = (userData.allowed_tabs || []).includes(cb.value);
        });
    });
}

function resetUserForm() {
    state.editingUser = null;
    $('userFormTitle').textContent = 'Додати користувача';
    $('editUserUsername').value = '';
    $('editUserUsername').disabled = false;
    $('editUserPassword').value = '';
    $('passwordHint').textContent = "(обов'язково)";
    $('editUserRole').value = 'user';
    $('btnCancelUser').style.display = 'none';

    // Reset tab checkboxes
    const checkboxes = $$('#tabsCheckboxes input[type="checkbox"]');
    checkboxes.forEach(cb => {
        if (cb.value === 'status') return;
        cb.checked = false;
    });
}

async function saveUser() {
    const username = $('editUserUsername').value.trim();
    const password = $('editUserPassword').value;
    const role = $('editUserRole').value;

    // Get selected tabs
    const allowedTabs = ['status']; // Always include status
    const checkboxes = $$('#tabsCheckboxes input[type="checkbox"]:checked');
    checkboxes.forEach(cb => {
        if (cb.value !== 'status' && !allowedTabs.includes(cb.value)) {
            allowedTabs.push(cb.value);
        }
    });

    if (!username) {
        alert('Введіть логін');
        return;
    }

    if (!state.editingUser && !password) {
        alert('Введіть пароль');
        return;
    }

    try {
        if (state.editingUser) {
            // Update existing user
            const data = { role, allowed_tabs: allowedTabs };
            if (password) data.password = password;

            await api.put(`/admin/users/${username}`, data);
        } else {
            // Create new user
            await api.post('/admin/users', {
                username,
                password,
                role,
                allowed_tabs: allowedTabs
            });
        }

        resetUserForm();
        loadUsers();

        // If we edited current user, refresh auth status
        if (state.editingUser === state.user?.username) {
            checkAuthStatus();
        }
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

async function deleteUser(username) {
    if (!confirm(`Видалити користувача "${username}"?`)) return;

    try {
        await api.delete(`/admin/users/${username}`);
        loadUsers();
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

// ========== USB CAMERA ==========

let cameraPollInterval = null;
let cameraSnapshotInterval = null;
let cameraConnected = false;

async function loadCameraStatus() {
    try {
        const data = await api.get('/camera/status');
        const badge = $('cameraStatusBadge');
        const stream = $('cameraStream');
        const noSignal = $('cameraNoSignal');

        if (data.connected && data.has_frame) {
            badge.textContent = data.active_device || "Підключено";
            badge.className = 'scanner-status-badge connected';
            stream.classList.remove('hidden');
            noSignal.classList.add('hidden');
            // Start snapshot polling if not already running
            if (!cameraConnected) {
                cameraConnected = true;
                startSnapshotPolling();
            }
        } else {
            badge.textContent = "Відключено";
            badge.className = 'scanner-status-badge disconnected';
            stream.classList.add('hidden');
            stream.src = '';
            const errText = data.error || 'Камера не знайдена';
            const devList = (data.video_devices || []).join(', ') || 'не знайдено';
            noSignal.innerHTML = `<span>${errText}</span><br><small style="color:#666">Пристрої: ${devList}</small>`;
            noSignal.classList.remove('hidden');
            cameraConnected = false;
            stopSnapshotPolling();
        }

        $('cameraResolution').textContent = data.connected ? `${data.width}x${data.height}` : '--';
        $('cameraFps').textContent = data.connected ? Math.round(data.fps) : '--';

        // Recording status
        const btnRec = $('btnCameraRec');
        const btnStop = $('btnCameraRecStop');
        const recDurationRow = $('cameraRecDurationRow');

        const recAllowed = data.recording_allowed !== false;

        if (data.recording) {
            $('cameraRecStatus').textContent = data.recording_file || 'Записує...';
            $('cameraRecStatus').style.color = '#e74c3c';
            btnRec.style.display = 'none';
            btnStop.style.display = '';
            recDurationRow.style.display = '';
            const secs = Math.floor(data.recording_duration);
            const mins = Math.floor(secs / 60);
            const s = secs % 60;
            $('cameraRecDuration').textContent = `${mins}:${s.toString().padStart(2, '0')}`;
        } else if (!recAllowed) {
            $('cameraRecStatus').textContent = 'USB не підключено — запис заборонено';
            $('cameraRecStatus').style.color = '#e67e22';
            btnRec.style.display = '';
            btnRec.disabled = true;
            btnRec.title = 'Підключіть USB-накопичувач';
            btnStop.style.display = 'none';
            recDurationRow.style.display = 'none';
        } else {
            $('cameraRecStatus').textContent = 'Не записує';
            $('cameraRecStatus').style.color = '';
            btnRec.style.display = '';
            btnRec.disabled = false;
            btnRec.title = '';
            btnStop.style.display = 'none';
            recDurationRow.style.display = 'none';
        }
    } catch (error) {
        console.error('Camera status error:', error);
    }
}

const REC_PER_PAGE = 80; // 4 columns x 20 rows
let recCurrentPage = 0;
let recAllData = [];

async function loadCameraRecordings() {
    try {
        const data = await api.get('/camera/recordings');
        recAllData = data.recordings || [];
        recCurrentPage = 0;
        renderRecordingsPage();
    } catch (error) {
        console.error('Failed to load recordings:', error);
    }
}

function renderRecordingsPage() {
    const list = $('cameraRecordingsList');
    const pagination = $('recPagination');
    if (!list) return;

    if (recAllData.length === 0) {
        list.innerHTML = '<div class="empty-message">Немає записів</div>';
        if (pagination) pagination.innerHTML = '';
        return;
    }

    // Paginate
    const totalPages = Math.ceil(recAllData.length / REC_PER_PAGE);
    const start = recCurrentPage * REC_PER_PAGE;
    const pageData = recAllData.slice(start, start + REC_PER_PAGE);

    // Group by folder (date)
    const groups = {};
    for (const rec of pageData) {
        const folder = rec.folder || '/';
        if (!groups[folder]) groups[folder] = [];
        groups[folder].push(rec);
    }

    // Render groups
    let html = '';
    // Sort date folders newest first
    const sortedFolders = Object.keys(groups).sort().reverse();
    for (const folder of sortedFolders) {
        const recs = groups[folder];
        const totalMb = recs.reduce((s, r) => s + (r.size_mb || 0), 0).toFixed(1);
        html += `<div class="rec-date-group">`;
        html += `<div class="rec-date-header" onclick="this.parentElement.classList.toggle('collapsed')">`;
        html += `<span class="rec-date-arrow">&#x25BE;</span>`;
        html += `<span class="rec-date-label">${folder}</span>`;
        html += `<span class="rec-date-info">${recs.length} файлів &middot; ${totalMb} МБ</span>`;
        html += `</div>`;
        html += `<div class="rec-date-grid">`;
        for (const rec of recs) {
            const res = (rec.width && rec.height) ? `${rec.width}x${rec.height}` : '--';
            const fps = rec.fps ? `${rec.fps}` : '--';
            const codec = rec.codec || '--';
            const dur = rec.duration_str || '--';
            const displayName = rec.display_name || rec.filename;
            const encodedPath = rec.filename.split('/').map(encodeURIComponent).join('/');
            html += `<div class="rec-grid-card">`;
            html += `  <div class="rec-grid-name" title="${rec.filename}">${displayName}</div>`;
            html += `  <div class="rec-grid-meta">`;
            html += `    <span>${dur}</span><span>${res}</span><span>${fps} fps</span>`;
            html += `    <span>${codec}</span><span>${rec.size_mb} МБ</span>`;
            html += `  </div>`;
            html += `  <div class="rec-grid-actions">`;
            html += `    <a class="btn btn-secondary btn-sm" href="/api/camera/recordings/${encodedPath}" download title="Завантажити">&#x2B07;</a>`;
            html += `    <button class="btn btn-danger btn-sm" onclick="deleteCameraRecording('${rec.filename.replace(/'/g, "\\'")}')" title="Видалити">&times;</button>`;
            html += `  </div>`;
            html += `</div>`;
        }
        html += `</div></div>`;
    }
    list.innerHTML = html;

    // Render pagination
    if (pagination) {
        if (totalPages <= 1) {
            pagination.innerHTML = '';
        } else {
            let phtml = '';
            phtml += `<button class="btn btn-secondary btn-sm" ${recCurrentPage === 0 ? 'disabled' : ''} onclick="recGoPage(${recCurrentPage - 1})">&laquo;</button>`;
            for (let i = 0; i < totalPages; i++) {
                phtml += `<button class="btn btn-sm ${i === recCurrentPage ? 'btn-primary' : 'btn-secondary'}" onclick="recGoPage(${i})">${i + 1}</button>`;
            }
            phtml += `<button class="btn btn-secondary btn-sm" ${recCurrentPage >= totalPages - 1 ? 'disabled' : ''} onclick="recGoPage(${recCurrentPage + 1})">&raquo;</button>`;
            pagination.innerHTML = phtml;
        }
    }
}

function recGoPage(page) {
    const totalPages = Math.ceil(recAllData.length / REC_PER_PAGE);
    if (page < 0 || page >= totalPages) return;
    recCurrentPage = page;
    renderRecordingsPage();
}

async function loadStorageInfo() {
    try {
        const data = await api.get('/camera/storage');
        const el = (id) => document.getElementById(id);
        if (el('storageRecDir')) el('storageRecDir').textContent = data.recordings_dir || '--';
        if (el('storageRecCount')) el('storageRecCount').textContent = data.recordings_count || '0';
        if (el('storageRecSize')) el('storageRecSize').textContent = data.recordings_size_str || '--';
        // Quota bar
        if (el('storageQuotaMax')) el('storageQuotaMax').textContent = (data.quota_max_str || '--') + ' ліміт';
        if (el('storageQuotaPct')) el('storageQuotaPct').textContent = (data.quota_used_pct || 0) + '%';
        if (el('storageQuotaBar')) {
            const qpct = data.quota_used_pct || 0;
            el('storageQuotaBar').style.width = Math.min(qpct, 100) + '%';
            el('storageQuotaBar').className = 'storage-bar-used' + (qpct > 90 ? ' critical' : qpct > 70 ? ' warning' : '');
        }
        // Disk bar
        if (el('storageDiskUsed')) el('storageDiskUsed').textContent = data.disk_used_str || '--';
        if (el('storageDiskFree')) el('storageDiskFree').textContent = (data.disk_free_str || '--') + ' вільно';
        if (el('storageDiskPct')) el('storageDiskPct').textContent = (data.disk_used_pct || 0) + '%';
        if (el('storageDiskBar')) {
            const pct = data.disk_used_pct || 0;
            el('storageDiskBar').style.width = pct + '%';
            el('storageDiskBar').className = 'storage-bar-used' + (pct > 90 ? ' critical' : pct > 70 ? ' warning' : '');
        }
    } catch (error) {
        console.error('Failed to load storage info:', error);
    }
}

async function startCameraRecording() {
    try {
        const data = await api.post('/camera/record/start');
        if (data.status === 'recording' || data.status === 'already_recording') {
            loadCameraStatus();
        } else {
            alert('Помилка запису: ' + (data.error || 'невідома'));
        }
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function stopCameraRecording() {
    try {
        await api.post('/camera/record/stop');
        loadCameraStatus();
        loadCameraRecordings();
        loadStorageInfo();
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function deleteCameraRecording(filename) {
    if (!confirm(`Видалити запис "${filename}"?`)) return;
    try {
        const encodedPath = filename.split('/').map(encodeURIComponent).join('/');
        await api.delete(`/camera/recordings/${encodedPath}`);
        loadCameraRecordings();
        loadStorageInfo();
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

// === USB Storage Management ===

async function loadUsbStatus() {
    try {
        const data = await api.get('/usb/status');
        const el = (id) => document.getElementById(id);
        const badge = el('usbStatusBadge');

        if (!data.detected) {
            if (badge) { badge.textContent = 'Не знайдено'; badge.className = 'scanner-status-badge disconnected'; }
            if (el('usbDevice')) el('usbDevice').textContent = '--';
            if (el('usbModel')) el('usbModel').textContent = '--';
            if (el('usbFstype')) el('usbFstype').textContent = '--';
            if (el('usbRecCount')) el('usbRecCount').textContent = '--';
            if (el('usbBarContainer')) el('usbBarContainer').style.display = 'none';
            if (el('btnUsbMount')) el('btnUsbMount').style.display = 'none';
            if (el('btnUsbUnmount')) el('btnUsbUnmount').style.display = 'none';
            if (el('btnUsbFormat')) el('btnUsbFormat').style.display = 'none';
            return;
        }

        const info = data.device_info;
        if (el('usbDevice')) el('usbDevice').textContent = `${info.device} (${info.size})`;
        if (el('usbModel')) el('usbModel').textContent = info.model || '--';
        if (el('usbFstype')) el('usbFstype').textContent = info.fstype || '--';

        if (data.mounted) {
            if (badge) { badge.textContent = 'Змонтовано'; badge.className = 'scanner-status-badge connected'; }
            if (el('btnUsbMount')) el('btnUsbMount').style.display = 'none';
            if (el('btnUsbUnmount')) el('btnUsbUnmount').style.display = '';
            if (el('btnUsbFormat')) el('btnUsbFormat').style.display = 'none';
            if (el('usbRecCount')) el('usbRecCount').textContent = data.recordings_count || '0';
            if (el('usbBarContainer')) el('usbBarContainer').style.display = '';
            if (el('usbDiskUsed')) el('usbDiskUsed').textContent = data.disk_used_str || '--';
            if (el('usbDiskFree')) el('usbDiskFree').textContent = (data.disk_free_str || '--') + ' вільно';
            if (el('usbDiskPct')) el('usbDiskPct').textContent = (data.disk_used_pct || 0) + '%';
            if (el('usbDiskBar')) {
                const pct = data.disk_used_pct || 0;
                el('usbDiskBar').style.width = pct + '%';
                el('usbDiskBar').className = 'storage-bar-used' + (pct > 90 ? ' critical' : pct > 70 ? ' warning' : '');
            }
        } else {
            if (badge) { badge.textContent = 'Не змонтовано'; badge.className = 'scanner-status-badge warning'; }
            if (el('btnUsbMount')) el('btnUsbMount').style.display = '';
            if (el('btnUsbUnmount')) el('btnUsbUnmount').style.display = 'none';
            if (el('btnUsbFormat')) el('btnUsbFormat').style.display = '';
            if (el('usbRecCount')) el('usbRecCount').textContent = '--';
            if (el('usbBarContainer')) el('usbBarContainer').style.display = 'none';
        }
    } catch (error) {
        console.error('Failed to load USB status:', error);
    }
}

async function usbMount() {
    try {
        const data = await api.post('/usb/mount');
        if (data.status === 'mounted' || data.status === 'already_mounted') {
            loadUsbStatus();
            loadStorageInfo();
            loadCameraRecordings();
            loadCameraStatus();
        } else {
            alert('Помилка: ' + (data.error || 'невідома'));
        }
    } catch (error) {
        alert('Помилка монтування: ' + error.message);
    }
}

async function usbUnmount() {
    if (!confirm('Відмонтувати USB-накопичувач? Запис буде вимкнено.')) return;
    try {
        const data = await api.post('/usb/unmount');
        loadUsbStatus();
        loadStorageInfo();
        loadCameraRecordings();
        loadCameraStatus();
    } catch (error) {
        alert('Помилка: ' + error.message);
    }
}

async function usbFormat() {
    // Get device list first
    let devData;
    try {
        devData = await api.get('/usb/devices');
    } catch (e) {
        alert('Не вдалося отримати список пристроїв');
        return;
    }
    const devices = devData.devices || [];
    if (devices.length === 0) {
        alert('USB-пристрої не знайдено');
        return;
    }
    const device = devices[0].device;
    if (!confirm(`УВАГА! Всі дані на ${device} будуть видалені!\n\nФорматувати ${device} як ext4?`)) return;

    try {
        const data = await api.post('/usb/format', { device });
        if (data.status === 'formatted') {
            alert(`${device} відформатовано як ext4`);
            loadUsbStatus();
        } else {
            alert('Помилка: ' + (data.error || 'невідома'));
        }
    } catch (error) {
        alert('Помилка форматування: ' + error.message);
    }
}

function refreshSnapshot() {
    const stream = $('cameraStream');
    if (!stream || !cameraConnected) return;
    // Load snapshot as new Image to avoid flicker
    const img = new Image();
    img.onload = function() {
        stream.src = img.src;
    };
    img.src = '/api/camera/snapshot?t=' + Date.now();
}

function startSnapshotPolling() {
    if (cameraSnapshotInterval) return;
    refreshSnapshot();
    cameraSnapshotInterval = setInterval(refreshSnapshot, 200); // ~5 fps
}

function stopSnapshotPolling() {
    if (cameraSnapshotInterval) {
        clearInterval(cameraSnapshotInterval);
        cameraSnapshotInterval = null;
    }
}

function startCameraPolling() {
    if (cameraPollInterval) return;
    loadCameraStatus();
    loadCameraRecordings();
    loadStorageInfo();
    loadUsbStatus();
    cameraPollInterval = setInterval(() => {
        loadCameraStatus();
        loadCameraRecordings();
    }, 3000);
}

function stopCameraPolling() {
    if (cameraPollInterval) {
        clearInterval(cameraPollInterval);
        cameraPollInterval = null;
    }
    stopSnapshotPolling();
    const stream = $('cameraStream');
    if (stream) {
        stream.src = '';
        stream.classList.add('hidden');
    }
    const noSignal = $('cameraNoSignal');
    if (noSignal) noSignal.classList.remove('hidden');
    cameraConnected = false;
}

// ========== CYCLE STATISTICS ==========

let statsAllData = [];
let statsFiltered = [];
let statsSortCol = 'timestamp';
let statsSortAsc = false; // newest first
const STATS_PER_PAGE = 50;
let statsCurrentPage = 0;

async function loadCycleHistory() {
    try {
        const data = await api.get('/stats/history');
        statsAllData = data.history || [];
        applyStatsFilters();
    } catch (error) {
        console.error('Failed to load cycle history:', error);
    }
}

function applyStatsFilters() {
    const fDevice = ($('statsFilterDevice') || {}).value || '';
    const fGroup = ($('statsFilterGroup') || {}).value || '';
    const fStatus = ($('statsFilterStatus') || {}).value || '';
    const fDateFrom = ($('statsFilterDateFrom') || {}).value || '';
    const fDateTo = ($('statsFilterDateTo') || {}).value || '';

    statsFiltered = statsAllData.filter(r => {
        if (fDevice && (r.device_name || '') !== fDevice) return false;
        if (fGroup && (r.group || '') !== fGroup) return false;
        if (fStatus && r.status !== fStatus) return false;
        if (fDateFrom && r.timestamp < fDateFrom) return false;
        if (fDateTo && r.timestamp < fDateTo + '\xff') {
            // fDateTo is inclusive — keep records on that date
        } else if (fDateTo && r.timestamp > fDateTo + ' 23:59:59') return false;
        return true;
    });

    sortStatsData();
    statsCurrentPage = 0;
    renderStatsTable();
}

function sortStatsData() {
    const col = statsSortCol;
    const asc = statsSortAsc;
    statsFiltered.sort((a, b) => {
        let va = a[col], vb = b[col];
        if (typeof va === 'number' && typeof vb === 'number') return asc ? va - vb : vb - va;
        va = (va || '').toString();
        vb = (vb || '').toString();
        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
}

function renderStatsTable() {
    const tbody = $('statsTableBody');
    const pagination = $('statsPagination');
    const countEl = $('statsCount');
    if (!tbody) return;

    if (countEl) countEl.textContent = `${statsFiltered.length} / ${statsAllData.length} записів`;

    if (statsFiltered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="12" class="empty-message">Немає даних</td></tr>';
        if (pagination) pagination.innerHTML = '';
        return;
    }

    const totalPages = Math.ceil(statsFiltered.length / STATS_PER_PAGE);
    const start = statsCurrentPage * STATS_PER_PAGE;
    const pageData = statsFiltered.slice(start, start + STATS_PER_PAGE);

    const dlIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">'
        + '<path d="M8 1v9m0 0L5 7m3 3l3-3M2 12v1a2 2 0 002 2h8a2 2 0 002-2v-1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    let html = '';
    for (const r of pageData) {
        const statusClass = r.status === 'OK' ? 'stats-ok' : 'stats-fail';
        const torqueStr = (r.torque !== '' && r.torque != null) ? r.torque : '';
        const videoLink = r.video_file
            ? `<a href="/api/camera/recordings/${encodeURIComponent(r.video_file)}" download title="Завантажити відео">${dlIcon}</a>`
            : '';
        html += `<tr>`;
        html += `<td class="stats-ts">${r.timestamp || ''}</td>`;
        html += `<td>${r.device_name || r.device || ''}</td>`;
        html += `<td>${r.group || ''}</td>`;
        html += `<td>${r.what || ''}</td>`;
        html += `<td>${r.screw_size || ''}</td>`;
        html += `<td>${torqueStr}</td>`;
        html += `<td>${r.task || ''}</td>`;
        html += `<td>${r.fixture || ''}</td>`;
        html += `<td>${r.screws}/${r.total_screws}</td>`;
        html += `<td>${r.cycle_time}</td>`;
        html += `<td><span class="stats-badge ${statusClass}">${r.status}</span></td>`;
        html += `<td class="stats-video">${videoLink}</td>`;
        html += `</tr>`;
    }
    tbody.innerHTML = html;

    // Sort arrows
    document.querySelectorAll('#statsTable th.sortable').forEach(th => {
        const arrow = th.querySelector('.sort-arrow');
        if (th.dataset.sort === statsSortCol) {
            arrow.textContent = statsSortAsc ? ' \u25B2' : ' \u25BC';
        } else {
            arrow.textContent = '';
        }
    });

    // Pagination
    if (pagination) {
        if (totalPages <= 1) {
            pagination.innerHTML = '';
        } else {
            let ph = '';
            ph += `<button class="btn btn-secondary btn-sm" ${statsCurrentPage === 0 ? 'disabled' : ''} onclick="statsGoPage(${statsCurrentPage - 1})">&laquo;</button>`;
            const maxBtns = 7;
            let pStart = Math.max(0, statsCurrentPage - 3);
            let pEnd = Math.min(totalPages, pStart + maxBtns);
            if (pEnd - pStart < maxBtns) pStart = Math.max(0, pEnd - maxBtns);
            for (let i = pStart; i < pEnd; i++) {
                ph += `<button class="btn btn-sm ${i === statsCurrentPage ? 'btn-primary' : 'btn-secondary'}" onclick="statsGoPage(${i})">${i + 1}</button>`;
            }
            ph += `<button class="btn btn-secondary btn-sm" ${statsCurrentPage >= totalPages - 1 ? 'disabled' : ''} onclick="statsGoPage(${statsCurrentPage + 1})">&raquo;</button>`;
            pagination.innerHTML = ph;
        }
    }
}

function statsGoPage(page) {
    const totalPages = Math.ceil(statsFiltered.length / STATS_PER_PAGE);
    if (page < 0 || page >= totalPages) return;
    statsCurrentPage = page;
    renderStatsTable();
}

async function populateStatsDropdowns() {
    // Populate device dropdown from system devices
    const devSelect = $('statsFilterDevice');
    if (devSelect) {
        const saved = devSelect.value;
        try {
            const devices = await api.get('/devices');
            const names = [...new Set(devices.map(d => d.name).filter(Boolean))];
            names.sort();
            devSelect.innerHTML = '<option value="">Всі девайси</option>';
            for (const n of names) {
                devSelect.innerHTML += `<option value="${n}">${n}</option>`;
            }
        } catch (e) { /* keep empty */ }
        devSelect.value = saved || '';
    }

    // Populate group dropdown from device groups
    const grpSelect = $('statsFilterGroup');
    if (grpSelect) {
        const saved = grpSelect.value;
        try {
            const data = await api.get('/device-groups');
            const groups = (data.groups || []).slice().sort();
            grpSelect.innerHTML = '<option value="">Всі групи</option>';
            for (const g of groups) {
                grpSelect.innerHTML += `<option value="${g}">${g}</option>`;
            }
        } catch (e) { /* keep empty */ }
        grpSelect.value = saved || '';
    }
}

function initStatsTab() {
    // Sort on header click
    document.querySelectorAll('#statsTable th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sort;
            if (statsSortCol === col) {
                statsSortAsc = !statsSortAsc;
            } else {
                statsSortCol = col;
                statsSortAsc = col === 'timestamp' ? false : true;
            }
            sortStatsData();
            statsCurrentPage = 0;
            renderStatsTable();
        });
    });

    // Live filters — use 'change' for selects, 'input' for date inputs
    ['statsFilterDevice', 'statsFilterGroup', 'statsFilterStatus'].forEach(id => {
        const el = $(id);
        if (el) el.addEventListener('change', applyStatsFilters);
    });
    ['statsFilterDateFrom', 'statsFilterDateTo'].forEach(id => {
        const el = $(id);
        if (el) el.addEventListener('input', applyStatsFilters);
    });

    // Populate dropdown options
    populateStatsDropdowns();

    // Export CSV button
    $('btnExportStats').addEventListener('click', exportStatsCSV);
}

function exportStatsCSV() {
    if (statsFiltered.length === 0) {
        alert('Немає даних для експорту');
        return;
    }

    const headers = ['Дата/час', 'Девайс', 'Група', 'Що крутим', 'Розмір', 'Момент', 'Таска', 'Оснастка', 'Гвинтів', 'Всього', 'Час (с)', 'Статус'];
    const rows = statsFiltered.map(r => [
        r.timestamp || '',
        r.device_name || r.device || '',
        r.group || '',
        r.what || '',
        r.screw_size || '',
        r.torque != null ? r.torque : '',
        r.task || '',
        r.fixture || '',
        r.screws,
        r.total_screws,
        r.cycle_time,
        r.status || '',
    ]);

    // BOM for Excel UTF-8 compatibility
    let csv = '\uFEFF' + headers.join(';') + '\n';
    for (const row of rows) {
        csv += row.map(v => {
            const s = String(v).replace(/"/g, '""');
            return s.includes(';') || s.includes('"') || s.includes('\n') ? `"${s}"` : s;
        }).join(';') + '\n';
    }

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `statistics_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ========== BARCODE SCANNER ==========

let scannerPollInterval = null;
let lastKnownScanCount = 0;

async function loadScannerStatus() {
    try {
        const data = await api.get('/scanner/status');

        // Update status badge
        const badge = $('scannerStatusBadge');
        if (data.connected) {
            badge.textContent = "Підключено";
            badge.className = 'scanner-status-badge connected';
        } else {
            badge.textContent = data.error || "Відключено";
            badge.className = 'scanner-status-badge disconnected';
        }

        // Device path
        $('scannerDevicePath').textContent = data.device_path || '--';

        // Scan count
        $('scannerScanCount').textContent = data.scan_count || 0;

        // Last scan value
        const valEl = $('scannerLastValue');
        if (data.last_scan) {
            valEl.textContent = data.last_scan;
        } else {
            valEl.textContent = '--';
        }

        // Last scan time
        const timeEl = $('scannerLastTime');
        if (data.last_scan_time) {
            const d = new Date(data.last_scan_time * 1000);
            timeEl.textContent = d.toLocaleString('uk-UA');
        } else {
            timeEl.textContent = '';
        }

        // History
        const histList = $('scannerHistoryList');
        if (data.history && data.history.length > 0) {
            // Show newest first
            const reversed = [...data.history].reverse();
            histList.innerHTML = reversed.map(item => {
                const t = new Date(item.time * 1000);
                const timeStr = t.toLocaleTimeString('uk-UA');
                return `<div class="scanner-history-item">
                    <span class="scan-val">${escapeHtml(item.value)}</span>
                    <span class="scan-time">${timeStr}</span>
                </div>`;
            }).join('');
        } else {
            histList.innerHTML = '<div class="scanner-history-empty">Немає сканувань</div>';
        }

        lastKnownScanCount = data.scan_count || 0;
    } catch (e) {
        console.error('Failed to load scanner status:', e);
    }
}

function startScannerPolling() {
    if (scannerPollInterval) return;
    loadScannerStatus();
    scannerPollInterval = setInterval(loadScannerStatus, 2000);
}

function stopScannerPolling() {
    if (scannerPollInterval) {
        clearInterval(scannerPollInterval);
        scannerPollInterval = null;
    }
}

function switchAdminSubtab(tabId) {
    // Update buttons
    $$('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`.admin-subtab-btn[data-admintab="${tabId}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    // Update panels
    $$('.admin-subtab-panel').forEach(p => p.classList.remove('active'));
    const panel = $(tabId);
    if (panel) panel.classList.add('active');

    // Load data for the selected sub-tab
    if (tabId === 'admin-scanner') {
        startScannerPolling();
    } else {
        stopScannerPolling();
    }
    if (tabId === 'admin-camera') {
        startCameraPolling();
    } else {
        stopCameraPolling();
    }
    if (tabId === 'admin-groups') {
        loadDeviceGroups();
    }
    if (tabId === 'admin-users') {
        loadUsers();
    }
    if (tabId === 'admin-backups') {
        loadBackupSettings();
        loadBackupList();
    }
}

function initAdminTab() {
    // Save user button
    $('btnSaveUser').addEventListener('click', saveUser);

    // Cancel edit button
    $('btnCancelUser').addEventListener('click', resetUserForm);

    // Camera controls
    $('btnCameraRec').addEventListener('click', startCameraRecording);
    $('btnCameraRecStop').addEventListener('click', stopCameraRecording);

    // Device groups management
    $('btnAddGroup').addEventListener('click', addDeviceGroup);
    $('newGroupName').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addDeviceGroup();
    });

    // Group editor buttons
    $('btnRenameGroup').addEventListener('click', renameDeviceGroup);
    $('editGroupName').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') renameDeviceGroup();
    });
    $('btnAddDeviceToGroup').addEventListener('click', addDeviceToGroup);

    // Backup controls
    $('btnCreateBackup').addEventListener('click', createBackup);
    $('btnSaveBackupSettings').addEventListener('click', saveBackupSettings);
    $('backupFileInput').addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            restoreFromFile(e.target.files[0]);
            e.target.value = '';  // reset so same file can be selected again
        }
    });

    // Admin sub-tab switching
    $$('.admin-subtab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchAdminSubtab(btn.dataset.admintab);
        });
    });

    // Load data when switching to/from admin main tab
    $$('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'admin') {
                // Activate the currently selected sub-tab
                const activeSubBtn = document.querySelector('.admin-subtab-btn.active');
                const activeSubTab = activeSubBtn ? activeSubBtn.dataset.admintab : 'admin-scanner';
                switchAdminSubtab(activeSubTab);
                loadUsers();
                loadDeviceGroups();
            } else {
                stopScannerPolling();
                stopCameraPolling();
            }
        });
    });
}

// ========== BACKUP MANAGEMENT ==========

async function loadBackupSettings() {
    try {
        const data = await api.get('/backup/settings');
        const chk = $('backupAutoEnabled');
        if (chk) chk.checked = !!data.auto_enabled;
        const intv = $('backupInterval');
        if (intv) intv.value = data.interval_hours || 24;
        const maxc = $('backupMaxCount');
        if (maxc) maxc.value = data.max_backups || 10;
        const dirInfo = $('backupDirInfo');
        if (dirInfo) {
            const src = data.usb_mounted ? 'USB-накопичувач' : 'Локально';
            dirInfo.textContent = `${data.backup_dir || '—'} (${src})`;
        }
    } catch (e) {
        console.error('Failed to load backup settings:', e);
    }
}

async function saveBackupSettings() {
    try {
        const auto_enabled = $('backupAutoEnabled').checked;
        const interval_hours = parseInt($('backupInterval').value) || 24;
        const max_backups = parseInt($('backupMaxCount').value) || 10;
        await api.post('/backup/settings', { auto_enabled, interval_hours, max_backups });
        showToast('Налаштування збережено');
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

async function loadBackupList() {
    const tbody = $('backupTableBody');
    if (!tbody) return;
    try {
        const data = await api.get('/backup/list');
        const backups = data.backups || [];
        if (backups.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-message">Немає бекапів</td></tr>';
            return;
        }
        const dlIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1v9m0 0L5 7m3 3l3-3M2 12v1a2 2 0 002 2h8a2 2 0 002-2v-1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        const restoreIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8a6 6 0 0111.47-2.4M14 2v3.6h-3.6M14 8A6 6 0 012.53 10.4M2 14v-3.6h3.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        const delIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M5.33 4V2.67a1.33 1.33 0 011.34-1.34h2.66a1.33 1.33 0 011.34 1.34V4m2 0v9.33a1.33 1.33 0 01-1.34 1.34H4.67a1.33 1.33 0 01-1.34-1.34V4h9.34z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        let html = '';
        for (const b of backups) {
            const sourceLabel = b.source === 'usb' ? 'USB' : 'Локально';
            html += `<tr>`;
            html += `<td class="stats-ts">${b.created}</td>`;
            html += `<td>${b.file}</td>`;
            html += `<td><span class="backup-source-badge backup-src-${b.source}">${sourceLabel}</span></td>`;
            html += `<td>${b.size_str}</td>`;
            html += `<td class="backup-actions-cell">`;
            html += `<a href="/api/backup/download/${encodeURIComponent(b.file)}" download class="backup-action-btn backup-btn-dl" title="Завантажити">${dlIcon}</a>`;
            html += `<button class="backup-action-btn backup-btn-restore" title="Відновити" onclick="restoreBackup('${b.path.replace(/'/g, "\\'")}')">${restoreIcon}</button>`;
            html += `<button class="backup-action-btn backup-btn-del" title="Видалити" onclick="deleteBackup('${b.path.replace(/'/g, "\\'")}')">${delIcon}</button>`;
            html += `</td>`;
            html += `</tr>`;
        }
        tbody.innerHTML = html;
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-message">Помилка завантаження</td></tr>';
    }
}

async function createBackup() {
    const btn = $('btnCreateBackup');
    if (btn) { btn.disabled = true; btn.textContent = 'Створення...'; }
    try {
        const result = await api.post('/backup/create', {});
        if (result.status === 'ok') {
            showToast(`Бекап створено: ${result.file} (${result.size_str})`);
            loadBackupList();
        } else {
            alert('Помилка: ' + (result.error || 'Невідома помилка'));
        }
    } catch (e) {
        alert('Помилка: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Створити бекап зараз'; }
    }
}

async function restoreBackup(path) {
    if (!confirm('Відновити конфігурацію з бекапу? Поточні налаштування будуть перезаписані.')) return;
    try {
        const result = await api.post('/backup/restore', { path });
        if (result.status === 'ok') {
            alert(`Відновлено ${result.restored.length} файлів:\n${result.restored.join('\n')}\n\nСторінка буде перезавантажена.`);
            location.reload();
        } else {
            alert('Помилка: ' + (result.error || 'Невідома помилка'));
        }
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

async function restoreFromFile(file) {
    if (!file) return;
    if (!confirm('Відновити конфігурацію з файлу? Поточні налаштування будуть перезаписані.')) return;
    try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/api/backup/restore', { method: 'POST', body: formData });
        const result = await resp.json();
        if (result.status === 'ok') {
            alert(`Відновлено ${result.restored.length} файлів:\n${result.restored.join('\n')}\n\nСторінка буде перезавантажена.`);
            location.reload();
        } else {
            alert('Помилка: ' + (result.error || 'Невідома помилка'));
        }
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

async function deleteBackup(path) {
    if (!confirm('Видалити цей бекап?')) return;
    try {
        await api.post('/backup/delete', { path });
        loadBackupList();
        showToast('Бекап видалено');
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

function showToast(msg) {
    // Simple toast notification
    let toast = document.getElementById('backupToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'backupToast';
        toast.className = 'backup-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

// ========== LOGS PANEL ==========

let logsLastId = 0;
let logsRefreshInterval = null;
let logsData = [];

const LOG_LEVEL_COLORS = {
    'DEBUG': 'log-debug',
    'INFO': 'log-info',
    'WARNING': 'log-warning',
    'ERROR': 'log-error',
    'CRITICAL': 'log-critical'
};

const LOG_CATEGORY_LABELS = {
    'SYSTEM': 'Система',
    'AUTH': 'Авторизація',
    'XY': 'XY Стіл',
    'CYCLE': 'Цикл',
    'RELAY': 'Реле',
    'SENSOR': 'Датчики',
    'API': 'API',
    'DEVICE': 'Девайси',
    'GCODE': 'G-Code',
    'COMM': 'Комунікація',
    'ERROR': 'Помилки'
};

async function loadLogs(reset = false) {
    try {
        const level = $('logLevelFilter').value;
        const category = $('logCategoryFilter').value;
        const search = $('logSearchFilter').value;

        let url = '/api/logs?limit=500';
        if (level) url += `&level=${level}`;
        if (category) url += `&category=${category}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (!reset && logsLastId > 0) url += `&since_id=${logsLastId}`;

        const response = await api.get(url.replace('/api', ''));
        const newLogs = response.logs || [];

        if (reset) {
            logsData = newLogs;
            logsLastId = 0;
        } else {
            logsData = [...logsData, ...newLogs];
        }

        // Update last ID
        if (logsData.length > 0) {
            logsLastId = Math.max(...logsData.map(l => l.id));
        }

        renderLogs();
        updateLogStats();
    } catch (e) {
        console.error('Failed to load logs:', e);
    }
}

function renderLogs() {
    const container = $('logsContainer');
    if (!container) return;

    if (logsData.length === 0) {
        container.innerHTML = '<div class="logs-empty">Немає логів для відображення</div>';
        return;
    }

    // Sort by ID descending (newest first at top? No, let's keep chronological with newest at bottom)
    const sortedLogs = [...logsData].sort((a, b) => a.id - b.id);

    let html = '<div class="logs-list">';
    for (const log of sortedLogs) {
        const levelClass = LOG_LEVEL_COLORS[log.level] || 'log-info';
        const categoryLabel = LOG_CATEGORY_LABELS[log.category] || log.category;
        const source = log.source ? `[${log.source}]` : '';

        html += `<div class="log-entry ${levelClass}">`;
        html += `<span class="log-time">${log.timestamp_display}</span>`;
        html += `<span class="log-level">${log.level}</span>`;
        html += `<span class="log-category">${categoryLabel}</span>`;
        if (source) {
            html += `<span class="log-source">${source}</span>`;
        }
        html += `<span class="log-message">${escapeHtml(log.message)}</span>`;
        if (log.details && Object.keys(log.details).length > 0) {
            html += `<span class="log-details" title="${escapeHtml(JSON.stringify(log.details))}">📋</span>`;
        }
        html += '</div>';
    }
    html += '</div>';

    container.innerHTML = html;

    // Auto-scroll to bottom
    if ($('logAutoScroll').checked) {
        container.scrollTop = container.scrollHeight;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function updateLogStats() {
    try {
        const response = await api.get('/logs/stats');

        $('logStatTotal').textContent = response.total || 0;
        $('logStatInfo').textContent = response.by_level?.INFO || 0;
        $('logStatWarning').textContent = response.by_level?.WARNING || 0;
        $('logStatError').textContent = response.by_level?.ERROR || 0;
        $('logStatCritical').textContent = response.by_level?.CRITICAL || 0;
    } catch (e) {
        console.error('Failed to load log stats:', e);
    }
}

async function clearLogs() {
    if (!confirm('Очистити буфер логів?')) return;

    try {
        await api.post('/logs/clear');
        logsData = [];
        logsLastId = 0;
        loadLogs(true);
    } catch (e) {
        alert('Помилка: ' + e.message);
    }
}

function startLogsAutoRefresh() {
    if (logsRefreshInterval) return;

    logsRefreshInterval = setInterval(() => {
        if ($('logAutoRefresh').checked) {
            loadLogs(false);
        }
    }, 2000); // Refresh every 2 seconds
}

function stopLogsAutoRefresh() {
    if (logsRefreshInterval) {
        clearInterval(logsRefreshInterval);
        logsRefreshInterval = null;
    }
}

function initLogsTab() {
    // Filter change handlers
    $('logLevelFilter').addEventListener('change', () => loadLogs(true));
    $('logCategoryFilter').addEventListener('change', () => loadLogs(true));

    // Search with debounce
    let searchTimeout;
    $('logSearchFilter').addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => loadLogs(true), 300);
    });

    // Button handlers
    $('btnRefreshLogs').addEventListener('click', () => loadLogs(true));
    $('btnClearLogs').addEventListener('click', clearLogs);

    // Auto-refresh toggle
    $('logAutoRefresh').addEventListener('change', (e) => {
        if (e.target.checked) {
            startLogsAutoRefresh();
        }
    });

    // Load logs when switching to logs tab
    $$('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'logs') {
                loadLogs(true);
                startLogsAutoRefresh();
            } else {
                // Don't stop refresh - keep updating in background
            }
        });
    });
}

// Initialize Application
async function init() {
    // Check auth status first
    const isLoggedIn = await checkAuthStatus();
    if (!isLoggedIn) {
        window.location.href = '/login';
        return;
    }

    initTabs();
    initControlTab();
    initXYTab();
    initStatsTab();
    initSettingsTab();
    initAdminTab();
    initLogsTab();

    // Setup logout button
    $('btnLogout').addEventListener('click', handleLogout);

    // Initialize Cycle Status panel with default values
    updateCycleStatusPanel('IDLE', '-', 0, 0);

    // Start polling
    updateStatus();
    setInterval(updateStatus, POLL_INTERVAL);

    // Load initial data
    loadDeviceGroups();
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
window.deleteDeviceGroup = deleteDeviceGroup;
window.toggleDeviceGroup = toggleDeviceGroup;
window.onCoordSourceChange = onCoordSourceChange;
window.setCurrentCoord = setCurrentCoord;
window.moveCoordUp = moveCoordUp;
window.moveCoordDown = moveCoordDown;
window.updateTypeStyle = updateTypeStyle;
window.editUser = editUser;
window.deleteUser = deleteUser;
