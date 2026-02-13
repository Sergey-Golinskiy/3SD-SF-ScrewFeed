# 3SD-SF-ScrewFeed: Анализ проекта и инструкция по обновлению на сервере

## Часть 1: Анализ проекта

### Что это

**3SD-SF-ScrewFeed** — промышленная система автоматической закрутки винтов на базе двух Raspberry Pi 5 в архитектуре Master-Slave. Система автоматически позиционирует детали на XY-столе и закручивает винты с контролем крутящего момента.

Целевое применение — автоматизация сборки устройств Notifi Group (Motion Cam, Motion Protect и др.).

---

### Архитектура системы

```
┌──────────────────────────────────────────────────────────┐
│                  MASTER RASPBERRY PI 5                    │
│                                                          │
│  main.py (точка входа)                                   │
│  ├── GPIOController (lgpio)    → 10 реле + 9 датчиков   │
│  ├── RelayController           → управление актуаторами  │
│  ├── SensorController          → чтение сигналов         │
│  ├── XYTableController         → UART к Slave Pi         │
│  ├── CycleStateMachine         → автоматика цикла        │
│  ├── USBCamera                 → MJPEG видеопоток        │
│  ├── BarcodeScanner            → считывание штрих-кодов  │
│  ├── USBStorage                → внешние накопители      │
│  └── Flask API Server          → REST API + Web UI       │
└──────────────────┬───────────────────────────────────────┘
                   │ UART Serial (/dev/ttyAMA0, 115200 baud)
                   ▼
┌──────────────────────────────────────────────────────────┐
│                  SLAVE RASPBERRY PI 5                     │
│                                                          │
│  xy_cli.py (контроллер XY-стола)                         │
│  ├── X-axis Stepper  → STEP/DIR/ENA                     │
│  ├── Y-axis Stepper  → STEP/DIR/ENA                     │
│  ├── Endstops        → концевики X_MIN, Y_MIN           │
│  ├── E-STOP          → аварийная остановка (GPIO 13)     │
│  └── Serial Parser   → обработка команд                  │
└──────────────────────────────────────────────────────────┘
```

---

### Технологический стек

| Категория | Технология | Назначение |
|-----------|-----------|-----------|
| **Язык** | Python 3.10+ | Основной язык |
| **Web-фреймворк** | Flask + Flask-CORS | REST API (50+ эндпоинтов) |
| **GPIO** | lgpio | Управление GPIO Raspberry Pi 5 |
| **Serial** | pyserial | UART-связь Master ↔ Slave |
| **Конфигурация** | PyYAML | Парсинг YAML-файлов |
| **Аутентификация** | bcrypt | Хэширование паролей |
| **Desktop UI** | PyQt5 (EGLFS) | Интерфейс для тачскрина |
| **Web UI** | HTML5 + CSS3 + Vanilla JS | Браузерный интерфейс |
| **Камера** | OpenCV (опционально) | MJPEG-стриминг, запись видео |
| **Деплой** | systemd | Управление сервисами |
| **Хранение данных** | YAML/JSON файлы | Без базы данных |

---

### Структура файлов

```
3SD-SF-ScrewFeed/
├── README.md                        # Описание проекта
├── xy_cli.py                        # Slave Pi: контроллер XY-стола (~2000 строк)
├── doc/                             # Документация (13 файлов)
├── scripts/                         # Утилиты
│   └── setup_splash.sh
└── screwdrive/                      # Основное приложение (Master Pi)
    ├── main.py                      # Точка входа (346 строк)
    ├── requirements.txt             # Python-зависимости
    ├── install_services.sh          # Установщик systemd-сервисов
    ├── config/                      # Конфигурация
    │   ├── settings.yaml            # Настройки движения, таймингов, API
    │   ├── gpio_pins.yaml           # Карта GPIO (реле и датчики)
    │   ├── devices.yaml             # Программы устройств (10+ профилей)
    │   ├── auth.yaml                # Пользователи и роли
    │   ├── fixtures.yaml            # Фикстуры
    │   ├── global_cycles.txt        # Глобальный счетчик циклов
    │   └── cycle_history.json       # История выполнения
    ├── core/                        # Ядро системы
    │   ├── gpio_controller.py       # Абстракция GPIO
    │   ├── relays.py                # Управление 10 реле
    │   ├── sensors.py               # Мониторинг 9 датчиков
    │   ├── xy_table.py              # Связь с XY-столом (38KB)
    │   ├── state_machine.py         # Автоматика цикла (16KB)
    │   ├── camera.py                # USB-камера + MJPEG (29KB)
    │   ├── scanner.py               # Сканер штрих-кодов (11KB)
    │   └── usb_storage.py           # USB-накопители (10KB)
    ├── api/                         # REST API
    │   ├── server.py                # Flask API (2769 строк, 50+ эндпоинтов)
    │   ├── auth.py                  # Аутентификация
    │   └── logger.py                # Логирование
    ├── ui/                          # Desktop UI
    │   └── touchdesk.py             # PyQt5 EGLFS интерфейс
    ├── templates/                   # Web UI
    │   ├── index.html               # Главная страница (6 вкладок)
    │   └── login.html               # Страница входа
    ├── static/                      # Веб-ресурсы
    │   ├── css/style.css            # Темная тема
    │   └── js/app.js                # Клиентская логика (~3000 строк)
    └── services/                    # Systemd-сервисы
        ├── screwdrive-api.service
        ├── touchdesk.service
        └── splashscreen.service
```

---

### Основные возможности

**Автоматизация:**
- Автопозиционирование XY-стола (рабочая зона 220x500 мм)
- Последовательная закрутка винтов с контролем момента
- 10+ программ для разных устройств (Motion Cam, Motion Protect и др.)
- История циклов с видеозаписью

**Интерфейсы:**
- Web UI (6 вкладок: Status, Control, XY, Settings, Stats, Admin)
- Desktop UI (PyQt5 EGLFS для тачскрина)
- CLI-режим для тестирования
- REST API (50+ эндпоинтов)

**Безопасность:**
- Физическая кнопка E-STOP
- Световая завеса (area sensor)
- Контроль аларма моторов
- Таймауты на все операции
- Аутентификация с ролями (admin/user)

**Мониторинг:**
- Реалтайм-статус всех компонентов
- Камера с MJPEG-стримингом и записью
- Сканер штрих-кодов
- Журнал логов с фильтрацией

---

### Взаимосвязи модулей

```
Пользователь (браузер/тачскрин)
    │
    ▼
Flask REST API (server.py) ─────────────── Web UI (index.html + app.js)
    │                                       Desktop UI (touchdesk.py)
    ▼
CycleStateMachine (state_machine.py)
    ├── RelayController (relays.py) ────── 10 реле → пневматика, питание, режимы
    ├── SensorController (sensors.py) ──── 9 датчиков → безопасность, обратная связь
    ├── XYTableController (xy_table.py) ── UART → xy_cli.py (Slave Pi)
    ├── USBCamera (camera.py) ──────────── MJPEG стрим + запись видео
    ├── BarcodeScanner (scanner.py) ────── HID USB → идентификация деталей
    └── USBStorage (usb_storage.py) ────── Внешние накопители
```

**Конфигурация** (YAML-файлы) загружается в `main.py` и передается во все модули.

**Цикл закрутки** (state_machine):
```
IDLE → READY → HOMING → MOVING_FREE → MOVING_WORK → LOWERING
→ SCREWING → RAISING → VERIFYING → (следующий винт или COMPLETED)
```

---

### Последние изменения в репозитории

| Дата | Автор | Коммит | Описание |
|------|-------|--------|----------|
| 2026-02-12 | GOLINSKIY | `f31e612` | Merge PR #5: review-documentation |
| 2026-02-12 | Claude | `ae35b0a` | docs: review and improve documentation coverage |
| 2026-02-12 | GOLINSKIY | `77f16cc` | rm (удаление старых файлов) |
| 2026-02-12 | GOLINSKIY | `b01df52` | last |
| 2026-01-27 | GOLINSKIY | `ece63e3` | Initial commit |

**Что изменилось в последнем PR (#5):**
- Удалена папка `OLD/` с устаревшими файлами (cycle_onefile.py, touchdesk.py, web_ui.py и др.)
- Удалены файлы `barcode_reader_timeout.py` и `old_main.ino`
- Обновлен `README.md` (переработано описание проекта)
- Добавлены новые документы: `CONFIGURATION.md`, `USER_FLOW.md`
- Обновлен `ARCHITECTURE.md`
- Перенесены файлы в `doc/` (DEPLOY_PULL_GUIDE.md, SPLASH_SETUP_UA.md)
- Итого: +1322 / -4225 строк в 22 файлах

---

## Часть 2: Инструкция — как сделать pull на сервере (Raspberry Pi)

### Краткая справка

| Компонент | Описание |
|-----------|----------|
| **Master Pi** | Основной контроллер: Web UI, REST API, реле, датчики |
| **Slave Pi** | Контроллер XY-стола: шаговые двигатели через GPIO |
| **Связь** | UART Serial между Pi (/dev/ttyAMA0, 115200 baud) |

---

### 1. Первоначальная установка (если проект еще не клонирован)

#### На Master Pi

```bash
# Подключаемся к Master Pi по SSH
ssh pi@<IP_MASTER_PI>

# Обновляем систему
sudo apt update && sudo apt upgrade -y

# Устанавливаем зависимости
sudo apt install -y python3-pip python3-venv python3-lgpio python3-pyqt5 git

# Клонируем репозиторий
cd /home/pi
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed

# Создаем виртуальное окружение и ставим зависимости
cd screwdrive
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Устанавливаем systemd-сервисы
sudo ./install_services.sh
sudo systemctl daemon-reload
sudo systemctl enable screwdrive-api
sudo systemctl start screwdrive-api
```

#### На Slave Pi (XY-стол)

```bash
# Подключаемся к Slave Pi по SSH
ssh pi@<IP_SLAVE_PI>

# Обновляем систему
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-lgpio git

# Клонируем репозиторий
cd /home/pi
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed

# Устанавливаем зависимости
pip install lgpio pyserial --break-system-packages

# Создаем systemd-сервис для XY-стола
sudo systemctl enable xy_table
sudo systemctl start xy_table
```

---

### 2. Обновление кода (Pull) на сервере

#### Вариант A: Быстрое обновление (рекомендуется)

Выполнить на **каждой Pi** (Master и Slave):

```bash
# Подключаемся по SSH
ssh pi@<IP_АДРЕС_PI>

# Переходим в директорию проекта
cd /home/pi/3SD-SF-ScrewFeed

# Останавливаем сервисы перед обновлением
sudo systemctl stop screwdrive-api    # только на Master Pi
sudo systemctl stop xy_table          # только на Slave Pi

# Получаем последние изменения
git fetch origin
git pull origin main

# Если нужно переключиться на конкретную ветку:
# git checkout <имя-ветки>
# git pull origin <имя-ветки>

# Обновляем зависимости (на Master Pi)
cd screwdrive
source venv/bin/activate
pip install -r requirements.txt
deactivate
cd ..

# Перезапускаем сервисы
sudo systemctl start screwdrive-api   # только на Master Pi
sudo systemctl start xy_table         # только на Slave Pi

# Проверяем статус
sudo systemctl status screwdrive-api  # на Master Pi
sudo systemctl status xy_table        # на Slave Pi
```

#### Вариант B: Обновление конкретной ветки

```bash
cd /home/pi/3SD-SF-ScrewFeed

# Останавливаем сервисы
sudo systemctl stop screwdrive-api

# Получаем конкретную ветку
git fetch origin <имя-ветки>
git checkout <имя-ветки>
git pull origin <имя-ветки>

# Обновляем зависимости и перезапускаем
cd screwdrive && source venv/bin/activate && pip install -r requirements.txt && deactivate && cd ..
sudo systemctl restart screwdrive-api
```

#### Вариант C: Полная переустановка (при серьезных проблемах)

```bash
cd /home/pi

# Бэкап конфигурации
cp -r 3SD-SF-ScrewFeed/screwdrive/config/ ~/config_backup_$(date +%Y%m%d)

# Останавливаем сервисы
sudo systemctl stop screwdrive-api
sudo systemctl stop xy_table

# Удаляем старую версию и клонируем заново
rm -rf 3SD-SF-ScrewFeed
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed

# Восстанавливаем конфигурацию
cp -r ~/config_backup_*/* screwdrive/config/

# Устанавливаем зависимости
cd screwdrive
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Перезапускаем
sudo systemctl start screwdrive-api
sudo systemctl start xy_table
```

---

### 3. Скрипт автоматического обновления

Создайте файл `/home/pi/update_screwfeed.sh`:

```bash
#!/bin/bash
# Скрипт обновления 3SD-SF-ScrewFeed
# Использование: ./update_screwfeed.sh [ветка]

set -e

BRANCH="${1:-main}"
PROJECT_DIR="/home/pi/3SD-SF-ScrewFeed"
SERVICE_MASTER="screwdrive-api"
SERVICE_SLAVE="xy_table"

echo "=== Обновление 3SD-SF-ScrewFeed ==="
echo "Ветка: $BRANCH"

# Определяем тип Pi (Master или Slave)
if systemctl list-unit-files | grep -q "$SERVICE_MASTER"; then
    SERVICE="$SERVICE_MASTER"
    IS_MASTER=true
    echo "Тип: Master Pi"
elif systemctl list-unit-files | grep -q "$SERVICE_SLAVE"; then
    SERVICE="$SERVICE_SLAVE"
    IS_MASTER=false
    echo "Тип: Slave Pi"
else
    echo "ВНИМАНИЕ: Сервис не найден, продолжаем без перезапуска"
    SERVICE=""
    IS_MASTER=false
fi

# Останавливаем сервис
if [ -n "$SERVICE" ]; then
    echo "Останавливаем $SERVICE..."
    sudo systemctl stop "$SERVICE"
fi

# Обновляем код
cd "$PROJECT_DIR"
echo "Получаем обновления..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"

# Обновляем зависимости на Master Pi
if [ "$IS_MASTER" = true ] && [ -f screwdrive/requirements.txt ]; then
    echo "Обновляем Python зависимости..."
    cd screwdrive
    source venv/bin/activate
    pip install -r requirements.txt --quiet
    deactivate
    cd ..
fi

# Запускаем сервис
if [ -n "$SERVICE" ]; then
    echo "Запускаем $SERVICE..."
    sudo systemctl start "$SERVICE"
    echo "Статус:"
    sudo systemctl status "$SERVICE" --no-pager -l
fi

echo "=== Обновление завершено ==="
```

Установка скрипта:

```bash
chmod +x /home/pi/update_screwfeed.sh

# Использование:
./update_screwfeed.sh              # обновить ветку main
./update_screwfeed.sh develop      # обновить конкретную ветку
```

---

### 4. Проверка после обновления

#### На Master Pi

```bash
# Проверяем статус сервиса
sudo systemctl status screwdrive-api

# Проверяем API
curl http://localhost:5000/api/health

# Проверяем связь с XY-столом
curl http://localhost:5000/api/xy/status

# Просмотр логов в реальном времени
sudo journalctl -u screwdrive-api -f
```

#### На Slave Pi

```bash
# Проверяем статус
sudo systemctl status xy_table

# Просмотр логов
sudo journalctl -u xy_table -f
```

#### Через Web UI

Откройте в браузере: `http://<IP_MASTER_PI>:5000`

- Вкладка **Status** — проверить подключение всех компонентов
- Вкладка **XY Table** — проверить связь со столом (PING/PONG)
- Вкладка **Logs** — посмотреть логи системы

---

### 5. Откат к предыдущей версии

Если после обновления что-то сломалось:

```bash
cd /home/pi/3SD-SF-ScrewFeed

# Останавливаем сервис
sudo systemctl stop screwdrive-api

# Смотрим историю коммитов
git log --oneline -20

# Откатываемся к нужному коммиту
git checkout <хэш-коммита>

# Перезапускаем
sudo systemctl start screwdrive-api
```

---

### 6. Частые проблемы при pull

#### Конфликт локальных изменений

```bash
# Если есть локальные изменения, которые мешают pull:
git stash                  # сохранить изменения
git pull origin main       # обновиться
git stash pop              # вернуть изменения

# Или если локальные изменения не нужны:
git checkout -- .          # отменить все локальные изменения
git pull origin main
```

#### Ошибка "Permission denied"

```bash
# Проверьте права на директорию
ls -la /home/pi/3SD-SF-ScrewFeed/

# Исправьте владельца если нужно
sudo chown -R pi:pi /home/pi/3SD-SF-ScrewFeed/
```

#### Ошибка "Could not resolve host"

```bash
# Проверьте интернет-соединение
ping -c 3 github.com

# Проверьте DNS
cat /etc/resolv.conf

# Если нет интернета — скопируйте файлы вручную:
# На локальном компьютере:
scp -r ./3SD-SF-ScrewFeed/ pi@<IP_PI>:/home/pi/
```

---

### 7. Структура сервисов

| Сервис | Pi | Файл | Описание |
|--------|----|-------|----------|
| `screwdrive-api` | Master | `screwdrive/services/screwdrive-api.service` | API сервер + Web UI |
| `touchdesk` | Master | `screwdrive/services/touchdesk.service` | Desktop UI (тачскрин) |
| `splashscreen` | Master | `screwdrive/services/splashscreen.service` | Заставка при загрузке |
| `xy_table` | Slave | создается вручную (см. раздел 1) | Контроллер XY-стола |

#### Управление сервисами

```bash
sudo systemctl start <имя>     # запустить
sudo systemctl stop <имя>      # остановить
sudo systemctl restart <имя>   # перезапустить
sudo systemctl status <имя>    # статус
sudo systemctl enable <имя>    # автозапуск при загрузке
sudo systemctl disable <имя>   # убрать автозапуск
sudo journalctl -u <имя> -f    # логи в реальном времени
```
