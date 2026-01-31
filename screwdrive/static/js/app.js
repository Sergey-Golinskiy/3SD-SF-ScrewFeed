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
    lastEstopState: null  // Track emergency stop button state
};

// API Functions
const api = {
    async get(path) {
        const response = await fetch(`${API_BASE}${path}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    },

    async post(path, data = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    },

    async put(path, data = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    },

    async delete(path) {
        const response = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
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

    // Check if state changed
    if (state.lastEstopState !== null && state.lastEstopState !== estopActive) {
        if (estopActive) {
            // Button pressed - trigger E-STOP
            console.log('Emergency stop button PRESSED - triggering E-STOP');
            try {
                await api.post('/cycle/estop');
                await api.post('/xy/estop');
            } catch (error) {
                console.error('E-STOP trigger failed:', error);
            }
        } else {
            // Button released - clear E-STOP
            console.log('Emergency stop button RELEASED - clearing E-STOP');
            try {
                await api.post('/cycle/clear_estop');
                await api.post('/xy/clear_estop');
            } catch (error) {
                console.error('Clear E-STOP failed:', error);
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
    $('xyState').textContent = xy.state || '-';
    const x = (xy.x || 0).toFixed(2);
    const y = (xy.y || 0).toFixed(2);
    $('xyPosition').textContent = `X: ${x}  Y: ${y}`;

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

// XY Table Tab
function updateXYTab(status) {
    const xy = status.xy_table || {};
    const health = xy.health || {};
    const endstops = xy.endstops || {};

    // Position
    const x = (xy.x || 0).toFixed(2);
    const y = (xy.y || 0).toFixed(2);
    $('xyPosDisplay').textContent = `X: ${x}  Y: ${y}`;

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

    // Error
    const errorEl = $('xyLastError');
    if (health.last_error) {
        errorEl.textContent = health.last_error;
        errorEl.className = 'value';
    } else {
        errorEl.textContent = '-';
        errorEl.className = 'value';
    }

    // Homed status
    const homedX = $('xyHomedX');
    homedX.textContent = xy.x_homed ? 'X: захомлено' : 'X: не захомлено';
    homedX.className = `homed-indicator ${xy.x_homed ? 'homed' : 'not-homed'}`;

    const homedY = $('xyHomedY');
    homedY.textContent = xy.y_homed ? 'Y: захомлено' : 'Y: не захомлено';
    homedY.className = `homed-indicator ${xy.y_homed ? 'homed' : 'not-homed'}`;
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
        'ESTOP': 'E-STOP',
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

            api.post('/xy/jog', { dx, dy, feed });
        });
    });

    // Home buttons
    $('btnXYHome').addEventListener('click', () => api.post('/xy/home'));
    $('btnHomeX').addEventListener('click', () => api.post('/xy/home/x'));
    $('btnHomeY').addEventListener('click', () => api.post('/xy/home/y'));
    $('btnZero').addEventListener('click', () => api.post('/xy/zero'));

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
        api.post('/xy/move', { x, y, feed });
    });
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
        // Format: NAME_SCREWSIZE (X holes)
        const displayName = device.screw_size
            ? `${device.name}_${device.screw_size} (${device.holes} holes)`
            : `${device.name} (${device.holes} holes)`;
        list.innerHTML += `
            <div class="device-item ${isSelected ? 'selected' : ''}"
                 data-key="${device.key}"
                 onclick="selectDevice('${device.key}')">
                <div class="device-name">${displayName}</div>
            </div>
        `;
    }
}

function updateDeviceSelect() {
    const select = $('deviceSelect');
    select.innerHTML = '<option value="">-- Select Device --</option>';

    for (const device of state.devices) {
        select.innerHTML += `<option value="${device.key}">${device.name} (${device.holes} holes)</option>`;
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
    $('editName').value = device.name || '';
    $('editWhat').value = device.what || '';
    $('editHoles').value = device.holes || 0;
    $('editScrewSize').value = device.screw_size || '';
    $('editTask').value = device.task || '';

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
    $('editHoles').value = 0;
    $('editScrewSize').value = '';
    $('editTask').value = '';

    clearCoordRows();
    addCoordRow();
}

async function saveDevice() {
    const key = $('editDeviceKey').value.trim();
    const name = $('editName').value.trim();

    if (!name) {
        alert('Назва девайсу обов\'язкова');
        return;
    }

    // Generate key from name if new device
    const deviceKey = key || name.toUpperCase().replace(/\s+/g, '_');

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
        what: $('editWhat').value.trim(),
        holes: parseInt($('editHoles').value) || 0,
        screw_size: $('editScrewSize').value.trim(),
        task: $('editTask').value.trim(),
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

            api.post('/xy/jog', { dx, dy, feed });
        });
    });

    // Home buttons in settings
    $('btnHomeSettings').addEventListener('click', () => api.post('/xy/home'));
    $('btnHomeXSettings').addEventListener('click', () => api.post('/xy/home/x'));
    $('btnHomeYSettings').addEventListener('click', () => api.post('/xy/home/y'));
}

// Update XY position on settings tab
function updateSettingsXYPos(status) {
    const xy = status.xy_table || {};
    const x = (xy.x || 0).toFixed(2);
    const y = (xy.y || 0).toFixed(2);
    const posDisplay = $('settingsXYPos');
    if (posDisplay) {
        posDisplay.textContent = `X: ${x}  Y: ${y}`;
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
