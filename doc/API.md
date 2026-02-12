# REST API документація

## Базова інформація

| Параметр | Значення |
|----------|----------|
| Base URL | `http://<host>:5000/api` |
| Формат | JSON |
| Автентифікація | Cookie-based session |

---

## Системні ендпоінти

### GET /api/health

Перевірка стану системи.

**Відповідь:**
```json
{
  "status": "ok",
  "timestamp": "2024-02-05T10:30:00Z"
}
```

### GET /api/status

Повний статус системи.

**Відповідь:**
```json
{
  "xy_table": {
    "connected": true,
    "state": "ready",
    "position": {"x": 100.0, "y": 200.0},
    "homed": {"x": true, "y": true},
    "estop": false
  },
  "sensors": {
    "area_sensor": "INACTIVE",
    "emergency_stop": "INACTIVE",
    "ger_c2_up": "ACTIVE",
    "do2_ok": "INACTIVE"
  },
  "relays": {
    "r01_pit": "OFF",
    "r04_c2": "OFF",
    "r06_di1_pot": "OFF"
  },
  "cycle": {
    "state": "IDLE",
    "device": null,
    "holes_completed": 0,
    "total_holes": 0
  }
}
```

---

## Датчики

### GET /api/sensors

Отримати стан всіх датчиків.

**Відповідь:**
```json
{
  "alarm_x": "INACTIVE",
  "alarm_y": "INACTIVE",
  "area_sensor": "INACTIVE",
  "ped_start": "INACTIVE",
  "ger_c2_up": "ACTIVE",
  "ger_c2_down": "INACTIVE",
  "ind_scrw": "INACTIVE",
  "do2_ok": "INACTIVE",
  "emergency_stop": "INACTIVE"
}
```

### GET /api/sensors/safety

Отримати стан безпеки.

**Відповідь:**
```json
{
  "estop_pressed": false,
  "area_blocked": false,
  "driver_alarm_x": false,
  "driver_alarm_y": false,
  "safe_to_operate": true
}
```

### GET /api/sensors/{name}

Отримати стан конкретного датчика.

**Параметри:**
- `name` - назва датчика (area_sensor, emergency_stop, тощо)

**Відповідь:**
```json
{
  "name": "area_sensor",
  "state": "INACTIVE",
  "gpio": 17,
  "active_low": true
}
```

---

## Реле

### GET /api/relays

Отримати стан всіх реле.

**Відповідь:**
```json
{
  "r01_pit": "OFF",
  "r02_brake_x": "ON",
  "r03_brake_y": "ON",
  "r04_c2": "OFF",
  "r05_di4_free": "OFF",
  "r06_di1_pot": "OFF",
  "r07_di5_tsk0": "OFF",
  "r08_di6_tsk1": "OFF",
  "r09_pwr_x": "OFF",
  "r10_pwr_y": "OFF"
}
```

### POST /api/relays/{name}

Керування реле.

**Параметри:**
- `name` - назва реле

**Тіло запиту:**
```json
{
  "state": "on"
}
```

Або для імпульсу:
```json
{
  "state": "pulse",
  "duration": 200
}
```

**Значення state:**
- `"on"` - увімкнути
- `"off"` - вимкнути
- `"pulse"` - імпульс (потрібен `duration` в мс)

**Відповідь:**
```json
{
  "status": "ok",
  "relay": "r01_pit",
  "state": "ON"
}
```

---

## XY Стіл

### GET /api/xy/status

Отримати статус XY столу.

**Відповідь:**
```json
{
  "connected": true,
  "state": "ready",
  "position": {
    "x": 100.0,
    "y": 200.0
  },
  "homed": {
    "x": true,
    "y": true
  },
  "estop": false,
  "endstops": {
    "x_min": "open",
    "y_min": "open"
  },
  "health": {
    "ping_ok": true,
    "latency_ms": 5
  }
}
```

### POST /api/xy/connect

Підключитися до XY столу.

**Відповідь:**
```json
{
  "status": "connected"
}
```

### POST /api/xy/disconnect

Відключитися від XY столу.

**Відповідь:**
```json
{
  "status": "disconnected"
}
```

### POST /api/xy/home

Виконати хомінг.

**Тіло запиту (опціонально):**
```json
{
  "axis": "X"
}
```

Значення `axis`:
- `null` або відсутнє - обидві осі
- `"X"` - тільки X
- `"Y"` - тільки Y

**Відповідь:**
```json
{
  "status": "homed",
  "position": {"x": 0.0, "y": 0.0}
}
```

### POST /api/xy/move

Переміщення до позиції.

**Тіло запиту:**
```json
{
  "x": 100.0,
  "y": 200.0,
  "feed": 10000
}
```

**Відповідь:**
```json
{
  "status": "ok",
  "position": {"x": 100.0, "y": 200.0}
}
```

### POST /api/xy/jog

Відносне переміщення.

**Тіло запиту:**
```json
{
  "dx": 10.0,
  "dy": 0.0,
  "feed": 5000
}
```

**Відповідь:**
```json
{
  "status": "ok",
  "position": {"x": 110.0, "y": 200.0}
}
```

### POST /api/xy/command

Відправити команду напряму.

**Тіло запиту:**
```json
{
  "command": "G X100 Y200 F10000"
}
```

**Відповідь:**
```json
{
  "status": "ok",
  "response": "ok"
}
```

### POST /api/xy/estop

Аварійна зупинка XY столу.

**Відповідь:**
```json
{
  "status": "estop_activated"
}
```

### POST /api/xy/clear_estop

Скинути E-STOP.

**Відповідь:**
```json
{
  "status": "estop_cleared"
}
```

### POST /api/xy/enable_motors

Увімкнути мотори.

**Відповідь:**
```json
{
  "status": "motors_enabled"
}
```

### POST /api/xy/disable_motors

Вимкнути мотори.

**Відповідь:**
```json
{
  "status": "motors_disabled"
}
```

---

## Девайси

### GET /api/devices

Отримати список девайсів.

**Відповідь:**
```json
{
  "devices": [
    {
      "key": "MCO_BACK_M3x10",
      "name": "MCO",
      "holes": 4,
      "screw_size": "M3x10",
      "task": "0",
      "torque": 0.8,
      "work_x": 50.0,
      "work_y": 100.0
    }
  ]
}
```

### GET /api/devices/{key}

Отримати девайс з повною програмою.

**Відповідь:**
```json
{
  "key": "MCO_BACK_M3x10",
  "name": "MCO",
  "what": "Задня панель",
  "holes": 4,
  "screw_size": "M3x10",
  "task": "0",
  "torque": 0.8,
  "work_x": 50.0,
  "work_y": 100.0,
  "work_feed": 5000,
  "steps": [
    {"x": 5.0, "y": 10.0, "type": "free", "feed": 60000},
    {"x": 50.0, "y": 100.0, "type": "work", "feed": 30000}
  ]
}
```

### POST /api/devices

Створити новий девайс.

**Тіло запиту:**
```json
{
  "name": "NEW",
  "what": "Нова деталь",
  "holes": 2,
  "screw_size": "M3x8",
  "task": "0",
  "torque": 0.5,
  "work_x": 50.0,
  "work_y": 100.0,
  "work_feed": 5000,
  "steps": [
    {"x": 50.0, "y": 100.0, "type": "work", "feed": 30000}
  ]
}
```

**Відповідь:**
```json
{
  "status": "created",
  "key": "NEW_НОВА_M3x8"
}
```

### PUT /api/devices/{key}

Оновити девайс.

**Тіло запиту:** (аналогічно POST)

**Відповідь:**
```json
{
  "status": "updated",
  "key": "NEW_НОВА_M3x8"
}
```

### DELETE /api/devices/{key}

Видалити девайс.

**Відповідь:**
```json
{
  "status": "deleted"
}
```

---

## Цикл

### POST /api/cycle/estop

Активувати аварійну зупинку.

**Відповідь:**
```json
{
  "status": "estop_activated"
}
```

### POST /api/cycle/clear_estop

Скинути аварійну зупинку.

**Відповідь:**
```json
{
  "status": "estop_cleared"
}
```

---

## UI State (синхронізація)

### GET /api/ui/state

Отримати стан UI.

**Відповідь:**
```json
{
  "operator": "desktop",
  "selected_device": "MCO_BACK_M3x10",
  "cycle_state": "RUNNING",
  "holes_completed": 2,
  "total_holes": 4,
  "initialized": true,
  "status_message": "Закручування 2/4"
}
```

### POST /api/ui/state

Оновити стан UI.

**Тіло запиту:**
```json
{
  "operator": "desktop",
  "cycle_state": "RUNNING",
  "status_message": "Закручування"
}
```

### POST /api/ui/select-device

Вибрати девайс.

**Тіло запиту:**
```json
{
  "device": "MCO_BACK_M3x10"
}
```

---

## Офсети

### GET /api/offsets

Отримати робочі офсети.

**Відповідь:**
```json
{
  "x": 50.0,
  "y": 100.0
}
```

### POST /api/offsets

Встановити офсети.

**Тіло запиту:**
```json
{
  "x": 50.0,
  "y": 100.0
}
```

---

## Логи

### GET /api/logs

Отримати логи.

**Параметри запиту:**
- `level` - фільтр за рівнем (INFO, WARNING, ERROR)
- `category` - фільтр за категорією
- `search` - пошук за текстом
- `limit` - максимальна кількість записів

**Відповідь:**
```json
{
  "logs": [
    {
      "timestamp": "2024-02-05T10:30:00Z",
      "level": "INFO",
      "category": "Cycle",
      "message": "Цикл запущено",
      "details": null
    }
  ],
  "total": 1234,
  "stats": {
    "info": 1000,
    "warning": 200,
    "error": 30,
    "critical": 4
  }
}
```

### DELETE /api/logs

Очистити логи.

**Відповідь:**
```json
{
  "status": "cleared"
}
```

---

## Користувачі (Admin)

### GET /api/users

Отримати список користувачів.

**Відповідь:**
```json
{
  "users": [
    {
      "username": "admin",
      "role": "admin",
      "allowed_tabs": ["status", "control", "xy", "settings", "admin", "logs"]
    }
  ]
}
```

### POST /api/users

Створити користувача.

**Тіло запиту:**
```json
{
  "username": "operator",
  "password": "secret123",
  "role": "user",
  "allowed_tabs": ["status", "control"]
}
```

### PUT /api/users/{username}

Оновити користувача.

### DELETE /api/users/{username}

Видалити користувача.

---

## Автентифікація

### POST /api/login

Вхід.

**Тіло запиту:**
```json
{
  "username": "admin",
  "password": "password"
}
```

**Відповідь:**
```json
{
  "status": "ok",
  "user": {
    "username": "admin",
    "role": "admin"
  }
}
```

### POST /api/logout

Вихід.

**Відповідь:**
```json
{
  "status": "logged_out"
}
```

### GET /api/me

Поточний користувач.

**Відповідь:**
```json
{
  "username": "admin",
  "role": "admin",
  "allowed_tabs": ["status", "control", "xy", "settings", "admin", "logs"]
}
```

---

## Коди помилок

| Код | Опис |
|-----|------|
| 200 | Успішно |
| 400 | Невірний запит |
| 401 | Не автентифіковано |
| 403 | Доступ заборонено |
| 404 | Не знайдено |
| 500 | Внутрішня помилка |

### Формат помилки

```json
{
  "error": "Device not found",
  "code": "DEVICE_NOT_FOUND",
  "details": "Device with key 'XYZ' does not exist"
}
```
