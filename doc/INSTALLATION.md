# Інструкція з встановлення

## Системні вимоги

### Апаратне забезпечення

**Master Raspberry Pi 5:**
- Raspberry Pi 5 (4GB+ RAM)
- MicroSD карта 32GB+
- Живлення 5V/5A USB-C
- GPIO підключення до реле та датчиків

**Slave Raspberry Pi 5 (XY стіл):**
- Raspberry Pi 5 (4GB+ RAM)
- MicroSD карта 16GB+
- Живлення 5V/5A USB-C
- GPIO підключення до драйверів моторів

**Комунікація:**
- UART кабель між Pi (TX-RX, RX-TX, GND-GND)
- Або Ethernet для SSH

### Програмне забезпечення

- Raspberry Pi OS (64-bit, Bookworm)
- Python 3.10+
- lgpio library (для GPIO на Pi 5)

---

## Встановлення на Master Pi

### 1. Підготовка системи

```bash
# Оновлення системи
sudo apt update && sudo apt upgrade -y

# Встановлення залежностей
sudo apt install -y python3-pip python3-venv git

# Налаштування UART
sudo raspi-config
# -> Interface Options -> Serial Port
# -> Login shell: No
# -> Serial hardware: Yes
```

### 2. Клонування репозиторію

```bash
cd /home/user
git clone https://github.com/your-repo/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed
```

### 3. Створення віртуального середовища

```bash
cd screwdrive
python3 -m venv venv
source venv/bin/activate
```

### 4. Встановлення залежностей

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
flask>=2.0.0
flask-cors>=3.0.0
lgpio>=0.2.0.0
pyserial>=3.5
pyyaml>=6.0
bcrypt>=4.0.0
```

### 5. Налаштування конфігурації

```bash
# Копіювання прикладів конфігурації
cp config/settings.yaml.example config/settings.yaml
cp config/devices.yaml.example config/devices.yaml
```

**Редагування config/settings.yaml:**
```yaml
xy_table:
  mode: "serial"
  serial_port: "/dev/ttyAMA0"
  serial_baud: 115200
  slave_ssh_host: "192.168.1.101"
  slave_ssh_user: "root"

api:
  host: "0.0.0.0"
  port: 5000
```

### 6. Тестовий запуск

```bash
python main.py --api-only --port 5000
```

Відкрийте браузер: `http://<master-ip>:5000`

---

## Встановлення на Slave Pi (XY стіл)

### 1. Підготовка системи

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip git

# Налаштування UART
sudo raspi-config
# -> Interface Options -> Serial Port
# -> Login shell: No
# -> Serial hardware: Yes
```

### 2. Клонування репозиторію

```bash
cd /home/user
git clone https://github.com/your-repo/3SD-SF-ScrewFeed.git
cd 3SD-SF-ScrewFeed
```

### 3. Встановлення залежностей

```bash
pip install lgpio pyserial
```

### 4. Тестовий запуск

```bash
python3 xy_cli.py
```

Перевірка команд:
```
> PING
PONG

> M119
X_MIN:open Y_MIN:open
ok
```

### 5. Запуск в serial режимі

```bash
python3 xy_cli.py --serial /dev/ttyAMA0 --baud 115200
```

---

## Налаштування автозапуску

### Master Pi - systemd service

**Файл: /etc/systemd/system/screwdrive.service**
```ini
[Unit]
Description=3SD-SF-ScrewFeed Master Service
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
Environment="PATH=/home/user/3SD-SF-ScrewFeed/screwdrive/venv/bin"
ExecStart=/home/user/3SD-SF-ScrewFeed/screwdrive/venv/bin/python main.py --api-only --port 5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Активація:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable screwdrive
sudo systemctl start screwdrive
sudo systemctl status screwdrive
```

### Slave Pi - systemd service

**Файл: /etc/systemd/system/xy_table.service**
```ini
[Unit]
Description=XY Table Controller Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/user/3SD-SF-ScrewFeed
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/xy_cli.py --serial /dev/ttyAMA0 --baud 115200
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Активація:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable xy_table
sudo systemctl start xy_table
sudo systemctl status xy_table
```

---

## Налаштування Desktop UI

### Встановлення PyQt5

```bash
sudo apt install -y python3-pyqt5
# або через pip
pip install PyQt5
```

### Запуск Desktop UI

```bash
cd /home/user/3SD-SF-ScrewFeed/screwdrive
python ui/touchdesk.py
```

### Автозапуск Desktop UI

**Файл: /etc/systemd/system/touchdesk.service**
```ini
[Unit]
Description=TouchDesk UI Service
After=screwdrive.service
Requires=screwdrive.service

[Service]
Type=simple
User=user
Environment="DISPLAY=:0"
Environment="QT_QPA_PLATFORM=eglfs"
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
ExecStart=/home/user/3SD-SF-ScrewFeed/screwdrive/venv/bin/python ui/touchdesk.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
```

---

## Підключення UART між Pi

### Розпіновка

| Master Pi | Slave Pi |
|-----------|----------|
| GPIO 14 (TX) | GPIO 15 (RX) |
| GPIO 15 (RX) | GPIO 14 (TX) |
| GND | GND |

### Перевірка з'єднання

**На Master Pi:**
```bash
# Встановлення minicom
sudo apt install minicom

# Підключення
minicom -b 115200 -D /dev/ttyAMA0
```

**На Slave Pi:**
```bash
python3 xy_cli.py --serial /dev/ttyAMA0
```

Відправте `PING` з minicom - має прийти `PONG`.

---

## Налаштування GPIO

### Дозволи на GPIO

```bash
# Додати користувача до групи gpio
sudo usermod -a -G gpio user

# Або запускати від root
sudo python3 xy_cli.py
```

### Перевірка lgpio

```python
import lgpio
h = lgpio.gpiochip_open(0)
print("GPIO chip opened successfully")
lgpio.gpiochip_close(h)
```

---

## Налаштування мережі

### Статична IP адреса

**Файл: /etc/dhcpcd.conf**
```
interface eth0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=8.8.8.8

interface wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=8.8.8.8
```

### SSH доступ

```bash
# Увімкнення SSH
sudo systemctl enable ssh
sudo systemctl start ssh
```

---

## Перевірка встановлення

### Checklist Master Pi

- [ ] Python 3.10+ встановлено
- [ ] Залежності встановлено
- [ ] UART налаштовано
- [ ] Конфігурація створена
- [ ] API сервер запускається
- [ ] Web UI відкривається
- [ ] Датчики читаються
- [ ] Реле перемикаються

### Checklist Slave Pi

- [ ] Python 3.10+ встановлено
- [ ] lgpio встановлено
- [ ] pyserial встановлено
- [ ] UART налаштовано
- [ ] xy_cli.py запускається
- [ ] Мотори рухаються
- [ ] Кінцевики працюють
- [ ] Хомінг виконується

### Checklist з'єднання

- [ ] UART кабель підключено
- [ ] PING/PONG працює
- [ ] M114 повертає статус
- [ ] G28 виконує хомінг
- [ ] Рух виконується

---

## Усунення проблем

### Проблема: GPIO помилка дозволів

**Симптом:**
```
ERROR: Cannot open GPIO chip: Permission denied
```

**Рішення:**
```bash
sudo usermod -a -G gpio $USER
# Перелогіньтесь
```

### Проблема: UART не працює

**Симптом:**
```
serial.serialutil.SerialException: could not open port /dev/ttyAMA0
```

**Рішення:**
1. Перевірте raspi-config (Serial Port enabled)
2. Перевірте /boot/config.txt:
   ```
   enable_uart=1
   dtoverlay=disable-bt
   ```
3. Перезавантажте

### Проблема: Web UI не відкривається

**Симптом:** Браузер показує "Connection refused"

**Рішення:**
1. Перевірте чи сервіс запущено:
   ```bash
   sudo systemctl status screwdrive
   ```
2. Перевірте порт:
   ```bash
   sudo netstat -tlnp | grep 5000
   ```
3. Перевірте firewall:
   ```bash
   sudo ufw allow 5000
   ```

### Проблема: Мотори не рухаються

**Симптом:** Команди виконуються без руху

**Рішення:**
1. Перевірте Enable сигнал (M17)
2. Перевірте живлення драйверів
3. Перевірте гальма (r02, r03 ON)
4. Перевірте аларми (M114)

---

## Оновлення системи

### Оновлення коду

```bash
cd /home/user/3SD-SF-ScrewFeed
git pull origin main

# Оновлення залежностей
cd screwdrive
source venv/bin/activate
pip install -r requirements.txt

# Перезапуск сервісів
sudo systemctl restart screwdrive
sudo systemctl restart xy_table
```

### Backup конфігурації

```bash
# Backup
cp -r config/ config_backup_$(date +%Y%m%d)/

# Restore
cp -r config_backup_20240205/ config/
```
