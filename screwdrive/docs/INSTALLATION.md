# Инструкция по установке

## Требования

### Аппаратные требования

- **Master Pi 5**: Raspberry Pi 5 (4GB+ RAM рекомендуется)
- **XY Table Pi 5**: Raspberry Pi 5 (2GB+ RAM достаточно)
- Сенсорный экран (опционально, для touchdesk UI)
- SD карты 16GB+ каждая

### Программные требования

- Raspberry Pi OS Bookworm (64-bit)
- Python 3.11+
- lgpio library

---

## Установка на XY Table Pi 5

### 1. Базовая настройка системы

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка системных зависимостей
sudo apt install -y python3-pip python3-lgpio git

# Установка Python пакетов
pip install pyserial lgpio pyyaml --break-system-packages
```

### 2. Настройка UART

```bash
# Включение UART в конфиге
sudo raspi-config
# Interface Options -> Serial Port
# Login shell: NO
# Serial hardware: YES

# Альтернативно через config.txt:
sudo nano /boot/firmware/config.txt
```

Добавить в конец файла:
```ini
# Enable UART
enable_uart=1
dtoverlay=uart0
```

### 3. Установка xy_cli.py

```bash
# Клонирование репозитория
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed

# Проверка работы
python3 xy_cli.py --help
```

### 4. Создание systemd сервиса

```bash
sudo nano /etc/systemd/system/xy_table.service
```

Содержимое:
```ini
[Unit]
Description=XY Table Controller
After=multi-user.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/3SD-SF-ScrewFeed
ExecStart=/usr/bin/python3 /home/pi/3SD-SF-ScrewFeed/xy_cli.py --serial /dev/ttyAMA0 --baud 115200
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# Включение и запуск
sudo systemctl daemon-reload
sudo systemctl enable xy_table.service
sudo systemctl start xy_table.service

# Проверка статуса
sudo systemctl status xy_table.service
```

---

## Установка на Master Pi 5

### 1. Базовая настройка системы

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка системных зависимостей
sudo apt install -y python3-pip python3-lgpio python3-pyqt5 git

# Клонирование репозитория
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed/screwdrive

# Установка Python зависимостей из requirements.txt
pip install -r requirements.txt --break-system-packages

# Или установка вручную:
# pip install flask flask-cors pyserial pyyaml lgpio --break-system-packages
```

### Зависимости (requirements.txt)

| Пакет | Версия | Назначение |
|-------|--------|------------|
| flask | >=2.0.0 | Web сервер API |
| flask-cors | >=3.0.0 | CORS для API |
| lgpio | >=0.2.0 | GPIO для Raspberry Pi 5 |
| pyserial | >=3.5 | Serial связь с XY Table |
| pyyaml | >=6.0 | Конфигурационные файлы |
| PyQt5 | >=5.15.0 | Desktop UI (опционально) |

### 2. Настройка UART

```bash
sudo raspi-config
# Interface Options -> Serial Port
# Login shell: NO
# Serial hardware: YES
```

Добавить в `/boot/firmware/config.txt`:
```ini
enable_uart=1
dtoverlay=uart0
```

### 3. Установка screwdrive

```bash
# Клонирование репозитория
git clone https://github.com/Sergey-Golinskiy/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed/screwdrive

# Создание директорий для конфигурации и логов
sudo mkdir -p /etc/screwdrive
sudo mkdir -p /var/log/screwdrive
sudo chown $USER:$USER /var/log/screwdrive

# Копирование конфигурации
sudo cp config/*.yaml /etc/screwdrive/
```

### 4. Настройка конфигурации

```bash
sudo nano /etc/screwdrive/settings.yaml
```

Отредактируйте настройки под вашу систему:
```yaml
xy_table:
  mode: "serial"
  serial_port: "/dev/ttyAMA0"
  serial_baud: 115200

api:
  host: "0.0.0.0"
  port: 5000
```

### 5. Создание systemd сервиса

```bash
sudo nano /etc/systemd/system/screwdrive.service
```

Содержимое:
```ini
[Unit]
Description=Screw Drive Control System
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/3SD-SF-ScrewFeed/screwdrive
ExecStart=/usr/bin/python3 /home/pi/3SD-SF-ScrewFeed/screwdrive/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable screwdrive.service
sudo systemctl start screwdrive.service
```

---

## Проверка установки

### На XY Table Pi 5

```bash
# Проверка сервиса
sudo systemctl status xy_table.service

# Просмотр логов
sudo journalctl -u xy_table.service -f

# Ручной тест через minicom
sudo apt install minicom
minicom -D /dev/ttyAMA0 -b 115200
# Введите: PING
# Должно ответить: PONG
```

### На Master Pi 5

```bash
# Проверка сервиса
sudo systemctl status screwdrive.service

# Проверка API
curl http://localhost:5000/api/health

# Проверка связи с XY столом
curl http://localhost:5000/api/xy/status

# Тест хоминга
curl -X POST http://localhost:5000/api/xy/home
```

---

## Устранение неполадок

### XY Table не отвечает

1. Проверьте физическое подключение UART кабеля
2. Убедитесь что UART включен: `ls /dev/ttyAMA0`
3. Проверьте права доступа: `sudo usermod -aG dialout $USER`
4. Перезагрузите Pi после изменений config.txt

### GPIO не работает

1. Проверьте установку lgpio: `python3 -c "import lgpio; print('OK')"`
2. Убедитесь что пользователь в группе gpio: `groups`
3. Попробуйте запустить с sudo для теста

### Ошибка "Cannot open GPIO chip"

На Pi 5 используется RP1 контроллер GPIO:
```bash
# Проверка
ls /dev/gpiochip*
# Должен быть /dev/gpiochip0 или /dev/gpiochip4
```

---

## Следующие шаги

После успешной установки:

1. [Подключите две Pi 5](PI_TO_PI_CONNECTION.md)
2. [Изучите API](API.md)
3. [Подключите оборудование](HARDWARE.md)
