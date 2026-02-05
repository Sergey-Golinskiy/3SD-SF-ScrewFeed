# Архітектура системи 3SD-SF-ScrewFeed

## Структура проекту

```
3SD-SF-ScrewFeed/
├── README.md                           # Опис проекту
├── xy_cli.py                          # Контролер XY столу (Slave Pi)
├── old_main.ino                       # Оригінальна Arduino версія (довідка)
│
├── doc/                               # Документація
│   ├── README.md                      # Цей файл
│   ├── ARCHITECTURE.md                # Архітектура
│   ├── HARDWARE.md                    # Апаратне забезпечення
│   └── ...
│
├── screwdrive/                        # Основний пакет застосунку
│   ├── main.py                        # Точка входу (Master Pi)
│   ├── requirements.txt               # Python залежності
│   │
│   ├── config/                        # Конфігураційні файли
│   │   ├── settings.yaml              # Основні налаштування
│   │   ├── devices.yaml               # Програми девайсів
│   │   ├── gpio_pins.yaml             # Мапінг GPIO пінів
│   │   └── auth.yaml                  # Автентифікація
│   │
│   ├── core/                          # Ядро системи
│   │   ├── __init__.py                # Експорт класів
│   │   ├── gpio_controller.py         # GPIO абстракція (lgpio)
│   │   ├── relays.py                  # Керування реле
│   │   ├── sensors.py                 # Зчитування датчиків
│   │   ├── xy_table.py                # Комунікація з XY столом
│   │   └── state_machine.py           # Логіка автоматизації
│   │
│   ├── api/                           # REST API
│   │   ├── __init__.py
│   │   ├── server.py                  # Flask сервер
│   │   ├── auth.py                    # Автентифікація
│   │   └── logger.py                  # Логування
│   │
│   ├── ui/                            # Користувацькі інтерфейси
│   │   └── touchdesk.py               # PyQt5 десктоп UI
│   │
│   ├── templates/                     # HTML шаблони
│   │   ├── index.html                 # Головна сторінка Web UI
│   │   └── login.html                 # Сторінка входу
│   │
│   ├── static/                        # Статичні файли Web UI
│   │   ├── css/style.css              # Стилі
│   │   └── js/app.js                  # JavaScript логіка
│   │
│   └── logs/                          # Директорія логів
│
└── OLD/                               # Архів старого коду
```

---

## Модулі ядра

### 1. GPIOController (`core/gpio_controller.py`)

**Призначення:** Уніфікована абстракція GPIO через lgpio

**Можливості:**
- Цифровий ввід/вивід
- Налаштування pull-up/pull-down
- Інверсія логіки active high/low
- Потокобезпечні операції

**Ключові методи:**
```python
init()                      # Ініціалізація GPIO чіпу
close()                     # Звільнення ресурсів
setup_input(pin, pull_up)   # Налаштування входу
setup_output(pin)           # Налаштування виходу
read(pin)                   # Зчитування стану
write(pin, value)           # Запис значення
```

### 2. RelayController (`core/relays.py`)

**Призначення:** Керування 10 реле (шуруповерт, пневматика, гальма)

**Реле системи:**
| Реле | GPIO | Функція |
|------|------|---------|
| r01_pit | 5 | Подавач гвинтів (імпульс 200мс) |
| r02_brake_x | 6 | Гальмо мотора X |
| r03_brake_y | 13 | Гальмо мотора Y |
| r04_c2 | 16 | Циліндр (ON=вниз) |
| r05_di4_free | 19 | Вільне обертання |
| r06_di1_pot | 20 | Режим моменту |
| r07_di5_tsk0 | 21 | Вибір задачі 0 |
| r08_di6_tsk1 | 26 | Вибір задачі 1 |
| r09_pwr_x | 4 | Живлення мотора X (інвертовано) |
| r10_pwr_y | 24 | Живлення мотора Y (інвертовано) |

**Всі реле: active LOW** (GPIO LOW = ON, GPIO HIGH = OFF)

**Ключові методи:**
```python
set(name, state)            # Встановити ON/OFF
pulse(name, duration)       # Імпульс заданої тривалості
get_all_states()            # Отримати стани всіх реле
cylinder_down()             # Опустити циліндр
cylinder_up()               # Підняти циліндр
feed_screw()                # Подати гвинт
select_task(task)           # Вибрати задачу (0-3)
```

### 3. SensorController (`core/sensors.py`)

**Призначення:** Зчитування датчиків з дебаунсингом та моніторингом

**Датчики системи:**
| Датчик | GPIO | Active | Функція |
|--------|------|--------|---------|
| alarm_x | 2 | LOW | Аларм драйвера X |
| alarm_y | 3 | LOW | Аларм драйвера Y |
| area_sensor | 17 | LOW | Світлова завіса |
| ped_start | 18 | LOW | Педаль старту |
| ger_c2_up | 22 | LOW | Циліндр вгорі |
| ger_c2_down | 23 | LOW | Циліндр внизу (аварія!) |
| ind_scrw | 12 | LOW | Датчик гвинта |
| do2_ok | 25 | LOW | Момент досягнуто |
| emergency_stop | 27 | **HIGH** | Аварійна кнопка |

**Ключові методи:**
```python
read(name)                  # Отримати поточний стан
get_all_states()            # Всі датчики
start_monitoring()          # Запустити фоновий потік
on_change(name, callback)   # Колбек на зміну стану
is_safe()                   # Перевірка безпеки
is_torque_reached()         # Момент досягнуто?
is_screw_detected()         # Гвинт подано?
```

### 4. XYTableController (`core/xy_table.py`)

**Призначення:** Комунікація з XY координатним столом

**Режими роботи:**
- **SERIAL** - команди через UART до slave Pi з xy_cli.py
- **DIRECT** - пряме GPIO керування (на майбутнє)

**Стани:**
```
DISCONNECTED → CONNECTING → READY
                    ↓
               ERROR / ESTOP
```

**Ключові команди:**
| Команда | Опис |
|---------|------|
| PING | Тест з'єднання |
| G28 | Хомінг всіх осей |
| G X Y F | Абсолютне переміщення |
| M17 / M18 | Увімкнути/вимкнути мотори |
| M112 | Аварійна зупинка |
| M999 | Скинути E-STOP |
| M114 | Запит статусу |

**Ключові методи:**
```python
connect()                   # Встановити з'єднання
disconnect()                # Закрити з'єднання
home()                      # Хомінг осей
move_to(x, y, feed)         # Абсолютне переміщення
move_relative(dx, dy, feed) # Відносне переміщення
estop()                     # Аварійна зупинка
clear_estop()               # Скинути E-STOP
ping()                      # Тест з'єднання
```

### 5. CycleStateMachine (`core/state_machine.py`)

**Призначення:** Логіка автоматизації циклу закручування

**Стани:**
```
IDLE → READY → HOMING → MOVING_FREE → MOVING_WORK → LOWERING
                ↑                                        ↓
                ← SCREWING ← RAISING ← VERIFYING ←──────┘
                ↓
            COMPLETED/ERROR/ESTOP
```

**Ключові класи:**
- `CycleState` - перелік станів
- `CycleError` - коди помилок
- `ProgramStep` - один крок програми
- `DeviceProgram` - повна програма девайсу
- `CycleStatus` - поточний статус
- `CycleStateMachine` - головна машина станів

---

## API сервер (`api/server.py`)

**Технологія:** Flask REST API

**Основні ендпоінти:**
| Метод | Шлях | Опис |
|-------|------|------|
| GET | /api/health | Перевірка здоров'я |
| GET | /api/status | Повний статус системи |
| GET/POST | /api/relays | Керування реле |
| GET | /api/sensors | Стан датчиків |
| GET | /api/xy/status | Статус XY столу |
| POST | /api/xy/move | Переміщення |
| POST | /api/xy/home | Хомінг |
| GET/POST | /api/devices | Керування девайсами |
| POST | /api/cycle/start | Запуск циклу |
| POST | /api/cycle/stop | Зупинка циклу |

**Особливості:**
- CORS увімкнено
- Автентифікація користувачів
- Фоновий моніторинг E-STOP
- Система логування

---

## Користувацькі інтерфейси

### Web UI (`templates/index.html`, `static/js/app.js`)

**Технології:** HTML5, JavaScript (Vanilla), CSS3 Dark Theme

**Вкладки:**
1. **Статус** - моніторинг в реальному часі
2. **Керування** - керування циклом
3. **XY Стіл** - позиціонування та джогінг
4. **Налаштування** - редактор девайсів
5. **Адмін** - керування користувачами
6. **Логи** - перегляд логів

### Desktop UI (`ui/touchdesk.py`)

**Технологія:** PyQt5

**Режими:**
1. **START** - вибір девайсу та ініціалізація
2. **WORK** - виконання циклу закручування

**Особливості:**
- Повноекранний режим
- Сумісність з EGLFS (без X11)
- Сенсорний інтерфейс
- Синхронізація з Web UI

---

## Потоки даних

### Ініціалізація
```
User → UI → API → StateMachine → XYTable → Slave Pi
                              → Relays
                              → Sensors
```

### Цикл закручування
```
User → UI → API → StateMachine
                       ↓
                  XYTable.move()
                       ↓
                  Relays.cylinder_down()
                       ↓
                  Relays.torque_mode()
                       ↓
                  Sensors.wait_torque()
                       ↓
                  Relays.cylinder_up()
```

### E-STOP
```
Physical Button → GPIO → trigger_estop()
                              ↓
                  disable_motors()
                  invalidate_homing()
                  notify_master()
```

---

## Конфігураційні файли

### settings.yaml
```yaml
motion:
  steps_per_mm_x: 40.0
  steps_per_mm_y: 40.0
  x_max_mm: 220.0
  y_max_mm: 500.0
  max_feed_mm_s: 600.0

timing:
  torque_timeout_s: 15.0
  cylinder_down_timeout_s: 3.0
  cylinder_up_timeout_s: 3.0

xy_table:
  mode: "serial"
  serial_port: "/dev/ttyAMA0"
  serial_baud: 115200
```

### devices.yaml
```yaml
devices:
  - key: "MCO_4holes"
    name: "MCO_Back_M3x10"
    holes: 4
    task: "0"
    torque: 0.8
    work_x: 50
    work_y: 100
    program:
      - { type: "free", x: 5, y: 108.5, f: 60000 }
      - { type: "work", x: 29.5, y: 108.5, f: 60000 }
```

---

## Python залежності

```
flask>=2.0.0              # Веб-сервер
flask-cors>=3.0.0         # CORS підтримка
lgpio>=0.2.0.0            # GPIO для Raspberry Pi 5
pyserial>=3.5             # Serial комунікація
pyyaml>=6.0               # YAML конфігурація
bcrypt>=4.0.0             # Хешування паролів
PyQt5>=5.15.0             # Desktop UI (опціонально)
```
