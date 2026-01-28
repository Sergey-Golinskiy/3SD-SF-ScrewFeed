# REST API Reference

API сервер запускается на порту 5000 по умолчанию.

Базовый URL: `http://<master-pi-ip>:5000/api`

---

## Health & Status

### GET /api/health

Проверка работоспособности системы.

**Response:**
```json
{
    "status": "ok",
    "gpio_initialized": true,
    "xy_connected": true,
    "cycle_state": "IDLE"
}
```

### GET /api/status

Полный статус системы.

**Response:**
```json
{
    "relays": {
        "screwdriver_power": "OFF",
        "screwdriver_direction": "OFF",
        "cylinder_down": "OFF",
        "cylinder_up": "OFF",
        "vacuum": "OFF",
        "blow": "OFF"
    },
    "sensors": {
        "area_sensor": "INACTIVE",
        "emergency_stop": "INACTIVE",
        "screw_present": "INACTIVE",
        "torque_reached": "INACTIVE",
        "cylinder_up": "ACTIVE",
        "cylinder_down": "INACTIVE"
    },
    "xy_table": {
        "connected": true,
        "state": "READY",
        "x": 0.0,
        "y": 0.0
    },
    "cycle": {
        "state": "IDLE",
        "error": "NONE",
        "error_message": "",
        "current_device": "",
        "current_step": 0,
        "total_steps": 0,
        "holes_completed": 0,
        "total_holes": 0,
        "cycle_count": 0
    }
}
```

---

## Relay Control

### GET /api/relays

Получить состояние всех реле.

**Response:**
```json
{
    "screwdriver_power": "OFF",
    "screwdriver_direction": "OFF",
    "cylinder_down": "OFF",
    "cylinder_up": "OFF",
    "vacuum": "OFF",
    "blow": "OFF"
}
```

### GET /api/relays/{name}

Получить состояние одного реле.

**Response:**
```json
{
    "name": "vacuum",
    "state": "ON"
}
```

### POST /api/relays/{name}

Установить состояние реле.

**Request Body:**
```json
{
    "state": "on"  // "on", "off", "toggle", or "pulse"
}
```

Для pulse можно указать длительность:
```json
{
    "state": "pulse",
    "duration": 0.5
}
```

**Response:**
```json
{
    "name": "vacuum",
    "state": "ON"
}
```

### POST /api/relays/all/off

Выключить все реле.

**Response:**
```json
{
    "status": "ok"
}
```

---

## Sensor Reading

### GET /api/sensors

Получить состояние всех датчиков.

**Response:**
```json
{
    "area_sensor": "INACTIVE",
    "emergency_stop": "INACTIVE",
    "screw_present": "INACTIVE",
    "torque_reached": "INACTIVE",
    "cylinder_up": "ACTIVE",
    "cylinder_down": "INACTIVE"
}
```

### GET /api/sensors/{name}

Получить состояние одного датчика.

**Response:**
```json
{
    "name": "area_sensor",
    "state": "INACTIVE",
    "active": false
}
```

### GET /api/sensors/safety

Получить статус датчиков безопасности.

**Response:**
```json
{
    "safe": true,
    "estop_pressed": false,
    "area_blocked": false
}
```

---

## XY Table Control

### GET /api/xy/status

Получить статус XY стола.

**Response:**
```json
{
    "connected": true,
    "state": "READY",
    "ready": true,
    "position": {
        "x": 100.0,
        "y": 200.0
    }
}
```

Возможные состояния (`state`):
- `DISCONNECTED` - нет связи
- `READY` - готов к командам
- `MOVING` - в движении
- `HOMING` - калибровка
- `ERROR` - ошибка
- `ESTOP` - аварийная остановка

### POST /api/xy/connect

Подключиться к XY столу.

**Response:**
```json
{
    "status": "connected"
}
```

### POST /api/xy/disconnect

Отключиться от XY стола.

**Response:**
```json
{
    "status": "disconnected"
}
```

### POST /api/xy/home

Выполнить калибровку (homing).

**Request Body (optional):**
```json
{
    "axis": "X"  // "X", "Y", or null for both
}
```

**Response:**
```json
{
    "status": "homed",
    "axis": "all"
}
```

### POST /api/xy/move

Переместить в абсолютную позицию.

**Request Body:**
```json
{
    "x": 100.0,
    "y": 200.0,
    "feed": 10000.0  // мм/мин (опционально)
}
```

**Response:**
```json
{
    "status": "ok",
    "position": {
        "x": 100.0,
        "y": 200.0
    }
}
```

### POST /api/xy/jog

Переместить относительно текущей позиции.

**Request Body:**
```json
{
    "dx": 10.0,
    "dy": -5.0,
    "feed": 600.0
}
```

**Response:**
```json
{
    "status": "ok",
    "position": {
        "x": 110.0,
        "y": 195.0
    }
}
```

### POST /api/xy/zero

Переместить в нулевую позицию.

**Response:**
```json
{
    "status": "ok"
}
```

### POST /api/xy/estop

Аварийная остановка XY стола.

**Response:**
```json
{
    "status": "estop_active"
}
```

### POST /api/xy/clear_estop

Сбросить аварийную остановку.

**Response:**
```json
{
    "status": "estop_cleared"
}
```

---

## Cycle Control

### GET /api/cycle/status

Получить статус цикла автоматизации.

**Response:**
```json
{
    "state": "RUNNING",
    "error": "NONE",
    "error_message": "",
    "current_device": "MCO_4holes",
    "current_step": 3,
    "total_steps": 12,
    "holes_completed": 1,
    "total_holes": 4,
    "cycle_count": 15,
    "is_running": true,
    "is_paused": false
}
```

Возможные состояния (`state`):
- `IDLE` - система не готова
- `READY` - готова к старту
- `HOMING` - калибровка
- `MOVING_FREE` - свободное перемещение
- `MOVING_WORK` - перемещение к рабочей позиции
- `LOWERING` - опускание цилиндра
- `SCREWING` - закручивание
- `RAISING` - подъём цилиндра
- `VERIFYING` - проверка
- `PAUSED` - пауза
- `ERROR` - ошибка
- `ESTOP` - аварийная остановка
- `COMPLETED` - цикл завершён

### POST /api/cycle/start

Запустить цикл для устройства.

**Request Body:**
```json
{
    "device": "MCO_4holes"
}
```

**Response:**
```json
{
    "status": "started",
    "device": "MCO_4holes"
}
```

### POST /api/cycle/stop

Остановить текущий цикл.

**Response:**
```json
{
    "status": "stopped"
}
```

### POST /api/cycle/pause

Приостановить цикл.

**Response:**
```json
{
    "status": "paused"
}
```

### POST /api/cycle/resume

Возобновить приостановленный цикл.

**Response:**
```json
{
    "status": "resumed"
}
```

### POST /api/cycle/estop

Аварийная остановка всей системы.

**Response:**
```json
{
    "status": "estop_active"
}
```

### POST /api/cycle/clear_estop

Сбросить аварийную остановку.

**Response:**
```json
{
    "status": "estop_cleared"
}
```

---

## Devices

### GET /api/devices

Получить список доступных устройств (программ).

**Response:**
```json
[
    {
        "key": "MCO_4holes",
        "name": "MCO_Back_M3x10",
        "holes": 4,
        "steps": 12
    }
]
```

### GET /api/devices/{key}

Получить детали программы устройства.

**Response:**
```json
{
    "key": "MCO_4holes",
    "name": "MCO_Back_M3x10",
    "holes": 4,
    "steps": [
        {"type": "free", "x": 5.0, "y": 108.5, "feed": 60000.0},
        {"type": "work", "x": 29.5, "y": 108.5, "feed": 60000.0},
        {"type": "free", "x": 29.5, "y": 115.0, "feed": 60000.0}
    ]
}
```

---

## Error Handling

Все ошибки возвращаются в формате:

```json
{
    "error": "Error message description"
}
```

HTTP коды:
- `200` - успех
- `400` - неверный запрос (отсутствуют параметры)
- `404` - ресурс не найден
- `500` - внутренняя ошибка
- `503` - сервис недоступен (компонент не инициализирован)

---

## Примеры использования

### Python (requests)

```python
import requests

BASE_URL = "http://192.168.1.100:5000/api"

# Получить статус
r = requests.get(f"{BASE_URL}/status")
print(r.json())

# Включить реле
r = requests.post(f"{BASE_URL}/relays/vacuum", json={"state": "on"})
print(r.json())

# Переместить XY стол
r = requests.post(f"{BASE_URL}/xy/move", json={"x": 100, "y": 200, "feed": 10000})
print(r.json())

# Запустить цикл
r = requests.post(f"{BASE_URL}/cycle/start", json={"device": "MCO_4holes"})
print(r.json())
```

### JavaScript (fetch)

```javascript
const BASE_URL = 'http://192.168.1.100:5000/api';

// Получить статус
const status = await fetch(`${BASE_URL}/status`).then(r => r.json());
console.log(status);

// Переместить XY стол
await fetch(`${BASE_URL}/xy/move`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({x: 100, y: 200, feed: 10000})
});
```

### cURL

```bash
# Статус
curl http://localhost:5000/api/status

# Включить реле
curl -X POST http://localhost:5000/api/relays/vacuum \
     -H "Content-Type: application/json" \
     -d '{"state": "on"}'

# Переместить XY стол
curl -X POST http://localhost:5000/api/xy/move \
     -H "Content-Type: application/json" \
     -d '{"x": 100, "y": 200, "feed": 10000}'
```
