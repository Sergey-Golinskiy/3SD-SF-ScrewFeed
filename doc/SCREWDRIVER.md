# Система керування шуруповертом

## Огляд

Система керування шуруповертом включає:
- Пневматичний циліндр (опускання/підняття)
- Контроль моменту закручування
- Автоматичну подачу гвинтів
- Вибір задачі (програми шуруповерта)

---

## Реле керування

### Карта реле

| Реле | GPIO | Назва | Функція | Режим |
|------|------|-------|---------|-------|
| R01 | 5 | r01_pit | Подавач гвинтів | Імпульс 200мс |
| R04 | 16 | r04_c2 | Циліндр | ON=вниз, OFF=вгору |
| R05 | 19 | r05_di4_free | Вільне обертання | Утримувати або імпульс |
| R06 | 20 | r06_di1_pot | Режим моменту | Утримувати до DO2_OK |
| R07 | 21 | r07_di5_tsk0 | Вибір задачі біт 0 | Імпульс 700мс |
| R08 | 26 | r08_di6_tsk1 | Вибір задачі біт 1 | Імпульс 700мс |

**Всі реле: Active LOW** (GPIO LOW = реле ON)

---

## Датчики зворотного зв'язку

| Датчик | GPIO | Active | Функція |
|--------|------|--------|---------|
| ger_c2_up | 22 | LOW | Циліндр у верхній позиції |
| ger_c2_down | 23 | LOW | Циліндр внизу (НЕБЕЗПЕКА!) |
| ind_scrw | 12 | LOW | Гвинт виявлено |
| do2_ok | 25 | LOW | Момент досягнуто |

---

## Операції шуруповерта

### 1. Подача гвинта

**Реле:** R01 (r01_pit)
**Тривалість імпульсу:** 200мс

```python
def feed_screw():
    pulse("r01_pit", 0.2)  # 200мс імпульс
```

**Перевірка подачі:**
```python
# Очікуємо спрацювання датчика ind_scrw
for attempt in range(3):
    pulse("r01_pit", 0.2)
    if wait_for_sensor("ind_scrw", "ACTIVE", timeout=1.0):
        return True
return False  # Гвинт не подано після 3 спроб
```

### 2. Керування циліндром

**Реле:** R04 (r04_c2)

**Опускання:**
```python
def cylinder_down():
    set("r04_c2", "on")
    wait_for_sensor("ger_c2_down", "ACTIVE", timeout=3.0)
```

**Підняття:**
```python
def cylinder_up():
    set("r04_c2", "off")
    wait_for_sensor("ger_c2_up", "ACTIVE", timeout=3.0)
```

**ВАЖЛИВО:** Якщо ger_c2_down спрацювало несподівано - негайно вимкнути R04!

### 3. Режим вільного обертання

**Реле:** R05 (r05_di4_free)

```python
# Запуск вільного обертання
set("r05_di4_free", "on")

# Зупинка
set("r05_di4_free", "off")

# Короткий імпульс для зупинки шпинделя
pulse("r05_di4_free", 0.2)
```

### 4. Режим моменту

**Реле:** R06 (r06_di1_pot)
**Датчик:** do2_ok

```python
def torque_cycle():
    set("r06_di1_pot", "on")     # Увімкнути режим моменту

    # Очікуємо досягнення моменту
    if wait_for_sensor("do2_ok", "ACTIVE", timeout=2.0):
        set("r06_di1_pot", "off")  # Вимкнути при досягненні
        return True

    # Таймаут - момент не досягнуто
    set("r06_di1_pot", "off")
    return False
```

### 5. Вибір задачі

**Реле:** R07, R08
**Тривалість імпульсу:** 700мс

| Task | R07 | R08 |
|------|-----|-----|
| 0 | OFF | OFF |
| 1 | ON (700мс) | OFF |
| 2 | OFF | ON (700мс) |
| 3 | ON (700мс) | ON (700мс) |

```python
def select_task(task: int):
    if task & 0x01:  # Біт 0
        pulse("r07_di5_tsk0", 0.7)
    if task & 0x02:  # Біт 1
        pulse("r08_di6_tsk1", 0.7)
```

---

## Повний цикл закручування

### Послідовність операцій

```
1. Подача гвинта (R01 імпульс 200мс)
   └─ Перевірка: ind_scrw → ACTIVE
   └─ Retry: до 3 спроб

2. Увімкнення моменту (R06 ON)

3. Опускання циліндра (R04 ON)
   └─ Очікування: ger_c2_down → ACTIVE (3с)

4. Закручування
   └─ Очікування: do2_ok → ACTIVE (2с)
   └─ Якщо таймаут → TORQUE_NOT_REACHED

5. Вимкнення моменту (R06 OFF)

6. Підняття циліндра (R04 OFF)
   └─ Очікування: ger_c2_up → ACTIVE (5с)

7. Імпульс free run (R05 імпульс 200мс)
   └─ Зупинка шпинделя
```

### Таймлайн операції

```
Час (с)  0    1    2    3    4    5    6    7
         │    │    │    │    │    │    │    │
R01      ▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  (подача)
ind_scrw ░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  (гвинт є)
R06      ░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░  (момент)
R04      ░░░░░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░  (циліндр)
do2_ok   ░░░░░░░░░░░░░░░░░░░░▓▓▓▓░░░░░░░░░░░  (момент OK)
R05      ░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓░░░░░░  (free run)
```

---

## Обробка помилок

### TORQUE_NOT_REACHED

**Умова:** do2_ok не став ACTIVE за 2 секунди

**Обробка:**
```python
def handle_torque_error():
    # 1. Вимкнути режим моменту
    set("r06_di1_pot", "off")

    # 2. Підняти циліндр
    set("r04_c2", "off")
    wait_for_sensor("ger_c2_up", "ACTIVE", timeout=5.0)

    # 3. Free run для звільнення гвинта
    pulse("r05_di4_free", 0.2)

    # 4. Повернення до оператора
    move_to_operator_position()

    # 5. Стан = PAUSED
    state = "PAUSED"
    message = "Момент не досягнуто. Перевірте гвинт та натисніть СТАРТ."
```

### CYLINDER_TIMEOUT

**Умова:** Циліндр не досяг позиції за таймаут

**Обробка:**
```python
def handle_cylinder_error():
    # 1. Вимкнути циліндр
    set("r04_c2", "off")

    # 2. Аварійна зупинка шуруповерта
    set("r06_di1_pot", "off")
    pulse("r05_di4_free", 0.2)

    # 3. Стан = ERROR
    state = "ERROR"
    message = "Циліндр не досяг позиції"
```

### SCREW_NOT_DETECTED

**Умова:** ind_scrw не став ACTIVE після 3 спроб подачі

**Обробка:**
```python
def handle_screw_error():
    state = "ERROR"
    message = "Гвинт не виявлено після 3 спроб"
```

---

## Безпека

### Аварійні датчики

| Датчик | Стан | Дія |
|--------|------|-----|
| ger_c2_down | ACTIVE неочікувано | Негайно R04 OFF |
| area_sensor | ACTIVE | Зупинка циклу, вимкнення моторів |
| emergency_stop | ACTIVE | Повна зупинка всього |

### Послідовність аварійної зупинки

```python
def emergency_shutdown():
    # 1. Підняти циліндр
    set("r04_c2", "off")

    # 2. Вимкнути режим моменту
    set("r06_di1_pot", "off")

    # 3. Імпульс free run
    pulse("r05_di4_free", 0.3)

    # 4. Вимкнути мотори XY
    xy_disable_motors()

    # 5. Стан = ERROR або ESTOP
```

### Блокування

**Рух заборонено якщо:**
- E-STOP натиснуто
- Аларм драйвера активний
- Світлова завіса заблокована
- Циліндр у небезпечній позиції

---

## Налаштування контролера шуруповерта

### Інтерфейс DI (Digital Input)

| Вхід | Реле | Функція |
|------|------|---------|
| DI1 | R06 | Режим моменту |
| DI4 | R05 | Вільне обертання |
| DI5 | R07 | Task біт 0 |
| DI6 | R08 | Task біт 1 |

### Інтерфейс DO (Digital Output)

| Вихід | GPIO | Функція |
|-------|------|---------|
| DO2 | 25 | Момент досягнуто |

### Параметри задач (Tasks)

Кожна задача (0-3) може мати різні налаштування:
- Цільовий момент (Nm)
- Швидкість обертання (RPM)
- Профіль закручування
- Кількість обертів

Налаштування задач виконується на контролері шуруповерта.

---

## API методи

### RelayController

```python
# Подача гвинта
feed_screw()                      # Імпульс R01, 200мс

# Циліндр
cylinder_down()                   # R04 ON
cylinder_up()                     # R04 OFF
is_cylinder_down() -> bool        # Стан R04

# Шуруповерт
screwdriver_free_start()          # R05 ON
screwdriver_free_stop()           # R05 OFF
screwdriver_torque_start()        # R06 ON
screwdriver_torque_stop()         # R06 OFF

# Вибір задачі
select_task(task: int)            # Імпульс R07/R08, 700мс
```

### SensorController

```python
# Датчики циліндра
is_cylinder_up() -> bool          # ger_c2_up
is_cylinder_down() -> bool        # ger_c2_down

# Датчик гвинта
is_screw_detected() -> bool       # ind_scrw
is_screw_absent() -> bool

# Датчик моменту
is_torque_reached() -> bool       # do2_ok
is_torque_not_reached() -> bool

# Очікування
wait_for_active(sensor, timeout)
wait_for_inactive(sensor, timeout)
```

---

## Константи часу

```python
# Тривалості імпульсів
FEEDER_PULSE_DURATION = 0.2    # 200мс - подача гвинта
TASK_PULSE_DURATION = 0.7      # 700мс - вибір задачі
FREERUN_PULSE_DURATION = 0.2   # 200мс - зупинка шпинделя
ESTOP_CLEAR_PULSE = 0.3        # 300мс - скидання E-STOP

# Таймаути операцій
CYLINDER_DOWN_TIMEOUT = 3.0    # 3с - опускання циліндра
CYLINDER_UP_TIMEOUT = 5.0      # 5с - підняття циліндра
TORQUE_TIMEOUT = 2.0           # 2с - очікування моменту
SCREW_FEED_TIMEOUT = 1.0       # 1с - подача гвинта
MAX_SCREW_RETRIES = 3          # Максимум спроб подачі
```

---

## Діагностика

### Перевірка датчиків

```bash
curl http://localhost:5000/api/sensors

# Очікувана відповідь (в стані спокою):
{
  "ger_c2_up": "ACTIVE",      # Циліндр вгорі
  "ger_c2_down": "INACTIVE",  # Не внизу
  "ind_scrw": "INACTIVE",     # Гвинта немає
  "do2_ok": "INACTIVE"        # Момент не досягнуто
}
```

### Тестування реле

```bash
# Подача гвинта
curl -X POST http://localhost:5000/api/relays/r01_pit \
  -H "Content-Type: application/json" \
  -d '{"state":"pulse","duration":200}'

# Опускання циліндра
curl -X POST http://localhost:5000/api/relays/r04_c2 \
  -H "Content-Type: application/json" \
  -d '{"state":"on"}'

# Підняття циліндра
curl -X POST http://localhost:5000/api/relays/r04_c2 \
  -H "Content-Type: application/json" \
  -d '{"state":"off"}'
```

### Типові проблеми

| Проблема | Можлива причина | Рішення |
|----------|-----------------|---------|
| Гвинт не подається | Порожній магазин | Заповнити |
| Гвинт не виявляється | Несправний датчик | Перевірити ind_scrw |
| Момент не досягається | Неправильна задача | Перевірити task |
| Циліндр не опускається | Немає тиску повітря | Перевірити пневматику |
| Циліндр не піднімається | Заклинило | Ручне втручання |
