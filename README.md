# 3SD-SF-ScrewFeed

Автоматизована система для закручування гвинтів з XY-координатним столом на базі двох Raspberry Pi 5.

## Огляд

Система складається з двох Raspberry Pi 5, які працюють у конфігурації Master-Slave:

| Компонент | Опис |
|-----------|------|
| **Master Pi** | Основний контролер: керування циклом, реле, датчики, REST API, Web UI, Desktop UI |
| **Slave Pi** | Контролер XY столу: крокові мотори через GPIO (`xy_cli.py`) |
| **Зв'язок** | UART Serial (`/dev/ttyAMA0`, 115200 baud) |

### Основні можливості

- Автоматичне позиціонування за XY координатами (220x500 мм)
- Послідовне закручування гвинтів за програмою
- Контроль моменту закручування
- Захисні блокування (світлова завіса, аварійна кнопка E-STOP)
- Веб-інтерфейс (6 вкладок) та десктоп-інтерфейс (PyQt5 EGLFS)
- Налаштування девайсів (програм закручування) через UI
- USB-камера з відеозаписом та MJPEG стрімінгом
- Сканер штрих-кодів для ідентифікації деталей
- Автентифікація з ролями (admin, operator)

## Стек технологій

- **Python 3.10+** — основна мова
- **Flask** — REST API сервер (50+ ендпоінтів)
- **PyQt5** — десктоп сенсорний інтерфейс (EGLFS)
- **lgpio** — GPIO на Raspberry Pi 5
- **pyserial** — UART комунікація Master ↔ Slave
- **OpenCV** — USB камера (опціонально)
- **HTML/CSS/JS** — веб-інтерфейс (Vanilla JS, Dark Theme)

## Швидкий старт

### Master Raspberry Pi 5

```bash
cd /home/user/3SD-SF-ScrewFeed/screwdrive
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py --api-only --port 5000
```

### Slave Raspberry Pi 5 (XY стіл)

```bash
cd /home/user/3SD-SF-ScrewFeed
pip install lgpio pyserial
python3 xy_cli.py --serial /dev/ttyAMA0 --baud 115200
```

### Доступ до веб-інтерфейсу

```
http://<master-pi-ip>:5000
```

## Архітектура

```
┌────────────────────────────────────────────────────────────────┐
│                      MASTER RASPBERRY PI 5                      │
├────────────────────────────────────────────────────────────────┤
│  main.py                                                        │
│  ├─ GPIOController (lgpio)       - керування GPIO              │
│  ├─ RelayController              - 10 реле                     │
│  ├─ SensorController             - 9 датчиків                  │
│  ├─ XYTableController            - зв'язок з XY столом         │
│  ├─ CycleStateMachine            - логіка циклу                │
│  ├─ USBCamera                    - відеозапис та стрімінг      │
│  ├─ BarcodeScanner               - сканування штрих-кодів      │
│  ├─ USBStorage                   - зовнішній USB-накопичувач   │
│  └─ Flask API Server             - REST API + Web UI           │
│                                                                 │
│  UI:                                                            │
│  ├─ Web UI (index.html, app.js)  - браузерний інтерфейс       │
│  └─ Desktop UI (touchdesk.py)    - PyQt5 EGLFS інтерфейс      │
└──────────────────────────┬─────────────────────────────────────┘
                           │ UART Serial (/dev/ttyAMA0, 115200)
                           ▼
┌────────────────────────────────────────────────────────────────┐
│                      SLAVE RASPBERRY PI 5                       │
├────────────────────────────────────────────────────────────────┤
│  xy_cli.py --serial /dev/ttyAMA0                                │
│  ├─ GPIO (lgpio)                 - пряме керування GPIO        │
│  ├─ Stepper Motors (X/Y)         - крокові мотори              │
│  ├─ Endstops (X_MIN, Y_MIN)     - кінцевики                   │
│  └─ E-STOP button                - апаратна аварійна кнопка    │
└────────────────────────────────────────────────────────────────┘
```

## Структура проекту

```
3SD-SF-ScrewFeed/
├── README.md                  # Цей файл
├── xy_cli.py                  # Контролер XY столу (Slave Pi)
├── doc/                       # Документація (12 документів)
├── scripts/                   # Утилітарні скрипти
└── screwdrive/                # Основний пакет (Master Pi)
    ├── main.py                # Точка входу
    ├── core/                  # Ядро: GPIO, реле, датчики, XY, камера, сканер
    ├── api/                   # Flask REST API + автентифікація
    ├── ui/                    # PyQt5 Desktop UI
    ├── templates/             # HTML шаблони Web UI
    ├── static/                # CSS, JS
    ├── config/                # YAML конфігурація
    ├── services/              # Systemd service файли
    └── resources/             # Splash screen, KMS config
```

## GPIO (Master Pi)

### Реле (виходи) — Active LOW

| Реле | GPIO | Функція |
|------|------|---------|
| R01 | 5 | Подавач гвинтів (імпульс 200мс) |
| R02 | 6 | Гальмо мотора X |
| R03 | 13 | Гальмо мотора Y |
| R04 | 16 | Циліндр (ON=вниз, OFF=вгору) |
| R05 | 19 | Вільне обертання шуруповерта |
| R06 | 20 | Режим моменту |
| R07 | 21 | Вибір задачі 0 (імпульс 700мс) |
| R08 | 26 | Вибір задачі 1 (імпульс 700мс) |
| R09 | 4 | Живлення драйвера X (інвертовано) |
| R10 | 24 | Живлення драйвера Y (інвертовано) |

### Датчики (входи)

| Датчик | GPIO | Active | Функція |
|--------|------|--------|---------|
| alarm_x | 2 | LOW | Аларм драйвера X |
| alarm_y | 3 | LOW | Аларм драйвера Y |
| area_sensor | 17 | LOW | Світлова завіса |
| ped_start | 18 | LOW | Педаль старту |
| ger_c2_up | 22 | LOW | Циліндр вгорі |
| ger_c2_down | 23 | LOW | Циліндр внизу |
| ind_scrw | 12 | LOW | Датчик гвинта |
| do2_ok | 25 | LOW | Момент досягнуто |
| emergency_stop | 27 | **HIGH** | E-STOP кнопка |

## GPIO (Slave Pi — XY стіл)

| Сигнал | GPIO | Опис |
|--------|------|------|
| X_STEP | 9 | Крок X |
| X_DIR | 10 | Напрямок X |
| X_ENA | 11 | Enable X |
| X_MIN | 2 | Кінцевик X |
| Y_STEP | 21 | Крок Y |
| Y_DIR | 7 | Напрямок Y |
| Y_ENA | 8 | Enable Y |
| Y_MIN | 3 | Кінцевик Y |
| ESTOP | 13 | E-STOP кнопка |

## Команди XY столу (xy_cli.py)

| Команда | Опис |
|---------|------|
| `PING` | Тест з'єднання → `PONG` |
| `G28` | Хомінг обох осей |
| `G X<mm> Y<mm> F<mm/min>` | Абсолютне переміщення |
| `DX ±<mm> F<mm/min>` | Джог по X |
| `DY ±<mm> F<mm/min>` | Джог по Y |
| `M17` / `M18` | Enable / Disable моторів |
| `M112` | Аварійна зупинка (E-STOP) |
| `M999` | Скинути E-STOP |
| `M114` | Повний статус |

## Документація

Повна документація в папці [`doc/`](doc/README.md):

- [Архітектура](doc/ARCHITECTURE.md)
- [Апаратне забезпечення](doc/HARDWARE.md)
- [XY стіл](doc/XY_TABLE.md)
- [Шуруповерт](doc/SCREWDRIVER.md)
- [Інтерфейси](doc/USER_INTERFACE.md)
- [User Flow](doc/USER_FLOW.md)
- [Робочий цикл](doc/WORK_CYCLE.md)
- [REST API](doc/API.md)
- [Конфігурація](doc/CONFIGURATION.md)
- [Встановлення](doc/INSTALLATION.md)
- [Оновлення на сервері](doc/DEPLOY_PULL_GUIDE.md)
- [Splash Screen](doc/SPLASH_SETUP_UA.md)

## Безпека

- **E-STOP** — негайне зупинення всіх операцій (GPIO 27 Master, GPIO 13 Slave)
- **Світлова завіса** — зупинка при перетині робочої зони
- **Аларми драйверів** — автоматичний power cycle при збої
- **Таймаути** — захист від зависання всіх операцій
- **Моніторинг E-STOP** — безперервна перевірка кожні 50мс

## Ліцензія

Проект 3SD-SF-ScrewFeed для автоматизації процесу закручування гвинтів.
