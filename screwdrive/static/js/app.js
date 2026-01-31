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
    coordRows: []
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

    } catch (error) {
        state.connected = false;
        updateConnectionIndicator(false);
        console.error('Status update failed:', error);
    }
}

function updateConnectionIndicator(connected) {
    const indicator = $('connectionStatus');
    indicator.className = `status-indicator ${connected ? 'connected' : 'error'}`;
    indicator.querySelector('.text').textContent = connected ? 'Connected' : 'Disconnected';
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
        // Initial render
        for (const [name, value] of Object.entries(relays)) {
            const isOn = value === 'ON' || value === true;
            grid.innerHTML += `
                <div class="relay-control">
                    <span class="name">${formatName(name)}</span>
                    <button class="toggle-btn ${isOn ? 'on' : 'off'}"
                            data-relay="${name}"
                            onclick="toggleRelay('${name}')">
                        ${isOn ? 'ON' : 'OFF'}
                    </button>
                </div>
            `;
        }
    } else {
        // Update existing
        for (const [name, value] of Object.entries(relays)) {
            const isOn = value === 'ON' || value === true;
            const btn = grid.querySelector(`[data-relay="${name}"]`);
            if (btn) {
                btn.className = `toggle-btn ${isOn ? 'on' : 'off'}`;
                btn.textContent = isOn ? 'ON' : 'OFF';
            }
        }
    }
}

async function toggleRelay(name) {
    try {
        await api.post(`/relays/${name}`, { state: 'toggle' });
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
    const x = (xy.x || 0).toFixed(2);
    const y = (xy.y || 0).toFixed(2);

    $('xyPosDisplay').textContent = `X: ${x}  Y: ${y}`;
    $('xyStateDisplay').textContent = xy.state || '-';
}

function initXYTab() {
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
        list.innerHTML += `
            <div class="device-item ${isSelected ? 'selected' : ''}"
                 data-key="${device.key}"
                 onclick="selectDevice('${device.key}')">
                <div class="device-name">${device.name}</div>
                <div class="device-info">${device.holes} holes, ${device.steps} steps</div>
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
        alert('Device name is required');
        return;
    }

    // Generate key from name if new device
    const deviceKey = key || name.toUpperCase().replace(/\s+/g, '_');

    // Collect coordinates
    const steps = [];
    const rows = $('coordsList').querySelectorAll('.coord-row');
    rows.forEach(row => {
        const x = parseFloat(row.querySelector('.coord-x').value) || 0;
        const y = parseFloat(row.querySelector('.coord-y').value) || 0;
        const type = row.querySelector('.coord-type').value;
        const feed = parseFloat(row.querySelector('.coord-feed').value) || 5000;
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

function addCoordRow(x = '', y = '', type = 'free', feed = 5000) {
    const list = $('coordsList');
    const rowNum = list.children.length + 1;

    const row = document.createElement('div');
    row.className = 'coord-row';
    row.innerHTML = `
        <span class="row-num">${rowNum}</span>
        <input type="number" class="coord-x" value="${x}" step="0.1" placeholder="0">
        <input type="number" class="coord-y" value="${y}" step="0.1" placeholder="0">
        <select class="coord-type">
            <option value="free" ${type === 'free' ? 'selected' : ''}>FREE</option>
            <option value="work" ${type === 'work' ? 'selected' : ''}>WORK</option>
        </select>
        <input type="number" class="coord-feed" value="${feed}" step="100" placeholder="5000">
        <button class="btn-go" onclick="goToCoord(this)">GO</button>
        <button class="btn btn-del" onclick="removeCoordRow(this)">-</button>
    `;

    list.appendChild(row);
    state.coordRows.push(row);
}

function removeCoordRow(btn) {
    const row = btn.closest('.coord-row');
    row.remove();
    renumberCoordRows();
}

function renumberCoordRows() {
    const rows = $('coordsList').querySelectorAll('.coord-row');
    rows.forEach((row, i) => {
        row.querySelector('.row-num').textContent = i + 1;
    });
}

async function goToCoord(btn) {
    const row = btn.closest('.coord-row');
    const x = parseFloat(row.querySelector('.coord-x').value) || 0;
    const y = parseFloat(row.querySelector('.coord-y').value) || 0;
    const feed = parseFloat(row.querySelector('.coord-feed').value) || 5000;

    try {
        await api.post('/xy/move', { x, y, feed });
    } catch (error) {
        alert('Move failed: ' + error.message);
    }
}

function initSettingsTab() {
    $('btnNewDevice').addEventListener('click', newDevice);
    $('btnAddCoord').addEventListener('click', () => addCoordRow());
    $('btnSaveDevice').addEventListener('click', saveDevice);
    $('btnCancelEdit').addEventListener('click', cancelEdit);
    $('btnDeleteDevice').addEventListener('click', deleteDevice);
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
window.goToCoord = goToCoord;
window.removeCoordRow = removeCoordRow;
