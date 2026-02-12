# Инструкция: как сделать pull на сервере (Raspberry Pi)

## Описание проекта

**3SD-SF-ScrewFeed** — система автоматической закрутки винтов на базе двух Raspberry Pi 5.

| Компонент | Описание |
|-----------|----------|
| **Master Pi** | Основной контроллер: Web UI, REST API, управление реле и датчиками |
| **Slave Pi** | Контроллер XY-стола: управление шаговыми двигателями через GPIO |
| **Связь** | UART (Serial) между двумя Pi |

**Стек**: Python 3.10+, Flask, lgpio, PyQt5, HTML/JS/CSS

---

## 1. Первоначальная установка (если проект еще не клонирован)

### На Master Pi

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

### На Slave Pi (XY-стол)

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

## 2. Обновление кода (Pull) на сервере

### Вариант A: Быстрое обновление (рекомендуется)

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

### Вариант B: Обновление конкретной ветки

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

### Вариант C: Полная переустановка (при серьезных проблемах)

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

## 3. Автоматизация обновления (скрипт)

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

## 4. Проверка после обновления

### На Master Pi

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

### На Slave Pi

```bash
# Проверяем статус
sudo systemctl status xy_table

# Просмотр логов
sudo journalctl -u xy_table -f
```

### Через Web UI

Откройте в браузере: `http://<IP_MASTER_PI>:5000`

- Вкладка **Status** — проверить подключение всех компонентов
- Вкладка **XY Table** — проверить связь со столом (PING/PONG)
- Вкладка **Logs** — посмотреть логи системы

---

## 5. Откат к предыдущей версии

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

## 6. Частые проблемы при pull

### Конфликт локальных изменений

```bash
# Если есть локальные изменения, которые мешают pull:
git stash                  # сохранить изменения
git pull origin main       # обновиться
git stash pop              # вернуть изменения

# Или если локальные изменения не нужны:
git checkout -- .          # отменить все локальные изменения
git pull origin main
```

### Ошибка "Permission denied"

```bash
# Проверьте права на директорию
ls -la /home/pi/3SD-SF-ScrewFeed/

# Исправьте владельца если нужно
sudo chown -R pi:pi /home/pi/3SD-SF-ScrewFeed/
```

### Ошибка "Could not resolve host"

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

## 7. Структура сервисов

| Сервис | Pi | Файл | Описание |
|--------|----|-------|----------|
| `screwdrive-api` | Master | `screwdrive/services/screwdrive-api.service` | API сервер + Web UI |
| `touchdesk` | Master | `screwdrive/services/touchdesk.service` | Desktop UI (сенсорный экран) |
| `splashscreen` | Master | `screwdrive/services/splashscreen.service` | Заставка при загрузке |
| `xy_table` | Slave | создается вручную (см. раздел 1) | Контроллер XY-стола |

### Управление сервисами

```bash
sudo systemctl start <имя>     # запустить
sudo systemctl stop <имя>      # остановить
sudo systemctl restart <имя>   # перезапустить
sudo systemctl status <имя>    # статус
sudo systemctl enable <имя>    # автозапуск при загрузке
sudo systemctl disable <имя>   # убрать автозапуск
sudo journalctl -u <имя> -f    # логи в реальном времени
```
