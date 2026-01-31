# Автозапуск при загрузке системы

Данное руководство описывает настройку автоматического запуска компонентов системы управления при загрузке Raspberry Pi 5.

## Содержание

1. [Обзор](#обзор)
2. [Автозапуск API сервера](#автозапуск-api-сервера)
3. [Автозапуск Desktop UI](#автозапуск-desktop-ui)
4. [Автозапуск Web UI](#автозапуск-web-ui)
5. [Управление сервисами](#управление-сервисами)
6. [Отладка](#отладка)

---

## Обзор

Система состоит из нескольких компонентов, которые можно запускать автоматически:

| Компонент | Описание | Сервис |
|-----------|----------|--------|
| API Server | REST API + Web UI | `screwdrive-api.service` |
| TouchDesk | PyQt5 Desktop UI | `screwdrive-touchdesk.service` |

---

## Автозапуск API сервера

### 1. Создание systemd сервиса

```bash
sudo nano /etc/systemd/system/screwdrive-api.service
```

Содержимое файла:

```ini
[Unit]
Description=Screw Drive Control API Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Переменные окружения
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### 2. Активация сервиса

```bash
# Перезагрузить конфигурацию systemd
sudo systemctl daemon-reload

# Включить автозапуск при загрузке
sudo systemctl enable screwdrive-api.service

# Запустить сервис сейчас
sudo systemctl start screwdrive-api.service

# Проверить статус
sudo systemctl status screwdrive-api.service
```

---

## Автозапуск Desktop UI (TouchDesk)

### Вариант 1: Запуск через systemd (рекомендуется для headless)

Для запуска PyQt5 приложения на устройстве без X-сервера (headless с сенсорным экраном):

```bash
sudo nano /etc/systemd/system/screwdrive-touchdesk.service
```

Содержимое:

```ini
[Unit]
Description=Screw Drive TouchDesk UI
After=screwdrive-api.service
Requires=screwdrive-api.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py

# Для EGLFS (headless с сенсорным экраном)
Environment=QT_QPA_PLATFORM=eglfs
Environment=QT_QPA_EGLFS_ALWAYS_SET_MODE=1
Environment=PYTHONUNBUFFERED=1

# Настройки framebuffer
Environment=QT_QPA_EGLFS_FB=/dev/fb0

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Вариант 2: Автозапуск через .desktop (для X11/Wayland)

Если используется графический рабочий стол:

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/screwdrive-touchdesk.desktop
```

Содержимое:

```ini
[Desktop Entry]
Type=Application
Name=Screw Drive TouchDesk
Exec=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Touch interface for Screw Drive Control
```

### Вариант 3: Запуск через rc.local

```bash
sudo nano /etc/rc.local
```

Добавить перед `exit 0`:

```bash
# Запуск TouchDesk UI
sleep 10 && /usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py &
```

### Активация systemd сервиса TouchDesk

```bash
sudo systemctl daemon-reload
sudo systemctl enable screwdrive-touchdesk.service
sudo systemctl start screwdrive-touchdesk.service
```

---

## Настройка EGLFS для сенсорного экрана

### 1. Установка необходимых пакетов

```bash
sudo apt-get install -y libqt5gui5-gles qt5-qpa-plugins
sudo apt-get install -y libgles2-mesa libgles2-mesa-dev
```

### 2. Настройка прав доступа

```bash
# Добавить пользователя в группы video и input
sudo usermod -aG video,input $USER
```

### 3. Конфигурация framebuffer

Создать файл конфигурации EGLFS:

```bash
sudo nano /etc/eglfs.json
```

Содержимое:

```json
{
  "device": "/dev/dri/card0",
  "hwcursor": false,
  "pbuffers": true,
  "outputs": [
    {
      "name": "DSI-1",
      "mode": "800x480"
    }
  ]
}
```

Добавить в сервис:

```ini
Environment=QT_QPA_EGLFS_KMS_CONFIG=/etc/eglfs.json
```

### 4. Настройка сенсорного экрана

```bash
# Проверить устройства ввода
cat /proc/bus/input/devices

# Установить библиотеку для калибровки
sudo apt-get install -y libts-bin evtest

# Калибровка
sudo ts_calibrate
```

---

## Управление сервисами

### Основные команды

```bash
# Статус сервисов
sudo systemctl status screwdrive-api.service
sudo systemctl status screwdrive-touchdesk.service

# Запуск/остановка
sudo systemctl start screwdrive-api.service
sudo systemctl stop screwdrive-api.service

# Перезапуск
sudo systemctl restart screwdrive-api.service

# Просмотр логов
sudo journalctl -u screwdrive-api.service -f
sudo journalctl -u screwdrive-touchdesk.service -f

# Логи за последний час
sudo journalctl -u screwdrive-api.service --since "1 hour ago"

# Отключить автозапуск
sudo systemctl disable screwdrive-api.service
```

### Скрипт управления

Создайте скрипт для удобного управления:

```bash
sudo nano /usr/local/bin/screwdrive
sudo chmod +x /usr/local/bin/screwdrive
```

Содержимое:

```bash
#!/bin/bash

case "$1" in
    start)
        sudo systemctl start screwdrive-api.service
        sudo systemctl start screwdrive-touchdesk.service
        ;;
    stop)
        sudo systemctl stop screwdrive-touchdesk.service
        sudo systemctl stop screwdrive-api.service
        ;;
    restart)
        sudo systemctl restart screwdrive-api.service
        sudo systemctl restart screwdrive-touchdesk.service
        ;;
    status)
        echo "=== API Server ==="
        sudo systemctl status screwdrive-api.service --no-pager
        echo ""
        echo "=== TouchDesk UI ==="
        sudo systemctl status screwdrive-touchdesk.service --no-pager
        ;;
    logs)
        sudo journalctl -u screwdrive-api.service -u screwdrive-touchdesk.service -f
        ;;
    *)
        echo "Usage: screwdrive {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
```

Использование:

```bash
screwdrive start    # Запустить все
screwdrive stop     # Остановить все
screwdrive status   # Статус сервисов
screwdrive logs     # Просмотр логов в реальном времени
```

---

## Отладка

### Проблема: Сервис не запускается

1. Проверьте логи:
```bash
sudo journalctl -u screwdrive-api.service -n 50 --no-pager
```

2. Запустите вручную для диагностики:
```bash
cd /home/user/3SD-SF-ScrewFeed/screwdrive
sudo python3 main.py
```

### Проблема: TouchDesk не отображается

1. Проверьте платформу Qt:
```bash
export QT_DEBUG_PLUGINS=1
python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py
```

2. Попробуйте другие платформы:
```bash
# LinuxFB
QT_QPA_PLATFORM=linuxfb python3 ui/touchdesk.py

# X11 (если установлен)
QT_QPA_PLATFORM=xcb python3 ui/touchdesk.py

# VNC (для удаленной отладки)
QT_QPA_PLATFORM=vnc python3 ui/touchdesk.py
```

### Проблема: Нет доступа к GPIO

```bash
# Проверить права
ls -la /dev/gpiochip*

# Добавить правило udev
sudo nano /etc/udev/rules.d/99-gpio.rules
```

Содержимое:

```
SUBSYSTEM=="gpio", KERNEL=="gpiochip*", MODE="0666"
```

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### Проблема: Сервис падает при загрузке

Увеличьте задержку перед запуском:

```ini
[Service]
ExecStartPre=/bin/sleep 10
```

---

## Быстрая установка

Скрипт для автоматической настройки всех сервисов:

```bash
#!/bin/bash
# install_services.sh

SCREWDRIVE_DIR="/home/user/3SD-SF-ScrewFeed/screwdrive"

# Создать сервис API
cat << 'EOF' | sudo tee /etc/systemd/system/screwdrive-api.service
[Unit]
Description=Screw Drive Control API Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Создать сервис TouchDesk
cat << 'EOF' | sudo tee /etc/systemd/system/screwdrive-touchdesk.service
[Unit]
Description=Screw Drive TouchDesk UI
After=screwdrive-api.service
Requires=screwdrive-api.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py
Environment=QT_QPA_PLATFORM=eglfs
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Активировать сервисы
sudo systemctl daemon-reload
sudo systemctl enable screwdrive-api.service
sudo systemctl enable screwdrive-touchdesk.service

echo "Services installed and enabled!"
echo "Run 'sudo systemctl start screwdrive-api' to start"
```

Сохраните и запустите:

```bash
chmod +x install_services.sh
./install_services.sh
```

---

## Запуск только Web UI (без TouchDesk)

Если нужен только веб-интерфейс без десктопного приложения:

```bash
# Отключить TouchDesk
sudo systemctl disable screwdrive-touchdesk.service

# Оставить только API
sudo systemctl enable screwdrive-api.service
```

Web UI доступен по адресу: `http://<IP-адрес>:5000/`
